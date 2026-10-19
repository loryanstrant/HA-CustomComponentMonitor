# Decisions

Dated, non-obvious choices and the quirks that forced them. One entry per
decision, each ending in a lesson worth carrying elsewhere.

## 2026-08-23 — The card editors are built on `ha-form`

All three visual editors were hand-built `innerHTML`, producing browser-default
form controls inside Home Assistant's own card dialog. They matched none of
their neighbours and followed the browser's `color-scheme` rather than the
active theme, so on a dark theme they were light-mode boxes in a dark dialog.

The stronger reason was structural. `_render()` replaced the whole subtree, and
it ran on every `setConfig()` — which the card dialog calls back after every
`config-changed`. So the editors destroyed the control the user was operating.
The Update Action Tracker's title box fired on `input`, meaning every keystroke
replaced the `<input>` being typed into. `HA-Laundry-Weather` had the visible
form of this (a dropdown flashing open and shut) and fixed it in v0.3.1 with an
equality guard before re-rendering; v0.4.0 replaced the guard with `ha-form`,
where the form element is created once and thereafter only `.hass`, `.schema`
and `.data` are assigned. There is no `innerHTML` left to blow away.

**Lesson:** when a bug is "we destroyed the thing the user was holding", an
equality guard is a promise and the wrong shape is the cause. Move to a
structure where the destructive step does not exist, and the next edit to
`_render()` cannot reintroduce it.

## 2026-08-23 — One editor per card, not one shared editor keyed by card type

`HA-Transformers-Allspark-UI` keys a single editor off `config.type` and picks
its schema from a map. That is right there, and wrong here, for a reason that is
invisible from the source: Allspark is a **bundle** — one built file — whereas
`__init__.py` registers each of this repo's three cards as its own
`res_type: module` Lovelace resource, each with its own `?hacstag=<mtime>`
cache-bust.

A shared editor needs a fourth resource, and two faults follow:

- The modules load in parallel. If `getConfigElement()` runs before the shared
  module has defined the element, Home Assistant assigns `.setConfig`/`.hass` to
  an un-upgraded `HTMLElement`; they become plain expando properties and the
  class's setters never fire on upgrade. The editor opens blank, intermittently.
- A static `import "./ccm-card-editor.js"` resolves against the importing
  module's URL and **drops the query string**, so the shared file would be
  served with no `hacstag` and cached forever — the stale-bundle trap this
  fleet already knows.

The price of the choice is about 35 lines of identical plumbing repeated three
times. That is cheaper than an intermittent blank editor.

**Lesson:** a pattern borrowed from another repo carries that repo's build
model with it. "One file at runtime" and "one file in the editor" are different
claims, and only the first one makes a shared module safe.

## 2026-08-23 — The editor writes nothing the user did not choose

The title boxes used to be pre-filled with the card's default title, and
`getStubConfig()` baked every default into every newly-added card. That is not
cosmetic: `_riu_migrateConfig` exists in this repo solely to strip
`RIU_LEGACY_TITLE`, the old "Recently Installed but Unused" string, back out of
dashboards where an earlier stub had frozen it. Renaming the card did not reach
anyone who had ever added it.

So the rebuilt editors start the title blank, name the default in helper text
underneath, and prune the emitted config: an empty title is dropped rather than
stored as `""`, and any value equal to the card's own default is not persisted.
`getStubConfig()` is trimmed to match.

**Lesson:** a default that has been copied into user data is no longer a
default — it is a value you now have to migrate. Store the absence, and let the
code supply the default at read time.

## 2026-10-20 — New in HACS: automatic hiding, not flagging

