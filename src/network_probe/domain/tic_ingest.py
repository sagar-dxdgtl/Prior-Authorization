"""Streaming TiC in-network MRF → NPI→TIN crosswalk CSV ingester.

Extracts provider_groups from three sources:

1. Top-level ``provider_references[]`` with **inline** ``provider_groups``
   (UHC-style).
2. ``in_network[].negotiated_rates[]`` embedded ``provider_groups``.
3. Top-level ``provider_references[]`` with a ``location`` URL pointing to
   an **external** file containing provider_groups (Cigna / modern Aetna
   style).  URLs are resolved concurrently; failures on individual URLs are
   logged and skipped — the run continues.

Filtering
---------
* ``npi_filter`` — keep only rows whose NPI is in the set.
* ``tin_filter`` — keep only provider_groups whose TIN is in the set.
  Normalization strips all non-digit characters before comparison, so
  ``"93-3510922"`` and ``"933510922"`` are equivalent.
* Both filters — a row must satisfy **both** (intersection).
"""

from __future__ import annotations

import csv
import gzip
import json
import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import ijson

logger = logging.getLogger(__name__)


def _open(path: str):
    p = Path(path)
    return gzip.open(p, "rb") if str(p).endswith(".gz") else open(p, "rb")


def _normalize_tin(tin: str) -> str:
    """Strip all non-digit characters from a TIN string."""
    return re.sub(r"\D", "", tin)


def _networks_of(pr: dict) -> list[str]:
    """The network names a provider_reference declares, as a list of plain strings.

    ``network_name`` is an ARRAY in the schema and really is populated: BCBS SC's own in-network
    file carries ``"network_name":["PREFERRED BLUE"]`` on every provider_reference, and a group sold
    into two networks lists both. Returns ``[""]`` when the file names none, so such a row is still
    written — an unlabelled fact is worth keeping, an absent one is not.
    """
    raw = pr.get("network_name")
    if isinstance(raw, str):
        raw = [raw]
    names = [" ".join(str(n).split()) for n in (raw or []) if str(n).strip()]
    return names or [""]


def _emit(groups, npi_filter, tin_filter, payer: str | None, writer, seen: set,
          networks: list[str] | None = None) -> None:
    """Write unique (npi, tin, network) rows from a provider_groups list.

    When ``tin_filter`` is provided, skip any provider_group whose normalized
    ``tin.value`` is not in the set.  When ``npi_filter`` is provided, skip
    any NPI not in the set.  Both filters compose as an intersection.

    ``networks`` is the label the MRF itself put on this group. The de-dup key includes it, so a
    provider group sold into two networks becomes two facts — which a single per-file label could
    not express, and which is the whole point of reading it from the data.
    """
    for g in groups or []:
        tin = (g.get("tin") or {}).get("value")
        if not tin:
            continue
        if tin_filter is not None and _normalize_tin(str(tin)) not in tin_filter:
            continue
        for npi in g.get("npi") or []:
            npi = str(npi)
            if npi_filter is not None and npi not in npi_filter:
                continue
            for net in (networks or [""]):
                key = (npi, str(tin), net)
                if key not in seen:
                    seen.add(key)
                    writer.writerow([npi, tin, payer or "", net])


def _default_resolver(url: str) -> dict | list:
    """Fetch *url* and return the parsed JSON body.

    Handles both plain JSON and gzip-compressed responses.  Uses httpx so
    that the same HTTP client config (timeouts, proxies) applies everywhere.
    Raises on any HTTP or parse error — callers must handle exceptions.
    """
    import httpx

    with httpx.Client(follow_redirects=True, timeout=60) as client:
        resp = client.get(url)
        resp.raise_for_status()
        content = resp.content
        # Some CDNs serve .json.gz with Content-Type: application/json
        if url.endswith(".gz") or resp.headers.get("content-encoding") == "gzip":
            content = gzip.decompress(content)
        return json.loads(content)


