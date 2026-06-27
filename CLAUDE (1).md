# CLAUDE.md — Find-a-Doctor Network-Status Verification Probe

## Objective

Build a small Python service that, given a **provider** (name / NPI) and a **payer + plan**,
queries that payer's public "Find a Doctor" provider directory and returns a structured
**in-network / out-of-network / unknown** verdict.

This is the "network-status enrichment" layer that sits alongside an existing 270/271
eligibility capability. The 271 tells us the member is *active*; this probe tells us whether
*this provider* participates in *this member's plan network* — the gap that raw eligibility
transactions (and tools like pVerify) miss.

**Goal for this first build: prove it works end-to-end against ONE payer.** Start with Oscar.
Do not try to support all payers at once. Get one clean, real, live lookup working first.

---

## Critical constraint — DISCOVER, do not assume

There is **no documented public API** for these directories and **no shared standard** across
payers. Each payer's Find-a-Doctor page calls its own private backend JSON endpoint.

**You must discover the real endpoint by inspection. Do NOT hardcode an endpoint URL from
memory or from this file — none is given here on purpose, because any guessed URL will be
stale or wrong.** The discovery step below is the most important part of the task.

---

## Test case (use this exact case to validate)

Use a known-true case so you can tell whether the probe is actually working:

- **Payer:** Oscar Health
- **Provider:** Kyle Herron, NPI `1679766943`
- **Member plan:** Oscar Florida individual — "BASE SILVER CSR 150 / SILVERSIMPLEPCPSAVER", 2026
- **Patient area (for plan/network scoping):** West Palm Beach, FL 33409
- **Expected ground-truth answer:** **OUT OF NETWORK** (independently confirmed via the payer
  portal). If your probe returns OON for this case, it works. If it returns in-network, either
  the probe is hitting the wrong network/plan or parsing wrong — investigate before moving on.

A second, softer sanity check (optional): pick any large, obviously-participating PCP in the
same Oscar FL network and confirm the probe returns IN-NETWORK for them. A probe that returns
OON for *everyone* is broken, not accurate.

---

## Phase 1 — Endpoint discovery (do this first, manually-ish)

