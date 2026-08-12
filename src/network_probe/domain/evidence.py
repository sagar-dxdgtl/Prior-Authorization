"""Evidence panel — what EACH source independently says, for side-by-side display.

The four provider-network / coverage signals answer different questions and disagree in known
ways. This assembles them so the UI can show the comparison, then the calculated determination:

  Stedi 271        coverage + plan-level OON tier   (NOT provider-specific network — always UNKNOWN there)
  Credentialing    provider network (clinic contract; all lines; the decider when present)
  TiC MRF          provider network (commercial only; presence=IN, absence=inconclusive)
  Payer directory  provider network (public, unreliable; run live, best-effort)

Each entry: {source, answers, status, tone, detail}. The final determination stays on the result.
"""

from __future__ import annotations

from network_probe.domain.line_of_business import is_commercial, line_of_business
from network_probe.domain.models import NetworkStatus
from network_probe.domain.tic_network import tic_network_status, tic_tin_in_roster


def _tone(status: str) -> str:
    s = (status or "").upper()
    if s in ("IN_NETWORK", "ACTIVE"):
        return "success"
    if s in ("OUT_OF_NETWORK", "INACTIVE"):
        return "danger"
    if s == "REVIEW":
        return "warning"
    return "neutral"  # UNKNOWN / N/A / NO_RECORD / NOT_FOUND / NOT_RUN / UNAVAILABLE


def _stedi_source(result) -> dict:
    active = result.coverage_active
    status = "ACTIVE" if active is True else ("INACTIVE" if active is False else "UNKNOWN")
    oonb = result.out_of_network_benefits
    oon_str = "yes" if oonb is True else ("no" if oonb is False else "undetermined")
    # When coverage is unknown, say WHY (payer couldn't find the member, or the payer isn't mapped) —
    # so a reviewer sees "verify member ID" / "map the payer" instead of a bare "unknown".
    audit = result.source_audit or {}
    reason = audit.get("error_note") or (audit.get("note") if status == "UNKNOWN" else None)
    why = f" Reason: {str(reason).rstrip().rstrip('.')}." if (status == "UNKNOWN" and reason) else ""
    # SHOW THE PLAN WE ACTUALLY HAVE. This read `plan_name` alone, so a plan the app had already
    # resolved rendered as "plan '—'" — which invites the reader to conclude the plan is unknown
    # when it is not. Row 9 of the 8-12-26 sheet is the case: the payer named no plan (it rejected
    # the identity), but the CMS contract-PBP-segment on the form pinned H0354-027-000, and the
    # portal check ran on it. Where the plan did NOT come from the payer, say so rather than let a
    # typed value borrow the authority of a 271.
    plan = result.plan_name or getattr(result, "selected_plan", None)
    pin_src = getattr(result, "plan_pin_source", None)
    plan_txt = f"'{plan}'" if plan else "'—'"
    if plan and not result.plan_name and pin_src:
        plan_txt = f"{plan_txt} (from the {pin_src})"
    return {
        "source": "Stedi 271 (eligibility)",
        "answers": "coverage + plan tier",
        "status": status,
        "tone": _tone(status),
        "detail": (
            f"Coverage {status.lower()}; plan {plan_txt}. Plan-level out-of-network "
            f"benefits: {oon_str}. A 271 gives plan-tier only — provider-specific network is "
            f"UNKNOWN here."
            + why
        ),
    }


def _credentialing_source(q, credentialing) -> dict:
    rec = credentialing.lookup(q.payer, q.npi, q.tin, plan=q.plan_hint) if credentialing else None
    if rec is None:
        return {
            "source": "Credentialing matrix",
            "answers": "provider network",
            "status": "NO_RECORD",
            "tone": "neutral",
            "detail": "No clinic credentialing record for this (payer, NPI, billing TIN).",
        }
    st = "IN_NETWORK" if rec.in_network else "OUT_OF_NETWORK"
    return {
        "source": "Credentialing matrix",
        "answers": "provider network",
        "status": st,
        "tone": _tone(st),
        "detail": f"Clinic contract: {('in' if rec.in_network else 'out of')}-network"
                  f"{f' (plan {rec.plan})' if rec.plan else ''}{f', per {rec.source}' if rec.source else ''}.",
    }


