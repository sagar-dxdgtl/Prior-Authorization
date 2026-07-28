"""resolve_provider_network — the plan-type-aware provider-INN resolver that sits at the top of
check_network. It fuses two sources by line of business:

  * credentialing matrix (clinic's own contract; all lines; covers IN and OON)
  * TiC MRF (live; COMMERCIAL only; can prove IN, never OON)

Commercial: fuse both (agree / conflict→REVIEW / whichever has data). Medicare/Medicaid/Dual:
credentialing only — TiC is federally exempt, so it's never consulted (honest N/A, not a blank).
"""

import re
from types import SimpleNamespace

from network_probe.domain.credentialing import CredentialingMatrix, CredentialRecord
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.provider_network import resolve_provider_network
from network_probe.domain.tin_crosswalk import TinCrosswalk


def _cred(*rows):
    return CredentialingMatrix(records=[CredentialRecord(*r) for r in rows])


def _cw(*recs):
    return TinCrosswalk(records=[{"payer": p, "npi": n, "tin": t} for (p, n, t) in recs])


def _q(payer, npi, tin, plan=None):
    return ProviderQuery(payer=payer, npi=npi, tin=tin, plan_hint=plan)


# ---- commercial: TiC is the live source ----

def test_commercial_tic_in_no_credentialing_is_in_via_tic():
    q = _q("ambetter-centene-tx-houston", "1710305735", "933510922", plan="Ambetter ACA PPO")
    v = resolve_provider_network(q, benefit_type="ACA", credentialing=_cred(),
                                 crosswalk=_cw(("ambetter-centene-tx-houston", "1710305735", "933510922")))
    assert v is not None
    assert v.status == NetworkStatus.IN_NETWORK
    assert "tic" in (v.source_url or "").lower()


def test_commercial_credentialing_oon_but_tic_in_is_review_conflict():
    # Cigna CO: clinic says OON, but the billing TIN appears in Cigna's real in-network MRF → REVIEW
    q = _q("cigna-healthcare-co-denver", "1629339312", "475181686", plan="Cigna Open Access Plus")
    v = resolve_provider_network(
        q, benefit_type="Commercial",
        credentialing=_cred(("cigna-healthcare-co-denver", "1629339312", "475181686", False, "Cigna Commercial CO")),
        crosswalk=_cw(("cigna-healthcare-co-denver", "1629339312", "475181686")),
    )
    assert v.status == NetworkStatus.REVIEW
    assert any(s.get("source") == "TIC" and s.get("result") == "contradicts" for s in (v.corroboration or []))


def test_commercial_credentialing_and_tic_agree_in():
    q = _q("bcbs-empire-anthem-elevance-az", "1992078745", "843447602", plan="BCBS AZ PPO")
    v = resolve_provider_network(
        q, benefit_type="Commercial",
        credentialing=_cred(("bcbs-empire-anthem-elevance-az", "1992078745", "843447602", True, "BCBS AZ")),
        crosswalk=_cw(("bcbs-empire-anthem-elevance-az", "1992078745", "843447602")),
    )
    assert v.status == NetworkStatus.IN_NETWORK


def test_commercial_credentialing_only_tic_silent_uses_credentialing():
    # TiC has no row (Anthem masks TINs) → credentialing decides; TiC signal is inconclusive not a flip
    q = _q("bcbs-empire-anthem-elevance-ga-atlanta", "1902811656", "921600050", plan="BCBS Anthem GA")
    v = resolve_provider_network(
        q, benefit_type="Commercial",
        credentialing=_cred(("bcbs-empire-anthem-elevance-ga-atlanta", "1902811656", "921600050", True, "BCBS GA")),
        crosswalk=_cw(),
    )
    assert v.status == NetworkStatus.IN_NETWORK
    assert (v.source_url or "").startswith("credentialing")


def test_commercial_neither_source_falls_through_to_directory():
    q = _q("some-commercial-payer", "1111111111", "222222222", plan="Some PPO")
    v = resolve_provider_network(q, benefit_type="Commercial", credentialing=_cred(), crosswalk=_cw())
    assert v is None  # nothing decisive → let the directory leg run


# ---- Medicare / Medicaid / Dual: TiC is exempt, never consulted ----

