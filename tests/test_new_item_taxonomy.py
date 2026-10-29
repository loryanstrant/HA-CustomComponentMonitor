"""The New-in-HACS category taxonomy (v1.17.0).

"Other" had become the biggest group on the card — 58 of 127 visible items on
this house's production store, with nothing left in the AI queue, so every one
of them was a settled verdict rather than unfinished work. The cause was the
taxonomy, not the model: sixteen bare labels with no definitions, no bucket for
the tooling HACS mostly publishes, and a prompt that offered "Other" as an
escape hatch.

What is asserted here is the shape of the fix, because none of it is
self-enforcing: a label without a definition is a label the prompt cannot
explain, an alias pointing at a string that is not a category is a silent
mis-file, and the card override only earns its keep if it is applied at the one
place that writes the cache.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.custom_component_monitor.const import (
    NEW_ITEM_CATEGORY_ALIASES,
    NEW_ITEM_CATEGORY_DEFINITIONS,
    NEW_ITEM_CATEGORY_FALLBACK,
    NEW_ITEM_CATEGORY_OPTIONS,
)
from custom_components.custom_component_monitor.sensor import (
    NEW_ITEM_CATEGORY_CATALOGUE,
    CustomComponentMonitorCoordinator,
    card_category_override,
)

from .conftest import make_entry

DASHBOARD = "Dashboard & theming"
DIAGNOSTICS = "System & diagnostics"


# -- the label set ---------------------------------------------------------------


def test_every_category_has_a_definition() -> None:
    """A label with no definition is a label the prompt cannot explain.

    The catalogue the model is sent is built from
    :data:`NEW_ITEM_CATEGORY_DEFINITIONS`, keyed on the option strings, so a
    category added to the list and forgotten here would raise a ``KeyError`` at
    import. Asserting set equality rather than catching that is what makes the
    failure say *which* label, and catches the reverse mistake too — a stale
    definition for a label that no longer exists.
    """
    assert set(NEW_ITEM_CATEGORY_DEFINITIONS) == set(NEW_ITEM_CATEGORY_OPTIONS)


def test_other_is_the_last_option_and_the_fallback() -> None:
    """Both copies in services.yaml and the card's group order assume this."""
    assert NEW_ITEM_CATEGORY_OPTIONS[-1] == NEW_ITEM_CATEGORY_FALLBACK


def test_the_options_are_alphabetical_apart_from_other() -> None:
    """The list is hand-maintained in three places; order is the tell."""
    rest = NEW_ITEM_CATEGORY_OPTIONS[:-1]
    assert rest == sorted(rest, key=str.lower)


def test_every_definition_is_a_sentence_not_a_restatement() -> None:
    """A definition that just repeats the label tells the model nothing."""
    for label, definition in NEW_ITEM_CATEGORY_DEFINITIONS.items():
        assert len(definition) > len(label) + 20, label


def test_the_catalogue_names_every_category_once() -> None:
    """The prompt's static prefix, built once at import for the prefix cache."""
    lines = NEW_ITEM_CATEGORY_CATALOGUE.splitlines()

    assert len(lines) == len(NEW_ITEM_CATEGORY_OPTIONS)
    for label in NEW_ITEM_CATEGORY_OPTIONS:
        assert f"- {label}: " in NEW_ITEM_CATEGORY_CATALOGUE


# -- the aliases -----------------------------------------------------------------


def test_every_alias_resolves_to_a_real_category() -> None:
    """An alias pointing at a near-miss string files items into nothing.

    ``_normalise_new_item_category`` looks the model's answer up here and uses
    the result verbatim, so a value with a typo or an old label becomes a group
    heading no service call and no copy of the list can match.
    """
    for alias, category in NEW_ITEM_CATEGORY_ALIASES.items():
        assert category in NEW_ITEM_CATEGORY_OPTIONS, alias


