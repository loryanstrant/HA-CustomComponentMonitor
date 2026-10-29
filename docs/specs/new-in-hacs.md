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

Held alphabetically with `Other` pinned last — 20 categories plus the fallback, since 1.17.0
(the original 16 are marked; see the *1.17.0* addendum for why the five were added and the
measurement that prompted it):

Appliances · **Automation & scheduling** · Climate & heating · **Covers & blinds** ·
Dashboard & theming · Energy & solar · **Garden & water** · Health & fitness · Lighting ·
Media & audio · Network & infrastructure · Notifications & voice · Presence & location ·
Security & cameras · **Sensors & devices** · **System & diagnostics** · Tasks & calendar ·
Transport & travel · Vehicles & EV · Weather & environment · *Other*

Every label carries a one-sentence definition in `NEW_ITEM_CATEGORY_DEFINITIONS`, which is
what the prompt actually shows the model; a label with no definition is a label the prompt
cannot explain, and a test asserts the two sets are equal.

The list is duplicated by hand in three more places — the two `category` selectors in
`services.yaml` and the card's own group ordering — so a label added here must be added
there too. The `services.yaml` copies are enforced by test; the card's ordering is
size-independent and needs no change.

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

## Addendum — shipped in 1.16.0

Three changes to the row and the group header, after using the card against a real store:

- **The display name is the link to GitHub**, and the separate `mdi:open-in-new` icon that
  used to carry that link is gone — one destination, one affordance, and the row only has
  width for two icons at 380 px. `owner/repo` is still on the name's tooltip.
- **An install button** (`mdi:download`) on every row. It does **not** install: it navigates
  in-app to that repository's own HACS screen, `/hacs/repository?owner=…&repository=…`,
  where the version picker, the README and the restart prompt live. The *No installing*
  non-goal above therefore still holds as written — the card hands the job to HACS rather
  than doing it.
- **`Dismiss all (n)` asks first**, in an in-card confirmation that names the category and
  the count. The per-row cross and `Dismiss N selected` are deliberately not confirmed.

Acceptance criteria added with them:

- A row whose `full_name` is not exactly `owner/repo` shows no install button rather than a
  link that resolves to the wrong repository.
- A modified click (Ctrl/Cmd/Shift) on the install button opens a new tab and leaves the
  dashboard where it is; a plain click navigates in-app without a full frontend reload.
- While the confirmation is open the rest of the card is `inert`, focus starts on **Cancel**,
  Escape cancels, and a click on the dialog's own text does nothing.
- A sensor update arriving while the confirmation is open updates the count in its label if
  the count changed, closes the dialog if the category emptied, and dismisses only the items
  the label counted at the moment **Dismiss** was pressed.

## Addendum — shipped in 1.17.0

### The measurement

`Other` had become the largest group on the card rather than the leftover pile A7 intends.
Observed by the owner at **96 of 165**; read off live `sensor.hacs_new_items` on this house's
production HA on **2026-10-27 20:48 AEDT**:

```
state 127 · by_category {Dashboard & theming: 33, Other: 58, Energy & solar: 23, Weather & environment: 13}
ai_enabled true · ai_pending 0 · ai_in_progress false · ai_last_error None
ai_last_run 2026-10-26T10:19:28Z · ai_rate_per_minute 178.4 · items_truncated 0
total_new 961 · dismissed_count 705 · hidden_count 129
```

`ai_pending: 0` alongside 58 settled `Other` items is what rules out the first hypothesis —
that the 300+ item backlog had simply not been worked through. Every one of those 58 was a
cached verdict, so the 1.15.1 pacing work was behaving correctly and the fault was in the
question being asked, not in how many times it was asked.

Reading all 58 by name, kind, description and topics:

| Pile | Count | Why it fell through |
|---|---|---|
| Tooling for HA itself (entity audits, recorder/statistics, backups, update pins) | ~24 | No category existed for the house's own plumbing |
| Lovelace cards with no domain subject (tap confirmations, date pickers, wall panels) | ~15 | Nothing told the model that `kind == "card"` already settles it |
| Covers, irrigation, schedulers, small device bridges | ~10 | No category existed |
| Genuinely off-topic | ~5 | Correctly `Other` |

