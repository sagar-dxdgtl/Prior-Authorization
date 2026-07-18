"""Plan-benefit (CMS PBP) layer: the OON *tier* signal for Medicare-Advantage plans.

Two concerns:
  * pure logic — the OON capability a plan record implies (`record_oon_capability`) and resolving a
    member's free-text plan string to a specific PBP plan by name (`best_plan_match`). No DB, no I/O.
  * `PlanBenefitStore` — a thin, idempotent SQLAlchemy wrapper over the global `plan_benefits` table
    (public CMS data, tenant_id NULL), mirroring `ProviderNetworkStore`.

PBP carries benefit DESIGN only — it never answers whether a provider is in-network (no rosters).
It answers: given the provider is OON, does the plan pay OON benefits, and what is the MOOP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import func, or_, select

from network_probe.db.base import SessionLocal, app_engine
from network_probe.db.models import PlanBenefit
from network_probe.domain.line_of_business import (
    line_of_business,
    plan_oon_capability,
    plan_type,
)

_CONTRACT_RE = re.compile(r"\b([HRSE]\d{4})\b", re.I)

# generic tokens shared by many plan names — dropped so a match needs *distinctive* overlap
_STOPWORDS = {
    "the", "from", "of", "and", "inc", "llc", "plan", "plans", "medicare", "advantage",
    "health", "healthcare", "group", "for", "llp", "co",
}


def record_oon_capability(rec) -> bool | None:
    """Does this plan pay OUT-of-network benefits? (bool | None; None = defer to the 271.)

    Priority, all deliberately conservative:
      1. ANY D-SNP → None. Dual cost-sharing is Medicaid-wrapped / member-specific — never assert a
         tier from plan structure, even with a MOOP on file.
      2. a filed combined or OON MOOP (`*_yn == '1'`) → True. A combined in+out MOOP only exists when
         the plan actually pays OON — hard evidence, stronger than the structural type.
      3. otherwise the structural plan type decides (PPO/PFFS True, pure HMO False, else None).
    """
    if getattr(rec, "dsnp", False):
        return None
    if (getattr(rec, "comb_moop_yn", "") or "") == "1" or (getattr(rec, "oon_moop_yn", "") or "") == "1":
        return True
    return plan_oon_capability(getattr(rec, "plan_type", None), dsnp=False)


@dataclass
class PlanTypeResolution:
    plan_type: str  # normalized token or "unknown"
    capability: bool | None  # OON-benefit capability for the determination (None = defer to the 271)
    source: str  # "pbp" | "plan-string" | "none" | "n/a"
    record: PlanBenefit | None = None  # the matched PBP plan (for MOOP display), if any
    contract: str | None = None  # H/R/S/E contract parsed from the plan string, if present


def parse_contract(plan: str | None) -> str | None:
    """Extract a CMS contract number (H/R/S/E + 4 digits) embedded in a plan string, uppercased."""
    m = _CONTRACT_RE.search(plan or "")
    return m.group(1).upper() if m else None


def resolve_plan_type(plan_hint: str | None, benefit_type: str | None = None, store=None) -> PlanTypeResolution:
    """Resolve a member's plan string to an OON-tier signal, Medicare/Dual only.

    Precedence: an authoritative PBP match (when a `store` is provided and resolves) → the explicit
    product token written in the plan string → unknown. `store=None` (test env / live disabled) skips
    the DB entirely and uses the string token. Commercial/other lines → N/A (PBP is Medicare-only)."""
    lob = line_of_business(plan_hint, benefit_type)
    contract = parse_contract(plan_hint)
    if lob not in ("medicare", "dual"):
        return PlanTypeResolution("unknown", None, "n/a", None, contract)

    if store is not None:
        try:
            rec = store.resolve(plan_hint, contract=contract)
        except Exception:  # noqa: BLE001 — a store/DB hiccup must degrade to the string token, not error
            rec = None
        if rec is not None:
            return PlanTypeResolution(rec.plan_type, record_oon_capability(rec), "pbp", rec, contract)

    tok = plan_type(plan_hint)
    if tok != "unknown":
        return PlanTypeResolution(tok, plan_oon_capability(tok, dsnp=lob == "dual"), "plan-string", None, contract)
    return PlanTypeResolution("unknown", None, "none", None, contract)


def _tokens(s: str | None) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(t) >= 2 and t not in _STOPWORDS}


def best_plan_match(hint: str | None, candidates, min_overlap: int = 2):
    """Pick the PBP plan whose name best matches the member's plan string `hint`, by distinctive-token
    overlap. Returns None when nothing overlaps enough (`min_overlap`) OR when the top-scoring matches
    span DIFFERENT plan types — we refuse to guess a type from an ambiguous family (the demo-safety
    rule). Ties among candidates of the SAME type are fine (type is what the determination needs)."""
    hint_toks = _tokens(hint)
    if not hint_toks or not candidates:
        return None
    scored = []
    for c in candidates:
        name_toks = _tokens(getattr(c, "plan_name", "")) | _tokens(getattr(c, "org_marketing_name", ""))
        scored.append((len(hint_toks & name_toks), c))
    best = max(s for s, _ in scored)
    if best < min_overlap:
        return None
    winners = [c for s, c in scored if s == best]
    # The guard must key on the resolved OON CAPABILITY (what flows into the determination), not just
    # the plan_type token — dsnp and the MOOP flags change the tier too. If the tied winners imply
    # DIFFERENT capabilities (e.g. a PPO and its PPO D-SNP sibling), refuse to guess.
    if len({record_oon_capability(c) for c in winners}) > 1:
        return None
    # Deterministic pick among same-capability winners (never candidate/DB-order dependent).
    return sorted(winners, key=lambda c: (getattr(c, "contract_number", "") or "", getattr(c, "pbp_id", "") or ""))[0]


class PlanBenefitStore:
    def __init__(self, engine=None):
        self.engine = engine or app_engine()

    def latest_year(self) -> int | None:
        with SessionLocal(bind=self.engine) as s:
            return s.execute(select(func.max(PlanBenefit.year))).scalar()

    def lookup(self, contract: str, pbp: str, segment: str = "0", year: int | None = None) -> PlanBenefit | None:
        if not (contract and pbp):
            return None
        conds = [PlanBenefit.contract_number == contract.strip().upper(), PlanBenefit.pbp_id == pbp.strip()]
        if segment is not None:
            conds.append(PlanBenefit.segment_id == str(segment).strip())
        if year:
            conds.append(PlanBenefit.year == year)
        with SessionLocal(bind=self.engine) as s:
            return s.execute(
                select(PlanBenefit).where(*conds).order_by(PlanBenefit.year.desc()).limit(1)
            ).scalars().first()

    def by_contract(self, contract: str, year: int | None = None) -> list[PlanBenefit]:
        if not contract:
            return []
        conds = [PlanBenefit.contract_number == contract.strip().upper()]
        if year:
            conds.append(PlanBenefit.year == year)
        with SessionLocal(bind=self.engine) as s:
            return list(s.execute(
                select(PlanBenefit).where(*conds).order_by(PlanBenefit.contract_number, PlanBenefit.pbp_id)
            ).scalars().all())

    def search(self, hint: str | None, year: int | None = None, limit: int = 80) -> list[PlanBenefit]:
        toks = sorted(_tokens(hint), key=len, reverse=True)[:5]
        if not toks:
            return []
        likes = [PlanBenefit.plan_name.ilike(f"%{t}%") for t in toks]
        likes += [PlanBenefit.org_marketing_name.ilike(f"%{t}%") for t in toks]
        conds = [or_(*likes)]
        if year:
            conds.append(PlanBenefit.year == year)
        with SessionLocal(bind=self.engine) as s:
            return list(s.execute(
                select(PlanBenefit).where(*conds).order_by(PlanBenefit.contract_number, PlanBenefit.pbp_id).limit(limit)
            ).scalars().all())

    def resolve(
        self, plan_hint: str | None, contract: str | None = None, year: int | None = None
    ) -> PlanBenefit | None:
        """Best-effort: resolve a member plan string to a specific PBP plan. Prefers the contract's
        own plans (when an H-number is known) over a global name search. Returns None when it is
        unresolved or ambiguous across plan types — the caller then defers to the 271."""
        year = year or self.latest_year()
        candidates = self.by_contract(contract, year) if contract else self.search(plan_hint, year)
        return best_plan_match(plan_hint, candidates)

    def upsert(self, records, tenant_id=None) -> int:
        """Insert PlanBenefitRecords idempotently, keyed by (tenant_id, contract, pbp, segment, year).
        Re-running the ingest is a no-op. Returns the number of NEW rows written."""
        wanted = [r for r in records if getattr(r, "contract_number", None) and getattr(r, "pbp_id", None)]
        if not wanted:
            return 0
        years = {r.year for r in wanted}
        n = 0
        with SessionLocal(bind=self.engine) as s:
            existing: set = set()
            for row in s.execute(
                select(
                    PlanBenefit.contract_number, PlanBenefit.pbp_id, PlanBenefit.segment_id, PlanBenefit.year
                ).where(PlanBenefit.year.in_(years))
            ).all():
                existing.add((row.contract_number, row.pbp_id, row.segment_id, row.year))
            for r in wanted:
                key = (r.contract_number, r.pbp_id, r.segment_id or "0", r.year)
                if key in existing:
                    continue
                s.add(PlanBenefit(
                    tenant_id=tenant_id, contract_number=r.contract_number, pbp_id=r.pbp_id,
                    segment_id=r.segment_id or "0", year=r.year, plan_type=r.plan_type,
                    plan_type_code=r.plan_type_code, plan_name=r.plan_name,
                    org_marketing_name=r.org_marketing_name, snp_type_code=r.snp_type_code,
                    dsnp=bool(r.dsnp), network_flag=r.network_flag, inn_moop=r.inn_moop,
                    comb_moop_yn=r.comb_moop_yn or None, comb_moop=r.comb_moop,
                    oon_moop_yn=r.oon_moop_yn or None, oon_moop=r.oon_moop,
                ))
                existing.add(key)
                n += 1
            s.commit()
        return n


_DEFAULT: PlanBenefitStore | None = None


def default_plan_benefit_store() -> PlanBenefitStore:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = PlanBenefitStore()
    return _DEFAULT