1. Open the Oscar Find-a-Doctor / provider search page in a real browser
   (start at the public provider search on hioscar.com — locate the current "Find a Doctor"
   / provider search entry point yourself; the path changes, so navigate to it, don't assume).
2. Open browser DevTools → **Network** tab → filter to **Fetch/XHR**.
3. Perform a provider search in the UI (search a provider name, set plan/network + location).
4. Identify the **XHR/fetch request** that returns provider results as JSON. Record:
   - the full request **URL** (path + query params)
   - the **HTTP method**
   - all **request headers** (especially any `Authorization`, `x-api-key`, cookies, or
     CSRF/anti-bot tokens)
   - the **query/body params** and which one carries: search term, plan/network id, location/zip
   - the **response JSON shape** — find the field(s) that encode provider identity (name, NPI)
     and the field(s) that encode network/plan participation
5. Write findings into a file `DISCOVERY.md` in the repo before writing any probe code. This is
   the contract the probe is built against.

**If the endpoint is protected** (API key tied to the page, request signing, Cloudflare/Akamai
challenge, CAPTCHA, aggressive rate limiting): document exactly what protection you see in
`DISCOVERY.md` and STOP to report it. Do not attempt to defeat bot protection, solve CAPTCHAs,
or evade WAFs. If Oscar is hard-blocked, note it and recommend trying a softer payer next
(e.g. the others in the adapter list) rather than forcing Oscar.

---

## Phase 2 — Build the probe (one payer: Oscar)

Architecture: **pluggable per-payer adapter behind one shared interface.** Even though we build
only Oscar now, structure it so adding Humana/Cigna/Devoted/BCBS-TX later is just a new adapter.

```
network_probe/
  __init__.py
  models.py          # dataclasses: ProviderQuery, NetworkVerdict
  base.py            # abstract PayerAdapter interface
  adapters/
    oscar.py         # concrete Oscar adapter (built from DISCOVERY.md)
  service.py         # picks adapter by payer, runs query, returns NetworkVerdict
  cli.py             # `python -m network_probe.cli --payer oscar --npi 1679766943 ...`
tests/
  test_oscar.py      # asserts the Herron OON ground-truth case
DISCOVERY.md
README.md
```

### Data models (`models.py`)

```python
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class NetworkStatus(str, Enum):
    IN_NETWORK = "IN_NETWORK"
    OUT_OF_NETWORK = "OUT_OF_NETWORK"
    UNKNOWN = "UNKNOWN"          # could not determine — DO NOT default to OON

@dataclass
class ProviderQuery:
    payer: str
    plan_hint: str              # e.g. "BASE SILVER CSR 150" / network name
    npi: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None

@dataclass
class NetworkVerdict:
    status: NetworkStatus
    matched_provider: Optional[dict]   # raw provider record that matched, for audit
    plan_or_network_checked: str
    source_url: str                    # exact endpoint queried — for the audit trail
    confidence: str                    # "high" | "medium" | "low"
    notes: str                         # human-readable explanation of how verdict was reached
```

### Adapter interface (`base.py`)

```python
from abc import ABC, abstractmethod
from .models import ProviderQuery, NetworkVerdict

class PayerAdapter(ABC):
    payer_name: str
    @abstractmethod
    def check_network(self, q: ProviderQuery) -> NetworkVerdict: ...
```

### Oscar adapter (`adapters/oscar.py`)

Implement against the real endpoint recorded in `DISCOVERY.md`. Requirements:

- Use `httpx` (sync is fine for the demo). Send the exact headers/params discovery found.
- Match the returned providers against the query. **Match on NPI when available** (exact);
  fall back to name + location when NPI isn't in the response. Be strict about matching so you
  don't accept a same-name different provider.
- Determine participation for the **specific plan/network** in `plan_hint`, not "any Oscar
  network." A provider can be in one Oscar network and not another — this is the #1 source of
  wrong answers. If the directory requires selecting a plan/network to search, pass it.
- Return a `NetworkVerdict`:
  - provider found in the requested network → `IN_NETWORK`, confidence high
  - searched the correct network and provider definitively absent → `OUT_OF_NETWORK`,
    confidence high/medium
  - couldn't confirm the network was set correctly, endpoint ambiguous, or no clean match →
    `UNKNOWN` (never silently return OON on ambiguity — UNKNOWN is the honest answer and keeps
    the demo trustworthy)

### Service + CLI

- `service.py`: maps `payer` string → adapter instance, runs `check_network`, returns verdict.
- `cli.py`: prints the verdict as readable text **and** as JSON (`--json` flag). Example:
  ```
  python -m network_probe.cli --payer oscar --npi 1679766943 \
      --last-name Herron --plan "BASE SILVER CSR 150" --state FL --zip 33409
  ```

---

## Phase 3 — Validate

- `tests/test_oscar.py`: run the Herron case, assert `status == OUT_OF_NETWORK`
  (or at minimum `!= IN_NETWORK` if the directory's "absent" signal is implicit).
- Run the optional in-network sanity provider; assert it returns `IN_NETWORK`.
- If both behave correctly → the probe works; document in `README.md` how to run it and what
  the verdicts mean.

---

## Engineering rules

- **Never default ambiguity to OUT_OF_NETWORK.** Wrong OON in a demo is worse than UNKNOWN.
- **Always populate `source_url`** in the verdict — the audit trail (which endpoint, which
  network was checked) is part of the value proposition; "trust us" is not.
- **Respect the site:** add a real `User-Agent`, keep request volume tiny (this is per-provider
  lookup, not bulk crawling), add a small delay between calls, cache responses during dev so you
  don't hammer the endpoint while iterating.
- **Do not** attempt to bypass CAPTCHAs, WAFs, or auth. If blocked, document and report — that's
  a valid, useful finding, not a failure to brute-force past.
- Keep the Oscar-specific logic fully inside `adapters/oscar.py`. The interface, models, service,
  and CLI must stay payer-agnostic so the next adapter drops in cleanly.
- Pin deps: `httpx`, `pytest`. Add a `requirements.txt`.

## Definition of done

1. `DISCOVERY.md` documents the real Oscar endpoint (or documents the blocker if protected).
2. `python -m network_probe.cli ...` returns a structured verdict for the Herron case.
3. The verdict for Herron is **not** IN_NETWORK (ideally OUT_OF_NETWORK with high confidence).
4. The architecture is pluggable so Humana/Cigna/Devoted/BCBS-TX adapters can be added later
   without touching shared code.
5. `README.md` explains run steps, verdict meanings, and clearly states this is a per-provider
   live directory lookup (not a bulk download, not a curated table).
