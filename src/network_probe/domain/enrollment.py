"""Provider enrollment source — Medicare (PECOS) + state Medicaid, by NPI.

Reuses the data paths proven in the Physician-Search screening project:

  * PECOS / Medicare — the CMS **"Order and Referring"** dataset on data.cms.gov (national, no auth).
    Resolve the current data-file id via the JSON:API metadata endpoint, then hit the data-viewer with
    ``keyword=<NPI>``. Enrolled ⇔ any of Part B / DME / HHA / PMD / Hospice == "Y" for the NPI's row.
  * Medicaid — strictly **per state**. NY is a clean Socrata example
    (health.data.ny.gov, "Medicaid Enrolled Provider Listing"); other states are different endpoints
    (see the screening project's medicaid_sources.csv). Enrolled ⇔ an NPI-exact row exists.

Used as a **negative filter, gated by line of business**: a provider who is NOT Medicare-enrolled
cannot be in-network for any Medicare (FFS or MA) plan; not state-Medicaid-enrolled → cannot be in a
Medicaid MCO. Only ``enrolled is False`` (a *successful* lookup that found no match) is decisive OON;
a failed/unreachable lookup returns ``enrolled = None`` (undetermined) — never a false OON. And
``enrolled is True`` only CLEARS the gate (necessary, not sufficient) — the plan network
(credentialing / TiC) still decides the positive case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from network_probe.core._http import CachedClient

# CMS "Order and Referring" (carries the PECOS Part B/DME/HHA/PMD/Hospice eligibility flags).
_PECOS_META = (
    "https://data.cms.gov/jsonapi/node/dataset"
    "?filter[field_dataset_type.name]=Order and Referring"
    "&sort=-field_dataset_version,-field_re_release_version"
    "&fields[node--dataset]=title,field_dataset_version&page[limit]=1"
)
_PECOS_DATA = "https://data.cms.gov/data-api/v1/dataset/{ds}/data-viewer"
_PECOS_FLAGS = {"PARTB", "DME", "HHA", "PMD", "HOSPICE"}

# Per-state Medicaid enrollment endpoints (extend from the screening project's medicaid_sources.csv).
# NY: Socrata "Medicaid Enrolled Provider Listing".
_NY_SOCRATA = "https://health.data.ny.gov/resource/keti-qx5t.json"


@dataclass
class EnrollmentResult:
    enrolled: bool | None  # True (enrolled) | False (confirmed not enrolled) | None (undetermined)
    program: str  # "medicare-pecos" | "medicaid-<ST>"
    detail: str
    flags: dict = field(default_factory=dict)  # e.g. {"partb":"Y","dme":"Y",...} (PECOS)
    source_date: str | None = None

    def to_dict(self) -> dict:
        return {
            "enrolled": self.enrolled,
            "program": self.program,
            "detail": self.detail,
            "flags": self.flags,
            "source_date": self.source_date,
        }


def _norm_npi(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def live_enabled() -> bool:
    """Live PECOS/Medicaid HTTP lookups run outside the test env; unit tests inject mock clients or
    lookup fns instead, so callers using the default live client skip the network under APP_ENV=test."""
    try:
        from network_probe.core.config import get_settings

        return get_settings().app_env != "test"
    except Exception:
        return True


def pecos_enrollment(npi, client: CachedClient | None = None) -> EnrollmentResult:
    """Is this NPI enrolled in Medicare (present in the CMS Order-and-Referring/PECOS file)?"""
    n = _norm_npi(npi)
    if len(n) != 10:
        return EnrollmentResult(None, "medicare-pecos", "no valid NPI to check")
    client = client or CachedClient()
    try:
        meta = client.get_json(_PECOS_META, headers={"accept": "application/vnd.api+json"})
        ds = ((meta.get("data") or [{}])[0] or {}).get("id")
        if not ds:
            return EnrollmentResult(None, "medicare-pecos", "could not resolve the PECOS dataset id")
        url = _PECOS_DATA.format(ds=ds) + "?" + urlencode({"keyword": n, "size": 20, "offset": 0})
        data = client.get_json(url, headers={"accept": "application/json", "user-agent": "network-probe/1.0"})
    except Exception as exc:  # noqa: BLE001 — a lookup failure must NOT be read as "not enrolled"
        return EnrollmentResult(None, "medicare-pecos", f"PECOS lookup failed ({type(exc).__name__})")

    meta_obj = data.get("meta") or {}
    headers = meta_obj.get("headers") or []
    rows = data.get("data") or []
    idx = {h.upper(): i for i, h in enumerate(headers)}
    npi_i = idx.get("NPI", 0)
    match = next((r for r in rows if _norm_npi(r[npi_i]) == n), None) if headers else None
    src_date = (meta_obj.get("data_file_name") or None)
    if match is None:
        return EnrollmentResult(
            False, "medicare-pecos",
            f"NPI {n} is not in the CMS Order-and-Referring (PECOS) file — not Medicare-enrolled.",
            source_date=src_date,
        )
    flags = {headers[i].lower(): match[i] for h, i in idx.items() if h in _PECOS_FLAGS}
    enrolled = any(str(v).strip().upper() == "Y" for v in flags.values())
    tail = "Medicare-enrolled (PECOS)." if enrolled else "listed in PECOS but no active order/refer flags."
    return EnrollmentResult(enrolled, "medicare-pecos", f"NPI {n}: {tail}", flags=flags, source_date=src_date)


def _medicaid_ny(n: str, client: CachedClient) -> EnrollmentResult:
    url = _NY_SOCRATA + "?" + urlencode({"$where": f"npi='{n}'", "$limit": 50})
    data = client.get_json(url, headers={"accept": "application/json"})
    rows = data if isinstance(data, list) else (data.get("data") or [])
    match = [r for r in rows if _norm_npi(r.get("npi")) == n]
    if match:
        types = sorted({str(r.get("medicaid_type") or "").strip() for r in match if r.get("medicaid_type")})
        return EnrollmentResult(True, "medicaid-NY", f"NPI {n} is enrolled in NY Medicaid"
                                + (f" ({', '.join(types)})" if types else "") + ".")
    return EnrollmentResult(False, "medicaid-NY", f"NPI {n} not in NY Medicaid Enrolled Provider Listing.")


# state -> live-API lookup fn (extend per-state; states without a fn return undetermined)
_MEDICAID_STATE_APIS = {"NY": _medicaid_ny}


def medicaid_enrollment(npi, state, client: CachedClient | None = None) -> EnrollmentResult:
    """Is this NPI enrolled in the given state's Medicaid? Per-state; only implemented states can
    return True/False — others return None (undetermined), so we never assert OON we can't back up."""
    n = _norm_npi(npi)
    st = (state or "").strip().upper()
    if len(n) != 10 or not st:
        return EnrollmentResult(None, f"medicaid-{st or '?'}", "no valid NPI/state to check")
    fn = _MEDICAID_STATE_APIS.get(st)
    if fn is None:
        return EnrollmentResult(None, f"medicaid-{st}", f"no Medicaid enrollment source wired for {st} yet")
    client = client or CachedClient()
    try:
        return fn(n, client)
    except Exception as exc:  # noqa: BLE001
        return EnrollmentResult(None, f"medicaid-{st}", f"{st} Medicaid lookup failed ({type(exc).__name__})")