Two aggravating factors: the prompt offered sixteen **bare labels** with no definitions, and
it said *"use 'Other' only when nothing else fits"* — which a 9B model reads as permission.

### What changed

- **Five new categories** — `System & diagnostics`, `Automation & scheduling`,
  `Garden & water`, `Covers & blinds`, `Sensors & devices` — with aliases, and the alias
  `"system"` **re-pointed** from `Network & infrastructure` (where it had been filing entity
  audits) to `System & diagnostics`.
- **Every category now has a definition**, and the catalogue of definitions leads the prompt,
  ahead of the item being judged. Leading with it is deliberate: it is identical for every
  item in a batch, so the local backend's prefix cache has the longest possible shared prefix
  to reuse. `Other` is now defined positively — *not about a home or Home Assistant at all* —
  rather than as a last resort.
- **A card that comes back `Other` becomes `Dashboard & theming`**, via one pure function
  applied at the single place that writes the AI cache, so the cached verdict, the published
  sensor attribute and the restore path cannot disagree. It only ever promotes the fallback;
  a card the model placed somewhere specific is left alone.
- **The whole store re-sorts itself on upgrade**, by bumping `NEW_ITEM_AI_PROMPT_VERSION`,
  which invalidates every cached verdict. Two to four minutes of background work after the
  restart at the measured throughput; the card stays usable and shows the progress.
- **The card opens with every group folded**, with an `Expand all` / `Collapse all` control in
  the existing chip row. Folding replaced "first two open": at ~20 groups, two open groups
  meant scrolling past two long lists to reach the rest of the week's drop.

Unchanged by explicit decision: **every item still costs one AI call** — the rules-first
hybrid rejected in the 2026-10-20 table above stays rejected — and group order stays
alphabetical with `Other` last.

### One bug fixed with it

Restore-by-category is the only consumer of the AI cache for items the card no longer shows,
and the re-sort above would have blinded it: a dismissed item is never re-queued (the queue
is built from the visible set), so once its stored `prompt_version` went stale, `get_ai`
would have returned `None` for all 705 dismissed items **permanently**. Restoring
`Energy & solar` would have restored nothing, and restoring `Other` would have un-dismissed
the entire backlog in one tap. `get_ai` gained an `include_stale` escape hatch, which only
the restore path passes. Semantically, restore-by-category matches the category an item
carried **when it was dismissed** — which is also the category that was on screen at the
time. Dismissals, never-hide overrides and hide records live in separate stores that never
consult `prompt_version`, so the bump cannot lose any of them.

### Acceptance criteria added

| # | Criterion | How it is verified |
|---|---|---|
| A17 | Every category has a definition, every alias resolves to a real category, and `"system"` resolves to `System & diagnostics` | `tests/test_new_item_taxonomy.py` |
| A18 | The prompt leads with the category catalogue and defines `Other` as off-topic rather than as a last resort | Prompt-text assertion in the same file |
| A19 | A `card` whose verdict is `Other` is stored **and** published as `Dashboard & theming`, including when the same pass hides it on country scope | `_apply_new_item_ai_result` tests |
| A20 | Restore-by-category still matches dismissed items after a `NEW_ITEM_AI_PROMPT_VERSION` bump, and restoring `Other` does not sweep up stale dismissals | `tests/test_new_items_services.py` |
| A21 | A13's lockstep is enforced, not merely documented: `manifest.json` and all four `CARD_VERSION`s must agree | `tests/test_card_staging.py` |
| A22 | The card opens with every group folded and `Expand all` / `Collapse all` acts on every group this pass knows about, not only the ones the kind filter shows | Card code + screenshots at desktop and ~380 px |

### Deliberately not fixed

The country-hidden items (129 at the measurement) sit at `ai_pending: true` indefinitely,
because the AI queue is built from the visible list only and `ai_pending` counts visible
items only. That is this spec's own decision — *country rules pre-filter so hidden items cost
no inference* — and the audit list is flat and ungrouped, so the stale label is never shown to
anyone. A handful of items hidden by the **AI's** country scope rather than by the word rules
will be re-judged during the re-sort, so the hidden count may move slightly; anything
manually restored is permanently exempt and cannot be touched.
