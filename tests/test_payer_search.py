import httpx

from network_probe.core._http import CachedClient
from network_probe.payers.search import search_roster, search_stedi

ROWS = [
    {"label": "Aetna", "key": "aetna-az", "benefit_type": "Commercial", "state": "AZ",
     "stedi_payer_id": "60054", "enrollment_status": "needs_enrollment"},
    {"label": "Aetna Better Health", "key": "aetna-better-health-fl-south-florida",
     "benefit_type": "Managed Medicaid", "state": "FL-South Florida",
     "stedi_payer_id": "ABH01", "enrollment_status": "needs_enrollment"},
    {"label": "Oscar", "key": "oscar-az", "benefit_type": "ACA", "state": "AZ",
     "stedi_payer_id": "OSCAR", "enrollment_status": "supported"},
]


def test_roster_ranks_exact_then_prefix_then_substring():
    out = search_roster(ROWS, "aetna")
    assert out[0]["label"] == "Aetna" and out[0]["value"] == "aetna-az"  # exact before prefix
    assert out[0]["source"] == "roster" and out[0]["market"] == "AZ"
    assert {o["label"] for o in out} == {"Aetna", "Aetna Better Health"}


def test_roster_blank_query_returns_nothing():
    assert search_roster(ROWS, "   ") == []


def test_roster_dedupes_same_catalogue_key():
    # Same payer/market appears once per benefit type but shares the catalogue key (value).
    # AntD Select requires unique values -> collapse to one option.
    rows = [
        {"label": "Aetna", "key": "aetna-az", "benefit_type": "Commercial", "state": "AZ",
         "stedi_payer_id": "60054", "enrollment_status": "needs_enrollment"},
        {"label": "Aetna", "key": "aetna-az", "benefit_type": "Medicare Advantage", "state": "AZ",
         "stedi_payer_id": "60054", "enrollment_status": "needs_enrollment"},
    ]
    out = search_roster(rows, "aetna")
    assert len(out) == 1 and out[0]["value"] == "aetna-az"


# --- token-set matcher: client free-text payer names -> canonical roster rows ---------------
# Mirrors the real catalogue shape: canonical brand labels, one dedupe key per (payer, state),
# multiple benefit types per key. Client rosters use richer names ("UHC AARP Medicare Advantage",
# "Humana Medicare CO") that must still resolve to the right brand/state/benefit row.
CATALOG = [
    {"label": "UnitedHealthcare", "key": "unitedhealthcare-az", "benefit_type": "Commercial",
     "state": "AZ", "stedi_payer_id": "87726", "enrollment_status": "supported"},
    {"label": "UnitedHealthcare", "key": "unitedhealthcare-az", "benefit_type": "Medicare Advantage",
     "state": "AZ", "stedi_payer_id": "87726", "enrollment_status": "supported"},
    {"label": "UnitedHealthcare", "key": "unitedhealthcare-ga-atlanta", "benefit_type": "Medicare Advantage",
     "state": "GA-Atlanta", "stedi_payer_id": "87726", "enrollment_status": "supported"},
    {"label": "UnitedHealthcare Community Plan", "key": "unitedhealthcare-community-plan-ny",
     "benefit_type": "Managed Medicaid", "state": "NY", "stedi_payer_id": "87726",
     "enrollment_status": "needs_enrollment"},
    {"label": "Humana", "key": "humana-az", "benefit_type": "Medicare Advantage", "state": "AZ",
     "stedi_payer_id": "61101", "enrollment_status": "supported"},
    {"label": "Humana", "key": "humana-co-denver", "benefit_type": "Medicare Advantage",
     "state": "CO-Denver", "stedi_payer_id": "61101", "enrollment_status": "supported"},
    {"label": "Humana", "key": "humana-fl", "benefit_type": "Medicare Advantage", "state": "FL",
     "stedi_payer_id": "61101", "enrollment_status": "supported"},
    {"label": "Oscar", "key": "oscar-az", "benefit_type": "ACA", "state": "AZ",
     "stedi_payer_id": "OSCAR", "enrollment_status": "supported"},
    {"label": "Aetna", "key": "aetna-az", "benefit_type": "Commercial", "state": "AZ",
     "stedi_payer_id": "60054", "enrollment_status": "needs_enrollment"},
    {"label": "Aetna", "key": "aetna-az", "benefit_type": "Medicare Advantage", "state": "AZ",
     "stedi_payer_id": "60054", "enrollment_status": "needs_enrollment"},
    {"label": "Aetna Better Health", "key": "aetna-better-health-az", "benefit_type": "Managed Medicaid",
     "state": "AZ", "stedi_payer_id": "ABH01", "enrollment_status": "needs_enrollment"},
    {"label": "Peach State Health Plan (Centene)", "key": "peach-state-ga-atlanta",
     "benefit_type": "Managed Medicaid", "state": "GA-Atlanta", "stedi_payer_id": "68069",
     "enrollment_status": "needs_enrollment"},
    {"label": "BCBS / Empire (Anthem / Elevance)", "key": "bcbs-empire-az", "benefit_type": "ACA",
     "state": "AZ", "stedi_payer_id": None, "enrollment_status": "needs_payer_id"},
]


def test_uhc_abbreviation_resolves_to_unitedhealthcare():
    # "uhc" is not a substring of "unitedhealthcare" -> only an alias can bridge it.
    out = search_roster(CATALOG, "UHC AARP Medicare Advantage")
    assert out, "UHC ... must resolve to UnitedHealthcare"
    assert out[0]["label"] == "UnitedHealthcare"
    assert out[0]["benefit_type"] == "Medicare Advantage"  # benefit hint, not Commercial


