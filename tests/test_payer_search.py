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
