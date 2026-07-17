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
from network_probe.domain.tic_network import tic_network_status


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
    return {
        "source": "Stedi 271 (eligibility)",
        "answers": "coverage + plan tier",
        "status": status,
        "tone": _tone(status),
        "detail": (
            f"Coverage {status.lower()}; plan '{result.plan_name or '—'}'. Plan-level out-of-network "
            f"benefits: {oon_str}. A 271 gives plan-tier only — provider-specific network is UNKNOWN here."
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
    # (group contracted, physician-specific gap → Physician OON)? Check the TiC crosswalk + persisted
    # facts (not credentialing — this row is specifically the TiC/MRF signal).
    group = False
    try:
        cw = crosswalk
        if cw is None:
            from network_probe.domain.tin_crosswalk import default_crosswalk

            cw = default_crosswalk()
        group = bool(cw and cw.has_tin(q.payer, q.tin))
        if not group:
            from network_probe.domain.network_facts import default_provider_network_store

            group = default_provider_network_store().group_contracted(q.payer, q.tin) is True
    except Exception:
        group = False
    if group:
        return {
            "source": "TiC MRF",
            "answers": "provider network",
            "status": "GROUP_ONLY",
            "tone": "warning",
            "detail": (f"Billing TIN {q.tin} IS in-network in the payer's MRF (under other NPIs), but this "
                       f"physician's NPI {q.npi} is not listed — a physician-specific out-of-network gap."),
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
                      catalogue=None, run_directory: bool = True) -> list[dict]:
    """Gather the four sources' independent findings for side-by-side display. `run_directory=False`
    skips the (live) directory call — used in tests and when a directory read isn't wanted."""
    if credentialing is None:
        from network_probe.domain.credentialing import default_credentialing

        credentialing = default_credentialing()
    return [
        _stedi_source(result),
        _credentialing_source(q, credentialing),
        _tic_source(q, result, benefit_type, crosswalk),
        _directory_source(q, catalogue, run_directory),
    ]
