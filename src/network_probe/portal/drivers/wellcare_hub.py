"""Wellcare Find a Provider — Centene's public hub (my.wellcare.com) plus its plan-agnostic lookup.

Covers the Ins Test 3 "Wellcare" row (Wellcare Medicare Advantage, GA). Centene runs the same
component library for Ambetter, Health Net and Fidelis, so every selector here is a `data-testid`
from that shared library rather than a Wellcare-specific class — the only Wellcare-specific things
are the two hostnames and the `medicare` line segment in the hub path.

Observed live 2026-07-28. The portal exposes TWO surfaces, and the driver uses both because each
answers half the question:

  LEG A — the plan-pinned hub (mandatory, plan-first):
    https://www.wellcarefindaprovider.com/                        301 -> my.wellcare.com hub landing
    landing card "I Need a Provider" -> "Search for a Provider"   href = /x/findaprovider/medicare/en/default/location
    /…/en/default/location    "Enter Your Plan Location"  #autocomplete-input (city/county/ZIP) -> option -> Continue
    /…/en/ga/product?…        "Choose a Plan"             12 radios in [data-testid=product-radio] -> Continue
    /…/en/ga/search/<prodId>  "Find a Provider"           plan banner names the pinned product + place + year
    /…/en/ga/results/<prodId>?…filters-npiFilter=<npi>     "N Result(s) found within 50 miles; M providers in-network"

  LEG B — the plan-AGNOSTIC Provider Lookup (linked from the landing card "I Have a Provider"):
    https://www.wellcarefindaprovider.com/navigate-a-network.html#providerlookup
    provider name-or-NPI + a Google-Places location -> "Showing 1-1 of 1 Results" cards
    (`.card-result-notmobile`) that print "NPI: <npi>" and "Provider is in-network with N plans";
    "View All In-Network Plans" then lists every product by name ("Wellcare Giveback (HMO-POS) - H1112", …).

Why both: leg A is the only surface that can be pinned to one product (doctrine: absence is only
evidence inside a confirmed network), but the 271 plan string for this row is the product *line*
("Wellcare Medicare Advantage (GA)"), not one of the 12 products GA offers — so a leg-A absence
alone cannot rule out the other products. Leg B helps in the *positive* direction: when it finds the
provider it names every product they are in-network for, which can resolve a line-level plan string.

WHAT LEG B IS SCOPED TO, MEASURED RATHER THAN INFERRED (2026-07-28). The UI says it checks "if your
provider is in-network" and its no-results copy says "may not be in-network with any of our plans",
which reads as a search of every Wellcare product. It is not. The request it actually issues is

    POST https://external-api.search.my.centene.com/pces/query?index=CEL
    … {"values": {"field": "network.product.networkId",
                  "values": ["15294","14126","13191","13269","13193","13267"]}} …
      + a geoFilter on the committed location with radius 50mi, and the NPI as
        nationalProviderIdentifiers.number

i.e. it is scoped to a FIXED SET OF SIX networkIds. Those ids are not the pinned product (leg B never
receives one — the page's own analytics payload carries planId="", planProduct="", planProductId="",
only brand=wellcare/state=ga/product=medicare), but neither are they "all Wellcare products": nothing
observable on the page maps those six networks to the 12 products the GA market sells, and the
companion /productmapping/v2/v2/query call answered {"networks":[]}. So a leg-B zero is an absence
from six networks of unknown extent.

CONSEQUENCE, and it is the whole reason this file is shaped the way it is: a leg-B zero is NOT a
verdict. It is recorded, screenshotted and reported as corroboration, and the only OUT_OF_NETWORK
this driver will produce is one scoped to a product the plan IDENTIFIER confirmed (leg A's
pinned-product absence, or leg B finding the provider and its complete product list not naming the
member's product). An earlier cut made the leg-B zero decisive on the strength of the UI's wording;
the request body above is why it no longer does. To lift that restriction, someone must establish
which products those networkIds are — e.g. by reading the networkId leg A's own results request
carries for a product whose identifier the 271 confirms, and checking it is in the six.

Traps that cost a live run here (all still live in the code as guards):

  * The typeahead's provider-NAME suggestions are NOT plan-scoped. Typing "Manayan" on a GA HMO-POS
    product offered `autocomplete-option-name-REX C MANAYAN` (an NPPES-Ontario-CA physician) while
    the plan-scoped results page for the same term returned only MANAN B SHAH. So — unlike UHC Find
    Care, where the suggestion list *is* the result set — here a suggestion proves nothing. Only the
    /results/ page counts, which is why every search is verified to have reached one.
  * Clicking the "Search" button alone never searches; the query is committed by clicking one of the
    intent options (`autocomplete-option-npi-…` / `-searchAll-…`), and that click occasionally
    no-ops while the debounce re-renders the list — which looks exactly like "no results". Hence
    type-with-delay, then wait_for_url("**/results/**"), then one retry, and reached=False if not.
  * The plan-agnostic lookup's location box is raw Google Places: typing "30144" offered
    "30144 60th Ave S, Auburn, WA" FIRST. Clicking `.first` searched Washington and returned 0
    results — a fabricated OON. Places are now scored against the clinic's city/state/ZIP, and the
    location the portal echoes back is re-checked before any absence is believed.
  * Two of the 12 products are PDP (Part D drug-only) and have no medical provider network at all;
    they are never pinned.
  * The Continue button is `[data-testid=button]` on the location step but
    `[data-testid=primary-action-button]` on the product step — clicked by accessible name instead.

FIVE DEFECTS AN ADVERSARIAL REVIEW PROVED IN THE FIRST CUT OF THIS DRIVER, and what replaced them.
They are all one family: a claim in the note that the code had not actually established.

  1. THE CONTROL WAS THE WRONG QUERY TYPE. The decisive leg-B OON rested on an *NPI* query returning
     0, but the liveness control that licensed believing that zero was a *surname* query. A surname
     control cannot tell "this provider is absent" from "this field does not index NPIs at all" —
     under the second reading every NPI in existence returns 0 and every provider looks OON. The
     control is now an NPI query for a control NPI the portal itself printed on one of its own
     cards, and it must come back carrying that NPI. Verified live 2026-07-28: NPI 1457548513
     (MANAN B SHAH, read off the surname control's card) returned that provider at the same
     location and radius, which is what proves the field is an NPI index. No control NPI available
     -> the zero is not evidence -> UNKNOWN.
  2. THE CONTROL'S OWN RESULTS WERE NEVER LOOKED AT. `_lb_research` returned a bare count, so a
     control search that had returned OUR OWN provider still counted as proof the index was live,
     and the code then concluded our provider was absent from it — a note contradicting itself. The
     control now returns its cards; if this provider is among them (by printed NPI or by name
     identity) the two readings contradict and the answer is UNKNOWN, and if the control set was
     truncated it cannot be said not to contain us either.
  3. AN NPI-FILTERED COUNT WAS READ AS A GENERAL RESULT SET. `a_populated and confirmed` also fired
     while `a` was still the exact-NPI-filter search, so ">0 results for this NPI" — the portal
     saying our provider IS listed — could produce an OUT_OF_NETWORK whose note claimed those
     results were for the surname. The pinned-plan OON now requires the search to have been the
     general `searchAll` one (`kind`), the set to be fully read (not truncated), the cards to have
     been inspected, and the plan-scoped set to contain in-network providers at all.
  4. THE SURNAME CARDS WERE DISCARDED WITHOUT INSPECTION. The second hub search overwrote `a` with
     `found=False, found_name=None` unconditionally, and the note then asserted "N result(s) for
     <surname>, none this NPI" — a claim about cards nothing had read. Both legs now run `_identity`
     over the cards they actually read and carry the outcome ("no card matched" / "ambiguous" /
     the matched card) into the verdict and the note.
  5. PLAN MATCHING WAS LOCAL, AND "COVERED" WAS A NORMALISED STRING EQUALITY. Product pinning now
     goes through the shared, tested `plan_match.match_plan`, and only `PlanMatch.confirms_network`
     (an identifier match — contract/PBP/market code) may license an OON; a token-only match pins
     the search and nothing more. "Is the pinned product in the provider's in-network plan list"
     is likewise an identifier/token comparison, because `_norm("… (HMO-POS)") == _norm("… (HMO-POS)
     - H1112")` is False and that inequality alone used to produce an OUT_OF_NETWORK.
"""

from __future__ import annotations

import re

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal import plan_match
from network_probe.portal.drivers.base import PortalDriver
from network_probe.portal.models import PortalCapture, PortalQuery, PortalStatus

ENTRY = "https://www.wellcarefindaprovider.com/"
HUB = "https://my.wellcare.com"
LOOKUP = "https://www.wellcarefindaprovider.com/navigate-a-network.html#providerlookup"

