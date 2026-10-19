"""Country classification for the New-in-HACS review card.

A repository built for another country's mail service, transport network or
energy market says so somewhere in its prose -- a country name, a demonym, a
bare ISO code, or a national brand. This module is a table, not a model: it
never touches the network or Home Assistant, so these tests exercise it
exactly as every other plain-Python table in this integration is tested --
with no ``hass`` fixture at all.
"""
from __future__ import annotations

import pytest

from custom_components.custom_component_monitor.countries import (
    CountryMatch,
    CountryVerdict,
    classify,
    scope_is_foreign,
)


def desc(text: str) -> dict:
    """A minimal ``fields`` mapping carrying only a description."""
    return {"name": None, "full_name": None, "description": text, "topics": None}


# -- home: read as AU, never hidden -----------------------------------------

HOME_SENTENCES = [
    "Spot price and forecast sensors for AU energy markets.",
    "Built for the AUS postal network.",
    "Ships Aus-wide parcel tracking.",
    "Aust Post delivery tracker and notifier.",
    "A dashboard card built for Oz drivers and their utes.",
    "Weather radar and alerts for Australia.",
    "An Australian energy market integration.",
    "The Aussie battery monitor card.",
    "Transport departures for NSW trains.",
    "Power outage alerts for VIC residents.",
    "Rainfall radar for QLD.",
    "Energy plan comparison for ACT households.",
    "Public transport data for New South Wales.",
    "Track your next Australia Post parcel delivery.",
    "Spot pricing sensor for Amber Electric customers.",
]


@pytest.mark.parametrize("sentence", HOME_SENTENCES)
def test_home_sentences_are_never_hidden(sentence):
    verdict = classify(desc(sentence), home_country="AU")

    assert verdict.hide is False
    assert verdict.home_matches, f"expected a home match in: {sentence!r}"


# -- must not register as any country match at all ---------------------------

NO_MATCH_TEXTS = [
    "status",
    "bus",
    "Delivery",
    "Attribute",
    "inside",
    "nominate",
    "item",
    "monitor",
    "description",
    "automation",
    "pause",
    "because",
    "thermostat",
    "idle",
    "isolate",
    "indicator",
    "integration for a generic device",
    "a us-based cloud",
    "the IT department",
    "check-in sensor",
]


@pytest.mark.parametrize("text", NO_MATCH_TEXTS)
def test_ordinary_english_never_matches_a_country(text):
    verdict = classify(desc(text), home_country="AU")

    assert verdict.home_matches == ()
    assert verdict.foreign_matches == ()
    assert verdict.hide is False


# -- the harder half: country words that are also ordinary English ------------
#
# These are the sentences a word-boundary match gets wrong, and a wrong *hide*
# is invisible -- the item simply never appears -- so each one is a repository
# the card would have silently eaten. Two separate rules keep them out:
# _AMBIGUOUS_LONG_TERMS (the long, case-insensitive rule) and _code_pattern
# being ALL-CAPS only (the short-code rule, which is why "Can be used ..." is
# not Canada and "Nor does it ..." is not Norway).

AMBIGUOUS_ENGLISH_TEXTS = [
    "The Swiss army knife of home automation dashboards.",
    "Can be used with any thermostat that reports a setpoint.",
    "Nor does it need a cloud account to work.",
    "Fin temperature sensor for a heatsink.",
    "Mount the sensor on flag poles or fence posts.",
    "Pole position lap timer for sim racing.",
    "A Great Dane activity and weight tracker.",
    "Roast turkey cooking timer with push alerts.",
    "Fine china cabinet display lighting.",
    "Add a chile pepper plant moisture sensor.",
    "Swede and parsnip harvest tracker for the veggie patch.",
    "Thai language support for the voice assistant.",
    "Kiwi fruit ripeness readings from a Bluetooth probe.",
    "North America and South America region maps.",
    "Run an post-install hook after each update.",
    "A card to act on pending updates.",
    "Act on the first trigger only, then stop.",
]


