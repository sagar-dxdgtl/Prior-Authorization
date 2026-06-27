# Stedi eligibility — live-verified contract (Task 27)

Verified **live** against `healthcare.us.stedi.com` with a **test API key** (mock payers, synthetic
member → no real PHI, no payer enrollment). Confirms the `StediEligibilityClient` + `parse_271_benefits`
work against the real API and that our benefit field-paths match Stedi's schema.

## Auth & keys
- Header: `Authorization: <STEDI_API_KEY>` (raw key, no `Bearer`). Keys are `test…` (sandbox, mock
  payers, realistic 271s, free) or production (real payers; needs per-payer **enrollment**; real PHI →
  run only in a BAA'd/encrypted environment, never the local dev box).

## Eligibility check
`POST https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3`

Request:
```json
{
  "tradingPartnerServiceId": "60054",
  "provider": {"organizationName": "Test Clinic", "npi": "1679766943"},
  "subscriber": {"firstName": "John", "lastName": "Doe", "dateOfBirth": "YYYYMMDD", "memberId": "AETNA9wcSu"},
  "encounter": {"serviceTypeCodes": ["30"]}
}
```
- `tradingPartnerServiceId` = the payer's **`primaryPayerId`** (e.g. Aetna `60054`). Any valid-checkdigit NPI works.
- **Mock requires exact documented subscriber values**; otherwise the payer returns an AAA error.

Response — top-level keys observed:
`meta, controlNumber, reassociationKey, tradingPartnerServiceId, provider, subscriber,
subscriberTraceNumbers, payer, errors, x12, eligibilitySearchId, id` — plus, on success,
`planInformation, planDateInformation, planStatus, benefitsInformation`.

`benefitsInformation[]` entry fields (✅ all match `parse_271_benefits`):
`code, name, coverageLevelCode, coverageLevel, serviceTypeCodes, serviceTypes, timeQualifierCode,
timeQualifier, benefitAmount, benefitPercent, inPlanNetworkIndicatorCode, inPlanNetworkIndicator,
authOrCertIndicator, additionalInformation, …`

Errors arrive in a top-level `errors[]` (and `subscriber.aaaErrors[]`): `{field, code, description,
followupAction, location, possibleResolutions}`. Observed AAA codes: **72** = invalid/missing member id,
**71** = DOB doesn't match. Our parser keeps only the **code** (drops the PHI-bearing free text) and
returns `UNKNOWN` (never a guessed OON). ✅ verified live.

## Payer network (for the resolver)
`GET https://healthcare.us.stedi.com/2024-04-01/payers` → `{ "items": [ … ] }`, each:
`stediId, primaryPayerId, displayName, conciseName, names, aliases, enrollment, coverageTypes,
operatingStates, programs`. `scripts/resolve_payer_ids.py` matches roster labels against these and
proposes `primaryPayerId` (dry-run by default).

## Verification status
- ✅ Auth, endpoint, request/response JSON, AAA-error parsing, PHI redaction — **live-verified**
  (`tests/test_stedi_live.py::test_live_error_path_parses_and_redacts`, `pytest -m live`).
- ✅ Benefit field-paths confirmed against Stedi's schema; benefits logic unit-tested with the same names.
- ⏳ **Full benefits 271 parse**: enable `test_live_full_benefits_parse` by setting the exact documented
  mock member (Stedi mock-requests doc) — member id/name `AETNA9wcSu / John Doe` are confirmed valid;
  only the exact `dateOfBirth` is needed.

## Possible refinement (Slice B)
- Stedi returns a structured `authOrCertIndicator` per benefit — could replace the text-scan for
  prior-auth/referral detection (more reliable than parsing `additionalInformation` descriptions).