def test_dual_uses_credentialing_and_marks_tic_na():
    # Birenbaum: UHC Dual Complete → OON from credentialing; TiC N/A (exempt), not a blank
    q = _q("unitedhealthcare-az", "1245461292", "843447602", plan="AZ UNITEDHEALTHCARE DUAL COMPLETE HMOPOS H0032")
    v = resolve_provider_network(
        q, benefit_type="Dual Eligible (FIDE SNP)",
        credentialing=_cred(("unitedhealthcare-az", "1245461292", "843447602", False, "UHC Dual Complete")),
        crosswalk=_cw(("unitedhealthcare-az", "1245461292", "843447602")),  # even if present, must be ignored
    )
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert (v.source_url or "").startswith("credentialing")
    sig = next((s for s in (v.corroboration or []) if s.get("source") == "TIC"), None)
    assert sig is not None and sig.get("result") == "n/a"
    assert "medicare" in sig.get("detail", "").lower() or "exempt" in sig.get("detail", "").lower()


def test_medicaid_no_credentialing_falls_through_without_tic():
    q = _q("mercy-care-az", "1992078745", "843447602", plan="Mercy Care AHCCCS")
    v = resolve_provider_network(q, benefit_type="Managed Medicaid", credentialing=_cred(),
                                 crosswalk=_cw(("mercy-care-az", "1992078745", "843447602")))
    assert v is None  # exempt line, no credentialing → directory leg, never a TiC-based IN


def test_no_tin_returns_none():
    q = _q("ambetter-centene-tx-houston", "1710305735", None, plan="Ambetter ACA")
    assert resolve_provider_network(q, benefit_type="ACA", credentialing=_cred(), crosswalk=_cw()) is None


# ---- P2: the physician gap — the ONLY TiC-derived OON --------------------------------
#
# Oscar FL: TIN 463812940 is in-network under 16 other NPIs in the ingested MRF; ours
# (1568423168, Clarke) is absent. Staff ground truth: "Physician OON".
# See HANDOFF-2026-07-28.md §0 and §2.

_OSCAR = "oscar-fl-south-florida"
_OSCAR_TIN = "463812940"
_CLARKE = "1568423168"


class _Roster:
    """Test double for ProviderNetworkStore holding the ingested Oscar FL TiC roster."""

    def __init__(self, npis, tin=_OSCAR_TIN, payer=_OSCAR):
        self._rows = [
            SimpleNamespace(payer_key=payer, npi=n, tin=tin, in_network=True, source="tic") for n in npis
        ]

    def facts_for(self, payer_key, npi=None, tin=None):
        return [
            f
            for f in self._rows
            if f.payer_key == payer_key
            and (not npi or f.npi == str(npi))
            and (not tin or f.tin == re.sub(r"\D", "", str(tin)))
        ]


def test_physician_gap_from_tic_is_oon_when_no_credentialing():
    q = _q(_OSCAR, _CLARKE, _OSCAR_TIN, plan="Oscar Silver Classic Standard")
    v = resolve_provider_network(
        q, benefit_type="ACA", credentialing=_cred(), crosswalk=_cw(),
        store=_Roster(["1699931030", "1760430029", "1790953933"]),
    )
    assert v is not None
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    # the determination layer needs this flag to label it Physician OON rather than payer-level OON
    assert v.matched_provider.get("group_contracted") is True
    assert "3" in v.notes  # the note states how many other NPIs carry the TIN


def test_tin_absent_from_the_roster_is_not_an_oon():
    """Absence of the TIN proves nothing — MRFs are incomplete. Must fall through."""
    q = _q(_OSCAR, _CLARKE, "999999999", plan="Oscar Silver Classic Standard")
    v = resolve_provider_network(
        q, benefit_type="ACA", credentialing=_cred(), crosswalk=_cw(),
        store=_Roster(["1699931030", "1760430029"]),
    )
    assert v is None


def test_credentialing_note_does_not_deny_a_tin_the_mrf_holds():
    """The audit note asserted the opposite of what the database held. It must not."""
    q = _q(_OSCAR, _CLARKE, _OSCAR_TIN, plan="Oscar Silver Classic Standard")
    v = resolve_provider_network(
        q, benefit_type="ACA",
        credentialing=_cred((_OSCAR, _CLARKE, _OSCAR_TIN, False, "Oscar FL")),
        crosswalk=_cw(),
        store=_Roster(["1699931030", "1760430029", "1790953933"]),
    )
    sig = next((s for s in (v.corroboration or []) if s.get("source") == "TIC"), None)
    assert sig is not None
    assert "not found" not in sig["detail"].lower()
    assert sig["result"] == "corroborates"  # credentialing OON + MRF physician gap agree


# ---- P3: no-network coverage (Original Medicare / Medigap) ----------------------------
#
# Roulhac, NPI 1801837109, Humana CO: Original Medicare + a Humana Medicare Supplement.
# There is no provider network, so Medicare participation IS the answer and a directory
# lookup can only mislead. See HANDOFF-2026-07-28.md §1 P3.