@pytest.mark.parametrize("text", AMBIGUOUS_ENGLISH_TEXTS)
def test_english_words_that_are_also_country_words_never_match(text):
    verdict = classify(desc(text), home_country="AU")

    assert verdict.home_matches == (), f"false home match in: {text!r}"
    assert verdict.foreign_matches == (), f"false foreign match in: {text!r}"
    assert verdict.hide is False


# -- ... and the countries above are still reachable by their real names ------

STILL_REACHABLE = [
    ("Train departures and delays for Switzerland.", "CH"),
    ("Polish energy spot prices from the day-ahead market.", "PL"),
    ("Chinese air quality index readings.", "CN"),
    ("Parcel tracking for Denmark.", "DK"),
    ("Electricity prices for Finland.", "FI"),
    ("Train times across the Netherlands.", "NL"),
    ("Earthquake alerts for Türkiye.", "TR"),
    ("Bin collection days in Ireland.", "IE"),
]


@pytest.mark.parametrize(("text", "country"), STILL_REACHABLE)
def test_dropping_the_ambiguous_term_does_not_disable_the_country(text, country):
    """Every country trimmed above keeps at least one unambiguous term.

    This is the other half of :data:`AMBIGUOUS_ENGLISH_TEXTS`: removing "Swiss"
    or "China" from prose matching is only safe because "Switzerland" and
    "Chinese" still land, so a Swiss railway card is still hidden here.
    """
    verdict = classify(desc(text), home_country="AU")

    assert verdict.hide is True, text
    assert country in {m.country for m in verdict.foreign_matches}


# -- real production strings: exact outcomes ---------------------------------


def test_postnord_sweden_description_is_hidden():
    verdict = classify(
        desc(
            "Track your next scheduled mail delivery from PostNord in "
            "Sweden, simple and easy."
        ),
        home_country="AU",
    )

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"SE"}
    assert verdict.home_matches == ()


def test_usps_description_is_hidden():
    verdict = classify(
        desc(
            "Home Assistant integration providing day of package counts "
            "and USPS informed delivery images."
        ),
        home_country="AU",
    )

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"US"}


def test_au_nz_markets_keeps_it_because_au_is_home():
    verdict = classify(
        desc("Spot price and forecast sensors for AU/NZ electricity markets."),
        home_country="AU",
    )

    assert verdict.hide is False
    assert {m.country for m in verdict.home_matches} == {"AU"}
    assert {m.country for m in verdict.foreign_matches} == {"NZ"}


def test_makera_cnc_has_no_country_matches():
    verdict = classify(
        desc("Home Assistant integration for Makera Z1 CNC machines"),
        home_country="AU",
    )

    assert verdict.hide is False
    assert verdict.home_matches == ()
    assert verdict.foreign_matches == ()


def test_pawsistant_has_no_country_matches():
    verdict = classify(
        desc(
            "Pawsistant is a Home Assistant integration for tracking your "
            "dogs activities as sensors"
        ),
        home_country="AU",
    )

    assert verdict.hide is False
    assert verdict.home_matches == ()
    assert verdict.foreign_matches == ()


def test_comfort_card_has_no_country_matches():
    verdict = classify(
        desc("A sensor-only temperature and humidity comfort card for Home Assistant."),
        home_country="AU",
    )

    assert verdict.hide is False
    assert verdict.home_matches == ()
    assert verdict.foreign_matches == ()


def test_postnord_slug_in_full_name_is_hidden():
    fields = {
        "name": None,
        "full_name": "ticstyle/PostNord-Mail-Delivery",
        "description": None,
        "topics": None,
    }

    verdict = classify(fields, home_country="AU")

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"SE"}
    assert all(m.field == "full_name" for m in verdict.foreign_matches)


def test_nsw_transport_full_name_is_not_hidden():
    fields = {
        "name": None,
        "full_name": "someone/ha-nsw-transport",
        "description": None,
        "topics": None,
    }

    verdict = classify(fields, home_country="AU")

    assert verdict.hide is False


