"""HTTP API + web UI over the network-status probe.

Run:
    uvicorn network_probe.api:app --reload
    # or
    python -m network_probe.api

Endpoints:
    GET  /              -> the web UI (single self-contained page)
    GET  /api/payers    -> available payers + the fields each needs + examples
    POST /api/check     -> run a verdict for one provider/plan

The API is a thin shell over network_probe.service.check_network — the verdict logic
stays in the adapters, single source of truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import io

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .models import ProviderQuery
from .report_ingest import parse_report, report_to_query
from .service import check_network

_STATIC = Path(__file__).parent / "static"

# Payer catalogue that drives the UI (select options, field hints, example fills).
PAYERS = [
    {
        "key": "oscar",
        "label": "Oscar Health — FL marketplace (live scrape, open API)",
        "needs": ["plan", "last_name", "state", "zip"],
        "example": {"npi": "1679766943", "last_name": "Herron",
                    "plan": "BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER",
                    "state": "FL", "zip": "33409"},
        "example_label": "Kyle A Herron · Silver Simple PCP Saver",
    },
    {
        "key": "devoted",
        "label": "Devoted Health — Medicare Advantage (live, Algolia)",
        "needs": ["plan", "npi", "state"],
        "example": {"npi": "1679766943", "last_name": "Herron", "plan": "HMO",
                    "state": "FL", "zip": "33409"},
        "example_label": "Kyle A Herron · HMO",
    },
    {
        "key": "humana-fhir",
        "label": "Humana — FHIR Provider Directory (compliant CMS API)",
        "needs": ["plan", "npi"],
        "example": {"npi": "1679766943", "last_name": "Herron", "plan": "Medicare PPO"},
        "example_label": "Kyle A Herron · Medicare PPO",
    },
    {
        "key": "cigna-fhir",
        "label": "Cigna — FHIR Provider Directory (compliant CMS API)",
        "needs": ["plan", "npi"],
        "example": {"npi": "", "last_name": "", "plan": ""},
        "example_label": "",
    },
    {
        "key": "fhir",
        "label": "Generic FHIR PDEX (set a base URL)",
        "needs": ["plan", "npi", "base_url"],
        "example": {"npi": "1679766943", "plan": "Medicare PPO",
                    "base_url": "https://fhir.humana.com/api"},
        "example_label": "Kyle A Herron · fhir.humana.com",
    },
    {
        "key": "uhc",
        "label": "United Healthcare — public FHIR Provider Directory (Optum, no login)",
        "needs": ["plan", "npi"],
        "example": {"npi": "1972603934", "last_name": "Fradkin", "plan": ""},
        "example_label": "Kevin Fradkin",
    },
]

# Test cases extracted from the pVerify 271 eligibility reports in ./test-data.
# Each verifies the *rendering provider* against the *subscriber's plan network*.
SAMPLES = [
    {"label": "Ochoa, Clemencia · Oscar · Dr Herron",
     "payer": "oscar", "plan": "BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER",
     "npi": "1679766943", "last_name": "Herron", "first_name": "Kyle", "state": "FL", "zip": "33409"},
    {"label": "Craig, Duana · Devoted TX HMO · Dr George",
     "payer": "devoted", "plan": "HMO", "npi": "1720209885", "last_name": "George",
     "first_name": "Jojy", "state": "TX", "zip": ""},
    {"label": "Rodriguez, Aurelia · Devoted CO PPO · Dr Li",
     "payer": "devoted", "plan": "PPO", "npi": "1629339312", "last_name": "Li",
     "first_name": "Jing", "state": "CO", "zip": ""},
    {"label": "Franz, Robert · Humana Medicare PPO · Dr Friedman",
     "payer": "humana-fhir", "plan": "Medicare PPO", "npi": "1336160274",
     "last_name": "Friedman", "first_name": "Jefffrey", "state": "", "zip": ""},
    {"label": "Schindler, Brian · Humana Medicare PPO · Dr Leschak",
     "payer": "humana-fhir", "plan": "Medicare PPO", "npi": "1760430029",
     "last_name": "Leschak", "first_name": "Stephen", "state": "", "zip": ""},
    {"label": "Benschneider, Todd · Cigna · Dr Kiang",
     "payer": "cigna-fhir", "plan": "", "npi": "1184610453", "last_name": "Kiang",
     "first_name": "William", "state": "FL", "zip": "33647", "tin": "463812940"},
    {"label": "Salman, Sobia · UnitedHealthcare · Dr Fradkin",
     "payer": "uhc", "plan": "Bronze Essential", "npi": "1972603934", "last_name": "Fradkin",
     "first_name": "Kevin", "state": "TX", "zip": "", "tin": "933510922"},
]

# Independently-confirmed truth (Availity / payer portal / phone) for the demo cases, keyed by
# (payer, npi). Surfaced as `ground_truth` so the UI can show "real vs what we gave".
GROUND_TRUTH: dict[tuple[str, str], dict] = {
    ("oscar", "1679766943"): {"truth": "OUT_OF_NETWORK", "source": "Availity / payer portal",
                              "note": "Absent from Oscar network 066."},
    ("devoted", "1629339312"): {"truth": "OUT_OF_NETWORK", "source": "Availity / payer portal",
                                "note": "Devoted directory lists Dr Li as IN for CO PPO — stale."},
    ("humana-fhir", "1336160274"): {"truth": "OUT_OF_NETWORK", "source": "Availity / payer portal",
                                    "note": "Not in the queried Medicare PPO network."},
    ("cigna-fhir", "1184610453"): {"truth": "OUT_OF_NETWORK", "source": "Cigna portal (TIN-level)",
                                   "note": "Out-of-network for this patient's TIN."},
    ("uhc", "1972603934"): {"truth": "IN_NETWORK", "source": "UHC Transparency-in-Coverage MRF (TX exchange)",
                            "note": "In-network under billing TIN 933510922 (Texas UVC Medical, PLLC)."},
}

# Seeded accuracy scorecard for the 4 pVerify OON examples (see TODO-network-accuracy.md).
# Not a live re-run — documented results, with Rodriguez corrected by the golden-record override.
BENCHMARK = [
    {"case": "Ochoa · Oscar · Herron", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "high", "caught": True,
     "how": "directory absence (primary signal)"},
    {"case": "Benschneider · Cigna · Kiang", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "medium", "caught": True,
     "how": "directory absence (primary signal)"},
    {"case": "Franz · Humana · Friedman", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "medium", "caught": True,
     "how": "directory absence (primary signal)"},
    {"case": "Rodriguez · Devoted CO PPO · Li", "truth": "OUT_OF_NETWORK",
     "our_status": "OUT_OF_NETWORK", "our_confidence": "high", "caught": True,
     "how": "golden-record override (Availity); directory still lists Li as IN — stale"},
]

app = FastAPI(title="Network-Status Verification Probe", version="1.0")


class CheckRequest(BaseModel):
    payer: str
    plan: str = ""
    npi: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    tin: Optional[str] = None
    year: Optional[int] = None
    base_url: Optional[str] = None


class OverrideRequest(BaseModel):
    payer: str
    npi: str
    status: str                 # IN_NETWORK | OUT_OF_NETWORK | REVIEW
    verified_by: str
    verified_at: str            # ISO date
    network: Optional[str] = None
    plan: Optional[str] = None
    tin: Optional[str] = None
    note: str = ""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/api/payers")
def payers() -> list[dict]:
    return PAYERS


@app.get("/api/samples")
def samples() -> list[dict]:
    return SAMPLES


@app.get("/api/benchmark")
def benchmark() -> list[dict]:
    return BENCHMARK


@app.get("/api/oon")
def oon(npi: Optional[str] = None):
    """Saved out-of-network benefits (Stedi 271), prefetched into `.cache/oon_benefits.json`.
    Demo-only: no live fetch here — populate with `python -m network_probe.oon_benefits
    test-data/*.pdf`. Returns `available=False` when a member hasn't been prefetched."""
    from .oon_benefits import load_oon
    if npi is None:
        return load_oon() or {}
    entry = load_oon(npi)
    if not entry:
        return {"npi": npi, "available": False, "benefits": [], "oon_count": 0}
    return {**entry, "available": True}


