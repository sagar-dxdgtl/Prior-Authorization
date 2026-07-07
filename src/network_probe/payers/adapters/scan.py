"""SCAN Health Plan directory adapter — presence-based in-network.

SCAN's public PDEX directory (https://providerdirectory.scanhealthplan.com — InterSystems
FHIR R4, no auth) lists SCAN's **in-network** providers, but exposes NO traversable network
linkage. Verified live (see docs/payer-sources/MATRIX.md):
  - PractitionerRole carries no `network-reference` extension,
  - the provider's OrganizationAffiliations have no `network` field, and
  - InsurancePlan.network is unpopulated (76 plans, 0 network refs).

So the only honest signal SCAN's directory gives is **presence**:

    provider present in the directory  -> IN_NETWORK for SCAN (cannot narrow to a sub-plan)
    provider absent                    -> OUT_OF_NETWORK

When the query carries a state, we best-effort confirm the provider services that state via
Practitioner.address / PractitionerRole.location -> Location.address.state. A *confirmed*
mismatch downgrades to UNKNOWN (present in SCAN, but not evidenced in the queried state);
missing location data never fails the verdict — it stays presence-based.

SCAN rate-limits aggressively, so volume is kept minimal and CachedClient's delay/cache apply.
"""

from __future__ import annotations

from urllib.parse import quote, urlencode

import httpx

from network_probe.core._http import CachedClient
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.payers.adapters.base import PayerAdapter

FHIR_ACCEPT = {"accept": "application/fhir+json"}
SCAN_BASE = "https://providerdirectory.scanhealthplan.com"
MAX_LOC_RESOLVE = 6  # cap Location reads when confirming state (SCAN rate-limits hard)


