"""The evidence panel: gather what EACH source independently says, so the UI can show them
side-by-side and then the calculated determination. Sources answer different questions:
  - Stedi 271         -> coverage + plan-level OON tier (NOT provider-specific network)
  - Credentialing     -> provider network (clinic contract; all lines)
  - TiC MRF           -> provider network (commercial only; presence=IN, absence=unknown)
  - Payer directory   -> provider network (public, unreliable; run live, best-effort)
"""

from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.credentialing import CredentialingMatrix, CredentialRecord
from network_probe.domain.evidence import assemble_evidence
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.tin_crosswalk import TinCrosswalk


def _result(**kw):
    base = dict(
        coverage_active=True, plan_name="Ambetter ACA PPO", group=None, coverage_dates={},
        network_status=NetworkStatus.IN_NETWORK, benefits=[], pcp_required=None, prior_auth_required=None,
        referral_required=None, cob=None, network_verdict=None, corroboration=[], source_audit={},
        out_of_network_benefits=True,
    )
    base.update(kw)
    return EligibilityResult(**base)


def _by(sources, name):
    return next((s for s in sources if s["source"].lower().startswith(name)), None)


def test_evidence_has_all_four_sources():
    q = ProviderQuery(payer="p", plan_hint="Ambetter ACA", npi="1", tin="2")
    ev = assemble_evidence(q, _result(), benefit_type="ACA", credentialing=CredentialingMatrix(records=[]),
                           crosswalk=TinCrosswalk(records=[]), run_directory=False)
    names = {s["source"] for s in ev}
    assert any("stedi" in n.lower() for n in names)
    assert any("credential" in n.lower() for n in names)
    assert any("tic" in n.lower() for n in names)
    assert any("director" in n.lower() for n in names)


def test_stedi_source_reports_plan_tier_not_provider_network():
    q = ProviderQuery(payer="p", plan_hint="Ambetter ACA", npi="1", tin="2")
    ev = assemble_evidence(q, _result(coverage_active=True, out_of_network_benefits=True),
                           benefit_type="ACA", credentialing=CredentialingMatrix(records=[]),
                           crosswalk=TinCrosswalk(records=[]), run_directory=False)
    stedi = _by(ev, "stedi")
    assert stedi["answers"] == "coverage + plan tier"
    # a 271 cannot determine provider-specific network
    assert "UNKNOWN" in stedi["status"] or "plan" in stedi["detail"].lower()


def test_tic_reports_in_network_when_billing_tin_in_mrf():
    q = ProviderQuery(payer="ambetter", plan_hint="Ambetter ACA", npi="1710305735", tin="933510922")
    cw = TinCrosswalk(records=[{"payer": "ambetter", "npi": "1710305735", "tin": "933510922"}])
    ev = assemble_evidence(q, _result(), benefit_type="ACA", credentialing=CredentialingMatrix(records=[]),
                           crosswalk=cw, run_directory=False)
    tic = _by(ev, "tic")
    assert tic["status"] == "IN_NETWORK"


def test_tic_marks_na_for_exempt_medicare_line():
    q = ProviderQuery(payer="uhc-az", plan_hint="UHC DUAL COMPLETE", npi="1", tin="2")
    ev = assemble_evidence(q, _result(plan_name="UHC DUAL COMPLETE"), benefit_type="Dual Eligible (FIDE SNP)",
                           credentialing=CredentialingMatrix(records=[]), crosswalk=TinCrosswalk(records=[]),
                           run_directory=False)
    tic = _by(ev, "tic")
    assert tic["status"] == "N/A"
    assert "exempt" in tic["detail"].lower()


def test_credentialing_reports_contract_status():
    q = ProviderQuery(payer="uhc-az", plan_hint="UHC DUAL COMPLETE", npi="1245461292", tin="843447602")
    cred = CredentialingMatrix(records=[CredentialRecord("uhc-az", "1245461292", "843447602", False, plan="UHC Dual")])
    ev = assemble_evidence(q, _result(network_status=NetworkStatus.OUT_OF_NETWORK),
                           benefit_type="Dual Eligible (FIDE SNP)", credentialing=cred,
                           crosswalk=TinCrosswalk(records=[]), run_directory=False)
    c = _by(ev, "credential")
    assert c["status"] == "OUT_OF_NETWORK"


def test_credentialing_no_record_is_explicit():
    q = ProviderQuery(payer="p", plan_hint="X", npi="1", tin="2")
    ev = assemble_evidence(q, _result(), benefit_type="ACA", credentialing=CredentialingMatrix(records=[]),
                           crosswalk=TinCrosswalk(records=[]), run_directory=False)
    c = _by(ev, "credential")
    assert c["status"] == "NO_RECORD"
