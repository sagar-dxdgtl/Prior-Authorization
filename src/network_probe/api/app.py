"""HTTP API + web UI over the network-status probe.

Run:
    uvicorn network_probe.api:app --reload
    # or
    python -m network_probe.api

Endpoints:
    GET  /              -> the web UI (single self-contained page)
    GET  /api/payers    -> available payers + the fields each needs + examples
    POST /api/check     -> run a verdict for one provider/plan

The API is a thin shell over network_probe.domain.service.check_network — the verdict logic
stays in the adapters, single source of truth.
"""

from __future__ import annotations

import io
import logging
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

from network_probe.api.admin import router as admin_router
from network_probe.api.netutil import assert_safe_url
from network_probe.api.ratelimit import RateLimitHeadersMiddleware
from network_probe.api.review import router as review_router
from network_probe.api.validation import normalize_dob, valid_npi
from network_probe.auth.deps import get_context
from network_probe.auth.routes import router as auth_router
from network_probe.core.config import get_settings
from network_probe.core.context import RequestContext
from network_probe.domain.audit import write_audit
from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.eligibility import check_eligibility
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.domain.report_ingest import parse_report, report_to_query
from network_probe.domain.service import check_network

log = logging.getLogger("preauth.api")

_STATIC = Path(__file__).parent / "static"

