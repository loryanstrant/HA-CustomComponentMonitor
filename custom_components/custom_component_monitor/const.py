"""Constants for the Custom Component Monitor integration."""

DOMAIN = "custom_component_monitor"

# Sensor keys
SENSOR_ALL_COMPONENTS = "all_hacs_components"
SENSOR_UNUSED_INTEGRATIONS = "unused_integrations"
SENSOR_UNUSED_THEMES = "unused_themes"
SENSOR_UNUSED_FRONTEND = "unused_frontend"
SENSOR_HACS_UPDATES = "hacs_updates"

# Sensor attributes
ATTR_COMPONENTS = "components"
ATTR_TOTAL_COMPONENTS = "total_components"
ATTR_USED_COMPONENTS = "used_components"
ATTR_UNUSED_COMPONENTS = "unused_components"
ATTR_EXCLUDED_COMPONENTS = "excluded_components"
ATTR_UPDATES = "updates"
# Per-update AI enrichment (#67)
ATTR_CATEGORIES = "categories"
ATTR_SUMMARY = "summary"
ATTR_ENTITY_ID = "entity_id"
# AI run status, surfaced on sensor.hacs_updates (#91)
ATTR_AI_ENABLED = "ai_enabled"
ATTR_AI_PENDING = "ai_pending"
ATTR_AI_IN_PROGRESS = "ai_in_progress"
ATTR_AI_LAST_ERROR = "ai_last_error"
ATTR_AI_LAST_RUN = "ai_last_run"
# Observed throughput of the New-in-HACS categorisation loop, and what it
# implies for the backlog (#95 / v1.15.1). Both are null until a batch has
# actually run, because they are measurements and not estimates.
ATTR_AI_RATE_PER_MINUTE = "ai_rate_per_minute"
ATTR_AI_ETA_SECONDS = "ai_eta_seconds"
# When exact install-date tracking began, surfaced on
# sensor.hacs_installed_components so the card can explain its "~" marks (#93)
ATTR_INSTALL_DATES_SINCE = "install_dates_since"

# Options
CONF_EXCLUDE = "exclude"  # user-maintained list of components to treat as used (#70)
# AI summarise & categorise updates (#67) — opt-in
CONF_AI_CATEGORIZATION_ENABLED = "ai_categorization_enabled"
CONF_AI_TASK_ENTITY = "ai_task_entity"

# AI categorisation (#67)
AI_CACHE_STORAGE_KEY = "custom_component_monitor.ai_categories"
# Own store version — v2 moved from a flat {name|version: {...}} dict to
# {"entries": {repo_key|version: record}, "legacy": {...}} (#91).
AI_CACHE_STORAGE_VERSION = 2
AI_CATEGORY_OPTIONS = [
    "Bug fixes",
    "New features",
    "Documentation",
    "Translations",
    "Breaking changes",
    "Dependencies",
    "Other",
]
# Models don't reliably honour the select-selector options, so map the common
# free-form tags they emit back onto the canonical set (#67).
AI_CATEGORY_ALIASES = {
    "bug fixes": "Bug fixes",
    "bug fix": "Bug fixes",
    "bugfix": "Bug fixes",
    "bugfixes": "Bug fixes",
    "fix": "Bug fixes",
    "fixes": "Bug fixes",
    "maintenance": "Bug fixes",
    "patch": "Bug fixes",
    "new features": "New features",
    "new feature": "New features",
    "feature": "New features",
    "features": "New features",
    "enhancement": "New features",
    "enhancements": "New features",
    "improvement": "New features",
    "improvements": "New features",
    "documentation": "Documentation",
    "docs": "Documentation",
    "doc": "Documentation",
    "translations": "Translations",
    "translation": "Translations",
    "i18n": "Translations",
    "localization": "Translations",
    "localisation": "Translations",
    "breaking changes": "Breaking changes",
    "breaking change": "Breaking changes",
    "breaking": "Breaking changes",
    "dependencies": "Dependencies",
    "dependency": "Dependencies",
    "deps": "Dependencies",
    "dependency updates": "Dependencies",
    "other": "Other",
    "chore": "Other",
    "chores": "Other",
    "misc": "Other",
    "miscellaneous": "Other",
}
AI_MAX_PER_SCAN = 8  # cap new AI calls per scan so refreshes stay snappy
AI_CALL_TIMEOUT = 30  # seconds per AI Task call
# A failed generation is recorded and retried with growing backoff rather than
# re-sent on every hourly scan (#91). Index by (attempts - 1), clamped.
AI_FAILURE_BACKOFF_MINUTES = (15, 60, 360, 1440)
AI_MAX_ATTEMPTS = 5  # after this, only a forced/manual run retries
AI_CACHE_MAX_AGE_DAYS = 30  # evict records untouched for this long
AI_CACHE_MAX_ENTRIES = 500  # hard cap, oldest-updated dropped first

