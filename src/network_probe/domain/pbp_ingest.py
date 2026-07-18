"""Parser for CMS Medicare-Advantage **PBP Benefits** files (public, no-auth, quarterly).

The CY2026 ZIP (`https://www.cms.gov/files/zip/pbp-benefits-2026.zip`) ships tab-delimited flat files
with a header row of column names. Two are all we need for the OON benefit-tier question:

  * ``pbp_section_a.txt`` — plan attributes: type (`pbp_a_plan_type`), name, SNP type, network flag.
  * ``pbp_section_d.txt`` — plan-level MOOP: in-network (`pbp_d_out_pocket_amt`), combined in+out
    (`pbp_d_comb_max_enr_amt` — the PPO OON signal), and OON-only (`pbp_d_oon_max_enr_oopc_amt`).

Both key on ``(pbp_a_hnumber, pbp_a_plan_identifier, segment_id)`` — the H-number + PBP + segment
that a HETS/271 returns. This module reads by column NAME (never fixed position — the files carry
their own header), joins A⟕D, and yields one :class:`PlanBenefitRecord` per plan (Section A is the
plan universe; a plan missing from D still emits, with MOOPs None). It contains **no provider data**
— PBP is benefit design only; provider-network membership comes from other sources entirely.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from network_probe.domain.line_of_business import plan_type_from_pbp_code

_DSNP_CODE = "3"  # pbp_a_special_need_plan_type: 1=I-SNP, 3=D-SNP, 4=C-SNP


@dataclass
class PlanBenefitRecord:
    contract_number: str  # H/R/E number, e.g. "H0321"
    pbp_id: str  # plan id, 3-char with leading zeros, e.g. "002"
    segment_id: str  # usually "0"
    year: int
    plan_type_code: str  # raw CMS code, e.g. "02"
    plan_type: str  # normalized token: hmo|hmopos|ppo|pffs|unknown
    plan_name: str
    org_marketing_name: str
    snp_type_code: str  # "" | "1" | "3" | "4"
    dsnp: bool
    network_flag: str  # "1"=Full, "2"=No, "3"=Partial
    inn_moop: str | None  # in-network MOOP dollar amount
    comb_moop_yn: str  # "1"=Yes, "2"=No, ""=blank — a combined in+out MOOP only exists with OON benefits
    comb_moop: str | None  # combined in+out MOOP dollar amount
    oon_moop_yn: str
    oon_moop: str | None  # out-of-network-only MOOP dollar amount


def _clean(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


def _read_tsv(path: str | Path) -> tuple[dict[str, int], list[list[str]]]:
    """Return (name→index map from the header row, list of data rows). Latin-1: CMS files are not
    guaranteed UTF-8 and must never raise on a stray byte."""
    with open(path, newline="", encoding="latin-1") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if not rows:
        return {}, []
    header = rows[0]
    return {h.strip(): i for i, h in enumerate(header)}, rows[1:]


def _get(row: list[str], ix: dict[str, int], name: str) -> str:
    i = ix.get(name)
    return row[i] if i is not None and i < len(row) else ""


def _key(row: list[str], ix: dict[str, int]) -> tuple[str, str, str]:
    return (
        _get(row, ix, "pbp_a_hnumber").strip(),
        _get(row, ix, "pbp_a_plan_identifier").strip(),
        _get(row, ix, "segment_id").strip(),
    )


def _parse_section_d(path: str | Path) -> dict[tuple[str, str, str], dict[str, str | None]]:
    ix, rows = _read_tsv(path)
    out: dict[tuple[str, str, str], dict[str, str | None]] = {}
    for row in rows:
        if not row or not any(row):
            continue
        out[_key(row, ix)] = {
            "inn_moop": _clean(_get(row, ix, "pbp_d_out_pocket_amt")),
            "comb_moop_yn": _get(row, ix, "pbp_d_comb_max_enr_amt_yn").strip(),
            "comb_moop": _clean(_get(row, ix, "pbp_d_comb_max_enr_amt")),
            "oon_moop_yn": _get(row, ix, "pbp_d_oon_max_enr_oopc_yn").strip(),
            "oon_moop": _clean(_get(row, ix, "pbp_d_oon_max_enr_oopc_amt")),
        }
    return out


def iter_plan_benefits(
    section_a_path: str | Path, section_d_path: str | Path, year: int
) -> Iterator[PlanBenefitRecord]:
    """Yield one PlanBenefitRecord per Section-A plan, enriched with Section-D MOOPs where present."""
    d_by_key = _parse_section_d(section_d_path)
    ix, rows = _read_tsv(section_a_path)
    for row in rows:
        if not row or not any(row):
            continue
        key = _key(row, ix)
        contract, pbp, segment = key
        if not contract:
            continue
        code = _get(row, ix, "pbp_a_plan_type").strip()
        snp = _get(row, ix, "pbp_a_special_need_plan_type").strip()
        d = d_by_key.get(key, {})
        yield PlanBenefitRecord(
            contract_number=contract,
            pbp_id=pbp,
            segment_id=segment,
            year=year,
            plan_type_code=code,
            plan_type=plan_type_from_pbp_code(code),
            plan_name=_get(row, ix, "pbp_a_plan_name").strip(),
            org_marketing_name=_get(row, ix, "pbp_a_org_marketing_name").strip(),
            snp_type_code=snp,
            dsnp=snp == _DSNP_CODE,
            network_flag=_get(row, ix, "pbp_a_network_flag").strip(),
            inn_moop=d.get("inn_moop"),
            comb_moop_yn=d.get("comb_moop_yn", ""),
            comb_moop=d.get("comb_moop"),
            oon_moop_yn=d.get("oon_moop_yn", ""),
            oon_moop=d.get("oon_moop"),
        )


def ingest_plan_benefits(section_a_path: str | Path, section_d_path: str | Path, year: int, store=None) -> int:
    """Parse the CMS PBP Section A + D files and idempotently upsert every plan into `plan_benefits`
    as global rows (tenant_id NULL). Re-running is a no-op. Returns the count of NEW rows written."""
    if store is None:
        from network_probe.domain.plan_benefits import default_plan_benefit_store

        store = default_plan_benefit_store()
    return store.upsert(list(iter_plan_benefits(section_a_path, section_d_path, year)))
