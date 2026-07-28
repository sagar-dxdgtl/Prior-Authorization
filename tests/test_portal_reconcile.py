"""Folding a live portal capture back into the provider-network verdict.

The payer's own find-a-doctor portal is the member-facing directory, so a decisive portal answer
outranks a public directory read — `test2-portal-verification` recorded flex.optum putting Naar
IN while the findcare portal said OON, and the portal was right.

But it does NOT outrank contract-level evidence. Credentialing is the clinic's own contract and a
TiC hit on NPI+TIN is the payer's own filed roster; when either disagrees with the portal that is a
genuine conflict for a human, never a silent flip. This module is that precedence table.
"""

from network_probe.domain.models import NetworkStatus
from network_probe.domain.portal_reconcile import is_contract_backed, reconcile_portal
from network_probe.portal.models import PortalStatus

IN, OON, UNK, REV = (
    NetworkStatus.IN_NETWORK,
    NetworkStatus.OUT_OF_NETWORK,
    NetworkStatus.UNKNOWN,
    NetworkStatus.REVIEW,
)


# ---- the portal cannot speak ------------------------------------------------------------

def test_unknown_portal_changes_nothing():
    status, sig = reconcile_portal(IN, PortalStatus.UNKNOWN, source_url="fhir")
    assert status == IN
    assert sig["result"] == "inconclusive"


def test_blocked_portal_changes_nothing():
    """A refused portal is not evidence of anything — least of all out-of-network."""
    status, sig = reconcile_portal(IN, PortalStatus.BLOCKED, source_url="fhir")
    assert status == IN
    assert sig["result"] == "inconclusive"


# ---- the portal fills a silence --------------------------------------------------------

def test_decisive_portal_settles_an_unknown_verdict():
    status, sig = reconcile_portal(UNK, PortalStatus.OUT_OF_NETWORK, source_url="fhir")
    assert status == OON
    assert sig["result"] == "settles"


# ---- the portal vs a mere directory ----------------------------------------------------

def test_portal_overrides_a_disagreeing_directory():
    """The exact Naar case: the public directory over-includes, the member portal does not."""
    status, sig = reconcile_portal(
        IN, PortalStatus.OUT_OF_NETWORK, source_url="https://flex.optum.com/fhirpublic/R4"
    )
    assert status == OON
    assert sig["result"] == "overrides"
    assert "directory" in sig["detail"].lower()


def test_agreement_is_corroboration_not_a_change():
    status, sig = reconcile_portal(OON, PortalStatus.OUT_OF_NETWORK, source_url="fhir")
    assert status == OON
    assert sig["result"] == "corroborates"


# ---- the portal vs contract-level evidence ---------------------------------------------

def test_portal_disagreeing_with_credentialing_is_review_not_a_flip():
    status, sig = reconcile_portal(IN, PortalStatus.OUT_OF_NETWORK, source_url="credentialing-matrix")
    assert status == REV
    assert sig["result"] == "contradicts"


def test_portal_disagreeing_with_a_tic_hit_is_review_not_a_flip():
    """A TiC hit on NPI+TIN is the payer's own filed roster — a portal miss does not erase it."""
    status, sig = reconcile_portal(IN, PortalStatus.OUT_OF_NETWORK, source_url="tic-mrf")
    assert status == REV
    assert sig["result"] == "contradicts"


def test_portal_agreeing_with_contract_evidence_still_corroborates():
    status, sig = reconcile_portal(OON, PortalStatus.OUT_OF_NETWORK, source_url="credentialing-matrix")
    assert status == OON
    assert sig["result"] == "corroborates"


def test_review_is_never_downgraded_by_the_portal():
    """An existing source conflict stays a conflict; one more opinion does not resolve it."""
    status, _ = reconcile_portal(REV, PortalStatus.IN_NETWORK, source_url="credentialing-matrix+tic-mrf")
    assert status == REV


# ---- what counts as contract-level -----------------------------------------------------

def test_contract_sources_are_credentialing_and_tic_only():
    assert is_contract_backed("credentialing-matrix") is True
    assert is_contract_backed("tic-mrf") is True
    assert is_contract_backed("credentialing-matrix+tic-mrf") is True
    assert is_contract_backed("https://flex.optum.com/fhirpublic/R4") is False
    assert is_contract_backed("enrollment") is False
    assert is_contract_backed(None) is False


def test_signal_names_the_portal_so_a_reader_sees_which_source_moved_it():
    _, sig = reconcile_portal(UNK, PortalStatus.IN_NETWORK, source_url="fhir",
                              portal_name="UHC Find Care (guest)")
    assert sig["source"] == "Payer portal"
    assert "UHC Find Care" in sig["detail"]
