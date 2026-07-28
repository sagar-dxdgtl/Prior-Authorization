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

import json
from dataclasses import dataclass, field
from datetime import date, datetime
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

# Per-state Medicaid enrollment endpoints (ported from the screening project's medicaid_sources.csv).
# NY: Socrata "Medicaid Enrolled Provider Listing".
_NY_SOCRATA = "https://health.data.ny.gov/resource/keti-qx5t.json"
# IL: HFS individual-provider directory (the state agency's own list), JSON POST search.
_IL_HFS = "https://ext2.hfs.illinois.gov/hfsindprovdirectory/Main/ProviderDataSource/"
# ME: the MaineCare provider directory, an anonymous-read FHIR R4 server.
_ME_FHIR = "https://maineproviderdirectory.verityanalytics.org/fhir"

# --- Medicare ASSIGNMENT (a different axis than enrollment — see assignment_status) --------------
# CMS "Doctors and Clinicians" National Downloadable File — the data behind medicare.gov Care
# Compare. `ind_assgn` is the individual's participation status and is the field Care Compare
# renders as "Charges the Medicare-approved amount".
_DAC_DATASET = "mj5m-pzi6"
_DAC_QUERY = "https://data.cms.gov/provider-data/api/1/datastore/query/{ds}/0"
# Opt-out affidavits get their own monthly file; resolve the current one the same way as PECOS.
_OPTOUT_META = (
    "https://data.cms.gov/jsonapi/node/dataset"
    "?filter[field_dataset_type.name]=Opt Out Affidavits"
    "&sort=-field_dataset_version"
    "&fields[node--dataset]=title,field_dataset_version&page[limit]=1"
)
_OPTOUT_DATA = "https://data.cms.gov/data-api/v1/dataset/{ds}/data-viewer"

#: assignment_status() outcomes.
ACCEPTS = "accepts"  # ind_assgn = Y — charges the Medicare-approved amount
MAY_EXCEED = "may-exceed"  # ind_assgn = M — non-participating, may balance-bill to the limiting charge
OPTED_OUT = "opted-out"  # private contract in force — Medicare pays nothing
UNKNOWN_ASSIGNMENT = "unknown"


@dataclass
class AssignmentResult:
    status: str  # ACCEPTS | MAY_EXCEED | OPTED_OUT | UNKNOWN_ASSIGNMENT
    detail: str
    #: False when the opt-out file could not be reached. Opt-out is the only decisive NEGATIVE here,
    #: so a caller must be able to discount an ACCEPTS that was never checked against it.
    optout_checked: bool = True
    flags: dict = field(default_factory=dict)
    source_date: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "optout_checked": self.optout_checked,
            "flags": self.flags,
            "source_date": self.source_date,
        }


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


