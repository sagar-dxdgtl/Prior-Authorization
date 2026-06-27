# TODO — Network-status accuracy: handling "the directory is wrong"

**Status:** **#1–#5 all shipped.** Confidence/asymmetry, corroboration+REVIEW (NPPES), TIN-level
source, freshness, and the override/golden-record layer are built, wired through CLI/API/UI, and
tested (47 pass). Remaining is a *live* claims-grade network source (Availity) — see notes. See
"Progress" at bottom.
**Owner:** RCM network-probe.
**Why this exists:** our probe is only as accurate as the payer directory it reads. When the
directory is itself wrong, a single-source verdict is confidently wrong — and a **false IN** is the
expensive error (patient seen, then billed at OON rates).

---

## The motivating case (from `test-data/P Verify OON examples …pdf`)

pVerify's own OON examples are cases where the **271 / pVerify said "In-Network" but the provider was
actually OON** (confirmed via Availity + payer portal + phone). We ran our probe on the four that map
to our test data:

| Case | Truth (Availity/portal) | Our probe | Result |
|---|---|---|---|
| Ochoa · Oscar · Herron | OON | OUT_OF_NETWORK (high) | ✅ caught |
| Benschneider · Cigna · Kiang | OON | OUT_OF_NETWORK (med) | ✅ caught |
| Franz · Humana · Friedman | OON | OUT_OF_NETWORK (med) | ✅ caught |
| **Rodriguez · Devoted CO PPO · Li** | **OON** | **IN_NETWORK (high)** | ❌ **missed** |

**3/4.** The miss is not a code bug: Devoted's *own* Find-a-Doctor directory explicitly lists
Jing Li, MD (NPI 1629339312) as in-network for "CO PPO" (6 records, 37 counties). Availity says OON.
The payer's directory is stale/wrong — the classic problem below.

### How pVerify manages it today (read from their doc)
No automated fix — **redundancy + humans**: run 271 *and* Availity, check the payer's TIN-level
network-status portal (Cigna "You are Out-Of-Network for this patient"), **call the payer** for a
reference #, and **log manual notes / route to a human queue** (PEC). Accurate-ish but
labor-intensive and reactive. **Our opportunity: automate the agreement, escalate only the conflicts.**