def test_system_resolves_to_diagnostics_not_networking() -> None:
    """The single most damaging entry in the old table.

    "system" is the one-word answer a small model reaches for when the
    repository is Home Assistant plumbing, and until v1.17.0 it pointed at
    "Network & infrastructure" — filing entity audits and recorder tools under
    networking, which is why the new bucket has to own it outright.
    """
    assert NEW_ITEM_CATEGORY_ALIASES["system"] == DIAGNOSTICS


def test_the_networking_aliases_were_left_alone() -> None:
    """Re-pointing "system" must not have dragged its neighbours with it."""
    for alias in ("network", "infrastructure", "router", "nas"):
        assert NEW_ITEM_CATEGORY_ALIASES[alias] == "Network & infrastructure"


@pytest.mark.parametrize(
    ("alias", "expected"),
    [
        ("diagnostics", DIAGNOSTICS),
        ("monitoring", DIAGNOSTICS),
        ("logs", DIAGNOSTICS),
        ("recorder", DIAGNOSTICS),
        ("backups", DIAGNOSTICS),
        ("scheduler", "Automation & scheduling"),
        ("blueprints", "Automation & scheduling"),
        ("irrigation", "Garden & water"),
        ("aquarium", "Garden & water"),
        ("blinds", "Covers & blinds"),
        ("garage door", "Covers & blinds"),
        ("bluetooth", "Sensors & devices"),
        ("dongle", "Sensors & devices"),
    ],
)
def test_the_new_buckets_have_aliases(alias: str, expected: str) -> None:
    """A new label with no aliases only ever fills on an exact-string answer."""
    assert NEW_ITEM_CATEGORY_ALIASES[alias] == expected


# -- the card override -----------------------------------------------------------


def test_a_card_that_came_back_other_becomes_dashboard_furniture() -> None:
    assert card_category_override("card", NEW_ITEM_CATEGORY_FALLBACK) == DASHBOARD


def test_a_card_the_ai_placed_somewhere_specific_is_left_alone() -> None:
    """The override only ever promotes the fallback — it never overrules."""
    assert card_category_override("card", "Lighting") == "Lighting"


def test_an_integration_that_came_back_other_stays_other() -> None:
    """An integration is not frontend, so there is nothing to infer from kind."""
    assert (
        card_category_override("integration", NEW_ITEM_CATEGORY_FALLBACK)
        == NEW_ITEM_CATEGORY_FALLBACK
    )


@pytest.mark.parametrize("kind", [None, "", "  ", "theme", "plugin"])
def test_an_unknown_kind_is_not_assumed_to_be_a_card(kind) -> None:
    """Only HACS's literal "card" counts; "plugin" is HACS's *URL* word."""
    assert (
        card_category_override(kind, NEW_ITEM_CATEGORY_FALLBACK)
        == NEW_ITEM_CATEGORY_FALLBACK
    )


def test_the_kind_comparison_tolerates_case_and_padding() -> None:
    assert card_category_override(" Card ", NEW_ITEM_CATEGORY_FALLBACK) == DASHBOARD


# -- the override is applied where the cache is written ---------------------------


@pytest.fixture
def new_items_coordinator(hass: HomeAssistant) -> CustomComponentMonitorCoordinator:
    """A coordinator whose new-items registry is loaded but never written."""
    coord = CustomComponentMonitorCoordinator(hass, make_entry())
    coord._ai_loaded = True
    coord._ai_store = None
    coord.new_items_registry._loaded = True
    coord.new_items_registry._store = None  # async_save() becomes a no-op
    return coord


SETTINGS = {
    "auto_hide_foreign": True,
    "home_country": "AU",
    "extra_home_terms": [],
}


def _card_item() -> dict:
    return {
        "name": "Confirm Tap Card",
        "full_name": "owner/confirm-tap-card",
        "kind": "card",
        "category": NEW_ITEM_CATEGORY_FALLBACK,
        "ai_pending": True,
    }


