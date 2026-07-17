"""TiC provider-network signal — the commercial, live half of provider-INN.

A billing TIN found in a payer's real Transparency-in-Coverage in-network MRF is decisive proof
the (provider, TIN) is contracted → IN_NETWORK. Absence is NOT proof of OON: MRFs are notoriously
incomplete (missing groups, representative-NPI masking, stale files), so a miss degrades to
UNKNOWN and the caller falls back to credentialing / directory. This asymmetry mirrors the
directory-confidence rule (we trust presence, distrust absence).

Data comes from the NPI→in-network-TIN crosswalk (tin_crosswalk.py): the in-code TiC-derived seed
plus any TIN_CROSSWALK_PATH bulk file. Only ever call this for COMMERCIAL lines — Medicare/Medicaid
are TiC-exempt (see line_of_business.is_commercial); a miss there is meaningless, not evidence.
"""

from __future__ import annotations

import re

from network_probe.domain.models import NetworkStatus


def _norm(t) -> str:
    return re.sub(r"[^0-9]", "", str(t or ""))


def tic_network_status(payer, npi, tin, crosswalk=None) -> tuple[NetworkStatus, list[str]]:
    """Return (status, known_tins) for this (payer, npi) from TiC.

    IN_NETWORK when `tin` is among the provider's contracted in-network TINs for `payer`; else
    UNKNOWN. `known_tins` are the in-network TINs the crosswalk holds for this (payer, npi) — surfaced
    for the reason line even on a miss.
    """
    if crosswalk is None:
        from network_probe.domain.tin_crosswalk import default_crosswalk

        crosswalk = default_crosswalk()
    known = crosswalk.tins_for(payer, npi) if crosswalk else []
    if tin and _norm(tin) in {_norm(t) for t in known}:
        return NetworkStatus.IN_NETWORK, known
    return NetworkStatus.UNKNOWN, known