# Only "medicare" is verified live: Wellcare is Centene's Medicare brand (Ambetter = Marketplace,
# Fidelis/Health Net = Medicaid, each its own deployment of this same hub). The landing page's own
# link is preferred over this constant precisely so a segment rename cannot break the walk.
_HUB_LINE = "medicare"

# The lines this Wellcare deployment sells. A plan from any other line is NOT in leg B's universe, so
# a leg-B zero says nothing about it — see `_line_ok`.
_HUB_LINES_OF_BUSINESS = ("medicare", "dual")

# --- leg A (hub) selectors ------------------------------------------------------------------------
_LANDING_SEARCH_LINK = "a[href*='/findaprovider/'][href*='/location']"
_PLACE_INPUT = "#autocomplete-input"  # hub location step; restricted to cities/counties/ZIPs
_PLACE_OPTION = "[role=option]"
_PRODUCT_GROUP = "[data-testid='product-radio']"
_PRODUCT_LABELS = "[data-testid='product-radio'] label"
_SEARCH_BOX = "[data-testid='search-providers-autocomplete'] input"
_PLAN_PANELS = ("[data-testid='search-page']", "[data-testid='result-page']")
_RESULT_CARDS = "[data-testid='vertical-card']"
# `vertical-card` is also the filter rail and the two sidebar promos — a card carrying any of these
# phrases is page chrome, not a provider.
_CHROME_CARDS = ("Skip to Results", "Use your member information", "Telehealth Options", "Provider Tier")

# --- leg B (plan-agnostic Provider Lookup) selectors ---------------------------------------------
_LB_PROVIDER_INPUT = "[data-testid='text-field']"
_LB_PROVIDER_INPUT_AGAIN = "[data-testid='provider-lookup-search']"  # same field on the results view
_LB_LOCATION_INPUT = "[data-testid='location-search-box']"
_LB_SEARCH_BUTTON = "[data-testid='provider-lookup-search-button']"
_LB_SEARCH_AGAIN = "[data-testid='search-results-search']"
_LB_COUNTER = "[data-testid='results-counter']"
_LB_LOCATION_ECHO = "[data-testid='user-location']"
_LB_PANEL = "[data-testid='search-results-notmobile']"
_LB_PLANS_LINK = "[data-testid='search-results-view-in-network-plan-link']"
_LB_PLAN_ROWS = "[data-testid='title-cell']"
_LB_PLANS_HEADING = "[data-testid='in-network-plans-h3']"
# The one result card of this view carries no testid; `card-result-notmobile` is its own (non-hashed,
# so not emotion-generated) class, verified by walking up from the "NPI: …" text node on 2026-07-28.
# Each card holds exactly one provider: name line, specialty, "NPI: <10 digits>", address, distance.
_LB_CARDS = (".card-result-notmobile", ".card-result-mobile", "[data-testid='search-results-card']")

_COUNT_IN_RADIUS = re.compile(r"([\d,]+)\s+Results?\s+found", re.I)
_COUNT_IN_NETWORK = re.compile(r"([\d,]+)\s+providers?\s+in-network", re.I)
_NO_RESULTS = re.compile(r"no results found|no providers match your search", re.I)
_LB_COUNT = (re.compile(r"of\s+([\d,]+)\s+Results?", re.I), re.compile(r"Showing\s+([\d,]+)\s+Results?", re.I))
_NPI_ON_CARD = re.compile(r"NPI[:#\s]*(\d{10})", re.I)

# Every NEGATIVE phrasing must be enumerated, and tested BEFORE the positive one, because the
# positive test is a substring check and "not in network" CONTAINS "in network". Two sibling drivers
# returned a false IN over the payer's own contrary badge for exactly this reason.
_BADGE_NEGATIVE = (
    "out-of-network", "out of network", "outofnetwork", "not in network", "not in-network",
    "not-in-network", "non-network", "nonnetwork", "nonparticipating", "non-participating",
    "not participating", "no longer in network", "no longer in-network", "not accepting",
)
_BADGE_POSITIVE = ("in-network", "in network")

# Post-nominals and honorifics are not first names: "REX C MANAYAN, MD" must not have "MD" mistaken
# for a name token when deciding whether a card carries a *different* first name than ours.
_CREDENTIALS = frozenset({
    "MD", "DO", "NP", "PA", "PAC", "DDS", "DMD", "DPM", "DC", "OD", "PHD", "PSYD", "APRN", "ARNP",
    "CRNP", "CNP", "CNM", "RN", "LCSW", "MBBS", "FACS", "FACP", "MPH", "MS", "MSN", "JR", "SR",
    "II", "III", "IV", "DR", "PROF",
})


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())


def _place_key(s: str | None) -> str:
    """Place-autocomplete text has no reliable separators (innerText of two spans renders
    "Kennesaw" + "GA, USA" as "KennesawGA, USA"), so compare on letters+digits only."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _first_line(s: str | None) -> str | None:
    """The first non-empty line — for a result card that is the provider's NAME line, and for a
    product radio it is the product name without the marketing copy underneath."""
    for line in (s or "").splitlines():
        if line.strip():
            return line.strip()[:120]
    return None


def _name_tokens(s: str | None) -> set[str]:
    """Alphabetic name tokens, upper-cased, single letters KEPT so an initial can be reasoned about.

    Matching must be per whole token, never substring: this portal's own typeahead answered the
    surname "Manayan" with "MANAN B SHAH" and with "REX C MANAYAN", so a substring test would call a
    stranger our provider and manufacture a false IN.
    """
    return {t for t in re.split(r"[^A-Za-z]+", (s or "").upper()) if t}


def _leg_a_blank() -> dict:
    """Leg A's evidence record. Defined once so the driver and its tests cannot drift apart, and so
    every consumer reads the same keys whether or not the leg ran."""
    return {
        "reached": False,       # a /results/ URL carrying this very term was actually loaded
        "kind": None,           # the portal's own search intent: "npi" (exact filter) | "searchAll"
        "term": None,
        "in_radius": None,      # "N Result(s) found within 50 miles"
        "in_network": None,     # "M providers in-network" — 0 means the set proves no network at all
        "cards": [],
        "cards_read": 0,
        "truncated": False,     # fewer cards read than the portal counted -> absence is unproven
        "card": None,           # the card identified as THIS provider, if any
        "identity": "not run",  # why: "surname + first name" | "no card matched" | "ambiguous" | …
        "badge": None,          # the portal's own network label on that card: "in" | "out" | None
        "npi_filter_zero": False,  # the exact-NPI filter for this plan returned 0 before we widened
    }


def _leg_b_blank() -> dict:
    """Leg B's evidence record (the plan-agnostic Provider Lookup)."""
    return {
        "reached": False,
        "count": None,             # results for OUR NPI at the clinic's location
        "npi_found": False,        # the portal printed our exact NPI on a card
        "matched_name": None,
        "name_agrees": None,       # does that card's name identify as our provider too?
        "plans": [],               # the products it says this provider is in-network for
        "plan_count": None,
        "plans_complete": False,   # every product the heading counted was actually read
        "liveness": None,          # results for the surname control, same location + radius
        "liveness_cards_read": 0,
        "liveness_complete": False,
        "liveness_has_us": False,  # the control returned this very provider -> self-contradiction
        "liveness_ambiguous": False,
        "liveness_match": None,
        "control_npi": None,       # an NPI the portal itself printed, used to prove it indexes NPIs
        "npi_index_proven": False,
        "location_ok": False,
        "location_echo": None,
        "line_ok": False,          # the member's line of business is one this deployment sells
        "detail": "not run",
        "npi_shot": None,
    }


