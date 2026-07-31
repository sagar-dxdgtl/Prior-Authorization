"""Playwright lifecycle + page classification for the portal layer.

Sync Playwright on purpose: the API routes are sync `def`, so FastAPI runs them in a threadpool
thread with no asyncio loop — which is exactly where sync Playwright is safe. A fresh browser context
per capture keeps cookies from bleeding between payers.

What this module will NOT do, by design: no CAPTCHA solving, no stealth/fingerprint patching, no
auth bypass. A challenge is *detected, screenshotted and reported* — never defeated. A payer that
refuses automated access yields BLOCKED, and the caller falls back to that payer's CMS-mandated
public FHIR directory (the sanctioned automated path for the same question).

Session reuse is supported through `storage_state`: if a human has satisfied a portal's challenge in
a headed session, that saved state is reused until it expires. The control did its job — a human
answered it — and reusing the resulting session is ordinary browser behaviour.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal.models import Reachability

# A real desktop Chrome UA, with the version taken from the browser we ACTUALLY launched.
#
# This is not evasion — Playwright drives genuine Chromium, and the only thing being changed is the
# default UA's "HeadlessChrome" token, which some CDNs 403 on sight even for legitimate clients.
# What matters is that the version must not be invented. A hardcoded "Chrome/131" against a real
# Chromium 149 is a false claim, and it is exactly the inconsistency Aetna's Akamai edge rejects: a
# request whose User-Agent names a browser its client hints contradict gets denied, while a real
# visible browser passes. my.wellcare.com separately warns the user that Chrome 131 has a known
# vulnerability. Deriving the version from `browser.version` keeps UA and client hints telling the
# same true story, and means this can never drift again when Playwright updates.
_UA_TEMPLATE = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)


def user_agent(browser=None) -> str:
    """The UA to present: real Chromium major version where we can read it, else the template's."""
    major = "149"
    try:
        major = (browser.version or "").split(".")[0] or major
    except Exception:  # noqa: BLE001 — a UA is never worth failing a capture over
        pass
    return _UA_TEMPLATE.format(major=major)

NAV_TIMEOUT_MS = 45_000  # hard per-navigation budget; a portal slower than this is not demo-viable

# Bot-protection *vendors*, detected in page source. Presence alone is NOT a block: most of these run
# transparently on pages that serve fine. Only meaningful when the page also failed to render content
# (an interstitial) or shows a challenge/refusal phrase. Notably "akamai" is deliberately absent — it
# names a CDN half the internet serves from, and matching it produced two false WAF_BLOCKs on 2026-07-28.
_PROTECTION_MARKERS: tuple[tuple[str, str], ...] = (
    ("_incapsula_resource", "Imperva / Incapsula"),
    ("captcha-delivery.com", "DataDome"),
    ("datadome", "DataDome"),
    ("px-captcha", "HUMAN / PerimeterX"),
    ("perimeterx", "HUMAN / PerimeterX"),
    ("kpsdk", "Kasada"),
    ("challenges.cloudflare.com", "Cloudflare Turnstile"),
    ("cf-turnstile", "Cloudflare Turnstile"),
    ("grecaptcha", "Google reCAPTCHA"),
    ("recaptcha", "Google reCAPTCHA"),
    ("hcaptcha", "hCaptcha"),
)

# Interactive challenges — a human is being asked to prove humanity. Matched against *visible text*,
# not page source, so a dormant script never counts as a challenge.
_CHALLENGE_PHRASES: tuple[str, ...] = (
    "press & hold",
    "press and hold",
    "verify you are a human",
    "verify you are human",
    "are you a robot",
    "complete the security check",
    "checking your browser",
    "unusual traffic",
    "i'm not a robot",
)

# Hard refusals — no challenge offered, the edge simply says no. Visible text only.
_WAF_PHRASES: tuple[str, ...] = (
    "access denied",
    "request unsuccessful",
    "incident id",
    "you don't have permission to access",
    "has been blocked",
    "error 1020",
    "reference #",
)

_MIN_CONTENT_CHARS = 40  # below this the page rendered nothing a human could read


