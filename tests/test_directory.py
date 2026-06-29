"""PDF-directory subsystem: line parser, name+state+zip matcher, and the DB-directory adapter.

No PDF or DB needed — the parser is tested on synthetic lines (mirroring the real Align layout,
validated live at 53,787 rows), the matcher on row dicts, and the adapter via an injected
candidates_fn. The live download + 53k-row load runs in the deployment (ENABLE_DIRECTORY_REFRESH).
"""

from __future__ import annotations

from network_probe.domain.directory_match import _norm, match_directory
from network_probe.domain.directory_pdf import parse_lines, parse_lines_aaneel
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.payers.adapters.db_directory import DbDirectoryAdapter

# real Align layout: NAME, anchor, accepting, street, "CITY, ST, ZIP", Phone — repeated.
LINES = [
    "INTERNAL MEDICINE",  # specialty section header
    "JOHN SCHMIDT",
    "Available As Of: 1/1/2021",
    "Accepting New Patients: Yes",
    "2441 SURFSIDE BLVD STE 200",
    "CAPE CORAL, FL, 33914",
    "Phone: (239) 541-7500",
    "KEITH A BAKER",
    "Available As Of: 8/25/2021",
    "Accepting New Patients: No",
    "316 DEL PRADO BLVD S",
    "CAPE CORAL, FL, 33990",
    "Phone: (239) 226-2650",
]


def test_parse_lines_extracts_records():
    es = parse_lines(LINES, specialties={"INTERNAL MEDICINE"})
    assert len(es) == 2
    j = es[0]
    assert (j.name, j.last_name, j.first_name) == ("JOHN SCHMIDT", "SCHMIDT", "JOHN")
    assert j.specialty == "Internal Medicine"
    assert j.accepting_new is True
    assert j.locations[0] == {
        "address": "2441 SURFSIDE BLVD STE 200",
        "city": "CAPE CORAL",
        "state": "FL",
        "zip": "33914",
    }
    assert es[1].last_name == "BAKER" and es[1].accepting_new is False


def test_parse_lines_multi_location_one_provider():
    lines = [
        "ANA CORONADO",
        "Available As Of: 6/1/2024",
        "Accepting New Patients: Yes",
        "21150 BISCAYNE BLVD",
        "AVENTURA, FL, 33180",
        "Phone: (305) 692-3270",
        "20801 BISCAYNE BLVD STE 200",
        "AVENTURA, FL, 33180",
        "Phone: (954) 682-2900",
    ]
    es = parse_lines(lines)
    assert len(es) == 1
    assert len(es[0].locations) == 2
    assert es[0].zips == {"33180"}


# AaNeel / eternalHealth layout: NAME[(M)] / Specialty / ProviderID / Org[/Org2] / Address / CITY,ST,ZIP / phone
AANEEL_LINES = [
    "Behavioral Health",  # section header (ignored — specialty is per-record)
    "CHRISTIANA HINES",
    "Professional Counselor",
    "P0191519-258948",
    "Valleywise Health",
    "950 E. Van Buren Street",
    "Avondale, AZ, 85323",
    "8338559973 ext:",
    "GEORGE JOUMAS(M)",
    "Professional Counselor",
    "P0203168-270581",
    "Banner University Physician",
    "Specialists LLC",  # multi-line org
    "1300 N 12th St Ste 320",
    "Phoenix, AZ, 85006",
    "6025213600 ext:",
]


def test_parse_lines_aaneel():
    es = parse_lines_aaneel(AANEEL_LINES)
    assert len(es) == 2
    a = es[0]
    assert (a.name, a.last_name, a.first_name) == ("CHRISTIANA HINES", "HINES", "CHRISTIANA")
    assert a.specialty == "Professional Counselor"
    assert a.locations[0] == {"address": "950 E. Van Buren Street", "city": "Avondale", "state": "AZ", "zip": "85323"}
    b = es[1]
    assert b.name == "GEORGE JOUMAS" and b.last_name == "JOUMAS"  # (M) suffix stripped
    assert b.locations[0]["address"] == "1300 N 12th St Ste 320"  # line before city/state/zip, past multi-line org
    assert b.locations[0]["zip"] == "85006"


# --- matcher -----------------------------------------------------------------
def _rows(*tuples):
    return [
        {"last_name": ln, "first_name": fn, "full_name": f"{fn} {ln}", "state": st, "zip": z, "city": "CAPE CORAL"}
        for (ln, fn, st, z) in tuples
    ]


SCHMIDTS = _rows(("SCHMIDT", "JOHN", "FL", "33914"), ("SCHMIDT", "JOHN", "FL", "33909"))
BAKER = _rows(("BAKER", "KEITH", "FL", "33990"))


def test_match_zip_disambiguates_two_johns():
    st, m, c, _ = match_directory(SCHMIDTS, payer_label="Align", last_name="Schmidt", first_name="John", state="FL", zip_code="33914")
    assert st == NetworkStatus.IN_NETWORK and c == "high" and m["zip"] == "33914"


def test_match_two_johns_no_zip_is_unknown():
    st, m, c, _ = match_directory(SCHMIDTS, payer_label="Align", last_name="Schmidt", first_name="John", state="FL")
    assert st == NetworkStatus.UNKNOWN and m is None


def test_match_absent_surname_is_oon():
    st, _, _, _ = match_directory([], payer_label="Align", last_name="Nobody", state="FL")
    assert st == NetworkStatus.OUT_OF_NETWORK


def test_match_wrong_state_is_oon():
    st, _, _, _ = match_directory(BAKER, payer_label="Align", last_name="Baker", first_name="Keith", state="AZ")
    assert st == NetworkStatus.OUT_OF_NETWORK


def test_match_unique_in_network_high():
    st, m, c, _ = match_directory(BAKER, payer_label="Align", last_name="Baker", first_name="Keith", state="FL", zip_code="33990")
    assert st == NetworkStatus.IN_NETWORK and c == "high"


# --- adapter -----------------------------------------------------------------
def test_db_directory_adapter_attaches_our_npi():
    by_ln = {"SCHMIDT": SCHMIDTS}
    a = DbDirectoryAdapter(
        payer_name="align-senior-health-plan-fl-south-florida",
        payer_label="Align Senior Care",
        candidates_fn=lambda pk, ln: by_ln.get(_norm(ln), []),
    )
    q = ProviderQuery(payer="align", plan_hint="", npi="1234567890", first_name="John", last_name="Schmidt", state="FL", zip_code="33914")
    v = a.check_network(q)
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.matched_provider["npi"] == "1234567890"  # NPI comes from OUR side, not the directory


def test_db_directory_adapter_requires_name():
    a = DbDirectoryAdapter(payer_name="x", payer_label="X", candidates_fn=lambda pk, ln: [])
    v = a.check_network(ProviderQuery(payer="x", plan_hint="", npi="1234567890"))
    assert v.status == NetworkStatus.UNKNOWN
