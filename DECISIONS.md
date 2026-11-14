# Decisions

Dated, non-obvious choices and the quirks that forced them. One entry per
decision, each ending in a lesson worth carrying elsewhere.

## 2026-11-03 — Install age is elapsed time, not local wall-clock time

Observed in GitHub Validate runs 481 and 482: HACS and Hassfest passed, but
`test_a_scanner_without_a_registry_still_reports_an_age` returned 8 rather than
9 days. Run 481 predates v1.17.2; the same commit passed before the November
US daylight-saving transition. The fallback `_install_info` subtracted two
local datetimes sharing a timezone, which measures wall-clock time: nine UTC
days spanning fall-back become eight days and 23 hours locally. Spring-forward
can overcount instead. The saved install-date registry already compares UTC.

The fallback now uses UTC too. The first twelve deterministic scanner cases
cover both transitions and an ordinary date across all four scan methods:
eight transition cases failed before the fix. Twelve further cases guard
sub-day ages, future dates and unknown dates. After the fix, all 501 tests pass;
Hassfest reports one integration and zero invalid. No workflow gate or assertion
was weakened, and the HACS review-sync behaviour is unchanged.

**Lesson:** a failure that appears on an unchanged commit after a clock change
can be real date arithmetic, not test flakiness. Pin both DST directions in
regression tests and use UTC when measuring elapsed time.

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

## 2026-10-20 — Pace the AI by time spent, not by items per cycle

v1.15.0 shipped a fixed budget of 10 AI calls per hourly scan, with an option to
raise the ceiling to 50. Watching it run in production showed what that actually
means: at 12:23:40 the integration sent ten items and had all ten answers back by
12:23:45 — 4.84 s, 0.48 s an item — then did nothing at all for the rest of the
hour. A 177-item backlog was therefore ~80 seconds of real AI work spread across
about eighteen hours of waiting. Raising the ceiling to 50 would have made it four
hours instead of eighteen, which is the same bug with a better number.

| Decision | Chosen | Rejected |
|---|---|---|
| What paces a pass | **A time budget** — default 20 s, settable 5–300 | A count of items per cycle, at any default or ceiling |
| How many items go at once | **Self-tuning**: throughput measured per wave size, 1→8 concurrent | A fixed concurrency; a user-set number; hill-climbing against one best-ever rate |
| Reaction to a failure | **Halve the wave immediately**; give up the pass after 3 consecutive all-failed waves | Keep the wave size and retry; abandon the pass on the first failure |
| When the budget is checked | **Between waves only** | Mid-flight, cancelling calls already sent |
| What happens when a pass ends with work left | **Another pass, 5 s later, until nothing is pending** | Wait for the next hourly scan; ask the user to call the service |
| Progress reporting | **Measured items/minute and an ETA**, as sensor attributes | A percentage, or nothing |

**Why a count can never be the right knob.** The correct number of items per hour
is a function of hardware nobody but the user has: a local 9B on a decent GPU
answers in 0.45 s, a CPU-only build in 30 s, a rate-limited cloud model in two
seconds but only twenty times a minute. Any default is wrong for most people, and
a ceiling only moves where it is wrong. A time budget is the same instruction
expressed in the one unit that means the same thing on every machine — "spend
twenty seconds on this, then get out of the way" — and it leaves the item count to
be *discovered* rather than configured. Measured on the production instance, the
same 177-item backlog that was taking ~18 hours clears in about two minutes.

**Why the concurrency tunes itself rather than being an option.** The budget alone
would still be throttled by sending one item at a time. So each wave is timed and
the next one is sized from the result. Throughput, not latency, is the thing being
maximised, because a backend that answers eight requests in 3 s is better than one
that answers one in 0.4 s even though the second looks faster per call. Failures
halve immediately — a backend refusing work should be backed off, not hammered —
and three consecutive all-failed waves end the pass, leaving the hourly scan as the
retry.

**Why throughput is remembered per wave size, not as a best-ever figure.** The first
cut of this controller kept one number, the best throughput ever observed, and grew
or shrank the wave against it. Review caught that this cannot recover. The bar only
ever ratchets upwards, so a single transient failure at the top of the ramp halved
the wave while leaving the bar at the *top size's* rate; every subsequent smaller —
and therefore legitimately slower — wave then read as a regression, and the size
walked down to one item at a time and stayed there for the life of the coordinator.
One flaky call would have reinstated the exact hourly trickle this release exists to
remove, and only a Home Assistant restart would have cleared it. The fix is to
record the measured rate *per wave size* and compare a size only against its
neighbours, so every decision is like-for-like; a size that has never been measured
is always worth one wave (never exploring is how a fast GPU ends up sorting two at a
time forever); a failed size is recorded as worth nothing rather than forgotten, or
the very next good wave would walk straight back into it; and after five waves with
no move the neighbours' records are dropped so they get re-measured, because the GPU
being busy once should not pin the wave size for a week of uptime.

