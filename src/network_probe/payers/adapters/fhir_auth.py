"""Auth layers for token-gated FHIR provider directories.

Some payers expose the SAME CMS-mandated PDEX Plan-Net API as the public payers, but behind a
credential instead of open access. Both builders here hand the generic ``FhirPdexAdapter`` an
authenticated ``CachedClient`` — so the directory-traversal logic (Practitioner → PractitionerRole
→ network match) stays completely payer-agnostic; only the transport gains a header:

- ``build_authed_fhir_adapter`` — OAuth2 client-credentials (Anthem/Elevance today). Fetches +
  caches a bearer token, attaches ``Authorization: Bearer``.
  Verified live against Elevance's directory: token endpoint takes form-encoded
  ``grant_type=client_credentials`` + ``client_id`` + ``client_secret`` and returns
  ``{access_token, token_type=bearer, expires_in}``; the FHIR base is a standard PDEX R4 server
  with inline ``network-reference`` displays and NPI ``identifier`` search.
- ``build_apikey_fhir_adapter`` — a single static request header, no token exchange (HCSC's
  `client_id` header today).
"""

from __future__ import annotations

import threading
import time

import httpx

from network_probe.api.netutil import assert_safe_url
from network_probe.core._http import DEFAULT_UA, CachedClient
from network_probe.payers.adapters.fhir_pdex import FhirPdexAdapter

#: refresh a token this many seconds before its stated expiry (clock skew + call latency margin)
TOKEN_REFRESH_LEEWAY = 60
#: fallback lifetime when the token response omits/!malforms expires_in
DEFAULT_TOKEN_TTL = 300.0


def _ttl_seconds(expires_in) -> float:
    try:
        ttl = float(expires_in)
        return ttl if ttl > 0 else DEFAULT_TOKEN_TTL
    except (TypeError, ValueError):
        return DEFAULT_TOKEN_TTL


class OAuth2ClientCredentials(httpx.Auth):
    """httpx auth flow: attach a cached bearer token; on a 401, force one refresh and retry once.

    Thread-safe token caching (a single adapter may be shared across requests). The token POST is
    made with a short-lived httpx client so the bearer is never written to the on-disk response
    cache used for FHIR reads.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        *,
        verify_url: bool = True,
        transport: httpx.BaseTransport | None = None,
    ):
        if verify_url:
            assert_safe_url(token_url)
        self._token_url = token_url
        self._form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if scope:
            self._form["scope"] = scope
        self._transport = transport  # injectable httpx.MockTransport for tests
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0

    def _fetch(self) -> None:
        with httpx.Client(
            timeout=25.0,
            transport=self._transport,
            headers={"user-agent": DEFAULT_UA, "accept": "application/json"},
        ) as c:
            resp = c.post(self._token_url, data=self._form)
            resp.raise_for_status()
            body = resp.json()
        token = body.get("access_token")
        if not token:
            raise ValueError("OAuth2 token endpoint returned no access_token")
        self._token = token
        self._expires_at = time.time() + _ttl_seconds(body.get("expires_in"))

    def token(self, *, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or time.time() >= self._expires_at - TOKEN_REFRESH_LEEWAY:
                self._fetch()
            return self._token  # type: ignore[return-value]

    def invalidate(self) -> None:
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.token()}"
        response = yield request
        if response.status_code == 401:
            # token rejected (expired early / revoked) — drop it, get a fresh one, retry once.
            self.invalidate()
            request.headers["Authorization"] = f"Bearer {self.token(force=True)}"
            yield request


def build_authed_fhir_adapter(
    payer_key: str,
    base_url: str,
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str | None = None,
    *,
    year: int | None = None,
    verify_urls: bool = True,
    cache_dir: str | None = ".cache",
    token_transport: httpx.BaseTransport | None = None,
    fhir_transport: httpx.BaseTransport | None = None,
) -> FhirPdexAdapter:
    """Wire an OAuth2-gated PDEX directory: bearer-token transport + the generic FhirPdexAdapter.

    ``*_transport`` args inject httpx.MockTransport in tests; left None they use the real network.
    """
    if verify_urls:
        assert_safe_url(base_url)
    auth = OAuth2ClientCredentials(
        token_url, client_id, client_secret, scope, verify_url=verify_urls, transport=token_transport
    )
    http = httpx.Client(
        timeout=20.0,
        headers={"user-agent": DEFAULT_UA, "accept": "application/fhir+json"},
        auth=auth,
        follow_redirects=True,
        transport=fhir_transport,
    )
    client = CachedClient(cache_dir=cache_dir, client=http)
    return FhirPdexAdapter(base_url=base_url, payer_name=payer_key, year=year, client=client)


def build_apikey_fhir_adapter(
    payer_key: str,
    base_url: str,
    header_name: str,
    header_value: str,
    *,
    year: int | None = None,
    verify_url: bool = True,
    cache_dir: str | None = ".cache",
    transport: httpx.BaseTransport | None = None,
) -> FhirPdexAdapter:
    """Wire a static-API-key-gated PDEX directory: one constant request header, no token exchange.

    Simpler than ``build_authed_fhir_adapter`` — the header is just baked into the httpx.Client's
    default headers instead of an ``httpx.Auth`` flow (no expiry/refresh to manage).

    ``transport`` injects httpx.MockTransport in tests; left None it uses the real network.
    """
    if verify_url:
        assert_safe_url(base_url)
    http = httpx.Client(
        timeout=20.0,
        headers={"user-agent": DEFAULT_UA, "accept": "application/fhir+json", header_name: header_value},
        follow_redirects=True,
        transport=transport,
    )
    client = CachedClient(cache_dir=cache_dir, client=http)
    return FhirPdexAdapter(base_url=base_url, payer_name=payer_key, year=year, client=client)