### Industry context (why this is the norm, not an edge case)
- CMS Medicare Advantage directory audits: **45–52% of listings had ≥1 inaccuracy**; studies put
  >50% of entries in error. Inaccuracies persist — **40.3% remained wrong for ~540 days**.
  ([CMS reqs](https://www.atlassystems.com/blog/cms-provider-directory-requirements),
  [CAQH issue brief](https://www.caqh.org/hubfs/Insights%20-%20Provider%20Directory%20Accuracy%20Issue%20Brief_vf.pdf),
  [AJMC persistence study](https://www.ajmc.com/view/persistence-of-provider-directory-inaccuracies-after-the-no-surprises-act))
- "**Ghost networks**": inactive/unreachable providers inflate networks; regulators have levied
  essentially no penalties. ([Quest Analytics](https://questanalytics.com/news/what-are-ghost-networks/),
  [OIG 2025 behavioral health report](https://oig.hhs.gov/reports/all/2025/many-medicare-advantage-and-medicaid-managed-care-plans-have-limited-behavioral-health-provider-networks-and-inactive-providers/),
  [ProPublica](https://www.propublica.org/article/ghost-networks-health-insurance-regulators))
- It's a **revenue-cycle** problem, not just compliance.
  ([Datagence](https://datagence.io/resources/provider-directories-arent-just-a-compliance-problem-theyre-a-revenue-cycle-problem/))

---

## The 5 work items

### 1. Treat the directory as ONE signal — confidence + asymmetric handling
**Goal:** never assert certainty from a single directory; be most skeptical of IN.
**Best practice (research):** layered validation, don't trust a single source; conflict resolution
needs a source-trust score + recency rule + tiebreaker, "or your golden record is just a
confident-looking guess" ([Cleanlist](https://www.cleanlist.ai/glossary/golden-record),
[Informatica](https://www.informatica.com/blogs/golden-record.html)).
**Tasks:**
- [x] Demote **single-source IN → `medium`** confidence; label "verify before billing." *(corroboration.finalize)*
- [x] Keep **absence-based OON** stronger than presence-based IN (asymmetry: false-IN is costlier).
- [x] Surface the asymmetry in the UI (caveat in rationale + medium meter).
- [ ] Add a per-payer **source-trust weight** (e.g. FHIR PDEX > marketing directory; PPO least trusted).

### 2. Multi-source corroboration — disagreement ⇒ REVIEW, not a verdict  ★ highest leverage
**Goal:** agree across ≥2 independent sources → high confidence; conflict → `REVIEW` (human queue).
**Best practice (research):** plans that pass audits use **multi-source reconciliation in layers**
(provider sites → NPPES NPI → state boards → CMS Care Compare), plus **cross-payer consistency**
("if 5 other plans list a different address, investigate"); several states add **claims data as an
activity signal** to kill ghost entries
([Atlas multi-source](https://www.atlassystems.com/blog/provider-data-accuracy),
[ASPE state coordination](https://aspe.hhs.gov/sites/default/files/documents/72a2324e3deb078b275c66eb53052c86/state-coordinate-provider-directory-accuracy.pdf)).
**Candidate sources we can actually reach:** Find-a-Doctor directory (have it), the payer's
**FHIR PDEX** directory (independent pipeline), the payer's **TIN-level network-status portal**
(Cigna-style, closest to truth), **NPPES** (NPI/identity/active), and **Availity/claims** (if access).
**Tasks:**
- [x] Add a `NetworkStatus.REVIEW` state + `confidence="conflict"`.
- [x] Define `corroboration.finalize(verdict, q, sources)` — pluggable sources, reconcile, REVIEW on
      contradiction, signals attached to the verdict + shown in CLI/UI/JSON.
- [x] Add **NPPES** identity/active source. **Live** via `POST https://npiregistry.cms.hhs.gov/RegistryBack/npiDetails`
      (the `.cms.gov` host doesn't resolve here; the `.cms.hhs.gov` RegistryBack endpoint does).
      Verified live: Kyle Herron → active → corroborates. NPPES returns identity/active/taxonomy/state
      only — **no TIN** (`identifiers: []`).
- [ ] **Wire a live independent _network_ source.** NPPES gives identity, not network. Need one of:
      Availity/claims, a TIN-level payer portal check (Cigna-style), or a 2nd payer directory feed.
      (Devoted's `api.prod.devoted.com/fhir` is patient-access OAuth, not the public directory — base
      URL for its PDEX directory still TBD.)

### 3. Verify by TIN + NPI, not NPI alone
**Goal:** catch "individual NPI listed, but their billing TIN/group is OON" (the Cigna/WAZNI PLLC case).
**Best practice (research):** contracts are negotiated at the **TIN/group (Type-2 NPI) level**, not the
individual; "individual NPIs capture clinical attribution but not economic reality." NPI→TIN mapping is
the crux and is what most vendors omit
([trekhealth NPI↔TIN](https://www.trekhealth.io/resources/npi-to-tin-mapping-connecting-clinical-and-financial-identity-in-transparency-in-coverage-data),
[AAO TIN/NPI](https://www.aao.org/eyenet/article/use-of-tins-and-npis-as-identifiers)).
**Tasks:**
- [x] Capture **TIN** (`ProviderQuery.tin`, CLI `--tin`, API field, UI input, sample data).
- [x] `TinScopeSource` — if the member's billing TIN isn't among the provider's in-network TINs →
      contradicts → REVIEW. Oscar exposes per-TIN data (`network_infos[].tin`); others degrade to
      inconclusive. *(Verified live: Oscar/Jessica + wrong TIN → REVIEW.)*
- [x] **Loader built** (`tin_crosswalk.TinCrosswalk`, JSON/CSV, `TIN_CROSSWALK_PATH` env) feeding
      `TinScopeSource`. Just needs a data file (TiC / 835 / NPI→TIN).
- [ ] Build an **NPI→TIN map** from Transparency-in-Coverage machine-readable files (so we can scope
      TIN even when the directory doesn't expose it). **Needed because:** none of the public sources
      publish TIN/EIN — NPPES `identifiers` is empty; Humana/UHC FHIR `Organization.identifier` is
      null; Devoted gives only group keys/names (`copracticehealth` / "Practice Health (CO)"). The
      **only** payer embedding real TINs is **Oscar** (`network_infos[].tin`). A member's billing TIN
      otherwise comes from the **claim/835** or the **TiC** crosswalk. (The 271 reports' "Fed Tax ID"
      field is blank.)

### 4. Freshness & transition signals
**Goal:** auto-lower confidence on stale or soon-to-change records.
**Best practice (research):** No Surprises Act requires **90-day verification**, **2-business-day**
updates, **1-business-day** response to network-status inquiries; non-compliance up to **$100/day per
affected person** — so freshness metadata is both available and meaningful
([Claritev NSA](https://www.claritev.com/insights/understanding-provider-directory-requirements-under-the-no-surprises-act/),
[MD Clarity NSA](https://www.mdclarity.com/blog/no-surprises-act-provider-directory)).
**Signals we already see:** Oscar's `last_inn_date` and `going_oon_soon`; FHIR `meta.lastUpdated`;
Devoted's effective dates.
**Tasks:**
- [x] Parse + surface `going_oon_soon` / `last_inn_date` (Oscar) via `FreshnessSource`.
- [x] Decay confidence: `going_oon_soon` → IN drops to **low** + caveat.
- [ ] Pull FHIR `meta.lastUpdated` per payer; decay on stale last-verified dates.
- [ ] (Stretch) "network-status inquiry response" artifact for the NSA 1-business-day rule.

### 5. Correction / override layer + feedback loop (golden record)
**Goal:** when a human/Availity confirms the directory was wrong, remember it so future checks are right.
**Best practice (research):** MDM **golden record** via 5 steps — ingestion, matching, **survivorship**,
validation, ongoing maintenance — with **source-trust + recency tiebreakers**
([Semarchy](https://semarchy.com/blog/healthcare-master-data-management-a-practical-guide/),
[Profisee](https://profisee.com/platform/golden-record-management/)).
**Tasks:**
- [x] Persistent **override store** (`overrides.py`, JSON) keyed to `(payer, NPI[, plan/network/TIN])`
      with verified_by + verified_at + note; consulted first in `finalize`, beats the directory.
- [x] Feed confirmed conflicts back: API `POST /api/override` + UI "Record verified status" buttons.
- [x] Survivorship: lookup prefers most-specific then most-recent. *(Verified live: Devoted/Li
      IN→OUT_OF_NETWORK after recording the Availity-confirmed status.)*
- [ ] (Stretch) Auto-expire overrides after N days to force re-verification (NSA-style).

---

## Suggested sequencing
1. **#1 confidence/asymmetry** (small, immediate honesty win; no new sources).
2. **#2 corroboration + REVIEW state** (highest leverage; turns the Devoted/Li miss into a flagged
   conflict instead of a false IN). Start with FHIR-PDEX-as-second-source + NPPES.
3. **#4 freshness** (cheap; data already present for Oscar).
4. **#3 TIN/NPI** (bigger; needs TIN capture + TiC NPI→TIN map).
5. **#5 override/golden record** (needs persistence; compounds value over time).

## Regression baseline
Use the `test-data/P Verify OON examples` set as the accuracy regression suite.
- **After #1 + #2:** Devoted/Li moved from confident **IN (high)** → **IN (medium) + "verify before
  billing"** — the false-confidence is gone. It's still IN because every source we can reach agrees
  (the directory says IN; NPPES corroborates Li as a real active CO provider).
- **To flip it to REVIEW/OON** needs a source that contradicts at the *network* level (Availity /
  TIN portal) — the open task above. NPPES alone can't: Li really is an active provider; the directory
  is wrong about *participation*, which an identity check can't see.
- The other three OON cases (Oscar/Herron, Cigna/Kiang, Humana/Friedman) stay correctly **OON**.

## Progress (this build)
- `corroboration.py` — `finalize()` pipeline: **override (#5) → sources → asymmetry/freshness**.
  Sources: `NppesSource` (#2 identity/active), `TinScopeSource` (#3), `FreshnessSource` (#4).
- `overrides.py` — golden-record store (#5): `Override`, `OverrideStore` (load/lookup/add,
  most-specific-then-most-recent), `verdict_from_override`.
- `models.py` — `NetworkStatus.REVIEW`, `confidence` incl. `conflict`, `ProviderQuery.tin`,
  `corroboration` on the verdict + JSON.
- Adapters — Oscar exposes `in_network_tins` + `going_oon_soon` + `last_inn_date` for the sources.
- `service.check_network(corroborate=True)`; CLI `--tin` / `--no-corroborate`;
  API `tin` field + `POST /api/override`; UI: **Needs review** badge, **Cross-checks** panel,
  TIN input, and **Record verified status** (golden-record) buttons.
- Verified live: Oscar/Jessica + wrong TIN → **REVIEW**; Devoted/Li after recording Availity OON →
  **OUT_OF_NETWORK (high, verified override)**.
- Tests: `tests/test_corroboration.py` (#1–#5 incl. the credential-suffix name-match bug).
  Full suite **47 pass**.

## What's left (live data only)
- A **claims-grade live network source** (Availity / a payer TIN portal API) — would let conflicts be
  detected *automatically* instead of via the override after a human/Availity check. Until then, the
  override loop is how a confirmed OON gets remembered.
- **NPPES is live** (RegistryBack/npiDetails) — identity/active corroboration now real.
- **NPI→TIN map** (TiC files / 835) — required to scope TIN beyond Oscar, since no public directory
  or NPPES publishes TIN/EIN.

## Sources
- CMS / accuracy: [Atlas CMS reqs](https://www.atlassystems.com/blog/cms-provider-directory-requirements) ·
  [CAQH brief](https://www.caqh.org/hubfs/Insights%20-%20Provider%20Directory%20Accuracy%20Issue%20Brief_vf.pdf) ·
  [Verisys](https://verisys.com/blog/provider-directory-accuracy-patient-access/) ·
  [AJMC persistence](https://www.ajmc.com/view/persistence-of-provider-directory-inaccuracies-after-the-no-surprises-act)
- Ghost networks: [Quest Analytics](https://questanalytics.com/news/what-are-ghost-networks/) ·
  [OIG 2025](https://oig.hhs.gov/reports/all/2025/many-medicare-advantage-and-medicaid-managed-care-plans-have-limited-behavioral-health-provider-networks-and-inactive-providers/) ·
  [ProPublica](https://www.propublica.org/article/ghost-networks-health-insurance-regulators) ·
  [Yale L&PR](https://yalelawandpolicy.org/laying-ghost-networks-rest-combatting-deceptive-health-plan-provider-directories)
- TIN vs NPI: [trekhealth](https://www.trekhealth.io/resources/npi-to-tin-mapping-connecting-clinical-and-financial-identity-in-transparency-in-coverage-data) ·
  [AAO](https://www.aao.org/eyenet/article/use-of-tins-and-npis-as-identifiers)
- No Surprises Act: [Claritev](https://www.claritev.com/insights/understanding-provider-directory-requirements-under-the-no-surprises-act/) ·
  [MD Clarity](https://www.mdclarity.com/blog/no-surprises-act-provider-directory)
- MDM / golden record: [Informatica](https://www.informatica.com/blogs/golden-record.html) ·
  [Semarchy](https://semarchy.com/blog/healthcare-master-data-management-a-practical-guide/) ·
  [Profisee](https://profisee.com/platform/golden-record-management/) ·
  [Cleanlist](https://www.cleanlist.ai/glossary/golden-record)