| Decision | Chosen | Rejected |
|---|---|---|
| Foreign-country items | **Hidden automatically**, with an always-available audit list | Flag-only; collapsed-but-visible group |
| Categorisation | **Local AI for every item** (country rules pre-filter so hidden items cost no inference) | Rules-first hybrid; rules only |
| Hiding vs. flagging | Wrong hides are recoverable from an audit list; wrong flags leave 40 irrelevant rows on screen and solve nothing | Flag-only designs |
| Country matching | **Short codes case-sensitive as tokens** (`AU` not `au`, standalone to avoid matching inside "status"); collision-prone codes blocklisted | Full country names only |
| Category rule | **Any home-country signal wins outright** — biased towards keeping, because a wrong keep costs one glance and a wrong hide is invisible | Tied matches; equal weight |
| Inference | **Local AI for every item**, batched so a 9B model never blocks a scan | Cloud AI; rules only |
| Architecture | **A fourth card, different cadence** | A section inside the existing update card |
| Grouping | **Alphabetical by category, "Other" pinned last** | Newest-first; busiest-first |
| Filter | **All / Integrations / Cards**, reusing the chip control the existing cards already use | No kind filter; two separate cards |

Hiding foreign items automatically rather than flagging them (or collapsing them) is non-obvious but critical. A flag-only design leaves the same 40 irrelevant repositories on screen and solves the core problem not at all — you still read past them weekly. Collapsed groups hide them but don't solve triaging. Automatic hiding removes them entirely, with an audit list always available for the one in a hundred times the heuristic was wrong. A wrong hide costs one click to restore and a "never hide again" exemption; a wrong keep costs one glance. The bias is deliberate.

Country matching uses case-sensitive short codes as whole tokens — `AU` matches the country but not the substring inside "status" or "bus", and `DE` matches Germany but not "Delivery". Collision-prone ISO codes (`IT`, `IN`, `NO`, etc.) are blocked as bare codes and reached only via their full names, so a two-letter code never silently matches the wrong country. Any home-country signal beats every foreign signal: "AU/NZ energy prices" is kept even if it also mentions Sweden, because a user typed both and meant both.

Categorisation uses the local AI Task entity already configured, batched at 10 per scan so a slow 9B model never blocks a scan. The country pre-filter runs first, so 40 hidden foreign items cost zero inference calls. Results are cached per repository, so a category is never regenerated.

It is a fourth card (not a section inside the update tracker) because the two solve different problems on different schedules: new items arrive in bulk; updates trickle in one or two at a time. The same cadence, the same card would make both slower and more cluttered.

Categories are alphabetical with `Other` pinned last (independent of where `O` would sort), controlled by the same All / Integrations / Cards chip filter the update tracker already uses — familiar, not novel.

**Lesson:** a heuristic that is hard to notice when wrong — hiding without flagging — only works when every wrong hide is recoverable and auditable. Make the audit list always reachable, let the user reverse course with one click and a "never again" exemption, and bias the heuristic towards false positives (wrong keeps) because they cost less than false negatives (wrong hides).

## 2026-10-20 — What the country filter treats as evidence, and what the sensor publishes

Running the heuristic above over a real HACS store rather than a hand-picked test set found two different kinds of problem, and both were fixed by the same principle: a signal only counts when it's unambiguous, and a count is never the thing that gets capped.

| Decision | Chosen | Rejected |
|---|---|---|
| Collision-prone short codes (`TR`, `SG`, `ES`, `ESP`, `PL`, `FI`) | Blocklisted as bare codes, reached only by full name or demonym | Trust every ISO code as a standalone token |
| A short code followed by `-`/`_` and a digit | Never matched (model number or protocol) | Treat any hyphenated suffix as disqualifying |
| The author half of `owner/repo` | Never scanned | Scan the whole `owner/repo` string |
| A demonym next to "English" within 40 characters, no sentence break | Discarded as a language credit, not a country match | Trust every demonym equally |
| Sensor attribute size | Capped at 300 visible / 100 hidden rows, newest first; counts uncapped | Publish every row regardless of store size |

