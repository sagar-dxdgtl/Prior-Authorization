"""PDF-directory subsystem: line parser, name+state+zip matcher, and the DB-directory adapter.

No PDF or DB needed — the parser is tested on synthetic lines (mirroring the real Align layout,
validated live at 53,787 rows), the matcher on row dicts, and the adapter via an injected
candidates_fn. The live download + 53k-row load runs in the deployment (ENABLE_DIRECTORY_REFRESH).
"""

from __future__ import annotations

import httpx
import pytest

from network_probe.domain import directory_load
from network_probe.domain.directory_match import _norm, match_directory
from network_probe.domain.directory_pdf import parse_lines, parse_lines_aaneel, parse_lines_ccp
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


# Community Care Plan (FL Medicaid) layout: each record is fully self-contained (no shared
# multi-location name header like allyalign), surname-first names, and a running 3-line page
# header ("<section>" / "<COUNTY>" / "N of M") that PyMuPDF re-extracts on every page. Transcribed
# verbatim from the live Broward PDF (docs/superpowers/specs/2026-07-15-community-care-plan-pdf-design.md).
CCP_LINES = [
    "PCP - ADOLESCENT MEDICINE",
    "BROWARD",
    "4 of 1933",
    "FLORENT-CARRE MARIE",
    "ADOLESCENT MEDICINE",
    "9241 W BROWARD BLVD",
    "PLANTATION, FL 33324",
    "Phone: 9542624100",
    "Office Hours: M-F 8:00-5:00p",
    "Gender Accepted: All",
    "Cultural Competence: Yes",
    "WheelChair Accessible: Yes",
    "Board Certification: No",
    "Accepting New Patients: Yes",
    "Age Limitations: 18Y-99Y",
    "Website:",
    "Performance Indicator: Not yet rated",
    "IGLESIAS ELBA AMALIA",
    "ADOLESCENT MEDICINE",
    "1150 N 35TH AVE 560",
    "HOLLYWOOD, FL 33021",
    "Phone: 9542651460",
    "Office Hours: M-F 8:00-5:00p",
    "Gender Accepted: All",
    "Cultural Competence: Yes",
    "WheelChair Accessible: Yes",
    "Board Certification: No",
    "Accepting New Patients: Yes",
    "Age Limitations: 00Y-21Y",
    "Website:",
    "Performance Indicator: Not yet rated",
    "PCP - ADOLESCENT MEDICINE",
    "BROWARD",
    "5 of 1933",
    "FLORENT-CARRE MARIE",
    "ADOLESCENT MEDICINE",
    "3200 S UNIVERSITY DR",
    "DAVIE, FL 33328",
    "Phone: 9542624100",
    "Office Hours: M-F 8:00-5:00p ; Sa 9:00-2:00",
    "Gender Accepted: All",
    "Cultural Competence: Yes",
    "WheelChair Accessible: Yes",
    "Board Certification: No",
    "Accepting New Patients: Yes",
    "Age Limitations: 18Y-99Y",
    "Website:",
    "Performance Indicator: Not yet rated",
]


def test_parse_lines_ccp_extracts_records_surname_first():
    es = parse_lines_ccp(CCP_LINES)
    assert len(es) == 3
    e = es[0]
    assert e.name == "FLORENT-CARRE MARIE"
    # CCP is surname-first: "FLORENT-CARRE MARIE" -> last="FLORENT-CARRE", first="MARIE" --
    # the OPPOSITE of allyalign's _split_name(), confirmed against a real match (this client's
    # own physician Desiree Clarke appears in the live Palm Beach PDF as "CLARKE DESIREE").
    assert e.last_name == "FLORENT-CARRE"
    assert e.first_name == "MARIE"
    assert e.specialty == "ADOLESCENT MEDICINE"
    assert e.accepting_new is True
    assert e.locations[0] == {
        "address": "9241 W BROWARD BLVD",
        "city": "PLANTATION",
        "state": "FL",
        "zip": "33324",
    }
    iglesias = es[1]
    assert iglesias.last_name == "IGLESIAS"
    assert iglesias.first_name == "ELBA AMALIA"


def test_parse_lines_ccp_two_locations_are_two_entries():
    """A provider at 2 addresses is 2 full separate records in CCP's PDF (unlike allyalign, which
    shares one name header with a multi-location list) -- confirmed live: FLORENT-CARRE MARIE
    appears as two complete, separate blocks in the real Broward PDF."""
    es = parse_lines_ccp(CCP_LINES)
    florent = [e for e in es if e.name == "FLORENT-CARRE MARIE"]
    assert len(florent) == 2
    assert len(florent[0].locations) == 1
    assert len(florent[1].locations) == 1
    assert {florent[0].locations[0]["zip"], florent[1].locations[0]["zip"]} == {"33324", "33328"}


def test_parse_lines_ccp_strips_page_header_mid_stream():
    """The 3-line running header ("PCP - ADOLESCENT MEDICINE" / "BROWARD" / "5 of 1933") appears
    a second time in CCP_LINES, between the 2nd and 3rd records -- must not be misread as part of
    a record or leak into any field."""
    es = parse_lines_ccp(CCP_LINES)
    for e in es:
        assert "BROWARD" not in e.name
        assert "of 1933" not in e.name


# --- directory_load.py: multi-URL support (no existing coverage before this task) -------------


