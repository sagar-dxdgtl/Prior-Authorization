"""The OON-benefit tier must resolve for EVERY line of business, not just Medicare.

`resolve_plan_type` gated its whole body on `lob in ("medicare", "dual")` and returned N/A for
everything else. The gate is right for the *PBP store* — CMS publishes Part C benefit files, so a
commercial plan can never be in it — but it was also switching off the **plan-string token**
fallback, which is not Medicare-specific in the slightest: a PPO pays out-of-network because it is
a PPO, whoever sells it.

The effect: any commercial member whose 271 came back silent on the OON tier
(`out_of_network_benefits is None`) could never be shown "Out-of-Network (with benefits)" — the
determination fell to plain "Out-of-Network", understating what the member is actually owed.
Measured before the fix: Aetna Choice POS II PPO, Cigna Open Access Plus PPO and BCBS Blue Choice
PPO all resolved to `capability=None` while the identical Medicare PPO string resolved to `True`.

Unchanged on purpose:
  * a definite 271 always wins — a capability only ever fills a SILENT one;
  * D-SNP still defers (dual cost-sharing is Medicaid-wrapped and member-specific);
  * Medicaid still defers (managed-care OON rules are state-specific, not readable from a name);
  * HMO-POS / POS / unrecognised still defer — the POS door is too narrow to call structurally.
"""

import pytest

from network_probe.domain.plan_benefits import resolve_plan_type

# --- commercial: the line that was silently switched off ------------------------------------------


@pytest.mark.parametrize(
    "plan",
    [
        "Aetna Choice POS II PPO",
        "Cigna Open Access Plus PPO",
        "BCBS Blue Choice PPO",
        "UnitedHealthcare Choice Plus PPO",
    ],
)
def test_a_commercial_ppo_pays_out_of_network(plan):
    r = resolve_plan_type(plan, None, store=None)
    assert r.capability is True, f"{plan} is a PPO — it pays OON whoever sells it"
    assert r.source == "plan-string"


@pytest.mark.parametrize(
    "plan",
    ["Oscar Silver Simple PCP Saver EPO", "UHC Commercial NHP Access HMO"],
)
def test_a_commercial_hmo_or_epo_does_not(plan):
    assert resolve_plan_type(plan, None, store=None).capability is False


def test_a_marketplace_ppo_resolves_too():
    """ACA/marketplace is its own line and was equally excluded by the old gate."""
    assert resolve_plan_type("Ambetter Marketplace Balanced Care PPO", None, store=None).capability is True


# --- unchanged behaviour: the deliberate defers ---------------------------------------------------


def test_medicare_still_resolves():
    r = resolve_plan_type("LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO", None, store=None)
    assert r.capability is True


def test_dsnp_still_defers_to_the_271():
    """Dual cost-sharing is Medicaid-wrapped and member-specific — never structural."""
    r = resolve_plan_type("UnitedHealthcare Dual Complete FL-S001 (HMO-POS D-SNP)", None, store=None)
    assert r.capability is None


def test_medicaid_still_defers_to_the_271():
    """Managed-Medicaid OON rules are set per state, not readable from a plan name."""
    assert resolve_plan_type("Molina Healthcare Medicaid STAR+PLUS", "medicaid", store=None).capability is None


@pytest.mark.parametrize("plan", ["Aetna Choice POS II", "Some Plan With No Product Token"])
def test_pos_and_unknown_still_defer(plan):
    assert resolve_plan_type(plan, None, store=None).capability is None


def test_the_pbp_store_is_still_only_consulted_for_medicare():
    """CMS PBP carries Part C plans only; asking it about a commercial plan would be a false match."""
    asked: list[str] = []

    class SpyStore:
        def resolve(self, hint, contract=None):
            asked.append(hint)
            return None

    resolve_plan_type("Aetna Choice POS II PPO", None, store=SpyStore())
    assert asked == [], "the PBP store must not be consulted for a commercial plan"

    resolve_plan_type("AARP Medicare Advantage Choice from UHC FL-0026 (PPO)", None, store=SpyStore())
    assert asked, "...but it must still be consulted for Medicare"
