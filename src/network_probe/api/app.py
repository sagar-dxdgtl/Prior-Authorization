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
from network_probe.api.quota import enforce_quota
from network_probe.api.ratelimit import RateLimitHeadersMiddleware
from network_probe.api.review import router as review_router
from network_probe.api.validation import normalize_dob, valid_npi
from network_probe.auth.deps import get_context
from network_probe.auth.routes import router as auth_router
from network_probe.core._http import CachedClient
from network_probe.core.config import get_settings
from network_probe.core.context import RequestContext
from network_probe.core.secrets_provider import get_secret
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


@app.on_event("startup")
async def _start_pdf_directory_refresh() -> None:
    """App-scheduled monthly refresh of PDF-only provider directories (e.g. Align Senior Care).
    Off by default; set ENABLE_DIRECTORY_REFRESH=1 in the deployment to enable. Tests never
    trigger the 14 MB download because the flag is unset."""
    import asyncio

    from network_probe.domain.directory_load import monthly_refresh_loop, refresh_enabled

    if refresh_enabled():
        asyncio.create_task(monthly_refresh_loop())
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
    stedi_payer_id: str | None = None


class RecheckRequest(BaseModel):
    payer: str
    stedi_payer_id: str | None = None
    npi: str | None = None
    plan: str = ""
    state: str | None = None
    zip: str | None = None
    tin: str | None = None
    base_url: str | None = None
    stedi_network_status: str = "UNKNOWN"


class PortalCaptureRequest(BaseModel):
    """A find-a-doctor walk for one provider. Provider + clinic fields ONLY — no member PHI ever
    reaches a portal (HANDOFF §7); `plan` is the network to pin, taken from the live 271."""

    payer_key: str
    npi: str
    plan: str | None = None
    provider_first_name: str | None = None
    provider_last_name: str | None = None
    state: str | None = None
    city: str | None = None
    zip: str | None = None
    tin: str | None = None
    # The verdict as it stands when the walk starts, so the portal answer can be reconciled against
    # it on completion instead of sitting beside it as decoration. All optional: with none of it the
    # capture still runs and simply reports its own finding.
    prior_network_status: str | None = None
    prior_source_url: str | None = None
    out_of_network_benefits: bool | None = None
    group_contracted: bool | None = None
    plan_oon_capability: bool | None = None
    #: The evidence the eligibility check already gathered (directory networks, roster, enrollment).
    #: Carried so the recomputed determination keeps the reading it had. Without it an INCONCLUSIVE
    #: walk silently flipped a directory-backed "likely in-network" into an out-of-network display —
    #: exactly what `reconcile_portal` promises a portal that could not answer can never do.
    prior_evidence: dict | None = None


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


@app.get("/api/payers/search")
def payers_search(
    q: str = "", state: str = "", limit: int = 20, ctx: RequestContext = Depends(get_context)
) -> list[dict]:
    """Searchable payer options for the eligibility UI: curated roster first, then the Stedi live
    payer directory (deduped) for the long tail. Auth-gated so unauthenticated callers can't drive
    Stedi lookups. `state` is the member's state from the form; it biases roster ranking toward that
    market so a verbose client name ("UHC AARP Medicare Advantage") lands on the right-state row."""
    from network_probe.payers.search import load_roster_rows, search_roster, search_stedi

    roster = search_roster(load_roster_rows(), q, limit, state=state or None)
    # Roster-first: if the curated roster answers, return it immediately — never block the typeahead
    # on the (6-10s) live Stedi directory call. Stedi is only for the long tail: payers NOT in the
    # roster at all. Cap it with a short timeout so an unknown-payer search can't hang the UI.
    if roster:
        return roster
    api_key = get_settings().stedi_api_key or get_secret("STEDI_API_KEY")
    if not api_key:
        return roster
    client = CachedClient(cache_dir=None, delay_seconds=0.0, timeout=5.0)
    return search_stedi(client, api_key, q, limit)


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
def eligibility(req: CheckRequest, ctx: RequestContext = Depends(enforce_quota)):
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
    result = check_eligibility(
        q, base_url=(req.base_url or None), tenant_id=ctx.tenant_id,
        stedi_payer_id=(req.stedi_payer_id or None),
    )
    write_audit(ctx, "eligibility", q, result, rid)
    return {"payer": req.payer, "request_id": rid, **result.to_dict()}


