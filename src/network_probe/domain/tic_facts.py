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
import datetime as dt
import re
from pathlib import Path

# The MRF's own build date, as the ingest labels record it — "…network (2026-06-14)" or
# "…in-network MRF, 2026-07-01". ISO only, and deliberately not a loose \d{4}.\d{2}.\d{2}: the same
# labels carry "pl-2ef-hr23" and "bounded 2-of-283 sample".
_BUILT_RE = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def source_built_at_from_label(label: str | None) -> dt.date | None:
    """When the payer BUILT this file, read out of the source label. None if it does not say.

    This is what separates a stale source from a genuine conflict, and it is not `retrieved_at` —
    that is when WE wrote the row, which says nothing about how old the payer's file already was.

    Ins Test 3 row 5 is the case in point: TiC says Raikar is IN the BCBS IL PPO roster, the portal
    read the same day says OUT. The label reads `…Participating Provider network (2026-06-14)`, so
    the file predates the portal read by 47 days and a contract ending in between explains both
    without either source being wrong. Row 3 was the mirror image — Oscar's TiC was stale because the
    contract STARTED after its MRF was built.

    None means "undated", never "built today". An undated source cannot be shown to be stale, so the
    reconciler must treat its disagreement as a real conflict rather than quietly ageing it out.
    """
    m = _BUILT_RE.search(label or "")
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None  # e.g. "(2026-13-45)" — a number shaped like a date is not a date


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
                # Stamp the file's own build date so a later disagreement can be dated rather than
                # argued. Omitted entirely when the label carries none — an absent value is
                # "undated", and defaulting it to today would make every stale file look fresh.
                built = source_built_at_from_label(network_name)
                if built:
                    fact["source_built_at"] = built
            rows.append(fact)
    return rows
