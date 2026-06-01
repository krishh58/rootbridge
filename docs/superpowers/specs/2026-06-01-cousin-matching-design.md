# Cousin Matching & Research Messaging — Design Spec

## Goal

Surface other RootBridge users who are researching the same ancestors, let them see each other's records, and message directly. Core retention mechanic — the app becomes more valuable as more users join.

## Architecture

Three subsystems working together:

1. **Matching engine** — background job that compares persons across all trees and writes confirmed matches to a `PersonMatch` table
2. **Discovery UI** — badge on the person card that shows how many researchers are studying this ancestor
3. **Inbox** — threaded conversations anchored to a specific ancestor, with shared records pinned above the chat

No external services required in v1. Matching runs entirely off the existing `persons` table.

## Tech Stack

- Python/Flask (existing), SQLAlchemy ORM (existing)
- SQLite → PostgreSQL (existing via Railway)
- Gmail SMTP mailer (existing `mailer.py`)
- D3.js person card (existing `static/js/person_card.js`)
- New: `app/match_routes.py`, `app/message_routes.py`, `app/matcher.py`

---

## Data Models

### PersonMatch

```python
class PersonMatch(db.Model):
    __tablename__ = 'person_matches'
    id            = db.Column(db.Integer, primary_key=True)
    person_a_id   = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    person_b_id   = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    user_a_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    user_b_id     = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    score         = db.Column(db.Integer, nullable=False)  # 0–100 confidence
    notified_a    = db.Column(db.Boolean, default=False, nullable=False)
    notified_b    = db.Column(db.Boolean, default=False, nullable=False)
    created_at    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (db.UniqueConstraint('person_a_id', 'person_b_id'),)
```

Always store `person_a_id < person_b_id` to prevent duplicate pairs.

### ResearchMessage

```python
class ResearchMessage(db.Model):
    __tablename__ = 'research_messages'
    id         = db.Column(db.Integer, primary_key=True)
    match_id   = db.Column(db.Integer, db.ForeignKey('person_matches.id'), nullable=False)
    sender_id  = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    body       = db.Column(db.Text, nullable=False)
    read       = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
```

### User (additions)

```python
discovery_enabled = db.Column(db.Boolean, default=True, nullable=False)
blocked_user_ids  = db.Column(db.JSON, default=list)
display_name      = db.Column(db.String(100))  # shown to matches; falls back to "first_name L."
```

---

## Matching Engine (`app/matcher.py`)

### Match criteria

Two persons match when all of the following hold:

- `first_name` match: case-insensitive strip, Levenshtein distance ≤ 1
- `last_name` match: case-insensitive strip, Levenshtein distance ≤ 1
- `birth_year` within ±5 years (both must be non-null)
- `birth_state` or `birth_country` match (at least one non-null field agrees)
- Different users, both with `discovery_enabled = True`
- Neither user has the other in `blocked_user_ids`

### Confidence score

| Condition | Points |
|-----------|--------|
| Exact first + last name | +40 |
| birth_year exact match | +30 |
| birth_year within ±2 | +20 |
| birth_year within ±5 | +10 |
| birth_state match | +20 |
| birth_country match (no state) | +10 |

Score ≥ 60 is written to `person_matches`. Score < 60 is discarded.

### `run_matcher(person_id=None)`

- If `person_id` is given: compare that one person against all others (called on every person save)
- If `person_id` is None: full scan (called nightly via APScheduler)
- Skips pairs already in `person_matches`
- On new match: sets `notified_a = False`, `notified_b = False`

---

## API Routes

### `app/match_routes.py`

```
GET  /api/persons/<person_id>/matches
     → list of matches for this person (auth required, must own/collaborate on the tree)
     → [{ match_id, display_name, record_count, gap_count, score, your_records, their_records }]

GET  /api/matches
     → all matches for the current user across all their trees
     → includes unread_count per match

POST /api/matches/<match_id>/seen
     → marks notified flag for the current user as True
```

### `app/message_routes.py`

