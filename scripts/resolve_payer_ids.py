"""Fill stedi_payer_id for global payers flagged needs_payer_id, from Stedi's payer network.
Gated by STEDI_API_KEY — no-ops without it. The exact Stedi payer-search endpoint/shape is
reconciled on the first live run (Task 27); structured here so it's a one-line endpoint fix."""
from __future__ import annotations
import os, sys
from sqlalchemy import text
from network_probe._http import CachedClient
from network_probe.secrets_provider import get_secret
from network_probe.db.base import owner_engine

PAYER_SEARCH_URL = os.environ.get(
    "STEDI_PAYER_SEARCH_URL", "https://healthcare.us.stedi.com/2024-04-01/payers/search")

def search_payer(client: CachedClient, api_key: str, name: str):
    try:
        data = client.get_json(f"{PAYER_SEARCH_URL}?query={name}", headers={"Authorization": api_key})
    except Exception:
        return None
    items = data.get("items") or data.get("payers") or data.get("results") or []
    for it in items:
        pid = it.get("stediId") or it.get("primaryPayerId") or it.get("payerId")
        if pid:
            return pid
    return None

def resolve_all(client: CachedClient | None = None) -> int:
    api_key = get_secret("STEDI_API_KEY")
    if not api_key:
        print("STEDI_API_KEY not set — skipping payer-id resolution.")
        return 0
    client = client or CachedClient(cache_dir=None, delay_seconds=0.3)
    updated = 0
    with owner_engine().begin() as conn:
        rows = conn.execute(text(
            "SELECT id, label FROM payers WHERE tenant_id IS NULL AND stedi_payer_id IS NULL")).mappings().all()
        for r in rows:
            pid = search_payer(client, api_key, r["label"])
            if pid:
                conn.execute(text(
                    "UPDATE payers SET stedi_payer_id=:pid, enrollment_status='needs_enrollment' WHERE id=:id"),
                    {"pid": pid, "id": r["id"]})
                updated += 1
                print(f"  {r['label']}: {pid}")
    print(f"Updated {updated} payer(s).")
    return updated

if __name__ == "__main__":
    resolve_all()
    sys.exit(0)
