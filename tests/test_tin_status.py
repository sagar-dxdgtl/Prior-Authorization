"""Verified TIN-level network status book (payer TIN portal facts) and its use by TinScopeSource."""
from __future__ import annotations

from network_probe.corroboration import TinScopeSource
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.tin_status import TinStatus, TinStatusBook, default_tin_status


def _oon_verdict():
    return NetworkVerdict(NetworkStatus.OUT_OF_NETWORK, {"npi": "1184610453", "name": "Kiang"},
                          "cigna-fhir", "u", "high", "not listed")


def test_seed_has_cigna_wazni_record():
    s = default_tin_status().lookup("cigna-fhir", "1184610453", "463812940")
    assert s is not None and s.status == "OUT_OF_NETWORK" and s.group == "Wazni PLLC"


def test_lookup_normalizes_tin_formatting():
    book = TinStatusBook(records=[TinStatus("cigna-fhir", "1184610453", "463812940", "OUT_OF_NETWORK")])
    assert book.lookup("cigna-fhir", "1184610453", "46-3812940") is not None
    assert book.lookup("cigna-fhir", "1184610453", "000000000") is None


def test_lookup_is_payer_and_npi_scoped():
    book = default_tin_status()
    assert book.lookup("oscar", "1184610453", "463812940") is None        # wrong payer
    assert book.lookup("cigna-fhir", "9999999999", "463812940") is None   # wrong npi


def test_tinscope_reports_verified_status_even_when_oon():
    q = ProviderQuery(payer="cigna-fhir", plan_hint="", npi="1184610453", last_name="Kiang", tin="463812940")
    sig = TinScopeSource().check(q, _oon_verdict())
    assert sig.result == "corroborates"
    assert "Cigna Network Status portal" in sig.detail


def test_seed_has_uhc_srinivas_rao_oon_record():
    s = default_tin_status().lookup("uhc", "1972941318", "412049581")
    assert s is not None and s.status == "OUT_OF_NETWORK"
    assert "Srinivas Rao" in (s.group or "") and "Transparency-in-Coverage" in (s.source or "")


def test_tinscope_reports_uhc_tic_oon_even_when_provider_unlisted():
    # provider isn't in the UHC directory (0 occurrences), yet the verified TiC TIN status still
    # yields a real group-level OON answer that corroborates the absence-based verdict.
    verdict = NetworkVerdict(NetworkStatus.OUT_OF_NETWORK, None, "uhc", "u", "high", "not listed")
    q = ProviderQuery(payer="uhc", plan_hint="", npi="1972941318", last_name="Rao", tin="412049581")
    sig = TinScopeSource().check(q, verdict)
    assert sig.result == "corroborates"
    assert "OUT-OF-NETWORK" in sig.detail and "Transparency-in-Coverage" in sig.detail
