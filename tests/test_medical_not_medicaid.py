"""'Medical' is not 'Medi-Cal'.

The Medicaid marker was `medi-?cal`, which case-insensitively is the SAME STRING as the ordinary
English word "Medical" — so any plan name containing it classified as Medicaid. And because
_noncommercial_lob tests Medicaid BEFORE Medicare, "Aetna Medicare Prime Extra Medical" came back
`medicaid`, not `medicare`.

That misroutes on both axes it feeds: Medicaid is TiC-exempt (so Transparency-in-Coverage is
silently skipped), and it selects the Managed Medicaid row when a payer key carries several
benefit_types. Found on AZ Blue's own portal label "Medicare Supplement Senior Preferred Medical".
"""

import pytest

from network_probe.domain.line_of_business import is_commercial, line_of_business


@pytest.mark.parametrize("plan,expected", [
    # real Medi-Cal (California Medicaid) — must still be caught
    ("Anthem Blue Cross Medi-Cal", "medicaid"),
    ("Health Net MediCal managed care", "medicaid"),
    ("Molina Medi-Cal", "medicaid"),
    # the ordinary English word — must NOT be Medicaid
    ("Aetna Medicare Prime Extra Medical", "medicare"),
    ("Medicare Supplement Senior Preferred Medical", "medicare"),
    ("UHC Medical Plan PPO", "commercial"),
    ("BCBS Medical Group HMO", "commercial"),
])
def test_medical_is_not_medi_cal(plan, expected):
    assert line_of_business(plan, None) == expected


def test_a_commercial_plan_named_medical_stays_tic_eligible():
    """The consequence that matters: Medicaid is TiC-exempt, so a misclassified commercial plan
    silently loses its Transparency-in-Coverage evidence."""
    assert is_commercial("UHC Medical Plan PPO", None) is True


def test_genuine_medicaid_is_still_exempt():
    assert is_commercial("Anthem Blue Cross Medi-Cal", None) is False
