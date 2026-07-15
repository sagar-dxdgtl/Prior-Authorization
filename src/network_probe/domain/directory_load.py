"""Download, parse, and load monthly PDF provider directories into payer_directory_entries.

For PDF-only plans (e.g. Align Senior Care on AllyAlign). "App-scheduled monthly" without any
external scheduler: a startup background loop checks staleness daily and reloads when a new month
arrives (so it's restart-safe — a fresh boot re-checks and catches up). Gated behind the
ENABLE_DIRECTORY_REFRESH env flag so tests/CI never trigger a 14 MB download.
"""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
import uuid

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from network_probe.core._http import DEFAULT_UA
from network_probe.domain.directory_match import _norm
from network_probe.domain.directory_pdf import parse_directory_pdf, toc_specialties

# payer_key MUST equal the roster slug (catalogue key) so DbDirectoryAdapter — which queries
# payer_directory_entries by that key — finds the rows. Each entry has a `format` (parser) and
# either a static `pdf_url` or a `page_url` + `link_pattern` to discover a date-stamped URL.
import re as _re  # noqa: E402

PDF_DIRECTORIES: dict[str, dict] = {
    "align-senior-health-plan-fl-south-florida": {
        "label": "Align Senior Care",
        "format": "allyalign",
        "pdf_url": (
            "https://prod-websiteapis.allyalign.cc/Api/ProviderDirectory/Download"
            "?fileName=Provider%20Directory%20PDF%20Align%20Senior%20Care%20FL%20ISNP_FL.pdf"
        ),
    },
    "eternalhealth-az": {
        "label": "eternalHealth",
        "format": "aaneel",
        # the wp-content URL is date-stamped (…ProviderDirectory-AZ-11212025.pdf) and changes each
        # update — discover the current AZ PDF link from the find-a-provider page.
        "page_url": "https://www.eternalhealth.com/for-members/find-a-provider-or-pharmacy/",
        "link_pattern": r"https://www\.eternalhealth\.com/wp-content/uploads/[^\"'<>]*ProviderDirectory-AZ-[^\"'<>]*\.pdf",
    },
    "community-care-plan-fl-south-florida": {
        "label": "Community Care Plan",
        "format": "ccp",
        # 3 static per-county PDFs (Broward/Miami-Dade/Palm Beach — the client's FL-South Florida
        # market), confirmed live 2026-07-15. No date-stamp discovery needed (unlike eternalHealth
        # above) — these URLs are stable month to month, only the PDF content changes.
        "pdf_urls": [
            "https://providerdirectory.ccpcares.org/Content/PDFs/ProviderDirectory_Broward.pdf",
            "https://providerdirectory.ccpcares.org/Content/PDFs/ProviderDirectory_MiamiDade.pdf",
            "https://providerdirectory.ccpcares.org/Content/PDFs/ProviderDirectory_PalmBeach.pdf",
        ],
    },
}


def resolve_pdf_url(cfg: dict) -> str:
    """Return the PDF URL — static `pdf_url`, or discovered from `page_url` via `link_pattern`."""
    if cfg.get("pdf_url"):
        return cfg["pdf_url"]
    page, pat = cfg.get("page_url"), cfg.get("link_pattern")
    if not (page and pat):
        raise ValueError("PDF-directory config needs either pdf_url or page_url+link_pattern")
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"user-agent": DEFAULT_UA}) as c:
        html = c.get(page).text
    m = _re.search(pat, html)
    if not m:
        raise ValueError(f"no PDF link matching {pat!r} on {page}")
    return m.group(0)


def resolve_pdf_urls(cfg: dict) -> list[str]:
    """Return every PDF URL this payer's directory is split across. Most payers publish one file
    (`pdf_url` static, or discovered via `page_url`+`link_pattern`) -- `resolve_pdf_url` already
    handles both, so this just wraps its result in a single-item list. Payers whose directory is
    split into several files (e.g. Community Care Plan's per-county PDFs) set `pdf_urls` (plural)
    directly instead."""
    if cfg.get("pdf_urls"):
        return list(cfg["pdf_urls"])
    return [resolve_pdf_url(cfg)]


def _month() -> str:
    return _dt.date.today().strftime("%Y-%m")


def download_pdf(url: str, timeout: float = 180.0) -> bytes:
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"user-agent": DEFAULT_UA}) as c:
        r = c.get(url)
        r.raise_for_status()
        return r.content


