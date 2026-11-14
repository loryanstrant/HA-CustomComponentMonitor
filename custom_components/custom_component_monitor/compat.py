"""Will the offered update actually run on this Home Assistant?

A component's ``hacs.json`` carries a ``homeassistant`` key: the minimum Home
Assistant version it declares. HACS enforces it on *install*, which is exactly
the problem this module exists for: the requirement is real, it is
machine-readable, and nothing shows it to the user before the install fails —
or, as with Microsoft 365 Calendar, before an install that should have been
refused goes ahead and has to be rolled back by hand.

This module only *compares*; whoever calls it decides which copy of
``hacs.json`` to hand over, and that choice is load-bearing. HACS and HA
Marketplace surface one ``repository_manifest`` per repository with no record
of which release it describes, so it answers for whatever version was last
downloaded — not the one on offer. #116 is what that cost: the caller now
reads the offered release's own manifest and falls back to
``REASON_TARGET_UNREADABLE`` rather than believing a stale one.

Three answers, and the third is first-class:

``met``
    The declared minimum is satisfied by the running Home Assistant.
``blocked``
    The declared minimum is higher than the running Home Assistant.
``unknown``
    Nothing usable was declared, or what was declared could not be compared.

"unknown" is never quietly folded into "met". Most components declare nothing
at all, and an update whose requirement could not be read must not look
identical to one that was checked and cleared.
"""
from __future__ import annotations

from typing import Any

from awesomeversion import (
    AwesomeVersion,
    AwesomeVersionException,
    AwesomeVersionStrategy,
)

STATE_MET = "met"
STATE_BLOCKED = "blocked"
STATE_UNKNOWN = "unknown"

#: Why a requirement came back ``unknown``. Carried on the row so the card can
#: say which kind of silence it is rather than one vague "unknown".
REASON_NOT_DECLARED = "not_declared"
REASON_UNREADABLE = "unreadable"
REASON_HA_UNREADABLE = "ha_unreadable"
#: The offered release's own ``hacs.json`` could not be read this scan, so
#: nothing is known about what *it* requires (#116). Distinct from
#: ``not_declared`` on purpose: the component may well declare a minimum, we
#: just could not get at the copy that counts.
REASON_TARGET_UNREADABLE = "target_unreadable"

# A version string has to be comparable to be worth comparing. AwesomeVersion
# accepts a lot: ">=2026.11.0" parses as UNKNOWN and raises on comparison, and
# "latest" parses as a SPECIALCONTAINER tag that compares as newer than every
# real version — which would block every update in the house. Both are treated
# as "declared something we cannot use".
_COMPARABLE_STRATEGIES = frozenset(
    {
        AwesomeVersionStrategy.CALVER,
        AwesomeVersionStrategy.SEMVER,
        AwesomeVersionStrategy.SIMPLEVER,
        AwesomeVersionStrategy.BUILDVER,
        AwesomeVersionStrategy.PEP440,
    }
)