def _extract_provider_groups(data) -> list:
    """Normalise the three shapes a resolved reference file may take.

    * ``{"provider_groups": [...]}``  — standard shape
    * ``[{...}, ...]``                — bare list of groups
    * ``{"npi": [...], "tin": {...}}`` — single group (treat as one-element list)
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if "provider_groups" in data:
            return data["provider_groups"] or []
        # single-group dict
        if "npi" in data and "tin" in data:
            return [data]
    return []


def ingest_tic(
    tic_path: str,
    out_csv: str,
    npi_filter=None,
    tin_filter=None,
    payer: str | None = None,
    reference_resolver: Callable[[str], dict | list] | None = None,
    max_workers: int = 16,
) -> int:
    """Stream a TiC in-network MRF (.json/.json.gz) → npi,tin,payer CSV.

    Returns the number of unique (npi, tin) rows written.

    Extracts provider_groups from three sources:

    1. ``provider_references[]`` with inline ``provider_groups`` (UHC-style).
    2. ``in_network[].negotiated_rates[]`` embedded ``provider_groups``.
    3. ``provider_references[]`` with ``location`` URLs resolved via
       ``reference_resolver`` (Cigna / modern Aetna style).

    Parameters
    ----------
    tic_path:
        Path to the TiC in-network MRF file (.json or .json.gz).
    out_csv:
        Destination CSV path (columns: npi, tin, payer).
    npi_filter:
        Optional iterable of NPIs (strings or ints) to keep; others skipped.
    tin_filter:
        Optional iterable of TINs (strings) to keep.  Non-digit characters
        are stripped before comparison, so ``"93-3510922"`` matches
        ``"933510922"``.  When set, only provider_groups whose TIN normalizes
        to a value in the set are included.  When combined with
        ``npi_filter``, a row must pass both filters.
    payer:
        Payer label written to the ``payer`` column.
    reference_resolver:
        Callable ``(url: str) -> parsed_json`` used to fetch external
        provider-reference files.  Defaults to ``_default_resolver`` which
        uses httpx and handles gzip.  Pass a fake callable in tests to avoid
        network access.  Errors on individual URLs are logged and skipped —
        the run does not abort.  When ``None`` and no ``location`` URLs are
        present, behaviour is identical to the previous version.
    max_workers:
        Maximum concurrent threads for resolving location URLs (default 16).
    """
    nf = set(map(str, npi_filter)) if npi_filter else None
    tf = {_normalize_tin(str(t)) for t in tin_filter} if tin_filter else None
    seen: set[tuple[str, str, str]] = set()

    with open(out_csv, "w", newline="", encoding="utf-8") as outf:
        writer = csv.writer(outf)
        # `network` is the MRF's own network_name. Appended rather than inserted, so a reader that
        # only knows the old three columns is unaffected, and facts_from_crosswalk_csv (DictReader)
        # simply sees no such key in a CSV written before this.
        writer.writerow(["npi", "tin", "payer", "network"])

        # Pass 1 — top-level provider_references (inline provider_groups)
        # Also collect location URLs for Pass 3 when a resolver is provided.
        # The network lives on the REFERENCE, not on the group, so it is carried alongside the URL:
        # resolving the file later must not lose which network pointed at it.
        location_urls: list[str] = []
        location_networks: dict[str, list[str]] = {}
        with _open(tic_path) as f:
            for pr in ijson.items(f, "provider_references.item"):
                nets = _networks_of(pr)
                if pr.get("provider_groups"):
                    _emit(pr["provider_groups"], nf, tf, payer, writer, seen, nets)
                elif pr.get("location") and reference_resolver is not None:
                    loc = str(pr["location"])
                    if loc not in location_urls:
                        location_urls.append(loc)
                        location_networks[loc] = nets
                    else:  # the same file serving two networks
                        location_networks[loc] = sorted(set(location_networks[loc]) | set(nets))

        # Pass 2 — provider_groups embedded inside each negotiated_rate. These carry no network_name
        # of their own; they are written unlabelled and the loader's --network-name still applies.
        with _open(tic_path) as f:
            for rate in ijson.items(f, "in_network.item.negotiated_rates.item"):
                _emit(rate.get("provider_groups"), nf, tf, payer, writer, seen)

        # Pass 3 — resolve external location URLs concurrently
        if location_urls and reference_resolver is not None:
            failed = 0

            def _resolve_one(url: str) -> tuple[str, list | None]:
                try:
                    data = reference_resolver(url)
                    return url, _extract_provider_groups(data)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("provider_reference resolver failed for %s: %s", url, exc)
                    return url, None

            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_resolve_one, u): u for u in location_urls}
                for fut in as_completed(futures):
                    url, groups = fut.result()
                    if groups is None:
                        failed += 1
                    else:
                        _emit(groups, nf, tf, payer, writer, seen,
                              location_networks.get(url))

            resolved = len(location_urls) - failed
            logger.info(
                "resolved %d external provider-reference files (%d failed)",
                resolved,
                failed,
            )

    return len(seen)
