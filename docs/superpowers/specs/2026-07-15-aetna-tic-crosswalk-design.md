# Aetna TiC NPI→TIN crosswalk — design

## Summary

Source real NPI→TIN network-participation data for Aetna into `tin_crosswalk.py`'s `_SEED`
list, the hand-curated crosswalk `TinScopeSource` (`src/network_probe/domain/corroboration.py`)
uses as a group-level corroboration signal. This is a stopgap while Aetna's authenticated
provider-directory API remains unavailable (`directory_access="needs-authorized-api"`
in `roster_seed.py`, unchanged by this work — see Scope).

Aetna's public "find a doctor" website was already researched this session and ruled out for
scraping (WAF-protected via Imperva Incapsula, real backend is CVS Health's proprietary OAuth2
API, `robots.txt` disallows the relevant paths). Separately, this session found that Aetna's
federally-mandated Transparency in Coverage (TiC) machine-readable files are hosted by vendor
HealthSparq/Kyruus at an **unprotected** host (`mrf.healthsparq.com`, no WAF, confirmed via
direct fetch) — a completely different, publicly-open data source, unrelated to the WAF'd
website. A live, current index URL was found and verified end-to-end:

```
https://mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com/prd/mrf/AETNACVS_I/ALICFI/2026-07-05/tableOfContents/2026-07-05_Aetna-Life-Insurance-Company_index.json.gz
```

(brand code `ALICFI`, Aetna's fully-insured exchange/marketplace plans, 283
`reporting_structure` entries, files publish monthly on the 5th).

## Prior art in this repo (read, not re-derived)

- `src/network_probe/domain/tin_crosswalk.py` — the actual runtime consumer. A module-level
  `_SEED` list of hand-verified `{"payer": ..., "npi": ..., "tin": ...}` records, each preceded
  by a prose comment documenting the exact MRF URL/date/brand checked and what was found —
  including an explicit "Checked and found NOT applicable/NOT present" section for markets or
  payers where the TiC mandate doesn't apply or the provider wasn't found. `default_crosswalk()`
  loads `_SEED` plus an optional `TIN_CROSSWALK_PATH` bulk file on top.
- `src/network_probe/domain/tic_ingest.py` and `scripts/pull_tic_index.py` — a fully generic,
  already-working TiC index/MRF downloader and NPI→TIN extractor. Handles the exact file shape
  Aetna publishes (`reporting_structure[] → in_network_files[].location`, and the
  `provider_references[].location`-with-external-file variant the code already labels
  "Cigna / modern Aetna style" — verified this session to be real working logic, not a stub).
  **No code changes needed** — this pipeline already supports Aetna's file shape unmodified.
- `src/network_probe/payers/roster_seed.py` — `SOURCES["Aetna"]["tic_url"]` currently points at
  `https://health1.aetna.com/app/public/`, Aetna's WAF-blocked human-facing discovery portal.
  This is consistent with the field's existing convention: Cigna's and Centene's `tic_url`
  entries (`_CIGNA_TIC`, `_CENTENE_TIC`) are also stable compliance-landing pages, not raw dated
  index URLs — dated URLs go stale in ~30 days and don't belong in this field. **No change to
  this file** — the specific, dated, verified-live URL found this session belongs in
  `tin_crosswalk.py`'s per-entry documentation instead, matching where Cigna's own dated URL
  (`https://www.cigna.com/static/mrf/co/latest.json`) already lives in that file's comments.
- Aetna appears in `ROSTER` across 7 markets: AZ, CO-Denver, FL-South Florida, IL, GA-Atlanta,
  TX-Houston, TX-Dallas — all `"Commercial"` benefit type, Stedi id `60054`. Catalogue keys are
  `f"{slug(label)}-{slug(state)}"` (e.g. `aetna-az`, `aetna-co-denver`, `aetna-tx-dallas`).

## Scope