The short-code blocklist grew once the matcher ran over a real 2,261-repository store: `TR` was matching "FRITZ!Box CallMonitor ... TR-064" and "GenieACS TR-069", `SG` was matching "Siedle SG-150", `ES` was matching the "Renpho ES-CS20M" scale, `ESP` was matching every ESP32/ESP8266 board in the store, `PL` was matching "Fantasy PL" (the football league) and "Pool Lab PL Max Series", and `FI` was matching "Poolex Silverline FI pool heat pumps" — none of which has anything to do with Türkiye, Singapore, Spain, Poland, or Finland. Each of those countries stays reachable through its full name or demonym, so blocklisting the code loses no real hide. The model-number guard is the general form of the same fix: a short code followed by a hyphen or underscore and then anything containing a digit is read as a model number or protocol rather than a country, because the digit is what makes "TR-064" safe to exclude while "UK-based" and "US-only" — which carry no digit — still match.

The `owner/repo` fix came from the same pass: an author's GitHub handle says who wrote the integration, not who it's for, and handles name countries constantly — "Jef-NL/trmnl-webhook-push" (a generic e-ink webhook bridge) and "Smart-Home-Assistant-UK/homeassistant-trading212" (a generic trading integration) were both being hidden on the strength of their author's handle alone. Only the repository half is scanned now; a country named in the repository itself, like "energy_hub_poland", still matches exactly as before.

The language-list rule is the narrowest of the five and exists for a specific, repeated shape: a demonym is also the name of a language, and a repository that lists the languages its UI is translated into reads exactly like one built for that country. A word-clock card ("Supports German, Swiss German (Bärndütsch) and English"), a quiz game ("5,796 questions in English, German and Spanish"), and a SolaX energy card ("English & Slovak, visual editor") were all being hidden this way. The fix discards a demonym match when the word "English" appears within 40 characters of it with no sentence-ending punctuation in between — because this house runs Home Assistant in English, and a repository naming another language *alongside* English is telling you what it's translated into, not who it's for. It deliberately never applies to a country's own name, a bare code, or a brand; it never reaches across a full stop ("Norwegian electricity prices. Docs in English." still hides); and it never fires on GitHub topics, which are scanned one word at a time, so a lone `polish` topic is still hidden — the audit view is the intended remedy for that residue. Measured on the 2,261-repository store, this took hides from 178 to 175 and lost no correct one.

The sensor side of this entry is a different kind of problem with the same shape: HACS stores are large enough that the fix isn't a smarter match, it's a limit. The same 2,240-new-repository store that motivated the blocklist above put 1.2 MB into this one entity's attributes, and Home Assistant re-sends that payload to every connected dashboard on every refresh — it isn't a recorder cost, it's a websocket cost, and excluding the entity from the recorder (already done in code) does nothing for it. The sensor now publishes at most 300 visible items and 100 hidden items, newest-first with ties broken by stars, which took the same store's attributes to 221 KB. Critically, only the two big lists are capped: the sensor's state, `total_new`, `hidden_count` and `dismissed_count` all keep describing the whole queue, and `items_truncated`/`hidden_truncated` say exactly how many rows were held back, so the card can say "showing 300 of 2,069 to review" and have that be true rather than a guess.

**Lesson:** a heuristic found wrong by inspection and a heuristic found wrong by running it over real data are different exercises, and only the second one finds collisions like "TR-064" or a translated-language credit — these don't show up in a small hand-built test set because nobody writes a test case for a bug they haven't seen yet. And a cap on what gets published is not the same decision as a cap on what gets counted: the numbers on screen have to stay true even when the rows backing them don't all fit.

## 2026-10-20 — A hide only persists if the place that decides visibility re-derives it

Code review of the New in HACS branch found that the AI's country judgement — the "second hide chance" that catches a USPS or PostNord integration whose text never names a country — was written to storage and then thrown away an hour later. Both bugs it found are the same mistake in two different places: a decision recorded in one component and consumed by none.

