"""Pick the catalogue row whose line of business matches the member's plan.

Six roster keys carry four `benefit_type` rows each — `bcbs-empire-anthem-elevance-hcsc-il` is
ACA + Commercial + Managed Medicaid + Medicare Advantage under one key, because a Blue plan sells
all four in one market. `DbPayerCatalogue.resolve()` returns the FIRST match, which is right for
`stedi_payer_id` (consistent per payer) and wrong for `benefit_type` (differs per row).

The failure is silent and cuts both ways. That key resolved to **Managed Medicaid**, so
`line_of_business` marked Transparency-in-Coverage federally exempt and never consulted it — and
Ins Test 3 row 5, whose NPI *and* TIN are both present in BCBS IL's in-network MRF, rendered
UNKNOWN instead of IN_NETWORK. The same arbitrary pick could equally have landed on Commercial and
fired TiC for a genuine Medicaid member, which is the over-claiming direction.

The member's plan is what actually decides the line, so it selects the row. With no plan there is
nothing to decide on, and this deliberately does NOT invent one — it keeps the catalogue's own
order, exactly as before. Guessing there is what produces the two failures above.
"""

from __future__ import annotations

from network_probe.domain.line_of_business import line_of_business

#: line_of_business() token -> the benefit_type values that represent it, best first. ACA and
#: Commercial are both TiC-eligible, so a commercial plan may legitimately select either.
_LOB_TO_BENEFIT: dict[str, tuple[str, ...]] = {
    "commercial": ("Commercial", "ACA"),
    "medicare": ("Medicare Advantage",),
    "medicaid": ("Managed Medicaid",),
    "dual": ("Dual Eligible (FIDE SNP)", "Medicare Advantage"),
    "federal": ("Commercial",),
}


def _rows_for(payer_key: str, catalogue):
    if catalogue is not None and hasattr(catalogue, "rows_for"):
        return catalogue.rows_for(payer_key) or []
    try:
        from sqlalchemy import select

        from network_probe.db.base import SessionLocal, app_engine
        from network_probe.db.models import Payer
        from network_probe.payers.catalogue import _slug

        want = _slug(payer_key)
        with SessionLocal(bind=app_engine()) as s:
            rows = s.execute(select(Payer)).scalars().all()
        hit = [p for p in rows if _slug(p.key) == want]
        return hit or [p for p in rows if _slug(p.label) == want]
    except Exception:  # noqa: BLE001 — no DB is not an error here, just no rows
        return []


def benefit_type_for(payer_key: str, plan: str | None, catalogue=None) -> str | None:
    """The `benefit_type` of the catalogue row matching `plan`'s line of business.

    Falls back to the catalogue's first row when the key has only one, when the plan gives no
    signal, or when no row represents the plan's line — never invents a line of business.
    """
    rows = _rows_for(payer_key, catalogue)
    if not rows:
        return None
    available = [getattr(r, "benefit_type", None) for r in rows]
    if len(rows) == 1 or not (plan or "").strip():
        return available[0]

    lob = line_of_business(plan, None)  # the PLAN alone decides — not any row's own tag
    for preferred in _LOB_TO_BENEFIT.get(lob, ()):
        if preferred in available:
            return preferred
    return available[0]
