# Spec — New in HACS: review, auto-filter and bulk-dismiss

**Status:** agreed 2026-10-20 · **Target version:** 1.15.0 · **Owner:** @loryanstrant

## Problem

HACS adds new integrations and cards to its store in bulk. There is no way to triage them:

- Measured on this house's production HA on 2026-10-20: **120 repositories flagged new**
  (86 integrations, 34 cards) out of 4,570 known and 220 installed.
- A large share are irrelevant on sight because they are built for one country — Swedish
  post, Dutch public transport, US utilities. Scanning past them is pure overhead, every time.
- The rest arrive as one flat, undifferentiated list, so "is there anything here about
  energy?" cannot be answered without reading all of it.
- Nothing remembers that an item has already been looked at and rejected, so the same
  rejects reappear on every visit.

Custom Component Monitor already reads the file that holds all of this
(`.storage/hacs.repositories`, via `ComponentScanner._load_storage_file`), and already
discards everything that is not installed. The data is in hand; only the triage is missing.

## Goal

Turn a 120-item HACS drop into a two-minute review: hide what is for another country,
group the remainder by what it actually does, and dismiss a whole group in one tap.

## Decisions taken (2026-10-20)

| Decision | Chosen | Rejected |
|---|---|---|
| Items for another country | **Hidden automatically**, with an always-available audit view | Flag-only; collapsed-but-visible group |
| Categorisation | **Local AI for every item** (country rules pre-filter so hidden items cost no inference) | Rules-first hybrid; rules only |
| Where it lives | **A new fourth card** | A section inside the existing update card |
| Country matching | Must handle short forms **AU / AUS / Aus / Oz** case-sensitively; collision-prone ISO codes blocklisted | Full country names only |
| Group order | **Alphabetical by category name**, with `Other` always pinned last | Newest-first; busiest-group-first |
| Kind filter | **All / Integrations / Cards**, reusing the filter control the existing cards already use | No kind filter; two separate cards |

Added after the mockup review on 2026-10-20, in the owner's words: *"have the categories
listed alphabetically, and the ability to filter by card/integration/all (like the solution
currently does)."* The filter is not a new control — it is the same chip row the update
tracker card already ships, relabelled to the two kinds this entity carries.

## Flow

### First run

1. Scan reads `.storage/hacs.repositories` and selects repositories flagged `new`.
2. Each is recorded in a first-seen registry (the date never moves forward afterwards).
3. The country test runs on the repository name, description and GitHub topics. A foreign
   match with no home-country match → auto-hidden, with the matched term recorded.
4. Everything surviving is queued for the local AI, which returns a category and a
   country scope. Results are cached per repository.
5. The AI runs in batches (default 10 per coordinator cycle) so a 9B local model never
   blocks a scan. The card shows how many are still pending.
6. `sensor.hacs_new_items` publishes the visible items grouped by category.

### Reviewing

1. The card shows `New in HACS — 83 to review · 37 hidden`, one collapsible group per
   category, **in alphabetical order with `Other` last**. Items within a group are newest
   first by default.
2. An `All` / `Integrations` / `Cards` chip row sits under the header. It filters the rows
   and the per-group counts; a group that empties under the filter disappears rather than
   showing a zero.
3. A row carries: name, kind (Integration / Card), stars, downloads, age, a two-line
   description, a tick box, a link to GitHub and a single-item dismiss.
4. Ticking rows reveals `Dismiss N selected`. A group header carries `Dismiss all (n)`.
5. `Show hidden (37)` opens the audit list; each row states why it was hidden
   (*matched "sweden" in description*) and offers **Restore**.
6. Restoring a country-hidden item also adds it to a never-hide list, so the rule cannot
   hide it again.

### Afterwards

Dismissed items never reappear, across restarts and across HACS clearing its own `new`
flag. The dismiss list is kept indefinitely.

## Acceptance criteria