**Why the budget is checked between waves and never mid-call.** A model slower than
the entire budget would otherwise make zero progress forever: start a wave, exceed
20 s, get cancelled, repeat. The loop checks the clock *before* sending a wave and
never after, and it skips that check for the first wave of a pass. So the slowest
imaginable backend still completes one wave per pass and the backlog still drains —
slowly, but monotonically. This is the difference between pacing and timing out.

**Why it chains itself instead of asking.** The request this came from was "no
manual effort, do the permanent fix". A pass that ends with items pending schedules
the next pass five seconds later, in a background task, repeating until `remaining`
hits zero — with two independent brakes: a pass that generated nothing stops the
chain (the next hourly scan is the back-off), and a hard cap of 500 passes stops a
pathological loop. The service is still there with `max_items`/`max_seconds` for
anyone who wants to bound one pass by hand, but nothing requires it.

The old `new_items_max_ai_per_cycle` constant is kept defined, unread, because
existing config entries still carry the key; the next options save drops it.

**Lesson:** when a limit exists to protect against unknown hardware, express it in a
unit that is the same on all hardware. "Ten items" encodes an assumption about how
fast one item is and is wrong by two orders of magnitude at the edges; "twenty
seconds" encodes none, and the quantity you actually wanted — how much work fits —
becomes something the code can measure at runtime instead of something the user has
to guess in a form. And the measurement is worth taking before changing the number:
the recorder history showing 4.84 s of work per hour is what proved the cap, not the
model, was the whole bottleneck.

**Second lesson, from the review.** An adaptive controller has to be judged on how it
*recovers*, not on how it converges — convergence is the easy half and the one the
happy-path tests cover. A monotone comparison (best-ever anything) is the smell: it
makes a transient fault permanent, and in a Home Assistant integration "permanent"
means until the user restarts Home Assistant, which they have no reason to connect to
a slow card. The test that earns its keep here is the one that fails a wave on purpose
and then checks the size climbs back.

## 2026-10-22 — Install is a link into HACS, not a download call

The install button on a New in HACS row navigates to
`/hacs/repository?owner=<owner>&repository=<repo>&category=<integration|plugin>`
and stops there. It does not call HACS's `hacs/repository/download` websocket
command, which is the other thing it could have done.

That route is current and it is the one HACS publishes: hacs/frontend's
`REDIRECTS` map (`src/hacs-my-redirect.ts`) resolves
`my.home-assistant.io/redirect/hacs_repository` to exactly that path, and its
repository dashboard resolves the repo by `owner/repository` rather than by the
numeric HACS id — which matters, because the sensor does not carry one. HACS
removed its install *services* some releases ago, so there is no supported
service call left to make; the websocket command is the only programmatic route
and it is internal.

Calling it would also have meant owning three things that are HACS's: which
version to install (the command takes one), showing the README before the user
commits, and the "restart Home Assistant" follow-up that an integration install
needs and a card install does not. Reimplementing those in a monitoring card is
how a monitor becomes a package manager. And a one-tap install sitting next to a
one-tap dismiss, on a row that is deliberately small, is a mis-tap that installs
something.

`category` is optional in the redirect contract, so it is omitted rather than
guessed when the item's kind is unknown — HACS resolves the repository without
it. Note HACS's own vocabulary here: a Lovelace card is a `plugin`, not a
`card`; the card's `kind` is translated at the boundary.

The navigation itself is `history.pushState` plus a `location-changed` event,
which is what Home Assistant's own `navigate()` does. A plain `<a href>` to an
HA panel costs a full frontend reload. The `href` is still a real one, so
middle-click, "open in new tab" and the browser's status bar all keep working —
the click handler cancels the default, it does not replace the anchor.

**Lesson:** when the upstream app has a screen for the thing you are about to
do, link to the screen. The version picker, the docs and the restart prompt are
not incidental chrome around the install — they *are* the install, and a button
that skips them is not a shortcut, it is a different and worse operation.

## 2026-10-22 — The display name is the GitHub link, and the link icon went

The row used to carry an `mdi:open-in-new` button that opened the repository on
GitHub. The name is now that link, and the icon is gone — not kept alongside.

Two affordances for one destination is a question the user has to answer every
time ("do those go to the same place?"), and the honest answer was yes. The
deciding constraint was width: at 380 px the row has space for about two icon
buttons, and the install button needed one of them. Keeping both would have
meant three icons and a wrapped row, to offer a second route to a destination
that already had one.

The tooltip still carries `owner/repo`. That is now the only place to see which
repository a row actually is without following the link, which is exactly why it
was kept when the name stopped being the slug.

**Lesson:** a new control is a reason to re-examine the old ones, not just a
reason to find space. The question is not "where does this fit" but "what is now
redundant" — and on a dense row, redundancy costs more than the feature earns.

## 2026-10-22 — Only Dismiss all is confirmed, and the confirmation is in-card

"Dismiss all (n)" sits one tap from the collapse toggle in a group header, and a
mis-tap silently clears an entire category. It now asks first, with a dialog that
names the category and the count: *Dismiss all 3 in Energy & solar?*

