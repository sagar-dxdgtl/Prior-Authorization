import json

import httpx

from network_probe.core._http import CachedClient
from network_probe.domain.models import ProviderQuery
from network_probe.stedi.client import StediEligibilityClient, _dob


def _mock(json_body):
    def handler(req):
        # PHI must be in the BODY, not the URL
        assert b"member" not in req.url.raw_path.lower()
        return httpx.Response(200, json=json_body)

    return CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_parses_271():
    body = {"benefitsInformation": [{"code": "1", "serviceTypeCodes": ["30"]}]}
    c = StediEligibilityClient(api_key="k", client=_mock(body), payer_id="OSCAR")
    res = c.check(ProviderQuery(payer="oscar", plan_hint="", npi="1679766943", member_id="M1", dob="01/02/1980"))
    assert res.coverage_active is True


def test_no_key_returns_unknown():
    c = StediEligibilityClient(api_key="", payer_id="OSCAR")  # empty key = not configured
    res = c.check(ProviderQuery(payer="oscar", plan_hint="", npi="1"))
    assert res.coverage_active is None and res.network_status.value == "UNKNOWN"


def test_no_payer_id_returns_unknown():
    c = StediEligibilityClient(api_key="k", payer_id=None)
    res = c.check(ProviderQuery(payer="mystery", plan_hint="", npi="1"))
    assert res.coverage_active is None


def test_provider_body_includes_first_name():
    # Some payers reject a 270 with only NPI + provider lastName (AAA-44 "Provider Not Found") --
    # the provider's firstName must be sent too, same as the subscriber block already does.
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"benefitsInformation": []})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = StediEligibilityClient(api_key="k", client=client, payer_id="CIGNA")
    c.check(
        ProviderQuery(
            payer="cigna", plan_hint="", npi="1629339312", provider_first_name="Jing", provider_last_name="Li"
        )
    )
    assert captured["body"]["provider"]["firstName"] == "Jing"
    assert captured["body"]["provider"]["lastName"] == "Li"
    assert captured["body"]["provider"]["npi"] == "1629339312"


def test_dob_normalization():
    assert _dob("01/02/1980") == "19800102"
    assert _dob("1980-01-02") == "19800102"
    assert _dob(None) is None


def test_client_reads_stedi_key_from_settings(monkeypatch):
    # the key lives in .env (loaded by Settings), NOT os.environ — the client must read it from Settings
    class _S:
        stedi_api_key = "env-key"
        stedi_eligibility_url = "https://example/elig"
    monkeypatch.setattr("network_probe.stedi.client.get_settings", lambda: _S())
    from network_probe.stedi.client import StediEligibilityClient
    assert StediEligibilityClient().api_key == "env-key"
