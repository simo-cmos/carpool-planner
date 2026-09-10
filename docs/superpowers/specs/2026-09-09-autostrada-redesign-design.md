# Autostrada Redesign — Design Spec

Date: 2026-09-09. Status: approved direction (Autostrada theme, Italian driving flavour, merged map
workspace, all fixes from the 2026-09-09 review). Implementation plan:
`docs/superpowers/plans/2026-09-09-autostrada-redesign.md`.

## 1. Why

The organizer UI works but reads as a generic AI-generated dashboard (cream ground, terracotta accent,
rounded cards with rainbow top borders, three copies of the nav). The home page is a marketing page:
returning users see hero copy, a permanently green checklist and buttons that duplicate the nav, and
nothing about the trip they are planning. Pages are split by object type (people/places, routes/results,
invites, history), which duplicates the map on two pages and makes Planning ~6000px tall on desktop and
~9000px on a phone.

Goals, in priority order (from the startup pivot): distinctive design, easier GUI, phone-first feel,
keep the food-stop differentiator, keep every coded feature.

## 2. Information architecture

Five pages, one nav. Old paths redirect so bookmarks, `SCROLL_TARGET_PAGES` and flash redirects keep
working.

| Slug | Path | Label | Content |
|---|---|---|---|
| `home` | `/` | Home | Current trip: exit-sign header, status strip, one next action, checklist while incomplete, recent trips |
| `plan` | `/plan` | Plan | Map workspace: sticky map + side panel with tabs People, Places, Rules, Results |
| `share` | `/share` | Share | Guest invites, public tunnel URLs, current-plan share (report, WhatsApp, per-driver messages) |
| `trips` | `/trips` | Trips | Past trips (load, snapshot, delete) |
| `settings` | `/settings` | Settings | Map defaults, meetup pooling, notifications, password; optimizer parameters under "Advanced" |

Redirects (303): `/setup` → `/plan?tab=people`, `/planning` → `/plan?tab=results`,
`/guest-links` → `/share`, `/history` → `/trips`. `/welcome` (landing) unchanged.

Deleted: the overview hero, stat cards, page-hub cards, the per-page intro banner, and the footer
"Move between app pages" nav. The landing page keeps the marketing role.

### 2.1 Home

Context: `optimization` is loaded on Home as well as Plan (today only on Planning).

- **Exit sign** (signature): destination name in a green sign panel with the exit arrow; trip name and
  target arrival as the second line. Empty state: the sign reads "No destination yet" and the arrow is
  replaced by the next-action link.
- **Status strip** as km-marker tiles: people in tonight's pool, drivers, guest replies pending
  (sum over open invites of `response_count - imported_count`), plan (cars · km · EUR of
  `optimization.best_result`, or "not run yet").
- **Next action**: exactly one primary button, computed server-side by `build_next_action(...)`:
  no destination → "Set a destination" (`/plan?tab=places`); no participants → "Add people"
  (`/plan?tab=people`); no driver → "Mark a driver" (`/plan?tab=people`); no optimization →
  "Run the plan" (POST `/optimization`); otherwise "Share the plan" (`/share`).
- **Checklist** only while any onboarding item is false.
- **Recent trips**: last three history entries with "Load" and "Snapshot".

### 2.2 Plan (merged workspace)

Desktop ≥ 1080px: two columns, `minmax(0, 1fr) 440px`. Map column is `position: sticky`, full
viewport height. Side panel scrolls. Phone: map is 42vh at the top, tabs below.

Tabs (buttons with `role="tab"`, panels toggled with the `hidden` attribute, ~20 lines of JS):

- **People**: quick-add form, participant table, saved crews, CSV tools, guest-session callout with
  "new replies" chip linking to the invite review.
- **Places**: destination form + favorites, meetup spot form + saved spots.
- **Rules**: pickup-order rules, ride-together rules, return-trip planning, planner preset + save trip.
- **Results**: run buttons, driver selection scores, plan cards, route comparison. Each plan card
  gets a "Show on map" button that selects that route set in the map (drives the existing
  `#route-set-select`).

Tab selection: `?tab=` query param wins; otherwise a `scroll` target inside a tab panel activates that
panel; otherwise People. All map toolbar controls remain (search, click target, fit, route option,
food mode, refresh).

### 2.3 Share

Left: create invite, public URLs (unchanged forms). Right: active invites (unchanged). New top card
"Current plan" when an optimization exists: open report, download, WhatsApp, and one message per
driver ("Giulia, you drive: pick up Fabio 18:57, Davide 19:17… arrive 20:00") with copy and
WhatsApp buttons. Built by `_build_driver_messages(chosen_trip, optimization, destination)` next to the
existing `_build_share_summary`.

### 2.4 Settings

One form, two groups: "Map & planning" (map centre, zoom, max meetup self-transfer, meetup pooling,
notifications) always visible; "Advanced optimizer" (`ants`, `iterations`, `alpha`, `beta`, `rho`)
inside a closed `<details>`. Security panel unchanged.

## 3. Visual system — "Autostrada"

The world of Italian driving supplies every device: motorway signage, license plates, km marker
stones, service-area signs, viaducts. Devices encode meaning; nothing is decoration.

### 3.1 Tokens (light)

