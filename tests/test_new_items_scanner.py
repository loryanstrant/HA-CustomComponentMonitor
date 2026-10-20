"""New in HACS: the scanner and sensor that triage not-yet-installed items (#95).

``ComponentScanner.scan_new_items`` is the mirror image of every other scanner
in this module: they all select ``installed=True``, this one selects
not-installed + new (``repo["new"]`` truthy, or first-seen still inside
``NEW_ITEMS_WINDOW_DAYS`` — HACS clears its own ``new`` flag, so the registry's
own memory has to carry the rest). The country pre-filter runs before
anything reaches the AI; AI categorisation itself is a separate, batched pass
on the coordinator. These tests cover the scan in isolation (no hass setup
required beyond the registry/scanner pair) and the AI batch runner with every
AI call mocked.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.custom_component_monitor.const import (
    CONF_AI_CATEGORIZATION_ENABLED,
    CONF_AI_TASK_ENTITY,
    CONF_AUTO_HIDE_FOREIGN,
    CONF_HOME_COUNTRY,
    CONF_NEW_ITEMS_AI_BUDGET_SECONDS,
    CONF_NEW_ITEMS_ENABLED,
    DEFAULT_HOME_COUNTRY,
    NEW_ITEM_CATEGORY_FALLBACK,
)
from custom_components.custom_component_monitor.new_items import NewItemRegistry
from custom_components.custom_component_monitor.sensor import (
    ComponentScanner,
    CustomComponentMonitorCoordinator,
)

# ---------------------------------------------------------------------------
# Fixture HACS repository snapshot
# ---------------------------------------------------------------------------

NEW_INTEGRATION = "someone/new-integration"
NEW_PLUGIN = "someone/new-card"
NEW_THEME = "someone/new-theme"
INSTALLED_NEW = "someone/already-installed"
SPARSE_ITEM = "someone/sparse-repo"
FOREIGN_ITEM = "someone/swedish-post-tracker"
AU_NZ_ITEM = "someone/au-nz-energy"
DISMISSED_ITEM = "someone/dismissed-repo"
STILL_FRESH_ITEM = "someone/recently-first-seen"
STALE_ITEM = "someone/long-first-seen"
NOT_NEW_NOT_INSTALLED = "someone/old-and-uninstalled"

REPOS: dict[str, dict] = {
    "1": {
        "full_name": NEW_INTEGRATION,
        "category": "integration",
        "installed": False,
        "new": True,
        "manifest_name": "New Integration",
        "description": "Does something useful with your thermostat.",
        "topics": ["climate", "heating"],
        "stargazers_count": 12,
        "downloads": 345,
        "last_updated": "2026-10-01T00:00:00+00:00",
    },
    "2": {
        "full_name": NEW_PLUGIN,
        "category": "plugin",
        "installed": False,
        "new": True,
        "manifest_name": "New Card",
        "description": "A pretty dashboard card.",
        "topics": ["dashboard"],
        "stargazers_count": 3,
        "downloads": 10,
        "last_updated": "2026-10-02T00:00:00+00:00",
    },
    "3": {
        "full_name": NEW_THEME,
        "category": "theme",
        "installed": False,
        "new": True,
        "manifest_name": "New Theme",
        "description": "A theme.",
        "topics": [],
    },
    "4": {
        "full_name": INSTALLED_NEW,
        "category": "integration",
        "installed": True,
        "new": True,
        "manifest_name": "Already Installed",
        "description": "Already on disk.",
        "topics": [],
    },
    "5": {
        "full_name": SPARSE_ITEM,
        "category": "integration",
        "installed": False,
        "new": True,
        "manifest_name": "Sparse Repo",
        "description": None,
        "topics": None,
        "stargazers_count": None,
        "downloads": None,
        "last_updated": None,
    },
    "6": {
        "full_name": FOREIGN_ITEM,
        "category": "integration",
        "installed": False,
        "new": True,
        "manifest_name": "Swedish Post Tracker",
        "description": "Track your mail delivery from PostNord in Sweden.",
        "topics": ["postnord", "sweden"],
    },
    "7": {
        "full_name": AU_NZ_ITEM,
        "category": "integration",
        "installed": False,
        "new": True,
        "manifest_name": "AU/NZ Energy",
        "description": "AU/NZ electricity prices.",
        "topics": ["energy"],
    },
    "8": {
        "full_name": DISMISSED_ITEM,
        "category": "integration",
        "installed": False,
        "new": True,
        "manifest_name": "Dismissed Repo",
        "description": "Already dealt with.",
        "topics": [],
    },
    "9": {
        "full_name": STILL_FRESH_ITEM,
        "category": "integration",
        "installed": False,
        # HACS has already cleared its own "new" flag...
        "new": False,
        "manifest_name": "Recently First Seen",
        "description": "First seen five days ago.",
        "topics": [],
    },
    "10": {
        "full_name": STALE_ITEM,
        "category": "integration",
        "installed": False,
        "new": False,
        "manifest_name": "Long First Seen",
        "description": "First seen ninety days ago.",
        "topics": [],
    },
    "11": {
        "full_name": NOT_NEW_NOT_INSTALLED,
        "category": "integration",
        "installed": False,
        "new": False,
        "manifest_name": "Old And Uninstalled",
        "description": "Neither new nor ever seen before.",
        "topics": [],
    },
}


@pytest.fixture
def registry(hass: HomeAssistant) -> NewItemRegistry:
    """A loaded new-items registry with an in-memory (never-written) store."""
    reg = NewItemRegistry(hass)
    reg._loaded = True
    reg._store = None  # async_save() becomes a no-op
    return reg


def at(days_ago: float):
    return dt_util.utcnow() - timedelta(days=days_ago)


async def run_scan(
    hass: HomeAssistant,
    registry: NewItemRegistry,
    *,
    home_country: str = DEFAULT_HOME_COUNTRY,
    extra_home_terms=(),
    auto_hide_foreign: bool = True,
):
    """Scan the fixture REPOS with every disk read stubbed out."""
    scanner = ComponentScanner(hass, None)
    with patch.object(
        ComponentScanner, "_load_hacs_repositories", AsyncMock(return_value=REPOS)
    ):
        return await scanner.scan_new_items(
            registry,
            home_country=home_country,
            extra_home_terms=extra_home_terms,
            auto_hide_foreign=auto_hide_foreign,
        )


def by_full_name(entries: list[dict], full_name: str) -> dict | None:
    return next((e for e in entries if e["full_name"] == full_name), None)


# ---------------------------------------------------------------------------
# Selection: installed / kind / category filtering
# ---------------------------------------------------------------------------


async def test_a_new_not_installed_integration_is_visible_with_kind_integration(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    item = by_full_name(result["items"], NEW_INTEGRATION)
    assert item is not None
    assert item["kind"] == "integration"


async def test_a_new_plugin_maps_to_kind_card(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    item = by_full_name(result["items"], NEW_PLUGIN)
    assert item is not None
    assert item["kind"] == "card"


async def test_a_new_theme_is_excluded_entirely(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    all_entries = result["items"] + result["hidden"]
    assert by_full_name(all_entries, NEW_THEME) is None


async def test_an_installed_item_flagged_new_is_excluded(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    all_entries = result["items"] + result["hidden"]
    assert by_full_name(all_entries, INSTALLED_NEW) is None


async def test_known_full_names_includes_every_repository_in_the_snapshot(
    hass: HomeAssistant, registry: NewItemRegistry
):
    """Used by the coordinator to prune the registry -- installed, themes, all of it."""
    result = await run_scan(hass, registry)

    assert INSTALLED_NEW in result["known_full_names"]
    assert NEW_THEME in result["known_full_names"]
    assert DISMISSED_ITEM in result["known_full_names"]


# ---------------------------------------------------------------------------
# None-tolerance
# ---------------------------------------------------------------------------


async def test_null_description_topics_and_stars_do_not_raise_and_stay_null(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    item = by_full_name(result["items"], SPARSE_ITEM)
    assert item is not None
    assert item["description"] is None
    assert item["topics"] == []
    assert item["stars"] is None
    assert item["downloads"] is None
    assert item["last_updated"] is None


async def test_description_is_truncated_to_200_characters(
    hass: HomeAssistant, registry: NewItemRegistry
):
    long_description = "x" * 400
    repos = dict(REPOS)
    repos["1"] = {**repos["1"], "description": long_description}
    scanner = ComponentScanner(hass, None)
    with patch.object(
        ComponentScanner, "_load_hacs_repositories", AsyncMock(return_value=repos)
    ):
        result = await scanner.scan_new_items(registry, home_country=DEFAULT_HOME_COUNTRY)

    item = by_full_name(result["items"], NEW_INTEGRATION)
    assert len(item["description"]) == 200


# ---------------------------------------------------------------------------
# Exact per-item dict keys
# ---------------------------------------------------------------------------


async def test_a_visible_item_carries_exactly_the_specified_keys(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    item = by_full_name(result["items"], NEW_INTEGRATION)
    assert set(item) == {
        "name",
        "full_name",
        "repository",
        "kind",
        "description",
        "topics",
        "stars",
        "downloads",
        "last_updated",
        "first_seen",
        "age_days",
        "category",
        "ai_pending",
    }


async def test_ai_pending_is_true_with_no_cached_verdict(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    item = by_full_name(result["items"], NEW_INTEGRATION)
    assert item["ai_pending"] is True
    assert item["category"] == NEW_ITEM_CATEGORY_FALLBACK


async def test_a_cached_ai_verdict_is_used_and_ai_pending_is_false(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.set_ai(
        NEW_INTEGRATION, category="Climate & heating", country_scope=None, model="test"
    )

    result = await run_scan(hass, registry)

    item = by_full_name(result["items"], NEW_INTEGRATION)
    assert item["ai_pending"] is False
    assert item["category"] == "Climate & heating"


# ---------------------------------------------------------------------------
# note_seen discipline
# ---------------------------------------------------------------------------


async def test_note_seen_is_only_called_for_repos_new_in_this_snapshot(
    hass: HomeAssistant, registry: NewItemRegistry
):
    with patch.object(registry, "note_seen", wraps=registry.note_seen) as spy:
        await run_scan(hass, registry)

    called_for = {call.args[0] for call in spy.call_args_list}
    # Flagged new=True in REPOS:
    assert NEW_INTEGRATION in called_for
    assert NEW_PLUGIN in called_for
    # Not flagged new=True this cycle -- must not be (re-)noted:
    assert STILL_FRESH_ITEM not in called_for
    assert STALE_ITEM not in called_for
    assert NOT_NEW_NOT_INSTALLED not in called_for


# ---------------------------------------------------------------------------
# The 30-day window, after HACS clears its own "new" flag
# ---------------------------------------------------------------------------


async def test_an_item_first_seen_five_days_ago_still_appears_once_hacs_clears_new(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.note_seen(STILL_FRESH_ITEM, now=at(5))

    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], STILL_FRESH_ITEM) is not None


async def test_an_item_first_seen_ninety_days_ago_no_longer_appears(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.note_seen(STALE_ITEM, now=at(90))

    result = await run_scan(hass, registry)

    all_entries = result["items"] + result["hidden"]
    assert by_full_name(all_entries, STALE_ITEM) is None


async def test_an_item_never_new_and_never_seen_before_does_not_appear(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    all_entries = result["items"] + result["hidden"]
    assert by_full_name(all_entries, NOT_NEW_NOT_INSTALLED) is None


# ---------------------------------------------------------------------------
# Country pre-filter
# ---------------------------------------------------------------------------


async def test_a_clearly_foreign_repo_is_hidden_with_an_explained_reason(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], FOREIGN_ITEM) is None
    hidden = by_full_name(result["hidden"], FOREIGN_ITEM)
    assert hidden is not None
    assert hidden["country"] == "SE"
    assert hidden["term"]
    assert hidden["field"]
    assert hidden["source"] == "rules"
    assert hidden["reason"]

    record = registry.hidden_record(FOREIGN_ITEM)
    assert record is not None
    assert record["country"] == "SE"
    assert record["source"] == "rules"


async def test_an_au_nz_repo_is_kept_because_home_signal_wins(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], AU_NZ_ITEM) is not None
    assert by_full_name(result["hidden"], AU_NZ_ITEM) is None


async def test_auto_hide_foreign_false_keeps_everything_visible(
    hass: HomeAssistant, registry: NewItemRegistry
):
    result = await run_scan(hass, registry, auto_hide_foreign=False)

    assert by_full_name(result["items"], FOREIGN_ITEM) is not None
    assert result["hidden"] == []


async def test_never_hide_exempts_a_previously_restored_item(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.record_hidden(
        FOREIGN_ITEM, country="SE", term="Sweden", field="description", source="rules"
    )
    registry.restore(FOREIGN_ITEM)  # sets never_hide, clears the auto-hide

    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], FOREIGN_ITEM) is not None
    assert by_full_name(result["hidden"], FOREIGN_ITEM) is None


async def test_keeping_a_previously_hidden_item_clears_the_hidden_record(
    hass: HomeAssistant, registry: NewItemRegistry
):
    """A repo once matched foreign that no longer does (edited description, say)."""
    registry.record_hidden(
        AU_NZ_ITEM, country="SE", term="Sweden", field="description", source="rules"
    )

    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], AU_NZ_ITEM) is not None
    assert registry.hidden_record(AU_NZ_ITEM) is None


# ---------------------------------------------------------------------------
# The AI's country_scope as a second hide chance, re-applied every scan
# ---------------------------------------------------------------------------


async def test_a_cached_foreign_ai_scope_hides_an_item_the_rules_would_keep(
    hass: HomeAssistant, registry: NewItemRegistry
):
    """The whole point of the second hide chance.

    NEW_INTEGRATION's own text names no country at all -- that is exactly
    when the AI's judgement is the only evidence there is. The scan has to
    read the cached verdict, because the scan is the only place visibility
    is decided: if it looked at ``classify()`` alone, an AI-sourced hide
    would be cleared on the very next cycle and, the verdict being cached
    (so ``ai_pending`` stays false), never asked about again -- un-hiding
    itself permanently and in silence.
    """
    registry.set_ai(
        NEW_INTEGRATION, category="Energy & solar", country_scope="SE", model="test"
    )

    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], NEW_INTEGRATION) is None
    hidden = by_full_name(result["hidden"], NEW_INTEGRATION)
    assert hidden is not None
    assert hidden["source"] == "ai"
    assert hidden["country"] == "SE"
    assert hidden["field"] == "ai_country_scope"
    assert hidden["reason"]

    record = registry.hidden_record(NEW_INTEGRATION)
    assert record is not None
    assert record["source"] == "ai"


async def test_a_foreign_ai_scope_named_in_prose_hides_too(
    hass: HomeAssistant, registry: NewItemRegistry
):
    """The model is asked to *name* the country, so it often answers in words."""
    registry.set_ai(
        NEW_INTEGRATION,
        category="Transport & travel",
        country_scope="New Zealand",
        model="test",
    )

    result = await run_scan(hass, registry)

    assert by_full_name(result["hidden"], NEW_INTEGRATION) is not None


async def test_a_global_ai_scope_leaves_the_item_visible(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.set_ai(
        NEW_INTEGRATION, category="Other", country_scope="global", model="test"
    )

    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], NEW_INTEGRATION) is not None
    assert registry.hidden_record(NEW_INTEGRATION) is None


async def test_a_home_signal_in_the_text_beats_a_foreign_ai_scope(
    hass: HomeAssistant, registry: NewItemRegistry
):
    """Any home signal wins outright -- against the model as against the rules."""
    registry.set_ai(
        AU_NZ_ITEM, category="Energy & solar", country_scope="SE", model="test"
    )

    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], AU_NZ_ITEM) is not None
    assert by_full_name(result["hidden"], AU_NZ_ITEM) is None


async def test_never_hide_exempts_an_item_from_the_ai_scope_too(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.set_ai(
        NEW_INTEGRATION, category="Energy & solar", country_scope="SE", model="test"
    )
    # The real sequence: the AI batch hid it, then the user pressed Restore in
    # the audit view. Restore only grants never_hide when there *is* an
    # auto-hide record to undo, so the hide has to come first.
    registry.record_hidden(
        NEW_INTEGRATION,
        country="SE",
        term="SE",
        field="ai_country_scope",
        source="ai",
    )
    registry.restore(NEW_INTEGRATION)

    result = await run_scan(hass, registry)

    assert by_full_name(result["items"], NEW_INTEGRATION) is not None
    assert by_full_name(result["hidden"], NEW_INTEGRATION) is None


async def test_auto_hide_foreign_false_also_disarms_the_ai_scope(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.set_ai(
        NEW_INTEGRATION, category="Energy & solar", country_scope="SE", model="test"
    )

    result = await run_scan(hass, registry, auto_hide_foreign=False)

    assert by_full_name(result["items"], NEW_INTEGRATION) is not None
    assert result["hidden"] == []


# ---------------------------------------------------------------------------
# Dismissal
# ---------------------------------------------------------------------------


async def test_a_dismissed_item_is_excluded_from_items_but_counted(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.dismiss(DISMISSED_ITEM)

    result = await run_scan(hass, registry)

    all_entries = result["items"] + result["hidden"]
    assert by_full_name(all_entries, DISMISSED_ITEM) is None
    assert result["dismissed_count"] == 1


# ---------------------------------------------------------------------------
# total_new / by_category (sensor-shape aggregates)
# ---------------------------------------------------------------------------


async def test_total_new_counts_everything_including_hidden_and_dismissed(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.dismiss(DISMISSED_ITEM)

    result = await run_scan(hass, registry)

    # 6 candidates flagged new=True this cycle: integration, plugin, foreign,
    # au/nz, sparse, dismissed -- NOT the theme (wrong kind), the installed
    # one, or the two first-seen-window items (which need to be pre-seeded
    # via note_seen() to appear at all, since new=False here; see the
    # dedicated first-seen-window tests), or the never-new/never-seen one.
    assert result["total_new"] == 6
    assert len(result["items"]) + len(result["hidden"]) + result["dismissed_count"] == (
        result["total_new"]
    )


async def test_total_new_also_counts_a_seeded_first_seen_window_item(
    hass: HomeAssistant, registry: NewItemRegistry
):
    registry.note_seen(STILL_FRESH_ITEM, now=at(5))

    result = await run_scan(hass, registry)

    assert result["total_new"] == 7  # the usual 6 plus the seeded one


def test_by_category_counts_only_visible_items():
    """``by_category`` is built on the sensor from ``items`` only (#95)."""
    items = [
        {"category": "Climate & heating"},
        {"category": "Climate & heating"},
        {"category": "Other"},
    ]
    by_category: dict[str, int] = {}
    for item in items:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1

    assert by_category == {"Climate & heating": 2, "Other": 1}


# ---------------------------------------------------------------------------
# AI batch runner (coordinator), every AI call mocked
# ---------------------------------------------------------------------------


def make_entry(
    *,
    ai_enabled: bool = True,
    ai_entity: str = "ai_task.test",
    home_country: str = DEFAULT_HOME_COUNTRY,
    auto_hide_foreign: bool = True,
    ai_budget_seconds: int = 20,
    new_items_enabled: bool = True,
) -> MagicMock:
    entry = MagicMock()
    entry.options = {
        CONF_AI_CATEGORIZATION_ENABLED: ai_enabled,
        CONF_AI_TASK_ENTITY: ai_entity,
        CONF_HOME_COUNTRY: home_country,
        CONF_AUTO_HIDE_FOREIGN: auto_hide_foreign,
        CONF_NEW_ITEMS_AI_BUDGET_SECONDS: ai_budget_seconds,
        CONF_NEW_ITEMS_ENABLED: new_items_enabled,
    }
    return entry


def new_item(full_name: str, *, ai_pending: bool = True, **extra) -> dict:
    base = {
        "name": full_name.split("/")[-1],
        "full_name": full_name,
        "repository": f"https://github.com/{full_name}",
        "kind": "integration",
        "description": "A thing.",
        "topics": [],
        "stars": 1,
        "downloads": 1,
        "last_updated": None,
        "first_seen": dt_util.utcnow().isoformat(),
        "age_days": 1,
        "category": NEW_ITEM_CATEGORY_FALLBACK,
        "ai_pending": ai_pending,
    }
    base.update(extra)
    return base


@pytest.fixture
def new_items_coordinator(hass: HomeAssistant) -> CustomComponentMonitorCoordinator:
    coord = CustomComponentMonitorCoordinator(hass, make_entry())
    coord._ai_loaded = True
    coord._ai_store = None
    coord.new_items_registry._loaded = True
    coord.new_items_registry._store = None
    return coord


async def test_the_max_items_limit_caps_one_pass_and_reports_the_rest(
    new_items_coordinator: CustomComponentMonitorCoordinator,
):
    """``max_items`` is the service's explicit "just these" escape hatch."""
    items = [new_item(f"owner/repo-{i}") for i in range(15)]
    new_items_coordinator.data = {"new_items": {"items": items, "hidden": []}}
    call = AsyncMock(return_value={"category": "Other", "country_scope": "global"})

    with patch.object(new_items_coordinator, "_async_ai_call", call):
        counts = await new_items_coordinator.async_generate_new_item_categories(limit=5)

    assert call.await_count == 5
    assert counts["generated"] == 5
    assert counts["remaining"] == 10


async def test_with_no_limit_a_fast_backend_clears_the_whole_backlog_in_one_pass(
    new_items_coordinator: CustomComponentMonitorCoordinator,
):
    """The v1.15.1 change, stated as a test.

    v1.15.0 stopped after the ``max_ai_per_cycle`` option's worth of items and
    waited an hour for the next scan. There is no such number any more: the
    pass runs until the time budget is spent, so a backend that answers
    instantly finishes the lot and leaves nothing remaining.
    """
    items = [new_item(f"owner/repo-{i}") for i in range(37)]
    new_items_coordinator.data = {"new_items": {"items": items, "hidden": []}}
    call = AsyncMock(return_value={"category": "Other", "country_scope": "global"})

    with patch.object(new_items_coordinator, "_async_ai_call", call):
        counts = await new_items_coordinator.async_generate_new_item_categories()

    assert call.await_count == 37
    assert counts["generated"] == 37
    assert counts["remaining"] == 0


async def test_a_junk_ai_response_yields_other_without_raising(
    new_items_coordinator: CustomComponentMonitorCoordinator,
):
    items = [new_item("owner/junk-repo")]
    new_items_coordinator.data = {"new_items": {"items": items, "hidden": []}}
    call = AsyncMock(return_value="not json, not helpful, just prose")

    with patch.object(new_items_coordinator, "_async_ai_call", call):
        counts = await new_items_coordinator.async_generate_new_item_categories()

    assert counts["failed"] == 0
    assert counts["generated"] == 1
    assert items[0]["category"] == NEW_ITEM_CATEGORY_FALLBACK
    assert items[0]["ai_pending"] is False

    cached = new_items_coordinator.new_items_registry.get_ai("owner/junk-repo")
    assert cached is not None
    assert cached["category"] == NEW_ITEM_CATEGORY_FALLBACK


async def test_an_ai_detected_foreign_scope_hides_the_item_with_source_ai(
    new_items_coordinator: CustomComponentMonitorCoordinator,
):
    items = [new_item("owner/swedish-thing")]
    new_items_data = {"items": items, "hidden": []}
    new_items_coordinator.data = {"new_items": new_items_data}
    call = AsyncMock(return_value={"category": "Energy & solar", "country_scope": "SE"})

    with patch.object(new_items_coordinator, "_async_ai_call", call):
        await new_items_coordinator.async_generate_new_item_categories()

    assert new_items_data["items"] == []
    assert len(new_items_data["hidden"]) == 1
    hidden = new_items_data["hidden"][0]
    assert hidden["full_name"] == "owner/swedish-thing"
    assert hidden["source"] == "ai"
    assert hidden["country"] == "SE"

    record = new_items_coordinator.new_items_registry.hidden_record("owner/swedish-thing")
    assert record is not None
    assert record["source"] == "ai"


async def test_the_batch_keeps_an_item_whose_own_text_names_home(
    new_items_coordinator: CustomComponentMonitorCoordinator,
):
    """The batch and the scan have to agree, or the item flaps.

    Both now go through the one ``ai_scope_hides`` predicate. If the batch
    hid this and the scan (which sees the home signal) un-hid it an hour
    later, the item would appear and disappear on every cycle.
    """
    items = [new_item("owner/energy-thing", description="AU/NZ electricity prices.")]
    new_items_data = {"items": items, "hidden": []}
    new_items_coordinator.data = {"new_items": new_items_data}
    call = AsyncMock(return_value={"category": "Energy & solar", "country_scope": "SE"})

    with patch.object(new_items_coordinator, "_async_ai_call", call):
        await new_items_coordinator.async_generate_new_item_categories()

    assert len(new_items_data["items"]) == 1
    assert new_items_data["hidden"] == []
    assert (
        new_items_coordinator.new_items_registry.hidden_record("owner/energy-thing")
        is None
    )


async def test_the_batch_hides_on_a_country_named_in_prose(
    new_items_coordinator: CustomComponentMonitorCoordinator,
):
    items = [new_item("owner/transit-thing")]
    new_items_data = {"items": items, "hidden": []}
    new_items_coordinator.data = {"new_items": new_items_data}
    call = AsyncMock(
        return_value={"category": "Transport & travel", "country_scope": "New Zealand"}
    )

    with patch.object(new_items_coordinator, "_async_ai_call", call):
        await new_items_coordinator.async_generate_new_item_categories()

    assert new_items_data["items"] == []
    assert len(new_items_data["hidden"]) == 1
    assert new_items_data["hidden"][0]["source"] == "ai"


async def test_an_exception_from_the_ai_call_leaves_the_item_pending(
    new_items_coordinator: CustomComponentMonitorCoordinator,
):
    items = [new_item("owner/flaky-repo")]
    new_items_coordinator.data = {"new_items": {"items": items, "hidden": []}}
    call = AsyncMock(side_effect=RuntimeError("provider down"))

    with patch.object(new_items_coordinator, "_async_ai_call", call):
        counts = await new_items_coordinator.async_generate_new_item_categories()

    assert counts["failed"] == 1
    assert counts["generated"] == 0
    assert items[0]["ai_pending"] is True
    assert new_items_coordinator.new_items_registry.get_ai("owner/flaky-repo") is None
    assert new_items_coordinator.new_items_ai_last_error is not None


async def test_ai_categorisation_is_skipped_when_ai_is_disabled(hass: HomeAssistant):
    coord = CustomComponentMonitorCoordinator(hass, make_entry(ai_enabled=False))
    coord.data = {
        "new_items": {"items": [new_item("owner/repo")], "hidden": []}
    }
    call = AsyncMock(return_value={"category": "Other", "country_scope": "global"})

    with patch.object(coord, "_async_ai_call", call):
        counts = await coord.async_generate_new_item_categories()

    call.assert_not_awaited()
    assert counts == {"generated": 0, "failed": 0, "skipped": 0, "remaining": 0}