| Decision | Chosen | Rejected |
|---|---|---|
| Where an AI-sourced hide lives | Re-derived from the cached `country_scope` by the scan, every cycle | Trusted from the `auto_hidden` record written by the AI batch |
| Who decides whether a scope is foreign | One predicate (`ai_scope_hides`), called by both the scan and the batch | Each caller applying its own version of the rule |
| An AI scope against a home signal in the text | Home wins, exactly as it does against a rules match | The AI's judgement overrides the text |
| Reading the AI's `country_scope` | Multi-word country names consumed first, then tokenise on any non-word character | Split on whitespace and commas, match single words only |

**The disappearing hide.** `ComponentScanner.scan_new_items` is the only place visibility is ever decided, and it decided purely from the rules matcher: if `classify()` found nothing, it called `clear_hidden()` and showed the item. So the AI batch would hide something correctly, write `source: "ai"` to storage, and the very next scan would wipe that record and un-hide it — permanently, because the verdict was by then cached, `ai_pending` was false, and the item would never be sent to the model again. No error, no log line, nothing in the attributes: the only way to see it was to diff two registry snapshots. The fix is to treat the cached `country_scope` as evidence on the same footing as the repository's own text, re-read on every scan. That also means a `NEW_ITEM_AI_PROMPT_VERSION` bump re-opens those hides for free, which is the right behaviour and falls out of the design rather than needing its own code.

**The scope the matcher could not read.** `scope_is_foreign()` split the model's answer on whitespace and commas and looked each word up, but the lookup table keys multi-word names whole — "New Zealand", "United Kingdom", "Czech Republic", "South Korea", "The Netherlands". The prompt asks the model to *name the country*, so prose is the expected answer shape, and for every multi-word country the tokens matched nothing, the scope read as "names no country we recognise", and the bias-to-keeping rule kept the item. A third of the table was silently exempt from the only check that existed for it. Multi-word names are now consumed longest-first before tokenising, and the tokeniser splits on any non-word character so "AU/NZ" and "AU-NZ" are understood too — `\w` rather than `[a-z]`, so "España" survives as one token instead of becoming "espa" + "a".

**One declined finding, recorded because declining it is also a decision.** The review also proposed blocklisting the demonym "Polish" the way `Pole`/`Poles` already are, on the grounds that "polish" is an ordinary English word and a dashboard card promising to "add some polish" would be hidden. It is a fair reading, and it was declined on the measurement: across the whole 2,261-repository store, "poland" matched three repositories and all three were correct, while "polish" matched exactly one — the `polish` GitHub *topic* on a generic encoding fixer, which is the known residue the audit view exists for. Blocklisting the demonym would have lost "Polish energy spot prices" and "Polish rail departures", neither of which names Poland or carries `POL`, in exchange for a false positive nobody has yet observed. Two tests already pin those hides deliberately. If an ordinary-English "polish" ever does show up in a real store, the blocklist is a one-line change and this paragraph is the context for making it.

**Lesson:** the author is the worst person to spot a decision that is written but never read, because they remember intending the consumer. Both of these bugs were invisible to a 316-test suite and to a live smoke test — the first because its symptom appears one scan cycle later than any test looked, the second because every test case used an ISO code while the prompt invites prose. Ask a reader who was not the writer, and give them the diff rather than the conclusion.

## 2026-10-20 — A name HACS "already has" is a name HACS generates at display time

The card was listing repository slugs — `ha-checklist-card`, `ScentAirHABridge`, `philips_shaver` — where the HACS panel, looking at the same store, lists "Mushroom", "Vertical Stack In Card" and "Frigate". The obvious conclusion is that HACS stores a name and the scanner was reading the wrong field. It does not, and it was not.

