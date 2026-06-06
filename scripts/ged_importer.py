"""
RootBridge GEDCOM Importer

Reads downloaded .ged files, filters out test/fake data, and imports
real persons into the RootBridge DB as seed records (no owner, public).

Run:
  python scripts/ged_importer.py [--dry-run] [--min-persons 5]
"""

import argparse, re, sys, os, json, tempfile, requests
from pathlib import Path
from collections import defaultdict

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0'}
INDEX_PATH = Path(__file__).parent.parent / 'data' / 'ged_index.json'

# ─── filter lists ─────────────────────────────────────────────────────────────

# Filenames that are clearly test/fictional data
FAKE_FILENAMES = {
    'lotr', 'got', 'game_of_thrones', 'shakespeare', 'royal', 'kennedy',
    'empty', 'dummy', 'sample', 'test', 'simple', 'tiny', 'advanced',
    'example', 'input', 'demo', 'potato', 'allged', 'testcom', 'gedcom56',
    'us05', 'us08', 'us11', 'us15', 'us20', 'family_info_errors',
    'rtsprint', 'kvsprint', 'error', 'deg_', 'utsdns', 'utslsd',
    'mbase', 'deg', '555sample',
}

# Surnames that appear only in fictional/test trees
FICTIONAL_SURNAMES = {
    'stark', 'lannister', 'targaryen', 'baratheon', 'tyrell', 'greyjoy',
    'baggins', 'gamgee', 'tolkien', 'shakespeare', 'hamlet',
    'smith',  # too generic for test files named "smith.ged"
}

# Real-looking files by name pattern
REAL_NAME_HINTS = [
    r'^[A-Z][a-z]+[-_][A-Z][a-z]+',  # Firstname-Lastname or Firstname_Lastname
    r'^[A-Z][a-z]{3,}family',         # SurnameFamily
    r'\d{4,}',                         # contains a year
    r'^[a-z]+\d+',                     # surname + number (e.g. ochuba-full)
]

def looks_fake(filepath, person_count, surnames):
    """Return True if this file is likely test/fictional data."""
    stem = filepath.stem.lower().replace('-', '_').replace(' ', '_')

    # Too small
    if person_count < 5:
        return True

    # Known fake filename prefix
    for fake in FAKE_FILENAMES:
        if stem.startswith(fake) or stem == fake:
            return True

    # Fictional surnames dominate
    if surnames:
        top = list(surnames.items())[:5]
        fictional_count = sum(c for s, c in top if s.lower() in FICTIONAL_SURNAMES)
        if fictional_count > person_count * 0.3:
            return True

    return False


# ─── GEDCOM parser ────────────────────────────────────────────────────────────

