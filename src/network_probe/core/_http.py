"""Small, polite, cached HTTP helper shared by adapters.

Why this exists (per the engineering rules in CLAUDE.md):
- send a real User-Agent,
- keep volume tiny and add a small delay between *live* calls,
- cache responses on disk during dev so we don't hammer the endpoint while iterating.

It is payer-agnostic: adapters just call client.get_json(url).
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)


class CachedClient:
    """Thin wrapper over httpx.Client with an optional on-disk JSON cache."""

    def __init__(
        self,
        cache_dir: str | None = ".cache",
        delay_seconds: float = 0.4,
        timeout: float = 20.0,
        user_agent: str = DEFAULT_UA,
        client: httpx.Client | None = None,
        use_proxy: bool = False,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay_seconds = delay_seconds
        # Outbound proxy is opt-in and used ONLY by non-PHI provider-directory clients (use_proxy=True)
        # to reach geo-IP-gated PUBLIC directories from local dev. The Stedi 270/271 (member PHI)
        # client never sets use_proxy, so PHI is never routed through a third-party proxy.
        proxy = None
        if client is None and use_proxy:
            try:
                from network_probe.core.config import get_settings

                proxy = get_settings().directory_proxy_url
            except Exception:
                proxy = None
        # `client` lets tests inject an httpx.Client(transport=MockTransport(...))
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"user-agent": user_agent, "accept": "application/json"},
            follow_redirects=True,
            proxy=proxy,
        )
        self._owns_client = client is None

    def _cache_path(self, url: str) -> Path | None:
        if not self.cache_dir:
            return None
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{digest}.json"

    def get_json(self, url: str, headers: dict | None = None) -> dict:
        cp = self._cache_path(url)
        if cp and cp.exists():
            with cp.open("r", encoding="utf-8") as fh:
                return json.load(fh)

        # live request: be polite
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        resp = self._client.get(url, headers=headers or {})
        resp.raise_for_status()
        data = resp.json()

        if cp:
            with cp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh)
        return data

    def post_json(self, url: str, content: str, headers: dict | None = None) -> dict:
        """POST a body and parse JSON. Cache key includes the body (for Algolia)."""
        cp = self._cache_path(url + "\n" + content)
        if cp and cp.exists():
            with cp.open("r", encoding="utf-8") as fh:
                return json.load(fh)

        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        resp = self._client.post(url, content=content, headers=headers or {})
        resp.raise_for_status()
        data = resp.json()

        if cp:
            with cp.open("w", encoding="utf-8") as fh:
                json.dump(data, fh)
        return data

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CachedClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
