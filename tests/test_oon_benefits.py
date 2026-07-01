"""Offline tests for OON benefit parsing + subscriber identity extraction.

All fixtures are synthetic (no PHI). The 271 shapes mirror what Stedi returns live:
- `inPlanNetworkIndicatorCode`: Y (in-network), N (out-of-network), W (not applicable).
- coinsurance carries `benefitPercent` ("0.40"); copay/deductible/OOP carry `benefitAmount`.
"""

from network_probe import oon_benefits as ob
from network_probe.oon_benefits import (
    parse_oon,
    oon_only,
    network_label,
    subscriber_identity,
    stedi_270_body,
    save_271,
    load_271,
)


SYNTHETIC_271 = {
    "payer": {"name": "DEVOTED HEALTH"},
    "benefitsInformation": [
        {"code": "A", "name": "Co-Insurance", "inPlanNetworkIndicatorCode": "N",
         "benefitPercent": "0.40", "serviceTypes": ["Chemotherapy"]},
        {"code": "B", "name": "Co-Payment", "inPlanNetworkIndicatorCode": "N",
         "benefitAmount": "50.00", "timeQualifier": "Visit",
         "coverageLevel": "Individual", "serviceTypes": ["Professional (Physician)"]},
        {"code": "C", "name": "Deductible", "inPlanNetworkIndicatorCode": "Y",
         "benefitAmount": "1500", "coverageLevel": "Individual"},
        {"code": "G", "name": "Out of Pocket (Stop Loss)", "inPlanNetworkIndicatorCode": "W",
         "benefitAmount": "9000", "coverageLevel": "Family"},
        {"code": "1", "name": "Active Coverage"},  # no network code
    ],
}


def test_network_label_maps_edi_codes():
    assert network_label("Y") == "In Network"
    assert network_label("N") == "Out of Network"
    assert network_label("W") == "Not Applicable"
    assert network_label(None) == "Unspecified"


def test_parse_oon_returns_all_lines_with_labels():
    rows = parse_oon(SYNTHETIC_271)
    assert len(rows) == 5
    assert [r["network"] for r in rows] == [
        "Out of Network", "Out of Network", "In Network", "Not Applicable", "Unspecified",
    ]


def test_parse_oon_formats_percent_and_amount():
    rows = parse_oon(SYNTHETIC_271)
    coins = next(r for r in rows if r["code"] == "A")
    copay = next(r for r in rows if r["code"] == "B")
    ded = next(r for r in rows if r["code"] == "C")
    assert coins["value"] == "40%"
    assert copay["value"] == "$50.00"
    assert ded["value"] == "$1,500.00"
    assert copay["service_types"] == ["Professional (Physician)"]
    assert copay["time"] == "Visit"


def test_oon_only_filters_to_out_of_network():
    oon = oon_only(parse_oon(SYNTHETIC_271))
    assert len(oon) == 2
    assert all(r["network_code"] == "N" for r in oon)


def test_parse_oon_empty_is_safe():
    assert parse_oon({}) == []
    assert parse_oon({"benefitsInformation": []}) == []


# --- subscriber identity (the person the 270 must be keyed on) ---

SUBSCRIBER_REPORT = (
    "Eligibility Report\n"
    "VERIFICATION TYPE : Subscriber Verification\n"
    "Member ID : DOB\n"                      # decoy label (the real one has digits)
    "Member ID : 900112233\n"
    "Date Of Birth : 03/14/1971\n"
)

DEPENDENT_REPORT = (
    "Eligibility Report\n"
    "VERIFICATION TYPE : Dependent Verification\n"
    "Subscriber Relationship : Spouse\n"
    "Firstname : MOHAMMAD\n"
    "Date Of Birth : 07/02/1968\n"            # subscriber DOB first
    "Member ID : 800556677\n"
    "Date Of Birth : 11/20/1972\n"            # dependent DOB second
)


def test_subscriber_identity_subscriber_case_uses_filename_name():
    idn = subscriber_identity("Eligibility Report - Craig, Duana - x.pdf", text=SUBSCRIBER_REPORT)
    assert idn["member_id"] == "900112233"     # skips the "DOB" decoy
    assert idn["dob"] == "19710314"            # YYYYMMDD
    assert idn["first_name"] == "Duana"
    assert idn["last_name"] == "Craig"


def test_subscriber_identity_dependent_case_uses_subscriber_name_and_first_dob():
    idn = subscriber_identity("Eligibility Report - Salman, Sobia - x.pdf", text=DEPENDENT_REPORT)
    assert idn["member_id"] == "800556677"
    assert idn["dob"] == "19680702"            # the subscriber's (first) DOB, not the patient's
    assert idn["first_name"] == "MOHAMMAD"     # subscriber, not the patient "Sobia"
    assert idn["last_name"] == "Salman"


# --- saved live 270/271 pair (served to the Stedi evidence lane without a runtime key) ---

def test_stedi_270_body_drops_empty_fields():
    body = stedi_270_body("87726", {"npi": "1", "firstName": None}, {"memberId": "M1", "lastName": ""})
    assert body["tradingPartnerServiceId"] == "87726"
    assert body["provider"] == {"npi": "1"}                 # None dropped
    assert body["subscriber"] == {"memberId": "M1"}         # empty dropped
    assert body["encounter"] == {"serviceTypeCodes": ["30"]}


def test_save_and_load_271_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ob, "RAW_271_DIR", tmp_path / "stedi_271")
    save_271("1972603934", {"provider": {"npi": "1972603934"}},
             {"benefitsInformation": [{"inPlanNetworkIndicatorCode": "Y"}]},
             meta={"patient": "Sobia Salman", "payer_key": "uhc"})
    got = load_271("1972603934")
    assert got["npi"] == "1972603934" and got["patient"] == "Sobia Salman"
    assert got["response_271"]["benefitsInformation"][0]["inPlanNetworkIndicatorCode"] == "Y"
    assert load_271("0000000000") is None    # not fetched
    assert load_271(None) is None
