# Payer Provider-Directory URLs — Verification & Policy Review

> Read-only research compiled 2026-06-28. Each directory URL, `robots.txt`, and Terms-of-Use page below was **fetched** to record the live HTTP status and any bot-protection encountered. **No CAPTCHA / WAF / bot-protection / login was bypassed** — where a page blocked automated access, that block is recorded as a data point (status code + protection observed). Fetches were performed by a small fetch-and-summarize tool (single requests, public pages only).
>
> "Loaded OK" = the public entry page returned content. Many directories are JavaScript single-page apps (SPA): the canonical public entry URL loads, but the actual provider search runs client-side. "Bot-protected" / 403 / timeout / socket-hang-up = the host's WAF refused automated requests (it does **not** mean a human browser is blocked).

## Summary table

| Payer | Directory URL | Bot-protection | Terms-of-Use URL | robots.txt note |
|---|---|---|---|---|
| Aetna | https://www.aetna.com/individuals-families/find-a-doctor.html (landing page → DocFind at `/docfind`) | Loaded OK (landing page). DocFind tool itself is Akamai-protected per source notes | https://www.aetna.com/legal-notices/disclaimer.html | Loaded. Disallows `/docfind/` (many lines), `/search/`, `/individuals-families/search-results.html`, `/inyourstate/`, `/members/`. No crawl-delay |
| UnitedHealthcare | https://www.uhc.com/find-a-doctor | **HTTP 403 — Akamai/WAF.** robots.txt itself also 403 | https://www.uhc.com/legal/terms-and-conditions (**403, not retrievable**) | `uhc.com/robots.txt` → 403 (blocked). Provider domain `uhcprovider.com/robots.txt` loads and Disallows `/content/provider/en/find-a-provider-referral-directory/*` |
| Cigna | https://hcpdirectory.cigna.com/ (SPA) | Loaded (page title confirmed); SPA, no block | https://www.cigna.com/legal/compliance/terms-of-use (loads but JS-rendered; terms text not in static HTML) | Subdomain `hcpdirectory.cigna.com/robots.txt` → 404. Main `cigna.com/robots.txt` loads; Disallows `/search*`, `/campaigns/`, `/customer_care/`. No crawl-delay |
| Humana | https://www.humana.com/find-a-doctor | **Timeout / connection dropped (WAF).** robots.txt also timed out | Not retrievable (all `humana.com/legal/*` candidates timed out) | `humana.com/robots.txt` → 60s timeout (WAF blocks automated access) |
| Anthem / Elevance BCBS | https://www.anthem.com/find-a-doctor | **Socket hang-up / timeout (WAF).** robots.txt also timed out | Fallback only: https://www.elevancehealth.com/legal (parent co., loaded OK; no bot-specific language) | `anthem.com/robots.txt` → timeout (not retrievable) |
| Kaiser Permanente | https://healthy.kaiserpermanente.org/front-door (canonical entry; old `/find-a-doctor` returns **HTTP 410 Gone**) | Loaded OK (front-door). No block on public pages | https://www.kaiserpermanente.org/termsconditions | Loaded. Disallows `/northern-california/physicians/`, `/mobile-app/`, `/as/authorization.oauth2`, `/mychartma`. 96+ sitemaps. No crawl-delay |
| Molina Healthcare | https://www.molinahealthcare.com/members/az/en-us/mem/medicaid/helpful-resources/provider.aspx | **HTTP 403 domain-wide (WAF).** Even root + robots.txt return 403 | https://www.molinahealthcare.com/members/common/en-us/legal/termsofuse.aspx (**403, not retrievable**) | `molinahealthcare.com/robots.txt` → 403 (blocked) |
| Ambetter (Centene) | https://www.ambetterhealth.com/en/find-a-provider/ → 301 → https://my.ambetterhealth.com/x/hub/public/en/landing-page (SPA) | Loaded (redirect chain to SPA). No WAF block. NB: old `/find-a-doctor.html` = 404 | https://www.ambetterhealth.com/terms-conditions.html | Loaded. `User-agent: *` / Disallow `/content/` (Allow `/content/*.pdf`, `/content/dam/`). Find-a-provider not disallowed. No crawl-delay |
| Wellcare (Centene) | https://www.wellcarefindaprovider.com/ → 301 → https://my.wellcare.com/x/hub/public/en/landing-page (SPA) | Loaded (redirect chain to SPA). No WAF block. NB: old `/en/find-a-doctor` = 404 | https://www.wellcare.com/en/corporate/legal | `wellcarefindaprovider.com/robots.txt` → 404. `wellcare.com/robots.txt` loads: `Disallow:` empty (all allowed) |
| Oscar | https://www.hioscar.com/care-options | Loaded OK (public search, no login) | Not located at tested paths (`/terms`, `/legal`, `/terms-and-conditions`, `/terms-of-service` all 404 or SPA stub) — check site footer | Loaded. `Allow: /`; Disallows `/search/*`, `/member/*`, `/messaging/*`, `/help/`. `/care-options` allowed. No crawl-delay |
| Devoted Health | https://www.devoted.com/search-providers/ | Loaded OK (public search) | https://www.devoted.com/terms-of-use/ | Loaded. `Allow: /`; Disallows misc paths (`/resources/_article`, `/find-a-plan-v2/*`, year archives). `/search-providers/` allowed. No crawl-delay |
| Alignment Health Plan | https://providersearch.alignmenthealthplan.com/ (SPA) | Loaded (HTTP 200) but JS SPA ("Loading…"); search needs JS rendering | https://www.alignmenthealthplan.com/about-us/terms-of-use | Main `alignmenthealthplan.com/robots.txt` loads; only Disallows `/media/...PDF(s)` dirs. Subdomain serves SPA for all paths (no real robots.txt). No crawl-delay |
| SCAN Health Plan | https://www.scanhealthplan.com/helpful-tools/provider-search | **HTTP 403 domain-wide (WAF).** Root + robots.txt also 403 | https://www.scanhealthplan.com/terms-of-use (**403, not retrievable**) | `scanhealthplan.com/robots.txt` → 403 (blocked) |
| Mercy Care (AZ) | https://www.mercycareaz.org/find-a-provider | **HTTP 403 domain-wide (WAF).** Root + robots.txt also 403 | https://www.mercycareaz.org/legal (**403, not retrievable**) | `mercycareaz.org/robots.txt` → 403 (blocked) |
| AvMed | https://www.avmed.org/find-doctors-facilities/ | Loaded OK (browsing not login-gated; member login optional for filtering) | https://www.avmed.org/en/terms-of-use | Loaded. `User-agent: *` / `Allow: /` — all crawlers, all paths; no Disallow |
| EmblemHealth | https://www.emblemhealth.com/find-a-doctor/find-the-right-care | Loaded OK (care-triage page; deep provider search is member-gated) | https://www.emblemhealth.com/legal/digital-services-privacy-policy-and-terms-of-use → redirects to https://www.emblemhealth.com/legal/privacy-and-security-policies | Loaded. Disallows `/search?*`, `/campaign/*`, `/errors/*`, `/*.xls(x)$`. `/find-a-doctor` allowed. No crawl-delay |
| Aetna Better Health | https://www.aetnabetterhealth.com/find-provider.html | **HTTP 403 domain-wide (WAF).** Root + robots.txt also 403 | https://www.aetnabetterhealth.com/legal (**403, not retrievable**) | `aetnabetterhealth.com/robots.txt` → 403 (blocked) |
| Health Choice Arizona | https://providerdirectory.healthchoiceaz.com/ | **ECONNREFUSED** (TLS connection refused on :443 at 204.153.155.211 — host not responding). Main `www.healthchoiceaz.com` = 403 | https://www.healthchoiceaz.com/terms-of-use (**403, not retrievable**) | `providerdirectory.healthchoiceaz.com/robots.txt` → ECONNREFUSED; `www.healthchoiceaz.com/robots.txt` → 403 |