def parse_ged(filepath):
    """
    Fast line-by-line GEDCOM parser.
    Returns list of person dicts:
      {first_name, last_name, birth_year, birth_place, death_year, death_place,
       sex, parent_ids (ged refs), spouse_ids (ged refs)}
    """
    persons = {}      # ged_id → dict
    families = {}     # fam_id → {husb, wife, children}
    surnames = defaultdict(int)

    current_id   = None
    current_type = None  # 'INDI' or 'FAM'
    in_birt      = False
    in_deat      = False
    current_fam  = None

    with open(filepath, 'r', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n\r')
            parts = line.split(' ', 2)
            if len(parts) < 2:
                continue
            level_str = parts[0].strip()
            try:
                level = int(level_str)
            except ValueError:
                continue
            tag_or_id = parts[1].strip() if len(parts) > 1 else ''
            value     = parts[2].strip() if len(parts) > 2 else ''

            # Level 0 — new record
            if level == 0:
                in_birt = in_deat = False
                if tag_or_id.startswith('@') and tag_or_id.endswith('@'):
                    rec_id = tag_or_id
                    rec_type = value.upper()
                    if rec_type == 'INDI':
                        current_id   = rec_id
                        current_type = 'INDI'
                        persons[rec_id] = {
                            'ged_id': rec_id, 'first_name': '', 'last_name': '',
                            'birth_year': None, 'birth_place': '',
                            'death_year': None, 'death_place': '',
                            'sex': '', 'fam_child': [], 'fam_spouse': [],
                        }
                    elif rec_type == 'FAM':
                        current_fam  = rec_id
                        current_type = 'FAM'
                        families[rec_id] = {'husb': None, 'wife': None, 'children': []}
                    else:
                        current_id = current_fam = None
                        current_type = None
                else:
                    current_id = current_fam = None
                    current_type = None
                continue

            # INDI tags
            if current_type == 'INDI' and current_id:
                p = persons[current_id]
                if level == 1:
                    in_birt = (tag_or_id == 'BIRT')
                    in_deat = (tag_or_id == 'DEAT')
                    if tag_or_id == 'NAME':
                        # Name format: First /Last/
                        name = value.replace('/', ' ').strip()
                        parts2 = name.split()
                        # Extract surname from /Surname/ pattern
                        m = re.search(r'/([^/]+)/', value)
                        if m:
                            p['last_name'] = m.group(1).strip()
                            first = value[:value.index('/')].strip()
                            p['first_name'] = first
                        else:
                            if parts2:
                                p['first_name'] = parts2[0]
                                p['last_name']  = parts2[-1] if len(parts2) > 1 else ''
                        if p['last_name']:
                            surnames[p['last_name']] += 1
                    elif tag_or_id == 'SEX':
                        p['sex'] = value.upper()
                    elif tag_or_id == 'FAMC':
                        ref = value.strip('@')
                        p['fam_child'].append(value)
                    elif tag_or_id == 'FAMS':
                        p['fam_spouse'].append(value)
                elif level == 2:
                    if in_birt:
                        if tag_or_id == 'DATE':
                            m = re.search(r'\b(\d{4})\b', value)
                            if m:
                                p['birth_year'] = int(m.group(1))
                        elif tag_or_id == 'PLAC':
                            p['birth_place'] = value[:100]
                    elif in_deat:
                        if tag_or_id == 'DATE':
                            m = re.search(r'\b(\d{4})\b', value)
                            if m:
                                p['death_year'] = int(m.group(1))
                        elif tag_or_id == 'PLAC':
                            p['death_place'] = value[:100]

            # FAM tags
            elif current_type == 'FAM' and current_fam:
                fam = families[current_fam]
                if level == 1:
                    if tag_or_id == 'HUSB':
                        fam['husb'] = value
                    elif tag_or_id == 'WIFE':
                        fam['wife'] = value
                    elif tag_or_id == 'CHIL':
                        fam['children'].append(value)

    return list(persons.values()), families, dict(surnames)


# ─── importer ─────────────────────────────────────────────────────────────────

def import_to_db(persons_data, families, source_file, tree_id, db_session,
                 Person, Tree):
    """Import parsed persons into RootBridge DB. Returns count imported."""
    from datetime import datetime

    ged_to_db_id = {}  # ged_id → db Person.id

    # First pass: create all Person records
    for p in persons_data:
        if not p['first_name'] and not p['last_name']:
            continue

        # Parse birth_state from birth_place
        birth_state = None
        birth_country = None
        bp = p.get('birth_place', '')
        if bp:
            parts = [x.strip() for x in bp.split(',')]
            if len(parts) >= 2:
                birth_state = parts[-2][:50] if len(parts) > 1 else None
                birth_country = parts[-1][:50]
            else:
                birth_country = parts[0][:50]

        person = Person(
            tree_id      = tree_id,
            first_name   = p['first_name'][:100],
            last_name    = p['last_name'][:100],
            birth_year   = p.get('birth_year'),
            birth_state  = birth_state,
            birth_country= birth_country,
            death_year   = p.get('death_year'),
            death_place  = p.get('death_place', '')[:200] or None,
            confidence   = 30,  # seed data, low confidence
        )
        db_session.add(person)
        db_session.flush()  # get ID
        ged_to_db_id[p['ged_id']] = person.id

    # Second pass: wire parent/spouse relationships
    for fam in families.values():
        husb_id = ged_to_db_id.get(fam.get('husb'))
        wife_id = ged_to_db_id.get(fam.get('wife'))
        child_ids = [ged_to_db_id.get(c) for c in fam.get('children', []) if ged_to_db_id.get(c)]

        if husb_id and wife_id:
            husb = db_session.get(Person, husb_id)
            wife = db_session.get(Person, wife_id)
            if husb and wife:
                husb.spouse_ids = list(set((husb.spouse_ids or []) + [wife_id]))
                wife.spouse_ids = list(set((wife.spouse_ids or []) + [husb_id]))

        parent_db_ids = [i for i in [husb_id, wife_id] if i]
        for child_id in child_ids:
            child = db_session.get(Person, child_id)
            if child:
                child.parent_ids = list(set((child.parent_ids or []) + parent_db_ids))

    db_session.commit()
    return len(ged_to_db_id)


# ─── main ─────────────────────────────────────────────────────────────────────

def _already_imported() -> set:
    """Return set of URLs already imported (stored in index as 'imported':true)."""
    if not INDEX_PATH.exists():
        return set()
    try:
        entries = json.loads(INDEX_PATH.read_text())
        return {e['url'] for e in entries if e.get('imported')}
    except Exception:
        return set()


def _mark_imported(url: str):
    """Mark a URL as imported in ged_index.json."""
    if not INDEX_PATH.exists():
        return
    try:
        entries = json.loads(INDEX_PATH.read_text())
        for e in entries:
            if e['url'] == url:
                e['imported'] = True
                break
        INDEX_PATH.write_text(json.dumps(entries, indent=2))
    except Exception:
        pass


def _stream_and_process(url: str, min_persons: int, dry_run: bool,
                         seed_tree_id, db_session, Person, Tree,
                         keep_dir: Path = None) -> tuple[str, int]:
    """
    Download a .ged URL, parse+import it.
    If keep_dir is set, save to keep_dir/<filename> (archive mode).
    Otherwise download to a temp file and delete after import.
    Returns (status, count) where status is 'imported'/'fake'/'small'/'error'.
    """
    filename = url.split('/')[-1].split('?')[0] or 'download.ged'
    # Ensure .ged extension
    if not filename.lower().endswith('.ged'):
        filename += '.ged'

    try:
        r = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        r.raise_for_status()
    except Exception:
        return ('error', 0)

    # Decide destination path
    if keep_dir:
        keep_dir.mkdir(parents=True, exist_ok=True)
        dest_path = keep_dir / filename
        # Avoid collisions — append a suffix if filename already exists
        counter = 1
        while dest_path.exists():
            dest_path = keep_dir / f"{filename[:-4]}_{counter}.ged"
            counter += 1
        tmp_path  = dest_path
        delete_after = False
    else:
        tmp_fd    = tempfile.NamedTemporaryFile(suffix='.ged', delete=False)
        tmp_path  = Path(tmp_fd.name)
        tmp_fd.close()
        delete_after = True

    with open(tmp_path, 'wb') as fh:
        for chunk in r.iter_content(chunk_size=65536):
            fh.write(chunk)

    try:
        persons_data, families, surnames = parse_ged(tmp_path)
        n = len(persons_data)

        if n < min_persons:
            if delete_after:
                tmp_path.unlink(missing_ok=True)
            return ('small', 0)

        fake_path = Path(filename)
        if looks_fake(fake_path, n, surnames):
            top = ', '.join(f"{s}({c})" for s, c in
                            sorted(surnames.items(), key=lambda x: -x[1])[:3])
            print(f"  [FAKE] {filename:45s} {n:>6} persons  {top}")
            if delete_after:
                tmp_path.unlink(missing_ok=True)
            return ('fake', 0)

        top = ', '.join(f"{s}({c})" for s, c in
                        sorted(surnames.items(), key=lambda x: -x[1])[:3])
        kept_note = f"  → saved to {tmp_path.name}" if not delete_after else ''
        print(f"  [REAL] {filename:45s} {n:>6} persons  {top}{kept_note}")

        if not dry_run:
            imported = import_to_db(persons_data, families, fake_path,
                                     seed_tree_id, db_session, Person, Tree)
            return ('imported', imported)
        else:
            return ('imported', n)

    except Exception as e:
        print(f"  [ERR] {filename}: {e}")
        return ('error', 0)
    finally:
        if delete_after:
            tmp_path.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run',     action='store_true', help='Parse only, no DB write')
    ap.add_argument('--min-persons', type=int, default=5,  help='Skip files with fewer persons')
    ap.add_argument('--ged-dir',     default='data/ged_files',
                    help='Local dir of .ged files (default mode)')
    ap.add_argument('--stream',      action='store_true',
                    help='Download from ged_index.json, import, delete — no local storage')
    ap.add_argument('--keep-dir',    default=None,
                    help='If set, save every downloaded .ged to this directory instead of deleting it')
    ap.add_argument('--skip-imported', action='store_true', default=True,
                    help='Skip URLs already marked imported in index (default: on)')
    args = ap.parse_args()

    # ── stream mode: download → import → delete ───────────────────────────────
    if args.stream:
        if not INDEX_PATH.exists():
            print(f"No index found at {INDEX_PATH}")
            sys.exit(1)

        entries   = json.loads(INDEX_PATH.read_text())
        done_urls = _already_imported() if args.skip_imported else set()
        pending   = [e for e in entries if e['url'] not in done_urls]
        print(f"Stream mode: {len(pending)} URLs to process ({len(done_urls)} already imported)")

        if not args.dry_run:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            os.chdir(Path(__file__).parent.parent)
            os.environ.setdefault('SECRET_KEY', 'devsecret')
            # Default to file-based SQLite so data persists between runs
            db_path = Path(__file__).parent.parent / 'instance' / 'local.db'
            os.environ.setdefault('DATABASE_URL', f'sqlite:///{db_path.resolve()}')
            from app import create_app
            from app.db import db
            from app.models import Person, Tree, User

            app = create_app()
            ctx = app.app_context()
            ctx.push()

            seed_user = User.query.filter_by(email='seed@rootbridge.internal').first()
            if not seed_user:
                seed_user = User(email='seed@rootbridge.internal')
                seed_user.password_hash = 'seed_not_for_login'
                db.session.add(seed_user)
                db.session.commit()

            seed_tree = Tree.query.filter_by(user_id=seed_user.id, name='GEDCOM Seed Data').first()
            if not seed_tree:
                seed_tree = Tree(user_id=seed_user.id, name='GEDCOM Seed Data')
                db.session.add(seed_tree)
                db.session.commit()

            tree_id    = seed_tree.id
            db_session = db.session
        else:
            tree_id = db_session = Person = Tree = None

        keep_dir = Path(args.keep_dir) if args.keep_dir else None
        if keep_dir:
            keep_dir.mkdir(parents=True, exist_ok=True)
            print(f"Archive mode: saving .ged files to {keep_dir}")

        counts = {'imported': 0, 'fake': 0, 'small': 0, 'error': 0}

        for i, entry in enumerate(pending, 1):
            url = entry['url']
            print(f"[{i}/{len(pending)}] {url[:80]}")
            status, n = _stream_and_process(
                url, args.min_persons, args.dry_run,
                tree_id, db_session, Person, Tree,
                keep_dir=keep_dir,
            )
            counts[status] += n if status == 'imported' else 1
            if status == 'imported' and not args.dry_run:
                _mark_imported(url)

        print(f"\n{'='*55}")
        print(f"  Persons imported : {counts['imported']:,}")
        print(f"  Fake/test        : {counts['fake']}")
        print(f"  Too small        : {counts['small']}")
        print(f"  Errors           : {counts['error']}")
        print(f"{'='*55}")
        return

    # ── local dir mode (original behaviour, used for already-downloaded files) ─
    ged_dir = Path(args.ged_dir)
    files   = sorted(ged_dir.glob('*.ged'))
    print(f"Found {len(files)} .ged files in {ged_dir}")

    if not args.dry_run:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        os.chdir(Path(__file__).parent.parent)
        os.environ.setdefault('SECRET_KEY', 'devsecret')
        from app import create_app
        from app.db import db
        from app.models import Person, Tree, User

        app = create_app()
        ctx = app.app_context()
        ctx.push()

        # Find or create a seed user + tree
        seed_user = User.query.filter_by(email='seed@rootbridge.internal').first()
        if not seed_user:
            seed_user = User(
                email = 'seed@rootbridge.internal',
            )
            seed_user.password_hash = 'seed_not_for_login'
            db.session.add(seed_user)
            db.session.commit()
            print(f"Created seed user id={seed_user.id}")

        seed_tree = Tree.query.filter_by(user_id=seed_user.id, name='GEDCOM Seed Data').first()
        if not seed_tree:
            seed_tree = Tree(user_id=seed_user.id, name='GEDCOM Seed Data')
            db.session.add(seed_tree)
            db.session.commit()
            print(f"Created seed tree id={seed_tree.id}")

    total_imported = 0
    total_skipped  = 0
    total_fake     = 0

    for f in files:
        try:
            persons_data, families, surnames = parse_ged(f)
        except Exception as e:
            print(f"  [ERR] {f.name}: {e}")
            continue

        n = len(persons_data)

        if n < args.min_persons:
            total_skipped += 1
            continue

        if looks_fake(f, n, surnames):
            top_surnames = ', '.join(f"{s}({c})" for s, c in sorted(surnames.items(), key=lambda x: -x[1])[:3])
            print(f"  [FAKE] {f.name:40s}  {n:>5} persons  surnames: {top_surnames}")
            total_fake += 1
            continue

        top_surnames = ', '.join(f"{s}({c})" for s, c in sorted(surnames.items(), key=lambda x: -x[1])[:3])
        print(f"  [REAL] {f.name:40s}  {n:>5} persons  surnames: {top_surnames}")

        if not args.dry_run:
            imported = import_to_db(persons_data, families, f, seed_tree.id,
                                     db.session, Person, Tree)
            total_imported += imported
        else:
            total_imported += n

    print(f"\n{'='*55}")
    print(f"  Real trees:  {len(files) - total_skipped - total_fake}")
    print(f"  Skipped (too small): {total_skipped}")
    print(f"  Filtered (fake/test): {total_fake}")
    print(f"  Persons {'(dry run) ' if args.dry_run else ''}imported: {total_imported:,}")
    print(f"{'='*55}")


if __name__ == '__main__':
    main()