# sync `def` so FastAPI runs the blocking httpx calls in a threadpool
@app.post("/api/check")
def check(req: CheckRequest):
    q = ProviderQuery(
        payer=req.payer,
        plan_hint=req.plan or "",
        npi=(req.npi or None),
        first_name=(req.first_name or None),
        last_name=(req.last_name or None),
        state=(req.state or None),
        zip_code=(req.zip or None),
        tin=(req.tin or None),
    )
    kwargs = {}
    if req.year:
        kwargs["year"] = req.year
    if req.base_url:
        kwargs["base_url"] = req.base_url
    try:
        verdict = check_network(q, **kwargs)
    except Exception as exc:  # bad payer, missing base_url, network error
        return JSONResponse(status_code=400, content={"error": str(exc)})
    gt = GROUND_TRUTH.get((req.payer, req.npi or ""))
    return {"payer": req.payer, "ground_truth": gt, **verdict.to_dict()}


@app.post("/api/check-from-report")
def check_from_report(file: UploadFile = File(...)):
    """Phase 1 — upload a pVerify 271 PDF; we parse payer/plan/provider/NPI and return the network
    verdict that fills the report's 'Provider Network: Unknown' field."""
    try:
        parsed = parse_report(io.BytesIO(file.file.read()))
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": f"could not parse report: {exc}"})
    if not parsed.get("payer_key"):
        return JSONResponse(status_code=400, content={"error": f"unmapped payer {parsed.get('payer_name')!r}"})
    if not parsed.get("npi"):
        return JSONResponse(status_code=400, content={"error": "no provider NPI found in report"})
    q = report_to_query(parsed)
    try:
        verdict = check_network(q)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return {"payer": q.payer, "parsed": parsed, **verdict.to_dict()}


@app.post("/api/override")
def add_override(req: OverrideRequest):
    """Record a human/authoritative-confirmed status (golden record). Wins over the directory."""
    from .overrides import OverrideStore, Override
    try:
        OverrideStore().add(Override(
            payer=req.payer, npi=req.npi, status=req.status, verified_by=req.verified_by,
            verified_at=req.verified_at, network=req.network, plan=req.plan, tin=req.tin, note=req.note))
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return {"ok": True}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
