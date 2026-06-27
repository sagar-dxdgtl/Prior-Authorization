# DISCOVERY-devoted.md — Devoted Health "Find a Provider" endpoint contract

**Discovered:** 2026-06-22, by live inspection of `https://www.devoted.com/search-providers`
(guest access). Second payer; see `DISCOVERY.md` for Oscar and the shared probe design.
**Payer:** Devoted Health (Medicare Advantage) · **Coverage year:** 2026

---

## TL;DR

- Devoted's directory is a public **Algolia** index. Search is a normal Algolia
  `POST /1/indexes/*/queries` using a **public, read-only InstantSearch key** embedded in the
  page (not a secret — InstantSearch keys are meant to ship to the browser). Works headless with
  no cookies (verified via `curl`).
- **Network scoping is one filter:** `NetworkNames:"<STATE> <PLANTYPE>"` (e.g. `"FL HMO"`,
  `"TX PPO CSNP"`, `"OH HMO DSNP"`) plus `DirectoryYear:"2026"` and, in the UI, `NetworkCountyCodes`.
- **NPI is directly searchable** as the Algolia `query` and returns only exact-NPI records — much
  cleaner than Oscar (no name fuzz, no 10-result cap). We still filter hits by exact `Npi`
  defensively.
- **Cross-payer ground truth:** Kyle A Herron, MD (NPI `1679766943`) is **OUT** of Oscar's FL HMO
  plan but **IN** Devoted's `FL HMO` network (13 location records, Palm Beach county 10490 covered).
  Jessica Herron (NPI `1568741320`) is **not in Devoted's directory at all** → OUT_OF_NETWORK.

---

## Protection assessment

| Check | Result |
|---|---|
| Auth / cookies required | **No** — public Algolia search key; `curl` with no cookies returns 200 |
| WAF / CAPTCHA / bot challenge | **None seen** |
| Key type | Algolia **secured search key** (base64; embeds `attributesToRetrieve=["*","-internalUseOnly"]`) — read-only by design |

**Not blocked. Proceed.** (Datadog RUM + GA analytics fire, but are irrelevant to the search path.)

---

## The endpoint

`POST https://en2fbm9o9o-dsn.algolia.net/1/indexes/*/queries`

**Headers**
```
x-algolia-application-id: EN2FBM9O9O
x-algolia-api-key: <public search key — see network_probe/adapters/devoted.py DEFAULT_API_KEY>
content-type: application/x-www-form-urlencoded
```

**Body** (Algolia multi-query envelope; `params` is a urlencoded query string)
```json
{"requests":[{"indexName":"provider_directory_quality_score_ranked",
  "params":"query=<NPI or name>&filters=DirectoryYear%3A%222026%22%20AND%20NetworkNames%3A%22FL%20HMO%22&hitsPerPage=50&attributesToRetrieve=[...]"}]}
```

Key `params`:
- **`query`** — the NPI (preferred; exact) or a name.
- **`filters`** — `DirectoryYear:"2026" AND NetworkNames:"FL HMO"` (+ optionally `NetworkCountyCodes:"10490"`).
- `aroundLatLng` / `aroundRadius=all` — zip centroid, used by the UI for ranking (not filtering).

**Response** — `results[0].hits[]`, each a directory record:
- identity: **`Npi`** (string), **`Npis`** (array), **`ProviderName`**, `DirectorySpecialty`, `PracticeName`
- network: **`NetworkNames`** (e.g. `["FL HMO"]`), `NetworkIDs`, **`NetworkCountyCodes`**, `NetworkState`,
  `HasHMO`/`HasPPO`/`HasCSNP`/`HasDSNP`, `DirectoryYear`, `StartDate`
- location: `AddressCity/State/Zip`, `_geoloc`
- `results[0].nbHits`, `nbPages`, `facets`

> A provider can appear as multiple hits (one per office location). All carry the same `Npi`.

### Network/plan model
The UI funnels: **State → Plan Type (HMO / HMO C-SNP / HMO D-SNP / PPO) → Zip → County**. That maps to
the `NetworkNames` value `"<STATE> <PLANTYPE>[ CSNP|DSNP]"`. The live facet (`facets=["NetworkNames"]`,
filtered to `DirectoryYear:"2026"`) enumerates the ~90 valid values; the probe validates the composed
name against this facet so it never queries a non-existent network.

---

## Verdict logic the probe implements (Devoted)

1. **Resolve network:** `state` + plan type from `plan_hint` → `"<STATE> <PLANTYPE>[ CSNP|DSNP]"`,
   validated against the live `NetworkNames` facet. Unresolvable → `UNKNOWN`.
2. **Require NPI** (Devoted name search is fuzzy and returns duplicate records). No NPI → `UNKNOWN`.
3. **Query `query=<NPI>`** filtered to `DirectoryYear + NetworkNames`; keep hits where `Npi`/`Npis`
   exactly equals the target.
   - ≥1 exact hit → **IN_NETWORK** (high); report location-record count + county coverage.
4. **If none**, re-query filtered to `DirectoryYear` only:
   - exact hit exists (other network) → **OUT_OF_NETWORK** (high): "in Devoted, not in `<net>`".
   - no exact hit anywhere → **OUT_OF_NETWORK** (medium) + caveat that Devoted says some NP/PAs may
     be in-network yet unlisted.

---

## Known limitations / gotchas

- **`Npis` is not a filterable attribute** — filtering `Npis:"<npi>"` silently returns 0. Use `query`
  + exact-match the returned hits (what the probe does).
- **County precision:** the probe filters by `NetworkNames + DirectoryYear`, not county. It reports the
  matched provider's `NetworkCountyCodes` in the notes; a future refinement would map the member's zip
  to Devoted's internal county code (e.g. 33409 → `10490` Palm Beach) and filter on it.
- **Public key may rotate.** App ID + key are overridable via `DEVOTED_ALGOLIA_APP_ID` /
  `DEVOTED_ALGOLIA_API_KEY` env vars; if Devoted rotates them, re-capture from the page.
- **NP/PA unlisted caveat** (Devoted's own notice) → "not found anywhere" is OON *medium*, not high.

## Evidence (`./.discovery/`, fixtures in `tests/fixtures/devoted-*.json`)
- `devoted-kyle-flhmo.json` — Kyle Herron IN FL HMO (13 records).
- `devoted-jessica-flhmo.json` / `devoted-jessica-2026-anynet.json` — Jessica absent (OON).
- `devoted-facets.json` — `NetworkNames` facet values used for resolution.
