# Aetna TiC NPI→TIN Crosswalk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Source real Aetna NPI→TIN network-participation data via its unprotected TiC MRF host and hand-document any confirmed findings into `tin_crosswalk.py`'s `_SEED` list, exactly matching the existing UHC/Cigna/Kaiser/Ambetter precedent's documentation style.

**Architecture:** No new source code. Run the existing, unmodified `scripts/pull_tic_index.py` against the already-verified live Aetna `ALICFI` index URL, filtered to 6 NPIs already established as UVC-affiliated via other payers' crosswalk entries. Cross-reference whatever TIN(s) each NPI resolves to in Aetna's data against the already-established TIN for that NPI, then hand-write the outcome into `tin_crosswalk.py` using one of three fixed templates (confirmed hit / discrepancy / not found).

**Tech Stack:** Python 3.12, existing `pull_tic_index.py`/`tic_ingest.py` pipeline, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-aetna-tic-crosswalk-design.md` — read it first.
- No changes to `tic_ingest.py`, `pull_tic_index.py`, or `roster_seed.py` — this task is data + documentation only.
- No broader Aetna brand-code sweep (`ALICSI`, state entities) — `ALICFI` only, this pass.
- `aetna-fl-south-florida` and `aetna-il` cannot be checked (no UVC-affiliated NPI established for either market by any payer yet) — document this gap explicitly, do not silently skip it.
- A discrepancy (Aetna's TIN differs from the already-established TIN for that NPI) must NOT be added to `_SEED` under either TIN — document it as an open discrepancy only.

---

## File Structure

- **Modify** `src/network_probe/domain/tin_crosswalk.py` — add a new dated sweep comment block, zero or more `_SEED` entries, and additions to the "Checked and found NOT applicable/NOT present" section.
- **Modify** `tests/test_tin_crosswalk.py` — add one new test asserting confirmed findings, **only if at least one NPI is a confirmed hit** (see Step 5's decision rule).
- **Scratch only, not committed** (`.cache/` is git-ignored): `.cache/aetna-alicfi-npis.txt` (NPI filter input), `.cache/aetna-alicfi-crosswalk.csv` (pull output).

No new files under version control.

---

### Task 1: Run the Aetna ALICFI sweep and document findings

**Files:**
- Modify: `src/network_probe/domain/tin_crosswalk.py` (comment block above `_SEED`, the `_SEED` list itself, lines ~74-119 in the current file)
- Modify: `tests/test_tin_crosswalk.py` (conditionally — see Step 5)
- Scratch: `.cache/aetna-alicfi-npis.txt`, `.cache/aetna-alicfi-crosswalk.csv`

**Interfaces:**
- Consumes: `scripts/pull_tic_index.py`'s existing CLI (`--index-url`, `--npi-file`, `--payer`, `--out`) and `src/network_probe/domain/tin_crosswalk.py`'s existing `_SEED: list[dict]` format (`{"payer": str, "npi": str, "tin": str}`) and `TinCrosswalk`/`default_crosswalk()` API — unchanged, no new interfaces produced.

**The 6 NPIs and their already-established TIN** (from other payers' existing `_SEED` entries — this is the ground truth every outcome below gets compared against):

| NPI | Name | Established TIN | Established via | Target catalogue key(s) |
|---|---|---|---|---|
| `1992078745` | Arthur Maydell | `843447602` | `unitedhealthcare-az` | `aetna-az` |
| `1629339312` | Jing Li | `475181686` | `cigna-healthcare-co-denver` | `aetna-co-denver` |
| `1598895435` | (Wende Moore's provider) | `475181686` | `kaiser-permanente-co-denver` | `aetna-co-denver` |
| `1902811656` | (unnamed in existing docs) | *none established* — Anthem GA masks billing TIN behind a representative NPI | `bcbs-empire-anthem-elevance-ga-atlanta` (presence-only check) | `aetna-ga-atlanta` |
| `1972603934` | Kevin Fradkin | `933510922` | `uhc` | `aetna-tx-houston`, `aetna-tx-dallas` |
| `1710305735` | Umang Patel | `933510922` and `412049581` | `uhc` and `ambetter-centene-tx-dallas` | `aetna-tx-houston`, `aetna-tx-dallas` |

- [ ] **Step 1: Build the NPI filter file**

Create `.cache/aetna-alicfi-npis.txt` (create the `.cache/` directory first if it doesn't exist — it's already git-ignored, confirmed via `.gitignore` line 2) with exactly this content (one NPI per line, per `pull_tic_index.py`'s `_load_npi_filter` format — no comment lines needed, it doesn't support them):

```
1992078745
1629339312
1598895435
1902811656
1972603934
1710305735
```

- [ ] **Step 2: Run the pull**

This downloads and scans **all 283** `ALICFI` in-network files — the `--npi-file` filter bounds the *output*, not the download volume, so every file gets fully fetched and streamed regardless of whether it contains a match. Individual file sizes are unknown ahead of time (could be large); **do not assume a short runtime** — run this as a background task (e.g. dispatch to a research fork, or `Bash` with `run_in_background: true`) rather than blocking synchronously, and allow at least 15-20 minutes before treating it as stuck.

Run:
```bash
source .venv/bin/activate && python3 scripts/pull_tic_index.py \
  --index-url 'https://mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com/prd/mrf/AETNACVS_I/ALICFI/2026-07-05/tableOfContents/2026-07-05_Aetna-Life-Insurance-Company_index.json.gz' \
  --npi-file .cache/aetna-alicfi-npis.txt \
  --payer aetna-alicfi \
  --out .cache/aetna-alicfi-crosswalk.csv