## Per-payer detail & Terms-of-Use quotes

### Aetna
- **Directory:** `https://www.aetna.com/individuals-families/find-a-doctor.html` loaded OK — a landing page that routes members to login or non-members to the DocFind tool at `aetna.com/docfind` (a subdirectory, not a separate domain). Per the source JSON, DocFind itself is behind Akamai Bot Manager.
- **ToS:** `https://www.aetna.com/legal-notices/disclaimer.html` is Aetna's Terms of Use (found via `/legal-notices.html`; the `/legal.html` and `/legal/terms-of-use.html` guesses 404). Explicit automated-access prohibition in the "Unauthorized conduct" section:
  > "use any robot, spider, site search/retrieval application or other manual or automatic device to retrieve, index, 'scrape' 'data mine'…"
- **robots.txt:** loads; heavily restricts `/docfind/`, `/search/`, `/individuals-families/search-results.html`, `/inyourstate/`, `/members/`. Updated 12/17/2025.

### UnitedHealthcare
- **Directory:** `https://www.uhc.com/find-a-doctor` returned **HTTP 403** (Akamai/WAF). The block extends to `robots.txt` and the legal pages on `uhc.com`.
- **ToS:** `https://www.uhc.com/legal/terms-and-conditions` — could not be retrieved (403). Not verified.
- **robots.txt:** `uhc.com/robots.txt` → 403. The professional/provider domain `uhcprovider.com/robots.txt` *does* load: it allows Googlebot full access and Disallows `/content/provider/en/find-a-provider-referral-directory/*` for `*`.