def test_the_override_reaches_both_the_cache_and_the_published_item(
    new_items_coordinator,
) -> None:
    """One rule, applied once, at the only production ``set_ai`` call.

    The cached verdict and the in-memory item are read by different things
    later — ``scan_new_items`` rebuilds the card from the cache, while this
    pass's ``items`` list is what the sensor publishes immediately — so a
    version of this rule that only fixed one of them would show the user
    "Dashboard & theming" until the next scan quietly put it back to "Other".
    """
    item = _card_item()
    items = [item]
    new_items: dict = {"items": items, "hidden": []}

    new_items_coordinator._apply_new_item_ai_result(
        item,
        {"category": NEW_ITEM_CATEGORY_FALLBACK, "country_scope": "global"},
        items=items,
        new_items=new_items,
        settings=SETTINGS,
        ai_entity="ai_task.test",
    )

    assert item["category"] == DASHBOARD
    assert item["ai_pending"] is False
    cached = new_items_coordinator.new_items_registry.get_ai("owner/confirm-tap-card")
    assert cached["category"] == DASHBOARD


def test_an_integrations_verdict_is_passed_through_untouched(
    new_items_coordinator,
) -> None:
    item = {
        "name": "Entity Audit",
        "full_name": "owner/entity-audit",
        "kind": "integration",
        "category": NEW_ITEM_CATEGORY_FALLBACK,
        "ai_pending": True,
    }
    items = [item]

    new_items_coordinator._apply_new_item_ai_result(
        item,
        {"category": DIAGNOSTICS, "country_scope": "global"},
        items=items,
        new_items={"items": items, "hidden": []},
        settings=SETTINGS,
        ai_entity="ai_task.test",
    )

    assert item["category"] == DIAGNOSTICS
    cached = new_items_coordinator.new_items_registry.get_ai("owner/entity-audit")
    assert cached["category"] == DIAGNOSTICS


def test_a_card_hidden_by_its_ai_country_scope_still_carries_the_override(
    new_items_coordinator,
) -> None:
    """The two rules are independent: being hidden does not restore "Other".

    A hidden item's category is what ``restore_new_items`` matches on later, so
    dropping the override on the hide path would make the card's own group name
    and the restore service disagree about the same item.
    """
    item = _card_item()
    items = [item]
    new_items: dict = {"items": items, "hidden": []}

    new_items_coordinator._apply_new_item_ai_result(
        item,
        {"category": NEW_ITEM_CATEGORY_FALLBACK, "country_scope": "SE"},
        items=items,
        new_items=new_items,
        settings=SETTINGS,
        ai_entity="ai_task.test",
    )

    assert items == []  # moved out of the visible set
    assert item["category"] == DASHBOARD
    cached = new_items_coordinator.new_items_registry.get_ai("owner/confirm-tap-card")
    assert cached["category"] == DASHBOARD


# -- the prompt ------------------------------------------------------------------


async def test_the_prompt_offers_the_catalogue_and_refuses_other_as_an_escape(
    new_items_coordinator,
) -> None:
    """What actually changed the model's behaviour, asserted on the text.

    The old prompt listed sixteen bare labels and said "use 'Other' only when
    nothing else fits", which reads as permission. The new one defines every
    label, puts the catalogue *first* so the local backend's prefix cache has
    something to reuse across a batch, and states positively that "Other" means
    off-topic.
    """
    captured: dict[str, str] = {}

    async def _capture(_entity, prompt, _structure=None):
        captured["prompt"] = prompt
        return {"category": "Lighting", "country_scope": "global"}

    with patch.object(new_items_coordinator, "_async_ai_call", _capture):
        await new_items_coordinator._async_categorise_new_item(
            {
                "name": "Some Light Thing",
                "full_name": "owner/some-light-thing",
                "kind": "integration",
                "description": "Controls lights.",
                "topics": ["lights"],
            },
            "ai_task.test",
        )

    prompt = captured["prompt"]
    assert NEW_ITEM_CATEGORY_CATALOGUE in prompt
    # The catalogue leads, so the shared prefix is as long as it can be.
    assert prompt.index(NEW_ITEM_CATEGORY_CATALOGUE) < prompt.index("Some Light Thing")
    assert "only when nothing else fits" not in prompt
    assert "not about a home" in prompt