def rows_from_pdf(path: str, payer_key: str, version: str, fmt: str = "allyalign") -> list[dict]:
    """Parse a directory PDF into flat payer_directory_entries mappings (one per provider-location)."""
    specs = toc_specialties(path) if fmt == "allyalign" else None
    now = _dt.datetime.now(_dt.UTC)
    rows: list[dict] = []
    for e in parse_directory_pdf(path, fmt=fmt, specialties=specs):
        for loc in e.locations or [{}]:
            rows.append(
                {
                    "id": uuid.uuid4(),
                    "tenant_id": None,
                    "payer_key": payer_key,
                    "last_name": _norm(e.last_name),
                    "first_name": _norm(e.first_name),
                    "full_name": e.name[:240],
                    "specialty": e.specialty,
                    "address": (loc.get("address") or None),
                    "city": (loc.get("city") or None),
                    "state": (loc.get("state") or None),
                    "zip": (loc.get("zip") or None),
                    "accepting_new": e.accepting_new,
                    "source_version": version,
                    "loaded_at": now,
                }
            )
    return rows


def _replace_rows(payer_key: str, rows: list[dict], engine=None) -> None:
    from network_probe.db.base import owner_engine  # owner role writes global reference data
    from network_probe.db.models import PayerDirectoryEntry

    with Session(engine or owner_engine()) as s:
        s.execute(
            delete(PayerDirectoryEntry).where(
                PayerDirectoryEntry.payer_key == payer_key,
                PayerDirectoryEntry.tenant_id.is_(None),
            )
        )
        if rows:
            s.bulk_insert_mappings(PayerDirectoryEntry, rows)
        s.commit()


def _rows_from_url(url: str, payer_key: str, version: str, fmt: str) -> list[dict]:
    """Download one PDF and parse it into rows, cleaning up its temp file afterward."""
    data = download_pdf(url)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        return rows_from_pdf(tmp_path, payer_key, version, fmt=fmt)
    finally:
        if tmp_path:
            os.unlink(tmp_path)


def load_directory(
    payer_key: str, *, pdf_path: str | None = None, pdf_bytes: bytes | None = None,
    version: str | None = None, engine=None,
) -> int:
    """Download (or use the given) PDF(s), parse them, and atomically replace this payer's rows.
    Returns the number of rows loaded.

    `pdf_path`/`pdf_bytes` override a single file (used by tests / one-off loads) exactly as
    before. Without an override, every URL `resolve_pdf_urls()` returns for this payer is
    downloaded and parsed in turn and the rows concatenated -- if any one fails, the whole call
    raises before `_replace_rows()` is reached, so a payer's directory is never left partially
    replaced from some counties/files but not others."""
    cfg = PDF_DIRECTORIES.get(payer_key)
    if cfg is None and pdf_path is None and pdf_bytes is None:
        raise ValueError(f"unknown PDF-directory payer {payer_key!r}")
    version = version or _month()
    fmt = (cfg or {}).get("format", "allyalign")
    if pdf_path is not None or pdf_bytes is not None:
        tmp_path = None
        try:
            if pdf_path is None:
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_bytes)
                    tmp_path = tmp.name
                pdf_path = tmp_path
            rows = rows_from_pdf(pdf_path, payer_key, version, fmt=fmt)
        finally:
            if tmp_path:
                os.unlink(tmp_path)
    else:
        rows = []
        for url in resolve_pdf_urls(cfg):
            rows.extend(_rows_from_url(url, payer_key, version, fmt=fmt))
    _replace_rows(payer_key, rows, engine)
    return len(rows)


def loaded_version(payer_key: str, engine=None) -> str | None:
    from network_probe.db.base import app_engine
    from network_probe.db.models import PayerDirectoryEntry

    with Session(engine or app_engine()) as s:
        return s.execute(
            select(func.max(PayerDirectoryEntry.source_version)).where(
                PayerDirectoryEntry.payer_key == payer_key
            )
        ).scalar()


def is_stale(payer_key: str, engine=None) -> bool:
    """Stale = never loaded, or last loaded in a prior calendar month."""
    return loaded_version(payer_key, engine) != _month()


def refresh_if_stale(payer_key: str) -> int | None:
    return load_directory(payer_key) if is_stale(payer_key) else None


async def monthly_refresh_loop(check_interval_seconds: int = 86_400) -> None:
    """Daily staleness check → reload when a new month's directory is due. Restart-safe."""
    import asyncio

    while True:
        for pk in PDF_DIRECTORIES:
            try:
                await asyncio.to_thread(refresh_if_stale, pk)
            except Exception:  # never let a bad download/parse kill the loop
                pass
        await asyncio.sleep(check_interval_seconds)


def refresh_enabled() -> bool:
    return os.getenv("ENABLE_DIRECTORY_REFRESH", "").strip() in ("1", "true", "yes")