### Cigna
- **Directory:** `https://hcpdirectory.cigna.com/` loaded (title "Cigna Health Care Provider Directory"); it is a SPA, so the search UI is client-rendered. No 4xx / block.
- **ToS:** `https://www.cigna.com/legal/compliance/terms-of-use` returns HTTP 200 but the terms body is JavaScript-loaded ("Loading…" in static HTML) — the automated-access language could not be quoted from a static fetch.
- **robots.txt:** the directory subdomain has none (404); `www.cigna.com/robots.txt` loads and Disallows `/search*`, `/campaigns/`, `/customer_care/`, partner-portal paths.

### Humana
- **Directory:** `https://www.humana.com/find-a-doctor` — **all fetches timed out (~60s)**, consistent with WAF dropping automated connections. No HTTP status returned.
- **ToS:** every candidate (`/legal/terms-conditions`, `/legal/terms-of-use`, `/terms-of-use`, members subdomain, provider path) timed out or socket-hung-up. **Not verified** — Humana blocks automated HTTP at the edge.
- **robots.txt:** `humana.com/robots.txt` also timed out.

### Anthem / Elevance BCBS
- **Directory:** `https://www.anthem.com/find-a-doctor` — **socket hang-up / timeout** on every attempt (WAF/Akamai). No status returned.
- **ToS:** no `anthem.com` page was retrievable. Parent-company fallback `https://www.elevancehealth.com/legal` loaded OK (copyright/liability/general usage terms) but contained **no bot/crawler/scraping-specific language** in the retrieved content. Anthem's own ToS was not verified.
- **robots.txt:** `anthem.com/robots.txt` timed out (not retrievable).

### Kaiser Permanente
- **Directory:** the old `https://healthy.kaiserpermanente.org/find-a-doctor` now returns **HTTP 410 Gone**. The live public entry point is `https://healthy.kaiserpermanente.org/front-door` (root `kaiserpermanente.org` 302-redirects there). Kaiser is a closed HMO; deep search/scheduling is member-gated.
- **ToS:** `https://www.kaiserpermanente.org/termsconditions` loaded OK with explicit, strong automated-access language:
  > "using any 'deep-link', 'page-scrape', 'robot', 'spider', data mining tools, data gathering and extraction tools, or other automatic device, program, algorithm or methodology, to (1) access, acquire, copy or monitor any portion of the Site…"
  > Violation "shall constitute a material breach of these Terms and Conditions," enforceable by terminating access.
