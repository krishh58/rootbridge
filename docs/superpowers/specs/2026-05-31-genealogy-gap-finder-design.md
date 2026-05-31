# Genealogy Gap-Finder Web App — Design Spec

**Date:** 2026-05-31  
**Status:** Approved  
**Working title:** RootBridge (working name — "bridges US records to the world")

---

## Problem

Most Americans can trace their family to great-great-grandparents via US Census records — then hit a wall. Ancestry.com charges $25–$60/month and focuses on its proprietary record vault. No product currently uses AI to intelligently bridge US records to European, African American, and Asian heritage databases using free public sources. 44 million African Americans face a specific structural barrier — "the 1870 wall" — where free tools provide no specialized guidance.

---

## Goal

Build a web app that ingests whatever genealogical data a user has (as little as a name + birth year), automatically searches free public databases, visualizes the family tree with gaps highlighted, and tells the user exactly where to look next. Users pay a subscription to save their research and access heritage-specific search cascades. Pricing undercuts Ancestry by 67–87%.

---

## Architecture

### Backend
- **Flask** (Python) on **Railway** — same deployment pattern as PhoneSaver backend
- **PostgreSQL** on Railway for user accounts, saved trees, and person records
- **Redis** (Railway plugin) for search result caching — FamilySearch results cached 24h to minimize API calls
- **OpenRouter** for AI synthesis (gap analysis narrative, document translation, research suggestions)

### Frontend
- Single-page app: vanilla JS + **D3.js** for the interactive family tree
- No framework overhead — Flask serves static HTML/JS, API calls via fetch()
- Mobile-responsive (CSS Grid)

### External APIs (all free, no scraping)
| API | Use | Auth |
|-----|-----|------|
| FamilySearch API | Core records: census, vital, military, church | OAuth2, free commercial use with attribution |
| WikiTree API | Cross-reference confirmed profiles | No auth required |
| Chronicling America (LoC) | US newspapers 1770–1963 | No auth, public domain |
| Riksarkivet (Sweden) | Swedish church records | Free API |
| Ellis Island / NARA | Ship manifests → origin village | Public domain |
| FamilySearch (EU partition) | German/Italian/Irish/Polish church records | Same OAuth2 token |
| Wikimedia Commons API | Hometown photos — town/village/region images | No auth, free |
| Library of Congress Prints & Photos | Historical US town and regional photos | No auth, public domain |
| David Rumsey Map Collection API | Historical maps by region + time period | Free, no key required |

### Payments
- **Stripe** — subscription billing + token top-up purchases, webhooks update DB tier and token balance per user

---

## Pricing Model — Hybrid Subscription + Token Top-Up

Every paid tier includes a monthly token allotment. Tokens are consumed by AI operations: gap analysis runs, document translations, AI research suggestions, and deep search cascades. Basic FamilySearch record lookups do NOT consume tokens — only AI work does.

### Monthly Token Allotments by Tier

| Tier | Monthly Price | Included Tokens | Notes |
|------|--------------|-----------------|-------|
| Free | $0 | 100 tokens | 5 searches/day cap, no save |
| US Records Only | $7.99/mo | 500 tokens | Covers ~25 full person research runs |
| + European Roots | $12.99/mo | 1,000 tokens | More AI translation work needed |
| African American Heritage | $12.99/mo | 1,000 tokens | Freedmen's Bureau + WPA synthesis |
| Asian/Pacific Heritage | $12.99/mo | 1,000 tokens | Multi-language translation |
| All Access | $19.99/mo | 2,500 tokens | Power users, all heritage cascades |

Unused tokens expire at end of billing month — no rollover (keeps revenue predictable).

### Token Top-Up Packs (Stripe one-time purchase)
| Pack | Price | Tokens | Cost per token |
|------|-------|--------|----------------|
| Starter | $2.99 | 300 | $0.010 |
| Standard | $7.99 | 1,000 | $0.008 |
| Power | $14.99 | 2,500 | $0.006 |
| Research Marathon | $24.99 | 5,000 | $0.005 |

Top-up tokens do not expire until used.