def profile_dir(portal_key: str) -> Path:
    """Where a portal's reusable session state lives (one human-satisfied session per portal)."""
    root = Path(os.environ.get("PORTAL_PROFILE_DIR") or ".cache/portal-profiles")
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{portal_key}.json"


@contextlib.contextmanager
def browser_session(headed: bool | None = None, proxy: str | None = None) -> Iterator:
    """Launch Chromium and yield it. `headed` defaults to $PORTAL_HEADED (a headed run is what lets an
    operator satisfy a challenge themselves). `proxy` is only for genuinely US-geo-locked hosts."""
    if headed is None:
        headed = os.environ.get("PORTAL_HEADED", "").strip().lower() in ("1", "true", "yes")
    launch: dict = {"headless": not headed}
    if proxy:
        launch["proxy"] = {"server": proxy}
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        try:
            yield browser
        finally:
            with contextlib.suppress(Exception):
                browser.close()


@contextlib.contextmanager
def portal_page(browser, portal_key: str | None = None, reuse_session: bool = True) -> Iterator[Page]:
    """A fresh context + page, optionally restoring a previously saved (human-satisfied) session."""
    ctx_args: dict = {
        "user_agent": user_agent(browser),
        "viewport": {"width": 1440, "height": 900},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }
    state = profile_dir(portal_key) if (portal_key and reuse_session) else None
    if state and state.exists():
        ctx_args["storage_state"] = str(state)
    context = browser.new_context(**ctx_args)
    context.set_default_timeout(NAV_TIMEOUT_MS)
    page = context.new_page()
    try:
        yield page
    finally:
        with contextlib.suppress(Exception):
            context.close()


def save_session(page: Page, portal_key: str) -> Path:
    """Persist this context's cookies/storage so a satisfied challenge is not re-asked next run."""
    path = profile_dir(portal_key)
    page.context.storage_state(path=str(path))
    return path


def screenshot(page: Page, out_dir: Path, name: str) -> str | None:
    """Full-page screenshot. Every outcome is captured — a screenshot of a block is evidence too."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
    except PlaywrightError:
        try:  # a mid-navigation page can refuse full_page; the viewport shot still evidences it
            page.screenshot(path=str(path))
        except PlaywrightError:
            return None
    return path.name


#: `settle` polls the rendered DOM this often, and treats this many unchanged samples as "rendered".
#: The ceiling exists only so a portal that animates forever cannot hang a walk; a healthy step
#: reaches quiescence in two polls.
_SETTLE_POLL_MS = 250
_SETTLE_STABLE_POLLS = 2
_SETTLE_MAX_S = 6.0


def settle(page: Page, pause_ms: int = 2_500) -> None:
    """Wait for a step to finish rendering, then pause. Best-effort — never raises.

    Deliberately NOT `wait_for_load_state("networkidle")`. Measured live on 2026-07-31:

      * UHC Find Care  — networkidle timed out 7 times out of 7, at its full 10s. 89s of a 143s walk.
      * Cigna HCP      — timed out 7 times out of 10, at its full 15s. 151s of a 167s walk (90%).

    These directories stream analytics for as long as the tab is open, so on most steps networkidle
    never fires and is simply a hidden 10-15s sleep. It is not useless everywhere — Cigna's *did*
    settle on 3 of 10 steps, twice instantly — which is why the replacement must stay fast on pages
    that are genuinely quiet rather than swapping one fixed delay for another.

    Polling the rendered DOM does both: it returns in ~500ms on a settled page and bounds the
    pathological case at `_SETTLE_MAX_S` instead of the portal's full network timeout.

    `pause_ms` stays a per-driver knob. Those pauses are hand-tuned against each SPA's own hydration
    (Cigna passes 3-8s at different steps) and are NOT part of what was measured here — do not
    "optimise" them without evidence for the specific step.
    """
    last, stable = -1, 0
    deadline = time.monotonic() + _SETTLE_MAX_S
    while time.monotonic() < deadline:
        try:
            size = page.evaluate("document.body ? document.body.innerHTML.length : 0")
        except (PlaywrightTimeout, PlaywrightError):
            break  # mid-navigation the page cannot be measured; the pause below still applies
        if size == last:
            stable += 1
            if stable >= _SETTLE_STABLE_POLLS:
                break
        else:
            last, stable = size, 0
        try:
            page.wait_for_timeout(_SETTLE_POLL_MS)
        except PlaywrightError:
            break
    try:
        page.wait_for_timeout(pause_ms)
    except PlaywrightError:
        pass


def find_search_control(page: Page, hints: tuple[str, ...], timeout_ms: int = 4_000) -> str | None:
    """First hint selector that resolves to a visible control, or None. Proves the portal is driveable
    rather than merely serving HTML."""
    for sel in hints:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            return sel
        except (PlaywrightTimeout, PlaywrightError):
            continue
    return None


@dataclass
class PageClass:
    """The classifier's reading of a loaded page."""

    reachability: Reachability
    detail: str
    challenge: str | None = None  # vendor, when an interactive challenge is actually being shown
    search_hint: str | None = None
    protection: str | None = None  # bot-protection vendor present in source (informational on its own)
    content_chars: int = 0