- **robots.txt:** loads; Disallows `/northern-california/physicians/`, `/mobile-app/`, `/as/authorization.oauth2`, `/mychartma`, community-provider docs. 96+ sitemaps (multi-language).

### Molina Healthcare
- **Directory:** `https://www.molinahealthcare.com/members/az/en-us/mem/medicaid/helpful-resources/provider.aspx` → **HTTP 403**. The 403 is **domain-wide**: root, the common provider-search page, robots.txt, and the legal pages all return 403 (WAF, consistent with Akamai).
- **ToS:** `https://www.molinahealthcare.com/members/common/en-us/legal/termsofuse.aspx` → 403. **Not verified.**
- **robots.txt:** 403 (blocked).

### Ambetter (Centene)
- **Directory:** the JSON's `/find-a-doctor.html` now **404s**. The working public entry is `https://www.ambetterhealth.com/en/find-a-provider/`, which **301-redirects to** `https://my.ambetterhealth.com/x/hub/public/en/landing-page` — a JavaScript SPA hub. No WAF block on the chain.
- **ToS:** `https://www.ambetterhealth.com/terms-conditions.html` loaded OK (the `/terms-of-use.html` guess 404'd). No literal "bot/crawler/scraper" wording, but it prohibits interference-style automation:
  > "use any device, software, or routine that interferes with the proper working of the Site"
  > "attempt to gain unauthorized access to, interfere with, damage, or disrupt any parts of the Site, the server on which the Site is stored, or any server, computer, or database connected to the Site"
- **robots.txt:** loads; `User-agent: *` / Disallow `/content/` (with Allow for `*.pdf` and `/content/dam/`). Find-a-provider path not disallowed.

### Wellcare (Centene)
- **Directory:** the JSON's `/en/find-a-doctor` **404s**. Working entry is `https://www.wellcarefindaprovider.com/`, which **301-redirects to** `https://my.wellcare.com/x/hub/public/en/landing-page` (SPA hub; same platform as Ambetter).
- **ToS:** `https://www.wellcare.com/en/corporate/legal` loaded OK. Expressly prohibits systematic scraping/database-building (no literal "bot," but unmistakable in intent):
  > "Create a database by systematically downloading and storing Site content" — prohibited
  > "Reproduce, duplicate, copy, sell, resell or otherwise exploit for any commercial purposes, any portion of … the Site" — prohibited
  > "Take any action that imposes an unreasonable or disproportionately large load on the Site's infrastructure" — prohibited
- **robots.txt:** `wellcarefindaprovider.com/robots.txt` → 404; `wellcare.com/robots.txt` loads with an **empty `Disallow:`** (everything allowed).

### Oscar
- **Directory:** `https://www.hioscar.com/care-options` loaded OK — a public network search ("Search our network | Oscar"), no login required.
- **ToS:** **not located** at the tested paths — `/terms`, `/legal`, `/terms-and-conditions`, `/terms-of-service` all returned 404 or an SPA stub that rendered only the word "Oscar". The correct ToS link is almost certainly in the site footer (JS-rendered) and was not resolvable by static fetch. **Not verified.**
- **robots.txt:** loads; `Allow: /` with Disallows for `/search/*`, `/member/*`, `/messaging/*`, `/help/`. The `/care-options` directory entry is **allowed** (note: `/search/*`, which the tool likely calls, is disallowed).

### Devoted Health
- **Directory:** `https://www.devoted.com/search-providers/` loaded OK (public search tool).
- **ToS:** `https://www.devoted.com/terms-of-use/` loaded OK with an explicit, unambiguous prohibition:
  > "You must not use any robot, spider, or other automatic device, process, or means to access the Services for any purpose, including monitoring or copyright of any of the material on or made available through the Services."
  > "…any manual process to monitor or copy any of the material on or made available through the Services or for any other unauthorized purpose without our prior written consent."
- **robots.txt:** loads; `Allow: /` with Disallows for misc non-directory paths (`/resources/_article`, `/find-a-plan-v2/*`, year archives, `/chat`). `/search-providers/` is **allowed**.

### Alignment Health Plan
- **Directory:** `https://providersearch.alignmenthealthplan.com/` returned HTTP 200 but is a JavaScript SPA (shows "Loading…", title "Provider Search | Alignment Health Plan"); the search UI requires JS rendering.
- **ToS:** `https://www.alignmenthealthplan.com/about-us/terms-of-use` loads but the excerpt only surfaced a link to a "Full Terms of Use" PDF/doc (last modified March 20, 2020); **no bot/crawler language was visible** in the partial content retrieved.
- **robots.txt:** the main domain's robots.txt loads and only Disallows `/media/...PDF(s)` directories — provider-search paths are not mentioned. The `providersearch` subdomain serves the SPA for every path (no genuine robots.txt). No crawl-delay.

### SCAN Health Plan
- **Directory:** `https://www.scanhealthplan.com/helpful-tools/provider-search` → **HTTP 403**, domain-wide (root and robots.txt also 403; WAF, likely Cloudflare-class).
- **ToS:** both `/legal/terms-and-conditions` and `/terms-of-use` → 403. **Not verified.**
- **robots.txt:** 403 (blocked).

### Mercy Care (AZ)
- **Directory:** `https://www.mercycareaz.org/find-a-provider` → **HTTP 403**, domain-wide (root and robots.txt also 403; WAF).
- **ToS:** `/legal` and `/terms-of-use` → 403. **Not verified.**
- **robots.txt:** 403 (blocked).

### AvMed
- **Directory:** `https://www.avmed.org/find-doctors-facilities/` loaded OK — browsing the search options is not login-gated (member login is optional for plan-specific filtering). (Note: the source JSON marked this "login"; the public entry page itself is reachable.)
- **ToS:** `https://www.avmed.org/en/terms-of-use` loaded OK (found via footer). **No** explicit bot/crawler/scraping language; closest general clause:
  > "Any reproduction or redistribution of this material is prohibited without the expressed written consent of AvMed."
- **robots.txt:** loads; `User-agent: *` / `Allow: /` — permits all crawlers on all paths, no Disallow entries.

### EmblemHealth
- **Directory:** `https://www.emblemhealth.com/find-a-doctor/find-the-right-care` loaded OK, but it is a care-triage guide (Teladoc vs. office vs. urgent care vs. ER); the interactive provider-search tool requires member sign-in.
- **ToS:** `https://www.emblemhealth.com/legal/digital-services-privacy-policy-and-terms-of-use` redirects to `https://www.emblemhealth.com/legal/privacy-and-security-policies` and loaded OK with an explicit automation prohibition:
  > "You may not use robots, scraping tools, or other automations to visit the Site, test the Site, or collect information from or about the Site."
- **robots.txt:** loads; Disallows `/search?*`, `/campaign/*`, `/errors/*`, `/*.xls(x)$`. The `/find-a-doctor` path is **not** disallowed. No crawl-delay.

### Aetna Better Health
- **Directory:** `https://www.aetnabetterhealth.com/find-provider.html` → **HTTP 403**, domain-wide (root and robots.txt also 403; WAF).
- **ToS:** `/legal` and `/terms-of-use.html` → 403. **Not verified.**
- **robots.txt:** 403 (blocked).

### Health Choice Arizona
- **Directory:** `https://providerdirectory.healthchoiceaz.com/` → **ECONNREFUSED** (TLS connection refused on port 443 at IP 204.153.155.211 — the subdomain was not responding to HTTPS at fetch time). The main site `www.healthchoiceaz.com` returns **HTTP 403** (WAF).
- **ToS:** `www.healthchoiceaz.com` candidates → 403; `providerdirectory…/terms` → ECONNREFUSED. **Not verified.**
- **robots.txt:** subdomain ECONNREFUSED; `www.healthchoiceaz.com/robots.txt` → 403. Not retrievable on either host.
