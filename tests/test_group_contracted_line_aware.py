"""`group_contracted` must respect the line of business its evidence came from.

Caught on LIVE 271 data, contradicted by the staff answer key. Ins Test 2 rows 3 and 4 are AARP
Medicare Advantage members at clinic TIN 463812940. That TIN carries three `source="tic"`
in-network facts for `unitedhealthcare-fl-south-florida`, loaded from UHC's COMMERCIAL NHP MRF.

`group_contracted()` counted them, so `final_determination` saw OON + group_contracted=True and
returned **PHYSICIAN_OUT_OF_NETWORK**. The staff determination is **OON w/ Benefits**.

Transparency-in-Coverage is commercial-only — Medicare Advantage, Medicaid and Dual are federally
exempt (§2). A TiC fact therefore says nothing about an MA member, and must not drive the
physician-vs-payer split for one. Credentialing is the clinic's own contract and covers every line,
so it still counts everywhere.
"""

from types import SimpleNamespace

from network_probe.domain.credentialing import CredentialingMatrix, CredentialRecord
from network_probe.domain.provider_network import group_contracted
from network_probe.domain.tin_crosswalk import TinCrosswalk

EMPTY_CW = TinCrosswalk(records=[])
EMPTY_CRED = CredentialingMatrix(records=[])
PAYER, TIN = "unitedhealthcare-fl-south-florida", "463812940"


class _TicStore:
    """Three in-network facts at that TIN, all from a COMMERCIAL MRF."""

    _rows = [
        SimpleNamespace(payer_key=PAYER, npi=n, tin=TIN, in_network=True, source="tic")
        for n in ("1760430029", "1154646842", "1487996625")
    ]

    def group_contracted(self, payer_key, tin):
        return True if any(f.payer_key == payer_key for f in self._rows) else None

    def facts_for(self, payer_key, npi=None, tin=None):
        return [f for f in self._rows if f.payer_key == payer_key]


def test_tic_evidence_does_not_make_a_medicare_member_physician_oon():
    """THE live failure: TiC is MA-exempt, so it cannot split physician-OON from payer-OON here."""
    assert group_contracted(PAYER, TIN, credentialing=EMPTY_CRED, crosswalk=EMPTY_CW,
                            store=_TicStore(), lob="medicare") is None


def test_tic_evidence_still_counts_for_a_commercial_member():
    assert group_contracted(PAYER, TIN, credentialing=EMPTY_CRED, crosswalk=EMPTY_CW,
                            store=_TicStore(), lob="commercial") is True


def test_medicaid_and_dual_are_exempt_too():
    for lob in ("medicaid", "dual", "federal"):
        assert group_contracted(PAYER, TIN, credentialing=EMPTY_CRED, crosswalk=EMPTY_CW,
                                store=_TicStore(), lob=lob) is None


def test_credentialing_counts_on_every_line_because_it_is_the_contract():
    cred = CredentialingMatrix(records=[CredentialRecord(PAYER, "9999999999", TIN, True)])
    for lob in ("medicare", "medicaid", "dual", "commercial"):
        assert group_contracted(PAYER, TIN, credentialing=cred, crosswalk=EMPTY_CW,
                                store=None, lob=lob) is True


def test_no_line_given_keeps_the_previous_behaviour():
    """Callers that cannot determine a line are unchanged — this fix narrows, never widens."""
    assert group_contracted(PAYER, TIN, credentialing=EMPTY_CRED, crosswalk=EMPTY_CW,
                            store=_TicStore()) is True