class ScanDirectoryAdapter(PayerAdapter):
    def __init__(
        self,
        base_url: str | None = None,
        payer_name: str = "scan",
        year: int | None = None,
        client: CachedClient | None = None,
    ):
        self.base_url = (base_url or SCAN_BASE).rstrip("/")
        self.payer_name = payer_name
        self.year = year
        self.client = client or CachedClient()

    def _get(self, url: str) -> dict:
        return self.client.get_json(url, headers=FHIR_ACCEPT)

    @staticmethod
    def _npis(resource: dict) -> list[str]:
        return [i.get("value") for i in (resource.get("identifier") or []) if i.get("value")]

    @staticmethod
    def _name(resource: dict) -> str:
        for nm in resource.get("name") or []:
            if nm.get("text"):
                return nm["text"]
            given = " ".join(nm.get("given") or [])
            return f"{given} {nm.get('family', '')}".strip()
        return "(unknown)"

    def _match(self, bundle: dict, npi: str | None, strict: bool) -> dict | None:
        first = None
        for e in bundle.get("entry") or []:
            r = e.get("resource") or {}
            if r.get("resourceType") != "Practitioner":
                continue
            first = first or r
            if npi and npi in self._npis(r):
                return r
        return None if strict else first

    def _find_practitioner(self, npi: str | None, first: str | None, last: str | None) -> dict | None:
        """Resolve a Practitioner by NPI (identifier search), falling back to name search."""
        if npi:
            try:
                b = self._get(f"{self.base_url}/Practitioner?identifier={quote(npi)}")
                hit = self._match(b, npi, strict=False)
                if hit:
                    return hit
            except httpx.HTTPStatusError:
                pass  # server rejected identifier search — fall back to name
        if last:
            params = {"family": last}
            if first:
                params["given"] = first
            b2 = self._get(f"{self.base_url}/Practitioner?{urlencode(params)}&_count=50")
            return self._match(b2, npi, strict=bool(npi))
        return None

    def _service_states(self, prac: dict, pid: str) -> set[str]:
        """Best-effort set of 2-letter states the provider services (never raises)."""
        states: set[str] = set()
        for a in prac.get("address") or []:
            if a.get("state"):
                states.add(a["state"].strip().upper()[:2])
        if not pid:
            return states
        try:
            roles = self._get(f"{self.base_url}/PractitionerRole?practitioner={quote(pid)}&_count=50")
        except httpx.HTTPError:
            return states
        loc_refs: list[str] = []
        for e in roles.get("entry") or []:
            for loc in (e.get("resource") or {}).get("location") or []:
                ref = loc.get("reference")
                if ref and ref not in loc_refs:
                    loc_refs.append(ref)
        for ref in loc_refs[:MAX_LOC_RESOLVE]:
            rid = ref.rsplit("/Location/", 1)[-1].rsplit("/", 1)[-1]
            try:
                loc = self._get(f"{self.base_url}/Location/{quote(rid)}")
            except httpx.HTTPError:
                continue
            st = (loc.get("address") or {}).get("state")
            if st:
                states.add(st.strip().upper()[:2])
        return states

    def check_network(self, q: ProviderQuery) -> NetworkVerdict:
        prac_url = f"{self.base_url}/Practitioner?identifier={q.npi}"
        if not q.npi and not q.provider_last_name:
            return NetworkVerdict(
                status=NetworkStatus.UNKNOWN,
                matched_provider=None,
                plan_or_network_checked=f"{self.payer_name} directory",
                source_url=self.base_url,
                confidence="low",
                notes="An NPI or provider name is required to query SCAN's provider directory.",
            )

        found = self._find_practitioner(q.npi, q.provider_first_name, q.provider_last_name)
        if not found:
            return NetworkVerdict(
                status=NetworkStatus.OUT_OF_NETWORK,
                matched_provider=None,
                plan_or_network_checked=f"{self.payer_name} in-network directory",
                source_url=prac_url,
                confidence="medium",
                notes=(
                    f"NPI {q.npi} is not present in SCAN's provider directory (which lists SCAN's "
                    f"in-network providers), so the provider is not listed as in-network for SCAN."
                ),
            )

        pid = found.get("id")
        name = self._name(found)
        provider = {"npi": q.npi, "name": name, "scan_id": pid}
        src = prac_url

        # best-effort state qualification ("AZ", "CO-Denver" -> "CO")
        want = (q.state or "").strip().upper().split("-")[0][:2]
        if want:
            states = self._service_states(found, pid)
            src = f"{prac_url} ; {self.base_url}/PractitionerRole?practitioner={pid}"
            if states:
                provider["service_states"] = sorted(states)
                if want in states:
                    return NetworkVerdict(
                        status=NetworkStatus.IN_NETWORK,
                        matched_provider=provider,
                        plan_or_network_checked=f"{self.payer_name} in-network directory ({want})",
                        source_url=src,
                        confidence="high",
                        notes=(
                            f"{name} (NPI {q.npi}) is listed in SCAN's in-network provider directory "
                            f"with a service location in {want}. SCAN's directory exposes no plan-level "
                            f"networks, so this confirms in-network for SCAN — not a specific SCAN plan."
                        ),
                    )
                return NetworkVerdict(
                    status=NetworkStatus.UNKNOWN,
                    matched_provider=provider,
                    plan_or_network_checked=f"{self.payer_name} in-network directory (state {want})",
                    source_url=src,
                    confidence="medium",
                    notes=(
                        f"{name} (NPI {q.npi}) is in SCAN's directory, but their listed service "
                        f"state(s) {sorted(states)} do not include {want}. SCAN spans multiple states; "
                        f"confirm the {want} location before relying on this."
                    ),
                )

        # presence only (no state given, or state undeterminable from sparse SCAN data)
        unconfirmed = f" Service state could not be confirmed against {want}." if want else ""
        return NetworkVerdict(
            status=NetworkStatus.IN_NETWORK,
            matched_provider=provider,
            plan_or_network_checked=f"{self.payer_name} in-network directory",
            source_url=src,
            confidence="medium",
            notes=(
                f"{name} (NPI {q.npi}) is listed in SCAN's in-network provider directory. SCAN's "
                f"directory exposes no plan-level network data, so this confirms the provider is "
                f"in-network for SCAN (not narrowed to a specific SCAN plan).{unconfirmed}"
            ),
        )
