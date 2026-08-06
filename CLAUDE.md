# Project notes

## The ZIP → county crosswalk (`network_probe/geo/`)

**A ZIP is not a place, and this is load-bearing.** US ZIP codes are USPS *delivery routes*, so they
do not nest inside counties: **10,186 of 33,791 ZCTAs (30%) span more than one county**, and one
spans six. Two different ZIPs also routinely share a county — `30144` (Kennesaw) and `30188`
(Woodstock) are both partly Cobb.

That matters because **Medicare Advantage plans are sold by county of residence**, and on the payer
portals the county picks the plan list. Measured live on the UHC guest portal, ZIP `30101`'s four
counties offer different lists:

| county | plans | only there |
|---|---|---|
| Cherokee | 11 | |
| Cobb | 12 | `GA-D001` |
| Bartow / Paulding | 14 | `GA-2 (PPO)`, `GA-MA01` |

So "which county is this ZIP in" has no single answer, and comparing two ZIP *strings* is neither
necessary nor sufficient for "same county". `geo/zip_county.py` therefore answers in county **sets**,
and `same_county()` is deliberately **three-valued** — `True` / `False` / `None` for "not
established". `None` is the common case (clinic `30144` is Cobb; member `30188` is Cherokee-or-Cobb)
and callers must never collapse it to either answer.

### The dependency

| | |
|---|---|
| Data file | `src/network_probe/geo/zip_counties.csv.gz` (~443 KB, ships as package data) |
| Source | Census Bureau **2020 ZCTA-to-County Relationship File** — free, no API key, no registration |
| Rebuild | `python -m scripts.build_zip_counties` |
| Runtime cost | none — lazily loaded once into a dict, no network, no DB |

It is declared in `pyproject.toml` under `[tool.setuptools.package-data]`. An install that omits it
degrades silently to "county unknown" rather than failing, because this is a refinement on top of a
working portal walk, not something the walk depends on.

### ⚠ Do not swap it for a ZIP library

`pgeocode`, `zipcodes` and `uszipcode` all return **one county per ZIP** — the dominant one. Measured
2026-08-06 against the portal's own dropdown:

| ZIP | portal (truth) | pgeocode / zipcodes |
|---|---|---|
| 30101 | Bartow, Cherokee, Cobb, Paulding | **Cobb only** ✗ |
| 30188 | Cherokee, Cobb | **Cherokee only** ✗ |
| 30144 | Cobb | Cobb ✓ |

They silently discard three of `30101`'s four counties. Cobb is a 12-plan list and Paulding a
14-plan one, so that is a *confident wrong county* — worse than no county at all. The Census file
reproduced the portal exactly on all three, which is why it was chosen.

`test_zip_county.py::test_the_portal_ground_truth_is_reproduced` fails if the data source is ever
swapped for one that collapses ZIPs to a single county, and
`test_the_crosswalk_is_national_and_not_a_truncated_download` catches a partial rebuild.

### County-scoping is per LINE OF BUSINESS — it is not universal

County service areas are a **Medicare** rule, confirmed by CMS: *"Each State County Code (SCC) in a
Plan's service area belongs to only one Segment. This enables MARx to automate the assignment of
Segment IDs based upon the residence SCC of the beneficiary."* Two payers implement it the same way —
UHC Find Care and Aetna's Medicare site (which has an explicit county select, `countyCode 17031 /
Cook County`).

Measured on UHC's guest flow, same multi-county ZIP (30101), 2026-08-06:

| line | county modal? | identifiers on the plan options |
|---|---|---|
| Medicare | **yes** | CMS contract-PBP-segment (`H5322-047-001`) |
| Medicaid | **yes** | none |
| Commercial | no | none (87 network names: Charter, Choice, …) |
| ACA Marketplace | no | non-CMS (`912`) |

So the segment rule applies to **Medicare only**. Commercial networks are contract-scoped, not
county-scoped, and ACA is rated by area but was not gated behind a county here.

⚠ **"No identifier" is NOT "segmented."** `_unsegmented(None)` is False, so a plan with no CMS id
(every Medicaid and commercial plan) once fell through to a message asserting it was county-split —
a claim never established. The three cases are distinct and must stay distinct: unsegmented id →
safe; segmented id → the county decides; **no id → unknown**, and say only that.

### `land_share` is not a decision

Each county carries its share of the ZIP's land area, for ordering and for showing a human which
county dominates. It is **never** used to choose a county: `30188` is 99.1% Cherokee by land, and
picking Cherokee on that basis is still a guess about where one member lives. This layer's whole
doctrine is that a guessed network is the worst failure available — see `portal/plan_match.py`.
