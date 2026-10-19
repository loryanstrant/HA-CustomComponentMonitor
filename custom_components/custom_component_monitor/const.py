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
CONF_NEW_ITEMS_MAX_AI_PER_CYCLE = "new_items_max_ai_per_cycle"

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
NEW_ITEMS_DEFAULT_MAX_AI_PER_CYCLE = 10
DEFAULT_HOME_COUNTRY = "AU"
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
# the two. Held alphabetically because that is the order the card renders in;
# "Other" is pinned last by NEW_ITEM_CATEGORY_FALLBACK rather than sorted.
NEW_ITEM_CATEGORY_OPTIONS = [
    "Appliances",
    "Climate & heating",
    "Dashboard & theming",
    "Energy & solar",
    "Health & fitness",
    "Lighting",
    "Media & audio",
    "Network & infrastructure",
    "Notifications & voice",
    "Presence & location",
    "Security & cameras",
    "Tasks & calendar",
    "Transport & travel",
    "Vehicles & EV",
    "Weather & environment",
    "Other",
]
NEW_ITEM_CATEGORY_FALLBACK = "Other"
# A local 9B model returns near-misses rather than the exact option string, so
# map the forms seen in practice back onto the canonical set (same approach as
# AI_CATEGORY_ALIASES).
NEW_ITEM_CATEGORY_ALIASES = {
    "appliance": "Appliances",
    "appliances": "Appliances",
    "kitchen": "Appliances",
    "laundry": "Appliances",
    "vacuum": "Appliances",
    "climate": "Climate & heating",
    "climate & heating": "Climate & heating",
    "heating": "Climate & heating",
    "hvac": "Climate & heating",
    "cooling": "Climate & heating",
    "thermostat": "Climate & heating",
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
    "system": "Network & infrastructure",
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
NEW_ITEM_AI_PROMPT_VERSION = 1

SERVICE_DISMISS_NEW_ITEMS = "dismiss_new_items"
SERVICE_RESTORE_NEW_ITEMS = "restore_new_items"
SERVICE_CATEGORISE_NEW_ITEMS = "categorise_new_items"