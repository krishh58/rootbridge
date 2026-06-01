# RootBridge — Collaboration, Fork, and Referral Design

**Date:** 2026-06-01
**Builds on:** Plans 1–5 (MVP complete)

---

## Goal

Add three interconnected social features:
1. **Tree collaboration** — invite family members to view or edit a tree by link or email
2. **Fork** — create a new tree from any existing person card
3. **Referral tokens** — earn tokens for bringing new users to RootBridge

---

## Architecture

Three new modules added to the existing Flask app:

- `app/collab_routes.py` — invite creation, accept, collaborator management
- `app/mailer.py` — Gmail SMTP email sending (graceful fallback to link-only)
- `app/fork_routes.py` — fork a person into a new tree

Existing modules modified:

- `app/models.py` — add `TreeInvite`, `TreeCollaborator` tables; add `referral_code` + `referred_by_user_id` to `User`
- `app/auth.py` — set `referred_by_user_id` on register if ref cookie present; award 25 tokens to referrer
- `app/stripe_routes.py` — award 150 tokens to referrer on first subscription
- `app/tree_routes.py` — replace raw `Tree.user_id == g.user_id` ownership checks with `_can_access_tree()` helper
- `static/js/person_card.js` — add "Start New Tree From Here" button
- `static/app.html` — add referral card to profile/settings panel

---

## Data Model

### New: TreeInvite

```
id                  INTEGER PRIMARY KEY
tree_id             INTEGER FK trees.id NOT NULL
role                VARCHAR(10) NOT NULL  -- 'viewer' | 'editor'
invite_token        VARCHAR(64) UNIQUE NOT NULL
email               VARCHAR(255)          -- nullable, for email delivery
claimed_by_user_id  INTEGER FK users.id   -- nullable until accepted
created_at          DATETIME
expires_at          DATETIME              -- created_at + 7 days
```

### New: TreeCollaborator

```
id          INTEGER PRIMARY KEY
tree_id     INTEGER FK trees.id NOT NULL
user_id     INTEGER FK users.id NOT NULL
role        VARCHAR(10) NOT NULL  -- 'viewer' | 'editor'
created_at  DATETIME
UNIQUE (tree_id, user_id)
```

### Modified: User

Add two columns:
```
referral_code         VARCHAR(16) UNIQUE NOT NULL  -- auto-generated at signup, e.g. 'RB-X4K9A2'
referred_by_user_id   INTEGER FK users.id          -- nullable
```

`referral_code` is generated as `'RB-' + secrets.token_hex(3).upper()` at register time. Unique constraint enforced at DB level.

---

## Access Control

A shared helper `_can_access_tree(tree_id, user_id, require_editor=False)` is added to `app/tree_routes.py`:

```python
def _can_access_tree(tree_id: int, user_id: int, require_editor: bool = False) -> bool:
    tree = Tree.query.get(tree_id)
    if not tree:
        return False
    if tree.user_id == user_id:
        return True  # owner always has full access
    collab = TreeCollaborator.query.filter_by(tree_id=tree_id, user_id=user_id).first()
    if not collab:
        return False
    if require_editor:
        return collab.role == 'editor'
    return True
```

All existing `Tree.user_id == g.user_id` ownership checks in `tree_routes.py` are updated:
- Read operations (GET tree, GET persons): use `_can_access_tree(tree_id, g.user_id)`
- Write operations (add/edit person): use `_can_access_tree(tree_id, g.user_id, require_editor=True)`
- Owner-only operations (delete tree, manage invites, PDF export, share token): keep raw `Tree.user_id == g.user_id` check

---

## API Routes

### Collaboration (`app/collab_routes.py`)

**`POST /api/trees/<tree_id>/invite`** — owner only
```json
Request:  { "role": "editor", "email": "cousin@example.com" }
Response: { "invite_url": "https://rootbridge.app/invite/abc123", "token": "abc123", "expires_at": "..." }
```
- Creates `TreeInvite` row with `expires_at = now + 7 days`
- If `email` provided, sends invite email via `mailer.send_invite_email()` (non-blocking, errors logged not raised)

**`GET /api/invite/<token>`** — public (no auth required)
```json
Response: { "tree_name": "Johnson Family", "inviter_name": "K. Henderson", "role": "editor", "expires_at": "..." }
```
- Returns 404 if token not found or expired

**`POST /api/invite/<token>/accept`** — requires auth
```json
Response: { "tree_id": 4, "role": "editor" }
```
- Returns 400 if already claimed, 410 if expired
- Creates `TreeCollaborator` row, sets `invite.claimed_by_user_id`

**`GET /api/trees/<tree_id>/collaborators`** — owner only
```json
Response: { "collaborators": [{ "user_id": 2, "email": "cousin@example.com", "role": "editor", "joined_at": "..." }] }
```