### Token Cost Per Operation (server-side)
| Operation | Token Cost | Typical OpenRouter cost |
|-----------|-----------|------------------------|
| Full person research run (AI gap summary) | 20 tokens | ~$0.002 |
| Document translation (per page) | 15 tokens | ~$0.001 |
| Deep European cascade (ship manifest + EU church AI synthesis) | 40 tokens | ~$0.004 |
| African American cascade (Freedmen's + WPA narrative AI synthesis) | 40 tokens | ~$0.004 |
| AI research suggestion refresh | 5 tokens | <$0.001 |
| Alfred — one chat message (with full person context) | 10 tokens | ~$0.001 |

This ensures the AI cost is always covered by token consumption. Margin target: 5× markup on OpenRouter cost.

### User Experience
- Token balance visible in the nav bar at all times
- Warning toast when balance drops below 100 tokens
- "Top up" button in settings — opens Stripe checkout inline
- Low-balance users see a soft prompt before running a deep cascade: "This will use ~40 tokens. You have 35 remaining. Top up?"
- Free tier users see token counter clearly — creates natural upgrade pressure

---

## Data Model

### `users`
- id, email, password_hash, tier (free/us/european/aa/asian/all), stripe_customer_id, stripe_subscription_id, created_at

### `trees`
- id, user_id, name, created_at, updated_at

### `persons`
- id, tree_id, first_name, last_name, birth_year, birth_state, birth_country, death_year, notes, confidence (0–100), parent_ids (JSON array), spouse_ids (JSON array)

### `search_results`
- id, person_id, source (familysearch/wikitree/chronicling/etc), record_type, url, raw_data (JSON), found_at

### `gaps`
- id, person_id, gap_type (missing_parents/missing_birth/missing_death/etc), suggested_source, suggested_query, resolved (bool)

### `alfred_messages`
- id, person_id, tree_id, role (user/assistant), content (text), created_at
- Stores Alfred's conversation history per person. Last 10 messages loaded as context on each new message.

---

## Feature: Gap Finder Engine

The core algorithm runs when a user submits a person or tree for research.

### Search Cascade by Heritage Tier

**US tier** (all users):
1. FamilySearch: Census 1790–1950 by name + birth year ± 3
2. FamilySearch: Vital records (birth/death certificates)
3. FamilySearch: Military service and pension records
4. WikiTree: Match by name + dates
5. Chronicling America: Obituary search

**+ European Roots tier:**
1. All US tier steps
2. Ellis Island manifest search → extract origin village
3. FamilySearch EU partition → German/Irish/Italian/Polish church records
4. Matricula Online → German/Austrian/Czech Catholic parishes
5. Riksarkivet → Swedish church records
6. AI translation of returned documents (OpenRouter)

**African American Heritage tier:**
1. All US tier steps
2. Freedmen's Bureau records (1865–1872) via FamilySearch
3. US Slave Schedules 1850–1860 (name search via FamilySearch)
4. WPA Slave Narratives (Library of Congress API)
5. Plantation and estate records (FamilySearch)
6. DNA ethnicity hints → African region identification
7. Alert user to "1870 wall" status with specific guidance

**Asian/Pacific Heritage tier:**
1. All US tier steps
2. Chinese Exclusion Act papers (NARA)
3. Japanese Koseki records (FamilySearch Japan)
4. Korean Hoju records (FamilySearch Korea)
5. Chinese village records — Guangdong, Fujian (FamilySearch China)
6. British colonial records — Indian subcontinent
7. AI translation (Chinese/Japanese/Korean/Hindi)

### Gap Classification
After searching, each person gets:
- `confidence` score 0–100 (% of expected data fields found)
- `gaps[]` list: each gap has type, suggested source, and suggested search query
- Persons sorted by lowest confidence first in the UI

### AI Synthesis
After each search cascade, OpenRouter (claude-3-haiku for cost) generates:
- A 2-3 sentence plain English summary of what was found
- Specific "where to look next" recommendations per gap
- For non-English documents: translation + key field extraction

---

## Feature: Visual Family Tree

Built with D3.js. Renders as a top-down generational tree.

### Node States
- **Blue solid border** — Confirmed (confidence ≥ 80%)
- **Yellow dashed border** — Partial (confidence 40–79%)
- **Red dashed border** — Gap / unknown person placeholder
- **Gray dashed** — Inferred (relationship suggested by AI, not confirmed)

### Click Interaction
Clicking any node opens a **Person Card** overlay with two panels:

**Left panel — Research data:**
- Name, birth/death dates and locations
- Confidence percentage bar
- Checklist: what's confirmed ✓ vs. what's missing ✗
- Source links for each confirmed fact
- "Search Now" button triggers a fresh gap-finder run for that person
- "Add Parent" / "Add Spouse" buttons to manually expand the tree

**Right panel — Hometown Visual:**
- Photo of the town, village, or region where the person was born/lived (Wikimedia Commons API)
- Historical map of the region at the approximate time period (Old Maps Online / David Rumsey collection)
- AI-generated "life context" blurb — 2–3 sentences describing what daily life looked like in that place and era (OpenRouter, costs 5 tokens)
- Example: *"Your ancestor lived in rural County Cork during the height of the Great Famine (1845–1852). Most families in this region depended entirely on potato crops. An estimated 1 million people emigrated from Cork during this decade — your family was among them."*

If no hometown photo is available (Wikimedia returns no results), the panel shows a regional landscape photo and flags the photo as approximate. If birth location is unknown entirely, the right panel is hidden.

**Alfred panel — AI Concierge (below the two panels):**

Alfred is the app's AI research assistant. He lives at the bottom of every person card and has full context on that specific person — every search result found, every gap identified, every source URL, the hometown photo on screen, and the full family tree structure.

Alfred can answer questions like:
- *"Where did you find the pension record?"* → "I found pension #5802 in FamilySearch's military records collection, filed August 1832 in Allen County, Kentucky. The record names his wife Sarah and lists service dates 1812–1815. [View original →]"
- *"Why can't you find his parents?"* → Explains the 1870 wall, names the specific Freedmen's Bureau collections to check, links to them
- *"Show me what this town looked like"* → Fetches and displays an additional Wikimedia or LoC image inline in the chat
- *"What does Geburtsname mean?"* → Translates German/Italian/Polish/etc. terms found in records
- *"Who else in my tree might connect to this person?"* → Cross-references the full tree, suggests likely relatives
- *"What should I search next?"* → Generates a prioritized to-do list of next research steps based on current gaps

**Alfred UI:**
- Small avatar/icon labeled "Alfred" with a subtle British butler aesthetic (bowler hat icon)
- Chat input at the bottom: *"Ask Alfred about [person name]..."*
- Responses appear as chat bubbles, newest at top — scrollable history per person
- Images requested by Alfred render inline in the chat bubble
- Source links render as clickable chips, not raw URLs
- Alfred's chat history is saved per person per tree (stored in DB)

**Alfred token cost:** 10 tokens per message sent (covers OpenRouter + image fetch if requested). Token cost shown as a small label next to the send button: "10 tokens". If balance is below 10, the input is disabled with a "Top up to chat with Alfred" prompt.

**Alfred's context window (sent with every message):**
- Person's full data: name, dates, places, confidence score
- All search results for that person (source, record type, URL, key fields)
- All gaps for that person (type, suggested source, suggested query)
- Current hometown photo URL and map URL
- Brief tree summary: who the person's parents, children, and spouse are (names + dates only)
- Last 10 messages of conversation history for continuity

**Alfred model:** OpenRouter `anthropic/claude-3-haiku` — fast, cheap, accurate on genealogy tasks. Fallback to `openai/gpt-4o-mini` if Haiku unavailable.

### Input: Flexible Entry
Users can start with as little as:
- First + last name (required minimum)
- Birth year ± 5 years (optional)
- Birth state or country (optional)
- Any additional known relatives

The system uses whatever is provided. More input = higher initial confidence.

---

## User Accounts & Subscriptions

### Free Tier
- 5 searches per day
- View gap report and tree
- Cannot save tree between sessions
- Cannot export
- No document upload

### Paid Tiers (saved trees + unlimited search)
| Tier | Monthly | Annual | Heritage Coverage |
|------|---------|--------|-------------------|
| US Records Only | $7.99 | $63.99 | US Census, vital, military |
| + European Roots | $12.99 | $103.99 | + ship manifests, EU church records, AI translation |
| African American Heritage | $12.99 | $103.99 | + Freedmen's Bureau, slave schedules, WPA narratives |
| Asian/Pacific Heritage | $12.99 | $103.99 | + Exclusion Act papers, Koseki, Hoju, colonial records |
| All Access | $19.99 | $149.00 | Every tier, every country |

All paid tiers include: unlimited searches, save tree, PDF export, share tree with family, AI gap analysis.

Annual pricing is 33% off monthly.

Stripe handles billing. Tier stored in DB, checked server-side on every API call.

---

## Feature: Document Upload (Post-MVP)

After core search is working:
- User uploads photo/scan of handwritten document (JPEG/PNG/PDF)
- Backend sends to OpenRouter vision model
- Extracts key fields: names, dates, places, relationships
- Optionally translates (German, Italian, Polish, etc.)
- Prompts user to confirm extracted data before adding to tree

This mirrors the session workflow used for the Henderson family PDFs.

---

## Routes

### Public
- `GET /` — landing page with demo tree
- `GET /pricing` — pricing tiers
- `POST /search` — free guest search (rate limited to 5/day by IP)
- `GET /auth/familysearch/callback` — OAuth callback

### Auth required
- `POST /api/trees` — create tree
- `GET /api/trees/<id>` — load tree
- `PUT /api/trees/<id>` — save tree
- `POST /api/persons/<id>/search` — trigger gap finder for one person
- `POST /api/persons/<id>/upload` — document upload (post-MVP)
- `GET /api/trees/<id>/export/pdf` — PDF export
- `POST /webhook/stripe` — Stripe billing webhook

---

## Security

- Passwords hashed with bcrypt
- JWTs for session auth (httpOnly cookie)
- FamilySearch OAuth2 token stored server-side, never exposed to browser
- OpenRouter key server-side only
- Stripe webhook signature verification
- GDPR: EU users get data export and deletion endpoints
- Rate limiting on guest search (5/day per IP via Redis)

---

## Tech Stack Summary

| Component | Technology |
|-----------|------------|
| Backend | Flask (Python 3.11) |
| Database | PostgreSQL |
| Cache | Redis |
| Frontend | Vanilla JS + D3.js + CSS Grid |
| Hosting | Railway |
| Payments | Stripe |
| AI | OpenRouter (claude-3-haiku for gap analysis, haiku-vision for doc upload) |
| Core genealogy API | FamilySearch (free, OAuth2) |
| Secondary APIs | WikiTree, Chronicling America, Riksarkivet, Ellis Island/NARA |

---

## MVP Scope

**In MVP:**
- Free guest search (rate limited)
- FamilySearch integration (US records)
- Visual tree with D3.js
- Person card overlay
- User accounts
- Stripe subscriptions (all tiers)
- US tier search cascade
- European Roots tier search cascade
- African American Heritage tier search cascade
- Gap classification + AI suggestions (OpenRouter)
- Save/load tree
- PDF export
- Share tree link

**Post-MVP (Phase 2):**
- Document upload + OCR + AI extraction
- Asian/Pacific Heritage tier
- Hispanic/Latino tier
- Native American/Indigenous tier
- Jewish heritage (JRI-Poland, Yad Vashem)
- Collaborative family editing (multiple users on one tree)
- DNA ethnicity hint integration
- Mobile app (React Native) once web revenue established

---

## Competitive Position

| | RootBridge | Ancestry | MyHeritage |
|--|--|--|--|
| Monthly cost | $7.99–$19.99 | $24.99–$59.99 | $19.99–$149.99 |
| AI gap analysis | ✓ | ✗ | ✗ |
| European bridge | ✓ | Partial | Partial |
| AA heritage specialist | ✓ | ✗ | ✗ |
| Free public APIs only | ✓ | ✗ (proprietary vault) | ✗ |
| Open to developers | Yes | No | No |

Key message: **67–87% cheaper than Ancestry. Smarter because it uses AI to tell you exactly what's missing and exactly where to find it.**

---

## Success Metrics (6 months)

- 500 free signups → 50 paid conversions (10% conversion rate)
- 50 subscribers at avg $12/mo = $600 MRR
- Target: $2,500 MRR by month 12 (200 paid subscribers)
- Long-term: 1% of the 200M European-descent Americans = 2M potential users