# --- Install-date registry (#93) ---
# HACS records no install date, and a HACS *update* rewrites the component's
# directory, so filesystem timestamps make an old component look freshly
# installed. The first time a component is seen installed is recorded here and
# never moved forward again.
INSTALL_DATES_STORAGE_KEY = "custom_component_monitor.install_dates"
INSTALL_DATES_STORAGE_VERSION = 1
# A component must be missing from the HACS snapshot for this long before its
# record is dropped. The snapshot lags and flaps, so a short grace period would
# silently reset install dates. Deliberately longer than the card's 30-day
# window: an uninstall/reinstall inside it keeps the original date, which is
# "recently installed" either way.
INSTALL_DATES_GRACE_DAYS = 45
INSTALL_DATES_MAX_ENTRIES = 1000
# A component whose key we've never seen is only called a *new* install if its
# files are also recent. Scans are hourly, so this is generous — it just has to
# rule out a component that merely changed key (a repo renamed upstream), whose
# files can be any age.
INSTALL_DATES_NEW_WINDOW_DAYS = 2

# HACS category to display type mapping
CATEGORY_MAP = {
    "integration": "Integration",
    "plugin": "Frontend / Card",
    "theme": "Theme",
}

# Update interval (in seconds)
DEFAULT_SCAN_INTERVAL = 3600  # 1 hour

# --- Update Action Tracker (merged from HACS-Update-Action-Tracker) ---
STORAGE_KEY = "custom_component_monitor.update_actions"
STORAGE_VERSION = 1
SERVICE_UPDATE_AND_ACTION = "update_and_action"
SERVICE_UPDATE_ALL = "update_all"
SERVICE_GENERATE_SUMMARIES = "generate_summaries"
UAT_CARD_JS = "update-action-tracker-card.js"
UAT_CARD_BASE_PATH = f"/local/{DOMAIN}/{UAT_CARD_JS}"

# --- Recently installed but unused card ---
RIU_CARD_JS = "recently-installed-unused-card.js"
RIU_CARD_BASE_PATH = f"/local/{DOMAIN}/{RIU_CARD_JS}"

# --- New in HACS (#95) ---
# HACS flags freshly indexed repositories with ``new: true`` in its own
# snapshot. Every existing scanner here filters on ``installed``; this one
# deliberately does the opposite, so the card can triage what has just arrived
# in the store rather than what is already on disk.
SENSOR_NEW_ITEMS = "new_items"
NEW_ITEMS_CARD_JS = "hacs-new-items-card.js"
NEW_ITEMS_CARD_BASE_PATH = f"/local/{DOMAIN}/{NEW_ITEMS_CARD_JS}"

# Attributes on sensor.hacs_new_items
ATTR_ITEMS = "items"
ATTR_BY_CATEGORY = "by_category"
ATTR_HIDDEN = "hidden"
ATTR_HIDDEN_COUNT = "hidden_count"
ATTR_DISMISSED_COUNT = "dismissed_count"
ATTR_TOTAL_NEW = "total_new"
ATTR_HOME_COUNTRY = "home_country"
# How many items/hidden rows were held back by the caps below, so the card can
# say "showing 300 of 2,049" rather than quietly lying about the count.
ATTR_ITEMS_TRUNCATED = "items_truncated"
ATTR_HIDDEN_TRUNCATED = "hidden_truncated"

