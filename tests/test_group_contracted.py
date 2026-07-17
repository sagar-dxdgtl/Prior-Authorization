"""group_contracted(payer, tin): is the clinic's billing TIN contracted with this payer under ANY
NPI? Positive evidence (an in-network credentialing row, a TiC MRF hit, or a persisted fact) → True.
No positive evidence → None (absence is NOT proof the group is out — we may only hold OON physicians).
This is the signal that splits Physician OON (group contracted) from payer-level OON."""

from network_probe.domain.credentialing import CredentialingMatrix, CredentialRecord
from network_probe.domain.provider_network import group_contracted
from network_probe.domain.tin_crosswalk import TinCrosswalk

_EMPTY_CRED = CredentialingMatrix(records=[])
_EMPTY_CW = TinCrosswalk(records=[])


def test_credentialing_in_network_under_tin_is_contracted():
    # a DIFFERENT physician at the same TIN is in-network with the payer -> the group is contracted
    cred = CredentialingMatrix(records=[CredentialRecord("p", "1111111111", "900000000", True)])
    assert group_contracted("p", "900000000", credentialing=cred, crosswalk=_EMPTY_CW, store=None) is True


def test_only_oon_record_is_unknown_not_false():
    cred = CredentialingMatrix(records=[CredentialRecord("p", "1111111111", "900000000", False)])
    assert group_contracted("p", "900000000", credentialing=cred, crosswalk=_EMPTY_CW, store=None) is None


def test_crosswalk_tin_present_is_contracted():
    # TiC has the billing TIN under some NPI for this payer -> group contracted (the Oscar/Munar case)
    cw = TinCrosswalk(records=[{"payer": "oscar-fl", "npi": "2222222222", "tin": "463812940"}])
    assert group_contracted("oscar-fl", "463812940", credentialing=_EMPTY_CRED, crosswalk=cw, store=None) is True


def test_tin_normalization_in_crosswalk():
    cw = TinCrosswalk(records=[{"payer": "oscar-fl", "npi": "2222222222", "tin": "46-3812940"}])
    assert group_contracted("oscar-fl", "463812940", credentialing=_EMPTY_CRED, crosswalk=cw, store=None) is True


def test_no_evidence_is_none():
    assert group_contracted("p", "900000000", credentialing=_EMPTY_CRED, crosswalk=_EMPTY_CW, store=None) is None


def test_store_positive_is_contracted():
    class _Store:
        def group_contracted(self, payer, tin):
            return True

    assert group_contracted("p", "900000000", credentialing=_EMPTY_CRED, crosswalk=_EMPTY_CW, store=_Store()) is True
