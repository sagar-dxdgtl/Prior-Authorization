"""Payer search for the UI select: curated roster first, Stedi live directory as fallback.

Pure functions (search_roster/search_stedi) so ranking + mapping are unit-tested without a DB or
network; load_roster_rows() is the thin DB read the endpoint uses.
"""

from __future__ import annotations

import os

from sqlalchemy import select

from network_probe.core._http import CachedClient
from network_probe.db.base import SessionLocal, app_engine
from network_probe.db.models import Payer

PAYERS_URL = os.environ.get("STEDI_PAYERS_URL", "https://healthcare.us.stedi.com/2024-04-01/payers")


def _option(*, value, label, market, benefit_type, stedi_payer_id, enrollment_status, source):
    return {
        "value": value, "label": label, "market": market, "benefit_type": benefit_type,
        "stedi_payer_id": stedi_payer_id, "enrollment_status": enrollment_status, "source": source,
    }


def search_roster(rows: list[dict], q: str, limit: int = 20) -> list[dict]:
    ql = (q or "").strip().lower()
    if not ql:
        return []
    scored: list[tuple[int, str, dict]] = []
    for r in rows:
        label = r.get("label") or ""
        key = r.get("key") or ""
        hay = label.lower()
        if ql not in hay and ql not in key.lower():
            continue
        rank = 0 if hay == ql else (1 if hay.startswith(ql) else 2)
        scored.append((rank, hay, r))
    scored.sort(key=lambda t: (t[0], t[1]))
    # Dedupe by catalogue key: the roster has one row per (payer, market, benefit_type), but the
    # key is (payer, market) — so the same payer/market appears once per benefit type with an
    # identical value. AntD Select requires unique option values; keep the best-ranked one.
    out: list[dict] = []
    seen_keys: set = set()
    for _, _, r in scored:
        key = r.get("key")
        if key in seen_keys:
            continue
        seen_keys.add(key)
        out.append(
            _option(
                value=key, label=r.get("label"), market=r.get("state"),
                benefit_type=r.get("benefit_type"), stedi_payer_id=r.get("stedi_payer_id"),
                enrollment_status=r.get("enrollment_status"), source="roster",
            )
        )
        if len(out) >= limit:
            break
    return out


def search_stedi(client: CachedClient, api_key: str, q: str, limit: int = 20) -> list[dict]:
    try:
        data = client.get_json(f"{PAYERS_URL}?query={q}", headers={"Authorization": api_key})
    except Exception:
        return []
    out: list[dict] = []
    for it in data.get("items") or []:
        pid = it.get("primaryPayerId") or it.get("stediId")
        if not pid:
            continue
        out.append(
            _option(
                value=f"stedi:{pid}", label=it.get("displayName") or it.get("conciseName") or "",
                market=None, benefit_type=None, stedi_payer_id=pid, enrollment_status=None, source="stedi",
            )
        )
        if len(out) >= limit:
            break
    return out


def load_roster_rows() -> list[dict]:
    with SessionLocal(bind=app_engine()) as s:
        payers = s.execute(select(Payer).where(Payer.tenant_id.is_(None))).scalars().all()
        return [
            {
                "label": p.label, "key": p.key, "benefit_type": p.benefit_type, "state": p.state,
                "stedi_payer_id": p.stedi_payer_id, "enrollment_status": p.enrollment_status,
            }
            for p in payers
        ]
