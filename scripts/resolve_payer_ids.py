"""Fill stedi_payer_id for global payers flagged needs_payer_id, from Stedi's payer network.

Gated by STEDI_API_KEY — no-ops without it. Uses the LIVE-VERIFIED Stedi payers endpoint
``GET /2024-04-01/payers`` (each ``items[]`` entry has ``stediId``, ``primaryPayerId``,
``displayName``, ``conciseName``, ``names``, ``aliases``). The eligibility API's
``tradingPartnerServiceId`` accepts the ``primaryPayerId`` (verified: Aetna ``60054`` returns data).

DRY-RUN by default: prints proposed matches for human review. Auto-matching healthcare payer
names is fuzzy (e.g. "BCBS / Empire (Anthem / Elevance)"), so review before writing. Apply with::

    python scripts/resolve_payer_ids.py --apply
"""

from __future__ import annotations

import os
import re
import sys

from sqlalchemy import text

from network_probe.core._http import CachedClient
from network_probe.core.config import get_settings
from network_probe.core.secrets_provider import get_secret
from network_probe.db.base import owner_engine

PAYERS_URL = os.environ.get("STEDI_PAYERS_URL", "https://healthcare.us.stedi.com/2024-04-01/payers")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def search_payer(client: CachedClient, api_key: str, name: str):
    """Best-match tradingPartnerServiceId (primaryPayerId, fallback stediId) for a roster label, or None."""
    try:
        data = client.get_json(f"{PAYERS_URL}?query={name}", headers={"Authorization": api_key})
    except Exception:
        return None
    want = _norm(name)
    if not want:
        return None
    for it in data.get("items") or []:
        candidates = [
            it.get("displayName"),
            it.get("conciseName"),
            *(it.get("names") or []),
            *(it.get("aliases") or []),
        ]
        for c in candidates:
            nc = _norm(c)
            if nc and (nc == want or want in nc or nc in want):
                return it.get("primaryPayerId") or it.get("stediId")
    return None


def resolve_all(client: CachedClient | None = None, apply: bool = False) -> int:
    api_key = get_settings().stedi_api_key or get_secret("STEDI_API_KEY")
    if not api_key:
        print("STEDI_API_KEY not set — skipping payer-id resolution.")
        return 0
    client = client or CachedClient(cache_dir=None, delay_seconds=0.3)
    n = 0
    with owner_engine().begin() as conn:
        rows = (
            conn.execute(text("SELECT id, label FROM payers WHERE tenant_id IS NULL AND stedi_payer_id IS NULL"))
            .mappings()
            .all()
        )
        for r in rows:
            pid = search_payer(client, api_key, r["label"])
            if pid:
                print(f"  {'APPLY' if apply else 'PROPOSE'}: {r['label']!r} -> {pid}")
                if apply:
                    conn.execute(
                        text(
                            "UPDATE payers SET stedi_payer_id=:pid, enrollment_status='needs_enrollment' WHERE id=:id"
                        ),
                        {"pid": pid, "id": r["id"]},
                    )
                n += 1
            else:
                print(f"  no confident match: {r['label']!r}")
    tail = "" if apply else " Re-run with --apply after reviewing the proposals."
    print(f"{'Applied' if apply else 'Proposed'} {n} payer id(s).{tail}")
    return n


if __name__ == "__main__":
    resolve_all(apply="--apply" in sys.argv)