def classify(page: Page, http_status: int | None, search_hints: tuple[str, ...] = ()) -> PageClass:
    """Classify what the browser met.

    Order matters. A *visible* challenge outranks the HTTP status, because interactive challenges are
    commonly served with a 403 — that is a gate being offered, not a flat refusal. Bot-protection
    scripts are reported but never on their own treated as a block: they run transparently on pages
    that serve perfectly well, which is exactly the false positive this replaced.
    """
    try:
        html = (page.content() or "").lower()
    except PlaywrightError as e:
        return PageClass(Reachability.ERROR, f"could not read page content: {e}")
    try:
        text = (page.inner_text("body") or "").lower()
    except PlaywrightError:
        text = ""

    protection = next((v for m, v in _PROTECTION_MARKERS if m in html), None)
    chars = len(text.strip())
    vendor = protection or "unidentified vendor"

    hit = next((p for p in _CHALLENGE_PHRASES if p in text), None)
    if hit:
        return PageClass(
            Reachability.CHALLENGE, protection=protection, content_chars=chars, challenge=vendor,
            detail=(
                f"interactive challenge shown ({vendor}, visible text matched {hit!r}). Not solved by "
                f"design — screenshotted and reported; a human can satisfy it in a headed run, after "
                f"which the saved session is reused."
            ),
        )

    hit = next((p for p in _WAF_PHRASES if p in text), None)
    if hit or (http_status is not None and http_status in (401, 403, 429)):
        return PageClass(
            Reachability.WAF_BLOCK, protection=protection, content_chars=chars,
            detail=(
                f"edge refused automated access (HTTP {http_status}"
                + (f", visible text matched {hit!r}" if hit else "")
                + "). No challenge offered — route this payer to its public FHIR directory instead."
            ),
        )

    # Nothing rendered. With a protection script present that is an invisible interstitial (Imperva's
    # JS check serves HTTP 200 and an empty body); without one it is just an un-hydrated SPA.
    if chars < _MIN_CONTENT_CHARS:
        if protection:
            return PageClass(
                Reachability.CHALLENGE, protection=protection, content_chars=chars, challenge=vendor,
                detail=(
                    f"page rendered no readable content ({chars} chars) with {vendor} present — a silent "
                    f"bot-protection interstitial. Retry headed; if it still does not clear, use the "
                    f"payer's public FHIR directory."
                ),
            )
        return PageClass(
            Reachability.LOADED, protection=protection, content_chars=chars,
            detail=(
                f"HTTP {http_status} but no readable content ({chars} chars) and no bot-protection "
                f"script — an un-hydrated SPA. Needs a longer settle or a deeper entry URL."
            ),
        )

    sel = find_search_control(page, search_hints) if search_hints else None
    note = f" (bot protection present: {protection}, served transparently)" if protection else ""
    if sel:
        return PageClass(
            Reachability.SEARCHABLE, protection=protection, content_chars=chars, search_hint=sel,
            detail=f"loaded with a usable provider-search control ({sel}){note}.",
        )
    return PageClass(
        Reachability.LOADED, protection=protection, content_chars=chars,
        detail=(
            f"loaded {chars} chars of content but no search control matched at the entry URL — the "
            f"driver needs to navigate deeper (plan selection / hub link) before searching{note}."
        ),
    )
