"""OAuth2 client-credentials layer for token-gated FHIR directories (Anthem/Elevance).

Offline tests drive the token flow + a full network verdict through httpx.MockTransport (no
network, no real creds). The live test (`-m live`) reads ANTHEM_FHIR_* from .env and hits the real
Elevance CMS-mandate PDEX endpoint for one non-PHI Practitioner lookup.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.payers.adapters.fhir_auth import (
    OAuth2ClientCredentials,
    build_authed_fhir_adapter,
)

TOKEN_URL = "https://auth.example.org/oauth2/token"
FHIR_BASE = "https://fhir.example.org/providerdirectory"
NPI = "1234567893"
PID = "anthem-pid-1"
NET_EXT = "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/network-reference"


# ---- token endpoint mock ----------------------------------------------------


def _token_transport(counter: dict, *, ttl=3600, omit_token=False):
    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        body = parse_qs(request.content.decode())
        # client-credentials grant must be form-encoded with the id+secret in the body
        assert body.get("grant_type") == ["client_credentials"], body
        assert body.get("client_id") == ["cid"], body
        assert body.get("client_secret") == ["secret"], body
        if omit_token:
            return httpx.Response(200, json={"token_type": "bearer", "expires_in": ttl})
        return httpx.Response(
            200, json={"access_token": f"tok-{counter['n']}", "token_type": "bearer", "expires_in": ttl}
        )

    return httpx.MockTransport(handler)


# ---- FHIR PDEX server mock (inline network-reference display, NPI identifier search) ---------


def _fhir_transport(reject_tokens: set[str] | None = None):
    reject = reject_tokens or set()

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not token or token in reject:
            return httpx.Response(401, json={"resourceType": "OperationOutcome"})
        u = urlsplit(str(request.url))
        qs = parse_qs(u.query)
        if u.path.endswith("/Practitioner"):
            if (qs.get("identifier") or [""])[0] == NPI:
                return httpx.Response(
                    200,
                    json={
                        "resourceType": "Bundle",
                        "entry": [
                            {
                                "resource": {
                                    "resourceType": "Practitioner",
                                    "id": PID,
                                    "name": [{"text": "Pat Anthem MD"}],
                                    "identifier": [{"system": "http://hl7.org/fhir/sid/us-npi", "value": NPI}],
                                }
                            }
                        ],
                    },
                )
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})
        if u.path.endswith("/PractitionerRole"):
            if (qs.get("practitioner") or [""])[0] == PID:
                return httpx.Response(
                    200,
                    json={
                        "resourceType": "Bundle",
                        "entry": [
                            {
                                "resource": {
                                    "resourceType": "PractitionerRole",
                                    "id": "r1",
                                    "extension": [{"url": NET_EXT, "valueReference": {"display": "Blue Priority PPO"}}],
                                    "specialty": [{"coding": [{"display": "Cardiology"}]}],
                                }
                            }
                        ],
                    },
                )
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def _adapter(*, token_counter, reject_tokens=None, ttl=3600):
    return build_authed_fhir_adapter(
        payer_key="anthem",
        base_url=FHIR_BASE,
        token_url=TOKEN_URL,
        client_id="cid",
        client_secret="secret",
        verify_urls=False,  # fake hosts; skip the SSRF DNS check
        cache_dir=None,
        token_transport=_token_transport(token_counter, ttl=ttl),
        fhir_transport=_fhir_transport(reject_tokens),
    )


# ---- token provider unit behaviour ------------------------------------------


def test_token_is_cached_within_ttl():
    counter = {"n": 0}
    auth = OAuth2ClientCredentials(
        TOKEN_URL, "cid", "secret", verify_url=False, transport=_token_transport(counter)
    )
    assert auth.token() == "tok-1"
    assert auth.token() == "tok-1"  # cached — no second POST
    assert counter["n"] == 1


def test_invalidate_forces_new_token():
    counter = {"n": 0}
    auth = OAuth2ClientCredentials(
        TOKEN_URL, "cid", "secret", verify_url=False, transport=_token_transport(counter)
    )
    assert auth.token() == "tok-1"
    auth.invalidate()
    assert auth.token() == "tok-2"
    assert counter["n"] == 2


def test_token_refetched_after_expiry(monkeypatch):
    counter = {"n": 0}
    # ttl=60 with the 60s refresh leeway means the cached token is always considered due → refetch.
    auth = OAuth2ClientCredentials(
        TOKEN_URL, "cid", "secret", verify_url=False, transport=_token_transport(counter, ttl=60)
    )
    assert auth.token() == "tok-1"
    assert auth.token() == "tok-2"  # past (expiry - leeway) → refreshed
    assert counter["n"] == 2


def test_missing_access_token_raises():
    counter = {"n": 0}
    auth = OAuth2ClientCredentials(
        TOKEN_URL, "cid", "secret", verify_url=False, transport=_token_transport(counter, omit_token=True)
    )
    with pytest.raises(ValueError, match="no access_token"):
        auth.token()


# ---- full verdict through the authed adapter --------------------------------


def test_in_network_through_authed_adapter():
    counter = {"n": 0}
    a = _adapter(token_counter=counter)
    v = a.check_network(ProviderQuery(payer="anthem", plan_hint="Blue Priority", npi=NPI, provider_last_name="Anthem"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.matched_provider["matched_network"] == "Blue Priority PPO"
    assert counter["n"] == 1  # one token served, then reused across Practitioner + PractitionerRole


def test_unknown_npi_is_out_of_network_through_authed_adapter():
    a = _adapter(token_counter={"n": 0})
    v = a.check_network(ProviderQuery(payer="anthem", plan_hint="Blue Priority", npi="1999999999"))
    assert v.status == NetworkStatus.OUT_OF_NETWORK


def test_401_triggers_one_refresh_and_succeeds():
    # FHIR server rejects the first token (tok-1) → auth flow refreshes to tok-2 → request succeeds.
    counter = {"n": 0}
    a = _adapter(token_counter=counter, reject_tokens={"tok-1"})
    v = a.check_network(ProviderQuery(payer="anthem", plan_hint="Blue Priority", npi=NPI, provider_last_name="Anthem"))
    assert v.status == NetworkStatus.IN_NETWORK
    assert counter["n"] >= 2  # refreshed at least once after the 401


# ---- live (real Elevance CMS-mandate PDEX endpoint, OAuth2) ------------------


@pytest.mark.live
def test_anthem_live_practitioner_lookup():
    """One non-PHI provider-directory lookup against the real Elevance endpoint using .env creds."""
    from network_probe.core.config import get_settings

    s = get_settings()
    if not s.anthem_fhir_ready:
        pytest.skip("ANTHEM_FHIR_* not configured in .env")
    a = build_authed_fhir_adapter(
        payer_key="anthem",
        base_url=s.anthem_fhir_base_url,
        token_url=s.anthem_fhir_token_url,
        client_id=s.anthem_fhir_client_id,
        client_secret=s.anthem_fhir_client_secret,
        scope=s.anthem_fhir_scope,
        cache_dir=None,
    )
    # A provider known to be present in the Elevance directory (verified via discovery probe).
    live_npi = os.environ.get("ANTHEM_LIVE_NPI", "1023054806")  # 'John D Smith, MD' (GA Medicaid networks)
    try:
        v = a.check_network(ProviderQuery(payer="anthem", plan_hint="", npi=live_npi, provider_last_name="Smith"))
    except httpx.HTTPError as exc:
        pytest.skip(f"live Anthem FHIR unreachable: {exc}")
    assert v.status == NetworkStatus.IN_NETWORK, v.notes
    assert v.matched_provider["networks"], "expected at least one network for a listed provider"
