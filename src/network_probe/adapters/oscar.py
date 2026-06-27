"""Oscar Health adapter.

Built strictly against the endpoint contract in DISCOVERY.md (reverse-engineered
2026-06-22 from hioscar.com, guest access, no auth required).

Flow (see DISCOVERY.md §"Verdict logic"):
  1. resolve plan_hint + state + year -> networkId   (get-network-plans / networks)
  2. search providers by last name within that network (autocomplete, capped at 10)
  3. match the target by NPI (exact); fall back to strict first+last name
  4a. matched   -> fetch provider profile -> read offices[].network_infos for the network/year
  4b. not matched, result count < cap -> OUT_OF_NETWORK (absence is conclusive)
  4c. not matched, result count == cap -> UNKNOWN (provider may be hidden behind the cap)

Honesty rule: ambiguity -> UNKNOWN, never a silent OUT_OF_NETWORK.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import quote, urlencode

from network_probe.base import PayerAdapter
from network_probe.core._http import CachedClient
from network_probe.models import NetworkStatus, NetworkVerdict, ProviderQuery

BASE = "https://www.hioscar.com"

# The autocomplete endpoint hard-caps results at 10 (verified: `limit`/`pageSize`
# ignored). If we get this many and still don't find our provider, absence is
# inconclusive -> UNKNOWN rather than a wrong OON. See DISCOVERY.md.
AUTOCOMPLETE_CAP = 10

# categories=2 == providers only (avoids drug/facility fuzzy-match noise).
PROVIDER_CATEGORY = "2"

# Minimum share of a plan's word-tokens that must appear in the hint to accept a
# plan->network resolution. Below this we cannot map the plan -> UNKNOWN.
PLAN_MATCH_MIN_RECALL = 0.6


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _tokens(s: str) -> list[str]:
    return [t for t in re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split() if t]


def _plan_match_score(hint: str, candidate: str) -> tuple[float, float]:
    """Score how well `candidate` (a plan or network name) matches `hint`.

    Returns (combined_score, plan_token_recall). Combined is in [0, 2]:
    recall of candidate tokens within the hint + recall of hint tokens within the
    candidate. The second component breaks ties toward the more specific plan
    (e.g. "... CSR 150" over the base "...").
    """
    hint_c, cand_c = _norm(hint), _norm(candidate)
    cand_toks, hint_toks = _tokens(candidate), _tokens(hint)
    if not hint_c or not cand_c or not cand_toks or not hint_toks:
        return (0.0, 0.0)
    cand_recall = sum(1 for t in cand_toks if t in hint_c) / len(cand_toks)
    hint_recall = sum(1 for t in hint_toks if t in cand_c) / len(hint_toks)
    return (cand_recall + hint_recall, cand_recall)


def _to_date(d: dict | None) -> date | None:
    if not d:
        return None
    try:
        return date(d["year"], d["month"], d["day"])
    except (KeyError, TypeError, ValueError):
        return None


class OscarAdapter(PayerAdapter):
    payer_name = "oscar"

    def __init__(
        self,
        year: int | None = None,
        client: CachedClient | None = None,
        today: date | None = None,
    ):
        # year defaults to the current calendar year (the coverage year to check).
        self._today = today or date.today()
        self.year = year or self._today.year
        self.client = client or CachedClient()

    # ---- HTTP wrappers (one method per discovered endpoint) -----------------

    def _networks(self) -> dict:
        return self.client.get_json(f"{BASE}/search/api/v2/networks")

    def _network_plans(self, network_id: str, state: str) -> dict:
        q = urlencode({"networkId": network_id, "planYear": self.year, "state": state})
        return self.client.get_json(f"{BASE}/api/get-network-plans?{q}")

    def _autocomplete(self, network_id: str, name: str, state: str) -> list[dict]:
        q = urlencode(
            {
                "network_id": network_id,
                "categories": PROVIDER_CATEGORY,
                "query": name,
                "state": state,
                "year": self.year,
            }
        )
        url = f"{BASE}/search/autocomplete/multientity/?{q}"
        data = self.client.get_json(url)
        rows = []
        for r in data.get("results", []):
            if r.get("group_type") != 2:  # providers only
                continue
            f = (r.get("response_fields") or {}).get("doctor_name_fields") or {}
            rows.append(
                {
                    "entity_id": r.get("entity_id"),
                    "display_name": r.get("display_name"),
                    "npi": f.get("npi"),
                    "first_name": f.get("first_name"),
                    "last_name": f.get("last_name"),
                    "specialty": f.get("primary_specialty"),
                    "_source_url": url,
                }
            )
        return rows

    def _provider_profile(self, entity_id: str, network_id: str, state: str, zip_code: str) -> dict:
        q = urlencode(
            {
                "networkId": network_id,
                "planYear": self.year,
                "state": state or "",
                "zip_code": zip_code or "",
            }
        )
        return self.client.get_json(
            f"{BASE}/api/provider-profile/legacy-initial-data-api/{quote(entity_id)}?{q}"
        )

    # ---- network resolution -------------------------------------------------

    def _fl_networks(self, state: str) -> list[dict]:
        """All Oscar networks covering `state` for self.year: {id, name, type}."""
        nby = self._networks().get("networkDetailsByYear", {})
        year_map = nby.get(str(self.year), {})
        out = []
        for nid, info in year_map.items():
            for area_name, area in (info.get("coverageAreas") or {}).items():
                if (area.get("state") or "").upper() == (state or "").upper():
                    out.append(
                        {
                            "id": nid,
                            "name": info.get("name") or area_name,
                            "area": area_name,
                            "type": info.get("networkType"),
                        }
                    )
        return out

    def resolve_network(self, plan_hint: str, state: str) -> dict | None:
        """Map a free-text plan_hint to exactly one network for `state`/year.

        Returns {network_id, network_name, matched_plan, policy_id, score, source_url}
        or None when no candidate clears PLAN_MATCH_MIN_RECALL.
        """
        networks = self._fl_networks(state)
        best = None  # (combined, cand_recall, payload)

        for net in networks:
            # 1) the plan_hint might *be* a network/area name
            for label in (net["area"], net["name"]):
                score, recall = _plan_match_score(plan_hint, label)
                cand = {
                    "network_id": net["id"],
                    "network_name": net["area"],
                    "matched_plan": None,
                    "policy_id": None,
                }
                if best is None or (score, recall) > (best[0], best[1]):
                    best = (score, recall, cand)

            # 2) match against the actual plan names in this network
            data = self._network_plans(net["id"], state)
            for grp in data.get("plans", []):
                for opt in grp.get("options", []):
                    policy_id, plan_name = opt[0], opt[1]
                    score, recall = _plan_match_score(plan_hint, plan_name)
                    cand = {
                        "network_id": net["id"],
                        "network_name": net["area"],
                        "matched_plan": plan_name,
                        "policy_id": policy_id,
                    }
                    if best is None or (score, recall) > (best[0], best[1]):
                        best = (score, recall, cand)

        if not best or best[1] < PLAN_MATCH_MIN_RECALL:
            return None
        result = dict(best[2])
        result["score"] = round(best[0], 3)
        result["source_url"] = (
            f"{BASE}/api/get-network-plans?networkId={result['network_id']}"
            f"&planYear={self.year}&state={state}"
        )
        return result

    # ---- participation parsing ---------------------------------------------

    def _participation(self, profile: dict, network_id: str) -> tuple[NetworkStatus, str, str, dict]:
        """Read offices[].network_infos for `network_id` and the coverage year.

        Returns (status, confidence, notes, extras) where extras carries per-TIN and freshness
        signals (in_network_tins, going_oon_soon, last_inn_date) for the corroboration layer.
        """
        provider = (
            profile.get("reduxState", {}).get("providerProfile", {}).get("provider", {})
        )
        offices = provider.get("offices") or []

        y_start, y_end = date(self.year, 1, 1), date(self.year, 12, 31)
        recs_for_net = []
        any_network_infos = False
        for off in offices:
            for ni in off.get("network_infos") or []:
                any_network_infos = True
                if str(ni.get("provider_network_id")) != str(network_id):
                    continue
                s = _to_date(ni.get("start_date")) or date.min
                e = _to_date(ni.get("end_date")) or date.max
                if s <= y_end and e >= y_start:  # intersects the coverage year
                    recs_for_net.append((s, e, bool(ni.get("in_network")), ni))

        # Oscar's own resolved flag for the requested network + today (cross-check)
        office_flag = any(bool(off.get("in_network")) for off in offices)
        going_oon = any(bool(off.get("going_oon_soon")) for off in offices)

        last_inn = next((off.get("last_inn_date") for off in offices if off.get("last_inn_date")), None)

        if not recs_for_net:
            if any_network_infos:
                # provider exists & has contracts, but none for this network/year
                return (
                    NetworkStatus.OUT_OF_NETWORK,
                    "medium",
                    f"Provider found, but has no participation record for network "
                    f"{network_id} intersecting {self.year}.",
                    {},
                )
            return (
                NetworkStatus.UNKNOWN,
                "low",
                "Provider found but profile carried no network participation records.",
                {},
            )

        # prefer records that cover today; else use all year-intersecting records
        covering_today = [r for r in recs_for_net if r[0] <= self._today <= r[1]]
        chosen = covering_today or recs_for_net
        in_net = any(r[2] for r in chosen)

        detail = "; ".join(
            f"net {ni.get('provider_network_id')} in_network={ni.get('in_network')} "
            f"{_to_date(ni.get('start_date'))}..{_to_date(ni.get('end_date'))}"
            for *_x, ni in chosen
        )
        if in_net:
            in_tins = sorted({str(ni.get("tin")) for *_x, ni in chosen
                              if ni.get("in_network") and ni.get("tin")})
            extras = {"in_network_tins": in_tins, "going_oon_soon": going_oon, "last_inn_date": last_inn}
            note = f"Active in-network record for network {network_id}, {self.year} [{detail}]."
            if going_oon:
                note += " NOTE: provider flagged going_oon_soon."
            return (NetworkStatus.IN_NETWORK, "high", note, extras)

        # records exist for this network/year but none are in-network
        conf = "high" if office_flag is False else "medium"
        return (
            NetworkStatus.OUT_OF_NETWORK,
            conf,
            f"Records exist for network {network_id}/{self.year} but none are in-network [{detail}].",
            {},
        )

    # ---- main entrypoint ----------------------------------------------------

    def check_network(self, q: ProviderQuery) -> NetworkVerdict:
        state = (q.state or "").upper()
        urls: list[str] = []

        # 1) resolve plan_hint -> network
        resolved = self.resolve_network(q.plan_hint, state)
        if not resolved:
            return NetworkVerdict(
                status=NetworkStatus.UNKNOWN,
                matched_provider=None,
                plan_or_network_checked=f"{q.plan_hint} ({state})",
                source_url=f"{BASE}/api/get-network-plans?...&state={state}",
                confidence="low",
                notes=(
                    f"Could not map plan_hint {q.plan_hint!r} to an Oscar network in "
                    f"{state} for {self.year}. Refusing to guess a network."
                ),
            )
        nid = resolved["network_id"]
        checked = (
            f"{resolved['network_name']} (networkId={nid}, year={self.year})"
            + (f" / plan '{resolved['matched_plan']}'" if resolved.get("matched_plan") else "")
        )
        if resolved.get("source_url"):
            urls.append(resolved["source_url"])

        # 2) we need a last name to search (NPI search is unsupported by Oscar)
        if not q.last_name:
            return NetworkVerdict(
                status=NetworkStatus.UNKNOWN,
                matched_provider=None,
                plan_or_network_checked=checked,
                source_url="; ".join(urls),
                confidence="low",
                notes="Oscar directory supports name search only (no NPI lookup); a last name is required.",
            )

        hits = self._autocomplete(nid, q.last_name, state)
        if hits:
            urls.append(hits[0]["_source_url"])
        else:
            urls.append(
                f"{BASE}/search/autocomplete/multientity/?network_id={nid}"
                f"&categories={PROVIDER_CATEGORY}&query={quote(q.last_name)}&state={state}&year={self.year}"
            )

        # 3) match by NPI (exact); fall back to strict first+last name
        match = None
        if q.npi:
            match = next((h for h in hits if (h.get("npi") or "") == q.npi), None)
        if match is None and q.first_name:
            fn, ln = q.first_name.strip().lower(), q.last_name.strip().lower()
            match = next(
                (
                    h
                    for h in hits
                    if (h.get("last_name") or "").strip().lower() == ln
                    and (h.get("first_name") or "").strip().lower() == fn
                ),
                None,
            )

        # 4a) matched -> authoritative participation from the profile
        if match:
            profile = self._provider_profile(match["entity_id"], nid, state, q.zip_code or "")
            urls.append(
                f"{BASE}/api/provider-profile/legacy-initial-data-api/{match['entity_id']}"
                f"?networkId={nid}&planYear={self.year}"
            )
            status, conf, notes, extras = self._participation(profile, nid)
            return NetworkVerdict(
                status=status,
                matched_provider={**match, **extras},
                plan_or_network_checked=checked,
                source_url="; ".join(urls),
                confidence=conf,
                notes=f"Matched provider {match['display_name']} (NPI {match.get('npi')}). " + notes,
            )

        # 4b/4c) not matched -> absence reasoning, guarded by the result cap
        cap_hit = len(hits) >= AUTOCOMPLETE_CAP
        if not cap_hit:
            return NetworkVerdict(
                status=NetworkStatus.OUT_OF_NETWORK,
                matched_provider=None,
                plan_or_network_checked=checked,
                source_url="; ".join(urls),
                confidence="high" if hits else "medium",
                notes=(
                    f"Searched network {nid} for last name {q.last_name!r}: {len(hits)} provider(s) "
                    f"returned (below the {AUTOCOMPLETE_CAP}-result cap), none matching the target "
                    f"{'NPI ' + q.npi if q.npi else 'name'}. Provider is not in this network's directory."
                ),
            )
        return NetworkVerdict(
            status=NetworkStatus.UNKNOWN,
            matched_provider=None,
            plan_or_network_checked=checked,
            source_url="; ".join(urls),
            confidence="low",
            notes=(
                f"Search for last name {q.last_name!r} hit the {AUTOCOMPLETE_CAP}-result cap and the "
                f"target was not among them. Cannot rule out that the provider is hidden behind the "
                f"cap — returning UNKNOWN rather than a possibly-wrong OON. Narrow by first name."
            ),
        )
