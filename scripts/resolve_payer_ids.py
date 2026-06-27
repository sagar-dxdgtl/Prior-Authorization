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

_STOPWORDS = frozenset(
    {
        "health",
        "healthcare",
        "plan",
        "plans",
        "inc",
        "llc",
        "the",
        "of",
        "and",
        "care",
        "insurance",
        "company",
        "co",
        "group",
        "system",
        "services",
        "solutions",
    }
)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str) -> frozenset:
    """Lowercase alphanumeric words, dropping stopwords and tokens ≤2 chars."""
    words = re.findall(r"[a-z0-9]+", (s or "").lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOPWORDS)


def search_payer(client: CachedClient, api_key: str, label: str):
    """Best-match (tradingPartnerServiceId, matched_displayName) for a roster label, or None.

    Matches only on name fields (displayName, conciseName, names) — aliases are excluded
    to avoid substring false matches like 'AZ', 'ID', 'SD' hitting payer names that
    contain those two-letter sequences as substrings.

    Scoring: token-overlap of lowercase-alphanumeric words (stopwords and ≤2-char tokens
    dropped). score = |want ∩ cand| / |want|, with +1.0 bonus on exact normalised match.
    Requires score ≥ 0.5 and at least one shared token.
    """
    try:
        data = client.get_json(f"{PAYERS_URL}?query={label}", headers={"Authorization": api_key})
    except Exception:
        return None

    want = _tokens(label)
    if not want:
        return None

    norm_label = _norm(label)
    best_score = -1.0
    best_result = None

    for it in data.get("items") or []:
        display_name = it.get("displayName") or ""
        concise_name = it.get("conciseName") or ""
        # Aliases deliberately excluded — they are short payer-id codes that produce
        # false substring matches (e.g. alias "AZ" hits "arizonahealthcarecostcontainmentsystemahcccs").
        name_fields = [display_name, concise_name] + list(it.get("names") or [])

        # Exact-norm bonus (rewards displayName/conciseName equality with label)
        bonus = 1.0 if (_norm(display_name) == norm_label or _norm(concise_name) == norm_label) else 0.0

        # Token union across all name fields
        cand: frozenset = frozenset().union(*(_tokens(f) for f in name_fields))

        overlap = want & cand
        if not overlap:
            continue

        score = len(overlap) / len(want) + bonus
        if score > best_score:
            best_score = score
            pid = it.get("primaryPayerId") or it.get("stediId")
            best_result = (pid, display_name)

    if best_score >= 0.5 and best_result is not None and best_result[0]:
        return best_result
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
            result = search_payer(client, api_key, r["label"])
            if result:
                pid, display_name = result
                print(f"  {'APPLY' if apply else 'PROPOSE'}: {r['label']!r} -> {pid}  ({display_name})")
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