# Payer catalogue that drives the UI (select options, field hints, example fills).
PAYERS = [
    {
        "key": "oscar",
        "label": "Oscar Health — FL marketplace (live scrape, open API)",
        "needs": ["plan", "last_name", "state", "zip"],
        "example": {
            "npi": "1679766943",
            "last_name": "Herron",
            "plan": "BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER",
            "state": "FL",
            "zip": "33409",
        },
        "example_label": "Kyle A Herron · Silver Simple PCP Saver",
    },
    {
        "key": "devoted",
        "label": "Devoted Health — Medicare Advantage (live, Algolia)",
        "needs": ["plan", "npi", "state"],
        "example": {"npi": "1679766943", "last_name": "Herron", "plan": "HMO", "state": "FL", "zip": "33409"},
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
        "example": {"npi": "1679766943", "plan": "Medicare PPO", "base_url": "https://fhir.humana.com/api"},
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
    {
        "label": "Ochoa, Clemencia · Oscar · Dr Herron",
        "payer": "oscar",
        "plan": "BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER",
        "npi": "1679766943",
        "last_name": "Herron",
        "first_name": "Kyle",
        "state": "FL",
        "zip": "33409",
    },
    {
        "label": "Craig, Duana · Devoted TX HMO · Dr George",
        "payer": "devoted",
        "plan": "HMO",
        "npi": "1720209885",
        "last_name": "George",
        "first_name": "Jojy",
        "state": "TX",
        "zip": "",
    },
    {
        "label": "Rodriguez, Aurelia · Devoted CO PPO · Dr Li",
        "payer": "devoted",
        "plan": "PPO",
        "npi": "1629339312",
        "last_name": "Li",
        "first_name": "Jing",
        "state": "CO",
        "zip": "",
    },
    {
        "label": "Franz, Robert · Humana Medicare PPO · Dr Friedman",
        "payer": "humana-fhir",
        "plan": "Medicare PPO",
        "npi": "1336160274",
        "last_name": "Friedman",
        "first_name": "Jefffrey",
        "state": "",
        "zip": "",
    },
    {
        "label": "Schindler, Brian · Humana Medicare PPO · Dr Leschak",
        "payer": "humana-fhir",
        "plan": "Medicare PPO",
        "npi": "1760430029",
        "last_name": "Leschak",
        "first_name": "Stephen",
        "state": "",
        "zip": "",
    },
    {
        "label": "Benschneider, Todd · Cigna · Dr Kiang",
        "payer": "cigna-fhir",
        "plan": "",
        "npi": "1184610453",
        "last_name": "Kiang",
        "first_name": "William",
        "state": "FL",
        "zip": "33647",
        "tin": "463812940",
    },
    {
        "label": "Salman, Sobia · UnitedHealthcare · Dr Fradkin",
        "payer": "uhc",
        "plan": "Bronze Essential",
        "npi": "1972603934",
        "last_name": "Fradkin",
        "first_name": "Kevin",
        "state": "TX",
        "zip": "",
        "tin": "933510922",
    },
]

# Independently-confirmed truth (Availity / payer portal / phone) for the demo cases, keyed by
# (payer, npi). Surfaced as `ground_truth` so the UI can show "real vs what we gave".
GROUND_TRUTH: dict[tuple[str, str], dict] = {
    ("oscar", "1679766943"): {
        "truth": "OUT_OF_NETWORK",
        "source": "Availity / payer portal",
        "note": "Absent from Oscar network 066.",
    },
    ("devoted", "1629339312"): {
        "truth": "OUT_OF_NETWORK",
        "source": "Availity / payer portal",
        "note": "Devoted directory lists Dr Li as IN for CO PPO — stale.",
    },
    ("humana-fhir", "1336160274"): {
        "truth": "OUT_OF_NETWORK",
        "source": "Availity / payer portal",
        "note": "Not in the queried Medicare PPO network.",
    },
    ("cigna-fhir", "1184610453"): {
        "truth": "OUT_OF_NETWORK",
        "source": "Cigna portal (TIN-level)",
        "note": "Out-of-network for this patient's TIN.",
    },
    ("uhc", "1972603934"): {
        "truth": "IN_NETWORK",
        "source": "UHC Transparency-in-Coverage MRF (TX exchange)",
        "note": "In-network under billing TIN 933510922 (Texas UVC Medical, PLLC).",
    },
}

# Seeded accuracy scorecard for the 4 pVerify OON examples (see TODO-network-accuracy.md).
# Not a live re-run — documented results, with Rodriguez corrected by the golden-record override.
BENCHMARK = [
    {
        "case": "Ochoa · Oscar · Herron",
        "truth": "OUT_OF_NETWORK",
        "our_status": "OUT_OF_NETWORK",
        "our_confidence": "high",
        "caught": True,
        "how": "directory absence (primary signal)",
    },
    {
        "case": "Benschneider · Cigna · Kiang",
        "truth": "OUT_OF_NETWORK",
        "our_status": "OUT_OF_NETWORK",
        "our_confidence": "medium",
        "caught": True,
        "how": "directory absence (primary signal)",
    },
    {
        "case": "Franz · Humana · Friedman",
        "truth": "OUT_OF_NETWORK",
        "our_status": "OUT_OF_NETWORK",
        "our_confidence": "medium",
        "caught": True,
        "how": "directory absence (primary signal)",
    },
    {
        "case": "Rodriguez · Devoted CO PPO · Li",
        "truth": "OUT_OF_NETWORK",
        "our_status": "OUT_OF_NETWORK",
        "our_confidence": "high",
        "caught": True,
        "how": "golden-record override (Availity); directory still lists Li as IN — stale",
    },
]

app = FastAPI(title="Network-Status Verification Probe", version="1.0")


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    MAX = 12 * 1024 * 1024  # 12 MB global ceiling (report ingest enforces its own 10 MB)

    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > self.MAX:
            return JSONResponse(status_code=413, content={"message": "request body too large"})
        return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RateLimitHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(review_router)


@app.exception_handler(HTTPException)
def _http_exc(request: Request, exc: HTTPException):
    d = exc.detail
    return JSONResponse(status_code=exc.status_code, content=d if isinstance(d, dict) else {"message": str(d)})


@app.exception_handler(RequestValidationError)
def _validation_exc(request: Request, exc: RequestValidationError):
    rid = uuid.uuid4().hex[:12]
    log.info("request validation failed req=%s", rid)  # do NOT log exc (may contain PHI input)
    return JSONResponse(status_code=422, content={"message": "invalid request", "request_id": rid})


@app.exception_handler(Exception)
def _unhandled(request: Request, exc: Exception):
    rid = uuid.uuid4().hex[:12]
    log.exception("unhandled error req=%s", rid)  # full detail server-side ONLY
    return JSONResponse(status_code=500, content={"message": "internal error", "request_id": rid})


def _result_from_verdict(verdict) -> EligibilityResult:
    """Wrap a directory NetworkVerdict as an EligibilityResult for auditing the network-only routes."""
    return EligibilityResult(
        coverage_active=None,
        plan_name=None,
        group=None,
        coverage_dates={},
        network_status=verdict.status,
        benefits=[],
        pcp_required=None,
        prior_auth_required=None,
        referral_required=None,
        cob=None,
        network_verdict=verdict,
        corroboration=verdict.corroboration or [],
        source_audit={"source": "directory", "url": verdict.source_url},
    )


class CheckRequest(BaseModel):
    payer: str
    plan: str = ""
    npi: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    state: str | None = None
    zip: str | None = None
    tin: str | None = None
    year: int | None = None
    base_url: str | None = None
    member_id: str | None = None
    dob: str | None = None


class OverrideRequest(BaseModel):
    payer: str
    npi: str
    status: str  # IN_NETWORK | OUT_OF_NETWORK | REVIEW
    verified_by: str
    verified_at: str  # ISO date
    network: str | None = None
    plan: str | None = None
    tin: str | None = None
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


@app.get("/api/eligibility/ping")
def eligibility_ping(ctx: RequestContext = Depends(get_context)):
    return {"ok": True, "tenant": str(ctx.tenant_id)}


@app.post("/api/eligibility")
def eligibility(req: CheckRequest, ctx: RequestContext = Depends(get_context)):
    if req.base_url:
        try:
            assert_safe_url(req.base_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"message": str(e)})
    if req.npi and not valid_npi(req.npi):
        raise HTTPException(status_code=400, detail={"message": "invalid NPI"})
    dob = None
    if req.dob:
        try:
            dob = normalize_dob(req.dob)
        except ValueError:
            raise HTTPException(status_code=400, detail={"message": "invalid DOB"})
    q = ProviderQuery(
        payer=req.payer,
        plan_hint=req.plan or "",
        npi=req.npi or None,
        first_name=req.first_name or None,
        last_name=req.last_name or None,
        state=req.state or None,
        zip_code=req.zip or None,
        tin=req.tin or None,
        member_id=req.member_id or None,
        dob=dob,
    )
    rid = uuid.uuid4().hex[:12]
    result = check_eligibility(q, base_url=(req.base_url or None), tenant_id=ctx.tenant_id)
    write_audit(ctx, "eligibility", q, result, rid)
    return {"payer": req.payer, "request_id": rid, **result.to_dict()}


