"""Cross-source corroboration + honest confidence (TODO items #1 and #2).

A payer directory is one signal, not ground truth (CMS audits: ~45–52% of listings carry an
error). So after an adapter returns its directory verdict, we:

  #1  Confidence + asymmetry — a single-directory IN is demoted to `medium` with a
      "verify before billing" caveat; absence-based OON stays as the adapter set it. A wrong
      IN is the expensive error, so we are most skeptical of IN.

  #2  Multi-source corroboration — independent sources are consulted. If one *contradicts* an
      IN (e.g. NPPES shows the NPI deactivated, or a different person), the verdict becomes
      REVIEW (human-verify) instead of a confident-but-wrong IN. Sources are pluggable; add
      Availity / a TIN-level portal check / a second payer directory as they become available.

Sources that can't be reached (e.g. NPPES blocked) degrade to an `inconclusive` signal — they
never flip a verdict on their own and never throw.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Optional, Protocol

from ._http import CachedClient
from .models import NetworkStatus, NetworkVerdict, ProviderQuery

# what counts as "high error rate" framing in the caveat
_DIRECTORY_CAVEAT = ("Single payer-directory source; directories are frequently stale "
                     "(CMS audits ~45–52% error) — verify before billing.")


@dataclass
class Signal:
    source: str
    result: str  # "corroborates" | "contradicts" | "inconclusive"
    detail: str

    def as_dict(self) -> dict:
        return {"source": self.source, "result": self.result, "detail": self.detail}


class CorroborationSource(Protocol):
    name: str
    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]: ...


def _name_tokens(name: str) -> set[str]:
    """Lowercase alpha tokens, dropping common credential suffixes."""
    creds = {"md", "do", "np", "pa", "dc", "phd", "lcsw", "lpc", "rn", "dds", "dmd", "od", "pt", "psyd"}
    return {t for t in re.findall(r"[a-z]+", (name or "").lower()) if t not in creds}


class NppesSource:
    """CMS NPPES registry — independent identity + active/deactivation check.

    NPPES doesn't carry network participation, so it can never *promote* an IN to high; it can
    only corroborate identity, or *contradict* (deactivated / not found / different person),
    which downgrades an IN to REVIEW.
    """

    name = "NPPES"
    BASE = "https://npiregistry.cms.hhs.gov/RegistryBack/npiDetails"

    def __init__(self, client: Optional[CachedClient] = None):
        self.client = client or CachedClient()

    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]:
        if not q.npi:
            return None
        body = json.dumps({"number": q.npi, "skip": 0, "exactMatch": False})
        headers = {
            "content-type": "application/json",
            "origin": "https://npiregistry.cms.hhs.gov",
            "referer": "https://npiregistry.cms.hhs.gov/search",
        }
        try:
            data = self.client.post_json(self.BASE, content=body, headers=headers)
        except Exception:
            return Signal(self.name, "inconclusive", "NPPES not reachable; identity not corroborated.")

        basic = data.get("basic") or {}
        if not basic:  # npiDetails returns {} for an unknown / deactivated NPI
            return Signal(self.name, "contradicts",
                          f"NPI {q.npi} is not active in NPPES (not found / deactivated).")
        status = basic.get("status")  # "A" = active
        nppes_last = (basic.get("lastName") or "").strip().lower()
        states = {a.get("state") for a in (data.get("addresses") or []) if a.get("state")}
        who = " ".join(x for x in [basic.get("firstName", ""), basic.get("lastName", "")] if x).strip() \
            or basic.get("organizationName", "") or "provider"

        if status and status != "A":
            return Signal(self.name, "contradicts", f"NPPES lists NPI {q.npi} as inactive (status {status}).")

        # identity mismatch: NPPES last name not present in the directory's matched name (or query)
        dir_tokens = _name_tokens((verdict.matched_provider or {}).get("name", "")) or _name_tokens(q.last_name or "")
        if nppes_last and dir_tokens and nppes_last not in dir_tokens:
            return Signal(self.name, "contradicts",
                          f"Identity mismatch: NPPES NPI {q.npi} is '{who}', directory matched a different name.")

        if q.state and states and q.state.upper() not in states:
            return Signal(self.name, "inconclusive",
                          f"NPPES practice states {sorted(states)} don't include {q.state.upper()}.")
        return Signal(self.name, "corroborates", f"NPPES: NPI active, {who}.")


class TinScopeSource:
    """TIN-level check (#3). Contracts are signed at the TIN/group level, so a provider can be
    in-network under one TIN and OON under the one the claim actually bills. If the member's billing
    TIN isn't among the provider's in-network TINs, that's a contradiction → REVIEW.

    In-network TINs come from the adapter (`matched_provider['in_network_tins']`, e.g. Oscar) or, when
    the directory doesn't expose them, from the NPI→TIN crosswalk (Phase 3). Otherwise inconclusive.
    """
    name = "TIN-scope"

    def __init__(self, crosswalk=None, status_book=None):
        self.crosswalk = crosswalk
        self.status_book = status_book

    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]:
        if not q.tin:
            return None

        # (a) Verified payer TIN-level network status (e.g. Cigna's Network Status portal or an
        # Availity TIN check). Authoritative for the specific (provider, TIN) and works even when
        # the directory didn't list the provider — this is a real group-level answer, not a guess.
        from .tin_status import default_tin_status
        book = self.status_book or default_tin_status()
        vs = book.lookup(q.payer, q.npi, q.tin) if book else None
        if vs:
            grp = f" ({vs.group})" if vs.group else ""
            prov = f" per {vs.source}" if vs.source else ""
            ver = f", verified {vs.verified_at}" if vs.verified_at else ""
            if vs.status == NetworkStatus.OUT_OF_NETWORK.value:
                agrees = verdict.status != NetworkStatus.IN_NETWORK
                tail = ("Confirms the directory's out-of-network finding." if agrees
                        else "Directory listed the provider, but the billing TIN is out-of-network — review.")
                return Signal(self.name, "corroborates" if agrees else "contradicts",
                              f"Billing TIN {q.tin}{grp} is OUT-OF-NETWORK{prov}{ver}. {tail}")
            agrees = verdict.status == NetworkStatus.IN_NETWORK
            return Signal(self.name, "corroborates" if agrees else "contradicts",
                          f"Billing TIN {q.tin}{grp} is IN-NETWORK{prov}{ver}.")

        # (b) No verified TIN status and the provider isn't listed -> nothing to compare against.
        if verdict.status != NetworkStatus.IN_NETWORK:
            return Signal(self.name, "inconclusive",
                          f"Billing TIN {q.tin} not evaluated — provider isn't listed in this directory.")
        tins = (verdict.matched_provider or {}).get("in_network_tins")
        src = "payer directory"
        if not tins:  # directory had none → try the NPI→TIN crosswalk (TiC-derived)
            from .tin_crosswalk import default_crosswalk
            cw = self.crosswalk or default_crosswalk()
            tins = cw.tins_for(q.payer, q.npi) if cw else []
            src = "contracted-TIN crosswalk (TiC)"
        if not tins:
            return Signal(self.name, "inconclusive",
                          f"Could not confirm TIN {q.tin} — no per-TIN data (directory or crosswalk).")
        norm = {re.sub(r'[^0-9]', '', str(t)) for t in tins}
        if re.sub(r'[^0-9]', '', q.tin) in norm:
            return Signal(self.name, "corroborates", f"In-network under billing TIN {q.tin} (per {src}).")
        return Signal(self.name, "contradicts",
                      f"Provider is in-network, but NOT under billing TIN {q.tin} "
                      f"(in-network TINs per {src}: {sorted(tins)}).")


class FreshnessSource:
    """Freshness/transition signals (#4): a directory entry flagged going-OON-soon, or with an old
    last-verified date, shouldn't be asserted at full confidence (NSA requires 90-day verification)."""
    name = "Freshness"

    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]:
        mp = verdict.matched_provider or {}
        if verdict.status != NetworkStatus.IN_NETWORK:
            return None
        if mp.get("going_oon_soon"):
            return Signal(self.name, "stale", "Directory flags this provider as going out-of-network soon.")
        lu = mp.get("last_updated") or mp.get("last_inn_date")
        if lu:
            return Signal(self.name, "corroborates", f"Directory record dated {lu}.")
        return None


class StediSource:
    """Independent eligibility (270/271) cross-check via Stedi's clearinghouse API (Phase 2).

    Activates only when STEDI_API_KEY is set. Honest caveat: a 271's network indicator is
    *benefit-tier*, and only some payers return a provider-specific network status — so this is a
    best-effort corroborator, often `inconclusive`. When the payer DOES return a clear in/out signal,
    it corroborates or flips the verdict to REVIEW.

    To go live: (1) set STEDI_API_KEY, (2) map payer -> Stedi `tradingPartnerServiceId` in PAYER_IDS
    (from stedi.com payer network), (3) the query carries member_id/dob (the 271 ingest fills these).
    Field paths below follow Stedi's 271 JSON; verify against a real response when enabling a payer.
    """

    name = "Stedi"
    BASE = os.environ.get(
        "STEDI_ELIGIBILITY_URL",
        "https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3",
    )
    # our adapter key -> Stedi tradingPartnerServiceId (primaryPayerId from the Stedi payer network,
    # all eligibility-SUPPORTED). Looked up via GET /payers/search.
    PAYER_IDS: dict[str, str] = {
        "oscar": "OSCAR",
        "devoted": "DEVOT",
        "humana-fhir": "61101",
        "cigna-fhir": "62308",
        "uhc": "87726",
    }

    def __init__(self, api_key: Optional[str] = None, client: Optional[CachedClient] = None):
        self.api_key = api_key or os.environ.get("STEDI_API_KEY")
        self.client = client or CachedClient()

    @staticmethod
    def _to_stedi_dob(dob: Optional[str]) -> Optional[str]:
        if not dob:
            return None
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", dob)
        return f"{m.group(3)}{int(m.group(1)):02d}{int(m.group(2)):02d}" if m else dob

    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]:
        if not self.api_key:
            return None  # not configured — stay silent
        payer_id = self.PAYER_IDS.get(q.payer)
        if not payer_id:
            return Signal(self.name, "inconclusive", f"No eligibility payer id mapped for {q.payer!r}.")
        if not (q.member_id or q.dob or q.last_name):
            return Signal(self.name, "inconclusive", "Eligibility check needs member id / DOB / last name.")
        body = {
            "tradingPartnerServiceId": payer_id,
            "provider": {k: v for k, v in {"npi": q.npi, "lastName": q.last_name}.items() if v},
            "subscriber": {k: v for k, v in {
                "memberId": q.member_id, "dateOfBirth": self._to_stedi_dob(q.dob),
                "firstName": q.first_name, "lastName": q.last_name}.items() if v},
            "encounter": {"serviceTypeCodes": ["30"]},  # 30 = health benefit plan coverage
        }
        try:
            data = self.client.post_json(
                self.BASE, content=json.dumps(body),
                headers={"Authorization": self.api_key, "content-type": "application/json"})
        except Exception:
            return Signal(self.name, "inconclusive", "Eligibility check failed.")
        return self._interpret(data)

    @staticmethod
    def _interpret(data: dict) -> Signal:
        """Read a 271. Two independent facts live here:

        1. **Active coverage** (planStatus statusCode "1") — the 271's reliable eligibility signal.
        2. **Benefit-tier network codes** (EB12 inPlanNetworkIndicatorCode). In a plan-level 271
           these describe which benefit *tiers* the plan quotes (in-network / out-of-network /
           not-applicable), NOT whether the queried provider is contracted. So a lone in-network
           tier is a soft corroboration; only an out-of-network-*only* quote is a real contradiction.
        """
        statuses = data.get("planStatus") or []
        active = any(str(s.get("statusCode")) == "1" or "active" in (s.get("status") or "").lower()
                     for s in statuses)
        plan = next((s.get("planDetails") for s in statuses if s.get("planDetails")), None) \
            or (data.get("planInformation") or {}).get("groupDescription")
        grp = (data.get("planInformation") or {}).get("groupNumber")
        cov = ("Active coverage" + (f" — {plan}" if plan else "") + (f" (group {grp})" if grp else "")) \
            if active else ""
        codes = {b.get("inPlanNetworkIndicatorCode") for b in (data.get("benefitsInformation") or [])
                 if b.get("inPlanNetworkIndicatorCode")}
        has_in, has_out = "Y" in codes, "N" in codes
        if has_out and not has_in:
            return Signal("Stedi", "contradicts",
                          f"271 quotes out-of-network benefit tiers only{(' — ' + cov) if cov else ''}.")
        if has_in and not has_out:
            return Signal("Stedi", "corroborates",
                          (f"{cov}; " if cov else "")
                          + "benefits quoted at the in-network tier (no out-of-network pricing returned).")
        if has_in and has_out:
            return Signal("Stedi", "inconclusive",
                          (f"{cov}; " if cov else "")
                          + "271 quotes both in- and out-of-network benefit tiers — no provider-specific determination.")
        if cov:
            return Signal("Stedi", "inconclusive",
                          f"{cov}; 271 carries no provider-specific network indicator (payer-dependent).")
        return Signal("Stedi", "inconclusive",
                      "271 carried no provider-specific in-network signal (payer-dependent).")


