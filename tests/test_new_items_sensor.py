"""The HACS-new-items sensor's published attributes, and the cap on them.

A real HACS store put 2,240 repositories through this entity and 1.2 MB into
these attributes, which Home Assistant re-sends to every connected dashboard on
every refresh. :data:`NEW_ITEMS_MAX_EXPOSED` caps what crosses the websocket --
and the whole point of these tests is that capping the *list* must not cap the
*counts*, because the card draws its headline numbers from them.
"""
from __future__ import annotations

from typing import Any

import pytest

from custom_components.custom_component_monitor.const import (
    ATTR_AI_PENDING,
    ATTR_BY_CATEGORY,
    ATTR_DISMISSED_COUNT,
    ATTR_HIDDEN,
    ATTR_HIDDEN_COUNT,
    ATTR_HIDDEN_TRUNCATED,
    ATTR_ITEMS,
    ATTR_ITEMS_TRUNCATED,
    ATTR_TOTAL_NEW,
    NEW_ITEMS_MAX_EXPOSED,
    NEW_ITEMS_MAX_EXPOSED_HIDDEN,
)
from custom_components.custom_component_monitor.sensor import HacsNewItemsSensor


def make_item(index: int, **overrides: Any) -> dict[str, Any]:
    """One new-item row. ``age_days`` ascends with the index, so low = newest."""
    item = {
        "name": f"Thing {index}",
        "full_name": f"owner/thing-{index}",
        "kind": "integration",
        "category": "Other",
        "age_days": index,
        "stars": 0,
    }
    item.update(overrides)
    return item


def make_sensor(coordinator, items, hidden, **extra: Any) -> HacsNewItemsSensor:
    """A sensor wired to a coordinator holding *items* and *hidden*."""
    coordinator.data = {
        "new_items": {
            "items": items,
            "hidden": hidden,
            "dismissed_count": extra.get("dismissed_count", 0),
            "total_new": extra.get("total_new", len(items) + len(hidden)),
        },
        "new_items_home_country": "AU",
    }
    return HacsNewItemsSensor(coordinator)


def test_a_small_store_is_published_whole(coordinator):
    """Under the cap, nothing is held back and nothing claims to be."""
    sensor = make_sensor(coordinator, [make_item(i) for i in range(12)], [])

    attrs = sensor.extra_state_attributes

    assert len(attrs[ATTR_ITEMS]) == 12
    assert attrs[ATTR_ITEMS_TRUNCATED] == 0
    assert attrs[ATTR_HIDDEN_TRUNCATED] == 0
    assert sensor.native_value == 12


def test_the_item_list_is_capped_but_the_counts_are_not(coordinator):
    """The list shortens; the state and ``total_new`` keep the full figure."""
    total = NEW_ITEMS_MAX_EXPOSED + 100
    sensor = make_sensor(
        coordinator,
        [make_item(i) for i in range(total)],
        [],
        total_new=total + 7,
        dismissed_count=7,
    )

    attrs = sensor.extra_state_attributes

    assert len(attrs[ATTR_ITEMS]) == NEW_ITEMS_MAX_EXPOSED
    assert attrs[ATTR_ITEMS_TRUNCATED] == 100
    # The three numbers the card puts on screen all still describe everything.
    assert sensor.native_value == total
    assert attrs[ATTR_TOTAL_NEW] == total + 7
    assert attrs[ATTR_DISMISSED_COUNT] == 7


def test_the_hidden_list_has_its_own_smaller_cap(coordinator):
    """``hidden_count`` is the audit figure and must survive the cap intact."""
    hidden_total = NEW_ITEMS_MAX_EXPOSED_HIDDEN + 91
    sensor = make_sensor(
        coordinator,
        [make_item(i) for i in range(3)],
        [make_item(i, reason="matched \"sweden\"") for i in range(hidden_total)],
    )

    attrs = sensor.extra_state_attributes

    assert len(attrs[ATTR_HIDDEN]) == NEW_ITEMS_MAX_EXPOSED_HIDDEN
    assert attrs[ATTR_HIDDEN_TRUNCATED] == 91
    assert attrs[ATTR_HIDDEN_COUNT] == hidden_total