def test_bare_aarp_resolves_to_unitedhealthcare():
    out = search_roster(CATALOG, "AARP Medicare Advantage")
    assert out and out[0]["label"] == "UnitedHealthcare"


def test_united_healthcare_spaced_resolves():
    out = search_roster(CATALOG, "United Healthcare Medicare Advantage")
    assert out and out[0]["label"].startswith("UnitedHealthcare")


def test_state_hint_ranks_matching_state_first():
    out = search_roster(CATALOG, "Humana Medicare CO")
    assert out[0]["label"] == "Humana" and out[0]["market"] == "CO-Denver"


def test_extra_plan_words_match_bare_brand_label():
    out = search_roster(CATALOG, "Oscar Health")
    assert [o["label"] for o in out] == ["Oscar"]


def test_benefit_hint_prefers_medicare_row_over_commercial():
    # Commercial + Medicare Advantage share the same dedupe key; the Medicare row must win.
    out = search_roster(CATALOG, "Aetna Medicare AZ")
    assert out[0]["label"] == "Aetna"
    assert out[0]["benefit_type"] == "Medicare Advantage"


def test_aetna_query_ranks_aetna_before_aetna_better_health():
    out = search_roster(CATALOG, "Aetna Medicare AZ")
    labels = [o["label"] for o in out]
    assert labels.index("Aetna") < labels.index("Aetna Better Health")


def test_state_filler_word_does_not_match_unrelated_brand():
    # "state" is a filler word in many Medicaid plan names -> must not link Sunshine to Peach State.
    out = search_roster(CATALOG, "Sunshine State Health Plan")
    assert all("Peach State" not in o["label"] for o in out)


def test_benefit_only_query_returns_nothing():
    # No brand token at all -> must not match every Medicare/Advantage row.
    assert search_roster(CATALOG, "Medicare Advantage") == []


def test_state_arg_biases_ranking_without_state_token_in_query():
    # The member's state comes from the form field, not the typed payer text -> passed as an arg.
    out = search_roster(CATALOG, "Humana", state="CO")
    assert out[0]["label"] == "Humana" and out[0]["market"] == "CO-Denver"


def test_state_arg_disambiguates_same_brand_across_states():
    # Two labels share the brand tokens {bcbs, anthem}: a junk IL "BCBS (Anthem)" that "covers all"
    # the query tokens, and the real GA row that carries the payer id. An explicitly-given state must
    # outrank token-coverage so the GA payer resolves (this is the live Czigans case).
    rows = [
        {"label": "BCBS (Anthem)", "key": "bcbs-anthem-il", "benefit_type": "Medicare Advantage",
         "state": "IL", "stedi_payer_id": None, "enrollment_status": "needs_payer_id"},
        {"label": "BCBS / Empire (Anthem / Elevance)", "key": "bcbs-empire-ga-atlanta",
         "benefit_type": "Commercial", "state": "GA-Atlanta", "stedi_payer_id": "00601",
         "enrollment_status": "needs_enrollment"},
    ]
    out = search_roster(rows, "BCBS Anthem Georgia", state="GA")
    assert out[0]["market"] == "GA-Atlanta" and out[0]["stedi_payer_id"] == "00601"


def test_state_arg_accepts_market_suffix_form():
    # A market-suffixed state ("CO-Denver") must normalize to the same CO bias.
    out = search_roster(CATALOG, "Humana", state="CO-Denver")
    assert out[0]["market"] == "CO-Denver"


def test_state_arg_absent_keeps_query_state_token_behavior():
    # Passing no state arg must not change the existing in-query state-hint behavior.
    out = search_roster(CATALOG, "Humana Medicare CO")
    assert out[0]["market"] == "CO-Denver"


def test_client_roster_markets_are_covered():
    # Regression guard for the live demo: every payer the client patient-roster references must have
    # a catalogue row in its market, so state-scoped search resolves to the right-state row instead
    # of falling back to an arbitrary market (which is what made state=FL surface a TX UHC row).
    from network_probe.payers.roster_seed import payer_rows

    rows = list(payer_rows())

    def has(label, state_prefix):
        return any(r["label"] == label and r["state"].upper().startswith(state_prefix) for r in rows)

    need = [
        ("Meridian Health", "IL"),
        ("UnitedHealthcare", "AZ"), ("UnitedHealthcare", "FL"), ("UnitedHealthcare", "GA"),
        ("Oscar", "FL"),
        ("Aetna", "AZ"),
        ("BCBS / Empire (Anthem / Elevance)", "AZ"),
        ("Humana", "CO"),
        ("Noridian Healthcare Solutions, LLC", "AZ"),
        ("Novitas Solutions, Inc.", "CO"),
        ("Health Choice / BCBS / (Anthem / Elevance)", "AZ"),
        ("UMR", "AZ"),
    ]
    missing = [(label, sp) for (label, sp) in need if not has(label, sp)]
    assert not missing, f"client-roster markets missing from catalogue: {missing}"


def test_stedi_maps_items_with_prefix_value():
    payload = {"items": [
        {"primaryPayerId": "128KY", "displayName": "Aetna Better Health of Kentucky", "stediId": "AABKY"},
        {"stediId": "ONLYSTEDI", "conciseName": "Some Plan"},
        {"displayName": "No Id Plan"},  # dropped: no id
    ]}
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=transport))
    out = search_stedi(client, "KEY", "aetna")
    assert out[0] == {
        "value": "stedi:128KY", "label": "Aetna Better Health of Kentucky", "market": None,
        "benefit_type": None, "stedi_payer_id": "128KY", "enrollment_status": None, "source": "stedi",
    }
    assert out[1]["value"] == "stedi:ONLYSTEDI"  # falls back to stediId
    assert len(out) == 2  # the id-less item is dropped
