"""A current Medicare opt-out is decisive for **Medicare Advantage**, not just for Medigap.

`no_network_verdict` already treats an opt-out as OON, but that branch only runs for coverage with
no provider network. Most of Test 2 and Test 3 is Medicare Advantage, which does have a network and
goes down the credentialing/directory path instead — where an opted-out provider was invisible.

It should not be. A provider who has filed an opt-out affidavit treats Medicare patients under a
private contract, and an MA organisation may not pay them for anything but emergency or urgently
needed care. So they cannot be in an MA plan's network, whatever a credentialing row or a payer
directory says — and those are exactly the sources most likely to be stale about it, because the
affidavit is filed with CMS, not with the plan.

This belongs in `enrollment_negative`, next to the PECOS check, because it is the same kind of
claim: a decisive negative drawn from the programme rather than from the plan. The same safety rule
applies throughout — only a *successful* lookup finding a *current* affidavit is decisive. An
unreachable source, an expired affidavit, or a provider merely absent from the assignment file must
never produce an OON.
"""

import pytest

from network_probe.domain.enrollment import (
    ACCEPTS,
    MAY_EXCEED,
    OPTED_OUT,
    UNKNOWN_ASSIGNMENT,
    AssignmentResult,
    EnrollmentResult,
)
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.provider_network import enrollment_negative


def _q(state="AZ"):
    return ProviderQuery(
        payer="aetna-az-phoenix", plan_hint="Aetna Medicare Prime HMO", npi="1801837109",
        tin="843447602", state=state,
    )


def _pecos(enrolled):
    return lambda npi: EnrollmentResult(enrolled, "medicare-pecos", f"NPI {npi}: stub")


def _assign(status, optout_checked=True):
    return lambda npi: AssignmentResult(status, f"stub {status} for {npi}", optout_checked=optout_checked)


# ---- the new decisive negative ------------------------------------------------------------------

@pytest.mark.parametrize("lob", ["medicare", "dual"])
def test_an_opted_out_provider_cannot_be_in_a_medicare_advantage_network(lob):
    v = enrollment_negative(_q(), lob, pecos_fn=_pecos(True), assignment_fn=_assign(OPTED_OUT))
    assert v is not None
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert v.confidence == "high"
    assert "opt" in v.notes.lower()


def test_the_opt_out_reason_is_given_not_just_the_status():
    """Staff have to act on this. 'Not enrolled' and 'has a private contract' need different calls."""
    v = enrollment_negative(_q(), "medicare", pecos_fn=_pecos(True), assignment_fn=_assign(OPTED_OUT))
    assert "private contract" in v.notes.lower()
    assert v.matched_provider.get("opted_out") is True


def test_opt_out_fires_even_when_the_pecos_lookup_could_not_be_determined():
    """The affidavit stands on its own — it does not need enrolment confirmed first."""
    v = enrollment_negative(_q(), "medicare", pecos_fn=_pecos(None), assignment_fn=_assign(OPTED_OUT))
    assert v is not None and v.status == NetworkStatus.OUT_OF_NETWORK


# ---- everything else must still defer -------------------------------------------------------------

@pytest.mark.parametrize("status", [ACCEPTS, MAY_EXCEED, UNKNOWN_ASSIGNMENT])
def test_any_non_optout_assignment_still_defers_to_the_plan_network(status):
    """Accepting assignment says nothing about THIS plan's network — it only clears the gate."""
    v = enrollment_negative(_q(), "medicare", pecos_fn=_pecos(True), assignment_fn=_assign(status))
    assert v is None


def test_a_failed_assignment_lookup_never_produces_an_oon():
    def boom(npi):
        raise RuntimeError("CMS down")

    assert enrollment_negative(_q(), "medicare", pecos_fn=_pecos(True), assignment_fn=boom) is None


def test_a_commercial_line_never_consults_the_opt_out_file():
    """Opt-out is a Medicare fact. A commercial member's network is not affected by it."""
    calls = []
    v = enrollment_negative(
        _q(), "commercial", pecos_fn=_pecos(True),
        assignment_fn=lambda npi: calls.append(npi) or _assign(OPTED_OUT)(npi),
    )
    assert v is None
    assert calls == []


