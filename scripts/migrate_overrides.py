"""Migrate legacy global JSON overrides (.overrides/overrides.json) into the tenant-scoped DB store."""
from __future__ import annotations

import json
from pathlib import Path

from network_probe.overrides import DbOverrideStore, Override


def migrate(json_path: Path, tenant_id) -> int:
    p = Path(json_path)
    if not p.exists():
        return 0
    raw = json.loads(p.read_text(encoding="utf-8"))
    store = DbOverrideStore(tenant_id)
    existing = {(o.payer, o.npi, o.status, o.verified_at) for o in store._items()}
    n = 0
    for rec in raw:
        ov = Override(**rec)
        key = (ov.payer, ov.npi, ov.status, ov.verified_at)
        if key not in existing:
            store.add(ov)
            existing.add(key)
            n += 1
    return n


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("Usage: python -m scripts.migrate_overrides <json_path> <tenant_id>")
        sys.exit(1)
    count = migrate(Path(sys.argv[1]), sys.argv[2])
    print(f"Migrated {count} override(s).")