def test_sv_se_topic_is_hidden_as_sweden():
    fields = {
        "name": None,
        "full_name": None,
        "description": None,
        "topics": ["sv-SE", "transit"],
    }

    verdict = classify(fields, home_country="AU")

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"SE"}
    assert all(m.field == "topics" for m in verdict.foreign_matches)


# -- false hides found by running this over a real HACS store -----------------
#
# Every string below is the real ``description``, ``name`` or ``full_name`` of a
# repository that a live run over all 2,240 new repositories in a production
# HACS index hid for the wrong reason. They are the only false positives the
# audit view turned up, and they fall into four mechanical classes: a code
# inside a model number, a code inside a protocol name, a code abutting a
# non-ASCII letter, and a country initial in the *author's* handle.

MODEL_NUMBERS_AND_PROTOCOLS = [
    "Home Assistant integration: FRITZ!Box CallMonitor (TCP/1012) + TR-064 "
    "phonebook name resolution (Digest auth).",
    "Home Assistant custom integration for GenieACS TR-069 router management",
    "Siedle SG-150 integration for HomeAssistant",
    "Home Assistant custom integration for the Renpho ES-CS20M and other "
    "compatible Renpho Bluetooth scales",
    "Reads plain sensors (Ecowitt, WeatherFlow, ESP) and publishes a forecast",
    "Home Assistant custom integration for ESP-EVSE solar surplus charging",
    "HA integration for Fantasy PL",
    "Home Assistant integration for the Pool Lab PL Max Series of controllers.",
    "Home Assistant custom integration for Poolex Silverline FI pool heat "
    "pumps (Tuya v3.3)",
    "Home Assistant integration for FRÄNKISCHE profi-air 140 lite",
]


@pytest.mark.parametrize("text", MODEL_NUMBERS_AND_PROTOCOLS)
def test_model_numbers_and_protocols_are_not_countries(text):
    verdict = classify(desc(text), home_country="AU")

    assert verdict.foreign_matches == (), f"false hide in: {text!r}"
    assert verdict.hide is False


def test_the_authors_handle_is_not_evidence_of_a_country():
    """Only the repository half of ``owner/repo`` is scanned.

    "Jef-NL" is who wrote it; TRMNL is an e-ink display and the integration is
    a generic webhook bridge. A handle is about the author, and handles carry
    nationality constantly -- so scanning the owner half hid a global
    integration with no way for the user to notice.
    """
    fields = {
        "name": None,
        "full_name": "Jef-NL/trmnl-webhook-push",
        "description": None,
        "topics": None,
    }

    verdict = classify(fields, home_country="AU")

    assert verdict.hide is False
    assert verdict.foreign_matches == ()


def test_the_repository_half_is_still_scanned():
    """The flip side: a country named in the repo name must still land."""
    fields = {
        "name": None,
        "full_name": "AllonGit/energy_hub_poland",
        "description": None,
        "topics": None,
    }

    verdict = classify(fields, home_country="AU")

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"PL"}


# -- ... and the true hides from the same run still hide ----------------------

TRUE_HIDES = [
    ("Pollen DK – Home Assistant Integration", "DK"),
    ("Track PPL CZ parcels in Home Assistant", "CZ"),
    ("Track NZ Post parcels in Home Assistant", "NZ"),
    ("Home Assistant integration for Posten & Bring (NO/SE/DK) parcel tracking", "SE"),
    ("A Home Assistant integration for tracking UK rail commutes", "GB"),
    ("Integrate ÖBB Scotty widget into Home Assistant.", "AT"),
    ("A minimalistic, SBB-style departure board for Home Assistant", "CH"),
    ("Home Assistant integration for retrieving Edenred PT card balances", "PT"),
    ("Spanish electricity spot prices from the PVPC tariff.", "ES"),
    ("Integración para España: consumo eléctrico y potencia contratada.", "ES"),
    ("Finnish electricity spot prices, hour by hour.", "FI"),
    ("Polish rail departures and delays.", "PL"),
    ("Parcel tracking for Singapore deliveries.", "SG"),
    ("Earthquake alerts for Türkiye.", "TR"),
]