class WellcareHubDriver(PortalDriver):
    key = "wellcare-hub"
    portal_name = "Wellcare Find a Provider (Centene hub)"

    def capture(self, page: Page, q: PortalQuery, shot) -> PortalCapture:
        trail: list[str] = []

        def result(status: PortalStatus, note: str, **kw) -> PortalCapture:
            # The trail is mandatory: a walk that stopped early and then claimed OON is the failure
            # mode this whole layer exists to prevent, so every verdict carries its own provenance.
            if trail:
                note = f"{note} [portal walk: {' → '.join(trail)}]"
            return PortalCapture(
                payer_key=q.payer_key, npi=q.npi, plan=q.plan, tin=q.tin, status=status,
                portal_name=self.portal_name, portal_url=page.url, driver=self.key, note=note, **kw
            )

        pinned, confirmed, banner, offered = self._walk_to_plan(page, q, trail)
        shot("wellcare-plan-walk")

        a = self._leg_a(page, q, pinned, trail, shot)

        # Leg B costs the portal three more lookups, so it is skipped only when leg A has already
        # answered about the member's OWN product — i.e. the plan was identifier-confirmed. Any
        # weaker pinning and leg B is the only thing that can resolve a line-level plan string.
        if self._leg_a_settles(confirmed, a):
            b = {**_leg_b_blank(),
                 "detail": "not run — the identifier-confirmed pinned product already answered"}
            trail.append("all-plans lookup not needed (plan confirmed by identifier)")
        else:
            b = self._lookup_all_plans(page, q, trail, shot)

        return self._verdict(result, shot, q, pinned, confirmed, banner, offered, a, b)

    # --- verdict ------------------------------------------------------------------------------------

    def _verdict(self, result, shot, q: PortalQuery, pinned, confirmed, banner, offered, a, b) -> PortalCapture:
        """Fuse the plan-pinned leg and the all-plans leg. Every branch names the evidence it used.

        Pure: it reads only the two evidence dicts, which is what makes each branch unit-testable
        offline (tests/test_wellcare_hub.py) rather than only reachable through a live portal.
        """
        plan_txt = _first_line(pinned) or (q.plan or "the member's plan")
        # The banner is the portal's own words for what it searched (product — place — plan year),
        # so it is what the note quotes when describing the plan-pinned leg.
        pinned_desc = banner or plan_txt
        a_name = _first_line(a.get("card"))
        a_count = a.get("in_radius") or 0
        a_populated = bool(a.get("reached")) and a_count > 0

        # --- leg A contradicting itself. The exact NPI filter for this plan returned nothing, yet a
        # card in the widened name search identifies as this provider. One of the two readings is
        # wrong and this driver cannot say which, so it says neither.
        if a.get("card") and a.get("npi_filter_zero"):
            return result(
                PortalStatus.UNKNOWN,
                f"Wellcare's plan-pinned directory disagrees with itself for NPI {q.npi}: its exact "
                f"NPI filter under {pinned_desc} returned 0 results, but the widened "
                f"{a.get('term')!r} search returned a card this driver identifies as the same "
                f"provider ({a_name!r}, matched on {a.get('identity')}). Either the NPI filter is "
                f"not indexing this provider or the card is a namesake — neither an in- nor an "
                f"out-of-network claim survives that.",
                result_count=a_count, matched_name=a_name,
                screenshot=shot("wellcare-legs-disagree"),
            )

        # --- leg A, the portal's own exact NPI filter returned a card we identify as this provider.
        if a.get("card") and a.get("kind") == "npi" and confirmed:
            if a.get("badge") == "out":
                return result(
                    PortalStatus.OUT_OF_NETWORK,
                    f"Wellcare's directory for {pinned_desc} returned NPI {q.npi} ({a_name!r}) and "
                    f"labels that card as NOT in-network. The payer's own label on its own card "
                    f"outranks the provider merely being listed.",
                    result_count=a_count, matched_name=a_name,
                    screenshot=shot("wellcare-oon-badge"),
                )
            return result(
                PortalStatus.IN_NETWORK,
                f"Wellcare's plan-pinned directory returned NPI {q.npi} for plan {pinned_desc} near "
                f"{q.zip_code or 'the clinic'} — listed as {a_name!r}. The query was the portal's own "
                f"exact NPI filter and the card was identified by {a.get('identity')}, so this is the "
                f"portal's identity claim, not a loose name match.",
                result_count=a_count, matched_name=a_name,
                screenshot=shot("wellcare-in-network"),
            )

        if b["npi_found"]:
            plans = b["plans"]
            listed = "; ".join(plans[:12]) or f"{b['plan_count'] or '?'} plan(s)"
            # A line-level plan string still resolves when the provider is in-network for EVERY
            # medical product this market sells: whichever of them the member holds, they are covered.
            # PDP products are excluded — Part D is drug-only and has no provider network.
            market = [t for t in offered if "(PDP)" not in t.upper()]
            uncovered = [t for t in market if not self._plan_listed(plans, t)]
            if not confirmed and market and not uncovered and b["plans_complete"]:
                return result(
                    PortalStatus.IN_NETWORK,
                    f"NPI {q.npi} ({b['matched_name']}) is in-network for ALL {len(market)} medical "
                    f"products Wellcare sells in this market ({listed}). The plan string {q.plan!r} "
                    f"names the product line rather than one product, but every product in that line "
                    f"covers this provider, so the member's own product does too.",
                    result_count=b["count"], matched_name=b["matched_name"],
                    screenshot=shot("wellcare-in-network-all-products"),
                )
            if confirmed and pinned:
                if self._plan_listed(plans, pinned):
                    return result(
                        PortalStatus.IN_NETWORK,
                        f"Wellcare's plan-agnostic Provider Lookup found NPI {q.npi} "
                        f"({b['matched_name']}) and lists {plan_txt} among the products they are "
                        f"in-network for ({listed}).",
                        result_count=b["count"], matched_name=b["matched_name"],
                        screenshot=shot("wellcare-in-network-plans"),
                    )
                if b["plans_complete"]:
                    return result(
                        PortalStatus.OUT_OF_NETWORK,
                        f"NPI {q.npi} ({b['matched_name']}) is a Wellcare network provider near "
                        f"{b['location_echo'] or 'the clinic'} but {plan_txt} is NOT among the "
                        f"{b['plan_count']} products they are in-network for ({listed}) — "
                        f"out-of-network for this member's plan.",
                        result_count=b["count"], matched_name=b["matched_name"],
                        screenshot=shot("wellcare-oon-other-plans"),
                    )
                return result(
                    PortalStatus.UNKNOWN,
                    f"NPI {q.npi} ({b['matched_name']}) is a Wellcare network provider near "
                    f"{b['location_echo'] or 'the clinic'} and {plan_txt} is not among the "
                    f"{len(plans)} product(s) this driver could read, but the portal's own heading "
                    f"counts {b['plan_count']} — the list was not read in full, so the member's "
                    f"product cannot be ruled out of it.",
                    result_count=b["count"], matched_name=b["matched_name"],
                    screenshot=shot("wellcare-plans-truncated"),
                )
            return result(
                PortalStatus.UNKNOWN,
                f"NPI {q.npi} ({b['matched_name']}) is in-network for {b['plan_count']} Wellcare "
                f"product(s) near {b['location_echo'] or 'the clinic'} ({listed}), but the plan string "
                f"{q.plan!r} does not name one of the products this market sells and "
                + (f"{len(uncovered)} of them are NOT covered ({'; '.join(uncovered[:6])})"
                   if uncovered else "the market's product list could not be read in full")
                + ", so we cannot say whether the member's own product is among them.",
                result_count=b["count"], matched_name=b["matched_name"],
                screenshot=shot("wellcare-plans-unresolved"),
            )

        if a.get("card"):
            # Leg A matched inside a product that is only representative of a line-level plan string,
            # and leg B (which would have named every product covering this provider) did not resolve.
            # Real presence, unresolved product: reportable, but not a verdict about the member's plan.
            badge = a.get("badge")
            listed_as = ("IS listed for" if badge != "out"
                         else "is listed, but labelled NOT in-network for,")
            return result(
                PortalStatus.UNKNOWN,
                f"NPI {q.npi} {listed_as} {pinned_desc} — the portal's exact NPI filter returned "
                f"{a_name!r} (identified by {a.get('identity')}). But {q.plan!r} names the product "
                f"line rather than one of the {len(offered)} products this market sells, and the "
                f"all-plans lookup that would have listed every product covering this provider was "
                f"inconclusive ({b['detail']}), so the member's own product is not confirmed.",
                result_count=a_count, matched_name=a_name,
                screenshot=shot("wellcare-in-representative-product"),
            )

        if self._leg_a_absence_is_evidence(a) and confirmed and pinned:
            return result(
                PortalStatus.OUT_OF_NETWORK,
                f"Wellcare's directory for {pinned_desc} returned {a_count} result(s) for "
                f"{a.get('term')!r} near {q.zip_code or 'the clinic'}; all {a.get('cards_read')} "
                f"cards were read and none is NPI {q.npi} ({a.get('identity')}) — out-of-network for "
                f"this plan. The same page reports {a.get('in_network')} provider(s) in-network for "
                f"this plan within the radius, so the pinned network is populated here and this is an "
                f"absence from a working directory, not an empty one. (The portal's exact NPI filter "
                f"for this plan also returned 0, which "
                f"corroborates but cannot stand alone: an unindexed NPI would look identical. The "
                f"evidence is the populated, fully-read, identity-checked result set.) The verdict is "
                f"scoped to this product, which is the one the plan identifier confirmed."
                + self._lookup_note(q, b),
                result_count=a_count, screenshot=shot("wellcare-oon-pinned-plan"),
            )

        if self._lookup_absence_is_real(b):
            # A real absence — but from the six networkIds leg B's own request is scoped to (see the
            # module docstring), NOT from every Wellcare product. Nothing observable maps those
            # networks to the products this market sells, so this is reported in full and answered
            # UNKNOWN. It was decisive in an earlier cut on the strength of the portal's "any of our
            # plans" copy; the request body says less than the copy does.
            return result(
                PortalStatus.UNKNOWN,
                f"Wellcare's Provider Lookup returned 0 results for NPI {q.npi} within 50 miles of "
                f"{b['location_echo'] or 'the clinic'} and states the provider \"may not be "
                f"in-network with any of our plans\", and that zero is a real absence rather than a "
                f"failed search: the same field, at the same location and radius, returned the "
                f"provider it prints for NPI {b['control_npi']} (so it is a live NPI index), and the "
                f"{q.provider_last_name!r} control returned {b['liveness']} result(s), all "
                f"{b['liveness_cards_read']} of them read, none of them this provider. What it is an "
                f"absence FROM is the limit: that lookup's own request scopes itself to a fixed set "
                f"of six network ids, and nothing on the page maps those networks to the "
                f"{len(offered) or 'the'} products this market sells, so it cannot be turned into a "
                f"statement about {q.plan!r}"
                + (f", which pinned only {_first_line(pinned)!r} as a representative product."
                   if pinned and not confirmed else ".")
                + self._pinned_note(q, a, pinned_desc, a_populated),
                result_count=0, screenshot=b.get("npi_shot") or shot("wellcare-absent-in-lookup-scope"),
            )

        if not b["reached"] and not pinned and not offered:
            # Neither Wellcare surface served anything usable. That is a *transport* outcome, not a
            # network verdict: it must never collapse into OON, and it is what tells the caller to
            # fall back to Centene's public FHIR directory for this payer.
            return result(
                PortalStatus.BLOCKED,
                f"Neither Wellcare surface answered for NPI {q.npi}: the plan-first hub did not reach "
                f"its product list and the all-plans Provider Lookup did not load ({b['detail']}). No "
                f"network conclusion can be drawn from a portal that did not serve.",
                screenshot=shot("wellcare-blocked"),
            )

        return result(
            PortalStatus.UNKNOWN,
            f"No honest verdict for NPI {q.npi} at Wellcare: "
            + "; ".join(self._why_unknown(q, pinned, confirmed, a, b)) + ".",
            result_count=a.get("in_radius") if a.get("reached") else None,
            screenshot=shot("wellcare-unknown"),
        )

    def _why_unknown(self, q: PortalQuery, pinned, confirmed, a, b) -> list[str]:
        """Every reason the answer is UNKNOWN, in the order the evidence was gathered. This is the
        note's whole content in that branch, so it must enumerate what actually happened."""
        why: list[str] = []
        if not pinned:
            why.append("no Wellcare product could be pinned, so nothing scoped the directory")
        elif not confirmed:
            why.append(
                f"the plan string {q.plan!r} carries no plan identifier that matches one of the "
                f"products this market sells, so {_first_line(pinned)!r} was pinned only as a "
                f"representative product of that line and an absence from it is not an absence from "
                f"the member's own product"
            )
        if a.get("reached") and (a.get("in_radius") or 0) == 0:
            why.append(f"the plan-pinned {a.get('kind')} search returned an empty set, which cannot "
                       f"distinguish out-of-network from a failed search")
        elif a.get("kind") == "npi" and (a.get("in_radius") or 0) > 0 and not a.get("card"):
            why.append(
                f"the plan-pinned exact-NPI filter returned {a.get('in_radius')} result(s) but none "
                f"of the {a.get('cards_read')} cards read could be identified as this provider "
                f"({a.get('identity')}) — a filtered hit nobody could identify is neither presence "
                f"nor absence"
            )
        elif a.get("identity") == "ambiguous":
            why.append("a plan-pinned card carries only an initial where our first name should be, "
                       "so it can decide nothing in either direction")
        elif a.get("truncated"):
            why.append(f"the plan-pinned search counted {a.get('in_radius')} result(s) but only "
                       f"{a.get('cards_read')} card(s) were on the page, so the set is truncated and "
                       f"this provider's absence from it is unproven")
        elif not a.get("reached") and pinned:
            why.append("the plan-pinned search never reached a results page")
        why.append(f"the all-plans Provider Lookup was inconclusive ({b['detail']})")
        return why

    def _pinned_note(self, q: PortalQuery, a: dict, pinned_desc: str, a_populated: bool) -> str:
        """What the plan-pinned leg contributes to the all-plans OON note — and NOTHING more than it
        established. The first cut asserted "N result(s) for <surname>, none this NPI" while the
        cards behind that N had never been read; every clause here names its own evidence."""
        if not a.get("reached"):
            return " (The plan-pinned leg did not complete; this rests on the all-plans leg alone.)"
        if a_populated and a.get("kind") == "searchAll":
            read = (f"all {a.get('cards_read')} of its cards were read and none is this provider "
                    f"({a.get('identity')})" if not a.get("truncated")
                    else f"only {a.get('cards_read')} of them were on the page, so that set was not "
                         f"read in full and contributes nothing")
            return (f" The plan-pinned search under {pinned_desc} agreed: 0 results for the exact NPI "
                    f"filter, and {a.get('in_radius')} result(s) for {a.get('term')!r} of which {read}.")
        if a_populated:
            return (f" The plan-pinned exact-NPI filter under {pinned_desc} returned "
                    f"{a.get('in_radius')} result(s), which this driver could not identify as this "
                    f"provider ({a.get('identity')}) — reported, not relied on.")
        return f" The plan-pinned search under {pinned_desc} agreed: 0 results for the exact NPI filter."

    def _lookup_note(self, q: PortalQuery, b: dict) -> str:
        """What leg B contributes to a verdict that leg A already carried — corroboration only, and
        described as an absence from the networks leg B searched rather than from "all plans"."""
        if not self._lookup_absence_is_real(b):
            return ""
        return (f" The plan-agnostic Provider Lookup agrees within its own scope: 0 results for NPI "
                f"{q.npi} at {b['location_echo'] or 'the clinic'} on a field proven to index NPIs "
                f"(control NPI {b['control_npi']} came back), though that lookup searches a fixed set "
                f"of six network ids rather than the member's product, so it corroborates rather than "
                f"decides.")

    def _leg_a_absence_is_evidence(self, a: dict) -> bool:
        """Is leg A's empty-of-us result set strong enough to be an absence?

        Every clause is a defect that was live in the first cut:
          * `kind == "searchAll"` — an exact-NPI-FILTER count of >0 was being read as a populated
            general result set, so the portal saying "this NPI IS listed" could produce an OON.
          * `not truncated` + `cards_read` — the portal counts results but paginates them; absence
            from page 1 of N is not absence, and this layer never pages (ToS).
          * `card is None and identity != "ambiguous"` — the cards must have been inspected and have
            come back neither ours nor undecidable.
          * `in_network > 0` — the page must report in-network providers for this plan within the
            radius, which proves the pinned network is populated here rather than empty. Note what
            this is NOT: the figure is the page's own "M providers in-network" line, not a property
            of the returned cards (live 2026-07-28 the hub printed "1 Result(s) found" alongside "60
            providers in-network"), so it may only be claimed as directory liveness for the plan.
        """
        return bool(
            a.get("kind") == "searchAll"
            and a.get("reached")
            and (a.get("in_radius") or 0) > 0
            and not a.get("truncated")
            and (a.get("cards_read") or 0) > 0
            and not a.get("card")
            and a.get("identity") not in ("ambiguous", "not run", "no surname to match")
            and (a.get("in_network") or 0) > 0
        )

    def _lookup_absence_is_real(self, b: dict) -> bool:
        """Is leg B's zero a real absence *within leg B's own search scope*, or just a zero?

        Note precisely what this does and does not license. It establishes that the field was queried
        at the clinic's location, that it is a live NPI index, and that our provider is not in what it
        searched. It does NOT establish WHAT it searched: the request is scoped to six networkIds of
        unmeasured extent (module docstring), so the caller must answer UNKNOWN. This predicate exists
        so that finding can be reported precisely rather than either overclaimed or thrown away.

        `npi_index_proven` is the fix for the reviewer's first finding: the claim is about an NPI
        query, so the control must be an NPI query too. A surname control returning results cannot
        rule out "this field does not index NPIs", under which reading every provider on earth is
        out-of-network. `liveness_has_us` / `liveness_ambiguous` are the second: a control that
        returned OUR provider cannot be evidence that our provider is absent.
        """
        return bool(
            b.get("reached")
            and b.get("location_ok")
            and b.get("line_ok")
            and b.get("count") == 0
            and b.get("npi_index_proven")
            and (b.get("liveness") or 0) > 0
            and b.get("liveness_complete")
            and not b.get("liveness_has_us")
            and not b.get("liveness_ambiguous")
        )

    def _leg_a_settles(self, confirmed: bool, a: dict) -> bool:
        """Whether leg A alone already answers about the member's OWN product — the only case where
        skipping leg B's extra lookups is both polite to the portal and honest. Mirrors `_verdict`."""
        if not confirmed:
            return False
        if a.get("card") and a.get("kind") == "npi":
            return True
        return self._leg_a_absence_is_evidence(a)

    # --- identity and badges -------------------------------------------------------------------------

    def _identity(self, cards: list[str], q: PortalQuery) -> tuple[str | None, str]:
        """Is one of these result cards THIS provider? Returns (the card's text or None, reason).

        The expected name comes from the QUERY, never from the search term: matching against the term
        would make whatever the portal chose to return for "Manayan" count as our Conrad Manayan —
        and live, that was MANAN B SHAH and REX C MANAYAN of Ontario CA, neither of them ours.

        Rules, each one a false verdict that has actually happened somewhere in this layer:
          * the surname must appear as a WHOLE token ("noremi" contains "orem"; "MANAN" does not
            contain "MANAYAN" but a fuzzy portal returns it anyway),
          * a known first name must AGREE — a same-surname stranger is not our provider,
          * only the card's NAME line is matched, never the whole card: the address line ("3280
            HOWELL MILL RD") and the specialty line would otherwise supply name tokens,
          * a card carrying only an initial where our first name should be decides NOTHING: it is
            reported "ambiguous" so the caller answers UNKNOWN rather than inventing either verdict.
            But an initial next to a *different* full first name ("REX C MANAYAN" for Conrad) is not
            ambiguous — it is someone else.
        """
        want_last = _name_tokens(q.provider_last_name)
        if not want_last:
            return None, "no surname to match"
        want_first = _name_tokens(q.provider_first_name)
        ambiguous = False
        for card in cards:
            # The printed NPI is a stronger identity handle than any name, so it is tried first and
            # over the whole card (that is where the portal prints it), not just the name line.
            if q.npi and q.npi in (card or ""):
                return card, "the NPI the portal printed on the card"
            toks = _name_tokens(_first_line(card))
            if not want_last <= toks:
                continue
            others = toks - want_last
            if want_first & others:
                return card, "surname + first name"
            if not want_first:
                ambiguous = True  # surname agrees and we have nothing to separate namesakes by
                continue
            initials = {t for t in others if len(t) == 1}
            names = {t for t in others if len(t) > 1 and t not in _CREDENTIALS}
            if initials & {f[0] for f in want_first} and not (names - want_first):
                ambiguous = True
        return None, "ambiguous" if ambiguous else "no card matched"

    def _badge(self, card_text: str | None) -> str | None:
        """The portal's own network label on a card: "in", "out", or None when it carries neither.

        Negatives are tested first and exhaustively, because the positive test is a substring check
        and "not in network" CONTAINS "in network" — the exact inversion that made two sibling
        drivers report IN_NETWORK over the payer's own contrary badge.
        """
        low = (card_text or "").lower()
        if any(neg in low for neg in _BADGE_NEGATIVE):
            return "out"
        if any(pos in low for pos in _BADGE_POSITIVE):
            return "in"
        return None

    def _plan_listed(self, rows: list[str], label: str | None) -> bool:
        """Is this product among those plan rows? Identifier first, then whole-token containment.

        A normalised string equality was the first cut's test, and `_norm("Wellcare Giveback
        (HMO-POS)") == _norm("Wellcare Giveback (HMO-POS) - H1112")` is False — the trailing contract
        number alone made a covered product look uncovered, which the verdict turned into an
        OUT_OF_NETWORK. Identifier comparison reuses the shared plan_match rules on purpose.
        """
        if not label or not rows:
            return False
        ids = plan_match.identifiers(label)
        toks = plan_match.distinctive_tokens(label)
        want = _norm(_first_line(label))
        for row in rows:
            if ids and ids & plan_match.identifiers(row):
                return True
            if toks and toks <= plan_match.distinctive_tokens(row):
                return True
            got = _norm(_first_line(row))
            if want and got and (want in got or got in want):
                return True
        return False

    def _lob(self, q: PortalQuery) -> str:
        from network_probe.domain.line_of_business import line_of_business

        return line_of_business(q.plan or "", None) or "unknown-line"

    def _line_ok(self, q: PortalQuery) -> bool:
        """Is the member's plan in the universe this Wellcare deployment searches?

        Leg B's decisive claim is "in-network with NONE of our plans". "Our plans" is this brand's
        line — Wellcare is Centene's MEDICARE brand, while Ambetter (Marketplace) and Fidelis /
        Health Net (Medicaid) are separate deployments of the same component library. A Medicaid
        member's absence from the Medicare lookup would be a fabricated OON.
        """
        return self._lob(q) in _HUB_LINES_OF_BUSINESS

    # --- leg A: the plan-pinned hub -----------------------------------------------------------------

    def _leg_a(self, page: Page, q: PortalQuery, pinned: str | None, trail: list[str],
               shot=None) -> dict:
        """The plan-pinned leg: the portal's exact NPI filter, widened to the surname only if that
        filter came back empty. Returns ONE evidence record describing the search it actually ran —
        which search that was (`kind`) is load-bearing, because an NPI-filtered count is a statement
        about our provider and a surname count is a statement about the network.

        Each results page is screenshotted as it is read, because the verdict branches fire after the
        browser has moved on to leg B: without this the note could cite a plan-pinned page that no
        screenshot in the capture actually shows.
        """
        if not pinned:
            return _leg_a_blank()

        first = self._hub_search(page, q, q.npi, "npi", trail)
        first["card"], first["identity"] = self._identity(first["cards"], q)
        first["badge"] = self._badge(first["card"])
        if shot:
            shot("wellcare-pinned-npi-filter")
        if not first["reached"] or (first["in_radius"] or 0) > 0 or not q.provider_last_name:
            # A populated NPI filter is the portal answering about THIS NPI; widening would throw
            # away the more specific answer (and the first cut then read that count as a surname set).
            return first

        # The NPI filter is exact, so a same-surname card after it returns 0 is a DIFFERENT physician
        # (live: "Manayan" surfaced MANAN B SHAH and REX C MANAYAN, neither ours). The surname search
        # runs anyway, because a populated result set from the pinned network is what turns our
        # absence from it into evidence — and its cards are inspected, not assumed.
        second = self._hub_search(page, q, q.provider_last_name, "searchAll", trail)
        second["card"], second["identity"] = self._identity(second["cards"], q)
        second["badge"] = self._badge(second["card"])
        second["npi_filter_zero"] = True
        if shot:
            shot("wellcare-pinned-surname")
        if not second["reached"]:
            # Keep the NPI filter's own (reached, empty) reading rather than losing the walk trail.
            return {**first, "npi_filter_zero": True}
        return second

    def _walk_to_plan(
        self, page: Page, q: PortalQuery, trail: list[str]
    ) -> tuple[str | None, bool, str | None, list[str]]:
        """location → product → search.

        Returns (pinned label, plan_confirmed, plan banner, every product label this market offers).
        The offered list is part of the evidence, not a by-product: it is what lets a line-level plan
        string resolve when the provider turns out to be in-network for all of them.
        """
        try:
            page.goto(ENTRY, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError) as e:
            trail.append(f"entry {ENTRY} failed: {type(e).__name__}")
            return None, False, None, []
        self._settle(page)
        self._cookies(page)
        trail.append(f"landing {page.url.split('?')[0]}")

        # Prefer the landing card's own href — it carries whichever line segment Centene is serving.
        href = None
        try:
            link = page.locator(_LANDING_SEARCH_LINK).first
            if link.count():
                href = link.get_attribute("href")
        except PlaywrightError:
            href = None
        target = (HUB + href) if href and href.startswith("/") else \
            f"{HUB}/x/findaprovider/{_HUB_LINE}/en/default/location"
        try:
            page.goto(target, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError) as e:
            trail.append(f"location step unreachable: {type(e).__name__}")
            return None, False, None, []
        self._settle(page)
        self._cookies(page)  # the cookie banner re-appears on the second host/route
        trail.append("location step")

        place = self._commit_place(page, _PLACE_INPUT, q)
        if not place:
            trail.append("plan location NOT set — no place matched the clinic")
            return None, False, None, []
        trail.append(f"place: {place}")
        if not self._click_any(page, "[data-testid='primary-action-button']", "Continue"):
            trail.append("location-step Continue NOT clickable")
            return None, False, None, []

        try:
            page.locator(_PRODUCT_GROUP).first.wait_for(state="visible", timeout=20_000)
        except (PlaywrightTimeout, PlaywrightError):
            trail.append("'Choose a Plan' step never rendered")
            return None, False, None, []
        self._cookies(page)

        label, confirmed, offered, basis = self._pick_product(page, q)
        trail.append(f"plan match: {basis}")
        if not label:
            trail.append(f"no product matched {q.plan!r} among {len(offered)} offered")
            return None, False, None, offered
        trail.append(f"product pinned{'' if confirmed else ' (representative only)'}: {_first_line(label)}")
        if not self._click_any(page, "[data-testid='primary-action-button']", "Continue"):
            trail.append("product-step Continue NOT clickable")
            return None, False, None, offered

        # Wait for the search step by URL and by control, not by a fixed pause: on a slow render the
        # banner had not painted yet and the walk reported "search step not confirmed" on a page that
        # went on to load perfectly.
        try:
            page.wait_for_url("**/search/**", timeout=25_000)
            page.locator(_SEARCH_BOX).first.wait_for(state="visible", timeout=20_000)
        except (PlaywrightTimeout, PlaywrightError):
            trail.append("search step never rendered after the product step")
            return None, False, None, offered
        self._settle(page, 2_500)
        self._cookies(page)

        banner = self._plan_banner(page)
        if not banner:
            trail.append("search page reached but it printed no plan banner")
            return None, False, None, offered
        trail.append(f"search page pinned to: {banner}")
        # The banner is the portal's own statement of what it is about to search. If it does not name
        # the product we clicked and the clinic's place, we have not proven the network is the right one.
        if not self._banner_agrees(banner, label):
            trail.append("banner does not name the product that was clicked — plan NOT confirmed")
            return label, False, banner, offered
        if q.zip_code and q.zip_code not in banner and not self._state_in(banner, q):
            trail.append("banner names neither the clinic ZIP nor its state — plan NOT confirmed")
            return label, False, banner, offered
        return label, confirmed, banner, offered

    def _banner_agrees(self, banner: str | None, label: str | None) -> bool:
        """Does the search page's own banner name the product we clicked? Compared on the product's
        NAME line and on plan identifiers, not on the whole radio label — the radio carries premium
        and benefit copy underneath that the banner never repeats, so a whole-label containment test
        fails on every product and silently reports "plan not confirmed"."""
        name = _first_line(label)
        if not name or not banner:
            return False
        if _norm(name) and _norm(name) in _norm(banner):
            return True
        ids = plan_match.identifiers(label)
        if ids and ids & plan_match.identifiers(banner):
            return True
        toks = plan_match.distinctive_tokens(name)
        return bool(toks) and toks <= plan_match.distinctive_tokens(banner)

    def _pick_product(self, page: Page, q: PortalQuery) -> tuple[str | None, bool, list[str], str]:
        """Pick the product the member's plan identifies, via the shared plan matcher.

        Returns (radio label, plan_confirmed, offered labels, basis). `plan_confirmed` is exactly
        `PlanMatch.confirms_network` — an IDENTIFIER match (contract / PBP / market code). A
        token-only match pins the search and licenses nothing, and no match at all pins a
        representative product of the member's line so the search is at least scoped; in both cases
        the verdict layer refuses to treat a single-product absence as decisive. Local token
        matching used to make that call here, which is how "Wellcare Medicare Advantage" — words
        every one of the 12 GA products carries — looked like a confident pin.
        """
        try:
            labels = page.locator(_PRODUCT_LABELS)
            texts = [(labels.nth(i).inner_text() or "").strip() for i in range(labels.count())]
        except PlaywrightError:
            return None, False, [], "product radios unreadable"
        texts = [t for t in texts if t]
        if not texts:
            return None, False, [], "the product step listed no products"
        offered = [_first_line(t) or "" for t in texts]

        match = plan_match.match_plan_with_fallback(q.plan, texts)
        if match is not None:
            if not self._click_label(page, match.index):
                return None, False, offered, f"{match.basis}; but the product radio would not click"
            return (texts[match.index], match.confirms_network, offered,
                    f"{match.confidence}: {match.basis}")

        # Nothing cleared the matcher's floor. Choose a representative product of the member's line,
        # never a PDP: Part D is drug-only and has no medical provider network to be in or out of.
        dual = self._lob(q) == "dual"
        medical = [t for t in texts if "(PDP)" not in t.upper()]
        want_dsnp = [t for t in medical if "SNP" in t.upper()]
        pool = (want_dsnp or medical) if dual else ([t for t in medical if "SNP" not in t.upper()] or medical)
        if not pool:
            return None, False, offered, "every product offered is a PDP — no medical network to search"
        basis = (f"no product identifier in the plan string {q.plan!r} matched any of the "
                 f"{len(texts)} products offered; pinned {_first_line(pool[0])!r} as a representative "
                 f"product of the same line, which scopes the search but licenses no verdict")
        if not self._click_label(page, texts.index(pool[0])):
            return None, False, offered, f"{basis}; but the product radio would not click"
        return pool[0], False, offered, basis

    def _click_label(self, page: Page, index: int) -> bool:
        try:
            page.locator(_PRODUCT_LABELS).nth(index).click()
        except (PlaywrightTimeout, PlaywrightError):
            return False
        self._settle(page, 1_500)
        return True

    def _hub_search(self, page: Page, q: PortalQuery, term: str, kind: str, trail: list[str]) -> dict:
        """Run one plan-scoped query and read the results page.

        `kind` is the portal's own search intent: "npi" (exact NPI filter) or "searchAll" (keyword),
        and it is recorded in the result because the two mean different things. Nothing is interpreted
        unless the browser actually landed on a /results/ URL carrying the term — a silently-failed
        commit would otherwise be indistinguishable from "no results".
        """
        out = {**_leg_a_blank(), "term": term, "kind": kind, "identity": "no cards read"}
        if not term:
            return out
        for attempt in (1, 2):
            try:
                box = page.locator(_SEARCH_BOX).first
                box.wait_for(state="visible", timeout=15_000)
                box.click()
                box.fill("")
                page.wait_for_timeout(400)
                box.type(term, delay=80)  # the intent list is rebuilt per keystroke; pasting can race it
            except (PlaywrightTimeout, PlaywrightError):
                trail.append(f"search box unusable for {kind} {term!r}")
                return out
            page.wait_for_timeout(4_500)
            try:
                opt = page.locator(f"[data-testid='autocomplete-option-{kind}-{term}']").first
                opt.wait_for(state="visible", timeout=8_000)
                opt.click()
            except (PlaywrightTimeout, PlaywrightError):
                trail.append(f"no '{kind}' search-intent option for {term!r}")
                return out
            try:
                # Wait for a results URL *carrying this term*, not merely for "/results/": the second
                # search of a capture starts ON a results page, so a bare "/results/" predicate matches
                # the PREVIOUS query's URL instantly and the old page gets read as the new answer.
                page.wait_for_url(lambda url: self._is_results_for(url, term), timeout=25_000)
                out["reached"] = True
                break
            except (PlaywrightTimeout, PlaywrightError):
                trail.append(f"{kind} {term!r} did not reach its own results page (attempt {attempt})")
        if not out["reached"]:
            return out
        self._settle(page, 4_000)
        if not self._is_results_for(page.url, term):
            trail.append(f"results URL no longer carries {term!r} — not trusting it")
            out["reached"] = False
            return out

        text = self._page_text(page)
        out["in_radius"] = 0 if _NO_RESULTS.search(text) else self._first_int(_COUNT_IN_RADIUS, text)
        out["in_network"] = self._first_int(_COUNT_IN_NETWORK, text)
        out["cards"] = self._cards(page)
        out["cards_read"] = len(out["cards"])
        # The portal counts results but shows a page of them, and this layer never pages (Wellcare's
        # ToS prohibits systematic downloading). Fewer cards than the count therefore means we did
        # NOT see the whole set, and nobody's absence from it can be claimed.
        out["truncated"] = out["in_radius"] is not None and out["cards_read"] < out["in_radius"]
        trail.append(
            f"{kind} {term!r} → {out['in_radius']} result(s) in radius / {out['in_network']} in-network"
            f", {out['cards_read']} card(s) read" + (" [TRUNCATED]" if out["truncated"] else "")
        )
        return out

    def _is_results_for(self, url: str, term: str) -> bool:
        """True only for a hub results URL whose own filters carry this search term."""
        u = (url or "").lower().replace("%20", " ").replace("+", " ")
        return "/results/" in u and term.lower() in u

    # --- leg B: the plan-agnostic Provider Lookup --------------------------------------------------

    def _lookup_all_plans(self, page: Page, q: PortalQuery, trail: list[str], shot) -> dict:
        """Ask "is this provider in-network with ANY Wellcare plan here?", then which ones.

        Three human-scale queries, in this order, because the decisive reading needs all three:
          1. our NPI — the answer,
          2. the surname — corroboration, and the source of a control NPI (the portal prints an NPI
             on every card),
          3. that control NPI — the liveness control that is the SAME QUERY TYPE as the answer. This
             is the one thing that separates "this provider is absent" from "this field does not
             index NPIs"; a surname control cannot, and believing it could was the reviewer's first
             finding. Verified live: NPI 1457548513 returned MANAN B SHAH at Kennesaw GA / 50 miles.
        """
        out = _leg_b_blank()
        out["line_ok"] = self._line_ok(q)
        if not self._lb_open_and_search(page, q, q.npi, trail, out):
            return out

        out["reached"] = True
        out["location_echo"] = self._text_of(page, _LB_LOCATION_ECHO)
        out["location_ok"] = self._state_in(out["location_echo"], q) or bool(
            q.city and _place_key(q.city) in _place_key(out["location_echo"])
        )
        out["count"] = self._lb_count(page)
        cards = self._lb_cards(page)
        panel = self._text_of(page, _LB_PANEL) or self._page_text(page)
        our_card, why = self._identity(cards, q)
        out["npi_found"] = f"NPI: {q.npi}" in panel or bool(our_card and q.npi in our_card)
        trail.append(
            f"lookup NPI {q.npi} @ {out['location_echo']!r} → {out['count']} result(s), "
            f"{len(cards)} card(s) read, identity: {why}"
            + ("" if out["location_ok"] else " [location NOT confirmed]")
        )
        # Keep this filename: it is the page a human would be shown as proof (the NPI, the echoed
        # location, the radius and Wellcare's own "may not be in-network with any of our plans"), and
        # the control searches that follow overwrite the live page.
        out["npi_shot"] = shot("wellcare-lookup-npi")

        if out["npi_found"]:
            out["matched_name"] = _first_line(our_card) or self._text_of(page, "[data-testid='provider-details']")
            out["name_agrees"] = why in ("surname + first name", "the NPI the portal printed on the card")
            out["plans"], out["plan_count"], out["plans_complete"] = self._plans_for_provider(page, trail, shot)
            out["detail"] = (f"found, in-network with {out['plan_count']} product(s)"
                             + ("" if out["plans_complete"] else " (product list not read in full)"))
            return out

        if not out["location_ok"]:
            out["detail"] = (f"the portal searched {out['location_echo']!r}, which is not the clinic's "
                             f"city/state — absence there proves nothing")
            return out
        if not out["line_ok"]:
            out["detail"] = (f"{q.plan!r} is a {self._lob(q)} plan, and this Wellcare deployment "
                             f"searches its {'/'.join(_HUB_LINES_OF_BUSINESS)} products — an absence "
                             f"from a product universe that does not contain the member's plan is "
                             f"not evidence about it")
            return out
        if not q.provider_last_name:
            out["detail"] = ("0 results for the NPI, and with no provider surname there is no control "
                             "search to prove the surface returns anything at all")
            return out

        # --- control 1: the surname. Corroborates, and supplies an NPI the portal itself printed.
        ctl = self._lb_research(page, q.provider_last_name, out["count"], trail)
        out["liveness"] = ctl["count"]
        out["liveness_cards_read"] = len(ctl["cards"])
        out["liveness_complete"] = bool(ctl["cards"]) and ctl["count"] is not None \
            and len(ctl["cards"]) >= ctl["count"]
        card, why = self._identity(ctl["cards"], q)
        # The control's own results MUST be looked at. A control that returned our provider cannot be
        # proof that our provider is absent — the first cut returned only a count and so asserted
        # both at once.
        out["liveness_has_us"] = card is not None
        out["liveness_ambiguous"] = why == "ambiguous"
        out["liveness_match"] = _first_line(card)
        shot("wellcare-lookup-liveness")

        if out["liveness_has_us"]:
            out["detail"] = (f"the {q.provider_last_name!r} control returned a card this driver "
                             f"identifies as the same provider ({out['liveness_match']!r}, matched on "
                             f"{why}) while the NPI query returned 0 — the surface contradicts itself, "
                             f"so neither reading can be used")
            return out
        if out["liveness_ambiguous"]:
            out["detail"] = (f"the {q.provider_last_name!r} control returned a card carrying only an "
                             f"initial where this provider's first name should be, so it can neither "
                             f"be nor not be them")
            return out
        if not (out["liveness"] or 0) > 0:
            out["detail"] = (f"0 results for the NPI and {out['liveness']} for "
                             f"{q.provider_last_name!r} — two empty sets, so the surface itself is "
                             f"unproven")
            return out
        if not out["liveness_complete"]:
            out["detail"] = (f"the {q.provider_last_name!r} control counted {out['liveness']} "
                             f"result(s) but only {out['liveness_cards_read']} card(s) could be read, "
                             f"so it cannot be said that this provider was not among them")
            return out

        # --- control 2: an NPI query, the same query type as the answer. This is the whole point.
        out["control_npi"] = next((n for n in ctl["npis"] if n != q.npi), None)
        if not out["control_npi"]:
            out["detail"] = (f"the {q.provider_last_name!r} control returned "
                             f"{out['liveness_cards_read']} card(s) but none printed an NPI, so no "
                             f"NPI query could be shown to work — and a 0 for an NPI query on a field "
                             f"that may not index NPIs at all is not evidence of absence")
            return out
        # A fresh search from the lookup's landing view, not a re-search on the results view: the
        # results view never echoes the term it is showing, so a no-op submit there would leave the
        # previous (surname) results on screen — whose one card carries this very control NPI, making
        # a no-op indistinguishable from success. Reloading the page cannot produce that coincidence.
        idx = _leg_b_blank()
        if self._lb_open_and_search(page, q, out["control_npi"], trail, idx):
            found = self._lb_cards(page)
            npis = self._npis_in(found)
            out["npi_index_proven"] = (self._lb_count(page) or 0) > 0 and out["control_npi"] in npis
            trail.append(f"NPI-index control {out['control_npi']} → "
                         f"{'returns its provider' if out['npi_index_proven'] else 'NOT returned'}")
            shot("wellcare-lookup-npi-index-control")
        if not out["npi_index_proven"]:
            out["detail"] = (f"0 results for NPI {q.npi}, but the control NPI {out['control_npi']} "
                             f"(printed by the portal itself on the {q.provider_last_name!r} card) "
                             f"did not come back either — this field cannot be shown to index NPIs, "
                             f"so its zero says nothing about this provider")
            return out
        out["detail"] = (f"0 results for the NPI at a location the portal echoed back, while the "
                         f"control NPI {out['control_npi']} returned its provider and "
                         f"{q.provider_last_name!r} returned {out['liveness']} other provider(s) — "
                         f"the field is a live NPI index and this provider is absent from every "
                         f"Wellcare product there")
        return out

    def _lb_open_and_search(self, page: Page, q: PortalQuery, term: str, trail: list[str],
                            out: dict) -> bool:
        """Load the lookup fresh and run one provider query at the clinic's location.

        Used for both the answer and the NPI-index control, so both are committed the same proven
        way (landing view -> results view), rather than the control riding on a re-search whose
        commit cannot be verified.
        """
        try:
            page.goto(LOOKUP, wait_until="domcontentloaded", timeout=45_000)
        except (PlaywrightTimeout, PlaywrightError) as e:
            out["detail"] = f"lookup page unreachable: {type(e).__name__}"
            trail.append(out["detail"])
            return False
        self._settle(page)
        self._cookies(page)
        trail.append(f"all-plans Provider Lookup ({term})")
        try:
            page.locator(_LB_PROVIDER_INPUT).first.fill(term)
        except (PlaywrightTimeout, PlaywrightError):
            out["detail"] = "provider field not usable"
            trail.append(out["detail"])
            return False
        if not self._commit_place(page, _LB_LOCATION_INPUT, q):
            out["detail"] = "no Google-Places option matched the clinic's city/state"
            trail.append(out["detail"])
            return False
        if not self._click_any(page, _LB_SEARCH_BUTTON, "Search"):
            out["detail"] = "lookup Search button not clickable"
            trail.append(out["detail"])
            return False
        self._settle(page, 5_000)
        return True

    def _lb_research(self, page: Page, term: str, before: int | None, trail: list[str]) -> dict:
        """Re-search on the lookup's results view, which keeps the already-committed location.

        Returns the count AND the cards, because the caller has to inspect what a control returned:
        a control that came back with our own provider is not evidence that our provider is absent.
        `before` is the count on screen when we arrived — this path is only entered after a 0, so any
        card appearing proves the new query really committed (the view echoes no search term, so
        there is nothing else to verify a commit against).
        """
        out: dict = {"term": term, "count": None, "cards": [], "npis": [], "committed": False}
        try:
            box = page.locator(_LB_PROVIDER_INPUT_AGAIN).first
            box.wait_for(state="visible", timeout=10_000)
            box.fill(term)
            page.locator(_LB_SEARCH_AGAIN).first.click()
        except (PlaywrightTimeout, PlaywrightError):
            trail.append(f"liveness control {term!r} could not be submitted")
            return out
        self._settle(page, 5_000)
        out["count"] = self._lb_count(page)
        out["cards"] = self._lb_cards(page)
        out["npis"] = self._npis_in(out["cards"])
        out["committed"] = (before or 0) == 0 and (out["count"] or 0) > 0
        trail.append(f"lookup control {term!r} → {out['count']} result(s), "
                     f"{len(out['cards'])} card(s), NPIs {out['npis']}")
        return out

    def _lb_cards(self, page: Page) -> list[str]:
        """One text block per result card on the plan-agnostic lookup.

        The card has no testid; `card-result-notmobile` is its own class, found by walking up from
        the "NPI: …" text node (2026-07-28). The fallback segments the results panel around each
        "NPI: <10 digits>" line so a class rename degrades to a slightly noisier read rather than to
        an empty card list — an empty list must never be mistaken for "no such provider", so callers
        check `cards_read` against the portal's own count.
        """
        for sel in _LB_CARDS:
            try:
                loc = page.locator(sel)
                n = loc.count()
            except PlaywrightError:
                continue
            if n:
                texts = [(loc.nth(i).inner_text() or "") for i in range(min(n, 30))]
                keep = [t for t in texts if t.strip()]
                if keep:
                    return keep
        panel = self._text_of(page, _LB_PANEL) or ""
        lines = [ln for ln in panel.splitlines()]
        blocks: list[str] = []
        for i, line in enumerate(lines):
            if not _NPI_ON_CARD.search(line):
                continue
            # A card is name / specialty / NPI / address / distance, so a window around the NPI line
            # captures it without dragging in the panel header (which would otherwise supply the
            # searched term back to identity matching).
            blocks.append("\n".join(lines[max(0, i - 2):i + 3]).strip())
        return blocks

    def _npis_in(self, cards: list[str]) -> list[str]:
        """Every NPI the portal printed on these cards, in order, de-duplicated."""
        found: list[str] = []
        for card in cards:
            for m in _NPI_ON_CARD.finditer(card or ""):
                if m.group(1) not in found:
                    found.append(m.group(1))
        return found

    def _plans_for_provider(self, page: Page, trail: list[str], shot) -> tuple[list[str], int | None, bool]:
        """Expand "View All In-Network Plans" and read the product names it lists.

        Also returns whether the list was read IN FULL (the heading counts the products): "the
        pinned product is not in this list" is only an out-of-network claim if the list is complete.
        """
        if not self._click_any(page, _LB_PLANS_LINK, "View All In-Network Plans"):
            trail.append("in-network plan list would not open")
            return [], None, False
        self._settle(page, 4_000)
        plans: list[str] = []
        try:
            rows = page.locator(_LB_PLAN_ROWS)
            for i in range(min(rows.count(), 40)):
                first_line = _first_line(rows.nth(i).inner_text() or "")
                if first_line:
                    plans.append(first_line)
        except PlaywrightError:
            pass
        heading = self._text_of(page, _LB_PLANS_HEADING) or ""
        counted = self._first_int(re.compile(r"([\d,]+)"), heading)
        complete = bool(plans) and (counted is None or len(plans) >= counted)
        trail.append(f"in-network plans: {len(plans)} listed of {counted if counted is not None else '?'} "
                     f"({heading.strip() or 'no heading'})" + ("" if complete else " [INCOMPLETE]"))
        shot("wellcare-lookup-plans")
        return plans, counted or (len(plans) or None), complete

    # --- shared plumbing ---------------------------------------------------------------------------

    def _commit_place(self, page: Page, selector: str, q: PortalQuery) -> str | None:
        """Type the clinic's location and commit the autocomplete option that really is that place.

        Returns the chosen option text, or None — and None must never be treated as "searched
        everywhere": the hub's box is restricted to cities/counties/ZIPs, but the lookup's box is raw
        Google Places, where "30144" offered a street address in Auburn, Washington first.
        """
        # "<city>, <ST>" — the 2-letter code, never the roster's market-suffixed state: typing
        # "Kennesaw, GA-Atlanta" made Google Places drop the plain-city suggestion and offer only
        # street addresses, so the radius origin became a house number instead of the city.
        typed = ", ".join(p for p in (q.city, self._state(q)) if p) or q.zip_code or ""
        if not typed:
            return None
        for candidate in (typed, q.zip_code or typed):
            try:
                box = page.locator(selector).first
                box.wait_for(state="visible", timeout=15_000)
                box.click()
                box.fill(candidate)
            except (PlaywrightTimeout, PlaywrightError):
                return None
            page.wait_for_timeout(3_500)
            try:
                opts = page.locator(f"{_PLACE_OPTION}:visible, .pac-item:visible")
                texts = [(opts.nth(i).inner_text() or "") for i in range(min(opts.count(), 12))]
            except PlaywrightError:
                texts = []
            idx = self._best_place(texts, q)
            if idx is None:
                continue
            try:
                opts.nth(idx).click()
            except (PlaywrightTimeout, PlaywrightError):
                return None
            self._settle(page, 2_000)
            return texts[idx].replace("\n", " ").strip()
        return None

    def _best_place(self, texts: list[str], q: PortalQuery) -> int | None:
        """Score place options against the clinic. A street address in another state scores 0."""
        city, state, zipc = _place_key(q.city), _place_key(self._state(q)), _place_key(q.zip_code)
        best, best_score = None, 0
        for i, raw in enumerate(texts):
            key = _place_key(raw)
            score = 0
            if state and state not in key:
                continue  # wrong state — never a candidate
            # A city-level place must outrank a street address inside the same city: Google Places
            # offered "3316 Busbee Dr NW, Kennesaw, GA 30144" alongside "Kennesaw, GA", and a house
            # number narrows the radius origin for no reason.
            if city and key.startswith(city):
                score += 4
            elif city and city in key:
                score += 1
            if zipc and zipc in key:
                score += 2
            if state:
                score += 1
            if score > best_score:
                best, best_score = i, score
        return best

    def _cards(self, page: Page) -> list[str]:
        try:
            loc = page.locator(_RESULT_CARDS)
            texts = [(loc.nth(i).inner_text() or "") for i in range(min(loc.count(), 30))]
        except PlaywrightError:
            return []
        return [t for t in texts if t.strip() and not any(m in t for m in _CHROME_CARDS)]

    def _plan_banner(self, page: Page) -> str | None:
        """The "Plan  <product> — <place> (<year>)" line the search/results page prints for itself."""
        for sel in _PLAN_PANELS:
            try:
                loc = page.locator(sel).first
                if not loc.count():
                    continue
                lines = [ln.strip() for ln in (loc.inner_text() or "").splitlines() if ln.strip()]
            except PlaywrightError:
                continue
            for i, line in enumerate(lines[:-1]):
                if line.rstrip(":") == "Plan":
                    return lines[i + 1]
        return None

    def _cookies(self, page: Page) -> None:
        """Dismiss the Centene cookie banner. It re-appears on every host/route in this flow, so this
        is called after each navigation rather than once at the start."""
        for name in ("Accept", "Decline"):
            for role in ("button", "link"):
                try:
                    b = page.get_by_role(role, name=name, exact=True).first
                    if b.count() and b.is_visible():
                        b.click()
                        page.wait_for_timeout(1_200)
                        return
                except (PlaywrightTimeout, PlaywrightError):
                    continue

    def _click_any(self, page: Page, testid_sel: str, label: str, timeout_ms: int = 12_000) -> bool:
        """testid → accessible name → visible text. The Continue button carries a *different* testid
        on each step of this walk, so the accessible name is the more durable handle here."""
        for kind in ("testid", "role", "text"):
            try:
                if kind == "testid":
                    loc = page.locator(testid_sel).first
                elif kind == "role":
                    loc = page.get_by_role("button", name=label, exact=True).first
                else:
                    loc = page.get_by_text(label, exact=True).first
                loc.wait_for(state="visible", timeout=timeout_ms)
                loc.click()
            except (PlaywrightTimeout, PlaywrightError):
                continue
            # The click landed. Settling is best-effort and must never be allowed to retract it.
            self._settle(page)
            return True
        return False

    def _settle(self, page: Page, pause_ms: int = 3_000) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except (PlaywrightTimeout, PlaywrightError):
            pass
        try:
            page.wait_for_timeout(pause_ms)
        except PlaywrightError:
            pass

    def _lb_count(self, page: Page) -> int | None:
        text = self._text_of(page, _LB_COUNTER) or ""
        for pattern in _LB_COUNT:
            n = self._first_int(pattern, text)
            if n is not None:
                return n
        return 0 if _NO_RESULTS.search(self._page_text(page)) else None

    def _state(self, q: PortalQuery) -> str:
        """The 2-letter state. Roster states carry a market suffix ("GA-Atlanta"), so trim to the code."""
        m = re.match(r"([A-Za-z]{2})\b", (q.state or "").strip())
        return m.group(1).upper() if m else ""

    def _state_in(self, text: str | None, q: PortalQuery) -> bool:
        state = self._state(q)
        return bool(state) and bool(re.search(rf"\b{state}\b", text or "", re.I))

    def _first_int(self, pattern: re.Pattern[str], text: str) -> int | None:
        m = pattern.search(text or "")
        return int(m.group(1).replace(",", "")) if m else None

    def _text_of(self, page: Page, selector: str) -> str | None:
        try:
            loc = page.locator(selector).first
            return (loc.inner_text() or "").strip() if loc.count() else None
        except PlaywrightError:
            return None

    def _page_text(self, page: Page) -> str:
        try:
            return page.inner_text("body") or ""
        except PlaywrightError:
            return ""