The other two dismiss paths are not guarded and should not be. The single-row
cross removes one item that the user is looking at. "Dismiss N selected" requires
every one of those rows to have been ticked first, which is as deliberate as an
action gets. Confirming those would be nagging, and a confirmation that fires on
safe actions is a confirmation people learn to dismiss without reading — which
is precisely the reflex that makes the dangerous one useless.

The dialog is drawn inside the card rather than with `window.confirm`, which is
unstyled, blocking, and looks like a browser error in a Home Assistant dashboard;
`ha-dialog` would mean depending on an internal frontend element from a card that
otherwise has no HA dependencies beyond `ha-card` and `ha-icon`. It is
`position: fixed`, not `absolute`: a card with several groups expanded is taller
than the screen, so an overlay centred on the *card* would open off-view from the
group the user just tapped.

Two details that are not cosmetic. The overlay is a child of the persistent
`<ha-card>` shell and is rendered by replacing only its own `innerHTML`, like
every other dynamic region — the shell is still built exactly once, so card-mod
styling survives. And the confirmation stores only the category, never the list
of keys: the keys are re-read when the user confirms. A sensor update landing
while the dialog is open therefore cannot make the button dismiss a different set
from the one its label counted; if the category empties in the meantime, the
dialog closes itself rather than confirming nothing.

**Lesson:** a confirmation dialog that captures its payload when it opens is a
race with whatever refreshes behind it. Capture the *selector*, resolve it on
confirm, and the dialog can never act on stale data — or re-render the label
against data it is no longer going to use.

## 2026-10-29 — "Other" was the taxonomy's fault, not the queue's

`Other` had become the biggest group on the New in HACS card — 96 of 165 on
screen for the owner, and 58 of 127 when the live sensor was read on 2026-10-27.
The first hypothesis, reasonably, was the backlog: over 300 items had gone
through the queue, so perhaps most of them simply had not been sorted yet.

The sensor settled it in one read. `ai_pending: 0`, `ai_in_progress: false`,
`ai_last_error: None`, last run finished the previous day, throughput 178
items/minute. Every one of those 58 items was a **cached verdict** — the model
had looked at each one and chosen `Other` deliberately. The 1.15.1 pacing work
was not implicated at all.

| Decision | Chosen | Rejected |
|---|---|---|
| What to fix | **The category set and the prompt** | Anything in the queue or the pacing |
| How many new categories | **Five** — System & diagnostics, Automation & scheduling, Garden & water, Covers & blinds, Sensors & devices | Merging into ~8 broader ones; adding none and accepting `Other` |
| What the model is shown | **Every label with a one-sentence definition**, catalogue first | Sixteen bare labels, as before |
| How `Other` is described | **Positively** — "not about a home or Home Assistant at all" | "Use `Other` only when nothing else fits" |
| Who still pays for an AI call | **Every item**, as before | The rules-first hybrid (rejected again) |

Reading all 58 by hand is what produced the categories rather than guessing
them: ~24 were tooling for Home Assistant *itself* (entity audits, recorder
extensions, discrete statistics, integration pins, backup helpers) — the house's
own plumbing, which the taxonomy had no word for at all; ~10 wanted covers,
irrigation, schedulers or small device bridges; only ~5 were genuinely
off-topic. A set of labels that has no home for the single largest thing the
store publishes is not a model problem.

Two wording faults made it worse. Sixteen bare labels is a multiple-choice
question with no key, so the model was inferring each category's scope from its
name — and "System & diagnostics" is exactly the kind of label whose boundary
cannot be guessed. Worse, the instruction *"use 'Other' only when nothing else
fits"* reads to a 9B model as permission rather than as a last resort; it now
says what `Other` **is** instead of when to reach for it. The definitions are
also placed *first*, ahead of the item being judged, because they are byte-for-byte
identical across a batch and the local backend's prefix cache can then reuse the
longest possible prefix for every call in a wave.

The old alias `"system" → Network & infrastructure` was the single most damaging
entry in the table: "system" is the one-word answer a small model reaches for
when a repository is HA plumbing, so entity audits and recorder tools were being
filed under networking. It now points at the new bucket. The genuinely
networking aliases (`network`, `infrastructure`, `router`, `nas`) were left alone.

**Lesson:** when a classifier's output is lopsided, read the actual rows before
touching the classifier. One sensor read ruled out the queue, and one afternoon
with 58 names produced both the missing categories and the two prompt faults —
neither of which would have been found by making the model bigger, the batch
smaller or the budget longer. And a label list is a prompt: a bare enum asks the
model to invent the boundaries you forgot to write down.

## 2026-10-29 — `kind` is evidence, and the card already knew it was a card