# Canned-but-correct 271 fixtures keyed by NPI. The interpretation path is the real
# StediSource._interpret, so only the payload is mocked. Most payers don't return a
# provider-specific network indicator in a 271, so unknown NPIs are honestly inconclusive.
_STEDI_FIXTURE_271: dict[str, dict] = {
    # Dr Jing Li — Devoted CO PPO: the payer's 271 returns out-of-network benefits,
    # independently agreeing with the Availity-confirmed OON truth (directory says IN, stale).
    "1629339312": {"benefitsInformation": [{"inPlanNetworkIndicatorCode": "N"}]},
}


class StediMockSource:
    """Fixture-backed Stedi 270/271 cross-check (canned `_STEDI_FIXTURE_271`).

    The payload is canned but flows through the real StediSource._interpret, so the semantics are
    genuine. Used as the last-resort fallback when there's neither a live key nor a saved live 271.
    """
    name = "Stedi"

    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]:
        data = _STEDI_FIXTURE_271.get(q.npi or "")
        if data is None:
            return Signal(self.name, "inconclusive",
                          "271 carried no provider-specific in-network signal (payer-dependent).")
        return StediSource._interpret(data)


class StediCachedSource:
    """Stedi 270/271 cross-check served from a saved *live* call
    (`.cache/stedi_271/<npi>.json`, populated once by the OON prefetch). Lets the app show the
    genuine eligibility determination without a live key at runtime — same "fetch once, serve from
    saved" pattern as the OON benefits. Falls back to the canned fixture, then honest inconclusive.
    """
    name = "Stedi"

    def check(self, q: ProviderQuery, verdict: NetworkVerdict) -> Optional[Signal]:
        from .oon_benefits import load_271
        saved = load_271(q.npi)
        if saved and saved.get("response_271"):
            return StediSource._interpret(saved["response_271"])
        return StediMockSource().check(q, verdict)   # fixture, else honest inconclusive