from network_probe.domain.enrollment import EnrollmentResult  # noqa: E402

_ROULHAC = "1801837109"


def _pecos(enrolled):
    def fn(npi):
        return EnrollmentResult(enrolled, "medicare-pecos", f"NPI {npi}: stub lookup")

    return fn


def test_medigap_medicare_participating_is_in_network():
    q = _q("humana-co-denver", _ROULHAC, "475181686", plan="Humana Medicare Supplement Plan G")
    v = resolve_provider_network(q, benefit_type="Medicare", credentialing=_cred(),
                                 crosswalk=_cw(), pecos_fn=_pecos(True))
    assert v is not None
    assert v.status == NetworkStatus.IN_NETWORK
    assert "no provider network" in v.notes.lower()


def test_medigap_not_medicare_enrolled_is_oon():
    q = _q("humana-co-denver", _ROULHAC, "475181686", plan="Humana Medicare Supplement Plan G")
    v = resolve_provider_network(q, benefit_type="Medicare", credentialing=_cred(),
                                 crosswalk=_cw(), pecos_fn=_pecos(False))
    assert v.status == NetworkStatus.OUT_OF_NETWORK


def test_medigap_undetermined_participation_is_unknown_not_a_directory_fallthrough():
    """Must NOT return None — falling through to a network directory is the false-OON path."""
    q = _q("humana-co-denver", _ROULHAC, "475181686", plan="Humana Medicare Supplement Plan G")
    v = resolve_provider_network(q, benefit_type="Medicare", credentialing=_cred(),
                                 crosswalk=_cw(), pecos_fn=_pecos(None))
    assert v is not None
    assert v.status == NetworkStatus.UNKNOWN


def test_medigap_resolves_even_without_a_billing_tin():
    """The no-network branch needs only an NPI — it must not sit behind the TIN gate."""
    q = _q("humana-co-denver", _ROULHAC, None, plan="Original Medicare")
    v = resolve_provider_network(q, benefit_type="Medicare", credentialing=_cred(),
                                 crosswalk=_cw(), pecos_fn=_pecos(True))
    assert v is not None
    assert v.status == NetworkStatus.IN_NETWORK


def test_medicare_advantage_still_uses_credentialing_not_the_no_network_branch():
    """Regression guard: MA has a network — this branch must not swallow it."""
    q = _q("unitedhealthcare-ga-atlanta", "1902811656", "921600050", plan="UHC Medicare Advantage PPO")
    v = resolve_provider_network(
        q, benefit_type="Medicare",
        credentialing=_cred(("unitedhealthcare-ga-atlanta", "1902811656", "921600050", True, "UHC MA GA")),
        crosswalk=_cw(), pecos_fn=_pecos(True),
    )
    assert v.status == NetworkStatus.IN_NETWORK
    assert (v.source_url or "").startswith("credentialing")


def test_physician_gap_names_the_other_tins_the_npi_bills_under():
    """"Contracted, but under a different tax ID" is not the same as "not contracted".

    Real case, Cigna national OAP 2026-07-01: Orem (NPI 1497741409) IS in-network — under TIN
    884413789 (Florida Veins LLC), not under the clinic's billing TIN 463812940. Claims sent
    under the clinic TIN would still price out-of-network, but the fix is a contracting one, so
    the audit note has to distinguish the two.
    """
    npi, clinic_tin, other_tin = "1497741409", "463812940", "884413789"

    class _Store:
        _rows = [
            SimpleNamespace(payer_key="cigna-fl", npi=n, tin=clinic_tin, in_network=True,
                            source="tic", network_name="Cigna national OAP")
            for n in ("1114353026", "1043330285")
        ] + [
            SimpleNamespace(payer_key="cigna-fl", npi=npi, tin=other_tin, in_network=True,
                            source="tic", network_name="Cigna national OAP")
        ]

        def facts_for(self, payer_key, npi=None, tin=None):
            return [
                f for f in self._rows
                if f.payer_key == payer_key
                and (not npi or f.npi == str(npi))
                and (not tin or f.tin == re.sub(r"\D", "", str(tin)))
            ]

    q = _q("cigna-fl", npi, clinic_tin, plan="Cigna Open Access Plus")
    v = resolve_provider_network(q, benefit_type="Commercial", credentialing=_cred(),
                                 crosswalk=_cw(), store=_Store())
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert other_tin in v.notes  # the note must name where they ARE contracted
    assert v.matched_provider.get("in_network_tins_for_npi") == [other_tin]
