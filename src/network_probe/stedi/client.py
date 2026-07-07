from __future__ import annotations

import json
import re
from typing import Protocol

from network_probe.core._http import CachedClient
from network_probe.core.config import get_settings
from network_probe.core.secrets_provider import get_secret
from network_probe.domain.benefits import EligibilityResult
from network_probe.domain.models import NetworkStatus, ProviderQuery
from network_probe.stedi.parse_271 import parse_271_benefits


class EligibilitySource(Protocol):
    def check(self, q: ProviderQuery) -> EligibilityResult: ...


def _unknown(reason: str) -> EligibilityResult:
    return EligibilityResult(
        coverage_active=None,
        plan_name=None,
        group=None,
        coverage_dates={},
        network_status=NetworkStatus.UNKNOWN,
        benefits=[],
        pcp_required=None,
        prior_auth_required=None,
        referral_required=None,
        cob=None,
        network_verdict=None,
        corroboration=[],
        source_audit={"source": "stedi", "note": reason},
    )


def _dob(dob: str | None) -> str | None:
    """Normalize MM/DD/YYYY or YYYY-MM-DD to Stedi's YYYYMMDD."""
    if not dob:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", dob)
    if m:
        return f"{m.group(3)}{int(m.group(1)):02d}{int(m.group(2)):02d}"
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", dob)
    if m:
        return f"{m.group(1)}{m.group(2)}{m.group(3)}"
    digits = re.sub(r"[^0-9]", "", dob)
    return digits or None


class StediEligibilityClient:
    DEFAULT_STC = ["30", "98"]

    def __init__(
        self,
        api_key: str | None = None,
        client: CachedClient | None = None,
        payer_id: str | None = None,
        service_type_codes: list | None = None,
    ):
        # Settings reads the .env file (pydantic); get_secret reads os.environ → AWS Secrets Manager.
        # Prefer the configured setting (.env/env var), then fall back to the secrets provider (prod/AWS).
        self.api_key = (
            api_key if api_key is not None
            else (get_settings().stedi_api_key or get_secret("STEDI_API_KEY"))
        )
        # PHI must never hit disk: force cache_dir=None.
        self.client = client or CachedClient(cache_dir=None, delay_seconds=0.2)
        self.payer_id = payer_id
        self.stc = service_type_codes or self.DEFAULT_STC
        self.url = get_settings().stedi_eligibility_url

    def check(self, q: ProviderQuery) -> EligibilityResult:
        if not self.api_key:
            return _unknown("STEDI_API_KEY not configured")
        if not self.payer_id:
            return _unknown(f"no Stedi payer id for {q.payer!r}")
        body = {
            "tradingPartnerServiceId": self.payer_id,
            "provider": {
                k: v
                for k, v in {"npi": q.npi, "firstName": q.first_name, "lastName": q.last_name}.items()
                if v
            },
            "subscriber": {
                k: v
                for k, v in {
                    "memberId": q.member_id,
                    "dateOfBirth": _dob(q.dob),
                    "firstName": q.first_name,
                    "lastName": q.last_name,
                }.items()
                if v
            },
            "encounter": {"serviceTypeCodes": self.stc},
        }
        try:
            data = self.client.post_json(
                self.url,
                content=json.dumps(body),
                headers={"Authorization": self.api_key, "content-type": "application/json"},
            )
        except Exception:
            return _unknown("Stedi eligibility call failed")
        return parse_271_benefits(data)