About 15 of those 58 `Other` items were Lovelace cards with no domain subject at
all — tap confirmations, date pickers, autocomplete rows, wall panels. The model
was right that they are not about lighting or energy. It was never told the one
fact that settles them: the sensor already knows each item's `kind`, and `kind ==
"card"` means the thing *is* dashboard furniture.

So a card whose verdict comes back `Other` is stored as `Dashboard & theming`.
The rule is a module-level pure function beside the existing `ai_scope_hides`,
and it is applied at the single place that writes the AI cache. That placement is
the whole decision: the cached verdict, the category published on the sensor
attribute and the category `restore_new_items` matches dismissed items on are
three different readers, and a version of this rule that fixed only one of them
would have shown "Dashboard & theming" until the next scan quietly put it back.
It cannot live in `_normalise_new_item_category`, which is a static method taking
only the raw string and has no access to the item.

It only ever **promotes the fallback**. A card the model placed in `Lighting`
stays in `Lighting` — the rule adds information where there was none rather than
overruling a judgement. It also still applies on the country-hide path, because a
hidden item's category is what restore matches on later.

The prompt was told the same fact in words as well, so the model gets the chance
to be right by itself; the post-rule is the backstop for when it is not.

**Lesson:** before improving a model's answer, check whether the caller already
holds the fact the model was guessing at. Structured data you have is strictly
better than inference you paid for — and the place to apply it is the single
choke point that writes the value, not wherever you noticed the problem.

## 2026-10-29 — A cache version bump is a change to every reader of that cache

Re-sorting the whole store on upgrade is one line: bump
`NEW_ITEM_AI_PROMPT_VERSION`, and `get_ai` returns `None` for every record
stamped with the old number, so every item is asked again with the better
question. That is the designed mechanism and it was used as designed.

It would also have broken `restore_new_items`, permanently, and the failure is
worth recording because it is invisible from the line that causes it.

Restore-by-category is the **only** consumer of the AI cache for items the card
no longer shows: a dismissed item is not in the sensor's list, so the category it
carried when it was dismissed exists nowhere else. And a dismissed item is never
re-queued — the AI queue is built from the visible set — so a `None` from `get_ai`
is not a gap that the next pass fills, it is permanent. After the bump, all 705
dismissed items would have resolved to the fallback: *restore "Energy & solar"*
would have restored nothing, and *restore "Other"* would have un-dismissed the
entire backlog in one tap. The destructive half is the one with no error message.

| Decision | Chosen | Rejected |
|---|---|---|
| How the restore path reads a stale verdict | **`get_ai(..., include_stale=True)`**, passed only there | Re-sending dismissed items to the AI; dropping the version gate entirely |
| What restore-by-category means | The category the item carried **when it was dismissed** | The category it would get today |
| Every other caller | Unchanged — the gate still applies | A global opt-out |

The stale record is genuinely still on disk to read: the store's cleaner
preserves unknown keys including `prompt_version`, and pruning skips anything
dismissed. Checked at the same time, and safe: dismissals, never-hide exemptions
and hide records live in separate dicts that never consult `prompt_version`, so
the bump cannot lose a dismissal or re-hide something the user manually restored.

The card's own answer to the taxonomy growing from four groups in practice to
~20 belongs to the same release: **every group now starts folded**, with an
`Expand all` / `Collapse all` control in the existing chip row. "First two open"
was right at four groups and wrong at twenty — it meant scrolling past two long
lists to reach the rest of the week's drop. The defaults are computed from the
full, unfiltered set of categories so the kind filter never reshuffles which
groups are open, and the toggle acts on every category the pass knows about
rather than only the visible ones: a user who asks for "all" must not have a
filtered-out group reappear half-open when they clear the filter.

**Lesson:** a cache invalidation is a change to every reader of that cache, and
the dangerous reader is the one whose data the invalidation can never restore.
Before bumping a version stamp, enumerate who reads the stamped records and ask
what each of them does with `None` — the answer here was "nothing" in one place
and "un-dismiss 705 items" in another, and both were two lines away from a
constant that looked like it only affected the model.

## 2026-10-29 — A render-time map that two readers disagree about

Found by the post-release review of 1.17.0, fixed in **1.17.1**.

`_expanded` in `hacs-new-items-card.js` maps category → folded?, and it was only ever added
to: `_renderDynamic` filled in a default for any category it had not seen, and nothing ever
removed one. That was harmless while the only reader was the group being drawn — a key for a
category with no rows renders nothing. 1.17.0 added a **second** reader with a different
idea of the key set: `Expand all` / `Collapse all` decides its direction from
`Object.keys(this._expanded)`, every category ever seen, while the chip row words the
button's label from the categories currently on screen.

*Dismiss all* on an open group is enough to split them. The emptied category drops off the
screen still holding `true`; the label, reading the live set, says `Expand all`; the toggle,
reading all keys, sees one open and runs a collapse. The press clears the stale key and
opens nothing. One dead click on the control that only exists because every group now starts
folded — small, but it is the button lying about itself.

| Decision | Chosen | Rejected |
|---|---|---|
| How the two readers are kept in step | **Rebuild `_expanded` from the live categories each render**, carrying existing values across | Passing the ordered list into the click handler; pruning only inside the toggle; reading the DOM for the current groups |
| Fold state of a category whose items were all dismissed | Dropped — it no longer exists | Remembered in case it returns |

Rebuilding is the fix rather than a second pruning step because it removes the *possibility*
of a difference instead of patching one site: there is no longer a key set to get out of
sync with, so a third reader added later inherits the agreement for free.

**Lesson:** a map that accumulates keys is fine with one reader and a trap with two. The
moment a second reader derives something from its key set — a count, a direction, a label —
the two readers have to be given the same keys, and the cheapest way to guarantee that is to
make the map hold only what is live rather than to teach each reader which keys to ignore.


## 2026-11-03 — Review decisions also clear HACS’s New flag

The monitor’s Dismiss button and country hides previously changed only its local registry.
Observed live on 3 November 2026: 28 visible, 806 dismissed and 119 hidden, while the owner
still saw many rejected items in HACS New. The fix mirrors HACS’s targeted clear handler:
resolve live repositories, set only their `data.new`, and let `hacs.data.async_write()` own
serialization and frontend notifications. No direct edits of HACS’s sparse storage.

The owner selected both manual and automatic-country syncing, and explicit Restore in
both lists. Saved pending intent precedes HACS writes; old dismissals reconcile on scan.
One review lock covers scan decisions, services and application of AI results—not the AI
requests—so a delayed response cannot undo a dismissal or restore. Hidden audit and
restored records bypass the ordinary 30-day window, without changing first-seen dates.
Automatic country-rule changes never re-mark items New; explicit Restore does.

**Lesson:** two interfaces offering the same review action need to share the underlying
review state. A local filter is not upstream dismissal, and clearing upstream flags also
changes the lifetime of every reader that formerly relied on those flags.


## 2026-11-03 — Themes and templates share the New review flow (1.17.4)

The owner approved including the nine themes and one template left outside the original
integration/plugin-only scanner. Discovery and HACS review syncing now share one
`NEW_ITEM_HACS_KINDS` allow-list. The same country matcher, registry, locking, services
and writer are reused; installed and unknown categories remain excluded.

Themes are always Dashboard & theming, including while AI work is pending and when
a verdict is applied. Templates retain the existing subject-based groups. No AI cache
version bump is needed: these two kinds were not previously scanned. The approved
preview adds type chips and editor options rather than a new screen. All four bundled
cards stay versioned with the manifest. Installed-repository usage monitoring is unchanged.

**Lesson:** a category expansion must reach both discovery and review synchronization;
sharing the allow-list avoids a visible item whose dismissal never reaches HACS.


## 2026-11-09 - Follow the active component store, not a retired file (1.18.0)

Issue #75 was investigated against DEV on HA 2026.11.0. The approved fix uses a
narrow HACS/Marketplace boundary: configured Marketplace owns inventory and review
writes, even when temporarily unavailable. HACS uses its existing version-six file
format. Marketplace uses one detached runtime snapshot per scan, with readiness and
shape guards. No newer HA module is imported by the adapter.

The coordinator is created before forwarding platforms and attaches unavailable sensors
when its initial scan cannot read the store. It retries after 30 seconds rather than
blocking HA startup, because Marketplace's startup work can run after HA started.
A successful scan restores the hourly interval. Missing/corrupt/incompatible sources
raise instead of becoming empty collections, so they cannot trigger registry pruning.
Existing IDs, storage keys and review locks stay unchanged. Selected New flags use the
active manager's own writer; local pending intent stays retryable on failure.

All backend and card update ownership checks include hacs and marketplace. The tracker
no longer guesses ownership from a brand icon if entity/source is unavailable. All four
cards have unavailable states; the review card opens the active store's download screen.
No new list, search/sort redesign or separate mobile-app work is included.

Readiness and envelope checks guard the private interfaces. Tagged Marketplace source
confirms its readiness fields; missing fields are explicitly rejected.
Tagged HACS 2.0.5 declares VERSION_STORAGE="6", which corrected the draft's version-one
assumption before the fix was committed. Regression tests cover both string and numeric
six and reject incompatible envelopes. See docs/verification/marketplace-1.18.0.md for
actual gate results and the limits of live testing.

**Lesson:** migration compatibility must follow the active owner and its lifecycle, not
simply try another filename. A readable stale file is worse than an honest unavailable
state, and mock storage headers must be checked against the real writer.


## 2026-11-12 — "Unknown" is a third answer, not a quiet "yes" (1.19.0)

A component's `hacs.json` can declare a minimum Home Assistant version, and both
store providers already surface that file verbatim as `repository_manifest`. HACS
enforces it on install. Nothing showed it to the user beforehand — so an install
either failed with no visible reason, or (Microsoft 365 — Calendar, reported by the
owner) went ahead and had to be rolled back by hand.

| Decision | Chosen | Rejected |
|---|---|---|
| Where the requirement comes from | **`repository_manifest["homeassistant"]` of the installed repository**, already in both stores' snapshots | Fetching `hacs.json` from GitHub per update; parsing the release notes |
| How many answers | **Three — met / blocked / unknown**, with a reason on every unknown | Two, folding "nothing declared" into compatible |
| What happens to a blocked update | **Hidden by default**, revealable, and with **no Update button at all** on the revealed row | A disabled button; a warning the user can click past |
| What happens to an unknown | **Listed and labelled**, out of bulk, individually installable after one tick that is forgotten on close | Hidden with the blocked ones; silently installable |
| Which version strings are trusted | **An allow-list of comparable strategies** (CalVer, SemVer, SimpleVer, BuildVer, PEP 440) | Whatever `AwesomeVersion` accepts |
| Two rows claiming one entity | **The strictest claim wins** | First match, as the reconciler already did |
| Where the protection lives | **Card and both of this integration's install services** | Card only |

**Why "unknown" is first-class.** Most components declare nothing at all, so a
two-state design would have shown the overwhelming majority as "compatible" —
a claim nobody checked, attached to the exact updates this feature exists to
warn about. `unknown` is therefore never folded into `met`: it is labelled, it
carries *why* (`not_declared` / `unreadable` / `ha_unreadable`), it is excluded
from every bulk install, and installing one needs an explicit per-row
acknowledgement. The tick is deliberately forgotten when the row closes, so it
can never be a setting the user left on.

**Why the version strings needed an allow-list rather than a try/except.**
`AwesomeVersion` accepts far more than it can order. `">=2026.11.0"` parses as
`UNKNOWN` and raises only when compared — survivable. `"latest"` is worse: it
parses as a valid `SPECIALCONTAINER` tag that compares as *newer than every real
version*, so a single component declaring `"latest"` would have blocked every
update in the house with a message naming a version that does not exist. Trusting
only five ordered strategies turns both into an honest `unknown`. Two valid
versions of incompatible shapes (`"2026.11.0"` against `"1.4"`) still raise on the
comparison itself, and that path is caught too.

**Why the strictest claim wins on a contested entity.** `_reconcile_updates`
matches a snapshot row to a live `update.*` entity by repository URL, then by
name, then by slug — and the two fallbacks are first-match, with nothing stopping
a second row claiming an entity another row already took. A fork beside its
original normalises to the same name and does exactly that. Before this feature a
wrong match mis-attributed release notes; now it decides which declared minimum
is believed, and being wrong in the *permissive* direction installs precisely what
this release exists to hold back. So `compat.strictest()` and the card's
`_stricterReq()` both answer an ambiguous entity with `blocked` > `unknown` >
`met`. Over-restricting shows a message the user can read and override;
under-restricting breaks their Home Assistant.

**Why a blocked row has no button rather than a disabled one.** A disabled control
still reads as "this is the thing to press", and the state it is disabled for can
change under a re-render. Removing the control removes the question. **Skip** stays,
because skipping an update you cannot install is the useful action.

**Why the services refuse too, and the one hole that remains.** The card is not the
only caller: `update_and_action` raises with both versions named, and `update_all`
skips blocked components and names them in the log rather than failing partway
through a batch. The asymmetry is deliberate — **blocked** is refused on both
sides, **unknown** only in the card, because a service call cannot see a card
acknowledgement and refusing it server-side would leave no route at all. Home
Assistant's own `update.install` is not ours and cannot be intercepted; the card
simply never offers it for a held-back update, and the release notes say so rather
than implying blanket protection.

**Why the bulk button had to be re-labelled in the same change.** With rows now
excluded from a bulk action while still on screen, "Update all (3)" above five
listed updates is a lie. It reads **Update all (N)** only when it will install all
N, **Update selected (N)** under a filter, and **Update N of M** when some listed
rows are out of scope — with a breakdown of why beside it, and the confirmation
dialog quoting the same number. "Update all" also used to call the service with no
`entity_ids` at all, meaning *install everything you can find*; it now always
passes the explicit list, so a hidden update cannot be installed by a button that
was not offering it.

**Why the card tests are still run by hand.** `tests/test_tracker_card_compat.cjs`
holds the "a blocked update is never installable" guarantee, so adding it to
`validate.yaml` looked obvious, and 1.19.0 originally did. It had to come back out.
This repo's CI and releases live on the **GitHub** mirror — Gitea Actions is
switched off for it (`has_actions: false`, last run June 2026) — and the mirror is
a one-way push authenticated by a token with no `workflow` scope, so GitHub refuses
any push carrying a change to `.github/workflows/*`. The refusal is checked per
commit rather than against the branch tip, so reverting the edit afterwards does not
clear it; the commit has to never exist. Until that token is re-minted, a workflow
file in this repo can only be changed by a human pushing to GitHub directly, and the
card tests run from the release checklist: `node --check` on every card, then
`node tests/*.cjs`.

**Lesson:** before adding a CI job, check that this repo's CI is somewhere you can
actually write to. A mirrored repo has two hosts and only one of them runs the
workflow — and the credential that mirrors it is a scope boundary you cannot see
from the diff.

**Lesson:** a compatibility check has three possible answers and the third one is
the common case. Designing for two means the default — nothing declared — inherits
the reassuring label, which is the one outcome worse than no check at all. The same
principle decides every tie downstream: when two sources disagree about whether an
install is safe, the answer is the restrictive one, because the costs are not
symmetric — a wrong warning is read and overridden in a second, and a wrong
clearance is a rollback by hand.

## 2026-11-14 — A status flag is not the data, and `UpdateFailed` is not a diagnosis (1.20.0)

Issue #114: every card blank after the 1.19.0 upgrade. Read live on the production
instance, the cards were innocent — all six sensors had been `unavailable` since
08:21:49 and every scan had failed with *"Hacs is starting; component data will retry
automatically."*, 65 times in half an hour. The component data on disk was complete and
correct the entire time.

HACS 2.0.5 was the cause, and permanently. That store holds two rows for one repository
name (`Joery/hass-afvalhulp`, the only duplicate in 6,454), so `set_repository_id` raises
out of `startup_tasks` — *after* `status.startup` is cleared and *before* the running
stage is set. `stage` therefore stays `"startup"` for the whole uptime of Home Assistant:
the log never prints the running line, and nothing recovers without a restart. Upstream
has closed this as fixed in the Marketplace that ships with Home Assistant 2026.11; HACS
itself has no release carrying the fix.

| Decision | Chosen | Rejected |
|---|---|---|
| What a failed scan does to data it already has | **Keeps it, flags `data_stale`, stays successful** | `UpdateFailed` — which is what it did |
| What the legacy-HACS file read requires | **Loaded, enabled, intact** (`require_readable`) | Also "finished starting" (`require_ready`), as before |
| Where "starting" still decides the answer | **An empty file while starting** — a file not yet filled in | Nowhere; or everywhere, as before |
| How long stale data is served | **24 hours**, then honestly unavailable | Forever; one scan interval |
| Retry pacing after a failure | **30 s doubling to the hourly interval**, reset on success | A fixed 30 s, as before |
| Where the user is told | **On the card** — a drawn ◐ plus the word "Stale", the age, and the reason | The log; a sensor attribute only |

**Why the strict gate was the wrong question.** `require_ready()` asks whether the
provider has finished starting, which is the right question before touching its live
in-memory objects — a half-built index is genuinely unusable. It is the wrong question to
ask of a *file*. HACS writes `.storage/hacs.repositories` itself and only ever writes a
complete one, so the file's contents are the evidence; the status flag is hearsay about an
unrelated object. The 1.18.0 boundary applied the strict gate to both, which is how one
stuck flag withheld data that was sitting there readable. The residual case is kept: a
file that is *empty* while the provider is starting is one HACS has not finished writing,
and publishing zero components from it would read as "everything was uninstalled".

**This qualifies the 2026-11-09 lesson rather than reversing it.** "A readable stale file
is worse than an honest unavailable state" was written about a *retired* file — reading
`hacs.repositories` after the owner had migrated to Marketplace, where it is an abandoned
snapshot of a store somebody else now owns. That still holds, and the Marketplace path is
untouched: it still requires full readiness, still refuses to fall back, still raises
rather than returning an empty collection. The 1.20.0 case is the *current* file of the
*active* owner, which is a different claim entirely — and the honest-unavailable half of
that lesson turned out to have a cost nobody had priced: six unavailable sensors are not
read as "the store is starting", they are read as "the integration is broken", because
the only place that said otherwise was the log.

**Why staleness is marked rather than silent, and why it expires.** `UpdateFailed` is a
single verdict covering two very different situations, and `CoordinatorEntity` turns both
into the same blank. A cold start has nothing to be stale about and still fails honestly.
A failure *with* data behind it now keeps that data and publishes four small scalars —
`data_stale`, `last_successful_scan`, `scan_error`, `store_status` — so the cards can say
which scan they are showing and why it is the newest one. They are capped at 24 hours
because "from yesterday" and "from last week" are not equally useful; past the grace
period the old blank is the truthful answer. The two states are mutually exclusive by
construction: there is no last good scan to be stale about on the path that renders
Unavailable.

**Why the backoff shipped in the same change.** The failed-scan interval was pinned at
30 s and left there, which assumes the cause is transient. This one was not: 120 failed
scans an hour, each logging the same error line, for as long as Home Assistant stayed up.
The interval now doubles up to the ordinary hourly one and the log line is deduplicated by
message, so a startup race still recovers in seconds and a permanent fault costs the same
as a working scan.

**The test gap, which is the part worth carrying.** 1.18.0's suite had a test for a
starting store and it asserted that refusing to read was *correct*. The fixture
(`manager()`) builds `stage="running"`, so every other test described a store that had
finished starting, and the one test that described the real production state pinned the
bug as the specification. Live testing did not catch it either: DEV and TEST1 run Home
Assistant 2026.11 with Marketplace and have no HACS at all, and TEST2 — which does have
HACS 2.0.5 — printed the same "HACS starting" line during the 9 November test run, was
correctly read as benign because it recovered seconds later, and has no duplicated
repository name to get stuck on. Production's duplicate row was re-fetched on 12
November, three days after those tests. So the fault was unreachable on every instance
that was tested, on the day it was tested.

**Lesson:** a test that asserts the current behaviour of a guard is not a test of the
guard's *premise*. "Refusing a starting store is correct" and "a starting store cannot be
read" are different claims, and only the first was ever tested — so the suite grew a test
whose job was to keep the bug. When a fixture only ever builds the healthy value of a
status field, every test in the file is silently about the healthy case; the state that
matters is the one production is actually in, and it is worth asking which instance could
even reach it before concluding that a live test cleared the change.

**Second lesson:** `UpdateFailed` is a diagnosis, not an error handler. It means "I have
nothing to show you", and reaching for it on every exception tells six entities to go
blank over a condition that may not affect the data at all. Separate *the scan failed*
from *there is nothing to show*, and the user keeps what they already had — with a label
saying how old it is, which is strictly more information than a blank card.

## 2026-11-14 — A manifest with no record of which release it describes can't judge a different release (1.21.0)

The v1.19.0 compatibility check compared a pending update's declared minimum Home
Assistant version against the one you're running — but HACS stores exactly one
`hacs.json` snapshot per repository (`repository_manifest`, in both stores), with no
field saying which release that snapshot describes. HACS only ever populates the
`homeassistant` key by reading GitHub for whatever version was being *downloaded* at the
time; its catalogue feeds (`data-v2.hacs.xyz/{integration,plugin}/data.json`) publish 0 of
6,279 rows with a `homeassistant` key at all. So when a new release appears, the feed
refreshes `last_version` and leaves the old release's `homeassistant` value sitting there
unrefreshed. The check was reading the *installed* version's requirement and labelling
the *offered* version with it. Observed on this house's production instance across all
seven pending updates: six agreed by coincidence, and **Microsoft 365 - Calendar v1.14.0**
did not — cached 2025.6.0 (v1.13.0's minimum) against an actual 2026.11.0, with the
card's own release notes contradicting its own compatibility line in the same render.

| Decision | Chosen | Rejected |
|---|---|---|
| Where the requirement comes from | **The offered version's own `hacs.json`**, fetched from that exact tag | HACS's stored snapshot, as v1.19.0/1.20.0 did |
| How long a successful read is trusted | **For the life of the running process** — a tag's contents never change | Re-fetched every scan |
| What a failed read does to an existing answer | **Reports `target_unreadable`; never keeps the stale cached value** | Falls back to the last value read |
| What wins when a failed read meets a known `blocked` | **The `blocked` verdict**, every time | Whichever was computed most recently |
| How much work this adds per scan | **Bounded** — limited concurrency, a per-request timeout, an overall time budget; a failure can't fail the scan | Unlimited fan-out with no budget |

**Why the fix had to be "ask the tag," not "ask HACS harder."** There is no second field
to read. HACS's own storage genuinely does not know which release its one cached
`homeassistant` value belongs to, so no amount of re-reading that store produces the
right answer — the only source that can state what a specific release requires is that
release's own manifest, which only exists at the ref it shipped on. This is also why
HACS's own install gate (`repositories/base.py` in 2.0.5) has the identical blind spot: it
checks the stored manifest regardless of which ref is being installed. A one-line fix for
it was merged upstream in hacs/integration PR #4652 on 2025-06-10 and has never shipped in
a release, so HACS itself still will not reliably stop an incompatible install — this
card's own fetch-the-offered-tag check is the only correct gate in the house right now.

**Why a failed read reports unknown instead of repeating the old value.** Caching only the
successful reads (never the failures) means a transient fetch error costs nothing but an
honest `target_unreadable` row this hour and a retry next hour — it can never calcify into
a wrong answer the way the HACS snapshot did. And because `blocked` still beats
`target_unreadable` when both exist for a row, a component already known to need a newer
Home Assistant can't be quietly reclassified as merely "unknown" by one bad fetch.

**The cost it does carry: the scan now touches the network.** The hourly refresh is also
awaited by the config-entry setup and by the install services when they meet an update the
last scan hadn't seen, so with GitHub unreachable the whole-pass budget is the worst-case
delay to starting up and to pressing Update. That is why the budget is 25 s rather than
something leisurely, and why it is a *whole-pass* bound rather than per request: it is the
only number that caps what a person waits for. Exceeding it is the safe direction —
unanswered rows become "couldn't check" and are retried — but a user staring at a button
is not, so the number stays short. The reads are skipped entirely once each offered tag is
cached, which is the normal case after the first scan following a new release.

**Lesson:** a cached value with no record of *which version it describes* cannot be used
to judge a *different* version, no matter how fresh the cache otherwise looks — "last
refreshed 10 minutes ago" says nothing about which release the content is about. And the
test suite that shipped with v1.19.0 passed cleanly through all of this, because every
test built its fixture by handing the manifest and the offered version as the same input
— the exact assumption production violates the moment a new release appears before the
next full re-fetch. A fixture that never lets the manifest and the version under test
disagree can't catch a bug that only exists when they do.
