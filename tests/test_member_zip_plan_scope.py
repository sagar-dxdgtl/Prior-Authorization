"""The member's residence ZIP comes from the 271, and pins the plan list the CLINIC ZIP cannot.

Found by driving UHC Find Care by hand on 2026-08-03. Its plan step is literally "Select the area
where you live", and the plan list is scoped to the MEMBER'S county — not the clinic's. Measured
against the live portal:

    Port St. Lucie 34986 (the clinic)  -> 12 Medicare plans: FL-35, FL-0006, FL-001P, FL-0025,
                                          FL-0031, FL-MA01, FL-MA2, five Dual Complete
    Miami 33101                        -> a different 17 plans
    the member's own FL-0026           -> in NEITHER, and the portal's own plan-name search
                                          returns nothing for it, because that box filters
                                          within the county

So a patient who travels to a specialist outside their home county had an unpinnable plan, and the
walk could never confirm a network — no matter how correct the 271 was. That is not a matcher
problem and no amount of Stedi accuracy fixes it.

The ZIP is read from the 271's own `subscriber.address.postalCode` (present in 7 of 7 cached real
271s, across UnitedHealthcare, Humana, Devoted, Oscar and Cigna), so it costs no data entry and
cannot drift from what the payer believes. Several payers return ZIP+4, which the portals' location
boxes reject, so it is truncated to five digits.

The boundary this must not cross: `member_zip` scopes a PLAN LIST. It is never typed into a provider
search box, and no other member field (name, id, DOB) goes anywhere near a portal — HANDOFF §7.
"""

from network_probe.portal.models import PortalQuery
from network_probe.stedi.parse_271 import _subscriber_zip, parse_271_benefits


def _271(postal: str | None) -> dict:
    sub: dict = {"lastName": "STEPHEN"}
    if postal is not None:
        sub["address"] = {"address1": "1 MAIN ST", "city": "PORT ST LUCIE", "state": "FL",
                          "postalCode": postal}
    return {
        "subscriber": sub,
        "planInformation": {"planName": "LPPO-AARP MEDICARE ADVANTAGE FROM UHC FL-0026 (PPO"},
        "benefitsInformation": [],
    }


# --- reading it out of the 271 ------------------------------------------------------------------


def test_a_plain_five_digit_zip_is_read():
    assert _subscriber_zip(_271("34986")) == "34986"


def test_zip_plus_four_is_truncated_to_five():
    """Humana and Cigna both return the 9-digit form; the portal location boxes reject it."""
    assert _subscriber_zip(_271("338111635")) == "33811"


def test_a_hyphenated_zip_plus_four_is_truncated_too():
    assert _subscriber_zip(_271("33811-1635")) == "33811"


def test_a_missing_address_is_not_an_error():
    assert _subscriber_zip(_271(None)) is None
    assert _subscriber_zip({}) is None


def test_a_short_or_junk_postal_code_is_declined():
    """Better no ZIP than a wrong one: an unpinnable plan is honest, a wrong county is a wrong
    network, and this whole layer exists to stop wrong-network answers."""
    assert _subscriber_zip(_271("FL")) is None
    assert _subscriber_zip(_271("123")) is None


def test_it_reaches_the_eligibility_result():
    r = parse_271_benefits(_271("34986"))
    assert r.member_zip == "34986"
    assert r.to_dict()["member_zip"] == "34986"


# --- the boundary --------------------------------------------------------------------------------


def test_the_portal_query_keeps_clinic_and_member_zips_apart():
    """Two different questions: where care is delivered, and where the member lives. Collapsing them
    is exactly the bug this fixes, so they are separate fields."""
    q = PortalQuery(payer_key="uhc", npi="1497741409", zip_code="34986", member_zip="33101")
    assert q.zip_code == "34986"
    assert q.member_zip == "33101"


def test_member_zip_defaults_to_none_so_existing_callers_are_unchanged():
    q = PortalQuery(payer_key="uhc", npi="1497741409", zip_code="34986")
    assert q.member_zip is None


# --- the driver uses the right ZIP at the right step ----------------------------------------------


def _walk(member_zip, clinic_zip="34986"):
    """Drive `_walk_to_plan` far enough to see which ZIP it commits, with a fake page."""
    from network_probe.portal.drivers.uhc_findcare import UhcFindCareDriver

    committed: list[str] = []
    d = UhcFindCareDriver()
    d._dismiss_overlays = lambda page: None
    # The walk now refuses to click into an unhydrated shell; a bare object() page cannot answer
    # that, so the fake declares itself ready. Nothing else about this test changes.
    d._await_shell = lambda page, **kw: True
    d._click_any = lambda page, sel, label, timeout_ms=8_000: True
    d._commit_location = lambda page, z: (committed.append(z), True)[1]
    # stop after the location step; we only assert the ZIP
    d._pick_plan = lambda page, plan, options=None: None
    pin, trail = d._walk_to_plan(
        object(),
        PortalQuery(payer_key="uhc", npi="1497741409", plan="AARP … FL-0026 (PPO)",
                    zip_code=clinic_zip, member_zip=member_zip),
    )
    return committed, trail


def test_the_plan_step_commits_the_MEMBER_zip_when_present():
    committed, trail = _walk(member_zip="33101", clinic_zip="34986")
    assert committed == ["33101"], "the plan list is scoped to where the member lives"
    assert any("member ZIP" in s for s in trail)


def test_it_falls_back_to_the_clinic_zip_when_the_271_gave_no_address():
    """This is now the path the PRODUCT takes, not just a fallback.

    Decided 2026-08-06: the web form sends only the Clinic ZIP a user typed, and no longer forwards
    the member's residence ZIP it had been reading out of the 271 — a walk should not be scoped by a
    value nobody on screen entered or could see. `member_zip` stays supported end-to-end (the driver,
    the API and the tests above all still honour it) for the day a field is added for it.

    The accepted cost, measured: MA plans are sold by county of residence and UHC's plan step asks
    "select the area where you live", so a patient treated outside their home county can have a plan
    that is not in the clinic county's list. The driver reports that as unpinnable rather than
    guessing, which is the safe direction.
    """
    committed, trail = _walk(member_zip=None, clinic_zip="34986")
    assert committed == ["34986"]
    assert any("clinic ZIP 34986" in s for s in trail)


def test_the_member_zip_is_never_echoed_into_the_stored_note():
    """The trail is persisted and rendered. Which county was searched stays auditable from the
    screenshot, which carries the portal's own echo — the digits do not need to be in the text."""
    _, trail = _walk(member_zip="33101", clinic_zip="34986")
    assert "33101" not in " ".join(trail)
