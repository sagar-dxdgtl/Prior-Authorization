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


def test_retries_without_subscriber_name_on_aaa73():
    # UHC (and others) reject memberId+name+DOB with AAA-73 when the name doesn't exactly match their
    # records, but accept memberId+DOB. The client must drop the name and retry rather than fail --
    # this is what unbroke live eligibility for the demo roster.
    calls = []

    def handler(req):
        sub = json.loads(req.content)["subscriber"]
        calls.append(sub)
        if sub.get("firstName") or sub.get("lastName"):
            return httpx.Response(200, json={"errors": [
                {"code": "73", "location": "Loop 2100C", "description": "Invalid/Missing Subscriber/Insured Name"}]})
        return httpx.Response(200, json={"benefitsInformation": [{"code": "1", "serviceTypeCodes": ["30"]}]})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = StediEligibilityClient(api_key="k", client=client, payer_id="87726")
    res = c.check(ProviderQuery(payer="uhc", plan_hint="", member_id="M1", dob="09/05/1946",
                                first_name="Lisa", last_name="Desormeaux", npi="123"))
    assert res.coverage_active is True                  # retry with memberId+DOB succeeded
    assert len(calls) == 2                              # first attempt carried the name, second dropped it
    assert calls[0].get("lastName") == "Desormeaux"
    assert "firstName" not in calls[1] and "lastName" not in calls[1]


def test_strips_member_id_suffix_on_aaa72():
    # Oscar (and others) reject "OSC79685899-01" with AAA-72 -- the "-01" is a dependent/sequence
    # suffix, not part of the member ID. The client must strip a trailing -NN and retry.
    calls = []

    def handler(req):
        m = json.loads(req.content)["subscriber"]["memberId"]
        calls.append(m)
        if m == "OSC79685899":
            return httpx.Response(200, json={"benefitsInformation": [{"code": "1", "serviceTypeCodes": ["30"]}]})
        return httpx.Response(200, json={"errors": [
            {"code": "72", "description": "Invalid/Missing Subscriber/Insured ID"}]})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = StediEligibilityClient(api_key="k", client=client, payer_id="OSCAR")
    res = c.check(ProviderQuery(payer="oscar", plan_hint="", member_id="OSC79685899-01", dob="01/21/1962", npi="1"))
    assert res.coverage_active is True
    assert "OSC79685899" in calls  # retried with the -01 suffix stripped


def test_no_retry_when_no_name_was_sent():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"benefitsInformation": [{"code": "1", "serviceTypeCodes": ["30"]}]})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = StediEligibilityClient(api_key="k", client=client, payer_id="87726")
    c.check(ProviderQuery(payer="uhc", plan_hint="", member_id="M1", dob="01/02/1980", npi="1"))
    assert len(calls) == 1  # no name to drop -> single call


def test_no_retry_on_non_identity_error():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"errors": [{"code": "79", "description": "Invalid/Missing Provider Identification"}]})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = StediEligibilityClient(api_key="k", client=client, payer_id="87726")
    res = c.check(ProviderQuery(payer="uhc", plan_hint="", member_id="M1", dob="01/02/1980",
                                first_name="A", last_name="B", npi="1"))
    assert len(calls) == 1  # code 79 is not a name problem -> no retry
    assert res.coverage_active is None


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


def test_provider_org_name_fallback_when_no_provider_name():
    # Stedi v3 rejects a 270 whose provider loop has neither organizationName nor lastName
    # ("Missing required field: provider organizationName or lastName is required"). The
    # eligibility form supplies only an NPI, so the client must add organizationName so every
    # check validates.
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"benefitsInformation": []})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = StediEligibilityClient(api_key="k", client=client, payer_id="DEVOT")
    c.check(ProviderQuery(payer="devoted", plan_hint="", npi="1720209885", member_id="M1", dob="01/02/1980"))
    prov = captured["body"]["provider"]
    assert prov["npi"] == "1720209885"
    assert prov.get("organizationName")  # present because no provider first/last name was given


def test_no_org_name_when_provider_last_name_present():
    # When a provider lastName IS supplied, that satisfies Stedi's requirement — don't also
    # send a placeholder organizationName.
    captured = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json={"benefitsInformation": []})

    client = CachedClient(cache_dir=None, delay_seconds=0, client=httpx.Client(transport=httpx.MockTransport(handler)))
    c = StediEligibilityClient(api_key="k", client=client, payer_id="CIGNA")
    c.check(ProviderQuery(payer="cigna", plan_hint="", npi="1629339312", provider_first_name="Jing", provider_last_name="Li"))
    assert "organizationName" not in captured["body"]["provider"]


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