| # | Criterion | How it is verified |
|---|---|---|
| A1 | New, not-yet-installed HACS items are visible in a sensor, distinct from installed ones | Unit test on a fixture store; DEV smoke with prod's real 120 items |
| A2 | `AU`, `AUS`, `Aus`, `Aust`, `Oz`, `Australia`, `Australian` and the state codes (`NSW VIC QLD WA SA TAS NT ACT`) all read as **home** | Table-driven unit test |
| A3 | `status`, `bus`, `Delivery`, `Attribute`, `inside`, `nominate`, `item` do **not** register as a country match | Table-driven unit test (negative cases) |
| A4 | `"…mail delivery from PostNord in Sweden"` is hidden; `"AU/NZ electricity prices"` is kept | Unit test using the real production descriptions |
| A5 | Any home-country signal beats every foreign signal | Unit test |
| A6 | Every auto-hide is explainable — country, matched term and which field it matched | Sensor attribute `hidden[]`; visible in the card's audit view |
| A7 | Items are grouped into the agreed plain-English categories by the local AI, with `Other` as the fallback for an unparseable answer | Unit test with a mocked AI; DEV smoke against the real Qwen entity |
| A8 | AI work is batched and never blocks a scan; progress is observable | `ai_pending` / `ai_in_progress` attributes; DEV smoke |
| A9 | A whole category can be dismissed in one action, and dismissal survives a restart | Service test + registry reload test |
| A10 | A restore un-hides and permanently exempts the item from the country rule | Registry test |
| A11 | The card renders correctly at desktop width and at ~380 px | Screenshots in `_IMAGES/`, checked against `standards/ux.md` |
| A12 | The card's settings panel is built on `ha-form`, matching the three rebuilt in 1.14.0 | Follows `docs/specs/card-editor-ha-form.md` |
| A13 | Version is bumped in lockstep: `manifest.json` plus all four cards' `CARD_VERSION` | Release gate |
| A14 | HACS clearing its own `new` flag does not empty the card | Scanner treats `new: true` **or** first-seen within a 30-day window as new |
| A15 | Category groups render alphabetically, with `Other` last regardless of alphabet | Card unit check + screenshot |
| A16 | `All` / `Integrations` / `Cards` filters the rows and the group counts, using the same chip control the existing cards use | Screenshot of each filter state; code reuses the existing `.filters`/`.chip` idiom |

## Category set

Held and displayed alphabetically, with `Other` pinned last:

Appliances · Climate & heating · Dashboard & theming · Energy & solar · Health & fitness ·
Lighting · Media & audio · Network & infrastructure · Notifications & voice ·
Presence & location · Security & cameras · Tasks & calendar · Transport & travel ·
Vehicles & EV · Weather & environment · **Other**

This set is **separate** from the existing `AI_CATEGORY_OPTIONS` used for release-note
categorisation (Bug fixes / New features / …) and must not be conflated with it.

## Non-goals

- **No installing.** The card reviews and dismisses; it never installs anything from HACS.
- **No cloud AI.** Categorisation uses the local AI task entity already configured.
- **No GitHub API calls** at scan time. Everything comes from HACS's own cached index, so
  items with no description and no topics stay thinly described rather than triggering a fetch.
- **No change to production HA** beyond reading its HACS store file. No prod restart, no
  prod install; testing happens on the DEV instance.
- **No notifications or to-do items** for new arrivals in this version.
- **Not a replacement for the HACS panel** — discovery and search stay there.

## Known limitations

- 9 of the 120 current items carry no topics and some carry no description at all, so their
  categorisation rests on the name alone and will often be `Other`.
- HACS stores cards under the category name `plugin`, not `lovelace`; the mapping to the
  user-facing word "Card" happens in the scanner.
- **HACS has no stored display name for anything you have not installed.** Measured against
  the production store: of 2,244 new, not-installed repositories, **none** carried a name —
  `repository_manifest` is an empty dict and `name` is absent. HACS's panel still shows a
  readable name for them because its own `display_name` property falls back to
  `slug.replace("-", " ").title()`. The card reads the stored name wherever HACS has one and
  otherwise derives one the same way, with the Home-Assistant-flavoured prefixes and suffixes
  removed and the author's own capitalisation preserved. `owner/repo` remains on the row's
  tooltip and behind the GitHub link.
- The country test is a heuristic. It is deliberately biased towards keeping an item
  (any home signal wins) and every hide is auditable, because a wrong hide is invisible and
  a wrong keep costs one glance.

## Technical plan

The full implementation detail — modules, storage schema, matching rules, service
signatures, test list and build order — is in the approved session plan and is reproduced in
the pull request description. Key anchors:

- New `countries.py` (table-driven matcher) and `new_items.py` (registry + dismiss store,
  patterned on the existing `install_dates.py`).
- `ComponentScanner.scan_new_items()` in `sensor.py`, selecting on `new` where the existing
  scanners select on `installed`.
- New entity `sensor.hacs_new_items`; new services `dismiss_new_items`,
  `restore_new_items`, `categorise_new_items`.
- New card `www/hacs-new-items-card.js` with an `ha-form` editor.
