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


# AAA subscriber-identity reject codes worth retrying with a different subscriber shape:
# 72 = Invalid/Missing Subscriber ID (often a dependent/sequence suffix like "-01" the payer rejects),
# 73 = Invalid/Missing Subscriber Name (payer matches on ID+DOB; our name didn't match its records),
# 75 = Subscriber Not Found (name or a member-id suffix may be the mismatch). 71 (DOB) is excluded --
# no name-drop or suffix-strip can fix a wrong birth date.
_IDENTITY_RETRY_CODES = {"72", "73", "75"}


def _subscriber_identity_error(data: dict) -> bool:
    return any(str(e.get("code")) in _IDENTITY_RETRY_CODES for e in (data.get("errors") or []))


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
    # Stedi v3 requires the provider (information-receiver) loop to carry a name — organizationName
    # or lastName. The eligibility form supplies only an NPI, so we fall back to this org name.
    # Stedi does not validate it against the NPI; override via provider_org_name for a real name.
    DEFAULT_ORG_NAME = "PROVIDER"

    def __init__(
        self,
        api_key: str | None = None,
        client: CachedClient | None = None,
        payer_id: str | None = None,
        service_type_codes: list | None = None,
        provider_org_name: str | None = None,
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
        self.provider_org_name = provider_org_name or self.DEFAULT_ORG_NAME
        self.url = get_settings().stedi_eligibility_url

    def check(self, q: ProviderQuery) -> EligibilityResult:
        if not self.api_key:
            return _unknown("STEDI_API_KEY not configured")
        if not self.payer_id:
            return _unknown(f"no Stedi payer id for {q.payer!r}")
        provider = {
            k: v
            for k, v in {
                "npi": q.npi,
                "firstName": q.provider_first_name,
                "lastName": q.provider_last_name,
            }.items()
            if v
        }
        # Stedi requires organizationName OR lastName in the provider loop; a bare NPI 400s. When
        # no provider lastName was supplied, add organizationName so every check validates.
        if not provider.get("lastName"):
            provider["organizationName"] = self.provider_org_name

        dob = _dob(q.dob)

        def _subscriber(member: str | None, include_name: bool) -> dict:
            fields = {"memberId": member, "dateOfBirth": dob}
            if include_name:
                fields["firstName"] = q.first_name
                fields["lastName"] = q.last_name
            return {k: v for k, v in fields.items() if v}

        def _post(subscriber: dict) -> dict | None:
            body = {
                "tradingPartnerServiceId": self.payer_id,
                "provider": provider,
                "subscriber": subscriber,
                "encounter": {"serviceTypeCodes": self.stc},
            }
            try:
                return self.client.post_json(
                    self.url,
                    content=json.dumps(body),
                    headers={"Authorization": self.api_key, "content-type": "application/json"},
                )
            except Exception:
                return None

        # Payers reject subscriber identity in fixable ways: UHC wants memberId+DOB (name mismatch →
        # AAA-73/75); Oscar rejects a dependent/sequence suffix like "OSC…-01" (AAA-72). Try the
        # identity as given, then progressively simpler shapes, stopping at the first that isn't a
        # retryable identity error. If all fail, report the original (as-given) response's error.
        had_name = bool(q.first_name or q.last_name)
        stripped = re.sub(r"-\d+$", "", q.member_id) if q.member_id else q.member_id
        variants = [_subscriber(q.member_id, had_name)]
        if had_name:
            variants.append(_subscriber(q.member_id, False))
        if stripped and stripped != q.member_id:
            variants.append(_subscriber(stripped, False))
            if had_name:
                variants.append(_subscriber(stripped, True))

        first_data = None
        for subscriber in variants:
            data = _post(subscriber)
            if data is None:
                return _unknown("Stedi eligibility call failed")
            if first_data is None:
                first_data = data
            if not _subscriber_identity_error(data):
                return parse_271_benefits(data)
        return parse_271_benefits(first_data)
