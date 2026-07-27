"""Reachability probe — what does a *real browser* actually meet at each payer portal?

The 2026-06-28 policy sweep (docs/payer-sources/directory-urls.md) was done with a plain fetch tool
and recorded 403s/timeouts for UHC, Humana, Molina and BCBS-IL. A real Chromium often walks straight
through those, and some of those verdicts were against the wrong host entirely (www.humana.com rather
than finder.humana.com). So before writing nine portal drivers we measure reality, once, and write the
result down. Drivers get built only where a browser genuinely reaches a search control.

Nothing here is defeated or bypassed: each target is loaded exactly once, classified, screenshotted,
and reported. Run:

    python -m network_probe.portal.probe                 # headless
    PORTAL_HEADED=1 python -m network_probe.portal.probe  # headed, so you can see/satisfy a challenge
    python -m network_probe.portal.probe --only uhc-findcare,molina-provider-search
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from network_probe.portal import browser as pb
from network_probe.portal.models import ProbeResult, Reachability
from network_probe.portal.targets import TARGETS, PortalTarget

_SETTLE_MS = 8_000  # bounded wait for SPA hydration after DOMContentLoaded
_RESETTLE_MS = 6_000  # second chance when the first paint was empty (slow SPA vs. real interstitial)


def probe_target(browser, target: PortalTarget, out_dir: Path) -> ProbeResult:
    """Load one portal entry URL, classify what came back, and screenshot it either way."""
    started = time.monotonic()
    http_status: int | None = None
    with pb.portal_page(browser, portal_key=target.key) as page:
        try:
            resp = page.goto(target.entry_url, wait_until="domcontentloaded", timeout=pb.NAV_TIMEOUT_MS)
            http_status = resp.status if resp else None
        except PlaywrightTimeout:
            shot = pb.screenshot(page, out_dir, f"{target.key}-timeout")
            return ProbeResult(
                portal_key=target.key, portal_name=target.portal_name, entry_url=target.entry_url,
                reachability=Reachability.TIMEOUT, screenshot=shot,
                duration_ms=int((time.monotonic() - started) * 1000),
                detail=f"no response within {pb.NAV_TIMEOUT_MS // 1000}s — not demo-viable as a live source.",
            )
        except PlaywrightError as e:
            return ProbeResult(
                portal_key=target.key, portal_name=target.portal_name, entry_url=target.entry_url,
                reachability=Reachability.ERROR, duration_ms=int((time.monotonic() - started) * 1000),
                detail=f"navigation failed: {type(e).__name__}: {str(e).splitlines()[0][:200]}",
            )

        try:  # let a SPA hydrate; not reaching networkidle is normal for analytics-heavy portals
            page.wait_for_load_state("networkidle", timeout=_SETTLE_MS)
        except (PlaywrightTimeout, PlaywrightError):
            pass

        pc = pb.classify(page, http_status, target.search_hints)
        # An empty body is ambiguous: a slow SPA and a silent bot-protection interstitial look alike at
        # first paint. Give it one more settle before believing either — a false CHALLENGE here would
        # wrongly condemn a portal that works.
        if pc.content_chars < pb._MIN_CONTENT_CHARS:
            page.wait_for_timeout(_RESETTLE_MS)
            pc = pb.classify(page, http_status, target.search_hints)

        title = None
        try:
            title = (page.title() or "")[:200]
        except PlaywrightError:
            pass
        shot = pb.screenshot(page, out_dir, f"{target.key}-{pc.reachability.value.lower()}")
        return ProbeResult(
            portal_key=target.key, portal_name=target.portal_name, entry_url=target.entry_url,
            reachability=pc.reachability, http_status=http_status, final_url=page.url, title=title,
            challenge=pc.challenge, search_hint=pc.search_hint, protection=pc.protection,
            content_chars=pc.content_chars, screenshot=shot,
            duration_ms=int((time.monotonic() - started) * 1000), detail=pc.detail,
        )


def probe_all(only: list[str] | None = None, headed: bool | None = None, out_dir: Path | None = None):
    """Probe every target (or just `only`). Sequential by design — never fan out at a payer."""
    out_dir = out_dir or Path(f".cache/portal-probe/{date.today().isoformat()}")
    targets = [t for t in TARGETS if not only or t.key in only]
    results: list[ProbeResult] = []
    with pb.browser_session(headed=headed) as browser:
        for t in targets:
            r = probe_target(browser, t, out_dir)
            results.append(r)
            print(f"  {r.reachability.value:<11} {t.key:<26} {r.http_status or '-':>4}  {r.duration_ms:>6}ms")
    return results, out_dir


_VERDICT_GUIDE = {
    Reachability.SEARCHABLE: "write the driver",
    Reachability.LOADED: "write the driver (navigate deeper first)",
    Reachability.CHALLENGE: "headed + human-satisfied session, else FHIR fallback",
    Reachability.WAF_BLOCK: "use the CMS-mandated FHIR directory instead",
    Reachability.TIMEOUT: "use the CMS-mandated FHIR directory instead",
    Reachability.ERROR: "re-probe; fix the entry URL",
}


def write_report(results: list[ProbeResult], out_dir: Path, path: Path) -> Path:
    """Write the probe report — the record that decides which drivers get built."""
    lines = [
        f"# Payer-portal reachability probe — {date.today().isoformat()}",
        "",
        "What a real Chromium met at each portal's public entry URL. Supersedes the 2026-06-28 "
        "fetch-tool sweep in `docs/payer-sources/directory-urls.md` for reachability only (that "
        "document remains authoritative on robots.txt and Terms of Use).",
        "",
        "Method: one navigation per portal, sequential, real desktop Chrome UA, 45s budget, full-page "
        "screenshot of every outcome. **No challenge was solved or bypassed** — challenges are detected, "
        "screenshotted and reported. Where a portal refuses automated access, the payer's CMS-mandated "
        "public FHIR Provider Directory API (CMS-9115-F) is the sanctioned path for the same question.",
        "",
        f"Screenshots: `{out_dir}/`",
        "",
        "| Portal | Sheet row(s) | Result | HTTP | Challenge shown | Bot-protection present | Next step |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        t = next((x for x in TARGETS if x.key == r.portal_key), None)
        rows = "<br>".join(t.sheet_rows) if t else ""
        lines.append(
            f"| `{r.portal_key}`<br>{r.portal_name} | {rows} | **{r.reachability.value}** | "
            f"{r.http_status or '—'} | {r.challenge or 'none'} | {r.protection or 'none detected'} | "
            f"{_VERDICT_GUIDE[r.reachability]} |"
        )
    challenged = [r for r in results if r.reachability is Reachability.CHALLENGE]
    lines += [
        "",
        f"**Challenges encountered: {len(challenged)} of {len(results)}.**"
        + (
            " " + ", ".join(f"`{r.portal_key}` ({r.challenge})" for r in challenged)
            if challenged
            else " No portal in this set presented a CAPTCHA or human-verification challenge to a real "
            "browser at its entry URL."
        ),
        "",
        "Note the distinction in the two right-hand columns: several portals *run* bot-protection scripts "
        "while serving content normally. A script being present is not a gate; only a challenge actually "
        "shown to the user is.",
    ]
    lines += ["", "## Per-portal detail", ""]
    for r in results:
        t = next((x for x in TARGETS if x.key == r.portal_key), None)
        lines += [
            f"### {r.portal_name} (`{r.portal_key}`)",
            "",
            f"- **Entry URL:** {r.entry_url}",
            f"- **Final URL:** {r.final_url or '—'}",
            f"- **Result:** {r.reachability.value} (HTTP {r.http_status or '—'}, {r.duration_ms}ms)",
            f"- **Page title:** {r.title or '—'}",
            f"- **Search control:** {r.search_hint or 'not found at entry URL'}",
            f"- **Screenshot:** `{r.screenshot or 'none'}`",
            f"- **Detail:** {r.detail}",
        ]
        if t and t.fhir_fallback:
            lines.append(f"- **FHIR fallback:** {t.fhir_fallback}")
        if t and t.notes:
            lines.append(f"- **Prior knowledge:** {t.notes}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))
    (out_dir / "probe.json").write_text(json.dumps([r.to_dict() for r in results], indent=2))
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description="Probe payer-portal reachability with a real browser.")
    ap.add_argument("--only", help="comma-separated portal keys to probe")
    ap.add_argument("--headed", action="store_true", help="run headed (lets a human satisfy a challenge)")
    ap.add_argument("--report", default=f"docs/discovery/PORTAL-PROBE-{date.today().isoformat()}.md")
    args = ap.parse_args()

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    print(f"Probing {len(only or TARGETS)} portal(s){' headed' if args.headed else ''}…")
    results, out_dir = probe_all(only=only, headed=args.headed or None)
    report = write_report(results, out_dir, Path(args.report))

    tally: dict[str, int] = {}
    for r in results:
        tally[r.reachability.value] = tally.get(r.reachability.value, 0) + 1
    print("\n" + "  ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    print(f"Report: {report}")


if __name__ == "__main__":
    main()