| Token | Value | Use |
|---|---|---|
| `--road` | `#F5F6F3` | page ground (cool white, not cream) |
| `--paper` | `#FFFFFF` | panels |
| `--ink` | `#1B1F23` | text |
| `--muted` | `#5C6570` | secondary text |
| `--line` | `#D3D8D0` | borders |
| `--sign-green` | `#0E7C3F` | autostrada: primary actions, best route, active tab, exit signs |
| `--sign-green-deep` | `#0A5C2E` | hover/pressed |
| `--sign-green-ink` | `var(--sign-green)` (dark: `#5BC98A`) | green as text on paper (AA in both themes) |
| `--sign-blue` | `#0B4F9C` | strade statali: secondary emphasis, passengers, parking "P", plate band |
| `--sign-brown` | `#7A4A22` | tourist/service signs: food stops ("Area di servizio") |
| `--sign-yellow` | `#F2C230` | temporary works: warnings, pending guest replies |
| `--sign-red` | `#C8102E` | prohibitions: danger buttons, errors |
| `--asphalt` | `#2A2D31` | dark surfaces (km-marker cap, plate text, nav on phone) |

Dark ("night drive"): `--road #1B1D20`, `--paper #24272B`, `--ink #F2F3EF`, `--muted #A9B0B6`,
`--line #3A3F45`, `--sign-green #0E7C3F`. Sign colours stay.

Shape: `border-radius: 6px` everywhere (sign corner), 2px borders, no box-shadows, no gradients, no
rainbow bars. Panels are flat white on the road ground.

### 3.2 Type

- Display: **Overpass** 700/800 (descends from Highway Gothic). Headings, sign panels, buttons, tabs.
  Sign panels use `letter-spacing: 0.01em`, sentence case, never all-caps except eyebrows.
- Body: **IBM Plex Sans** 400/500/600. All numerals `font-variant-numeric: tabular-nums`.
- Loaded from Google Fonts in `_brand_head.html` and `index.html`; fallback `"Bahnschrift", "Segoe UI",
  system-ui, sans-serif`.

### 3.3 Signature components

1. **Exit sign** `.sign` — green panel, white text, white inner rule (`box-shadow: inset 0 0 0 2px
   #fff, inset 0 0 0 5px var(--sign-green)`), an arrow glyph on the right. Used on Home for the
   destination and as the header of every driver card in Results (driver name = exit; passengers listed
   under it like towns with km and time, right-aligned). Loads with a 240ms slide from the right on
   Home only (`prefers-reduced-motion` disables).
2. **Targa** `.targa` — license-plate chip: white, 1px asphalt border, blue left band, uppercase
   Overpass name. Used for participant names in Results and the People table role column
   (drivers get a green band, passengers blue).
3. **Km marker** `.km-marker` — the ANAS cippo: white tile with an asphalt cap, big number, small label.
   Home status strip and the impact strip.
4. **Service area** — brown badge and brown food markers/legend. The food overlay label keeps its
   translated text; the brown colour and a fork-and-knife glyph carry the meaning.
5. **Parking P** — meetup spots use a blue square with white "P" marker (`.meetup-pin`), replacing the
   pixel meetup icon. Destination uses a mini green exit-sign marker.
6. **Viaduct strip** — a 40px-tall inline SVG of repeating arches under the landing hero and above the
   login card. Line art in `--line`, nothing else.
7. **Nav plate** — the brand mark in the top bar is a small Italian plate: blue band + "DM". On phones
   the nav becomes a fixed bottom bar with five glyphs.

### 3.4 Italian brand words

The UI stays fully translated (EN/IT/FR/ES). Exactly three Italian words are used untranslated as
brand labels, always next to a translated explanation or an icon: **Casello** (eyebrow on the login
card), **Area di servizio** (eyebrow on the food-stop legend/select), **Uscita** is *not* written —
the arrow carries it. No flags, no pizza, no Vespa.

### 3.5 Map

OpenStreetMap standard tiles (already switched; no API key). Best plan polyline `--sign-green` 5px,
alternates `--muted` 3px dashed, selected alternate `--sign-blue`. Driver pin: green square with white
letter; passenger: blue square; selection: yellow. Popups and clusters styled with the tokens.

## 4. Copy diet

Always-visible explanatory sentences under section labels move into `title` attributes on the label
(hover/long-press) — the list is in the plan (Task 11). Best-plan card shows cars · km · EUR; the
remaining badges live in the existing details disclosure. Flash/error messages remain English-only
(known gap, unchanged).

## 5. Fixes rolled in

- Landing hero heading is cream on cream (`.landing-hero h1 { color: #fff9ed }` on a light panel).
- Intro-banner eyebrows are yellow on cream (the banner is deleted; the eyebrow style is retokenised).
- `share_plan.html` duplicates the colour tokens inline; it switches to `style.css` tokens.

## 6. Out of scope

Hosting/multi-tenancy, new optimizer work, accounts, translations of flash messages, replacing the
guest invite flow's structure (it only gets the new tokens).

## 7. Testing

`unittest` only. Every task keeps `tests.test_web_app` and `tests.test_source_hygiene` green. New
tripwires: old palette hex values must not survive in `style.css`/templates; every `t("...")` key has
IT/FR/ES entries (existing test); Home renders the right next action per state; old paths redirect;
`/plan` contains all four tab panels and every element id `app.js` looks up.