def test_the_newest_items_are_the_ones_that_survive(coordinator):
    """Which rows are kept is a product decision, not an accident of order.

    The coordinator's list is deliberately shuffled out of age order here:
    the cap sorts newest-first itself, so a review queue shows the arrivals
    rather than whatever happened to be scanned first.
    """
    total = NEW_ITEMS_MAX_EXPOSED + 50
    items = [make_item(i) for i in reversed(range(total))]
    sensor = make_sensor(coordinator, items, [])

    published = sensor.extra_state_attributes[ATTR_ITEMS]

    ages = [item["age_days"] for item in published]
    assert ages == sorted(ages)
    assert max(ages) == NEW_ITEMS_MAX_EXPOSED - 1


def test_stars_break_the_tie_between_equally_new_items(coordinator):
    """On a day when fifty things land at once, popularity decides."""
    items = [make_item(0, stars=i, full_name=f"owner/eq-{i}") for i in range(
        NEW_ITEMS_MAX_EXPOSED + 10
    )]
    sensor = make_sensor(coordinator, items, [])

    published = sensor.extra_state_attributes[ATTR_ITEMS]

    stars = [item["stars"] for item in published]
    assert min(stars) == 10
    assert len(published) == NEW_ITEMS_MAX_EXPOSED


def test_by_category_counts_only_what_was_published(coordinator):
    """A group header must never promise a row the card was not given.

    ``by_category`` drives "Energy & solar (14)" and the "Dismiss all (14)"
    button beside it, so counting it over the full set would offer to act on
    rows that are not in ``items`` at all.
    """
    total = NEW_ITEMS_MAX_EXPOSED + 40
    items = [make_item(i, category="Energy & solar") for i in range(total)]
    sensor = make_sensor(coordinator, items, [])

    attrs = sensor.extra_state_attributes

    assert attrs[ATTR_BY_CATEGORY] == {"Energy & solar": NEW_ITEMS_MAX_EXPOSED}
    assert sum(attrs[ATTR_BY_CATEGORY].values()) == len(attrs[ATTR_ITEMS])


def test_ai_pending_counts_the_whole_backlog(coordinator):
    """The opposite choice to ``by_category``, and for the opposite reason.

    "27 still being sorted" is a statement about the queue the AI has left,
    not about what fitted into the attributes, so this one is counted over
    every item the coordinator holds.
    """
    total = NEW_ITEMS_MAX_EXPOSED + 60
    items = [make_item(i, ai_pending=True) for i in range(total)]
    sensor = make_sensor(coordinator, items, [])

    attrs = sensor.extra_state_attributes

    assert attrs[ATTR_AI_PENDING] == total
    assert len(attrs[ATTR_ITEMS]) == NEW_ITEMS_MAX_EXPOSED


def test_an_empty_store_publishes_empty_lists_not_none(coordinator):
    """The card reads ``attrs.items`` directly; ``None`` would break it."""
    sensor = make_sensor(coordinator, [], [])

    attrs = sensor.extra_state_attributes

    assert attrs[ATTR_ITEMS] == []
    assert attrs[ATTR_HIDDEN] == []
    assert attrs[ATTR_BY_CATEGORY] == {}
    assert sensor.native_value == 0


@pytest.mark.parametrize("attribute", [ATTR_ITEMS, ATTR_HIDDEN])
def test_the_two_big_lists_stay_out_of_the_recorder(attribute):
    """Capping the websocket payload does not make the recorder exclusion moot.

    300 rows a cycle is still far too much to write to the database, so both
    lists remain unrecorded -- this test fails if a later edit renames one of
    them and forgets the frozenset.
    """
    assert attribute in HacsNewItemsSensor._unrecorded_attributes
