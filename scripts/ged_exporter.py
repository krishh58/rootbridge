"""
RootBridge GED Exporter

Exports persons from local SQLite (or Railway PostgreSQL via DATABASE_URL)
to a standards-compliant GEDCOM 5.5.1 file.

Usage:
  # Export entire vault to a file
  python scripts/ged_exporter.py --output /mnt/h/rootcommons/rootbridge_vault.ged

  # Export a specific tree
  python scripts/ged_exporter.py --tree-id 1 --output my_tree.ged

  # Export only verified/high-confidence records
  python scripts/ged_exporter.py --min-confidence 60 --output high_conf.ged
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / 'instance' / 'local.db'


def ged_date(year):
    if year:
        return str(year)
    return ''


def write_ged(rows, out_path: Path, source_name='RootBridge Vault'):
    now = datetime.now(timezone.utc)
    date_str = now.strftime('%d %b %Y').upper()
    time_str = now.strftime('%H:%M:%S')

    total = 0
    with open(out_path, 'w', encoding='utf-8') as f:
        # Header
        f.write('0 HEAD\n')
        f.write('1 SOUR RootBridge\n')
        f.write('2 NAME RootBridge Genealogy Platform\n')
        f.write('2 VERS 1.0\n')
        f.write('1 DEST ANY\n')
        f.write(f'1 DATE {date_str}\n')
        f.write(f'2 TIME {time_str}\n')
        f.write('1 SUBM @SUBM1@\n')
        f.write('1 GEDC\n')
        f.write('2 VERS 5.5.1\n')
        f.write('2 FORM LINEAGE-LINKED\n')
        f.write('1 CHAR UTF-8\n')
        f.write('1 _SRC ' + source_name + '\n')
        f.write('\n')
        f.write('0 @SUBM1@ SUBM\n')
        f.write('1 NAME RootBridge\n')
        f.write('\n')

        for row in rows:
            pid, fn, mn, ln, by, bs, bc, dy, dp, conf = row

            indi_id = f'@I{pid}@'
            name_parts = ' '.join(filter(None, [fn, mn]))
            full_name = f'{name_parts} /{ln}/' if ln else name_parts

            f.write(f'0 {indi_id} INDI\n')
            f.write(f'1 NAME {full_name}\n')
            if fn:
                f.write(f'2 GIVN {fn}\n')
            if mn:
                f.write(f'2 MIDN {mn}\n')
            if ln:
                f.write(f'2 SURN {ln}\n')

            if by:
                f.write('1 BIRT\n')
                f.write(f'2 DATE {ged_date(by)}\n')
                place_parts = [p for p in [bs, bc] if p]
                if place_parts:
                    f.write(f'2 PLAC {", ".join(place_parts)}\n')

            if dy:
                f.write('1 DEAT\n')
                f.write(f'2 DATE {ged_date(dy)}\n')
                if dp:
                    f.write(f'2 PLAC {dp}\n')

            if conf:
                f.write(f'1 QUAY {min(3, conf // 25)}\n')  # 0-3 scale

            f.write('\n')
            total += 1

            if total % 10000 == 0:
                print(f'  {total:,} written...', end='\r', flush=True)

        f.write('0 TRLR\n')

    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', required=True, help='Output .ged file path')
    ap.add_argument('--tree-id', type=int, default=None, help='Export only this tree ID')
    ap.add_argument('--min-confidence', type=int, default=0, help='Minimum confidence score')
    ap.add_argument('--living-cutoff', type=int, default=100,
                    help='Skip persons born within this many years with no death date (default 100)')
    args = ap.parse_args()

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    cutoff_year = datetime.now().year - args.living_cutoff

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    where = ['(death_year IS NOT NULL OR birth_year IS NULL OR birth_year <= ?)']
    params = [cutoff_year]

    if args.tree_id:
        where.append('tree_id = ?')
        params.append(args.tree_id)

    if args.min_confidence:
        where.append('confidence >= ?')
        params.append(args.min_confidence)

    where_clause = ' AND '.join(where)
    c.execute(f'''
        SELECT id, first_name, middle_name, last_name,
               birth_year, birth_state, birth_country,
               death_year, death_place, confidence
        FROM persons
        WHERE {where_clause}
        ORDER BY last_name, first_name
    ''', params)

    rows = c.fetchall()
    conn.close()

    print(f'Exporting {len(rows):,} persons to {out}...')
    total = write_ged(rows, out)
    size_mb = out.stat().st_size / 1024 / 1024
    print(f'Done. {total:,} persons → {out} ({size_mb:.1f} MB)')


if __name__ == '__main__':
    main()