def _tic_source(q, result, benefit_type, crosswalk) -> dict:
    plan = q.plan_hint or result.plan_name
    if not is_commercial(plan, benefit_type):
        lob = line_of_business(plan, benefit_type)
        return {
            "source": "TiC MRF",
            "answers": "provider network",
            "status": "N/A",
            "tone": "neutral",
            "detail": f"Transparency-in-Coverage does not cover this line ({lob}) — Medicare Advantage, "
                      "Medicaid and Dual are federally exempt, so no MRF exists.",
        }
    status, known = tic_network_status(q.payer, q.npi, q.tin, crosswalk=crosswalk)
    if status == NetworkStatus.IN_NETWORK:
        return {
            "source": "TiC MRF",
            "answers": "provider network",
            "status": "IN_NETWORK",
            "tone": "success",
            "detail": f"Billing TIN {q.tin} found in the payer's live in-network MRF.",
        }
    # The specific NPI isn't in-network in the MRF. Is the billing TIN present under OTHER NPIs
    # (group contracted, physician-specific gap → Physician OON)? This row is specifically the
    # TiC/MRF signal, so it reads the MRF stores only — never credentialing.
    group, others = tic_tin_in_roster(q.payer, q.tin, npi=q.npi, crosswalk=crosswalk)
    if group and others:
        return {
            "source": "TiC MRF",
            "answers": "provider network",
            "status": "GROUP_ONLY",
            "tone": "warning",
            "detail": (f"Billing TIN {q.tin} IS in-network in the payer's MRF (under {len(others)} other "
                       f"NPI(s)), but this physician's NPI {q.npi} is not listed — a physician-specific "
                       f"out-of-network gap."),
        }
    return {
        "source": "TiC MRF",
        "answers": "provider network",
        "status": "NOT_FOUND",
        "tone": "neutral",
        "detail": (f"Billing TIN {q.tin} not in the payer's MRF"
                   + (f" (MRF lists other TINs for this NPI: {sorted(known)})" if known else "")
                   + ". Absence is not proof of out-of-network (MRFs are incomplete)."),
    }


def _enrollment_source(
    q, result, benefit_type, run_enrollment: bool, pecos_fn=None, medicaid_fn=None, assignment_fn=None
) -> dict:
    plan = q.plan_hint or result.plan_name
    lob = line_of_business(plan, benefit_type)
    base = {"source": "Program enrollment (PECOS/Medicaid)", "answers": "program eligibility"}
    if lob not in ("medicare", "medicaid", "dual"):
        return {**base, "status": "N/A", "tone": "neutral",
                "detail": "Commercial/federal line — Medicare/Medicaid enrollment is not the gate here."}
    from network_probe.domain.enrollment import live_enabled

    injected = any(f is not None for f in (pecos_fn, medicaid_fn, assignment_fn))
    if not run_enrollment or not (injected or live_enabled()):
        return {**base, "status": "NOT_RUN", "tone": "neutral", "detail": "Enrollment lookup not run."}
    if lob in ("medicare", "dual"):
        from network_probe.domain.enrollment import pecos_enrollment

        r = (pecos_fn or pecos_enrollment)(q.npi)
        base["source"] = "Medicare enrollment (PECOS)"
    else:
        from network_probe.domain.enrollment import medicaid_enrollment

        r = (medicaid_fn or medicaid_enrollment)(q.npi, q.state)
        base["source"] = f"Medicaid enrollment ({(q.state or '?').upper()})"

    # An opted-out provider IS Medicare-enrolled, so PECOS alone would print a green "ENROLLED"
    # beside the OUT_OF_NETWORK that `enrollment_negative` now returns for exactly that provider.
    # Both statements are true and together they are unreadable, so the affidavit takes the row.
    # Only checked where it is decisive (Medicare/dual) and where enrolment did not already settle it.
    if lob in ("medicare", "dual") and r.enrolled is not False:
        from network_probe.domain.enrollment import OPTED_OUT, assignment_status

        try:
            asg = (assignment_fn or assignment_status)(q.npi)
        except Exception:  # noqa: BLE001 — an unreachable source must not alter the row
            asg = None
        if asg is not None and asg.status == OPTED_OUT:
            return {
                **base, "source": "Medicare opt-out affidavit", "status": "OPTED_OUT", "tone": "danger",
                "detail": asg.detail + " — a Medicare Advantage plan cannot pay an opted-out provider → OON",
            }

    status = {True: "ENROLLED", False: "NOT_ENROLLED", None: "UNDETERMINED"}[r.enrolled]
    tone = {"ENROLLED": "success", "NOT_ENROLLED": "danger", "UNDETERMINED": "neutral"}[status]
    tail = " (necessary, not sufficient — enrolled ≠ in-network)" if r.enrolled is True else (
        " — cannot be in-network for this program → OON" if r.enrolled is False else "")
    return {**base, "status": status, "tone": tone, "detail": r.detail + tail}


def _money(v) -> str:
    try:
        return "$" + format(int(round(float(v))), ",")
    except (TypeError, ValueError):
        return str(v)


def _pbp_moop_str(rec) -> str:
    parts = []
    if rec.inn_moop:
        parts.append(f"in-network {_money(rec.inn_moop)}")
    if rec.comb_moop:
        parts.append(f"combined in+out {_money(rec.comb_moop)}")
    if rec.oon_moop:
        parts.append(f"out-of-network {_money(rec.oon_moop)}")
    return "; ".join(parts)