@app.post("/api/eligibility/recheck-network")
def recheck_network_route(req: RecheckRequest, ctx: RequestContext = Depends(enforce_quota)):
    """Re-run the network/directory leg only, for a plan the user picked from the 271's candidates.
    No 270 is sent; the caller passes back the 271's own network status to preserve the merge."""
    from network_probe.domain.eligibility import recheck_network

    if req.base_url:
        try:
            assert_safe_url(req.base_url)
        except ValueError as e:
            raise HTTPException(status_code=400, detail={"message": str(e)})
    if req.npi and not valid_npi(req.npi):
        raise HTTPException(status_code=400, detail={"message": "invalid NPI"})
    q = ProviderQuery(
        payer=req.payer, plan_hint=req.plan or "", npi=req.npi or None,
        state=req.state or None, zip_code=req.zip or None, tin=req.tin or None,
    )
    try:
        stedi_status = NetworkStatus(req.stedi_network_status)
    except ValueError:
        stedi_status = NetworkStatus.UNKNOWN
    return recheck_network(q, stedi_status, base_url=(req.base_url or None), tenant_id=ctx.tenant_id)


# sync `def` so FastAPI runs the blocking httpx calls in a threadpool
@app.post("/api/check")
def check(req: CheckRequest, ctx: RequestContext = Depends(enforce_quota)):
    # /api/check is network-status-only (no Stedi/subscriber concept) and is exclusively called by
    # the static UI's provider/plan form (index.html posts the physician's name here, e.g. "Herron") --
    # so first_name/last_name are the PROVIDER's name here, unlike /api/eligibility's subscriber fields.
    q = ProviderQuery(
        payer=req.payer,
        plan_hint=req.plan or "",
        npi=(req.npi or None),
        provider_first_name=(req.first_name or None),
        provider_last_name=(req.last_name or None),
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
def check_from_report(file: UploadFile = File(...), ctx: RequestContext = Depends(enforce_quota)):
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


@app.post("/api/portal/capture")
def portal_capture_start(req: PortalCaptureRequest, ctx: RequestContext = Depends(enforce_quota)):
    """Start a find-a-doctor walk for one provider and return immediately with a job id.

    A walk takes 40-210s live, so it cannot block the eligibility response. The caller renders the
    fast verdict (directory + TiC + credentialing + PBP) at once, then polls
    /api/portal/capture/{job_id} for the portal proof.

    `plan` should be the network pinned from the member's live 271: a portal verdict is only valid
    for the network it was searched in, and with no plan the drivers correctly return UNKNOWN
    rather than answer from an un-pinned directory.
    """
    if not valid_npi(req.npi):
        raise HTTPException(status_code=400, detail={"message": "invalid NPI"})
    from network_probe.portal.capture import driver_for
    from network_probe.portal.jobs import default_capture_jobs
    from network_probe.portal.models import PortalQuery

    if driver_for(req.payer_key) is None:
        raise HTTPException(
            status_code=404,
            detail={"message": f"no portal driver for payer {req.payer_key!r}"},
        )
    # The provider's name comes from NPPES (a public registry keyed by NPI) when the caller does not
    # supply it — NEVER from the member fields on the eligibility form. Portals are searched by
    # provider name + clinic ZIP, and a member name reaching a payer's public search box is exactly
    # what HANDOFF §7 forbids. Resolving it server-side means a caller cannot get this wrong.
    first, last = req.provider_first_name or None, req.provider_last_name or None
    if not last:
        from network_probe.core._http import CachedClient
        from network_probe.domain.report_ingest import _nppes_name

        first, last = _nppes_name(req.npi, CachedClient())

    q = PortalQuery(
        payer_key=req.payer_key,
        npi=req.npi,
        provider_first_name=first,
        provider_last_name=last,
        plan=req.plan or None,
        state=req.state or None,
        city=req.city or None,
        zip_code=req.zip or None,
        tin=req.tin or None,
    )
    prior = {
        "network_status": req.prior_network_status,
        "source_url": req.prior_source_url,
        "out_of_network_benefits": req.out_of_network_benefits,
        "group_contracted": req.group_contracted,
        "plan_oon_capability": req.plan_oon_capability,
        "evidence": req.prior_evidence,
    }
    job_id = default_capture_jobs().submit(q, prior=prior if req.prior_network_status else None)
    log.info("portal capture %s queued for %s/%s", job_id, req.payer_key, req.npi)
    return {"job_id": job_id, "status": "queued", "poll": f"/api/portal/capture/{job_id}"}


@app.get("/api/portal/capture/{job_id}")
def portal_capture_status(job_id: str, ctx: RequestContext = Depends(get_context)):
    """Poll a capture. `status` is queued | running | done | error; the portal verdict and the
    screenshot filename appear once it is done."""
    from network_probe.portal.jobs import default_capture_jobs

    job = default_capture_jobs().get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"message": "unknown capture job"})
    body = job.to_dict()
    if job.status == "done" and job.capture is not None and job.prior:
        body["reconciled"] = _reconcile_capture(job)
    return body


