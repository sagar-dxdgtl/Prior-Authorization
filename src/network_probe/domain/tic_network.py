"""TiC provider-network signal — the commercial, live half of provider-INN.

A billing TIN found in a payer's real Transparency-in-Coverage in-network MRF is decisive proof
the (provider, TIN) is contracted → IN_NETWORK. Absence is NOT proof of OON: MRFs are notoriously
incomplete (missing groups, representative-NPI masking, stale files), so a miss degrades to
UNKNOWN and the caller falls back to credentialing / directory. This asymmetry mirrors the
directory-confidence rule (we trust presence, distrust absence).

Data comes from BOTH TiC stores, which are not the same place:
  * the NPI→in-network-TIN crosswalk (tin_crosswalk.py) — the in-code TiC-derived seed plus any
    TIN_CROSSWALK_PATH bulk file;
  * the `provider_network_facts` table (network_facts.py), where `scripts/ingest_tic.py` lands a
    real MRF pull — filtered to source="tic" and in_network, so a directory read persisted as a
    fact never masquerades as MRF proof.

Only ever call this for COMMERCIAL lines — Medicare/Medicaid are TiC-exempt (see
line_of_business.is_commercial); a miss there is meaningless, not evidence.
"""

from __future__ import annotations

import re

from network_probe.domain.models import NetworkStatus


def _norm(t) -> str:
    return re.sub(r"[^0-9]", "", str(t or ""))


# provider_network_facts.source for MRF-derived rows. A directory read persisted as a fact is
# NOT Transparency-in-Coverage proof, so the TiC signal only ever counts this source.
_TIC_SOURCE = "tic"


def _default_store():
    """The persisted fact store, or None when there's no database (pure tests, CLI without a DB)."""
    try:
        from network_probe.domain.network_facts import default_provider_network_store

        return default_provider_network_store()
    except Exception:
        return None


def _tic_facts(store, payer, npi=None, tin=None) -> list:
    """In-network, TiC-sourced rows from `provider_network_facts`. Never raises: a store that is
    absent or unreachable yields no facts, which degrades to UNKNOWN — never to a false OON."""
    if store is None:
        return []
    try:
        rows = store.facts_for(payer, npi=npi, tin=tin)
    except Exception:
        return []
    return [
        f
        for f in rows
        if getattr(f, "in_network", False) and getattr(f, "source", None) == _TIC_SOURCE
    ]


def _resolved(crosswalk, store):
    if crosswalk is None:
        from network_probe.domain.tin_crosswalk import default_crosswalk

        crosswalk = default_crosswalk()
    return crosswalk, (store if store is not None else _default_store())


def tic_tin_in_roster(payer, tin, npi=None, crosswalk=None, store=None) -> tuple[bool, list[str]]:
    """Is this billing TIN in the payer's TiC in-network roster under ANY NPI?

    Returns (present, other_npis) — `other_npis` being the roster's NPIs at that TIN excluding
    `npi`. `present` with a non-empty `other_npis` and our NPI absent is the **physician gap**: the
    group is contracted and this physician is not. That is the only TiC-derived OUT_OF_NETWORK;
    a TIN *missing* from an MRF still proves nothing.
    """
    if not tin:
        return False, []
    crosswalk, store = _resolved(crosswalk, store)
    facts = _tic_facts(store, payer, tin=tin)
    others = sorted({str(f.npi) for f in facts if f.npi and str(f.npi) != str(npi or "")})
    present = bool(facts) or bool(crosswalk and crosswalk.has_tin(payer, tin))
    return present, others


def tic_roster_networks(payer, tin, store=None) -> list[str]:
    """The network label(s) the persisted in-network facts for (payer, tin) came from.

    A payer key can span several networks — Oscar GA sells both 063 (Guided Care) and 065 (Open
    Access), and a TIN in one roster says nothing about the other. Surfacing the label keeps a
    physician-gap note from reading as "contracted with Oscar GA" when the evidence is really
    "contracted in Oscar GA network 065". Empty when the facts carry no label.
    """
    if not tin:
        return []
    facts = _tic_facts(store if store is not None else _default_store(), payer, tin=tin)
    return sorted({n for f in facts if (n := getattr(f, "network_name", None))})


def tic_network_status(payer, npi, tin, crosswalk=None, store=None) -> tuple[NetworkStatus, list[str]]:
    """Return (status, known_tins) for this (payer, npi) from TiC.

    IN_NETWORK when `tin` is among the provider's contracted in-network TINs for `payer`; else
    UNKNOWN. `known_tins` are the in-network TINs known for this (payer, npi) — surfaced for the
    reason line even on a miss.

    Reads BOTH TiC stores: the in-code/bulk-file crosswalk seed *and* the `provider_network_facts`
    rows that `scripts/ingest_tic.py` writes. They are different stores; consulting only the seed
    made every MRF ingest silently inert and put a false "TIN not found" in the audit trail.
    """
    crosswalk, store = _resolved(crosswalk, store)
    known = set(crosswalk.tins_for(payer, npi) if crosswalk else [])
    known |= {str(f.tin) for f in _tic_facts(store, payer, npi=npi) if f.tin}
    known_tins = sorted(known)
    if tin and _norm(tin) in {_norm(t) for t in known_tins}:
        return NetworkStatus.IN_NETWORK, known_tins
    return NetworkStatus.UNKNOWN, known_tins