# Options
CONF_NEW_ITEMS_ENABLED = "new_items_enabled"
CONF_AUTO_HIDE_FOREIGN = "auto_hide_foreign"
CONF_HOME_COUNTRY = "home_country"
CONF_EXTRA_HOME_TERMS = "extra_home_terms"
# Retired in v1.15.1 in favour of CONF_NEW_ITEMS_AI_BUDGET_SECONDS. Kept
# defined because old config entries still carry the key: nothing reads it any
# more, and the next options save drops it.
CONF_NEW_ITEMS_MAX_AI_PER_CYCLE = "new_items_max_ai_per_cycle"
CONF_NEW_ITEMS_AI_BUDGET_SECONDS = "new_items_ai_budget_seconds"

NEW_ITEMS_STORAGE_KEY = "custom_component_monitor.new_items"
NEW_ITEMS_STORAGE_VERSION = 1
# HACS clears its own ``new`` flag — the panel has a "clear all new" action and
# some HACS upgrades reset it wholesale. Treating ``new: true`` as the sole
# definition would empty this card the first time the HACS panel is opened, so
# an item first seen inside this window still counts as new.
NEW_ITEMS_WINDOW_DAYS = 30
# A repository must be absent from the HACS snapshot for this long before its
# ``seen``/``ai`` records are pruned. The snapshot lags and flaps (same reason
# as INSTALL_DATES_GRACE_DAYS). Dismissals are never pruned.
NEW_ITEMS_GRACE_DAYS = 45
NEW_ITEMS_MAX_ENTRIES = 2000
# Retired in v1.15.1 alongside CONF_NEW_ITEMS_MAX_AI_PER_CYCLE. See the
# adaptive pacing block below for what replaced it and why.
NEW_ITEMS_DEFAULT_MAX_AI_PER_CYCLE = 10
DEFAULT_HOME_COUNTRY = "AU"

# --- Adaptive AI categorisation pacing (v1.15.1) ---------------------------
#
# v1.15.0 sent a fixed 10 items per hourly scan. That number is blind to the
# hardware underneath it, and measuring the real thing showed how blind:
# against a local Qwen3.5-9B each item took 0.40-0.48 s, so a cycle did ~4.5 s
# of work and then waited 59 minutes 55 s. A 200-item HACS drop — which is how
# HACS actually behaves, quiet for a month and then several hundred in a few
# days — took close to a day to sort, and the card sat on "177 still being
# sorted" the whole time.
#
# Raising the number would only move the wrong constant. The pacing is now a
# *time* budget per pass, a short catch-up pass while work remains, and a
# self-tuning number of concurrent calls: fast hardware clears the same
# backlog in minutes, slow hardware settles back to one call at a time, and
# neither needs the user to pick a number.

# How long one pass may spend on AI calls. Checked *between* waves and never
# mid-call, so a model slower than the entire budget still completes a wave
# per pass instead of being cut off and making no progress at all.
NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS = 20
NEW_ITEMS_AI_MIN_BUDGET_SECONDS = 5
NEW_ITEMS_AI_MAX_BUDGET_SECONDS = 300

# Pause between a pass that ran out of budget and the catch-up pass after it.
# Short enough that a 200-item backlog clears in minutes, long enough to give
# the event loop and the AI backend air between bursts.
NEW_ITEMS_AI_CATCHUP_SECONDS = 5