def _as_date(v) -> date | None:
    """Parse a CMS date string (MM/DD/YYYY, or ISO when a caller passes one). None if unreadable."""
    s = str(v or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _coerce_today(v) -> date:
    if isinstance(v, date):
        return v
    return _as_date(v) or date.today()


def _optout_row(n: str, client: CachedClient) -> dict | None:
    """This NPI's row in the current Opt Out Affidavits file, keyed by lower-cased header, or None.

    Lets transport errors propagate: the caller must be able to tell "checked, not opted out" from
    "never checked", because only the former licenses an in-network answer.
    """
    meta = client.get_json(_OPTOUT_META, headers={"accept": "application/vnd.api+json"})
    ds = ((meta.get("data") or [{}])[0] or {}).get("id")
    if not ds:
        raise RuntimeError("could not resolve the opt-out dataset id")
    url = _OPTOUT_DATA.format(ds=ds) + "?" + urlencode({"keyword": n, "size": 20, "offset": 0})
    data = client.get_json(url, headers={"accept": "application/json", "user-agent": "network-probe/1.0"})
    headers = (data.get("meta") or {}).get("headers") or []
    idx = {h.strip().lower(): i for i, h in enumerate(headers)}
    npi_i = idx.get("npi")
    if npi_i is None:
        return None
    # the data-viewer is a keyword search, so it also returns rows for OTHER people whose record
    # happens to contain the digits — only an NPI-exact row is this provider.
    for r in data.get("data") or []:
        if len(r) > npi_i and _norm_npi(r[npi_i]) == n:
            return {h: (r[i] if i < len(r) else None) for h, i in idx.items()}
    return None


def _optout_in_force(row: dict, today: date) -> bool:
    """Is the affidavit CURRENT? The file retains historical opt-outs, so bare presence is not the
    question — a provider whose opt-out ended years ago bills Medicare again today, and reading
    presence alone would manufacture a false OON. Unreadable dates count as in force: withholding
    an in-network answer is the safe direction, asserting one over a private contract is not."""
    eff = _as_date(row.get("optout effective date"))
    end = _as_date(row.get("optout end date"))
    if eff is None or end is None:
        return True
    return eff <= today <= end


def _dac_rows(n: str, client: CachedClient) -> list[dict]:
    """Rows for this NPI in the Care Compare National Downloadable File (one per practice location)."""
    url = _DAC_QUERY.format(ds=_DAC_DATASET) + "?" + urlencode(
        {
            "conditions[0][property]": "npi",
            "conditions[0][value]": n,
            "conditions[0][operator]": "=",
            "limit": 50,
        }
    )
    data = client.get_json(url, headers={"accept": "application/json"})
    return [r for r in (data.get("results") or []) if _norm_npi(r.get("npi")) == n]


def assignment_status(npi, client: CachedClient | None = None, today=None) -> AssignmentResult:
    """Does this provider accept Medicare assignment — and is there a private contract in the way?

    This is the fact a no-network (Original Medicare / Medigap) member actually needs, and it is
    strictly stronger than PECOS enrollment. Two CMS sources, checked in this order:

      1. **Opt Out Affidavits** — decisive and checked first. A provider with a current affidavit
         treats Medicare patients under a private contract; Medicare pays nothing, so neither does
         a supplement. This outranks the assignment flag, which can still read "Y" for them.
      2. **National Downloadable File** (`ind_assgn`) — the Care Compare backing data.
         `Y` → charges the Medicare-approved amount. `M` → non-participating: may balance-bill up
         to the 115% limiting charge, which only Medigap Plan F/G cover.

    Never returns a negative it cannot back up: the flag has **no "N" value** (verified live: Y and
    M only), so a provider missing from the file is UNKNOWN, not "does not accept assignment".
    """
    n = _norm_npi(npi)
    if len(n) != 10:
        return AssignmentResult(UNKNOWN_ASSIGNMENT, "no valid NPI to check", optout_checked=False)
    client = client or CachedClient()

    optout_checked, optout_note = True, ""
    try:
        row = _optout_row(n, client)
    except Exception as exc:  # noqa: BLE001 — unreachable ≠ not opted out; record it and carry on
        optout_checked, optout_note = False, f" (the CMS opt-out file was unreachable: {type(exc).__name__})"
    else:
        if row is not None and _optout_in_force(row, _coerce_today(today)):
            eff = row.get("optout effective date") or "?"
            end = row.get("optout end date") or "?"
            return AssignmentResult(
                OPTED_OUT,
                f"NPI {n} has a Medicare opt-out affidavit in force ({eff} – {end}): this provider "
                f"treats Medicare patients under a private contract, so Medicare pays nothing and "
                f"a Medicare Supplement pays nothing either.",
                flags={"optout_effective": eff, "optout_end": end},
            )

    try:
        rows = _dac_rows(n, client)
    except Exception as exc:  # noqa: BLE001
        return AssignmentResult(
            UNKNOWN_ASSIGNMENT,
            f"Medicare assignment lookup failed ({type(exc).__name__}){optout_note}",
            optout_checked=optout_checked,
        )
    if not rows:
        return AssignmentResult(
            UNKNOWN_ASSIGNMENT,
            f"NPI {n} is not listed in the CMS National Downloadable File, which covers clinicians "
            f"billing the Physician Fee Schedule. The file carries no 'does not accept' value, so "
            f"absence says nothing about assignment either way.{optout_note}",
            optout_checked=optout_checked,
        )

    vals = {str(r.get("ind_assgn") or "").strip().upper() for r in rows}
    flags = {"ind_assgn": sorted(v for v in vals if v), "rows": len(rows)}
    # One NPI has a row per practice location. If ANY of them says the clinician may exceed the
    # approved amount, the member can be balance-billed there — so M outranks Y, never the reverse.
    if "M" in vals:
        return AssignmentResult(
            MAY_EXCEED,
            f"NPI {n} is a NON-PARTICIPATING Medicare provider (ind_assgn=M): may bill up to the "
            f"115% limiting charge, and those Part B excess charges are covered only by Medigap "
            f"Plan F or G.{optout_note}",
            optout_checked=optout_checked,
            flags=flags,
        )
    if "Y" in vals:
        return AssignmentResult(
            ACCEPTS,
            f"NPI {n} accepts Medicare assignment (ind_assgn=Y) — charges the Medicare-approved "
            f"amount, which is what medicare.gov Care Compare shows.{optout_note}",
            optout_checked=optout_checked,
            flags=flags,
        )
    return AssignmentResult(
        UNKNOWN_ASSIGNMENT,
        f"NPI {n} is listed but carries no readable assignment flag.{optout_note}",
        optout_checked=optout_checked,
        flags=flags,
    )


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


def _medicaid_il(n: str, client: CachedClient) -> EnrollmentResult:
    """Illinois HFS — the state Medicaid agency's own individual-provider directory (JSON)."""
    body = json.dumps(
        {
            "requiresCounts": True,
            "search": [{"fields": ["NPI", "TheName"], "operator": "contains", "key": n, "ignoreCase": True}],
            "skip": 0,
            "take": 50,
        }
    )
    data = client.post_json(
        _IL_HFS, content=body, headers={"content-type": "application/json; charset=UTF-8", "accept": "application/json"}
    )
    # HFS matches with `contains`, so confirm the NPI ourselves rather than trusting the row count.
    match = [r for r in (data.get("result") or []) if _norm_npi(r.get("NPI")) == n]
    if match:
        name = str(match[0].get("TheName") or "").strip()
        return EnrollmentResult(
            True, "medicaid-IL",
            f"NPI {n} is in the Illinois HFS enrolled-provider directory" + (f" ({name})." if name else "."),
        )
    return EnrollmentResult(
        False, "medicaid-IL", f"NPI {n} is not in the Illinois HFS enrolled-provider directory."
    )


def _npi_from_fhir(resource: dict) -> str:
    """The us-npi identifier off a FHIR Practitioner/Organization. Practitioners also carry state
    licence numbers, so the identifier SYSTEM has to be checked — not just any identifier value."""
    for ident in resource.get("identifier") or []:
        system = str(ident.get("system") or "").lower()
        if system.endswith("us-npi") or "us-npi" in system:
            return _norm_npi(ident.get("value"))
    return ""


def _medicaid_me(n: str, client: CachedClient) -> EnrollmentResult:
    """Maine — the MaineCare provider directory, an anonymous-read FHIR R4 server. Presence IS
    enrollment here (the directory holds only active MaineCare providers)."""
    for kind in ("Practitioner", "Organization"):  # type-1 then type-2 (organisational) NPIs
        url = f"{_ME_FHIR}/{kind}?" + urlencode({"identifier": n, "_count": 500})
        data = client.get_json(url, headers={"accept": "application/fhir+json"})
        # A FHIR server reports errors as a 200 OperationOutcome; reading that as "no match" would
        # turn every server-side error into a confident OON.
        if str(data.get("resourceType") or "") == "OperationOutcome":
            return EnrollmentResult(
                None, "medicaid-ME", f"the MaineCare FHIR directory returned an OperationOutcome for NPI {n}."
            )
        for entry in data.get("entry") or []:
            resource = entry.get("resource") or {}
            if _npi_from_fhir(resource) == n:
                return EnrollmentResult(True, "medicaid-ME", f"NPI {n} is in the MaineCare provider directory.")
    return EnrollmentResult(False, "medicaid-ME", f"NPI {n} is not in the MaineCare provider directory.")


# state -> live-API lookup fn. A state earns a place here only when its source is BOTH
# identifier-grade (the NPI comes back in the response, so the match can be confirmed client-side)
# and authoritative for the state (its own enrolled-provider file, not one MCO's network). Anything
# less cannot carry the decisive negative that `enrollment_negative` builds on top of it.
_MEDICAID_STATE_APIS = {"NY": _medicaid_ny, "IL": _medicaid_il, "ME": _medicaid_me}

# States whose source was evaluated and deliberately NOT wired, with the reason. Recorded so the
# next person does not re-derive it, and so an unwired state gives a real answer instead of a shrug.
# (Ported from the screening project's medicaid_sources.csv + its per-state scrapers.)
_MEDICAID_UNWIRED = {
    "KS": "the KMAP directory answers, but its rows come back with NPI: null — the match cannot be confirmed",
    "TX": "the TMHP Online Provider Lookup returns HTML with the NPI encoded — the match cannot be confirmed",
    "MD": "eMedicaid is NPI-exact but HTML-scraped and needs TLS verification disabled",
    "WI": "the only source is a Centene MCO network directory, not the state's enrolled-provider file",
    "IA": "the only sources are MCO network directories (Centene/Molina), not the state's enrolled-provider file",
    "KY": "the Molina Sapphire API cannot search by NPI — it needs the provider's name, which we do not carry",
    "CT": "the HealthX API cannot search by NPI — it needs the provider's name, which we do not carry",
    "LA": "the myplan.healthy.la.gov API cannot search by NPI — it needs the provider's name",
    "UT": "the portal is name-only, returns no NPI, and serves a broken certificate chain",
    "NC": "the directory API requires a reCAPTCHA v2 token",
    "HI": "the directory is behind a Cloudflare challenge",
    "GA": "the portal needs a browser postback (DNN VIEWSTATE) per row",
    "AZ": "the CNSI portal is name-only and returns no NPI",
    "NM": "the Salesforce Aura portal returns no NPI and needs a separate address lookup",
    "MA": "the portal requires a city/ZIP and a CSRF token, and is an HTML grid",
    "AR": "the portal requires city, state and ZIP alongside the NPI",
    "TN": "the portal requires a real browser",
    "NE": "the source is a 6,000-page PDF that has to be bulk-imported, not queried",
    "SC": "the source is a bulk CSV export that has to be imported, not queried",
    "SD": "the source is a PowerBI export that has to be imported, not queried",
}


def medicaid_enrollment(npi, state, client: CachedClient | None = None) -> EnrollmentResult:
    """Is this NPI enrolled in the given state's Medicaid? Per-state; only wired states can return
    True/False — every other state returns None (undetermined), so we never assert an OON we cannot
    back up. Unwired states report WHY they are unwired rather than a bare "not supported"."""
    n = _norm_npi(npi)
    st = (state or "").strip().upper()
    if len(n) != 10 or not st:
        return EnrollmentResult(None, f"medicaid-{st or '?'}", "no valid NPI/state to check")
    fn = _MEDICAID_STATE_APIS.get(st)
    if fn is None:
        why = _MEDICAID_UNWIRED.get(st)
        return EnrollmentResult(
            None, f"medicaid-{st}",
            f"{st} Medicaid enrollment is not wired: {why}." if why
            else f"no Medicaid enrollment source wired for {st} yet",
        )
    client = client or CachedClient()
    try:
        return fn(n, client)
    except Exception as exc:  # noqa: BLE001
        return EnrollmentResult(None, f"medicaid-{st}", f"{st} Medicaid lookup failed ({type(exc).__name__})")