**In scope:**
- Run the existing `pull_tic_index.py` pipeline against the verified live `ALICFI` index URL
  above, filtered to the NPIs already established (via other payers' crosswalk entries) as
  UVC-affiliated providers, across the 5 of 7 Aetna markets that currently have one:
  - `aetna-az`: NPI `1992078745` (established TIN `843447602`, via `unitedhealthcare-az`)
  - `aetna-co-denver`: NPI `1629339312` (established TIN `475181686`, via
    `cigna-healthcare-co-denver`), NPI `1598895435` (established TIN `475181686`, via
    `kaiser-permanente-co-denver`)
  - `aetna-ga-atlanta`: NPI `1902811656` (no established EIN — Anthem's GA file masks the
    billing TIN behind a representative NPI; check for presence only, no TIN to confirm against)
  - `aetna-tx-houston` / `aetna-tx-dallas`: NPI `1972603934` and NPI `1710305735` (established
    TIN `933510922`, via `uhc` and `ambetter-centene-tx-dallas`; the latter NPI also resolves to
    TIN `412049581` under Ambetter)
- No `--tin-file` filter — pull whatever TIN each NPI resolves to in Aetna's file, so a
  different TIN than already on record surfaces as a discrepancy rather than being silently
  filtered out.
- Cross-reference: for each NPI that appears in Aetna's ALICFI data, compare its Aetna TIN
  against the TIN already established for that same NPI in other payers' crosswalk entries.
  Matching TIN = confirmed hit, documented and added to `_SEED`. Different TIN = discrepancy,
  documented but not silently treated as confirmation of either value. No appearance = documented
  in the "Checked and found NOT applicable/NOT present" section, same as the existing Meridian/
  Mercy/Humana/Anthem precedents.
- Explicit documentation that `aetna-fl-south-florida` and `aetna-il` cannot be checked in this
  pass — no UVC-affiliated NPI has been established for either market by *any* payer's crosswalk
  entry yet, so there is nothing to search Aetna's file for. This is a data gap, not something
  this task can close; it needs a client-supplied TIN/NPI for those markets first (from either
  payer).

**Explicitly out of scope:**
- No source-code changes to `tic_ingest.py`, `pull_tic_index.py`, or `roster_seed.py` — the
  pipeline already works unmodified, and `tic_url` already follows its existing convention (see
  Prior art above).
- No broader Aetna brand-code sweep (`ALICSI` self-insured, state-specific entities like "Aetna
  Health of Illinois") — deferred; `ALICFI` alone is checked first and gaps are documented, not
  chased exhaustively in this pass (matches the "single-pass now" choice already made).
- No change to `directory_access` for Aetna in `roster_seed.py` — the TIN crosswalk is an
  additive corroboration signal consumed by `TinScopeSource`, separate from and layered on top
  of whatever the primary directory-access-routed adapter returns; it doesn't change which
  adapter (if any) handles Aetna's primary directory check.
- DocFind (Aetna's legacy in-house directory system) and Aetna Better Health (Medicaid) PDF
  directories — both researched this session as separate potential paths, both explicitly
  deferred, not part of this task.
- Stedi 270/271 eligibility checks for Aetna — already ruled out this session (no per-provider
  network-status fields in live responses).

## Design

### Execution

1. Build a plain-text NPI filter file (one NPI per line, per `pull_tic_index.py`'s
   `--npi-file` format) containing the 6 NPIs listed in Scope above.
2. Run:
   ```
   python scripts/pull_tic_index.py \
     --index-url 'https://mrf.healthsparq.com/aetnacvs-egress.nophi.kyruushsq.com/prd/mrf/AETNACVS_I/ALICFI/2026-07-05/tableOfContents/2026-07-05_Aetna-Life-Insurance-Company_index.json.gz' \
     --npi-file <path> \
     --payer aetna-alicfi \
     --out <path>.csv
   ```
   This downloads and streams all 283 `ALICFI` in-network files (the NPI filter bounds the
   *output*, not the download volume — all 283 files get scanned). Given the volume, this
   execution step is dispatched as a background research fork rather than run inline, matching
   how this session has handled other heavy real-network TiC/MRF fetches.
3. Inspect the resulting CSV. For each `(npi, tin)` row found, compare `tin` against the TIN
   already on record for that NPI in `tin_crosswalk.py`'s existing `_SEED` entries/comments (see
   the mapping in Scope).

### Documentation and `_SEED` update

For each NPI checked, exactly one of three outcomes gets documented in
`tin_crosswalk.py`, matching the file's existing prose-comment-plus-`_SEED`-entry style:

- **Confirmed hit** (Aetna's TIN matches the already-established TIN): add
  `{"payer": "aetna-<market>", "npi": "<npi>", "tin": "<tin>"}` to `_SEED`, with a comment above
  documenting the ALICFI index URL, brand code, file date, and which of the 283 files it was
  found in (by description/plan name).
- **Discrepancy** (Aetna's TIN differs from the established TIN): documented in a comment near
  the relevant `_SEED` entries, explicitly flagging the mismatch — not added as a `_SEED` row
  under either TIN, since neither can be trusted over the other from this evidence alone.
- **Not found**: added to the existing "Checked and found NOT applicable/NOT present" comment
  section, following the exact tone/format of the Meridian/Mercy/Community Health Choice/Humana/
  Anthem entries already there (what was checked, against what NPI, in which file(s), and the
  conclusion).

`aetna-fl-south-florida` and `aetna-il` get a short note in that same section explaining they
have no UVC-affiliated NPI on record from any payer yet, so nothing could be searched for.

## Testing

No new source code, so no new unit tests. `tic_ingest.py`/`pull_tic_index.py`'s existing test
suites are unaffected (unmodified). The verification *is* the cross-reference step above —
manually checking each resulting row against already-established ground truth from other
payers' entries, the same standard every existing `_SEED` entry was held to.

## Follow-ups (not this change)

- Broader Aetna brand-code sweep (`ALICSI`, state entities) if `ALICFI` alone leaves markets
  uncovered and it's worth pursuing further.
- DocFind reverse-engineering and Aetna Better Health PDF-directory retesting — separate
  research threads from this session, own design if pursued.
- FL-South Florida and IL markets need a client-supplied TIN/NPI before *any* payer's crosswalk
  (not just Aetna's) can cover them.