# sync `def` so FastAPI runs the blocking httpx calls in a threadpool
@app.post("/api/check")
def check(req: CheckRequest, ctx: RequestContext = Depends(get_context)):
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
        try:
            assert_safe_url(req.base_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"message": str(e)})
        kwargs["base_url"] = req.base_url
    try:
        verdict = check_network(q, **kwargs)
    except Exception as exc:  # bad payer, missing base_url, network error
        rid = uuid.uuid4().hex[:12]
        log.warning("check failed req=%s: %s", rid, exc)
        return JSONResponse(status_code=400, content={"message": "could not complete check", "request_id": rid})
    write_audit(ctx, "network", q, _result_from_verdict(verdict), uuid.uuid4().hex[:12])
    gt = GROUND_TRUTH.get((req.payer, req.npi or ""))
    return {"payer": req.payer, "ground_truth": gt, **verdict.to_dict()}


@app.post("/api/check-from-report")
def check_from_report(file: UploadFile = File(...), ctx: RequestContext = Depends(get_context)):
    """Phase 1 — upload a pVerify 271 PDF; we parse payer/plan/provider/NPI and return the network
    verdict that fills the report's 'Provider Network: Unknown' field."""
    raw = file.file.read(10 * 1024 * 1024 + 1)
    if len(raw) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"message": "file too large"})
    rid = uuid.uuid4().hex[:12]
    try:
        parsed = parse_report(io.BytesIO(raw))
    except Exception as exc:
        log.warning("report parse failed req=%s: %s", rid, exc)
        return JSONResponse(status_code=400, content={"message": "could not parse report", "request_id": rid})
    if not parsed.get("payer_key"):
        return JSONResponse(status_code=400, content={"message": "unmapped payer in report", "request_id": rid})
    if not parsed.get("npi"):
        return JSONResponse(status_code=400, content={"message": "no provider NPI found in report", "request_id": rid})
    q = report_to_query(parsed)
    try:
        verdict = check_network(q)
    except Exception as exc:
        log.warning("report check failed req=%s: %s", rid, exc)
        return JSONResponse(status_code=400, content={"message": "could not complete check", "request_id": rid})
    write_audit(ctx, "report_ingest", q, _result_from_verdict(verdict), rid)
    return {"payer": q.payer, "parsed": parsed, "request_id": rid, **verdict.to_dict()}


@app.post("/api/override")
def add_override(req: OverrideRequest, ctx: RequestContext = Depends(get_context)):
    """Record a human/authoritative-confirmed status (golden record). Wins over the directory."""
    from network_probe.domain.overrides import DbOverrideStore, Override

    rid = uuid.uuid4().hex[:12]
    try:
        DbOverrideStore(ctx.tenant_id).add(
            Override(
                payer=req.payer,
                npi=req.npi,
                status=req.status,
                verified_by=req.verified_by,
                verified_at=req.verified_at,
                network=req.network,
                plan=req.plan,
                tin=req.tin,
                note=req.note,
            )
        )
    except Exception as exc:
        log.warning("override failed req=%s: %s", rid, exc)
        return JSONResponse(status_code=400, content={"message": "could not record override", "request_id": rid})
    try:
        status = NetworkStatus(req.status)
    except ValueError:
        status = NetworkStatus.UNKNOWN
    q = ProviderQuery(payer=req.payer, plan_hint=req.plan or "", npi=req.npi, tin=req.tin)
    result = EligibilityResult(
        coverage_active=None,
        plan_name=None,
        group=None,
        coverage_dates={},
        network_status=status,
        benefits=[],
        pcp_required=None,
        prior_auth_required=None,
        referral_required=None,
        cob=None,
        network_verdict=None,
        corroboration=[],
        source_audit={"source": "override", "verified_by": req.verified_by},
    )
    write_audit(ctx, "override", q, result, rid)
    return {"ok": True}


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