| Decision | Chosen | Rejected |
|---|---|---|
| Where the display name comes from | HACS's stored name where it exists; otherwise derived from the repository name | Fetch the repository's `hacs.json` from GitHub at scan time |
| Preference order | `repository_manifest.name` → `manifest_name` → `name`, matching HACS's own `display_name` | `manifest_name` first (what the code did), which disagrees with HACS whenever both are set |
| A `name` equal to the repository slug | Not a display name — fall through to deriving one | Accept any non-empty `name` |
| The derivation | Strip HA-flavoured decoration, split camelCase, preserve the author's capitalisation | HACS's own `slug.replace("-"," ").title()` |
| `owner/repo` on the card | Tooltip on the row, plus the existing GitHub link | A second visible line under every name |

Measured against the production store: of 2,244 new, not-installed integrations and cards, **zero** carried a display name. `repository_manifest` is `{}`, `manifest_name` is absent, `name` is `None`. Only 5 of 2,322 not-installed repositories anywhere in that store had one, against 45 of 46 installed ones — because HACS only fetches a repository's contents when you install it. Everything else in the store it knows by name and description from the index alone.

HACS's panel still shows something readable because `HacsRepository.display_name` ends in `self.data.full_name.split("/")[-1].replace("-", " ").replace("_", " ").title()`. "Vertical Stack In Card" is not a name anyone wrote down; it is that line. So the fix was never "read the field" — there is no field — it was "generate the name the same way HACS does, where HACS has nothing".

Two deliberate departures from HACS's version. First, `.title()` lower-cases everything it did not capitalise itself, which turns `ScentAirHABridge` into "Scentairhabridge" — worse than the slug it started from. Tokens the author capitalised are left alone and a short initialism list is forced upper, so `pv_management` reads "PV Management" rather than "Pv Management". Second, the Home-Assistant-flavoured decoration that half the store carries — `ha-`, `hass-`, `homeassistant-`, `lovelace-`, `-integration`, `-component` — is dropped, because in a list where every row is a Home Assistant add-on it is the one word that distinguishes nothing. Across the real store that produced a different name from HACS's rule for 1,526 of the 2,244 items and an empty name for none.

Five edge cases came out of running it over the store, and out of a review of the diff, rather than out of imagining them. A single leading lowercase letter is not a camelCase boundary — the split requires two lower/digit characters before it, or `iOS` becomes "i OS" and `FiOS` becomes "Fi OS". A single leading capital is not an acronym boundary either, for the mirror-image reason: `HAsmartirrigation` was being cut into "H Asmartirrigation", which is worse than both leaving it whole and what HACS itself shows, so that split now needs two capitals behind it as well — `ScentAirHABridge` still reads "Scent Air HA Bridge". A preposition left dangling by a removed suffix goes with it, so `DTE-Rates-for-Home-Assistant` reads "DTE Rates" and not "DTE Rates For" — but **only** when a suffix was actually removed, because the first version of that rule had no such gate and quietly ate the last word of `awesome-plug-in` and `listen-in-on`, names that were never decorated at all. Stripping can never consume the whole name: a repository literally called `ha-component` keeps its slug rather than rendering as a blank row. And the "this `name` is just the slug" test compares on a case- and separator-free signature, so a stored `My-Repo` against the slug `my_repo` is recognised for what it is instead of slipping through on a difference of punctuation.

The repository path is not shown as its own line. The name now carries the meaning, the kind chip says Integration or Card, and the stars/downloads/age line is already dense; adding `owner/repo` under every row would spend a line on something that matters at the moment you decide to install, not at the moment you triage. It is on the row's tooltip and one tap away behind the GitHub link, which is also where HACS itself puts it.

**Lesson:** "surely the upstream already has this data" is a hypothesis, not a premise, and it costs one query to settle. The right move was to go and count — 0 of 2,244 — before writing anything, because the answer decided whether this was a one-line field fix or a derivation with its own test file. It also pays to read how the upstream renders it: HACS's fallback line is both the explanation for the names on screen and the specification worth matching.
