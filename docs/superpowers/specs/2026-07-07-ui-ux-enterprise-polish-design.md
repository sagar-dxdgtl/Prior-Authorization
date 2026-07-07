# UI/UX enterprise polish — design

**Date:** 2026-07-07
**Branch:** main
**Status:** approved, ready for implementation plan

## Goal

Feedback on the current `web/` frontend (React + AntD) is that it reads as a generic
admin-panel/"industry standard" build, not an enterprise-grade product. This spec covers a
visual/structural overhaul of the existing app — same functionality, dramatically more
polished presentation — so it clears that bar before we start layering the new demo
(payer-directory + 270/271 + TiC data, OON/in-network display) on a separate branch.

"Enterprise-grade" here means Salesforce-level *polish* (consistent design tokens, spacing,
typography, data density, navigation structure) — not a literal Salesforce Lightning skin or
color clone.

## Non-goals

- No new functional screens, no new API calls, no behavior changes to auth, eligibility
  submission, or result data. This is presentation-layer only.
- No component-library swap. AntD stays; we theme it deeply instead of replacing it or
  hand-rolling components (Tailwind/shadcn-style rewrite was considered and rejected — much
  larger diff/risk for a system that already gives us working forms, validation, and tables).
- No fake/placeholder nav items for phase-2 features (Provider Directory, History). The nav
  rail component is built to accept a list of items; only "Eligibility Check" is registered
  now. Phase 2 extends that list — it does not need the shell rebuilt.

## Approach

Deep Ant Design theming via a real design-token system, plus a new `AppShell` layout
(fixed left nav rail + top bar + content area) that wraps the existing pages. Confirmed with
the user via mockups in the visual brainstorming companion (palette comparison, nav-rail
light/dark comparison, full-page Login + Eligibility mockups) — approved as-is.

## Design tokens

All colors/spacing/type as CSS custom properties in `web/src/index.css` (`:root`), consumed
both directly (raw CSS/inline styles that need them) and via an AntD `ConfigProvider` theme
object built from the same values (`web/src/theme/tokens.ts`) so AntD's internal component
styles (Table, Input, Card hover/focus states, etc.) match without per-component overrides.

**Neutral (slate) scale**
| token | hex | usage |
|---|---|---|
| slate-50 | `#F8FAFC` | page/content background |
| slate-100 | `#F1F5F9` | input fill, hover backgrounds |
| slate-200 | `#E2E8F0` | borders (cards, table rules, rail divider) |
| slate-300 | `#CBD5E1` | input borders |
| slate-400 | `#94A3B8` | muted icons, eyebrow labels, placeholder text |
| slate-500 | `#64748B` | secondary text, inactive nav items |
| slate-600 | `#475569` | form labels |
| slate-700 | `#334155` | body text |
| slate-900 | `#0F172A` | headings, dark surfaces (login left panel) |

**Brand accent — Deep Teal**
| token | hex | usage |
|---|---|---|
| brand-500 | `#0B6E8F` | primary buttons, links, active nav (icon/border), focus rings |
| brand-600 | `#095A76` | hover/pressed state |
| brand-50 | `#E6F3F7` | active-nav tint, info-banner tint |

**Semantic**
| token | text | tint bg |
|---|---|---|
| success | `#12805C` | `#E7F6EF` |
| warning | `#B45309` | `#FEF3E2` |
| danger | `#C0152F` | `#FDECEC` |
| neutral/default tag | slate-500 | slate-100 |

**Typography** — keep the existing system-font stack (no new web-font dependency/network
fetch): `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial,
sans-serif`.
| role | size / weight |
|---|---|
| page title (top bar) | 14px / 700 |
| card title | 13–14px / 700 |
| eyebrow/section label (uppercase) | 10px / 700, 0.4px tracking, slate-400 |
| body | 13px / 400, slate-700 |
| form label | 11–12px / 600, slate-600 |
| table header (uppercase) | 10.5px / 700, slate-400 |
| table cell | 12–13px / 400–600 |

**Spacing** — 4px-based scale: 4, 8, 12, 16, 20, 24, 32, 40, 48.

**Radius** — 6px controls/buttons/inputs; 10px cards; 999px pills/tags.

**Elevation** — cards: `1px solid slate-200` + `box-shadow: 0 1px 2px rgba(15,23,42,0.04)`
(replaces the current heavy `boxShadow: '0 4px 24px rgba(0,0,0,0.08)'` on the login card and
bare `borderRadius`-only cards on Eligibility). Popovers/dropdowns get a slightly stronger
shadow via AntD's `boxShadowSecondary` token, not custom per-component CSS.

## Dependencies

Add `@ant-design/icons` (the standard companion package for AntD, not currently installed) to
replace ad hoc unicode glyphs (`⚕`, `✓`, `▤`) with real vector icons: `FileSearchOutlined` for
the Eligibility Check nav item, `CheckOutlined` for the login feature checklist. The brand
mark itself stays a plain "P" monogram in a rounded square (no icon needed there — simplest
and always renders identically).