@pytest.mark.parametrize(("text", "country"), TRUE_HIDES)
def test_real_foreign_repositories_are_still_hidden(text, country):
    """Tightening the short-code rules must not blunt the actual filter.

    Each of the six newly blocklisted codes is represented here by the
    long-form term it falls back to, so a later edit that also removes the
    fallback fails loudly instead of silently showing the user Polish rail.
    """
    verdict = classify(desc(text), home_country="AU")

    assert verdict.hide is True, text
    assert country in {m.country for m in verdict.foreign_matches}


def test_a_hyphenated_country_prefix_without_a_digit_still_matches():
    """``(?![-_]\\w*\\d)`` is deliberately narrow.

    "UK-based", "US-only" and "NL-specific" are how people actually write a
    country qualifier, so the model-number guard only fires when a digit
    follows the hyphen.
    """
    verdict = classify(desc("A UK-based rail departure board."), home_country="AU")

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"GB"}


# -- a demonym in a language list names a translation, not a market ------------
#
# The fifth class the live audit turned up, and the subtlest: a repository that
# lists the languages its UI is translated into reads, word for word, like a
# repository built for those countries. All three strings below are real, and
# all three were hidden until the guard landed.

LANGUAGE_LISTS = [
    # German + Swiss German, 32 characters away from "English" -- the widest
    # real gap, and what sets _LANGUAGE_WINDOW.
    "Lovelace word clock card for Home Assistant - displays the time as "
    "illuminated words in a letter grid. Supports German, Swiss German "
    "(Bärndütsch) and English, with configurable colors.",
    # German and Spanish, both *after* "English": the guard looks both ways.
    "A quiz game for Home Assistant with 5,796 questions in English, German "
    "and Spanish.",
    # Slovak, joined by an ampersand rather than a comma.
    "Animated energy-flow card for SolaX inverters. English & Slovak, visual "
    "editor, HACS ready.",
]


@pytest.mark.parametrize("text", LANGUAGE_LISTS)
def test_a_language_list_beside_english_is_not_a_country(text):
    verdict = classify(desc(text), home_country="AU")

    assert verdict.foreign_matches == (), f"false hide in: {text!r}"
    assert verdict.hide is False


def test_the_guard_does_not_reach_across_a_sentence_boundary():
    """"Norwegian electricity prices" is for Norway however the docs read.

    A full stop between the demonym and "English" means they are making two
    separate statements -- who it is for, and what language it is documented
    in -- so the guard stands down and the hide stands.
    """
    verdict = classify(
        desc("Electricity prices for Norwegian households. Docs in English."),
        home_country="AU",
    )

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"NO"}


def test_the_guard_does_not_reach_beyond_its_window():
    """Far enough apart, the two words are not a list at all.

    Found by measurement: at an 80-character window this exact string -- a
    real one -- lost its "Swedish", because the only punctuation between the
    demonym and "English" is a comma.
    """
    verdict = classify(
        desc(
            "Home Assistant integration for Swedish road traffic events from "
            "the Traffic Information API, documentation in English"
        ),
        home_country="AU",
    )

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"SE"}


def test_only_a_demonym_is_softened_by_the_word_english():
    """A country's *name* beside "English" still hides.

    "Swedish" can be a language; "Sweden" cannot. Narrowing the guard to
    demonyms is what keeps "parcel tracking for Sweden" out of the user's
    way no matter what language its README is in.
    """
    verdict = classify(
        desc("Parcel tracking for Sweden. Available in English and Swedish."),
        home_country="AU",
    )

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"SE"}