**`DELETE /api/trees/<tree_id>/collaborators/<user_id>`** — owner only
- Returns 204 on success

**`GET /invite/<token>`** — public HTML page (no auth)
- Landing page showing invite details + "Accept Invite" button
- If not logged in, shows "Sign up to accept" with referral cookie preserved

### Fork (`app/fork_routes.py`)

**`POST /api/persons/<person_id>/fork`**
```json
Request:  { "tree_name": "Mother's Side" }   -- optional
Response: { "tree_id": 7, "person_id": 22, "tree_name": "Mother's Side" }
```
- Requires auth + `_can_access_tree(person.tree.id, g.user_id)` (viewer or editor)
- Creates new `Tree` owned by `g.user_id`
- Creates new `Person` copied from source: `first_name`, `last_name`, `birth_year`, `birth_state`, `confidence`
- Does NOT copy: `SearchResult`, `Gap`, `AlfredMessage`, parent/spouse relationships
- Default tree name: `"{first_name} {last_name} Family"` if not provided

### Referral (`app/auth.py` + `app/collab_routes.py`)

**`GET /api/me/referral`** — requires auth
```json
Response: {
  "code": "RB-X4K9A2",
  "referral_url": "https://rootbridge.app/?ref=RB-X4K9A2",
  "referred_count": 3,
  "tokens_earned": 475
}
```

`referred_count` = `User.query.filter_by(referred_by_user_id=g.user_id).count()`
`tokens_earned` = `referred_count * 25` + (count of referred users with a paid subscription * 150)

---

## Email

`app/mailer.py` — single function, Gmail SMTP:

```python
def send_invite_email(to_email, inviter_name, tree_name, role, invite_url):
    # sends via smtplib.SMTP_SSL('smtp.gmail.com', 465)
    # uses GMAIL_USER + GMAIL_APP_PASSWORD env vars
    # raises no exceptions — logs failures and returns False
```

New env vars required:
- `GMAIL_USER` — Gmail address used to send (e.g. `rootbridge.app@gmail.com`)
- `GMAIL_APP_PASSWORD` — Gmail App Password (16-char, generated in Google Account security settings)

If env vars absent or email fails, invite creation still succeeds — the `invite_url` is always returned to the frontend.

---

## Referral Flow

1. User copies their referral URL from the profile panel: `https://rootbridge.app/?ref=RB-X4K9A2`
2. Visitor arrives → landing page JS reads `?ref=` and stores in a cookie (`ref_code`, 7-day TTL)
3. Visitor registers → `POST /auth/register` reads `ref_code` cookie:
   - Looks up `User.query.filter_by(referral_code=ref_code).first()`
   - Sets `new_user.referred_by_user_id = referrer.id`
   - Awards referrer `+25` tokens via `referrer.token_balance += 25` + `db.session.commit()`
4. Referred user subscribes → existing Stripe webhook `checkout.session.completed` handler:
   - If `user.referred_by_user_id` is set and this is first subscription: award referrer `+150` tokens

---

## UI Changes

### Person Card (`static/js/person_card.js`)

Add button in `buildCardHTML()` alongside existing search buttons:
```
[ Search US Records ]
[ Search European Records (40 tokens) ]
[ Search AA Records (40 tokens) ]
[ Start New Tree From Here ]          ← new
```

`runFork(personId)` function:
- Prompts user for optional tree name
- POSTs to `/api/persons/<id>/fork`
- On success: shows toast "New tree created — Mother's Side", loads new tree via `loadTree(data.tree_id)`

### App Shell (`static/app.html`)

Settings/profile panel gets a **"Invite Family"** section:
- Referral link display with **Copy Link** button
- Stats line: "3 family members joined · 475 tokens earned"
- Explanation: *"Earn 25 tokens when someone signs up with your link, 150 more when they subscribe."*

Invites panel (accessible from Tree view, owner only):
- "Invite to this tree" form: role selector (Viewer / Editor) + optional email field + **Generate Invite Link** button
- Generated link shown with Copy button
- List of current collaborators with remove button

---

## Testing

New test files:
- `tests/test_collab_routes.py` — invite create, preview, accept, list, remove; expired token returns 410; duplicate accept returns 400; viewer cannot edit
- `tests/test_fork_routes.py` — fork creates new tree + person; source tree unchanged; viewer can fork; non-collaborator gets 403
- `tests/test_referral.py` — referral code set on register; referred_by set when ref cookie present; 25 tokens awarded to referrer on signup; 150 tokens awarded via Stripe webhook

---

## What This Does NOT Include

- Multi-level referral (you earn from who your referrals recruit) — flat one-level only
- Real-time collaboration (no WebSocket, no live cursor) — each save is a discrete API call
- Collaborator notifications (no "your tree was updated" emails) — post-MVP
- Transfer tree ownership — owner role is permanent unless manually changed in DB