def _comparable(raw: Any) -> AwesomeVersion | None:
    """Parse *raw* into a version that can be ordered, or return ``None``."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        version = AwesomeVersion(raw.strip())
    except AwesomeVersionException:
        return None
    if not version.valid or version.strategy not in _COMPARABLE_STRATEGIES:
        return None
    return version


def declared_min_ha_version(repository_manifest: Any) -> str | None:
    """The ``homeassistant`` key of a repository's ``hacs.json``, verbatim.

    Returned unparsed and untrimmed of meaning: an unusable value is still
    what the component declared, and the card shows it so the user can judge
    it. :func:`ha_requirement` decides whether it can be compared.
    """
    if not isinstance(repository_manifest, dict):
        return None
    raw = repository_manifest.get("homeassistant")
    if not isinstance(raw, str):
        return None
    return raw.strip() or None


def ha_requirement(
    repository_manifest: Any, installed_ha_version: str | None
) -> dict[str, Any]:
    """Compare a repository's declared HA minimum against the running HA.

    Returns ``{"state", "required", "installed", "reason"}``. ``required`` is
    the declared string (or ``None``), ``installed`` the running version, and
    ``reason`` is set only when *state* is ``unknown``.
    """
    required = declared_min_ha_version(repository_manifest)
    installed = (installed_ha_version or "").strip() or None
    result: dict[str, Any] = {
        "state": STATE_UNKNOWN,
        "required": required,
        "installed": installed,
        "reason": None,
    }

    if required is None:
        result["reason"] = REASON_NOT_DECLARED
        return result

    wanted = _comparable(required)
    if wanted is None:
        result["reason"] = REASON_UNREADABLE
        return result

    running = _comparable(installed)
    if running is None:
        result["reason"] = REASON_HA_UNREADABLE
        return result

    try:
        blocked = wanted > running
    except AwesomeVersionException:
        # Two valid versions of incompatible shapes ("2026.11.0" vs "1.4").
        result["reason"] = REASON_UNREADABLE
        return result

    result["state"] = STATE_BLOCKED if blocked else STATE_MET
    return result


def target_unreadable_requirement(installed_ha_version: str | None) -> dict[str, Any]:
    """The answer when the offered release's own ``hacs.json`` could not be read.

    Deliberately shaped like any other ``unknown``: ``required`` is ``None``
    because nothing was read, not because nothing was declared. Merge it over
    a stale answer with :func:`strictest` — that keeps a known ``blocked`` and
    discards a stale ``met``, which is the whole point.
    """
    return {
        "state": STATE_UNKNOWN,
        "required": None,
        "installed": (installed_ha_version or "").strip() or None,
        "reason": REASON_TARGET_UNREADABLE,
    }


def is_blocked(requirement: Any) -> bool:
    """True only for a requirement positively established as unmet."""
    return isinstance(requirement, dict) and requirement.get("state") == STATE_BLOCKED


#: How much each answer restricts installing, most restrictive first. Used when
#: two rows end up claiming the same update entity and disagree.
_SEVERITY = {STATE_BLOCKED: 2, STATE_UNKNOWN: 1, STATE_MET: 0}


def strictest(requirements: Any) -> dict[str, Any] | None:
    """The most restrictive of several requirements, or ``None`` if there are none.

    Two snapshot rows can end up pointing at the same ``update.*`` entity: the
    reconciler matches a row to an entity by repository URL, then by name, then
    by slug, and the name/slug fallbacks are first-match — so two installed
    components whose names normalise the same (a fork beside its original, say)
    can both land on one entity. Before this feature a wrong match only
    mis-attributed release notes; now it also decides which component's
    declared Home Assistant minimum is believed, and "believed the wrong one"
    in the permissive direction would install exactly what this is for.

    So an ambiguous entity is answered with the strictest claim on it:
    ``blocked`` beats ``unknown`` beats ``met``. Over-restricting shows a
    warning the user can read and override; under-restricting breaks their
    Home Assistant.
    """
    best: dict[str, Any] | None = None
    best_rank = -1
    for requirement in requirements or []:
        if not isinstance(requirement, dict):
            continue
        rank = _SEVERITY.get(requirement.get("state"), _SEVERITY[STATE_UNKNOWN])
        if rank > best_rank:
            best, best_rank = requirement, rank
    return best


def requirement_counts(requirements: Any) -> dict[str, int]:
    """Count the ``blocked`` and ``unknown`` rows, for the sensor's scalars.

    One function rather than one expression per caller: the scan computes
    these counts and the verification pass (#116) recomputes them after it has
    corrected the rows, and a count that disagreed with the rows it describes
    would be the same class of bug all over again. Anything that isn't a
    requirement dict counts as ``unknown``, never as fine.
    """
    counts = {"blocked": 0, "unknown_requirement": 0}
    for requirement in requirements or []:
        state = (
            requirement.get("state", STATE_UNKNOWN)
            if isinstance(requirement, dict)
            else STATE_UNKNOWN
        )
        if state == STATE_BLOCKED:
            counts["blocked"] += 1
        elif state == STATE_UNKNOWN:
            counts["unknown_requirement"] += 1
    return counts


def block_sentence(name: str, requirement: dict[str, Any]) -> str:
    """One plain sentence for a refusal, in the user's terms."""
    required = requirement.get("required") or "a newer version"
    installed = requirement.get("installed") or "an older version"
    return (
        f"{name} needs Home Assistant {required}; this system runs {installed}. "
        "Update Home Assistant first, then install this component update."
    )
