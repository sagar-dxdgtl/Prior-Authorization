from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from network_probe.db.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(UTC)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    username: Mapped[str] = mapped_column(String(150))
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40), default="user")
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Payer(Base):
    __tablename__ = "payers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    label: Mapped[str] = mapped_column(String(200))
    benefit_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    stedi_payer_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    enrollment_status: Mapped[str] = mapped_column(String(30), default="unknown")
    network_indicator_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    # Multi-source catalogue columns (Task: multi-source expansion). All nullable text.
    # fhir_base_url      — verified public PDEX Plan-Net base URL (routes the directory leg) or None.
    # tic_url            — verified Transparency-in-Coverage machine-readable-file index URL or None.
    # directory_url      — human-facing "find a doctor" page (informational).
    # directory_access   — "public-fhir" | "authorized-fhir" | "needs-authorized-api" | "pdf-directory" | "none".
    #                      ("authorized-fhir" = verified PDEX behind OAuth2; creds in .env, e.g. Anthem.)
    fhir_base_url: Mapped[str | None] = mapped_column(String, nullable=True)
    tic_url: Mapped[str | None] = mapped_column(String, nullable=True)
    directory_url: Mapped[str | None] = mapped_column(String, nullable=True)
    directory_access: Mapped[str | None] = mapped_column(String(40), nullable=True)


class EligibilityCheck(Base):
    __tablename__ = "eligibility_checks"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(20), index=True)
    payer_key: Mapped[str] = mapped_column(String(120))
    member_id_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    member_id_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    dob_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    name_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    npi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    result_jsonb: Mapped[dict] = mapped_column(JSONB, default=dict)
    source_audit: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class OverrideRow(Base):
    __tablename__ = "overrides"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    payer: Mapped[str] = mapped_column(String(120))
    npi: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))
    verified_by: Mapped[str] = mapped_column(String(120))
    verified_at: Mapped[str] = mapped_column(String(40))
    network: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plan: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    note: Mapped[str] = mapped_column(String, default="")


class ReviewCase(Base):
    __tablename__ = "review_cases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    eligibility_check_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    payer_key: Mapped[str] = mapped_column(String(120))
    npi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    member_id_hash: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open", index=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ProviderNetworkFact(Base):
    """Durable store of provider-network facts (payer, NPI, billing TIN → in/out), from the
    credentialing export, TiC MRF ingests, and directory reads. No PHI — contract facts only.

    - `npi` may be null for a group-level fact (TIN contracted with the payer under no specific NPI).
    - `source`: "credentialing" | "tic" | "directory".
    - Global rows are tenant_id NULL (readable by every tenant, like `payers`); a tenant may layer its
      own rows on top. The resolver / group-contracted check read these; ingest scripts write them.
    """

    __tablename__ = "provider_network_facts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "payer_key", "npi", "tin", "source", name="uq_pnf_identity"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    payer_key: Mapped[str] = mapped_column(String(120), index=True)
    npi: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    tin: Mapped[str] = mapped_column(String(20), index=True)
    in_network: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(20))
    plan: Mapped[str | None] = mapped_column(String(160), nullable=True)
    network_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    effective_date: Mapped[str | None] = mapped_column(String(40), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PlanBenefit(Base):
    """Medicare-Advantage plan benefit design from the public CMS PBP Benefits files (per contract
    year). Keyed by contract (H/R/E number) + PBP id + segment — the identifiers a HETS/271 returns.

    Benefit DESIGN only — plan type, SNP flag, and the in-network / combined / out-of-network MOOP.
    Contains **no provider data** (PBP has no rosters); it answers the *tier* question (does an OON
    provider get any benefit), never the network question. Public data → tenant_id is always NULL
    (global, readable by every tenant, like `payers`).
    """

    __tablename__ = "plan_benefits"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "contract_number", "pbp_id", "segment_id", "year", name="uq_planben_identity"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    contract_number: Mapped[str] = mapped_column(String(10), index=True)
    pbp_id: Mapped[str] = mapped_column(String(3))
    segment_id: Mapped[str] = mapped_column(String(3), default="0")
    year: Mapped[int] = mapped_column(Integer, index=True)
    plan_type: Mapped[str] = mapped_column(String(12))  # hmo|hmopos|ppo|pffs|unknown
    plan_type_code: Mapped[str | None] = mapped_column(String(4), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    org_marketing_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    snp_type_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    dsnp: Mapped[bool] = mapped_column(Boolean, default=False)
    network_flag: Mapped[str | None] = mapped_column(String(2), nullable=True)
    inn_moop: Mapped[str | None] = mapped_column(String(16), nullable=True)  # in-network MOOP
    comb_moop_yn: Mapped[str | None] = mapped_column(String(2), nullable=True)
    comb_moop: Mapped[str | None] = mapped_column(String(16), nullable=True)  # combined in+out MOOP
    oon_moop_yn: Mapped[str | None] = mapped_column(String(2), nullable=True)
    oon_moop: Mapped[str | None] = mapped_column(String(16), nullable=True)  # out-of-network MOOP
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class UsageCounter(Base):
    """Per-tenant request counter for daily/monthly quota enforcement. No PHI — just counts."""

    __tablename__ = "usage_counters"
    __table_args__ = (
        UniqueConstraint("tenant_id", "period_type", "period_key", name="uq_usage_tenant_period"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    period_type: Mapped[str] = mapped_column(String(8))  # "day" | "month"
    period_key: Mapped[str] = mapped_column(String(16))  # e.g. "2026-06-28" | "2026-06"
    count: Mapped[int] = mapped_column(Integer, default=0)


class PayerDirectoryEntry(Base):
    """A single provider-location row parsed from a payer's PDF provider directory.

    For plans that publish their network only as a monthly PDF with no NPI (e.g. Align Senior
    Care). Global reference data (tenant_id NULL): we never resolve these to NPIs — instead we
    match *our own* providers (name + state + zip from intake) against these rows. One row per
    (provider, location) so state/zip filtering is a plain WHERE. See domain/directory_match.py.
    """

    __tablename__ = "payer_directory_entries"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id"), nullable=True, index=True)
    payer_key: Mapped[str] = mapped_column(String(120), index=True)
    last_name: Mapped[str] = mapped_column(String(120), index=True)  # normalized (upper, A-Z only)
    first_name: Mapped[str] = mapped_column(String(120), default="")  # normalized
    full_name: Mapped[str] = mapped_column(String(240))  # as printed
    specialty: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address: Mapped[str | None] = mapped_column(String(240), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True, index=True)
    zip: Mapped[str | None] = mapped_column(String(10), nullable=True)
    accepting_new: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(40), nullable=True)  # directory "current as of"
    loaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class ReviewNote(Base):
    __tablename__ = "review_notes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), index=True)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("review_cases.id"), index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    text: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