def _portal_searched_without_listing(capture) -> bool:
    """Did the walk actually reach the payer's directory and come back without our provider?

    Deliberately narrow. A BLOCKED walk, a shell that never hydrated, or a driver that fell over
    proves nothing about the provider and must not suppress anything — the signal is only real when
    the portal WAS searched. A decisive IN/OUT is excluded because `reconcile_portal` has already
    acted on it; this exists for the UNKNOWN walk, which changes the verdict not at all and yet is
    still the member-facing directory declining to list them.
    """
    from network_probe.portal.models import PortalStatus

    if getattr(capture, "status", None) != PortalStatus.UNKNOWN:
        return False
    # `result_count` is set only when a result set was actually read (0 included: "searched, empty").
    # None means the walk never got far enough to search.
    return getattr(capture, "result_count", None) is not None


def _reconcile_capture(job) -> dict:
    """Fold the finished capture into the verdict that stood when the walk started.

    The portal outranks a public directory read but never contract evidence — a credentialing or
    TiC disagreement becomes REVIEW rather than a silent flip. See domain/portal_reconcile.
    """
    from network_probe.domain.determination import final_determination
    from network_probe.domain.models import NetworkStatus
    from network_probe.domain.portal_reconcile import reconcile_portal

    prior = job.prior or {}
    try:
        before = NetworkStatus(prior.get("network_status"))
    except ValueError:
        before = NetworkStatus.UNKNOWN

    after, signal = reconcile_portal(
        before,
        job.capture.status,
        source_url=prior.get("source_url"),
        portal_name=job.capture.portal_name,
    )
    # The evidence the eligibility check gathered, PLUS what this walk itself observed. Recomputed
    # rather than copied because `after` may have moved — but from the same evidence, or an
    # inconclusive walk would erase a directory finding it never contradicted.
    evidence = dict(prior.get("evidence") or {})
    if _portal_searched_without_listing(job.capture):
        # Not enough to call out-of-network (`after` is unchanged and `code` stays UNKNOWN), but it
        # must stop a public-directory read from displaying as in-network. The member-facing portal
        # is the accuracy check on that directory, so it cannot be out-voted by it.
        evidence["portal_absent"] = True
    determination = final_determination(
        after,
        prior.get("out_of_network_benefits"),
        group_contracted=prior.get("group_contracted"),
        plan_oon_capability=prior.get("plan_oon_capability"),
        evidence=evidence,
    )
    return {
        "network_status_before": before.value,
        "network_status_after": after.value,
        "changed": after != before,
        "signal": signal,
        "determination": determination.to_dict(),
    }


@app.get("/api/portal/screenshot/{name}")
def portal_screenshot(name: str, ctx: RequestContext = Depends(get_context)):
    """Serve one capture screenshot by filename, to authenticated callers only.

    Two guards, because the filename is the only input:
      * auth, matching the sibling capture routes. Screenshot names embed the NPI and a
        timestamp, and NPIs are public — so without auth the set is effectively enumerable, and
        which providers a clinic is checking is not something to hand out.
      * path containment: only a plain .png basename resolving inside LIVE_SHOT_DIR is served,
        so a crafted name cannot walk out of it. Deliberately NOT a mounted static directory.

    The names stay descriptive (driver-npi-label-timestamp) rather than random: they are the
    link between `portal_captures.screenshot` and the file, and a reviewer reading the audit
    table needs to find the proof. Auth is the access control; the name is a locator, not a
    secret.
    """
    from fastapi.responses import FileResponse

    from network_probe.portal.capture import LIVE_SHOT_DIR

    if "/" in name or "\\" in name or not name.endswith(".png"):
        raise HTTPException(status_code=400, detail={"message": "bad screenshot name"})
    path = (LIVE_SHOT_DIR / name).resolve()
    if not str(path).startswith(str(Path(LIVE_SHOT_DIR).resolve())) or not path.is_file():
        raise HTTPException(status_code=404, detail={"message": "screenshot not found"})
    return FileResponse(path, media_type="image/png")


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