## Components

### `AppShell` (new, `web/src/components/AppShell.tsx`)
Wraps authenticated pages. Structure:
- **Left nav rail**, 180–200px, white background, `1px solid slate-200` right border.
  Top: 20×20 rounded-square brand mark ("P" on brand-500) + "PriorAuth" wordmark (replaces
  the ⚕ emoji everywhere it appears). Nav items below as a simple config array
  (`{ key, label, icon, path }`); active item gets `brand-50` background, `brand-500` left
  border (3px) and text/icon color. Only one item registered today: **Eligibility Check**
  (routes to `/`). Bottom-pinned user block: username + role, "Sign out" — replaces the
  current top-nav-bar version of this.
- **Top bar**: white, `1px solid slate-200` bottom border, page title left (from route
  config, not hardcoded per-page). User identity/"Sign out" lives only in the rail footer —
  the top bar shows just the page title, so the two aren't duplicated in both places (the
  mockup showed both; this is a deliberate simplification made while writing this spec).
- **Content area**: `slate-50` background, `20-24px` padding, no artificial max-width (the
  rail already constrains reading width reasonably; full-bleed content reads more like a real
  product than the current centered `1100px` column).

`Login` does not use `AppShell` (it's pre-auth, keeps its own full-bleed split layout).

### `Login` (restyle in place, `web/src/pages/Login.tsx`)
Same two-panel structure and fields as today. Changes: left panel background `slate-900`
(was a teal/dark gradient) with the same brand mark used in the rail, feature checklist items
get small rounded check chips (brand-tinted) instead of a bare `✓` character. Right panel
form card: `slate-200` border + soft shadow (per Elevation above) instead of the heavy
`boxShadow` currently set inline, radius 12px→10px to match the token scale.

### `Eligibility` (restyle in place, `web/src/pages/Eligibility.tsx`)
- Wrapped in `AppShell` instead of its own hand-rolled nav bar (removes `navStyle` and the
  inline top-nav markup).
  - Form card: same fields, regrouped into three labeled sections in one row — **Provider**
  (Payer ID, NPI, Payer Base URL), **Member** (Member ID, First/Last Name, DOB), **Plan &
  Location** (Plan, State, ZIP) — each with a 10px uppercase eyebrow label above its fields.
  Submit button moves into a footer strip (`1px solid slate-100` top border, right-aligned)
  rather than sitting loose under the last row.
- Status row becomes four/five equal-width tiles (Coverage, Network Status, PCP Required,
  Prior Auth, Referral) — each a bordered card with an eyebrow label + pill, replacing the
  current `Row`/`Col` of ad hoc `Tag`s. Pill colors map to the semantic tokens above (not
  AntD's default red/green/orange).
- Network verdict banner: same content (`network_verdict` + `corroboration`), restyled as a
  left-accent-bar banner in `brand-50`/`brand-500` instead of AntD's default blue `Alert`.
- Cost-Share Matrix: same `buildMatrix` data/columns, table restyled — uppercase slate-400
  header row on `slate-50`, subtle zebra striping (`#FAFBFC` on alternate rows), IN/OON
  legend as small colored dots + label instead of colored `Tag`s, cost cells keep the
  bold-amount + muted met/remaining sub-line pattern already in `CostCell` (kept as-is,
  restyled via CSS not structure).
- Empty state (no `result` yet): currently renders nothing below the form. Add a simple
  centered placeholder ("Run a check to see coverage, network status, and cost-share
  details") inside a dashed-border panel, so the page doesn't look broken/unfinished before
  first use.
- Loading state: keep AntD's button `loading` spinner (already there); no skeleton needed —
  the request is fast enough that a skeleton would be over-engineering for this restyle.

### Cleanup (dead code touched by this work)
- Delete `web/src/App.css` — not imported anywhere (leftover Vite template file: `.hero`,
  `.counter`, `#next-steps` etc. don't exist in the current app).
- Delete `web/src/assets/hero.png`, `react.svg`, `vite.svg` — unused Vite template assets,
  confirmed no imports anywhere in `src/`.

## Data flow / error handling

Unchanged. This is a presentation-only pass — same `apiFetch` calls, same
`EligibilityResponse` shape, same `react-toastify` error surface on failure. No new loading
states beyond the empty-state panel described above.

## Testing

- `npm run build` (tsc + vite build) must stay green.
- `npm run lint` (oxlint) must stay green.
- Manual verification in-browser (per project convention for UI changes): start
  `uvicorn network_probe.api:app` + `npm run dev`, log in with the seeded admin credentials,
  run one eligibility check end-to-end, confirm the new shell/form/results render correctly
  and no existing behavior (auth redirect, forced password change, error toasts) regressed.
- No new automated tests needed — no new logic, only markup/styling.

## Rollout

Single PR/commit set on `main`. No feature flag — this replaces the existing screens
directly since there's no behavior change to gate.
