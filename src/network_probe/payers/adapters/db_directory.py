"""Adapter for plans whose network is a parsed monthly PDF (no FHIR, no NPIs) — e.g. Align
Senior Care. Looks our provider up in payer_directory_entries by surname, then disambiguates
by first name + ZIP (domain.directory_match). The NPI is taken from our query, not the directory.
"""

from __future__ import annotations

from network_probe.domain.directory_match import _norm, match_directory
from network_probe.domain.models import NetworkStatus, NetworkVerdict, ProviderQuery
from network_probe.payers.adapters.base import PayerAdapter


class DbDirectoryAdapter(PayerAdapter):
    def __init__(self, payer_name: str = "directory", payer_label: str | None = None, candidates_fn=None, **_ignore):
        self.payer_name = payer_name
        self.payer_label = payer_label or payer_name
        #: tests inject a callable (payer_key, last_name) -> list[rows]; prod hits the DB
        self._candidates_fn = candidates_fn

    def _candidates(self, last_name: str) -> list:
        if self._candidates_fn is not None:
            return list(self._candidates_fn(self.payer_name, last_name))
        from sqlalchemy import select

        from network_probe.db.base import SessionLocal, app_engine
        from network_probe.db.models import PayerDirectoryEntry

        with SessionLocal(bind=app_engine()) as s:
            return list(
                s.execute(
                    select(PayerDirectoryEntry).where(
                        PayerDirectoryEntry.payer_key == self.payer_name,
                        PayerDirectoryEntry.last_name == _norm(last_name),
                    )
                )
                .scalars()
                .all()
            )

    def check_network(self, q: ProviderQuery) -> NetworkVerdict:
        if not q.last_name:
            return NetworkVerdict(
                status=NetworkStatus.UNKNOWN,
                matched_provider=None,
                plan_or_network_checked=f"{self.payer_label} provider directory (monthly PDF)",
                source_url="db:payer_directory_entries",
                confidence="low",
                notes="A provider name is required to match a PDF-only directory (it carries no NPIs).",
            )
        rows = self._candidates(q.last_name)
        status, matched, conf, note = match_directory(
            rows,
            payer_label=self.payer_label,
            last_name=q.last_name,
            first_name=q.first_name,
            state=q.state,
            zip_code=q.zip_code,
        )
        provider = {**matched, "npi": q.npi} if matched is not None else None
        return NetworkVerdict(
            status=status,
            matched_provider=provider,
            plan_or_network_checked=f"{self.payer_label} provider directory (monthly PDF)",
            source_url="db:payer_directory_entries",
            confidence=conf,
            notes=note,
        )