def _pbp_source(q, result, benefit_type, run_pbp: bool, store=None) -> dict:
    """The OON benefit TIER from the public CMS PBP files (Medicare-only). Answers a different
    question than the network sources: given the provider is OON, does the plan pay OON benefits, and
    what is the MOOP. Never a provider-network signal (PBP has no rosters)."""
    # Prefer the Stedi 271 plan name (carries the H-number / product type) over a coarse plan_hint.
    plan = result.selected_plan or result.plan_name or q.plan_hint
    base = {"source": "Plan benefits (CMS PBP)", "answers": "OON benefit tier"}
    if line_of_business(plan, benefit_type) not in ("medicare", "dual"):
        return {**base, "status": "N/A", "tone": "neutral",
                "detail": "CMS PBP is Medicare-only — not the out-of-network tier source for this line."}
    if not run_pbp:
        return {**base, "status": "NOT_RUN", "tone": "neutral", "detail": "Plan-benefit lookup not run."}

    from network_probe.domain.enrollment import live_enabled
    from network_probe.domain.plan_benefits import default_plan_benefit_store, resolve_plan_type

    if store is None and live_enabled():
        try:
            store = default_plan_benefit_store()
        except Exception:  # noqa: BLE001 — best-effort; a missing table degrades to the string token
            store = None
    try:
        res = resolve_plan_type(plan, benefit_type, store=store)
    except Exception:  # noqa: BLE001
        return {**base, "status": "UNRESOLVED", "tone": "neutral", "detail": "Plan-benefit lookup unavailable."}

    cap = res.capability
    tier = ("pays out-of-network benefits" if cap is True else
            "has no routine out-of-network benefits (emergency/urgent only)" if cap is False else
            "out-of-network tier deferred to the 271 (ambiguous plan type / D-SNP)")
    if cap is True:
        status, tone = "HAS_OON_BENEFITS", "success"
    elif cap is False:
        status, tone = "NO_OON_BENEFITS", "danger"
    elif res.source == "none":
        status, tone = "UNRESOLVED", "neutral"
    else:
        status, tone = "DEFER", "neutral"

    rec = res.record
    if rec is not None:
        moop = _pbp_moop_str(rec)
        detail = (f"{rec.plan_type.upper()} plan '{rec.plan_name}' "
                  f"(contract {rec.contract_number}/{rec.pbp_id}{', D-SNP' if rec.dsnp else ''}) — {tier}."
                  + (f" MOOP: {moop}." if moop else "") + " Source: live CMS PBP.")
    elif res.source == "plan-string":
        detail = (f"Plan type '{res.plan_type}' read from the plan string — {tier}. "
                  "No PBP plan matched for MOOP detail.")
    else:
        detail = ("No plan-type token in the plan string and no PBP match — "
                  "out-of-network tier deferred to the 271.")
    return {**base, "status": status, "tone": tone, "detail": detail}


def _directory_source(q, catalogue, run_directory: bool) -> dict:
    if not run_directory:
        return {"source": "Payer directory", "answers": "provider network", "status": "NOT_RUN",
                "tone": "neutral", "detail": "Directory lookup not run."}
    try:
        from network_probe.domain.service import get_adapter

        adapter = get_adapter(q.payer, catalogue=catalogue)
        v = adapter.check_network(q)
        return {
            "source": "Payer directory",
            "answers": "provider network",
            "status": v.status.value,
            "tone": _tone(v.status.value),
            "detail": (v.notes or "")[:220] or f"Directory returned {v.status.value}.",
        }
    except Exception as exc:  # noqa: BLE001 — best-effort; a missing/gated directory is not an error
        return {
            "source": "Payer directory",
            "answers": "provider network",
            "status": "UNAVAILABLE",
            "tone": "neutral",
            "detail": f"No usable public directory for this payer ({type(exc).__name__}).",
        }


def assemble_evidence(q, result, benefit_type=None, credentialing=None, crosswalk=None,
                      catalogue=None, run_directory: bool = True, run_enrollment: bool = True,
                      run_pbp: bool = True, plan_store=None) -> list[dict]:
    """Gather each source's independent finding for side-by-side display. `run_directory` /
    `run_enrollment` / `run_pbp` gate the live lookups (directory FHIR call, PECOS/Medicaid call,
    CMS PBP DB read) — set False in tests and wherever a live read isn't wanted."""
    if credentialing is None:
        from network_probe.domain.credentialing import default_credentialing

        credentialing = default_credentialing()
    return [
        _stedi_source(result),
        _pbp_source(q, result, benefit_type, run_pbp, store=plan_store),
        _credentialing_source(q, credentialing),
        _tic_source(q, result, benefit_type, crosswalk),
        _enrollment_source(q, result, benefit_type, run_enrollment),
        _directory_source(q, catalogue, run_directory),
    ]
