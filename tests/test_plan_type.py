"""Plan-TYPE classifier (HMO / HMO-POS / PPO / PFFS / …) and the OON-benefit capability it implies.

Distinct from line_of_business (commercial vs Medicare vs …). Plan type answers a *different*
question: given the member is OON, does the plan structurally pay out-of-network benefits?

Grounded in real CY2026 CMS PBP plan-name strings. The hard safety rule: we only assert a
capability we can defend federally (PPO/PFFS carry OON + combined MOOP; pure HMO/EPO cover OON
for emergencies only). HMO-POS, POS, and every D-SNP are *ambiguous* → None (defer to the 271),
because the POS door is narrow and dual cost-sharing is Medicaid-wrapped / member-specific.
"""

from network_probe.domain.line_of_business import plan_oon_capability, plan_type

# ---- plan_type: structural token from the plan string ----

def test_ppo_from_aarp_string():
    assert plan_type("AARP Medicare Advantage from UHC WI-0002 (PPO)") == "ppo"


def test_hmopos_wins_over_bare_hmo_and_pos():
    # "HMO-POS" contains both "hmo" and "pos" — the compound must win, or Birenbaum mis-tags.
    assert plan_type("UHC Dual Complete AZ-S001 (HMO-POS D-SNP)") == "hmopos"
    assert plan_type("Humana Gold Plus H0028-016 (HMO-POS)") == "hmopos"
    assert plan_type("Some Plan HMOPOS") == "hmopos"


def test_plain_hmo():
    assert plan_type("Humana Gold Plus H0028-014 (HMO)") == "hmo"


def test_pffs():
    assert plan_type("Some Carrier Medicare PFFS Plan") == "pffs"


def test_epo_and_pos():
    assert plan_type("Cigna Connect EPO") == "epo"
    assert plan_type("Aetna Open Access POS") == "pos"


def test_ambiguous_multi_type_label_is_unknown():
    # "HMO/PPO" names BOTH a closed and an open product — refuse to guess a tier (real Aetna string)
    assert plan_type("Aetna HMO/PPO Medicare Advantage") == "unknown"
    assert plan_type("Some Plan HMO or PPO") == "unknown"


def test_no_structural_token_is_unknown():
    # the real demo strings carry NO explicit type token
    assert plan_type("UHC Medicare Dual Complete AZMCARE") == "unknown"
    assert plan_type("UHC AARP Medicare Advantage") == "unknown"
    assert plan_type("") == "unknown"
    assert plan_type(None) == "unknown"


# ---- plan_oon_capability: what that type implies for OON benefits ----

def test_ppo_and_pffs_have_oon_benefits():
    assert plan_oon_capability("ppo") is True
    assert plan_oon_capability("pffs") is True


def test_pure_hmo_and_epo_have_no_oon_benefits():
    assert plan_oon_capability("hmo") is False
    assert plan_oon_capability("epo") is False


def test_hmopos_pos_unknown_defer():
    # ambiguous → None (defer to the live 271), never a guessed tier
    assert plan_oon_capability("hmopos") is None
    assert plan_oon_capability("pos") is None
    assert plan_oon_capability("unknown") is None


def test_dsnp_always_defers_even_for_ppo():
    # dual cost-sharing is Medicaid-wrapped / member-specific — never assert a tier from structure.
    assert plan_oon_capability("ppo", dsnp=True) is None
    assert plan_oon_capability("hmo", dsnp=True) is None
    assert plan_oon_capability("hmopos", dsnp=True) is None
