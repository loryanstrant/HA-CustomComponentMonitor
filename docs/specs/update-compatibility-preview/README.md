# Design preview — Home Assistant compatibility filter

`index.html` + `styles.css` are a static mock, rendered to `desktop.png` and
`380.png` at the sizes `.agent/screens.yaml` declares. Not product code; the
card's own `_getStyles()` is copied verbatim into the stylesheet so the mock
reads the way the real card does.

Approved behaviour this mock settles, for whoever implements it:

## The three states, on every row

Each update carries exactly one: **● met**, **◐ blocked**, **○ requirement
unknown**. No row is ever silent. A row with no line would make absence mean
"compatible", and an update whose requirement could not be read must never be
indistinguishable from one that was checked and cleared.

The marks are **drawn in CSS** (a bordered circle with a full, half or empty
background), not typed as `●◐○`. Measured in this preview's own render, the
half-filled character U+25D0 came out about a third the size of the other two
and read as a stray wedge — which destroys the one distinction a colour-blind
reader relies on (house rule 9).

## The bulk-update button names its own scope

Today `_doRender()` emits `Update all (entities.length)` or, when a type or
category filter is on, `Update selected (shown.length)`. Note that the
unfiltered branch counts **all** entities rather than the visible ones — once
compatibility hiding exists that would read "Update all (6)" above a four-row
list, which is worse than the mismatch this rule fixes.

Let `visible` be the rows currently listed and `eligible` the subset the bulk
service will actually act on (requirement met; blocked and unknown are both
excluded).

| Case | Label |
|---|---|
| `eligible == visible` | unchanged — `Update all (N)` / `Update selected (N)` |
| `eligible < visible` | `Update N of M` |
| `eligible == 0` | `Update 0 of M`, disabled |

The line beside the button says *why* the others are out
(`◐ 2 blocked · ○ 2 requirement unknown`); the button says *how many*. The
confirmation dialog in `_handleUpdateAll()` must quote the same `eligible`
count, not `targets.length` as captured today.

Revealing incompatible rows never grows the eligible set.

## Blocked versus unknown are different refusals

**Blocked** (declared minimum not met): no Update button is rendered at all —
not a disabled one. Skip remains.

**Unknown**: the row stays listed and is never labelled compatible. Install is
available only after an explicit per-row acknowledgement, which is forgotten
when the row closes so it cannot carry over.
