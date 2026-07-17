"""resolve_provider_network — the plan-type-aware provider-INN resolver that sits at the top of
check_network. It fuses two sources by line of business:

  * credentialing matrix (clinic's own contract; all lines; covers IN and OON)
  * TiC MRF (live; COMMERCIAL only; can prove IN, never OON)

Commercial: fuse both (agree / conflict→REVIEW / whichever has data). Medicare/Medicaid/Dual:
credentialing only — TiC is federally exempt, so it's never consulted (honest N/A, not a blank).
"""

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
