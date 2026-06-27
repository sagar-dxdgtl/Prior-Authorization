"""Phase 1 — ingest a pVerify 271 eligibility report → a ProviderQuery we can verify.

These reports carry everything we need to fill their own `Provider Network: Unknown` field:
payer, plan name + policy type, the rendering provider's NPI, and the member's state/ZIP. The PDF
text layer is interleaved in the top "QUERY CRITERIA" block but clean in "PLAN COVERAGE" and
"DETAILED RESULT" — we parse from those. The provider's name isn't reliably in the text, so we
resolve it from NPPES by NPI (needed only for Oscar's name-based search).
"""

from __future__ import annotations

import json
import re

from network_probe.core._http import CachedClient
from network_probe.models import ProviderQuery

# payer string (lowercased, substring) -> adapter key
_PAYER_MAP = [
    ("oscar", "oscar"),
    ("devoted", "devoted"),
    ("humana", "humana-fhir"),
    ("cigna", "cigna-fhir"),
    ("unitedhealthcare", "uhc"),
    ("united healthcare", "uhc"),
    ("uhc", "uhc"),
]


def _extract_text(source) -> str:
    """Accept raw text, a path, or bytes; return the PDF text."""
    if isinstance(source, str) and "\n" in source and "PAYER" in source.upper():
        return source  # already text
    from pypdf import PdfReader
    reader = PdfReader(source)
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _first(pattern: str, text: str, flags=0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def parse_report(source) -> dict:
    """Extract the fields needed to run a network check from a pVerify eligibility report."""
    text = _extract_text(source)
    low = text.lower()

    payer_key = next((k for needle, k in _PAYER_MAP if needle in low), None)
    payer_name = _first(r"PAYER\s*:\s+([A-Za-z][^\n]*)", text) or ""

    plan_name = _first(r"Plan Name\s*:\s*([^\n]+)", text)
    policy_type = _first(r"Policy Type\s*:\s*([^\n]+)", text)
    npi = _first(r"\bNPI\s*:\s*\n?\s*(\d{10})", text)
    member_id = _first(r"Member ID\s*:\s*([A-Za-z0-9]+)", text)
    dob = _first(r"Date Of Birth\s*:\s*([\d/]+)", text)
    status = _first(r"Status\s*:\s*([A-Za-z]+)", text)

    # rendering provider name: the value right before "First : <first>  Grp NPI" in the query block
    prov = re.search(r"([A-Za-z][A-Za-z'\-]+)\s*\n\s*First\s*:\s*\n\s*([A-Za-z][A-Za-z'\-]+)\s*\n\s*Grp NPI", text)
    provider_last = prov.group(1) if prov else None
    provider_first = prov.group(2) if prov else None

    state = zip_code = None
    csz = re.search(r"City-State-Zip\s*:\s*[A-Za-z .]+-([A-Z]{2})-(\d{5})", text)
    if csz:
        state, zip_code = csz.group(1), csz.group(2)

    return {
        "payer_name": payer_name, "payer_key": payer_key,
        "plan_name": plan_name, "policy_type": policy_type,
        "npi": npi, "member_id": member_id, "dob": dob, "eligibility_status": status,
        "provider_first": provider_first, "provider_last": provider_last,
        "state": state, "zip": zip_code,
    }


def _nppes_name(npi: str, client: CachedClient) -> tuple[str | None, str | None]:
    """(first, last) from NPPES by NPI, or (None, None) if unavailable."""
    try:
        data = client.post_json(
            "https://npiregistry.cms.hhs.gov/RegistryBack/npiDetails",
            content=json.dumps({"number": npi, "skip": 0, "exactMatch": False}),
            headers={"content-type": "application/json",
                     "origin": "https://npiregistry.cms.hhs.gov",
                     "referer": "https://npiregistry.cms.hhs.gov/search"},
        )
        b = data.get("basic") or {}
        return (b.get("firstName"), b.get("lastName"))
    except Exception:
        return (None, None)


def report_to_query(parsed: dict, client: CachedClient | None = None) -> ProviderQuery:
    """Turn parsed fields into a ProviderQuery. Plan hint = the report's plan name (our alias map
    and the adapters resolve metal/HMO/PPO from it). Resolves the provider name from NPPES."""
    first, last = parsed.get("provider_first"), parsed.get("provider_last")
    if not last and parsed.get("npi"):  # fall back to NPPES only if the report didn't yield a name
        first, last = _nppes_name(parsed["npi"], client or CachedClient())
    return ProviderQuery(
        payer=parsed.get("payer_key") or parsed.get("payer_name") or "",
        plan_hint=parsed.get("plan_name") or parsed.get("policy_type") or "",
        npi=parsed.get("npi"),
        first_name=first,
        last_name=last,
        state=parsed.get("state"),
        zip_code=parsed.get("zip"),
        member_id=parsed.get("member_id"),
        dob=parsed.get("dob"),
    )
