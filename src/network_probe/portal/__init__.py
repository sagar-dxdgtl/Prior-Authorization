"""Live payer-portal capture — drives member-facing find-a-doctor portals in a real browser.

The portal is the accurate provider-network source: the CMS-mandated FHIR directories over-include
(false INs) and the TiC MRFs have gaps. This package produces a network verdict *plus a timestamped
screenshot* of the payer's own answer — the evidence a No Surprises Act directory-accuracy dispute
turns on.

Boundaries, deliberately: no CAPTCHA solving, no fingerprint patching, no auth bypass. Challenges are
detected, screenshotted and reported; where a portal refuses automated access the caller uses that
payer's CMS-mandated public FHIR Provider Directory API instead.
"""
