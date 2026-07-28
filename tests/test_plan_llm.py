"""Tier 3 plan disambiguation — the LLM fallback under `plan_match`.

Tiers 1 and 2 are deterministic: an identifier match (contract / HIOS / market code), then
distinctive-token overlap. When both decline, the plan string still has to be mapped onto one of
the payer's own network labels or the driver can only return UNKNOWN — which is why all six
unsettled Ins Test 3 rows are stuck.

Two properties are load-bearing and are what these tests exist to pin:

  * **An LLM pick can never license an OUT_OF_NETWORK.** It returns confidence "medium" at best,
    so `PlanMatch.confirms_network` stays False. It pins a plan for the *search*; absence in that
    plan still reads as UNKNOWN. This is the CareFlex rule, unchanged.
  * **No member data reaches the API.** HANDOFF §7: tier 3 is scrubbed to plan descriptors.
"""

from __future__ import annotations

import pytest

from network_probe.portal.plan_llm import disambiguate_plan, scrub_descriptor

AZ_NETWORKS = [
    "Statewide / National PPO",
    "Alliance PPO / EPO",
    "Alliance PPO + Prosano",
    "Statewide HMO",
    "Blue Best Life - Classic/Plus",
]


class _FakeClient:
    """Stands in for anthropic.Anthropic — records what was sent, returns a canned parse."""

    def __init__(self, index=None, reason="stub", stop_reason="end_turn", raises=None):
        self._index, self._reason = index, reason
        self._stop_reason, self._raises = stop_reason, raises
        self.sent: dict = {}
        self.messages = self  # client.messages.parse(...) -> our parse

    def parse(self, **kwargs):
        if self._raises is not None:
            raise self._raises
        self.sent = kwargs
        parsed = type("P", (), {"index": self._index, "reason": self._reason})()
        return type("R", (), {"parsed_output": parsed, "stop_reason": self._stop_reason})()


# ---- the safety property: an LLM pick never licenses an OON ------------------------------

def test_llm_match_is_never_confirms_network():
    c = _FakeClient(index=0, reason="'Statewide PPO' names the statewide PPO network")
    m = disambiguate_plan("BCBSAZ Statewide PPO plan", AZ_NETWORKS, client=c)
    assert m is not None
    assert m.index == 0
    assert m.label == "Statewide / National PPO"
    assert m.confidence == "medium"
    assert m.confirms_network is False  # THE rule: a name pick cannot license an OON


def test_basis_records_that_a_model_chose_it():
    c = _FakeClient(index=3, reason="the plan says HMO, not PPO")
    m = disambiguate_plan("BCBSAZ Statewide HMO", AZ_NETWORKS, client=c)
    assert "tier 3" in m.basis.lower() or "model" in m.basis.lower()
    assert "the plan says HMO, not PPO" in m.basis


# ---- refusing is a success ---------------------------------------------------------------

def test_model_declining_to_choose_is_none_not_a_guess():
    c = _FakeClient(index=None, reason="the plan names no network")
    assert disambiguate_plan("BCBS Arizona", AZ_NETWORKS, client=c) is None


@pytest.mark.parametrize("bad", [-1, 99])
def test_out_of_range_index_is_none(bad):
    """A hallucinated index must not become an IndexError or the wrong network."""
    assert disambiguate_plan("anything", AZ_NETWORKS, client=_FakeClient(index=bad)) is None


def test_a_refusal_stop_reason_is_none():
    c = _FakeClient(index=0, stop_reason="refusal")
    assert disambiguate_plan("anything", AZ_NETWORKS, client=c) is None


def test_api_failure_degrades_to_none_never_raises():
    c = _FakeClient(raises=RuntimeError("connection reset"))
    assert disambiguate_plan("anything", AZ_NETWORKS, client=c) is None


def test_too_few_options_never_calls_the_api():
    c = _FakeClient(index=0)
    assert disambiguate_plan("anything", ["only one"], client=c) is None
    assert c.sent == {}, "must not spend a call when there is nothing to choose between"


# ---- PHI: only plan descriptors leave the process ----------------------------------------

def test_member_identifiers_are_scrubbed_before_sending():
    """HANDOFF §7: this client's 'Ins Group Number' column holds member identifiers — an
    alpha-prefixed SRG######## and a 12-digit numeric. Neither may reach the API."""
    assert "SRG10057830" not in scrub_descriptor("Blue Shield CA PPO SRG10057830")
    assert "123456789012" not in scrub_descriptor("Humana Gold 123456789012")
    # a contract number is NOT a member id and must survive — it is the whole signal
    assert "H1036" in scrub_descriptor("Humana Gold Plus HMO H1036")
    assert "H2406018000" in scrub_descriptor("AARP Medicare Advantage H2406018000")


def test_nothing_member_shaped_reaches_the_client():
    c = _FakeClient(index=0)
    disambiguate_plan("Blue Shield CA PPO SRG10057830", AZ_NETWORKS, client=c)
    sent = repr(c.sent)
    assert "SRG10057830" not in sent


def test_the_request_uses_the_pinned_model_and_low_effort():
    c = _FakeClient(index=0)
    disambiguate_plan("BCBSAZ Statewide PPO", AZ_NETWORKS, client=c)
    assert c.sent["model"] == "claude-opus-5"
    assert c.sent["output_config"]["effort"] == "low"
    # sampling params were removed on this model family and 400 if sent
    assert "temperature" not in c.sent and "top_p" not in c.sent and "top_k" not in c.sent


# ---- the wrapper: tiers 1-2 first, tier 3 only when they decline -------------------------

from network_probe.portal.plan_match import match_plan_with_fallback  # noqa: E402

HUMANA = ["Humana Gold Plus HMO H1036", "HumanaChoice Florida PPO H5216", "Humana Honor PPO"]


def test_an_identifier_match_never_reaches_the_model():
    """Tier 1 is decisive and free — tier 3 must not be consulted, nor billed."""
    c = _FakeClient(index=2)
    m = match_plan_with_fallback("Humana plan H5216-062", HUMANA, client=c, enabled=True)
    assert m.index == 1
    assert m.confidence == "high"          # identifier tier
    assert m.confirms_network is True      # and it CAN license an OON
    assert c.sent == {}, "tier 3 was called even though an identifier matched"


def test_tier_3_runs_only_when_the_deterministic_tiers_decline():
    # "Humana medical plan" ties across all three labels, so tiers 1-2 both decline — this is
    # exactly the shape that leaves the six Ins Test 3 rows unsettled.
    c = _FakeClient(index=0, reason="the member is on the Gold Plus HMO")
    m = match_plan_with_fallback("Humana medical plan", HUMANA, client=c, enabled=True)
    assert m is not None
    assert m.index == 0
    assert m.confirms_network is False     # still cannot license an OON
    assert c.sent != {}


def test_disabled_is_exactly_the_old_behaviour():
    c = _FakeClient(index=0)
    assert match_plan_with_fallback("Humana medical plan", HUMANA,
                                    client=c, enabled=False) is None
    assert c.sent == {}
