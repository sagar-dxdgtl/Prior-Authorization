"""Turn an `ingest_tic` crosswalk CSV into `provider_network_facts` rows.

`scripts/ingest_tic.py` streams a payer's Transparency-in-Coverage in-network MRF into a
`npi,tin,payer` CSV. `tic_network_status` reads the `provider_network_facts` table. Nothing joined
the two, so every MRF pull landed somewhere the signal could not see it — the P2 defect. This is
that join, kept as a pure function so it is testable without a database.

Two notes on the mapping:
  * `in_network` is always True. These CSVs come from an **in-network** MRF, which is a roster of
    contracted providers — it carries no out-of-network assertions at all.
  * `payer_key` comes from the caller, not the CSV. ingest_tic writes the short adapter key
    ("oscar"), while the store and the roster are keyed by market ("oscar-ga-atlanta").
"""

from __future__ import annotations

import csv
import re
from pathlib import Path


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def facts_from_crosswalk_csv(
    path, payer_key, source: str = "tic", network_name: str | None = None
) -> list[dict]:
    """Build de-duplicated ProviderNetworkStore.upsert() rows from an ingest_tic crosswalk CSV.

    Rows missing an NPI or a TIN are skipped: a fact needs both to mean anything.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            rec = {(k or "").strip().lower(): v for k, v in raw.items()}
            npi, tin = _digits(rec.get("npi")), _digits(rec.get("tin"))
            if not npi or not tin or (npi, tin) in seen:
                continue
            seen.add((npi, tin))
            fact = {
                "payer_key": payer_key,
                "npi": npi,
                "tin": tin,
                "in_network": True,
                "source": source,
            }
            if network_name:
                fact["network_name"] = network_name
            rows.append(fact)
    return rows