```

Expected: prints `Selected 283 file(s) from index:` followed by a file listing, then (after the full scan completes) `Wrote N unique NPI->TIN rows to .cache/aetna-alicfi-crosswalk.csv` where `N` is between 0 and 7 (at most one row per NPI-TIN pair actually found; a single NPI can appear under more than one TIN).

**Contingency — stale index URL:** if the request to the `--index-url` itself fails (not an individual in-network file — those failing individually is normal and handled by `ingest_tic`'s per-URL error logging), the date segment has likely rolled past the verified `2026-07-05` snapshot (files publish monthly on the 5th). Retry once with the next monthly 5th-of-month date in the same pattern:
```
https://mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com/prd/mrf/AETNACVS_I/ALICFI/<YYYY-MM-05>/tableOfContents/<YYYY-MM-05>_Aetna-Life-Insurance-Company_index.json.gz
```
If that also fails, stop and report BLOCKED — do not brute-force further dates blindly.

- [ ] **Step 3: Inspect the results and classify each NPI**

Read `.cache/aetna-alicfi-crosswalk.csv`. For each of the 6 NPIs from Step 1, classify it into exactly one of three outcomes by comparing against the "established TIN" column in the table above:

- **CONFIRMED HIT** — the NPI appears in the CSV, and its TIN there **matches** the already-established TIN (or is one of the multiple established TINs, for `1710305735` which has two).
- **DISCREPANCY** — the NPI appears in the CSV, but its TIN there **differs** from every already-established TIN for that NPI.
- **NOT FOUND** — the NPI does not appear in the CSV at all.

(`1902811656` has no established TIN to compare against — if it appears in the CSV at all, treat that as a CONFIRMED HIT using whatever TIN Aetna's file reports, since there's no conflicting prior value to contradict; if absent, it's NOT FOUND.)

- [ ] **Step 4: Edit `tin_crosswalk.py` — apply the templates for each classified outcome**

Open `src/network_probe/domain/tin_crosswalk.py`. Find the existing comment block that ends right before `_SEED = [` (currently lines 74-119) and the "Checked and found NOT applicable/NOT present" sub-section within it (currently lines 100-111).

**For every CONFIRMED HIT**, insert one bullet into a new dated block placed immediately after the existing "2026-07-08 UVC demo-cases TiC sweep" bullet list (i.e. right before the blank comment line that precedes "Checked and found NOT applicable/NOT present"). Start the new block with this header line (only once, above its first bullet):

```python
#
# 2026-07-15 Aetna ALICFI (fully-insured exchange) TiC sweep, verified against the live
# mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com host (Aetna's TiC vendor, HealthSparq/
# Kyruus -- unlike the WAF-protected consumer "find a doctor" site, this MRF host has no auth/WAF):
```

Then one bullet per confirmed hit, in this exact form (fill in `<npi>`, `<name>`, `<tin>`, `<established-via-key>`, `<market-key(s)>`):

```python
#   - Aetna (ALICFI): NPI <npi> (<name>) -> TIN <tin>, matching the already-established TIN via
#     <established-via-key>'s MRF. Added under catalogue key(s) <market-key(s)>.
#     https://mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com/prd/mrf/AETNACVS_I/ALICFI/2026-07-05/tableOfContents/2026-07-05_Aetna-Life-Insurance-Company_index.json.gz
#     (2026-07-05 ALICFI table of contents).
```

For NPI `1902811656` specifically (no established TIN to match), use this variant instead of the standard bullet:

```python
#   - Aetna (ALICFI): NPI 1902811656 -> TIN <tin found>, no prior established TIN to compare
#     against (Anthem's GA file masks the billing TIN behind a representative NPI) -- taken as-is.
#     Added under catalogue key aetna-ga-atlanta.
#     https://mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com/prd/mrf/AETNACVS_I/ALICFI/2026-07-05/tableOfContents/2026-07-05_Aetna-Life-Insurance-Company_index.json.gz
#     (2026-07-05 ALICFI table of contents).
```

Then add one `_SEED` entry per confirmed hit (append to the end of the existing `_SEED` list, before the closing `]`), one row per target catalogue key (two rows for NPIs with two target keys, e.g. `1972603934` gets both `aetna-tx-houston` and `aetna-tx-dallas` rows if confirmed):

```python
    {"payer": "<market-key>", "npi": "<npi>", "tin": "<tin>"},
