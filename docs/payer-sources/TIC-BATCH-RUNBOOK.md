# TiC batch runbook — large in-network MRFs (UHC AZ commercial example)

The streaming ingester (`scripts/ingest_tic.py`) is constant-memory, but big payer MRFs (8–10 GB
compressed) need a box with fast egress + disk — not a laptop/sandbox. This runbook pulls a practice's
contracts (by **TIN**) out of UHC's Arizona **commercial HMO** files. **All public TiC data, no PHI.**

Validated already: the 98 MB AZ **exchange** file (`AZNETWORKEXGN`) ran end-to-end and matched 11 AZ +
2 CO providers. The 8 remaining entity TINs' **commercial** contracts live in the files below.

## Compute
- EC2 spot or Fargate in **us-east-1** (co-located with UHC's Azure East US blob store → fast).
- ~20 GB disk (hold one 9 GB `.gz` at a time), 4 GB RAM is plenty (only the `seen` (npi,tin) set is held).
- Python 3.12 + this repo (`pip install -e .`).

## 1. Practice TINs file (`practice-tins.txt`, one per line — keep OUT of git)
```
843447602
475181686
921600050
843012976
880715104
931867629
463812940
412049581
933510922
834407175
```

## 2. Get the EXACT file URLs from the index (robust — don't hardcode filenames)
UHC's filenames have inconsistent dashes; always read the index:
```bash
IDX="https://transparency-in-coverage.uhc.com/api/v1/uhc/blobs/download/2026-06-01/2026-06-01_UnitedHealthcare-of-Arizona-Inc_index.json"
curl -sL "$IDX" -o az_index.json
# list every in-network file URL + the plan it belongs to:
python - <<'PY'
import json
ix = json.load(open("az_index.json"))
for rs in ix.get("reporting_structure", []):
    plans = ", ".join(p.get("plan_name","") for p in rs.get("reporting_plans", []))
    for f in rs.get("in_network_files", []):
        print(f.get("location",""), "  <=", plans[:80])
PY
```
Target the commercial HMO plans (~8–10 GB each): **Choice-HMO (560), Navigate-HMO (637),
Doctors-Plan-Plus-HMO (698), Core-HMO (577)**. (Index also lists the dated blob index at
`https://transparency-in-coverage.uhc.com/api/v1/uhc/blobs/` if you need other insurers/states.)

## 3. Download + ingest each (filter to the practice TINs)
```bash
URL="<paste a location URL from step 2>"
curl -L "$URL" -o plan.json.gz                       # 8–10 GB; SAS tokens valid until 2030
python scripts/ingest_tic.py plan.json.gz choice.csv \
    --payer uhc --tin-file practice-tins.txt          # streams the .gz; constant memory
rm plan.json.gz                                       # reclaim disk before the next file
```
Repeat per plan (choice.csv, navigate.csv, …). Each output is `npi,tin,payer` for only your providers.

## 4. Combine + activate
```bash
# merge all per-plan CSVs, keep one header, dedupe
{ head -1 choice.csv; tail -q -n +2 *.csv | sort -u; } > uhc-az-crosswalk.csv
```
Point the app at it so `TinScopeSource` corroborates UHC network on every check:
```
TIN_CROSSWALK_PATH=/path/to/uhc-az-crosswalk.csv
```
(Or load it into the `npi_tin` store / DB once that lands.)

## Notes
- **Other states/payers:** same pattern — swap the index URL (each payer publishes its own TiC index;
  see `docs/payer-sources/MATRIX.md` `tic_url`). Aetna/Cigna/Humana indices differ in layout but the
  ingester handles both `provider_references[]` and `in_network[].negotiated_rates[]` shapes.
- **NPI vs TIN:** filtering by **TIN** catches providers even when the file lists individual (not group)
  NPIs — and surfaces those individual NPIs for the directory lane (as the AZ exchange run did).
- **Refresh:** MRFs are monthly (the `2026-06-01` date in the path). Re-run monthly; script it via cron
  once the first full set is validated.
- **Validate matches** against NPPES (`https://npiregistry.cms.hhs.gov/api/?number=<npi>&version=2.1`)
  before trusting a TIN match in production (one AZ NPI flagged as a possible TIN-share in the first run).

## Cigna-style payers (external provider_reference.location files)

Cigna and many modern Aetna plans put their NPI/TIN data in **separate referenced
files** instead of inline `provider_groups`.  The top-level MRF lists only:

```json
{"provider_references": [
    {"provider_group_id": 1, "location": "https://mrf.cigna.com/ref/abc123.json.gz"}
]}
```

The ingester resolves these automatically — **`--resolve-references` is ON by default**.

### Important: geo-restriction

Cigna's provider-reference files are served from a **geo-restricted AWS CloudFront**
distribution.  Requests from outside the US return 403.  Always run the ingester from a
**US IP address** (EC2 / Fargate in `us-east-1` or `us-west-2`) for Cigna and Aetna MRFs.
UHC's Azure CDN is open and works from anywhere.

### Usage

```bash
# Cigna MRF (external refs resolved automatically, default behaviour)
python scripts/ingest_tic.py cigna-plan.json.gz cigna.csv \
    --payer cigna --tin-file practice-tins.txt

# Force inline-only mode (disables resolver — use for UHC or air-gapped environments)
python scripts/ingest_tic.py uhc-plan.json.gz uhc.csv \
    --payer uhc --tin-file practice-tins.txt --no-resolve-references

# Tune concurrency (default 16 threads; raise for high-latency CDN endpoints)
python scripts/ingest_tic.py cigna-plan.json.gz cigna.csv \
    --payer cigna --tin-file practice-tins.txt --max-workers 32
```

The script prints the number of unique NPI→TIN rows written.  Resolver failures per URL
are logged at `WARNING` level; the run continues and all non-failing refs are still written.

## One-command pull from an index (`scripts/pull_tic_index.py`)

`pull_tic_index.py` automates the whole **index → select → download → ingest** pipeline:
it downloads a payer's TiC index (table-of-contents), flattens it, selects the relevant
in-network files (by `--state` / `--plan-contains`), downloads each, and runs the same
`ingest_tic` streamer (incl. Pass-3 external `provider_reference.location` resolution),
filtered to your practice's TINs/NPIs. Output is one deduplicated `npi,tin,payer` CSV.

First use case: **Cigna Arizona** — must run from a **US IP** (its CDN is geo-restricted).

### 1. Get Cigna's signed index URL (on a US IP)

Cigna's machine-readable-files page is JS-rendered, so the index link is not in the raw HTML.
Open it in a browser and copy the link that ends in `…_index.json?…` (a long signed URL):

```
https://www.cigna.com/legal/compliance/machine-readable-files
```

### 2. Preview the files first (`--list`)

```bash
python scripts/pull_tic_index.py \
    --index-url '<signed index url>' --state AZ --list
```

Prints each selected file's location + plan names without downloading anything. If nothing
matches your filter, the script warns and lists every file available in the index.

### 3. Full pull (download + ingest, filtered to your TINs)

```bash
python scripts/pull_tic_index.py \
    --index-url '<signed index url>' --state AZ \
    --payer cigna --tin-file practice-tins.txt --out cigna-az.csv
```

- `practice-tins.txt` — one TIN per line (`#` lines are comments); **keep OUT of git**.
- `cigna-az.csv` — output crosswalk (`npi,tin,payer`), rows deduped across all plan files.
- The script prints the files selected and the unique row count written.

### Flags

| Flag | Purpose |
|------|---------|
| `--index-url` (required) | URL to the TiC index JSON |
| `--out` (required) | Output CSV path |
| `--state` | Case-insensitive substring vs. location URL + plan names + market + description (e.g. `AZ`, `arizona`) |
| `--plan-contains` | Case-insensitive substring vs. plan names + market types |
| `--tin-file` / `--npi-file` | Filter rows to a practice's TINs / NPIs |
| `--payer` | Label written to the `payer` column |
| `--list` | Preview selected files, then exit |
| `--keep` | Keep downloaded temp files after ingestion |
| `--workdir` | Directory for temp downloads (defaults to OS temp) |
| `--max-workers` | Concurrency for downloads + resolver (default 16) |

### Notes

- **US IP required for Cigna/Aetna:** the provider-reference CloudFront CDN returns 403 outside
  the US. Run from EC2/Fargate in `us-east-1`/`us-west-2`. UHC's Azure CDN is open everywhere.
- **Signed-URL expiry:** Cigna's index URLs are time-limited — copy a fresh one each run.
- **Other payers:** the same command works for any CMS-compliant TiC index — just swap
  `--index-url` and `--payer`.

## Anthem/Elevance: TIN masked behind a "representative NPI"

Anthem/Elevance's own MRFs (confirmed on their GA commercial in-network files, and a known,
documented industry-wide issue — see [Serif Health's MRF-quality writeup](https://www.serifhealth.com/blog/learnings-from-mrf-land),
which calls out this exact behavior) do **not** publish a real EIN in `provider_references[].tin`.
Instead `tin.type` is `"npi"`, and the value is a single **representative NPI** shared by every
provider in that group — e.g. one real physician's own billing group had `tin.type="npi",
value="1619681244"` instead of an EIN, with zero `"type":"ein"` entries anywhere in the file
(3.1M+ tin objects checked). `ingest_tic.py`/`pull_tic_index.py` cannot extract a TIN from these
files for this reason — there is no TIN in them to extract.

**Workaround (partial — confirms the group, not the exact EIN):**

1. Look up the representative NPI in NPPES (`https://npiregistry.cms.hhs.gov/api/?number=<npi>&version=2.1`).
   It resolves to a real **Type 2 (organization) NPI** — in the case checked, "GEORGIA UVC MEDICAL LLC"
   (dba United Vein & Vascular Centers), a real, active, operating practice group.
2. Independently look up the target physician's own registered group/practice affiliation —
   NOT via NPPES (individual/Type-1 NPI records don't carry group affiliation), but via **CMS's
   "Doctors and Clinicians" National Downloadable File** (dataset `mj5m-pzi6` on
   `data.cms.gov/provider-data` — the same data that powers `medicare.gov/care-compare`, queryable
   directly even when the Care Compare *website* 403s automated fetches). It returns an
   `org_pac_id` and group/practice name for the physician.
3. If (1) and (2) name the same organization (same LLC name, same suite-level address, same org
   PAC ID) — as happened in this check — that's real, independent confirmation of *which group*
   the physician bills under, obtained without Anthem's file ever revealing a TIN.

**What this does NOT give you:** proof that a specific numeric EIN belongs to that group. Private,
for-profit LLC EINs are not published in any free public source — checked and confirmed absent
from SEC EDGAR full-text search, IRS Exempt-Orgs BMF / ProPublica Nonprofit Explorer (LLCs aren't
tax-exempt orgs, so this only ever works for nonprofits), OpenCorporates' public search, and a
general web search for the exact EIN string. Georgia's Secretary of State registry is searchable
by entity name, not by EIN, so it can confirm an LLC's *existence* by name but not link a specific
EIN to it either. The only known way to close that last link is a paid commercial EIN-reverse-
lookup API (Serif Health's own writeup notes these are "decently expensive pay-to-play" — not
integrated here). Treat a group-name match from this technique as strong supporting evidence for
TIN-scope corroboration, not as a standalone `tin_crosswalk.py` seed entry on its own.