# How many items go to the AI at once. Self-tuning: it keeps a measured
# throughput figure *per wave size* and moves to whichever neighbouring size
# (double or half) is worth more, trying any size it has never measured. A
# 16 GB GPU batches happily and walks up to MAX; a CPU-only box measures 1 as
# its best and stays there. Nothing the user has to pick.
#
# Per-size rather than best-ever on purpose. A single "best throughput seen"
# bar only ever ratchets upwards, so one transient failure at the top size
# halves the wave and then every smaller — and therefore legitimately slower —
# wave reads as a regression, walking the size down to 1 with no way back.
# That is the exact hourly trickle this release exists to remove, so the
# comparison has to be like-for-like.
NEW_ITEMS_AI_CONCURRENCY_START = 2
NEW_ITEMS_AI_CONCURRENCY_MAX = 8
# A neighbouring size must beat the current one by this factor to be worth
# moving to. The margin is the hold band that stops the size oscillating on
# measurement noise once it has converged.
NEW_ITEMS_AI_GROW_GAIN = 1.05
# After this many waves with no move, the neighbours' records are forgotten so
# they get re-measured. Backend load changes — another integration starts
# hammering the same GPU, or stops — and without this a stale number pins the
# size for as long as Home Assistant stays up.
NEW_ITEMS_AI_REPROBE_WAVES = 5

# Consecutive all-failed waves before a pass gives up and leaves the rest to
# the next hourly scan. Stops a dead AI entity being hammered in a tight
# catch-up loop.
NEW_ITEMS_AI_MAX_FAILED_WAVES = 3

# Hard stop on the catch-up chain, in case `remaining` never reaches zero.
# At the default budget this is many hours of work — it is a backstop against
# a logic error, not a pacing control.
NEW_ITEMS_AI_MAX_PASSES = 500
# How many rows the sensor publishes in its attributes. This is NOT a cap on
# the scan: the coordinator keeps the full lists, so dismissing a whole
# category still acts on every match. It caps only what crosses the websocket.
#
# Measured, not guessed: a HACS store with 2,240 repositories flagged new put
# 1.2 MB into this one entity's attributes, and that payload is re-sent to
# every connected dashboard on every refresh. The recorder is already spared
# via ``_unrecorded_attributes``; the frontend is not. 300 rows is ~160 KB and
# comfortably more than anyone reviews in a sitting, and the card shows how
# many are held back.
NEW_ITEMS_MAX_EXPOSED = 300
NEW_ITEMS_MAX_EXPOSED_HIDDEN = 100

# Categorisation of *store* items. Deliberately a different set from
# AI_CATEGORY_OPTIONS above, which categorises release notes — do not conflate
# the two.
#
# Held alphabetically for the benefit of whoever reads this file, and for the
# two copies of the list in services.yaml that have to match it. The *card*
# does not depend on the order at all: ``hniOrderedCategories`` in
# hacs-new-items-card.js re-sorts whatever it is given and pins "Other" last
# itself. (The comment here used to claim the opposite.)
#
# v1.17.0 added the last five entries. Measured on this house's production
# store on 2026-10-27: 58 of 127 visible items had settled on "Other" with
# nothing left in the queue, and reading all 58 showed why — roughly 24 were
# tooling for Home Assistant itself, ~15 were dashboard cards with no domain
# subject, ~10 wanted a bucket that did not exist (covers, irrigation,
# schedulers, small device bridges), and only ~5 were genuinely uncategorisable.
# "Other" was the taxonomy being too small, not the AI being wrong.
NEW_ITEM_CATEGORY_OPTIONS = [
    "Appliances",
    "Automation & scheduling",
    "Climate & heating",
    "Covers & blinds",
    "Dashboard & theming",
    "Energy & solar",
    "Garden & water",
    "Health & fitness",
    "Lighting",
    "Media & audio",
    "Network & infrastructure",
    "Notifications & voice",
    "Presence & location",
    "Security & cameras",
    "Sensors & devices",
    "System & diagnostics",
    "Tasks & calendar",
    "Transport & travel",
    "Vehicles & EV",
    "Weather & environment",
    "Other",
]
NEW_ITEM_CATEGORY_FALLBACK = "Other"

