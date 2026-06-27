"""Generic FHIR PDEX Plan-Net adapter — the COMPLIANT path.

Talks to a payer's public **CMS Provider Directory API** (HL7 FHIR R4, Da Vinci PDEX
Plan-Net IG). Under the CMS Interoperability & Patient Access rule, impacted payers
(Medicare Advantage, Medicaid/CHIP, QHP issuers) must expose this API publicly with
**no user authentication** — so unlike the web "Find a Doctor" tools, it's designed to
be queried programmatically. No scraping, no bot-protection to fight.

One class, parameterized by `base_url`, works for ANY PDEX Plan-Net server. Verified
live against Humana's `https://fhir.humana.com/api` (see DISCOVERY-fhir.md).

Verdict flow:
  1. Practitioner?identifier=<NPI>            -> is the provider in the directory at all?
  2. PractitionerRole?practitioner=<id>       -> the networks they participate in
     (network name is in the PDEX `network-reference` extension, valueReference.display)
  3. match plan_hint against those network names:
       strong match  -> IN_NETWORK
       provider present, no confident network match -> UNKNOWN (list the real networks)
       provider absent from directory               -> OUT_OF_NETWORK

Honesty rule: network-name matching is fuzzy, so we never emit a wrong OON from a name
mismatch — if we can't confidently map plan_hint to one of the provider's actual
networks, we return UNKNOWN with the real list for a human to map.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlencode

import httpx

from network_probe.base import PayerAdapter
from network_probe.core._http import CachedClient
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.plan_aliases import network_aliases

NETWORK_EXT_URL = "http://hl7.org/fhir/us/davinci-pdex-plan-net/StructureDefinition/network-reference"
FHIR_ACCEPT = {"accept": "application/fhir+json"}
MAX_ROLE_PAGES = 15  # safety cap when following Bundle.link[next]
MAX_ORG_RESOLVE = 14  # cap Organization name lookups (servers that omit network display)

# Known public PDEX Plan-Net base URLs (no auth). Extend freely.
KNOWN_ENDPOINTS = {
    "humana-fhir": "https://fhir.humana.com/api",
    "cigna-fhir": "https://p-hi2.digitaledge.cigna.com/ProviderDirectory/v1",
    # UnitedHealthcare's public PDEX directory (Optum FHIR Layer Exchange) — no auth/login.
    "uhc": "https://flex.optum.com/fhirpublic/R4",
}

# IN if the best network match clears these (combined = net-token-recall + hint-token-recall, max 2.0)
_MATCH_COMBINED_MIN = 1.5
_MATCH_NET_RECALL_MIN = 0.5


def _tokens(s: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split() if t]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _match_score(hint: str, network: str) -> float:
    """Combined token recall in [0,2]; rewards networks whose name the hint covers."""
    h_tok, n_tok = _tokens(hint), _tokens(network)
    h_c, n_c = _norm(hint), _norm(network)
    if not h_tok or not n_tok:
        return 0.0
    if h_c == n_c:  # exact (normalized) — beats every partial
        return 3.0
    if h_c and (h_c in n_c or n_c in h_c):
        shorter, longer = sorted((len(h_c), len(n_c)))
        return 2.0 + shorter / longer  # closer lengths rank higher (2.0–3.0)
    net_recall = sum(1 for t in n_tok if t in h_c) / len(n_tok)
    hint_recall = sum(1 for t in h_tok if t in n_c) / len(h_tok)
    # gate on net_recall so "FL Medicare HMO" doesn't latch onto "Natl Medicare HMO..."
    if net_recall < _MATCH_NET_RECALL_MIN:
        return 0.0
    return net_recall + hint_recall


class FhirPdexAdapter(PayerAdapter):
    def __init__(
        self,
        base_url: str | None = None,
        payer_name: str = "fhir",
        year: int | None = None,
        client: CachedClient | None = None,
    ):
        if not base_url:
            base_url = KNOWN_ENDPOINTS.get(payer_name)
        if not base_url:
            raise ValueError(
                f"FhirPdexAdapter needs a base_url (no known endpoint for {payer_name!r}). "
                f"Known: {', '.join(sorted(KNOWN_ENDPOINTS)) or '(none)'}."
            )
        self.base_url = base_url.rstrip("/")
        self.payer_name = payer_name
        self.year = year
        self.client = client or CachedClient()

    def _get(self, url: str) -> dict:
        return self.client.get_json(url, headers=FHIR_ACCEPT)

    @staticmethod
    def _next_link(bundle: dict) -> str | None:
        for ln in bundle.get("link") or []:
            if ln.get("relation") == "next" and ln.get("url"):
                return ln["url"]
        return None

    @staticmethod
    def _npis(resource: dict) -> list[str]:
        return [i.get("value") for i in (resource.get("identifier") or []) if i.get("value")]

    def _match_practitioner(self, bundle: dict, npi: str, strict: bool) -> tuple[str, dict] | None:
        first_prac = None
        for e in bundle.get("entry") or []:
            r = e.get("resource") or {}
            if r.get("resourceType") != "Practitioner":
                continue
            first_prac = first_prac or r
            if npi in self._npis(r):
                return r.get("id"), r
        # identifier-filtered search: trust the server's filter even if NPI isn't echoed
        if not strict and first_prac is not None:
            return first_prac.get("id"), first_prac
        return None

    def _find_practitioner(self, npi: str, first: str | None, last: str | None):
        """Resolve a Practitioner by NPI. Tries identifier search (Humana-style); falls
        back to name search + NPI match (Cigna-style servers don't support identifier)."""
        try:
            b = self._get(f"{self.base_url}/Practitioner?identifier={quote(npi)}")
            hit = self._match_practitioner(b, npi, strict=False)
            if hit:
                return hit
        except httpx.HTTPStatusError:
            pass  # server doesn't support Practitioner?identifier — fall back to name
        if last:
            params = {"family": last}
            if first:
                params["given"] = first
            b2 = self._get(f"{self.base_url}/Practitioner?{urlencode(params)}&_count=50")
            return self._match_practitioner(b2, npi, strict=True)
        return None

    def _org_name(self, reference: str) -> str | None:
        rid = reference.rsplit("/Organization/", 1)[-1].rsplit("/", 1)[-1]
        try:
            org = self._get(f"{self.base_url}/Organization/{quote(rid)}")
        except httpx.HTTPError:
            return None
        if org.get("resourceType") == "Organization":
            return org.get("name")
        for e in org.get("entry") or []:  # some servers wrap a read in a Bundle
            r = e.get("resource") or {}
            if r.get("resourceType") == "Organization":
                return r.get("name")
        return None

    def _networks_for(self, practitioner_id: str) -> tuple[list[str], list[str], int]:
        """Return (network_names, specialties, role_count) across all PractitionerRole pages.

        Network name comes from the PDEX network-reference extension's display when present
        (Humana); when only an Organization reference is given (Cigna), resolve it to a name.
        """
        url = f"{self.base_url}/PractitionerRole?practitioner={quote(practitioner_id)}&_count=50"
        names: list[str] = []
        refs: list[str] = []
        specialties: set[str] = set()
        roles = 0
        pages = 0
        while url and pages < MAX_ROLE_PAGES:
            bundle = self._get(url)
            for e in bundle.get("entry") or []:
                r = e.get("resource") or {}
                if r.get("resourceType") != "PractitionerRole":
                    continue
                roles += 1
                for ext in r.get("extension") or []:
                    if ext.get("url") == NETWORK_EXT_URL:
                        vr = ext.get("valueReference") or {}
                        if vr.get("display"):
                            names.append(vr["display"])
                        elif vr.get("reference"):
                            refs.append(vr["reference"])
                for c in r.get("specialty") or []:
                    for cd in c.get("coding") or []:
                        if cd.get("display"):
                            specialties.add(cd["display"])
            url = self._next_link(bundle)
            pages += 1
        # resolve Organization references that lacked an inline display name
        uniq_refs, seen_ref = [], set()
        for ref in refs:
            if ref not in seen_ref:
                seen_ref.add(ref)
                uniq_refs.append(ref)
        for ref in uniq_refs[:MAX_ORG_RESOLVE]:
            nm = self._org_name(ref)
            if nm:
                names.append(nm)
        if len(uniq_refs) > MAX_ORG_RESOLVE:
            names.append(f"(+{len(uniq_refs) - MAX_ORG_RESOLVE} more network organizations)")
        # de-dup network names, preserve order
        seen, uniq = set(), []
        for n in names:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        return uniq, sorted(specialties), roles

    @staticmethod
    def _practitioner_name(resource: dict) -> str:
        for nm in resource.get("name") or []:
            if nm.get("text"):
                return nm["text"]
            given = " ".join(nm.get("given") or [])
            return f"{given} {nm.get('family', '')}".strip()
        return "(unknown)"

    def check_network(self, q: ProviderQuery) -> NetworkVerdict:
        prac_url = f"{self.base_url}/Practitioner?identifier={q.npi}"
        if not q.npi:
            return NetworkVerdict(
                status=NetworkStatus.UNKNOWN, matched_provider=None,
                plan_or_network_checked=f"{self.payer_name} FHIR directory",
                source_url=self.base_url, confidence="low",
                notes="An NPI is required to query a FHIR Provider Directory (identifier search).",
            )

        found = self._find_practitioner(q.npi, q.first_name, q.last_name)
        if not found:
            return NetworkVerdict(
                status=NetworkStatus.OUT_OF_NETWORK, matched_provider=None,
                plan_or_network_checked=f"{self.payer_name} FHIR directory (plan hint: {q.plan_hint!r})",
                source_url=prac_url, confidence="medium",
                notes=(
                    f"NPI {q.npi} is not present in the {self.payer_name} FHIR Provider Directory, "
                    f"so the provider is not a contracted/listed provider for this payer."
                ),
            )
        pid, prac = found
        name = self._practitioner_name(prac)
        role_url = f"{self.base_url}/PractitionerRole?practitioner={pid}"
        networks, specialties, role_count = self._networks_for(pid)
        srcs = f"{prac_url} ; {role_url}"
        base_provider = {"npi": q.npi, "name": name, "specialty": ", ".join(specialties) or None,
                         "networks": networks}

        if not networks:
            return NetworkVerdict(
                status=NetworkStatus.OUT_OF_NETWORK, matched_provider=base_provider,
                plan_or_network_checked=f"{self.payer_name} (plan hint: {q.plan_hint!r})",
                source_url=srcs, confidence="medium",
                notes=f"{name} (NPI {q.npi}) is in the directory but has no active network roles.",
            )

        # rank the provider's networks against the plan hint AND any plan->network aliases
        aliases = network_aliases(self.payer_name, q.plan_hint or "", q.state)
        hints = [(q.plan_hint or "", False)] + [(a, True) for a in aliases]
        ranked = [
            (_match_score(hint, net), net, hint, is_alias)
            for net in networks
            for hint, is_alias in hints
            if hint.strip()
        ]
        ranked.sort(key=lambda x: x[0], reverse=True)
        best_score, best_net, best_hint, best_alias = ranked[0] if ranked else (0.0, None, None, False)

        if (q.plan_hint or "").strip() and best_score >= _MATCH_COMBINED_MIN:
            via = f" (via plan→network alias '{best_hint}')" if best_alias else ""
            return NetworkVerdict(
                status=NetworkStatus.IN_NETWORK,
                matched_provider={**base_provider, "matched_network": best_net},
                plan_or_network_checked=f"{self.payer_name} / network '{best_net}'",
                source_url=srcs, confidence="high",
                notes=(f"{name} (NPI {q.npi}) participates in '{best_net}', matched to plan hint "
                       f"{q.plan_hint!r}{via}. Total networks: {len(networks)}."),
            )

        if not (q.plan_hint or "").strip():
            return NetworkVerdict(
                status=NetworkStatus.IN_NETWORK, matched_provider=base_provider,
                plan_or_network_checked=f"{self.payer_name} (any network)",
                source_url=srcs, confidence="medium",
                notes=(f"{name} (NPI {q.npi}) is a contracted {self.payer_name} provider in "
                       f"{len(networks)} network(s): {', '.join(networks[:8])}"
                       f"{'…' if len(networks) > 8 else ''}. No plan hint given to narrow to one."),
            )

        # provider IS in the directory, but no confident match for the requested plan -> honest UNKNOWN
        return NetworkVerdict(
            status=NetworkStatus.UNKNOWN, matched_provider=base_provider,
            plan_or_network_checked=f"{self.payer_name} (plan hint: {q.plan_hint!r})",
            source_url=srcs, confidence="medium",
            notes=(
                f"{name} (NPI {q.npi}) is in the {self.payer_name} directory but none of their "
                f"{len(networks)} networks confidently matched plan hint {q.plan_hint!r}. "
                f"Their networks: {', '.join(networks)}. Map the plan to one of these to confirm."
            ),
        )
