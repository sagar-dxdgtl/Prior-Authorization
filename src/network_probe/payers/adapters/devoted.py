"""Devoted Health adapter.

Built against the contract in DISCOVERY.md (reverse-engineered 2026-06-22 from
devoted.com/search-providers, guest access).

Devoted's directory is an Algolia index (`provider_directory_quality_score_ranked`)
served with a PUBLIC, read-only InstantSearch key embedded in the page. Network
scoping is a single filter: `NetworkNames:"<STATE> <PLANTYPE>"` (e.g. "FL HMO",
"TX PPO CSNP"), plus `DirectoryYear`.

Flow (see DISCOVERY.md §"Devoted verdict logic"):
  1. resolve state + plan_hint -> NetworkNames value (validated against the live facet)
  2. Algolia query=<NPI>, filtered to that network + year
  3. keep hits whose Npi *exactly* equals the target (query=NPI returns only exact
     records, but we filter defensively)
  4a. exact hit in-network -> IN_NETWORK
  4b. not in this network but the NPI exists elsewhere in Devoted -> OUT_OF_NETWORK
  4c. NPI not in Devoted's directory at all -> OUT_OF_NETWORK (medium; NPs/PAs may be
      in-network yet unlisted, per Devoted's own notice)

Honesty rule: ambiguity (unresolvable plan, no NPI + ambiguous name) -> UNKNOWN.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from urllib.parse import urlencode

from network_probe.core._http import CachedClient
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.payers.adapters.base import PayerAdapter

# Public InstantSearch credentials (embedded in the page by design — not secrets).
# Overridable via env in case Devoted rotates them. See DISCOVERY.md.
DEFAULT_APP_ID = "EN2FBM9O9O"
DEFAULT_API_KEY = (
    "MTZkNmJmNWViZGYzMjdkYTU1NzI0YjE3MDhlM2ZjM2MzZWEwYjU3OGI4YmVjNWQ0MmUxOGM0N2E1M2Uw"
    "NWZkNmF0dHJpYnV0ZXNUb1JldHJpZXZlPSU1QiUyMiUyQSUyMiUyQyUyMi1pbnRlcm5hbFVzZU9ubHkl"
    "MjIlNUQ="
)
INDEX = "provider_directory_quality_score_ranked"

# Attributes we need back from each hit.
_ATTRS = [
    "ProviderName",
    "Npi",
    "Npis",
    "NetworkNames",
    "NetworkCountyCodes",
    "DirectorySpecialty",
    "AddressCity",
    "AddressState",
]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class DevotedAdapter(PayerAdapter):
    payer_name = "devoted"

    def __init__(
        self,
        year: int | None = None,
        client: CachedClient | None = None,
        app_id: str | None = None,
        api_key: str | None = None,
        today: date | None = None,
    ):
        self._today = today or date.today()
        self.year = year or self._today.year
        self.client = client or CachedClient(use_proxy=True)
        self.app_id = app_id or os.environ.get("DEVOTED_ALGOLIA_APP_ID", DEFAULT_APP_ID)
        self.api_key = api_key or os.environ.get("DEVOTED_ALGOLIA_API_KEY", DEFAULT_API_KEY)

    # ---- Algolia plumbing ---------------------------------------------------

    @property
    def _url(self) -> str:
        return f"https://{self.app_id.lower()}-dsn.algolia.net/1/indexes/*/queries"

    def _query(self, params: dict) -> dict:
        """Run one Algolia query; return the single result object."""
        param_str = urlencode(params)
        body = json.dumps({"requests": [{"indexName": INDEX, "params": param_str}]})
        headers = {
            "x-algolia-application-id": self.app_id,
            "x-algolia-api-key": self.api_key,
            "content-type": "application/x-www-form-urlencoded",
        }
        data = self.client.post_json(self._url, content=body, headers=headers)
        return data["results"][0]

    def _network_names(self, state: str) -> set[str]:
        """All NetworkNames facet values for `state` in self.year (e.g. {'FL HMO', ...})."""
        res = self._query(
            {
                "query": "",
                "hitsPerPage": 0,
                "facets": json.dumps(["NetworkNames"]),
                "maxValuesPerFacet": 300,
                "filters": f'DirectoryYear:"{self.year}"',
            }
        )
        facet = (res.get("facets") or {}).get("NetworkNames", {})
        prefix = f"{state.upper()} "
        return {name for name in facet if name.upper().startswith(prefix)}

    def _search_npi(self, npi: str, network_name: str | None) -> list[dict]:
        """query=<npi>, filtered to year (+ network if given). Returns exact-NPI hits."""
        filt = [f'DirectoryYear:"{self.year}"']
        if network_name:
            filt.append(f'NetworkNames:"{network_name}"')
        res = self._query(
            {
                "query": npi,
                "filters": " AND ".join(filt),
                "hitsPerPage": 50,
                "attributesToRetrieve": json.dumps(_ATTRS),
            }
        )
        out = []
        for h in res.get("hits", []):
            if h.get("Npi") == npi or npi in (h.get("Npis") or []):
                out.append(h)
        return out

    # ---- network resolution -------------------------------------------------

    def resolve_network(self, plan_hint: str, state: str) -> str | None:
        """Map state + plan_hint -> a valid NetworkNames value, or None.

        Devoted's 'network' is "<STATE> <PLANTYPE>[ CSNP|DSNP]". We detect the plan
        type from the hint and validate the composed name against the live facet so
        we never query a network that doesn't exist.
        """
        if not state:
            return None
        h = _norm(plan_hint)
        ptype = "PPO" if "ppo" in h else ("HMO" if "hmo" in h else None)
        if not ptype:
            return None
        snp = "DSNP" if "dsnp" in h else ("CSNP" if "csnp" in h else "")

        valid = self._network_names(state)
        candidates = []
        if snp:
            candidates.append(f"{state.upper()} {ptype} {snp}")
        candidates.append(f"{state.upper()} {ptype}")
        for c in candidates:
            if c in valid:
                return c
        return None

    # ---- main entrypoint ----------------------------------------------------

    def check_network(self, q: ProviderQuery) -> NetworkVerdict:
        state = (q.state or "").upper()
        net = self.resolve_network(q.plan_hint, state)
        base_src = self._url
        if not net:
            return NetworkVerdict(
                status=NetworkStatus.UNKNOWN,
                matched_provider=None,
                plan_or_network_checked=f"{q.plan_hint} ({state})",
                source_url=base_src,
                confidence="low",
                notes=(
                    f"Could not map plan_hint {q.plan_hint!r} + state {state!r} to a Devoted "
                    f"network for {self.year} (need a plan type: HMO/PPO, optionally C-SNP/D-SNP). "
                    f"Refusing to guess."
                ),
            )
        checked = f"Devoted {net} ({self.year})"

        # Devoted name search is fuzzy and returns duplicate location records, so NPI
        # is the reliable identity key. Require it (UNKNOWN, not a guess, if missing).
        if not q.npi:
            return NetworkVerdict(
                status=NetworkStatus.UNKNOWN,
                matched_provider=None,
                plan_or_network_checked=checked,
                source_url=base_src,
                confidence="low",
                notes="An NPI is required for a reliable Devoted match (name search is fuzzy).",
            )

        in_net = self._search_npi(q.npi, net)
        if in_net:
            h = in_net[0]
            counties = h.get("NetworkCountyCodes") or []
            matched = {
                "npi": q.npi,
                "name": h.get("ProviderName"),
                "specialty": h.get("DirectorySpecialty"),
                "city": h.get("AddressCity"),
                "network_names": h.get("NetworkNames"),
                "location_records": len(in_net),
            }
            return NetworkVerdict(
                status=NetworkStatus.IN_NETWORK,
                matched_provider=matched,
                plan_or_network_checked=checked,
                source_url=base_src,
                confidence="high",
                notes=(
                    f"NPI {q.npi} ({h.get('ProviderName')}) found in {net} for {self.year} "
                    f"across {len(in_net)} location record(s), covering {len(counties)} county code(s)."
                ),
            )

        # Not in this network — is the NPI anywhere in Devoted's directory this year?
        elsewhere = self._search_npi(q.npi, None)
        if elsewhere:
            nets = sorted({n for h in elsewhere for n in (h.get("NetworkNames") or [])})
            return NetworkVerdict(
                status=NetworkStatus.OUT_OF_NETWORK,
                matched_provider={"npi": q.npi, "name": elsewhere[0].get("ProviderName"), "network_names": nets},
                plan_or_network_checked=checked,
                source_url=base_src,
                confidence="high",
                notes=(f"NPI {q.npi} is in Devoted's {self.year} directory ({', '.join(nets)}) but NOT in {net}."),
            )

        return NetworkVerdict(
            status=NetworkStatus.OUT_OF_NETWORK,
            matched_provider=None,
            plan_or_network_checked=checked,
            source_url=base_src,
            confidence="medium",
            notes=(
                f"NPI {q.npi} was not found anywhere in Devoted's {self.year} provider directory, "
                f"so it is not listed in {net}. (Note: Devoted states some nurse practitioners / "
                f"physician assistants may be in-network but not individually listed.)"
            ),
        )