def test_a_bare_language_topic_is_still_hidden():
    """The one case the guard cannot reach, recorded on purpose.

    Topics are scanned one at a time, so a lone ``polish`` topic has no
    neighbouring words for a positional rule to read -- this really is a
    generic mojibake fixer, and it really is still hidden. The audit view is
    the remedy for this residue, and widening the guard to cover it would
    mean trusting a bare topic less than a described repository, which is the
    wrong trade.
    """
    fields = {
        "name": None,
        "full_name": "MacSiem/ha-encoding-fixer",
        "description": "Text encoding fixer - find and repair mojibake in entity names",
        "topics": ["polish", "encoding"],
    }

    verdict = classify(fields, home_country="AU")

    assert verdict.hide is True
    assert {m.country for m in verdict.foreign_matches} == {"PL"}


# -- other behaviour ----------------------------------------------------------


def test_classify_on_an_empty_mapping_never_raises():
    verdict = classify({})

    assert verdict.hide is False
    assert verdict.home_matches == ()
    assert verdict.foreign_matches == ()


def test_classify_on_explicit_nones_never_raises():
    verdict = classify({"name": None, "description": None, "topics": None})

    assert verdict.hide is False
    assert verdict.home_matches == ()
    assert verdict.foreign_matches == ()


def test_extra_home_terms_adds_a_home_signal():
    verdict = classify(
        desc("Bunnings stock levels"),
        home_country="AU",
        extra_home_terms=["Bunnings"],
    )

    assert verdict.hide is False
    assert any(m.term == "Bunnings" and m.country == "AU" for m in verdict.home_matches)


def test_extra_home_terms_are_not_home_without_being_passed():
    verdict = classify(desc("Bunnings stock levels"), home_country="AU")

    assert verdict.home_matches == ()


# -- scope_is_foreign ----------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (None, False),
        ("", False),
        ("global", False),
        ("worldwide", False),
        ("any", False),
        ("international", False),
        ("AU", False),
        ("AU,NZ", False),
        ("SE", True),
        ("SE,NO", True),
        ("se", True),
        ("nonsense", False),
        # The AI is asked to *name* the country, so a third of the table comes
        # back as prose rather than a code. Tokenising on whitespace first
        # left every one of these unrecognised, which reads as "no country"
        # and therefore "keep" -- the second hide chance quietly doing nothing.
        ("Sweden", True),
        ("New Zealand", True),
        ("new zealand", True),
        ("United Kingdom", True),
        ("Czech Republic", True),
        ("South Korea", True),
        ("The Netherlands", True),
        ("United States of America", True),
        ("Australia", False),
        # Separated however the model chose to separate them.
        ("AU/NZ", False),
        ("SE/NO", True),
        ("Sweden, Norway", True),
        ("Australia and New Zealand", False),
        ("New Zealand, Sweden", True),
        # Unicode survives the split: "espa" + "a" would match nothing.
        ("España", True),
    ],
)
def test_scope_is_foreign(scope, expected):
    assert scope_is_foreign(scope, home_country="AU") is expected


# -- CountryVerdict.reason -----------------------------------------------------


def test_reason_describes_the_foreign_match_when_hidden():
    verdict = classify(
        desc("Track your next scheduled mail delivery from PostNord in Sweden."),
        home_country="AU",
    )

    assert verdict.hide is True
    assert verdict.reason is not None
    assert "description" in verdict.reason
    assert any(m.term in verdict.reason for m in verdict.foreign_matches)


def test_reason_is_none_when_not_hidden():
    verdict = classify(desc("Weather alerts for Australia."), home_country="AU")

    assert verdict.hide is False
    assert verdict.reason is None


def test_reason_is_none_with_no_matches_at_all():
    verdict = classify(desc("a generic integration"), home_country="AU")

    assert verdict.hide is False
    assert verdict.reason is None


# -- identity / dataclass shape ------------------------------------------------


def test_country_match_and_verdict_are_frozen_dataclasses():
    match = CountryMatch(country="SE", term="Sweden", field="description")
    verdict = CountryVerdict(hide=True, home_matches=(), foreign_matches=(match,))

    with pytest.raises(AttributeError):
        match.country = "AU"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        verdict.hide = False  # type: ignore[misc]