def test_load_directory_concatenates_multiple_urls(monkeypatch):
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-multi",
        {"label": "Test", "format": "ccp", "pdf_urls": ["https://x/a.pdf", "https://x/b.pdf", "https://x/c.pdf"]},
    )
    monkeypatch.setattr(directory_load, "download_pdf", lambda url, timeout=180.0: b"fake-pdf-bytes")
    call_rows = iter([[{"a": 1}, {"a": 2}], [{"a": 3}], [{"a": 4}, {"a": 5}, {"a": 6}]])
    monkeypatch.setattr(
        directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": next(call_rows)
    )
    replaced = {}
    monkeypatch.setattr(
        directory_load,
        "_replace_rows",
        lambda payer_key, rows, engine=None: replaced.update(payer_key=payer_key, rows=rows),
    )
    n = directory_load.load_directory("test-multi")
    assert n == 6
    assert replaced["payer_key"] == "test-multi"
    assert replaced["rows"] == [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}, {"a": 5}, {"a": 6}]


def test_load_directory_aborts_on_partial_failure(monkeypatch):
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-multi-fail",
        {"label": "Test", "format": "ccp", "pdf_urls": ["https://x/a.pdf", "https://x/BAD.pdf", "https://x/c.pdf"]},
    )

    def fake_download(url, timeout=180.0):
        if "BAD" in url:
            raise httpx.HTTPError("boom")
        return b"fake-pdf-bytes"

    monkeypatch.setattr(directory_load, "download_pdf", fake_download)
    monkeypatch.setattr(
        directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": [{"a": 1}]
    )
    replace_called = []
    monkeypatch.setattr(
        directory_load, "_replace_rows", lambda payer_key, rows, engine=None: replace_called.append(True)
    )
    with pytest.raises(httpx.HTTPError):
        directory_load.load_directory("test-multi-fail")
    assert replace_called == [], "must not replace rows on partial failure"


def test_resolve_pdf_urls_singular_config_returns_one_item_list():
    cfg = {"pdf_url": "https://example.org/one.pdf"}
    assert directory_load.resolve_pdf_urls(cfg) == ["https://example.org/one.pdf"]


def test_resolve_pdf_urls_plural_config_returns_all_items():
    cfg = {"pdf_urls": ["https://example.org/a.pdf", "https://example.org/b.pdf"]}
    assert directory_load.resolve_pdf_urls(cfg) == ["https://example.org/a.pdf", "https://example.org/b.pdf"]


def test_load_directory_aborts_on_empty_url_result(monkeypatch):
    """A PDF that parses successfully but yields zero rows (a structure-drift failure mode, not
    an exception) must abort the whole load, not silently replace the payer's directory with a
    partial set from the other URLs."""
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-multi-empty",
        {"label": "Test", "format": "ccp", "pdf_urls": ["https://x/a.pdf", "https://x/b.pdf", "https://x/c.pdf"]},
    )
    monkeypatch.setattr(directory_load, "download_pdf", lambda url, timeout=180.0: b"fake-pdf-bytes")
    call_rows = iter([[{"a": 1}, {"a": 2}], [], [{"a": 3}]])  # the 2nd URL yields zero rows
    monkeypatch.setattr(
        directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": next(call_rows)
    )
    replace_called = []
    monkeypatch.setattr(
        directory_load, "_replace_rows", lambda payer_key, rows, engine=None: replace_called.append(True)
    )
    with pytest.raises(ValueError, match="zero rows"):
        directory_load.load_directory("test-multi-empty")
    assert replace_called == [], "must not replace rows when a URL yields zero rows"


def test_load_directory_aborts_on_empty_single_url_result(monkeypatch):
    """Single-URL payers (Align, EternalHealth) go through the same loop as multi-URL payers --
    confirm they get the identical protection, not just the multi-URL case."""
    monkeypatch.setitem(
        directory_load.PDF_DIRECTORIES,
        "test-single-empty",
        {"label": "Test", "format": "allyalign", "pdf_url": "https://x/only.pdf"},
    )
    monkeypatch.setattr(directory_load, "download_pdf", lambda url, timeout=180.0: b"fake-pdf-bytes")
    monkeypatch.setattr(directory_load, "rows_from_pdf", lambda path, payer_key, version, fmt="allyalign": [])
    replace_called = []
    monkeypatch.setattr(
        directory_load, "_replace_rows", lambda payer_key, rows, engine=None: replace_called.append(True)
    )
    with pytest.raises(ValueError, match="zero rows"):
        directory_load.load_directory("test-single-empty")
    assert replace_called == [], "must not replace rows when the only URL yields zero rows"


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
    q = ProviderQuery(
        payer="align",
        plan_hint="",
        npi="1234567890",
        provider_first_name="John",
        provider_last_name="Schmidt",
        state="FL",
        zip_code="33914",
    )
    v = a.check_network(q)
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.matched_provider["npi"] == "1234567890"  # NPI comes from OUR side, not the directory


def test_db_directory_adapter_requires_name():
    a = DbDirectoryAdapter(payer_name="x", payer_label="X", candidates_fn=lambda pk, ln: [])
    v = a.check_network(ProviderQuery(payer="x", plan_hint="", npi="1234567890"))
    assert v.status == NetworkStatus.UNKNOWN
