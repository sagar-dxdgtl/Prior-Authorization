"""The Surest deeplink walk — two defects that killed sheet row 8 (Erika Cano Fernandez, UHC Surest,
Kevin Fradkin NPI 1972603934, West University Houston TX 77005).

The capture stopped at:

    overlays dismissed → coverage: set by the Surest deeplink (lob=EI)
      → type of care 'Medical' NOT clickable

**1. THE DEEPLINK ALREADY ANSWERS "TYPE OF CARE".** Its payload carries `"coverageType":"M"` next to
`"lob":"EI"`, and the portal proves it in the URL it redirects to:

    /guest-plan-selection/plan-selection?planSelectionLob=EI&coverageType=M&chipValue=All

Measured live 2026-08-12 on the hydrated page, there is no care control to click at all:

    [data-testid*='medical']  count=0        [data-testid*='location'] count=3
    [data-testid*='care']     count=0        [data-testid*='plan']     count=1

The driver already skips the coverage-type CARD for exactly this reason, one line earlier — and then
hard-fails on the care step the same deeplink also answered.

**2. THE WALK STARTS BEFORE THE PORTAL EXISTS.** The entry settles 3s and each step then allows 8s.
Measured across five loads of this very URL on 2026-08-12, time-to-hydrate was 56s, 64s, >120s,
>150s, and one outright goto timeout (Optum's own flex.optum returned 504 and 500 in the same
window). A driver that clicks into an unhydrated SPA reports every control as missing, which reads
like a portal redesign rather than a slow page — and turns a transport problem into a network claim.
"""

from __future__ import annotations

import base64
import json

import pytest

from network_probe.portal.drivers.uhc_findcare import (
    SUREST_NETWORKS,
    UhcFindCareDriver,
    surest_deeplink,
    surest_network_from_plan,
)
from network_probe.portal.models import PortalQuery

SUREST = PortalQuery(
    payer_key="uhc-surest-tx-houston", npi="1972603934",
    provider_first_name="Kevin", provider_last_name="Fradkin",
    plan="Surest Choice Plus", state="TX", city="Houston", zip_code="77005",
)
NON_SUREST = PortalQuery(
    payer_key="unitedhealthcare-tx-houston", npi="1972603934",
    provider_last_name="Fradkin", plan="AARP Medicare Advantage (PPO)", zip_code="77005",
)


class TestTheDeeplinkPinsCareTypeToo:
    def test_the_payload_carries_coverage_type_medical(self):
        """This is why the care step is absent — it is answered before the page loads."""
        blob = surest_deeplink("choice plus").split("deeplink=")[1]
        payload = json.loads(base64.b64decode(blob))
        assert payload["lob"] == "EI"
        assert payload["coverageType"] == "M", "Medical is pinned by the deeplink, not by a click"
        assert payload["reciprocityId"] == SUREST_NETWORKS["choice plus"]


class _Walk(UhcFindCareDriver):
    """The walk with its browser seams scripted. `medical_clickable=False` reproduces the live page."""

    def __init__(self, *, medical_clickable: bool = False):
        super().__init__()
        self.medical_clickable = medical_clickable
        self.clicked: list[str] = []

    def _dismiss_overlays(self, page):
        return None

    def _click_any(self, page, sel, label, timeout_ms=8_000):
        self.clicked.append(label)
        if "medical" in sel:
            return self.medical_clickable
        return True

    def _commit_location(self, page, zip_code):
        return True

    def _county_modal(self, page):
        return False

    def _plan_options(self, page):
        return [], None

    def _plan_list(self, page):
        return (), None

    def _pick_plan(self, page, plan, options=None):
        return None

    def _await_shell(self, page, **kw):
        return True


class TestTheSurestWalkSurvivesAnAbsentCareStep:
    def test_an_absent_medical_control_no_longer_kills_a_surest_walk(self):
        d = _Walk(medical_clickable=False)
        pin, trail = d._walk_to_plan(object(), SUREST)
        assert "NOT clickable" not in " ".join(trail), f"walk died on a skipped step: {trail}"
        assert pin.name == "Choice Plus", f"the deeplink pin must still be reached: {trail}"
        assert pin.confirms, "reciprocityId is an identifier pin"

    def test_the_trail_says_the_deeplink_answered_it_rather_than_claiming_a_click(self):
        d = _Walk(medical_clickable=False)
        _, trail = d._walk_to_plan(object(), SUREST)
        joined = " ".join(trail)
        assert "care" in joined.lower()
        assert "deeplink" in joined.lower()

    def test_a_care_control_that_IS_present_is_still_used(self):
        """Surest may restore the step; clicking it when it exists keeps the walk faithful."""
        d = _Walk(medical_clickable=True)
        _, trail = d._walk_to_plan(object(), SUREST)
        assert "Medical" in d.clicked
        assert any("care: Medical" in s for s in trail)

    def test_a_NON_surest_walk_still_fails_hard_when_medical_is_missing(self):
        """The tolerance is scoped to the deeplink. On the ordinary guest flow the care step is real,
        and a missing control there means the walk genuinely cannot continue — it must not sail past
        it and answer about whatever network the portal happened to be holding."""
        d = _Walk(medical_clickable=False)
        pin, trail = d._walk_to_plan(object(), NON_SUREST)
        assert pin.name is None
        assert any("NOT clickable" in s for s in trail)


class TestTheWalkWaitsForTheShell:
    def test_the_walk_refuses_to_click_into_an_unhydrated_page(self):
        """A page that never rendered must be reported as such, not as a portal whose controls all
        vanished. 56s/64s/>150s were all measured on this URL in one session."""
        class _NeverHydrates(_Walk):
            def _await_shell(self, page, **kw):
                return False

        d = _NeverHydrates(medical_clickable=False)
        pin, trail = d._walk_to_plan(object(), SUREST)
        assert pin.name is None
        joined = " ".join(trail).lower()
        assert "hydrat" in joined or "never rendered" in joined, joined
        assert "NOT clickable" not in " ".join(trail), (
            "an unrendered page must not be reported as a missing control"
        )

    def test_a_hydrated_page_walks_normally(self):
        d = _Walk(medical_clickable=True)
        pin, _ = d._walk_to_plan(object(), SUREST)
        assert pin.name == "Choice Plus"


@pytest.mark.parametrize("plan,expected", [
    ("Surest Choice Plus", "choice plus"),
    ("SUREST SELECT PLUS POS", "select plus pos"),
    ("Options PPO", "options ppo"),
    ("Surest", None),
    (None, None),
])
def test_network_is_read_from_the_plan_and_never_guessed(plan, expected):
    assert surest_network_from_plan(plan) == expected