# One line per category, handed to the model with the label (v1.17.0).
#
# A bare list of twenty labels asks the model to guess what each one covers,
# and a 9B model guesses "Other". The definitions are the fix, and they are
# written for the boundaries that actually went wrong: that a thing which
# watches Home Assistant is *System & diagnostics* rather than Network, and
# that "Other" means "not about the home at all" rather than "none of these is
# a perfect fit".
#
# Keys must stay exactly equal to NEW_ITEM_CATEGORY_OPTIONS — enforced by a
# test, so a new label cannot be added without a definition.
NEW_ITEM_CATEGORY_DEFINITIONS = {
    "Appliances": (
        "Whitegoods and household machines — washers, dryers, dishwashers, "
        "ovens, coffee machines, robot vacuums, 3D printers."
    ),
    "Automation & scheduling": (
        "Tools for building or running automations themselves — schedulers, "
        "timers, calendars of rules, state machines, automation editors and "
        "blueprint helpers."
    ),
    "Climate & heating": (
        "Heating, cooling, ventilation and humidity — thermostats, heat pumps, "
        "air conditioners, radiator valves, fans, dehumidifiers."
    ),
    "Covers & blinds": (
        "Anything that opens and closes — blinds, roller shutters, awnings, "
        "curtains, garage doors, gates, windows, skylights."
    ),
    "Dashboard & theming": (
        "The look and feel of the Home Assistant interface — Lovelace cards, "
        "themes, layouts, sidebars, kiosk and wall-panel modes. Any frontend "
        "card belongs here unless it is clearly about one other subject on "
        "this list."
    ),
    "Energy & solar": (
        "Generating, storing, measuring or pricing electricity — solar "
        "inverters, home batteries, smart meters, tariffs, consumption "
        "monitoring."
    ),
    "Garden & water": (
        "Outdoors and water — irrigation controllers, sprinklers, soil "
        "moisture, lawn mowers, pools, spas, aquariums, plant care, water "
        "tanks and leak detection."
    ),
    "Health & fitness": (
        "Bodies and wellbeing — scales, sleep tracking, activity and "
        "exercise, blood pressure, medication reminders."
    ),
    "Lighting": (
        "Lights and light control — bulbs, strips, drivers, scenes, "
        "circadian and adaptive lighting."
    ),
    "Media & audio": (
        "Playing and managing media — speakers, TVs, receivers, streaming "
        "services, music libraries, remotes."
    ),
    "Network & infrastructure": (
        "The network and the servers on it — routers, switches, access "
        "points, DNS, VPNs, firewalls, NAS boxes, UPSs, printers, and "
        "integrations with other self-hosted services."
    ),
    "Notifications & voice": (
        "Getting a message out or talking to the house — push and chat "
        "notifications, text-to-speech, speech-to-text, voice assistants, "
        "LLM and conversation agents."
    ),
    "Presence & location": (
        "Who is where — device trackers, geofencing, room-level presence, "
        "occupancy, family location sharing."
    ),
    "Security & cameras": (
        "Protecting the house — alarm panels, cameras and NVRs, doorbells, "
        "locks, motion and door sensors used for security."
    ),
    "Sensors & devices": (
        "A bridge or driver for a specific piece of hardware whose job does "
        "not fit any other category — Bluetooth and BLE gadgets, serial and "
        "USB dongles, generic sensor platforms, buttons and remotes, "
        "single-device integrations."
    ),
    "System & diagnostics": (
        "Tools that watch over Home Assistant itself rather than the home — "
        "entity, device and integration auditing, logs and log parsing, "
        "database and recorder maintenance, statistics, backups, update and "
        "health monitoring, performance and debugging aids."
    ),
    "Tasks & calendar": (
        "Lists, plans and dates — to-do lists, chores, shopping, calendars, "
        "reminders, school and work schedules."
    ),
    "Transport & travel": (
        "Getting around outside the house — public transport departures, "
        "traffic and commute times, flights, trains, fuel prices, parcel and "
        "mail tracking."
    ),
    "Vehicles & EV": (
        "Cars and their charging — vehicle integrations, EV chargers and "
        "wallboxes, charge scheduling, battery and range reporting."
    ),
    "Weather & environment": (
        "Conditions, indoors and out — forecasts, rain and wind, air "
        "quality, pollen, UV, temperature and humidity reporting, "
        "earthquakes, fire and flood warnings."
    ),
    "Other": (
        "Not about a home or Home Assistant at all. Use this only when the "
        "repository is off-topic — not merely when no category is a perfect "
        "fit."
    ),
}
# A local 9B model returns near-misses rather than the exact option string, so
# map the forms seen in practice back onto the canonical set (same approach as
# AI_CATEGORY_ALIASES).
NEW_ITEM_CATEGORY_ALIASES = {
    "appliance": "Appliances",
    "appliances": "Appliances",
    "kitchen": "Appliances",
    "laundry": "Appliances",
    "vacuum": "Appliances",
    "automation": "Automation & scheduling",
    "automations": "Automation & scheduling",
    "automation & scheduling": "Automation & scheduling",
    "scheduling": "Automation & scheduling",
    "scheduler": "Automation & scheduling",
    "schedule": "Automation & scheduling",
    "schedules": "Automation & scheduling",
    "timer": "Automation & scheduling",
    "timers": "Automation & scheduling",
    "routine": "Automation & scheduling",
    "routines": "Automation & scheduling",
    "blueprint": "Automation & scheduling",
    "blueprints": "Automation & scheduling",
    "climate": "Climate & heating",
    "climate & heating": "Climate & heating",
    "heating": "Climate & heating",
    "hvac": "Climate & heating",
    "cooling": "Climate & heating",
    "thermostat": "Climate & heating",
    "cover": "Covers & blinds",
    "covers": "Covers & blinds",
    "covers & blinds": "Covers & blinds",
    "blind": "Covers & blinds",
    "blinds": "Covers & blinds",
    "shade": "Covers & blinds",
    "shades": "Covers & blinds",
    "shutter": "Covers & blinds",
    "shutters": "Covers & blinds",
    "curtain": "Covers & blinds",
    "curtains": "Covers & blinds",
    "awning": "Covers & blinds",
    "garage": "Covers & blinds",
    "garage door": "Covers & blinds",
    "gate": "Covers & blinds",
    "dashboard": "Dashboard & theming",
    "dashboard & theming": "Dashboard & theming",
    "theme": "Dashboard & theming",
    "themes": "Dashboard & theming",
    "theming": "Dashboard & theming",
    "ui": "Dashboard & theming",
    "frontend": "Dashboard & theming",
    "energy": "Energy & solar",
    "energy & solar": "Energy & solar",
    "solar": "Energy & solar",
    "power": "Energy & solar",
    "electricity": "Energy & solar",
    "battery": "Energy & solar",
    "garden": "Garden & water",
    "garden & water": "Garden & water",
    "gardening": "Garden & water",
    "irrigation": "Garden & water",
    "watering": "Garden & water",
    "sprinkler": "Garden & water",
    "sprinklers": "Garden & water",
    "plant": "Garden & water",
    "plants": "Garden & water",
    "lawn": "Garden & water",
    "mower": "Garden & water",
    "pool": "Garden & water",
    "spa": "Garden & water",
    "aquarium": "Garden & water",
    "water": "Garden & water",
    "health": "Health & fitness",
    "health & fitness": "Health & fitness",
    "fitness": "Health & fitness",
    "wellbeing": "Health & fitness",
    "light": "Lighting",
    "lights": "Lighting",
    "lighting": "Lighting",
    "media": "Media & audio",
    "media & audio": "Media & audio",
    "audio": "Media & audio",
    "music": "Media & audio",
    "tv": "Media & audio",
    "network": "Network & infrastructure",
    "network & infrastructure": "Network & infrastructure",
    "infrastructure": "Network & infrastructure",
    # "system" deliberately does NOT live here. Until v1.17.0 it pointed at
    # Network & infrastructure, which was the single most damaging entry in
    # this table: "system" is the one-word answer a model gives for every
    # Home-Assistant-watching tool, and there was nowhere for those to go.
    "router": "Network & infrastructure",
    "nas": "Network & infrastructure",
    "notifications": "Notifications & voice",
    "notifications & voice": "Notifications & voice",
    "notification": "Notifications & voice",
    "voice": "Notifications & voice",
    "tts": "Notifications & voice",
    "assistant": "Notifications & voice",
    "presence": "Presence & location",
    "presence & location": "Presence & location",
    "location": "Presence & location",
    "tracking": "Presence & location",
    "device tracker": "Presence & location",
    "security": "Security & cameras",
    "security & cameras": "Security & cameras",
    "camera": "Security & cameras",
    "cameras": "Security & cameras",
    "alarm": "Security & cameras",
    "lock": "Security & cameras",
    "doorbell": "Security & cameras",
    "sensor": "Sensors & devices",
    "sensors": "Sensors & devices",
    "sensors & devices": "Sensors & devices",
    "device": "Sensors & devices",
    "devices": "Sensors & devices",
    "bluetooth": "Sensors & devices",
    "ble": "Sensors & devices",
    "button": "Sensors & devices",
    "buttons": "Sensors & devices",
    "serial": "Sensors & devices",
    "usb": "Sensors & devices",
    "dongle": "Sensors & devices",
    "hardware": "Sensors & devices",
    "system": "System & diagnostics",
    "system & diagnostics": "System & diagnostics",
    "diagnostics": "System & diagnostics",
    "diagnostic": "System & diagnostics",
    "monitoring": "System & diagnostics",
    "monitor": "System & diagnostics",
    "logs": "System & diagnostics",
    "logging": "System & diagnostics",
    "logbook": "System & diagnostics",
    "database": "System & diagnostics",
    "recorder": "System & diagnostics",
    "statistics": "System & diagnostics",
    "stats": "System & diagnostics",
    "backup": "System & diagnostics",
    "backups": "System & diagnostics",
    "maintenance": "System & diagnostics",
    "debug": "System & diagnostics",
    "debugging": "System & diagnostics",
    "performance": "System & diagnostics",
    "updates": "System & diagnostics",
    "tools": "System & diagnostics",
    "developer tools": "System & diagnostics",
    "tasks": "Tasks & calendar",
    "tasks & calendar": "Tasks & calendar",
    "task": "Tasks & calendar",
    "todo": "Tasks & calendar",
    "to-do": "Tasks & calendar",
    "calendar": "Tasks & calendar",
    "chores": "Tasks & calendar",
    "transport": "Transport & travel",
    "transport & travel": "Transport & travel",
    "transit": "Transport & travel",
    "travel": "Transport & travel",
    "public transport": "Transport & travel",
    "flights": "Transport & travel",
    "vehicle": "Vehicles & EV",
    "vehicles": "Vehicles & EV",
    "vehicles & ev": "Vehicles & EV",
    "car": "Vehicles & EV",
    "cars": "Vehicles & EV",
    "ev": "Vehicles & EV",
    "charging": "Vehicles & EV",
    "weather": "Weather & environment",
    "weather & environment": "Weather & environment",
    "environment": "Weather & environment",
    "air quality": "Weather & environment",
    "forecast": "Weather & environment",
    "other": "Other",
    "misc": "Other",
    "miscellaneous": "Other",
    "general": "Other",
    "unknown": "Other",
}
# Bumping this invalidates every cached AI verdict on purpose.
#
# v1 -> v2 (v1.17.0): five new categories, a one-line definition per category
# in the prompt, and "use Other only when nothing else fits" replaced with
# "Other means not about the home at all". Every item is re-asked on upgrade;
# dismissals and never-hide overrides are stored separately and are untouched.
#
# ⚠ ``get_ai`` returns None for a record stamped with an older version, so any
# caller that needs the *last known* category of an item the AI will never be
# asked about again — a dismissed one — must pass ``include_stale=True``. See
# ``NewItemRegistry.get_ai`` and the restore-by-category path in __init__.py.
NEW_ITEM_AI_PROMPT_VERSION = 2

SERVICE_DISMISS_NEW_ITEMS = "dismiss_new_items"
SERVICE_RESTORE_NEW_ITEMS = "restore_new_items"
SERVICE_CATEGORISE_NEW_ITEMS = "categorise_new_items"