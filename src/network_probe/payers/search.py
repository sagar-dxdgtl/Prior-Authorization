"""Payer search for the UI select: curated roster first, Stedi live directory as fallback.

Pure functions (search_roster/search_stedi) so ranking + mapping are unit-tested without a DB or
network; load_roster_rows() is the thin DB read the endpoint uses.
"""

from __future__ import annotations

import os
import re

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


# Client abbreviations whose letters don't appear in the canonical label, so plain substring/token
# matching can never bridge them. Expanded in the query (longest key first) before tokenizing, so
# "UHC"/"AARP" reach "unitedhealthcare" and spelled-out Blues collapse to the "bcbs" brand token.
_ALIASES = {
    "united healthcare": "unitedhealthcare",
    "blue cross blue shield": "bcbs",
    "blue cross": "bcbs",
    "blue shield": "bcbs",
    "uhc": "unitedhealthcare",
    "uhg": "unitedhealthcare",
    "aarp": "unitedhealthcare",
    "bcbsa": "bcbs",
}

# Benefit / plan / geography noise dropped before brand-token matching. A match must rest on a real
# brand token, never on one of these — otherwise a filler word like "state" links unrelated plans
# ("Sunshine State" -> "Peach State") and "Medicare Advantage" matches every MA row.
_STOPWORDS = {
    "medicare", "medicaid", "advantage", "aca", "commercial", "managed", "traditional", "dual",
    "eligible", "fide", "snp", "secondary", "hmo", "ppo", "epo", "pos", "plan", "plans", "part",
    "senior", "health", "healthcare", "the", "of", "and", "inc", "llc", "co", "company",
    "solutions", "services", "state",
    # geography: state names, abbreviations, and the demo metros used as market suffixes
    "arizona", "az", "colorado", "florida", "fl", "georgia", "ga", "illinois", "il", "texas", "tx",
    "jersey", "york", "new", "nj", "ny", "denver", "tampa", "atlanta", "dallas", "houston", "miami",
    "south", "california",
}

# Query geo token -> state-code prefix. Used only to RANK a same-state row higher, never to match:
# the row's `state` is like "AZ" / "CO-Denver" / "TX - Houston", so we compare on the leading code.
_STATE_HINTS = {
    "az": "AZ", "arizona": "AZ", "co": "CO", "colorado": "CO", "denver": "CO",
    "fl": "FL", "florida": "FL", "tampa": "FL", "ga": "GA", "georgia": "GA", "atlanta": "GA",
    "il": "IL", "illinois": "IL", "tx": "TX", "texas": "TX", "dallas": "TX", "houston": "TX",
    "ny": "NY", "nj": "NJ",
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())


def _apply_aliases(s: str) -> str:
    for k, v in _ALIASES.items():
        s = re.sub(rf"\b{re.escape(k)}\b", v, s)
    return s


def _sig_tokens(s: str) -> list[str]:
    """Significant (brand) tokens: normalized, alias-expanded, minus benefit/geography stopwords."""
    return [t for t in _apply_aliases(_norm(s)).split() if t not in _STOPWORDS]


def _state_hints(s: str) -> set[str]:
    return {_STATE_HINTS[t] for t in _norm(s).split() if t in _STATE_HINTS}


def _state_arg_codes(state: str | None) -> set[str]:
    """State-code prefixes from an explicit state field ("FL", "Florida", "CO-Denver" -> CO)."""
    codes: set[str] = set()
    for t in _norm(state).split():
        if t in _STATE_HINTS:
            codes.add(_STATE_HINTS[t])
        elif len(t) == 2 and t.isalpha():
            codes.add(t.upper())
    return codes


def _benefit_bucket(s: str) -> str | None:
    """Coarse benefit class shared by a query hint and a row's benefit_type, for ranking only."""
    b = (s or "").lower()
    if "medicaid" in b:
        return "medicaid"
    if "dual" in b or "snp" in b:
        return "dual"
    if "medicare" in b or "advantage" in b:
        return "medicare"
    if "commercial" in b:
        return "commercial"
    if "aca" in b or "marketplace" in b or "exchange" in b:
        return "aca"
    return None


def _covers(label_token: str, q_tokens: list[str]) -> bool:
    # A label's brand token is covered if a query token equals it or one is a prefix of the other,
    # so "united" reaches "unitedhealthcare" and the "unitedhealthcare" alias matches it exactly.
    return any(q == label_token or q.startswith(label_token) or label_token.startswith(q) for q in q_tokens)


def search_roster(rows: list[dict], q: str, limit: int = 20, state: str | None = None) -> list[dict]:
    """Token-set match of a free-text payer name against canonical roster labels.

    Client rosters name payers far more verbosely than the catalogue ("UHC AARP Medicare Advantage"
    vs "UnitedHealthcare"), so a raw substring test misses. Instead: expand abbreviations, drop
    benefit/geography noise, and match on shared *brand* tokens — then rank by exactness, how fully
    the label is covered, shared-token count, and same-state / same-benefit hints.

    `state` is the member's state from the form (e.g. "FL"); it biases ranking toward that market
    even when the typed payer text carries no state token, so per-member auto-resolution lands on
    the right row instead of an arbitrary market.
    """
    q_tokens = _sig_tokens(q)
    if not q_tokens:
        return []
    q_norm = _norm(q).strip()
    q_states = _state_hints(q) | _state_arg_codes(state)
    q_benefit = _benefit_bucket(q)

    scored: list[tuple[tuple, dict]] = []
    for r in rows:
        label = r.get("label") or ""
        l_tokens = _sig_tokens(label)
        if not l_tokens:
            continue
        shared = sum(1 for lt in l_tokens if _covers(lt, q_tokens))
        key_hit = bool(q_norm) and q_norm in (r.get("key") or "").lower()
        if shared == 0 and not key_hit:
            continue
        exact = 0 if _norm(label).strip() == q_norm else 1
        covers_all = 0 if shared == len(l_tokens) else 1  # every brand token of the label is present
        state_boost = 0 if any((r.get("state") or "").upper().startswith(st) for st in q_states) else 1
        benefit_boost = 0 if (q_benefit and _benefit_bucket(r.get("benefit_type")) == q_benefit) else 1
        # state_boost outranks covers_all: when the caller gives a state, a same-state row must win
        # over a different-state label that merely "covers all" the query tokens (e.g. a junk
        # "BCBS (Anthem)" IL row beating the real GA Blue for a GA query). With no state given,
        # state_boost is uniformly 1, so covers_all remains the effective tie-break.
        rank = (exact, state_boost, covers_all, -shared, benefit_boost, len(l_tokens), _norm(label))
        scored.append((rank, r))
    scored.sort(key=lambda t: t[0])
    # Dedupe by catalogue key: the roster has one row per (payer, market, benefit_type), but the
    # key is (payer, market) — so the same payer/market appears once per benefit type with an
    # identical value. AntD Select requires unique option values; keep the best-ranked one (which,
    # thanks to benefit_boost, is the row whose benefit type matches the query's Medicare/Medicaid).
    out: list[dict] = []
    seen_keys: set = set()
    for _, r in scored:
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