def test_a_medicaid_line_never_consults_the_opt_out_file():
    calls = []
    v = enrollment_negative(
        _q(), "medicaid",
        medicaid_fn=lambda npi, st: EnrollmentResult(True, f"medicaid-{st}", "enrolled"),
        assignment_fn=lambda npi: calls.append(npi) or _assign(OPTED_OUT)(npi),
    )
    assert v is None
    assert calls == []


# ---- ordering and cost ----------------------------------------------------------------------------

def test_a_confirmed_not_enrolled_still_wins_and_skips_the_extra_lookup():
    """Already decisive — no reason to spend a second request to reach the same verdict."""
    calls = []
    v = enrollment_negative(
        _q(), "medicare", pecos_fn=_pecos(False),
        assignment_fn=lambda npi: calls.append(npi) or _assign(UNKNOWN_ASSIGNMENT)(npi),
    )
    assert v.status == NetworkStatus.OUT_OF_NETWORK
    assert "not enrolled" in v.notes.lower()
    assert calls == []


def test_the_trail_names_the_opt_out_file_as_the_source():
    v = enrollment_negative(_q(), "medicare", pecos_fn=_pecos(True), assignment_fn=_assign(OPTED_OUT))
    sources = [c["source"] for c in (v.corroboration or [])]
    assert "Medicare assignment" in sources


def test_an_injected_assignment_fn_alone_is_enough_to_run():
    """Callers must be able to exercise this without also stubbing PECOS."""
    v = enrollment_negative(_q(), "medicare", assignment_fn=_assign(OPTED_OUT))
    assert v is not None and v.status == NetworkStatus.OUT_OF_NETWORK


# ---- reaching the check at all --------------------------------------------------------------------
#
# `enrollment_negative` sits behind a `cred is None` guard on the Medicare/Medicaid path, so for any
# provider the clinic HAS credentialed the opt-out file was never consulted — i.e. it was skipped for
# precisely the rows it matters most on. A credentialing matrix is the clinic's own admin record and
# is the source most likely to be stale here, because an opt-out affidavit is filed with CMS and
# never with the plan.
#
# It does not follow that CMS should silently overturn contract evidence. That is the established
# precedence rule in this codebase (a disagreement with credentialing goes to REVIEW, never a silent
# flip), and it is the safer error here too: telling staff "credentialing says in-network but CMS
# says this provider opted out" is actionable, while flipping to OON on a monthly file that could be
# weeks stale is over-claiming in the other direction.

from network_probe.domain.credentialing import CredentialingMatrix, CredentialRecord  # noqa: E402
from network_probe.domain.provider_network import resolve_provider_network  # noqa: E402

_NPI, _TIN, _PAYER = "1801837109", "843447602", "aetna-az-phoenix"


def _cred(in_network):
    return CredentialingMatrix(
        records=[CredentialRecord(_PAYER, _NPI, _TIN, in_network, "Aetna Medicare AZ")]
    )


def _resolve(cred, assignment):
    q = ProviderQuery(payer=_PAYER, plan_hint="Aetna Medicare Prime HMO", npi=_NPI, tin=_TIN, state="AZ")
    return resolve_provider_network(
        q, benefit_type="Medicare Advantage", credentialing=cred,
        pecos_fn=_pecos(True), assignment_fn=_assign(assignment),
    )


def test_credentialing_saying_in_network_against_a_cms_opt_out_goes_to_review():
    v = _resolve(_cred(True), OPTED_OUT)
    assert v.status == NetworkStatus.REVIEW
    assert v.confidence == "conflict"
    assert "opt" in v.notes.lower()


def test_credentialing_saying_out_of_network_and_the_opt_out_simply_agree():
    v = _resolve(_cred(False), OPTED_OUT)
    assert v.status == NetworkStatus.OUT_OF_NETWORK


def test_a_credentialed_provider_who_has_not_opted_out_is_unaffected():
    """Regression guard: this is the ordinary MA path and it must not change."""
    v = _resolve(_cred(True), ACCEPTS)
    assert v.status == NetworkStatus.IN_NETWORK
    assert v.confidence == "high"


def test_with_no_credentialing_row_an_opt_out_is_still_a_plain_oon():
    v = _resolve(CredentialingMatrix(records=[]), OPTED_OUT)
    assert v.status == NetworkStatus.OUT_OF_NETWORK
