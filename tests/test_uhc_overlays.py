"""UHC's guest shell gates its own coverage cards behind dismissable overlays, and it redeploys.

On 2026-07-31 UHC shipped a "new guest experience": a modal headed "Welcome to the new guest
experience!" with a Get started button, on top of the coverage-type cards. The driver knew only the
two data-testids observed on 2026-07-28, so the modal was never dismissed, the coverage click landed
on nothing, and all three UHC rows of Ins Test 3 died at "coverage type 'Medicare' NOT clickable" —
after 198s of retries. The exact query that had returned OUT_OF_NETWORK three times that same
afternoon started failing, with no code change in between.

The lesson encoded here: dismissal must not depend on a single vendor testid surviving a redeploy.
Accessible names and visible labels are the durable handles.
"""

from network_probe.portal.drivers import uhc_findcare as u


def test_new_guest_experience_modal_is_dismissable():
    """The overlay list must cover the 2026-07-31 redeploy, not only the 2026-07-28 shell."""
    joined = " ".join(u._OVERLAYS).lower()
    assert "get started" in joined or "get-started" in joined, (
        "the 'Welcome to the new guest experience!' modal has a Get started button and no "
        "recognisable close testid; without a handle for it the coverage cards stay unclickable"
    )


def test_the_original_testids_are_kept():
    """A redeploy may be partial or reverted, so the older handles must not be dropped."""
    joined = " ".join(u._OVERLAYS)
    assert "guest-start-modal" in joined
    assert "guest-coachmark-container" in joined


def test_dismissal_survives_a_selector_that_no_longer_exists():
    """Every entry is tried; one that matches nothing must not stop the rest."""
    calls = []

    class _B:
        def __init__(self, vis):
            self._vis = vis

        def is_visible(self):
            if self._vis == "boom":
                raise RuntimeError("detached")
            return self._vis

        def click(self):
            calls.append(True)

    class _Loc:
        def __init__(self, b):
            self.first = b

    class _Page:
        def __init__(self):
            self.i = -1

        def locator(self, sel):
            self.i += 1
            return _Loc(_B("boom" if self.i == 0 else True))

        def wait_for_timeout(self, ms):
            return None

    u.UhcFindCareDriver()._dismiss_overlays(_Page())
    assert len(calls) == len(u._OVERLAYS) - 1, "a dead selector must not abort the remaining ones"
