"""
Monthly re-scan job — runs for every user with at least one tree.
Checks each person against WikiTree and NARA for records not already
stored in their SearchResult history, then emails a digest if anything new
was found.
"""
import logging
from datetime import datetime, timezone
from .db import db
from .models import User, Tree, Person, SearchResult
from .search_cascade import search_wikitree, search_nara

logger = logging.getLogger(__name__)

_MAX_PERSONS_PER_USER = 50   # guard against enormous trees on a free run
_MAX_NEW_PER_PERSON   = 3    # cap findings per person in the email


def run_monthly_rescan():
    """Called by APScheduler on the 1st of each month."""
    logger.info('Monthly re-scan started')
    users = User.query.all()
    sent = 0
    for user in users:
        try:
            findings = _scan_user(user)
            if findings:
                _send_digest(user, findings)
                sent += 1
        except Exception as exc:
            logger.error('Rescan failed for user %s: %s', user.id, exc)
    logger.info('Monthly re-scan complete — digest emails sent: %d', sent)


def _scan_user(user: User) -> list:
    """Return list of {person_name, items:[{source,record_type,url,title}]} for new finds."""
    findings = []
    persons_scanned = 0

    for tree in user.trees:
        for person in tree.persons:
            if persons_scanned >= _MAX_PERSONS_PER_USER:
                break
            if not person.last_name:
                continue

            existing_urls = {
                sr.url for sr in person.search_results if sr.url
            }

            new_items = []
            for result in _lightweight_search(person):
                if result.get('url') and result['url'] not in existing_urls:
                    new_items.append(result)
                    existing_urls.add(result['url'])
                    if len(new_items) >= _MAX_NEW_PER_PERSON:
                        break

            if new_items:
                _save_new_results(person, new_items)
                name = f"{person.first_name or ''} {person.last_name or ''}".strip()
                findings.append({'person_name': name, 'items': new_items})

            persons_scanned += 1

    return findings


def _lightweight_search(person: Person) -> list:
    """Run WikiTree + NARA only — fast and free, no token cost."""
    results = []
    first = person.first_name or ''
    last  = person.last_name  or ''
    by    = person.birth_year
    bp    = person.birth_state or person.birth_country or ''

    try:
        results += search_wikitree(first, last, by)
    except Exception:
        pass
    try:
        results += search_nara(first, last, by, bp)
    except Exception:
        pass

    return results


def _save_new_results(person: Person, items: list):
    """Persist new records so they show up in the person card and aren't re-reported."""
    for item in items:
        sr = SearchResult(
            person_id=person.id,
            source=item.get('source', 'rescan'),
            record_type=item.get('record_type', ''),
            url=item.get('url', ''),
            raw_data=item,
        )
        db.session.add(sr)
    db.session.commit()


def _send_digest(user: User, findings: list):
    from .mailer import send_rescan_email
    send_rescan_email(user.email, findings)
