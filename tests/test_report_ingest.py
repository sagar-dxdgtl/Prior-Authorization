"""Tests for pVerify 271 report parsing (Phase 1). Uses synthetic text mirroring the pVerify
layout (no PHI, no PDF dependency) so it runs in any checkout."""

from __future__ import annotations

from network_probe.domain.report_ingest import parse_report, report_to_query

# Mirrors the real pVerify text layout: interleaved query block + clean PLAN COVERAGE / DETAILED RESULT.
SYNTH = """QUERY CRITERIA
PAYER :
DOS :
05/20/2026
Oscar Health EDI
PROVIDER
SUBSCRIBER
DEPENDENT
Last :
Member ID :
DOB :
Relation :
OSC7672987101
02/01/1967
Herron
First :
Kyle
Grp NPI :
First :
First :
Clemencia
NPI :
1679766943
Fed Tax ID :
Last :
Last :
Ochoa
ELIGIBILITY RESULT
Provider Network :
Unknown
BASE SILVER CSR 150,SILVERSIMPLEPCPSAVER
Plan :
PLAN COVERAGE
Status                    : Active
Plan Name                 : BASE SILVER CSR 150,SILVERSIMPLEPCPSAVER
Policy Type               : HMO
Member ID                 : OSC7672987101
DETAILED RESULT
PAYER :                   Oscar Health EDI
SUBSCRIBER INFORMATION
Name                      : CLEMENCIA OCHOA
City-State-Zip            : WEST PALM BEACH-FL-334090000
Date Of Birth             : 02/01/1967
"""


def test_parse_core_fields():
    p = parse_report(SYNTH)
    assert p["payer_key"] == "oscar"
    assert p["npi"] == "1679766943"
    assert p["state"] == "FL" and p["zip"] == "33409"
    assert p["provider_first"] == "Kyle" and p["provider_last"] == "Herron"
    assert p["plan_name"].startswith("BASE SILVER CSR 150")
    assert p["policy_type"] == "HMO"


def test_report_to_query_uses_report_name_not_nppes():
    p = parse_report(SYNTH)
    q = report_to_query(p, client=None)  # name comes from the report → NPPES not called
    assert q.payer == "oscar" and q.npi == "1679766943"
    assert q.last_name == "Herron" and q.state == "FL" and q.zip_code == "33409"
    assert "SILVERSIMPLEPCPSAVER" in q.plan_hint


def test_payer_mapping():
    for needle, key in [
        ("Cigna", "cigna-fhir"),
        ("Humana", "humana-fhir"),
        ("United Healthcare", "uhc"),
        ("DEVOTED HEALTH", "devoted"),
    ]:
        txt = SYNTH.replace("Oscar Health EDI", needle)
        assert parse_report(txt)["payer_key"] == key