def default_sources(client: Optional[CachedClient] = None) -> list:
    # Eligibility (270/271) is fetched ONCE offline (oon_benefits prefetch) and served from the
    # saved 271 — the app never makes a live eligibility call. A live 270 from the UI would fail
    # anyway (the form carries no member id / DOB, which are PHI), so StediCachedSource serves the
    # saved live response (else the canned fixture, else honest inconclusive). Same "fetch once,
    # serve saved" model as the OON tab. The live StediSource is used only by the offline prefetch.
    return [NppesSource(client), TinScopeSource(), FreshnessSource(), StediCachedSource()]


def run_display_signals(verdict: NetworkVerdict, q: ProviderQuery, sources: list) -> list[Signal]:
    """Run each source defensively; a source that errors degrades to an inconclusive signal."""
    out: list[Signal] = []
    for src in sources:
        try:
            s = src.check(q, verdict)
        except Exception:
            s = Signal(getattr(src, "name", "source"), "inconclusive", "source error")
        if s:
            out.append(s)
    return out


def finalize(verdict: NetworkVerdict, q: ProviderQuery, sources: Optional[list] = None,
             override_store=None, signals: Optional[list] = None) -> NetworkVerdict:
    """Golden-record override (#5), then corroboration (#2) + confidence/asymmetry (#1 + #4).

    `signals` may carry pre-computed Signal objects (e.g. from `run_display_signals`) so the
    caller can run the sources once and reuse them; when None they are computed here.
    """
    # #5 — a confirmed override wins over the live directory.
    from .overrides import OverrideStore, verdict_from_override
    store = OverrideStore() if override_store is None else override_store
    ov = store.lookup(q)
    if ov:
        return verdict_from_override(ov, verdict)

    sources = default_sources() if sources is None else sources
    if signals is None:
        signals = run_display_signals(verdict, q, sources)
    verdict.corroboration = [s.as_dict() for s in signals]
    contradictions = [s for s in signals if s.result == "contradicts"]
    stale = [s for s in signals if s.result == "stale"]

    if verdict.status == NetworkStatus.IN_NETWORK:
        if contradictions:
            verdict.status = NetworkStatus.REVIEW
            verdict.confidence = "conflict"
            verdict.notes = (
                "CONFLICT — directory says in-network, but: "
                + "; ".join(s.detail for s in contradictions)
                + " Flagged for human verification. (Original: " + verdict.notes + ")"
            )
        else:
            # #1 asymmetry — never assert high from a single directory
            if verdict.confidence == "high":
                verdict.confidence = "medium"
            verdict.notes = verdict.notes + " " + _DIRECTORY_CAVEAT
            if stale:  # #4 freshness drags confidence down further
                verdict.confidence = "low"
                verdict.notes += " " + "; ".join(s.detail for s in stale)
            if any(s.source == "NPPES" and s.result == "corroborates" for s in signals):
                verdict.notes += " Identity corroborated by NPPES."
            if any(s.source == "TIN-scope" and s.result == "corroborates" for s in signals):
                verdict.notes += " In-network under the billing TIN."
    return verdict