```

**For every DISCREPANCY**, add one bullet to the same new "2026-07-15 Aetna ALICFI" block instead:

```python
#   - Aetna (ALICFI): NPI <npi> (<name>) appears under TIN <aetna-tin>, which DIFFERS from the
#     already-established TIN <established-tin> for this NPI via <established-via-key>'s MRF.
#     NOT added to _SEED under either TIN -- needs human review before treating either value as
#     authoritative for Aetna specifically.
#     https://mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com/prd/mrf/AETNACVS_I/ALICFI/2026-07-05/tableOfContents/2026-07-05_Aetna-Life-Insurance-Company_index.json.gz
#     (2026-07-05 ALICFI table of contents).
```

No `_SEED` entry for a discrepancy.

**For every NOT FOUND**, add one bullet to the existing "Checked and found NOT applicable/NOT present" section instead (append after the existing BCBS Anthem/Elevance bullet, before the `_SEED = [` line):

```python
#   - Aetna (ALICFI, fully-insured exchange): NPI <npi> (<name>, established TIN <established-tin>
#     via <established-via-key>) does not appear in any of Aetna's 283 ALICFI in-network files
#     (2026-07-05 table of contents) -- either not contracted under this specific Aetna brand, or
#     contracted under a different Aetna brand code (e.g. self-insured ALICSI) not checked in this
#     pass.
```

**Regardless of any pull result**, add this bullet to the same "Checked and found NOT applicable/NOT present" section (this is already known, independent of what the ALICFI pull found):

```python
#   - Aetna (FL-South Florida, IL): not checked -- no UVC-affiliated NPI has been established for
#     either market by any payer's crosswalk entry yet. Needs a client-supplied TIN/NPI for these
#     markets before any payer, including Aetna, can be checked here.
```

- [ ] **Step 5: Add a test for confirmed findings — only if there is at least one CONFIRMED HIT**

If Step 3 classified **zero** NPIs as CONFIRMED HIT, skip this step entirely — do not add an empty or vacuous test (a test that asserts nothing is treated as a defect in this repo's own review rubric). Proceed directly to Step 6.

If there is at least one CONFIRMED HIT, add this test to `tests/test_tin_crosswalk.py`, immediately after the existing `test_default_crosswalk_has_2026_07_08_tic_sweep_findings` function. Include exactly one assertion line per confirmed hit (using its target catalogue key, NPI, and TIN from Step 4) — for example, if `1992078745`/`aetna-az` and `1629339312`/`aetna-co-denver` were both confirmed hits:

```python
def test_default_crosswalk_has_aetna_alicfi_sweep_findings():
    # Real findings from the 2026-07-15 Aetna ALICFI (fully-insured exchange) TiC sweep.
    from network_probe.domain.tin_crosswalk import default_crosswalk

    cw = default_crosswalk()
    assert cw.tins_for("aetna-az", "1992078745") == ["843447602"]
    assert cw.tins_for("aetna-co-denver", "1629339312") == ["475181686"]
```

(Replace the two example assertion lines with one line per actual confirmed hit from Step 4 — same `cw.tins_for("<market-key>", "<npi>") == ["<tin>"]` shape, in catalogue-key order matching the table in this task's header.)

- [ ] **Step 6: Run the affected test files**

Run:
```bash
source .venv/bin/activate && python3 -m pytest tests/test_tin_crosswalk.py tests/test_tic_ingest.py tests/test_pull_tic_index.py -v
```
Expected: all pass — the pre-existing 9 tests in `test_tin_crosswalk.py` (or 10, if Step 5 added one), the pre-existing 14 tests in `test_tic_ingest.py`, and the pre-existing 19 tests in `test_pull_tic_index.py`, all unchanged by this task except the one conditionally-added test.

- [ ] **Step 7: Manual crosswalk load sanity check**

Run:
```bash
source .venv/bin/activate && python3 -c "
from network_probe.domain.tin_crosswalk import default_crosswalk
cw = default_crosswalk()
print('loaded OK, index size:', len(cw._index))
"
```
Expected: `loaded OK, index size: N` where `N` is at least 6 (the pre-existing seed entries) plus however many new `_SEED` rows Step 4 added — confirms the edited file has no syntax errors and loads cleanly.

- [ ] **Step 8: Commit**

```bash
git add src/network_probe/domain/tin_crosswalk.py tests/test_tin_crosswalk.py
git commit -m "$(cat <<'EOF'
feat(tin-crosswalk): add Aetna ALICFI TiC sweep findings

Ran the existing pull_tic_index.py pipeline (unmodified -- already
supports Aetna's MRF shape) against Aetna's verified-live, unprotected
ALICFI (fully-insured exchange) TiC index, filtered to 6 NPIs already
established as UVC-affiliated via other payers' crosswalk entries.
Documents confirmed hits, discrepancies, and not-found results in the
same style as the existing UHC/Cigna/Kaiser/Ambetter sweep, plus an
explicit gap note for FL-South Florida/IL (no established NPI yet from
any payer). No source-code changes -- data + documentation only.
EOF
)"
```

(If Step 5 was skipped because there were zero confirmed hits, `git add` only `src/network_probe/domain/tin_crosswalk.py` — there's nothing to stage in the test file.)

---

## After this plan

- Broader Aetna brand-code sweep (`ALICSI` self-insured, state-specific entities) if `ALICFI` alone left markets uncovered and it's worth pursuing.
- FL-South Florida and IL markets need a client-supplied TIN/NPI before any payer's crosswalk (not just Aetna's) can cover them.
- DocFind reverse-engineering and Aetna Better Health PDF-directory retesting remain separate, deferred research threads from this session.