```
GET  /api/matches/<match_id>/messages
     → full message thread for this match (auth required, must be user_a or user_b)
     → marks all messages from the other user as read=True

POST /api/matches/<match_id>/messages
     → body: { "body": "..." }
     → Explorer tier required to send first message; free users get 403 with upgrade_url
     → sends email notification to recipient if this is their first unread message in this thread
     → returns 201 with the created message

POST /api/matches/<match_id>/block
     → adds the other user to current user's blocked_user_ids
     → deletes the PersonMatch row (and all messages)
```

---

## Person Card Badge (Entry Point C)

`GET /api/persons/<person_id>/matches` is called when a person card is opened. If matches exist, the card shows:

```
👥 2 researchers found  [Connect →]
```

Clicking "Connect" opens a modal with:
- List of matched researchers (display name only, e.g. "Sarah M.")
- For each: how many records they have that you don't, and vice versa
- "View shared records" button per researcher

### Shared Records Modal

Split panel:

- **Left column:** your `SearchResult` records for this person (source, record_type, url)
- **Right column:** the other user's records for their matched person
- Records only the other user has are highlighted green ("they have this, you don't")
- Records only you have are highlighted blue ("you have this, they don't")
- "Start conversation" button below → opens inbox thread, scrolled to bottom

---

## Inbox (Entry Point A)

New **Connections** tab in `app.html` nav. Unread badge shows total unread message count across all threads.

### Conversation list

Each entry shows:
- Other user's display name
- Ancestor name the thread is about
- Last message preview (truncated 60 chars)
- Unread dot if unread

### Conversation view (Layout B)

```
┌─────────────────────────────────────┐
│  Sarah M. — James Henderson ~1851   │
├─────────────────────────────────────┤
│  [1860 Census] [Marriage 1873] [+2] │  ← shared records strip (scrollable)
├─────────────────────────────────────┤
│                                     │
│  Sarah: Do you have anything        │
│         after 1880?                 │
│                                     │
│         Yes — death cert 1912  [You]│
│                                     │
├─────────────────────────────────────┤
│  [Type a message...        ] [Send] │
└─────────────────────────────────────┘
```

Records strip shows `SearchResult` records from both users for the matched ancestor pair. Clicking a record opens the URL in a new tab.

---

## Notifications

- **In-app:** unread badge on Connections tab, updated on every page load via `GET /api/matches`
- **Email:** sent via existing `mailer.py` when the recipient has zero read messages in a thread and a new message arrives (i.e. first message only — not every message, to avoid spam)
- **Subject:** `"Sarah M. wants to collaborate on James Henderson — RootBridge"`

---

## Privacy & Access Control

| Rule | Detail |
|------|---------|
| Discovery default | On for all users |
| Turn off discovery | Account settings toggle — removes user from all future matches, does not delete existing ones |
| Display name | `display_name` if set, otherwise `"{first_name} {last_name[0]}."` derived from email prefix |
| Who can message | Explorer tier only can **send** first message. Free users see the match and shared records but get an upgrade prompt on send. |
| Block | Deletes match + all messages. Other user is added to `blocked_user_ids`. No notification sent. |
| Data visibility | Only `SearchResult` records (source, record_type, url) are shared — never raw tree structure or personal notes |

---

## Background Scheduler

APScheduler added to `app/__init__.py`:

```python
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.add_job(run_matcher, 'cron', hour=3)  # 3am nightly
scheduler.start()
```

`run_matcher()` is also called inline (non-blocking, via threading) when a person is saved via `POST /api/persons` or `PUT /api/persons/<id>`.

---

## What Is Not In V1

- File/image attachments (share record URLs by pasting links)
- Group threads (always 1:1 between two users)
- Fuzzy matching on names beyond Levenshtein ±1
- Read receipts shown in UI (read is tracked in DB but not displayed)
- Push notifications

---

## Testing

- `tests/test_matcher.py` — unit tests for match scoring, duplicate prevention, discovery_enabled gating
- `tests/test_match_routes.py` — API tests for GET matches, seen, block
- `tests/test_message_routes.py` — send message (paid gate), read marking, email trigger, block cleanup
