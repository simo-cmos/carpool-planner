# Autostrada Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the organizer UI into a five-page, trip-centred app (Home · Plan · Share · Trips · Settings) with a merged map workspace, and restyle everything in the "Autostrada" visual system (Italian motorway signage), keeping every existing feature.

**Architecture:** Structure first, skin second. Tasks 2–7 change the page model, templates and a little Python (page slugs, a next-action helper, per-driver share messages) without touching colours. Tasks 8–10 replace the visual layer in `style.css` plus the shared head partial, then add the signature components. Task 11 trims copy. No new runtime dependencies, no JS build step; the tab switcher and "show on map" buttons add ~30 lines to the existing IIFE.

**Tech Stack:** Python 3.12, FastAPI + Jinja2, SQLite, Leaflet 1.9 via CDN, plain `app.js` IIFE, `unittest`, Google Fonts (Overpass, IBM Plex Sans), OpenStreetMap tiles.

**Spec:** `docs/superpowers/specs/2026-09-09-autostrada-redesign-design.md`

## Global Constraints

- **Python:** `.venv\Scripts\python.exe`. Tests are `unittest`, never `pytest`.
- **Test command (every task):** `.venv\Scripts\python.exe -m unittest tests.test_web_app tests.test_source_hygiene` — must end `OK`.
- **No new runtime dependencies.** `requirements.txt` stays: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `qrcode`.
- **No JS toolchain.** `app/static/app.js` stays one IIFE loaded by `<script src>`.
- **Translations:** every new `t("...")` key in a template needs an `it`, `fr`, `es` entry in `app/i18n.py::TRANSLATIONS`; `tests.test_source_hygiene` fails otherwise. Labels that reach templates from Python (`APP_PAGES`, `build_next_action`, `WORKSPACE_TABS`) are not caught by that test — add them by hand in the task that introduces them.
- **Italian brand words** (spec §3.4): only `Casello` and `Area di servizio`, untranslated, as eyebrows. Nothing else in Italian outside the `it` dict.
- **Palette:** only the tokens in spec §3.1. No `#ef6a3a`, `#f2bf4d`, `#4ebd91`, `#f4ede2`, `#0f1d35` may survive (Task 8 adds a tripwire).
- **Bump `STATIC_ASSET_VERSION`** in `app/main.py` (currently `"20260909-osm-tiles"`) in every task that edits `app.js` or `style.css`. Use `"20260909-autostrada-<task>"`.
- **ASCII only in log strings** (Windows cp1252 console). Templates/CSS are UTF-8.
- **Branch:** `autostrada`, created in Task 0. One commit per task, message ends with
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- **Screenshots:** `playwright-cli` is installed globally. To look at a page without the organizer
  password, run a throwaway instance: `$env:DMPROJECT_DATA_DIR="<scratch>\data"; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8765`, then `Invoke-WebRequest -Method POST http://127.0.0.1:8765/sample` and `.../optimization`. Stop it afterwards; never touch the instance on port 8000.

---

## File Structure

| File | Responsibility | Touched by |
|---|---|---|
| `app/main.py` | `APP_PAGES`, `SCROLL_TARGET_PAGES`, redirects, context builder, `build_next_action`, `_build_driver_messages` | T2, T3, T5, T6 |
| `app/templates/index.html` | The organizer UI (all five pages) | T3, T4, T5, T6, T7, T9, T11 |
| `app/templates/_brand_head.html` | Shared `<head>` bits: fonts, manifest, theme bootstrap | T8 |
| `app/templates/_topbar.html` | **New.** Single nav, included by `index.html` | T4 |
| `app/templates/landing.html`, `login.html` | Marketing + login | T1, T10 |
| `app/templates/share_plan.html` | Share report (drops its private token block) | T8 |
| `app/static/style.css` | Whole visual layer | T1, T4, T5, T8, T9, T10 |
| `app/static/app.js` | Tabs, show-on-map, marker icons | T5, T9 |
| `app/static/viaduct.svg` | **New.** Arch strip for landing/login | T10 |
| `app/i18n.py` | Translations | T2–T7, T9–T11 |
| `tests/test_web_app.py` | Route tests | T2, T3, T5, T6, T7, T9 |
| `tests/test_source_hygiene.py` | Palette tripwire, DOM-id tripwire | T5, T8 |
| `README.md`, `docs/IMPROVEMENT_PLAN.md` | Docs | T12 |

---

### Task 0: Branch

- [ ] **Step 1: Create the branch**

```bash
git checkout -b autostrada
git status
```

Expected: `On branch autostrada`, clean tree except the untracked `.playwright-mcp/` folder.

---

### Task 1: Contrast fixes that ship even if nothing else does

**Files:**
- Modify: `app/templates/landing.html` (the `.landing-hero h1` rule in its `<style>`)
- Modify: `app/static/style.css` (`.eyebrow`)
- Modify: `app/main.py` (`STATIC_ASSET_VERSION`)

- [ ] **Step 1: Landing heading**

In `landing.html`, the hero section is `class="panel hero-copy landing-hero"`. `.hero-copy` paints a dark background in `style.css`, but the page-level `.panel` rule wins and the panel is light, so `color: #fff9ed` is invisible. Change the rule in the template's `<style>` to:

```css
.landing-hero h1 {
    font-size: clamp(2.6rem, 6vw, 4.4rem);
    line-height: 1.02;
    color: var(--ink);
    margin-bottom: 16px;
}
.landing-hero .lede {
    max-width: 56ch;
    margin: 0 auto 26px;
    color: var(--muted);
    font-size: 1.12rem;
}
```

- [ ] **Step 2: Eyebrow**

In `style.css`, `.eyebrow` uses `color: rgba(255, 242, 219, 0.92)` (designed for the dark hero, unreadable on cream intro banners). Change to `color: var(--muted);` and delete the `.eyebrow::before` gradient rule.

- [ ] **Step 3: Bump `STATIC_ASSET_VERSION` to `"20260909-autostrada-t1"`, run tests, screenshot `/welcome`, commit**

```bash
git commit -am "fix: landing heading and eyebrow labels were unreadable on light panels"
```

---

### Task 2: Page model — five slugs, legacy redirects, optimization on Home

**Files:**
- Modify: `app/main.py` (`APP_PAGES`, `SCROLL_TARGET_PAGES`, `_normalize_app_page`, `_infer_request_page`, page routes at ~770–800, `home()` ~1324, `_render_app_page` ~855, all `page="..."` call sites)
- Modify: `app/i18n.py`
- Test: `tests/test_web_app.py`

**Interfaces:**
- Produces: slugs `home | plan | share | trips | settings`; `active_tab: str` in the template context; GET `/plan`, `/share`, `/trips`; 303 redirects from `/setup`, `/planning`, `/guest-links`, `/history`.

- [ ] **Step 1: Failing tests**

Append to `WebAppTests`:

```python
    def test_legacy_page_paths_redirect_to_new_pages(self) -> None:
        for old_path, new_location in {
            "/setup": "/plan?tab=people",
            "/planning": "/plan?tab=results",
            "/guest-links": "/share",
            "/history": "/trips",
        }.items():
            response = self.client.get(old_path, follow_redirects=False)
            self.assertEqual(response.status_code, 303, old_path)
            self.assertEqual(response.headers["location"], new_location, old_path)

    def test_new_pages_render(self) -> None:
        for path in ("/", "/plan", "/share", "/trips", "/settings"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('aria-current="page"', response.text, path)

    def test_plan_tab_comes_from_query_or_scroll_target(self) -> None:
        self.assertIn('data-active-tab="rules"', self.client.get("/plan?tab=rules").text)
        self.assertIn('data-active-tab="places"', self.client.get("/plan?scroll=destination-panel").text)
        self.assertIn('data-active-tab="people"', self.client.get("/plan").text)
```

Also update the three existing tests `test_setup_page_renders_trip_setup_sections`, `test_planning_page_renders_map_workspace`, `test_map_pages_agree_on_which_elements_exist` to request `/plan` instead of `/setup` / `/planning`. In the first one delete the two `assertNotIn("Run ...")` lines (the merged page has the run buttons) and change `assertIn("Trip Setup", ...)` to `assertIn("Participants", ...)`. In the third, both halves now fetch `/plan` and both must contain `id="planner-map"` and `id="map-summary"`. Change `test_home_page_renders` to also assert `"Current trip"` is present.

- [ ] **Step 2: Run tests, confirm the new ones fail** (`404` on `/plan`, missing `data-active-tab`).

- [ ] **Step 3: Replace the page tables in `app/main.py`**

```python
APP_PAGES = {
    "home": {"label": "Home", "title": "Current trip", "description": "Where tonight's trip stands.", "path": "/"},
    "plan": {"label": "Plan", "title": "Plan", "description": "Map, people, places, rules, and results.", "path": "/plan"},
    "share": {"label": "Share", "title": "Share", "description": "Guest invites and the plan to send.", "path": "/share"},
    "trips": {"label": "Trips", "title": "Past trips", "description": "Load or delete past trips.", "path": "/trips"},
    "settings": {"label": "Settings", "title": "Settings", "description": "Defaults and notifications.", "path": "/settings"},
}
LEGACY_PAGE_REDIRECTS = {
    "/setup": "/plan?tab=people",
    "/planning": "/plan?tab=results",
    "/guest-links": "/share",
    "/history": "/trips",
}
WORKSPACE_TABS = [
    {"slug": "people", "label": "People"},
    {"slug": "places", "label": "Places"},
    {"slug": "rules", "label": "Rules"},
    {"slug": "results", "label": "Results"},
]
# Which workspace tab a scroll target lives in. None = the map column (no tab change).
SCROLL_TARGET_TABS = {
    "destination-panel": "places",
    "meetup-spots-panel": "places",
    "participants-panel": "people",
    "map-panel": None,
    "driver-selection-section": "results",
    "optimization-results-section": "results",
    "planner-return-planning": "rules",
    "planner-pickup-rules": "rules",
    "planner-ride-together-rules": "rules",
}
SCROLL_TARGET_PAGES = {
    **{target: "plan" for target in SCROLL_TARGET_TABS},
    "guest-invites-panel": "share",
    "advanced-settings-panel": "settings",
    "trip-history-panel": "trips",
}
```

In `_normalize_app_page` and `_infer_request_page`, replace the three `return "overview"` fallbacks with `return "home"`.

- [ ] **Step 4: Rewrite the page routes**

Replace the `/setup`, `/planning`, `/guest-links`, `/history` GET handlers with:

```python
@app.get("/plan")
def plan_page(request: Request):
    """Render the map workspace."""
    return _render_app_page(request, "plan")


@app.get("/share")
def share_page(request: Request):
    """Render guest invites and plan sharing."""
    return _render_app_page(request, "share")


@app.get("/trips")
def trips_page(request: Request):
    """Render past trips."""
    return _render_app_page(request, "trips")


for _old_path, _new_location in LEGACY_PAGE_REDIRECTS.items():
    app.add_api_route(
        _old_path,
        (lambda location: (lambda: RedirectResponse(location, status_code=303)))(_new_location),
        methods=["GET"],
        include_in_schema=False,
    )
```

`home()` ends with `return _render_app_page(request, "home")`.

- [ ] **Step 5: `_render_app_page` loads results on Home and Plan and resolves the tab**

```python
def _render_app_page(request: Request, page: str):
    """Render one top-level app page with whatever planner results are still valid."""
    flash = request.query_params.get("flash", "")
    scroll_target = request.query_params.get("scroll", "")
    wants_results = page in {"home", "plan"}
    optimization = get_cached_optimization_result() if wants_results else None
    selection = optimization or (get_cached_selection_result() if wants_results else None)
    active_tab = request.query_params.get("tab") or SCROLL_TARGET_TABS.get(scroll_target) or "people"
    if active_tab not in {tab["slug"] for tab in WORKSPACE_TABS}:
        active_tab = "people"
    return render_home(
        request,
        active_page=page,
        success=FLASH_MESSAGES.get(flash),
        scroll_target=scroll_target or None,
        selection=selection,
        optimization=optimization,
        active_tab=active_tab,
        workspace_tabs=WORKSPACE_TABS,
    )
```

`render_home` is also called from error paths (`status_code=400`) without `active_tab`; add `"active_tab": "people", "workspace_tabs": WORKSPACE_TABS` as defaults in the big context dict (before `**extra`, so explicit values win).

- [ ] **Step 6: Rename the 31 `page=` call sites**

```bash
sed -i 's/page="setup"/page="plan"/g; s/page="planning"/page="plan"/g; s/page="guest-links"/page="share"/g; s/page="history"/page="trips"/g' app/main.py
grep -n 'page="setup"\|page="planning"\|page="guest-links"\|page="history"\|"overview"' app/main.py
```

Expected: no matches.

- [ ] **Step 7: Template placeholders so tests can pass now**

In `index.html`, replace the six `{% set is_... %}` lines with:

```jinja
{% set is_home = active_page == 'home' %}
{% set is_plan = active_page == 'plan' %}
{% set is_share = active_page == 'share' %}
{% set is_trips = active_page == 'trips' %}
{% set is_settings = active_page == 'settings' %}
```

Then, with sed-style replace-all inside the template: `is_overview` → `is_home`; `is_planning or is_setup` → `is_plan`; `is_setup` → `is_plan`; `is_planning` → `is_plan`; `is_guest_links` → `is_share`; `is_history` → `is_trips`. Add `data-active-tab="{{ active_tab }}"` to the `<main class="page" ...>` tag. (The real tab markup arrives in Task 5; this attribute is what the test checks and what `app.js` will read.) Replace `{{ t("Trip Assembly") if is_plan else t("Map Workspace") }}` with `{{ t("Trip Assembly") }}`.

- [ ] **Step 8: Translations**

Add to each of `it`, `fr`, `es` in `TRANSLATIONS`:

```python
        "Home": "Home",  # it
        "Current trip": "Viaggio attuale",
        "Where tonight's trip stands.": "A che punto è il viaggio di stasera.",
        "Plan": "Pianifica",
        "Map, people, places, rules, and results.": "Mappa, persone, luoghi, regole e risultati.",
        "Share": "Condividi",
        "Guest invites and the plan to send.": "Inviti ospiti e piano da inviare.",
        "Trips": "Viaggi",
        "Past trips": "Viaggi passati",
        "People": "Persone",
        "Places": "Luoghi",
        "Rules": "Regole",
        "Results": "Risultati",
```

```python
        "Home": "Accueil",  # fr
        "Current trip": "Trajet en cours",
        "Where tonight's trip stands.": "Où en est le trajet de ce soir.",
        "Plan": "Planifier",
        "Map, people, places, rules, and results.": "Carte, personnes, lieux, règles et résultats.",
        "Share": "Partager",
        "Guest invites and the plan to send.": "Invitations et plan à envoyer.",
        "Trips": "Trajets",
        "Past trips": "Trajets passés",
        "People": "Personnes",
        "Places": "Lieux",
        "Rules": "Règles",
        "Results": "Résultats",
```

```python
        "Home": "Inicio",  # es
        "Current trip": "Viaje actual",
        "Where tonight's trip stands.": "Cómo va el viaje de esta noche.",
        "Plan": "Planificar",
        "Map, people, places, rules, and results.": "Mapa, personas, lugares, reglas y resultados.",
        "Share": "Compartir",
        "Guest invites and the plan to send.": "Invitaciones y plan para enviar.",
        "Trips": "Viajes",
        "Past trips": "Viajes anteriores",
        "People": "Personas",
        "Places": "Lugares",
        "Rules": "Reglas",
        "Results": "Resultados",
```

- [ ] **Step 9: Run tests → `OK`. Commit** `feat: five-page model with legacy redirects`.

---

### Task 3: Home page

**Files:**
- Modify: `app/main.py` (new `build_next_action`, context fields `next_action`, `pending_guest_replies`, `onboarding_complete`)
- Modify: `app/templates/index.html` (the `{% if is_home %}` hero + checklist + hub blocks)
- Modify: `app/i18n.py`
- Test: `tests/test_web_app.py`

**Interfaces:**
- Produces: `build_next_action(onboarding: dict[str, bool], has_plan: bool) -> dict[str, str]` with keys `label`, `href`, optional `method`.

- [ ] **Step 1: Failing tests**

```python
    def test_home_next_action_follows_trip_state(self) -> None:
        from app.main import build_next_action

        empty = {"has_destination": False, "has_participants": False, "has_driver": False, "has_history": False}
        self.assertEqual(build_next_action(empty, has_plan=False)["href"], "/plan?tab=places")
        self.assertEqual(build_next_action(empty | {"has_destination": True}, has_plan=False)["href"], "/plan?tab=people")
        ready = {"has_destination": True, "has_participants": True, "has_driver": True, "has_history": False}
        self.assertEqual(build_next_action(ready, has_plan=False), {"label": "Run the plan", "href": "/optimization", "method": "post"})
        self.assertEqual(build_next_action(ready, has_plan=True)["href"], "/share")

    def test_home_shows_destination_sign_and_pending_replies(self) -> None:
        self.client.post("/sample")
        home = self.client.get("/").text
        self.assertIn('id="home-sign"', home)
        self.assertIn("Modena Centro", home)
        self.assertIn("Run the plan", home)
        self.assertNotIn("First-Run Checklist", home)  # sample data completes it except history
        self.assertNotIn("Roadtrip Control Room", home)
```

(The checklist is hidden only when all four items are done; the sample dataset leaves `has_history` false, so change the `assertNotIn("First-Run Checklist"...)` line to `assertIn("Save your first trip", home)`.)

- [ ] **Step 2: `build_next_action` in `app/main.py`** (place right after `SCROLL_TARGET_PAGES`)

```python
def build_next_action(onboarding: dict[str, bool], has_plan: bool) -> dict[str, str]:
    """Pick the single most useful next step for the home page."""
    if not onboarding["has_destination"]:
        return {"label": "Set a destination", "href": "/plan?tab=places"}
    if not onboarding["has_participants"]:
        return {"label": "Add people", "href": "/plan?tab=people"}
    if not onboarding["has_driver"]:
        return {"label": "Mark a driver", "href": "/plan?tab=people"}
    if not has_plan:
        return {"label": "Run the plan", "href": "/optimization", "method": "post"}
    return {"label": "Share the plan", "href": "/share"}
```

In the context builder, after `onboarding` is computed (it is inline in the dict today — hoist it into a local `onboarding = {...}` first), add:

```python
        "onboarding": onboarding,
        "onboarding_complete": all(onboarding.values()),
        "next_action": build_next_action(onboarding, extra.get("optimization") is not None),
        "pending_guest_replies": sum(
            max(int(invite.get("response_count") or 0) - int(invite.get("imported_count") or 0), 0)
            for invite in trip_invites
            if invite.get("status") == "open"
        ),
```

- [ ] **Step 3: Replace the Home markup**

Delete everything between `{% if is_home %}` … `{% else %}` (the hero) and the `{% else %}` … `{% endif %}` page-intro block, and the second `{% if is_home %}` block (checklist + page-hub). Insert, right after the nav include point:

```jinja
        {% if is_home %}
        <section class="sign sign-hero" id="home-sign">
            {% if destination %}
            <div class="sign-body">
                <h1>{{ destination.name }}</h1>
                <p>{% if current_dataset %}{{ current_dataset.dataset_name }}{% else %}{{ t("Current trip") }}{% endif %}{% if destination.target_arrival_time %} · {{ t("arrive") }} {{ destination.target_arrival_time }}{% endif %}</p>
            </div>
            <span class="sign-arrow" aria-hidden="true">➚</span>
            {% else %}
            <div class="sign-body">
                <h1>{{ t("No destination yet") }}</h1>
                <p>{{ t("Pick where tonight ends and the plan starts from there.") }}</p>
            </div>
            {% endif %}
        </section>
        <section class="km-strip" aria-label="{{ t('Trip status') }}">
            <div class="km-marker"><strong>{{ active_participant_count }}</strong><span>{{ t("in tonight's pool") }}</span></div>
            <div class="km-marker"><strong>{{ driver_count }}</strong><span>{{ t("drivers") }}</span></div>
            <div class="km-marker{% if pending_guest_replies %} km-marker-pending{% endif %}"><strong>{{ pending_guest_replies }}</strong><span>{{ t("guest replies to import") }}</span></div>
            <div class="km-marker">
                {% if optimization and optimization.best_result %}
                <strong>{{ optimization.best_result.assignments|length }} {{ t("cars") }}</strong>
                <span>{{ '%.0f'|format(optimization.best_result.total_distance_km) }} km · EUR {{ '%.0f'|format(optimization.best_result.total_cost_eur) }}</span>
                {% else %}
                <strong>–</strong><span>{{ t("plan not run yet") }}</span>
                {% endif %}
            </div>
        </section>
        <section class="panel home-action">
            {% if next_action.method == 'post' %}
            <form method="post" action="{{ next_action.href }}"><button type="submit" class="button-big">{{ t(next_action.label) }}</button></form>
            {% else %}
            <a class="button-link button-big" href="{{ next_action.href }}">{{ t(next_action.label) }}</a>
            {% endif %}
            {% if not onboarding_complete %}
            <div class="checklist">
                <span class="{{ 'done' if onboarding.has_destination else '' }}">{{ t("Set a destination") }}</span>
                <span class="{{ 'done' if onboarding.has_participants else '' }}">{{ t("Add participants") }}</span>
                <span class="{{ 'done' if onboarding.has_driver else '' }}">{{ t("Mark at least one driver") }}</span>
                <span class="{{ 'done' if onboarding.has_history else '' }}">{{ t("Save your first trip") }}</span>
            </div>
            {% endif %}
        </section>
        {% if trip_history %}
        <section class="panel">
            <div class="panel-head"><h2>{{ t("Recent trips") }}</h2><a href="/trips">{{ t("All trips") }}</a></div>
            <div class="saved-list">
                {% for entry in trip_history[:3] %}
                <article class="mini-card">
                    <strong>{{ entry.trip_name }}</strong>
                    <span class="muted">{{ entry.trip_date or entry.created_at }}</span>
                    <div class="inline-actions">
                        <form method="post" action="/history/{{ entry.id }}/restore"><button class="secondary" type="submit" data-confirm="{{ t('Load this past trip? Your current trip is saved first.') }}">{{ t("Load this trip") }}</button></form>
                        <a class="button-link secondary-link" href="/history/{{ entry.id }}/snapshot" target="_blank">{{ t("Open snapshot") }}</a>
                    </div>
                </article>
                {% endfor %}
            </div>
        </section>
        {% endif %}
        {% endif %}
```

Keep the existing `pinned-summary` block only for `is_plan` (wrap it in `{% if is_plan and optimization and optimization.best_result %}`).

- [ ] **Step 4: Translations** (it / fr / es), including the Python-side labels:

```python
        "arrive": "arrivo", "No destination yet": "Nessuna destinazione", "Pick where tonight ends and the plan starts from there.": "Scegli dove finisce la serata: il piano parte da lì.",
        "Trip status": "Stato del viaggio", "in tonight's pool": "nel gruppo di stasera", "drivers": "autisti", "guest replies to import": "risposte ospiti da importare",
        "cars": "auto", "plan not run yet": "piano non ancora calcolato", "Set a destination": "Imposta una destinazione", "Add people": "Aggiungi persone",
        "Mark a driver": "Segna un autista", "Run the plan": "Calcola il piano", "Share the plan": "Condividi il piano", "Recent trips": "Viaggi recenti", "All trips": "Tutti i viaggi",
```
```python
        "arrive": "arrivée", "No destination yet": "Pas encore de destination", "Pick where tonight ends and the plan starts from there.": "Choisissez où finit la soirée : le plan part de là.",
        "Trip status": "État du trajet", "in tonight's pool": "dans le groupe de ce soir", "drivers": "conducteurs", "guest replies to import": "réponses d'invités à importer",
        "cars": "voitures", "plan not run yet": "plan pas encore calculé", "Set a destination": "Définir une destination", "Add people": "Ajouter des personnes",
        "Mark a driver": "Désigner un conducteur", "Run the plan": "Calculer le plan", "Share the plan": "Partager le plan", "Recent trips": "Trajets récents", "All trips": "Tous les trajets",
```
```python
        "arrive": "llegada", "No destination yet": "Sin destino todavía", "Pick where tonight ends and the plan starts from there.": "Elige dónde termina la noche: el plan empieza ahí.",
        "Trip status": "Estado del viaje", "in tonight's pool": "en el grupo de esta noche", "drivers": "conductores", "guest replies to import": "respuestas de invitados por importar",
        "cars": "coches", "plan not run yet": "plan aún no calculado", "Set a destination": "Definir un destino", "Add people": "Añadir personas",
        "Mark a driver": "Marcar un conductor", "Run the plan": "Calcular el plan", "Share the plan": "Compartir el plan", "Recent trips": "Viajes recientes", "All trips": "Todos los viajes",
```

Delete the now-unused hero translations later in Task 12 (they are harmless until then).

- [ ] **Step 5: Tests → `OK`. Commit** `feat: home page shows the current trip and one next action`.

---

### Task 4: One nav

**Files:**
- Create: `app/templates/_topbar.html`
- Modify: `app/templates/index.html` (replace `<nav class="app-nav panel">…</nav>` and delete `<nav class="app-footer-nav panel">…</nav>`)
- Modify: `app/static/style.css` (delete `.app-nav*`, `.app-footer-nav*`, `.page-intro`, `.page-hub*`, `.hero*`, `.stats`, `.stat-card` rules; add `.topbar*`)
- Modify: `app/main.py` (`STATIC_ASSET_VERSION`)

- [ ] **Step 1: `_topbar.html`**

```jinja
<header class="topbar">
    <a class="plate" href="/" aria-label="Drivers Manager"><span class="plate-band" aria-hidden="true">I</span><span class="plate-text">DM</span></a>
    <nav class="topbar-nav" aria-label="{{ t('App sections') }}">
        {% for page in page_links %}
        <a class="topbar-link{% if page.is_active %} active{% endif %}" href="{{ page.path }}"{% if page.is_active %} aria-current="page"{% endif %}>{{ t(page.label) }}</a>
        {% endfor %}
    </nav>
    <div class="topbar-tools">
        <button type="button" id="theme-toggle" class="theme-toggle" aria-label="{{ t('Switch color theme') }}"><span class="theme-toggle-icon" aria-hidden="true"></span></button>
        {% include "_language_selector.html" %}
    </div>
</header>
```

- [ ] **Step 2: `index.html`** — replace the whole `<nav class="app-nav panel" …>…</nav>` with `{% include "_topbar.html" %}`; delete the footer nav block entirely (the `/welcome` link moves to Settings in Task 7).

- [ ] **Step 3: CSS** (temporary, retokenised in Task 8 — write it with the new variable names already, they resolve to the old values until Task 8 defines them; add these two fallbacks at the top of `:root` now: `--sign-green: #0E7C3F; --asphalt: #2A2D31;`)

```css
.topbar {
    display: flex;
    align-items: center;
    gap: 18px;
    padding: 10px 0 18px;
}
.topbar-nav { display: flex; gap: 4px; flex: 1; }
.topbar-link {
    padding: 8px 14px;
    border-radius: 6px;
    font-family: "Overpass", "Bahnschrift", "Segoe UI", system-ui, sans-serif;
    font-weight: 700;
    color: var(--ink);
    text-decoration: none;
}
.topbar-link.active { background: var(--sign-green); color: #fff; }
.topbar-tools { display: flex; gap: 8px; align-items: center; }
.plate {
    display: inline-flex;
    align-items: stretch;
    border: 1.5px solid var(--asphalt);
    border-radius: 4px;
    overflow: hidden;
    text-decoration: none;
    font-family: "Overpass", "Bahnschrift", sans-serif;
    font-weight: 800;
    letter-spacing: 0.06em;
}
.plate-band { background: #0B4F9C; color: #fff; padding: 4px 5px; font-size: 0.7rem; display: grid; place-items: end; }
.plate-text { background: #fff; color: var(--asphalt); padding: 4px 8px; }
@media (max-width: 720px) {
    .topbar-nav {
        position: fixed;
        inset: auto 0 0 0;
        z-index: 40;
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 0;
        background: var(--paper);
        border-top: 2px solid var(--line);
        padding: 4px max(4px, env(safe-area-inset-left)) max(4px, env(safe-area-inset-bottom));
    }
    .topbar-link { text-align: center; padding: 10px 2px; font-size: 0.82rem; }
    .page { padding-bottom: 84px; }
}
```

Delete the rule blocks listed in **Files** (search each selector prefix; they are contiguous groups).

- [ ] **Step 4: Bump version `…-t4`, tests → `OK`, screenshot `/` at 1360 and 390 wide, commit** `refactor: single top bar, footer nav and intro banners removed`.

---

### Task 5: Merged map workspace

**Files:**
- Modify: `app/templates/index.html` (`is_plan` blocks)
- Modify: `app/static/app.js` (tabs, show-on-map, `map.invalidateSize`)
- Modify: `app/static/style.css` (`.workspace*`, `.ws-*`; delete `.planner-layout`, `.map-summary-row`, `.trip-assembly`, `.route-sidebar`)
- Modify: `app/main.py` (`STATIC_ASSET_VERSION`)
- Test: `tests/test_web_app.py`, `tests/test_source_hygiene.py`

**Interfaces:**
- Consumes: `active_tab`, `workspace_tabs` from Task 2.
- Produces: DOM: `.ws-tabs [role=tab][data-tab]`, `.ws-panel#panel-<slug>`, `button[data-show-route-set]`.

- [ ] **Step 1: Failing tests**

```python
    def test_plan_page_has_all_four_tab_panels(self) -> None:
        html = self.client.get("/plan").text
        for slug in ("people", "places", "rules", "results"):
            self.assertIn(f'id="panel-{slug}"', html)
            self.assertIn(f'data-tab="{slug}"', html)
        self.assertIn('id="panel-places" role="tabpanel" hidden', html)
        self.assertNotIn('id="panel-people" role="tabpanel" hidden', html)

    def test_plan_results_have_show_on_map_buttons(self) -> None:
        self.client.post("/sample")
        self.client.post("/optimization")
        html = self.client.get("/plan?tab=results").text
        self.assertIn('data-show-route-set="0"', html)
```

In `tests/test_source_hygiene.py` add a tripwire that every id `app.js` looks up exists somewhere in `index.html` (it catches the class of bug that broke the Setup map before):

```python
    def test_every_dom_id_app_js_looks_up_exists_in_index_html(self) -> None:
        source = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        html = (TEMPLATE_DIR / "index.html").read_text(encoding="utf-8")
        looked_up = set(re.findall(r'getElementById\("([a-z-]+)"\)', source))
        missing = sorted(element_id for element_id in looked_up if f'id="{element_id}"' not in html)
        self.assertEqual(missing, [], f"app.js looks up ids index.html never renders: {missing}")
```

- [ ] **Step 2: Restructure the template**

Replace the current `{% if is_plan %}<section class="panel map-panel" id="map-panel">…</section>{% endif %}` and every later `{% if is_plan %}…{% endif %}` block (destination panel, participants panel, planner, driver selection, optimization results) with one block. Move existing markup verbatim into the slots below — do not rewrite forms; only the wrapper changes. Ids referenced by `app.js` and `SCROLL_TARGET_TABS` must keep their ids.

```jinja
        {% if is_plan %}
        <section class="workspace" id="map-panel">
            <div class="workspace-map">
                <div class="map-toolbar">
                    {# existing search box, map-target, fit-mode, route-set-select, food-mode, refresh-map, food-overlay-status — unconditional now #}
                </div>
                <div id="planner-map" class="planner-map"></div>
                <div class="legend">{# existing legend spans #}</div>
                <div id="map-summary" class="map-summary muted"></div>
                <div id="route-toggles" class="route-toggles"></div>
            </div>
            <div class="workspace-side">
                <div class="ws-tabs" role="tablist" aria-label="{{ t('Workspace sections') }}">
                    {% for tab in workspace_tabs %}
                    <button type="button" role="tab" id="tab-{{ tab.slug }}" data-tab="{{ tab.slug }}" aria-controls="panel-{{ tab.slug }}" aria-selected="{{ 'true' if tab.slug == active_tab else 'false' }}">{{ t(tab.label) }}</button>
                    {% endfor %}
                </div>

                <div class="ws-panel" id="panel-people" role="tabpanel"{% if active_tab != 'people' %} hidden{% endif %}>
                    {# dataset-callout (guest session) #}
                    {# the participants <details id="participants-panel"> block: quick-add form, Current Trip & CSV column (saved crews, CSV tools), Current Participants table #}
                </div>

                <div class="ws-panel" id="panel-places" role="tabpanel"{% if active_tab != 'places' %} hidden{% endif %}>
                    {# <details id="destination-panel"> block #}
                    {# the Saved Meetup Spots subsection (form id="meetup-spot-form" + saved list), wrapped in <section id="meetup-spots-panel"> #}
                </div>

                <div class="ws-panel" id="panel-rules" role="tabpanel"{% if active_tab != 'rules' %} hidden{% endif %}>
                    {# planner-side: return-trip planning (id="planner-return-planning"), pickup rules (id="planner-pickup-rules"), ride-together rules (id="planner-ride-together-rules") #}
                    {# preset + save-trip form (form action="/history/save"), "Undo last replace" card #}
                </div>

                <div class="ws-panel" id="panel-results" role="tabpanel"{% if active_tab != 'results' %} hidden{% endif %}>
                    <div class="inline-actions">
                        <form method="post" action="/selection"><button type="submit" class="secondary">{{ t("Run driver selection") }}</button></form>
                        <form method="post" action="/optimization"><button type="submit">{{ t("Run full optimization") }}</button></form>
                        <form method="post" action="/history/duplicate"><button class="secondary" type="submit" data-confirm="{{ t('Load the latest past trip? Your current trip is saved first.') }}">{{ t("Load latest past trip") }}</button></form>
                    </div>
                    {% if planner_dataset_status %}{# existing badge-row #}{% endif %}
                    {# <details id="driver-selection-section"> block, unchanged #}
                    {# <details id="optimization-results-section"> block; inside each plan card's inline-actions add: #}
                    {#   <button type="button" class="secondary" data-show-route-set="{{ loop.index0 }}">{{ t("Show on map") }}</button> #}
                    {% if not selection and not optimization %}
                    <p class="muted">{{ t("Run driver selection or the full optimization to see plans here.") }}</p>
                    {% endif %}
                </div>
            </div>
        </section>
        {% endif %}
```

Delete the `trip-assembly` card entirely (its content lives on Home). Remove the `<details class="panel collapsible">` wrappers around Planner/Driver Selection/Results but keep the ids on plain `<section>` elements so scroll targets still resolve. The two `<details … open>` wrappers for destination and participants stay (they are useful on the phone).

- [ ] **Step 3: `app.js` additions** (place before the `if (scrollTarget)` block)

```js
    const workspaceTabs = Array.from(document.querySelectorAll("[role=tab][data-tab]"));
    function activateWorkspaceTab(slug, { pushUrl = true } = {}) {
        if (!workspaceTabs.length) return;
        workspaceTabs.forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.tab === slug)));
        document.querySelectorAll(".ws-panel").forEach((panel) => {
            panel.hidden = panel.id !== `panel-${slug}`;
        });
        if (pushUrl) {
            const url = new URL(window.location.href);
            url.searchParams.set("tab", slug);
            window.history.replaceState(null, "", url);
        }
        if (map) window.setTimeout(() => map.invalidateSize(), 60);
    }
    workspaceTabs.forEach((tab) => tab.addEventListener("click", () => activateWorkspaceTab(tab.dataset.tab)));

    document.addEventListener("click", (event) => {
        const button = event.target.closest("[data-show-route-set]");
        if (!button || !routeSetSelect) return;
        routeSetSelect.value = button.dataset.showRouteSet;
        routeSetSelect.dispatchEvent(new Event("change"));
        mapElement?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
```

The existing `if (scrollTarget)` block already scrolls to the element; the server has already un-hidden the right panel via `active_tab`, so nothing else is needed there.

- [ ] **Step 4: CSS**

```css
.workspace {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 440px;
    gap: 16px;
    align-items: start;
}
.workspace-map {
    position: sticky;
    top: 12px;
    display: grid;
    gap: 10px;
    max-height: calc(100vh - 24px);
}
.workspace-map .planner-map { height: min(62vh, 640px); }
.workspace-side { min-width: 0; display: grid; gap: 14px; }
.ws-tabs {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    border: 2px solid var(--line);
    border-radius: 6px;
    overflow: hidden;
}
.ws-tabs [role="tab"] {
    background: var(--paper);
    color: var(--ink);
    border: 0;
    border-right: 2px solid var(--line);
    border-radius: 0;
    padding: 12px 6px;
    font-weight: 700;
}
.ws-tabs [role="tab"]:last-child { border-right: 0; }
.ws-tabs [role="tab"][aria-selected="true"] { background: var(--sign-green); color: #fff; }
.ws-panel { display: grid; gap: 14px; }
@media (max-width: 1080px) {
    .workspace { grid-template-columns: 1fr; }
    .workspace-map { position: static; max-height: none; }
    .workspace-map .planner-map { height: 42vh; }
}
```

Delete `.planner-layout`, `.planner-main`, `.planner-side`, `.map-summary-row`, `.trip-assembly`, `.route-sidebar`, `.map-panel` rules.

- [ ] **Step 5: Translations** for `Workspace sections`, `Show on map`, `Run driver selection or the full optimization to see plans here.`:

it: `"Sezioni del workspace"`, `"Mostra sulla mappa"`, `"Esegui la selezione autisti o l'ottimizzazione completa per vedere i piani qui."`
fr: `"Sections de l'espace de travail"`, `"Afficher sur la carte"`, `"Lancez la sélection des conducteurs ou l'optimisation complète pour voir les plans ici."`
es: `"Secciones del espacio de trabajo"`, `"Mostrar en el mapa"`, `"Ejecuta la selección de conductores o la optimización completa para ver los planes aquí."`

- [ ] **Step 6: Bump version `…-t5`, tests → `OK`. Screenshot `/plan?tab=results` with sample data at 1360 and 390. Check in the browser: clicking a tab switches panels, "Show on map" changes the route option select, the map stays sticky while the side panel scrolls. Commit** `feat: merge setup and planning into one map workspace with tabs`.

---

### Task 6: Share page with per-driver messages

**Files:**
- Modify: `app/main.py` (`_build_driver_messages`, share context in the page builder)
- Modify: `app/templates/index.html` (`is_share` block)
- Modify: `app/i18n.py`
- Test: `tests/test_web_app.py`

**Interfaces:**
- Produces: `_build_driver_messages(chosen_trip, optimization, destination) -> list[dict[str, str]]` with keys `driver`, `text`, `whatsapp_url`; context key `driver_messages` (list, possibly empty).

- [ ] **Step 1: Failing test**

```python
    def test_share_page_lists_one_message_per_driver(self) -> None:
        self.client.post("/sample")
        self.client.post("/optimization")
        html = self.client.get("/share").text
        self.assertIn("Current plan", html)
        self.assertIn("data-copy-text=", html)
        self.assertIn("https://wa.me/?text=", html)
        from app.main import _build_driver_messages, _resolve_share_context
        optimization, chosen, _index = _resolve_share_context(None)
        messages = _build_driver_messages(chosen, optimization, {"name": "Modena Centro"})
        self.assertEqual(len(messages), len(chosen["assignments"]))
        self.assertTrue(messages[0]["text"].startswith(messages[0]["driver"]))
        self.assertIn("Modena Centro", messages[0]["text"])
```

- [ ] **Step 2: Helper** (next to `_build_share_summary`)

```python
def _build_driver_messages(chosen_trip: dict[str, object], optimization: dict[str, object], destination: dict[str, object] | None) -> list[dict[str, str]]:
    """One short WhatsApp-ready message per driver: who to pick up, when, where to."""
    participants = optimization["participants"]
    destination_name = destination["name"] if destination else "the destination"
    messages = []
    for assignment in chosen_trip["assignments"]:
        driver = participants[assignment["driver_index"]]["name"]
        riders = [participants[index]["name"] for index in assignment["passenger_indices"]]
        lines = [f"{driver}, you drive to {destination_name}."]
        lines.append("Pick up: " + (", ".join(riders) if riders else "nobody, you go solo."))
        for stop in assignment.get("pickup_schedule", []):
            lines.append(f"  {stop}")
        if assignment.get("outbound_departure_time"):
            lines.append(f"Leave at {assignment['outbound_departure_time']}.")
        if assignment.get("destination_arrival_time"):
            lines.append(f"Arrive around {assignment['destination_arrival_time']}.")
        lines.append(f"About {assignment['route_distance_km']:.0f} km, EUR {assignment['cost_per_person_eur']:.2f} per person.")
        text = "\n".join(lines)
        messages.append({"driver": driver, "text": text, "whatsapp_url": f"https://wa.me/?text={quote(text)}"})
    return messages
```

In `_render_app_page`, when `page == "share"` and a cached optimization exists, resolve the chosen trip and pass `driver_messages`, `share_route_set_index`, and `share_plan_name`:

```python
    driver_messages: list[dict[str, str]] = []
    share_plan_name = ""
    share_route_set_index = 0
    if page == "share" and get_cached_optimization_result() is not None:
        shared_optimization, chosen_trip, chosen_index = _resolve_share_context(None)
        if chosen_trip is not None:
            driver_messages = _build_driver_messages(chosen_trip, shared_optimization, get_destination())
            share_plan_name = str(chosen_trip["driver_set_name"])
            share_route_set_index = chosen_index
```

and add the three to the `render_home(...)` call.

- [ ] **Step 3: Template** — rename the `guest-invites-panel` section's page to `is_share` (done in Task 2) and insert above it:

```jinja
        {% if is_share and driver_messages %}
        <section class="panel" id="current-plan-share">
            <div class="panel-head">
                <h2>{{ t("Current plan") }}: {{ share_plan_name }}</h2>
                <div class="inline-actions">
                    <a class="button-link" href="/share/current?route_set_index={{ share_route_set_index }}" target="_blank" rel="noopener">{{ t("Open Share Report") }}</a>
                    <a class="button-link secondary-link" href="/share/current?route_set_index={{ share_route_set_index }}&download=true">{{ t("Download HTML") }}</a>
                </div>
            </div>
            <div class="saved-list">
                {% for message in driver_messages %}
                <article class="mini-card driver-message">
                    <strong>{{ message.driver }}</strong>
                    <pre class="pre-wrap">{{ message.text }}</pre>
                    <div class="inline-actions">
                        <button type="button" class="secondary" data-copy-text="{{ message.text }}" data-copy-done="{{ t('Message copied') }}">{{ t("Copy message") }}</button>
                        <a class="button-link secondary-link" href="{{ message.whatsapp_url }}" target="_blank" rel="noopener">{{ t("Share on WhatsApp") }}</a>
                    </div>
                </article>
                {% endfor %}
            </div>
        </section>
        {% endif %}
```

- [ ] **Step 4: Translations** — `Current plan` (it `Piano attuale`, fr `Plan actuel`, es `Plan actual`), `Message copied` (`Messaggio copiato` / `Message copié` / `Mensaje copiado`), `Copy message` (`Copia messaggio` / `Copier le message` / `Copiar mensaje`). `Open Share Report`, `Download HTML`, `Share on WhatsApp` already exist.

- [ ] **Step 5: Tests → `OK`. Commit** `feat: share page with one copyable message per driver`.

---

### Task 7: Trips page and Settings with an Advanced section

**Files:**
- Modify: `app/templates/index.html` (`is_trips`, `is_settings` blocks)
- Modify: `app/i18n.py`
- Test: `tests/test_web_app.py`

- [ ] **Step 1: Failing test**

```python
    def test_settings_hides_optimizer_parameters_behind_advanced(self) -> None:
        html = self.client.get("/settings").text
        self.assertIn('<details class="advanced-fields" id="advanced-settings-panel">', html)
        self.assertLess(html.index('name="map_latitude"'), html.index('name="num_ants"'))
        self.assertIn('href="/welcome"', html)
```

- [ ] **Step 2: Settings** — inside the existing `<form method="post" action="/settings" class="form-grid">`, reorder so the map/meetup/notification inputs come first, then wrap the five optimizer inputs:

```jinja
                    <details class="advanced-fields" id="advanced-settings-panel">
                        <summary>{{ t("Advanced optimizer") }}</summary>
                        <div class="advanced-grid">
                            <label><span>{{ t("Ants") }}</span><input name="num_ants" type="number" value="{{ settings.optimization.num_ants }}"></label>
                            <label><span>{{ t("Iterations") }}</span><input name="num_iterations" type="number" value="{{ settings.optimization.num_iterations }}"></label>
                            <label><span>Alpha</span><input name="alpha" type="number" step="any" value="{{ settings.optimization.alpha }}"></label>
                            <label><span>Beta</span><input name="beta" type="number" step="any" value="{{ settings.optimization.beta }}"></label>
                            <label><span>Rho</span><input name="rho" type="number" step="any" value="{{ settings.optimization.rho }}"></label>
                        </div>
                    </details>
```

(Copy the exact `value=` expressions from the current inputs; keep the `id="advanced-settings-panel"` on the details so the scroll target keeps working. Remove the id from the outer `<section>`.) Add an `<h2>{{ t("Map & planning") }}</h2>` above the form and, at the bottom of the settings page, `<p class="muted"><a href="/welcome">{{ t("About Drivers Manager") }}</a></p>`.

- [ ] **Step 3: Trips** — `is_trips` block: add `<h2>{{ t("Past trips") }}</h2>` above the list (the intro banner is gone). No other change.

- [ ] **Step 4: Translations** — `Advanced optimizer` (`Ottimizzatore avanzato` / `Optimiseur avancé` / `Optimizador avanzado`), `Map & planning` (`Mappa e pianificazione` / `Carte et planification` / `Mapa y planificación`), `About Drivers Manager` (`Informazioni su Drivers Manager` / `À propos de Drivers Manager` / `Acerca de Drivers Manager`).

- [ ] **Step 5: Tests → `OK`. Commit** `feat: settings advanced section, trips page heading`.

---

### Task 8: Autostrada tokens, type and surfaces

**Files:**
- Modify: `app/static/style.css` (lines 1–120: tokens, body, headings; `.panel`, `.card`, `.mini-card`, `.button-link`, `button`, `.badge*`, inputs, `.banner`, `.toast*`, `.checklist`, the dark block at the end)
- Modify: `app/templates/_brand_head.html`, `app/templates/index.html` `<head>` (font link, `theme-color`)
- Modify: `app/templates/share_plan.html` (delete its inline `:root {…}` block; add `<link rel="stylesheet" href="{{ url_for('static', path='/style.css') }}?v={{ static_asset_version }}">`)
- Modify: `app/static/manifest.webmanifest` (`theme_color`, `background_color`)
- Modify: `app/main.py` (`STATIC_ASSET_VERSION`)
- Test: `tests/test_source_hygiene.py`

- [ ] **Step 1: Tripwire test**

```python
    def test_old_palette_does_not_survive_the_redesign(self) -> None:
        """The Autostrada tokens replaced the cream/terracotta palette everywhere."""
        old_colors = ("#ef6a3a", "#cb4d1f", "#f2bf4d", "#4ebd91", "#f4ede2", "#0f1d35", "Space Grotesk")
        files = [REPO_ROOT / "app" / "static" / "style.css", *sorted(TEMPLATE_DIR.rglob("*.html"))]
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            leftovers = [color for color in old_colors if color.lower() in text]
            self.assertEqual(leftovers, [], f"{path.name} still uses the old palette: {leftovers}")
```

- [ ] **Step 2: Fonts** — in `_brand_head.html` and `index.html` replace the Google Fonts link with:

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Overpass:wght@600;700;800&family=IBM+Plex+Sans:wght@400;500;600&display=swap">
```

and `<meta name="theme-color" content="#0E7C3F">`. In `manifest.webmanifest` set `"theme_color": "#0E7C3F"`, `"background_color": "#F5F6F3"`.

- [ ] **Step 3: Tokens and base** — replace `:root { … }` through the `h2, h3 { color }` rule with:

```css
:root {
    --road: #F5F6F3;
    --paper: #FFFFFF;
    --paper-strong: #FFFFFF;
    --paper-tint: #FAFBF9;
    --ink: #1B1F23;
    --muted: #5C6570;
    --line: #D3D8D0;
    --line-strong: #B9C0B6;
    --sign-green: #0E7C3F;
    --sign-green-deep: #0A5C2E;
    --sign-blue: #0B4F9C;
    --sign-brown: #7A4A22;
    --sign-yellow: #F2C230;
    --sign-red: #C8102E;
    --asphalt: #2A2D31;
    --accent: var(--sign-green);
    --accent-strong: var(--sign-green-deep);
    --danger: var(--sign-red);
    --success: var(--sign-green);
    --radius: 6px;
    --radius-xl: 6px; --radius-lg: 6px; --radius-md: 6px; --radius-sm: 4px;
    --shadow: none;
    --shadow-soft: none;
    --font-display: "Overpass", "Bahnschrift", "Segoe UI", system-ui, sans-serif;
    --font-body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
    margin: 0;
    font-family: var(--font-body);
    font-variant-numeric: tabular-nums;
    color: var(--ink);
    background: var(--road);
    min-height: 100vh;
}
h1, h2, h3, h4, summary, button, .button-link, .route-toggle-title, .route-legend-main, .topbar-link, .ws-tabs [role="tab"] {
    font-family: var(--font-display);
}
h1, h2, h3, h4 { margin-top: 0; letter-spacing: 0.005em; color: var(--ink); }
h1 { font-size: clamp(1.8rem, 3vw, 2.4rem); font-weight: 800; }
h2 { font-size: 1.25rem; font-weight: 700; }
h3 { font-size: 1.02rem; font-weight: 700; }
.eyebrow { margin: 0 0 6px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.12em; font-size: 0.74rem; font-weight: 700; }
.lede, .muted, small.muted { color: var(--muted); }
```

Delete the `body::before` grid overlay rule.

- [ ] **Step 4: Surfaces and controls** — rewrite these rules (search each selector; keep layout properties, replace colour/shape/shadow):

```css
.card, .panel, .banner, .result-card, .mini-card {
    background: var(--paper);
    border: 2px solid var(--line);
    border-radius: var(--radius);
    box-shadow: none;
}
.panel { padding: 18px; margin-bottom: 14px; }
.panel::before, .card::before, .mini-card::before { content: none; }   /* kills the rainbow top bars */
button, .button-link {
    font-family: var(--font-display);
    font-weight: 700;
    font-size: 0.95rem;
    padding: 10px 16px;
    border-radius: var(--radius);
    border: 2px solid var(--sign-green);
    background: var(--sign-green);
    color: #fff;
    cursor: pointer;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    gap: 8px;
    box-shadow: none;
}
button:hover, .button-link:hover { background: var(--sign-green-deep); border-color: var(--sign-green-deep); }
button.secondary, .secondary-link { background: var(--paper); color: var(--sign-green); }
button.secondary:hover, .secondary-link:hover { background: var(--paper-tint); color: var(--sign-green-deep); }
button.danger { background: var(--sign-red); border-color: var(--sign-red); color: #fff; }
.button-big { font-size: 1.1rem; padding: 14px 22px; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible, textarea:focus-visible, [role="tab"]:focus-visible {
    outline: 3px solid var(--sign-yellow);
    outline-offset: 2px;
}
input, select, textarea {
    font: inherit;
    padding: 10px 12px;
    border: 2px solid var(--line);
    border-radius: var(--radius);
    background: var(--paper);
    color: var(--ink);
}
label > span { font-weight: 600; font-size: 0.86rem; color: var(--ink); }
.badge, .route-chip {
    border-radius: 4px;
    border: 1.5px solid var(--line-strong);
    background: var(--paper);
    color: var(--ink);
    padding: 3px 8px;
    font-weight: 600;
    font-size: 0.8rem;
}
.badge-warning { border-color: var(--sign-yellow); background: #FFF6D6; }
.badge-cheapest { border-color: var(--sign-green); color: var(--sign-green); }
.banner.error { border-color: var(--sign-red); color: var(--sign-red); }
.banner.success { border-color: var(--sign-green); color: var(--sign-green-deep); }
.checklist span { border: 1.5px solid var(--line-strong); border-radius: 4px; padding: 6px 10px; }
.checklist span.done { border-color: var(--sign-green); color: var(--sign-green-deep); background: #E7F3EC; }
.checklist span.done::before { content: "✓ "; }
.pinned-summary { border-left: 6px solid var(--sign-green); }
.toast { background: var(--asphalt); color: #fff; border-radius: var(--radius); box-shadow: none; }
```

Then sweep `style.css` for every remaining `linear-gradient(`, `radial-gradient(`, `box-shadow: 0`, and old hex literal (the tripwire lists them) and replace with the nearest token or delete the declaration. The guest invite (`.guest-*`) and share-report rules included.

- [ ] **Step 5: Dark block** — replace the `:root[data-theme="dark"] { … }` token remap with:

```css
:root[data-theme="dark"] {
    --road: #1B1D20;
    --paper: #24272B;
    --paper-strong: #24272B;
    --paper-tint: #2B2F34;
    --ink: #F2F3EF;
    --muted: #A9B0B6;
    --line: #3A3F45;
    --line-strong: #4A5057;
    --sign-green: #12904A;
    --sign-green-deep: #0E7C3F;
}
```

Walk the rest of the dark block and delete any rule that only re-paints a gradient or shadow; keep the ones that fix hardcoded `#fff` on text.

- [ ] **Step 6: `share_plan.html`** — delete its `:root` block, link `style.css`, and replace its remaining inline hex colours with tokens.

- [ ] **Step 7: Bump version `…-t8`, tests → `OK`. Screenshot `/`, `/plan`, `/share`, `/welcome`, `/login`, `/share/current`, one guest invite form, in light and dark. Fix anything unreadable. Commit** `style: Autostrada tokens, Overpass + IBM Plex Sans, flat sign surfaces`.

---

### Task 9: Signature components

**Files:**
- Modify: `app/static/style.css` (`.sign*`, `.targa*`, `.km-*`, `.service-*`, map pins)
- Modify: `app/templates/index.html` (results driver cards, participant role cell, food legend eyebrow)
- Modify: `app/static/app.js` (destination + meetup icons)
- Modify: `app/main.py` (`STATIC_ASSET_VERSION`)
- Modify: `app/i18n.py`
- Test: `tests/test_web_app.py` (`test_static_app_js_uses_raster_icon_paths`)

- [ ] **Step 1: Exit sign, km marker, targa, service badge CSS**

```css
.sign {
    background: var(--sign-green);
    color: #fff;
    border-radius: 8px;
    box-shadow: inset 0 0 0 3px #fff, inset 0 0 0 6px var(--sign-green);
    padding: 26px 28px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 18px;
    margin-bottom: 14px;
}
.sign h1, .sign h2, .sign h3 { color: #fff; margin: 0; }
.sign p { margin: 6px 0 0; color: rgba(255, 255, 255, 0.86); font-family: var(--font-display); font-weight: 600; }
.sign-arrow { font-size: clamp(2.4rem, 5vw, 3.6rem); line-height: 1; }
.sign-hero { animation: sign-pass 240ms ease-out; }
@keyframes sign-pass { from { transform: translateX(28px); opacity: 0; } to { transform: none; opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .sign-hero { animation: none; } }

.sign-card { padding: 14px 16px; border-radius: 6px; margin: 0; display: grid; gap: 10px; }
.sign-card .sign-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.sign-card .sign-towns { display: grid; gap: 4px; font-family: var(--font-display); font-weight: 600; }
.sign-card .sign-towns div { display: flex; justify-content: space-between; gap: 12px; }
.sign-card .sign-towns span:last-child { font-variant-numeric: tabular-nums; opacity: 0.9; }

.km-strip { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 14px; }
.km-marker {
    background: var(--paper);
    border: 2px solid var(--asphalt);
    border-top-width: 14px;
    border-radius: 6px;
    padding: 12px 14px 14px;
    display: grid;
    gap: 2px;
    text-align: center;
}
.km-marker strong { font-family: var(--font-display); font-weight: 800; font-size: 1.9rem; line-height: 1; }
.km-marker span { color: var(--muted); font-size: 0.82rem; }
.km-marker-pending { border-color: var(--sign-yellow); }
@media (max-width: 720px) { .km-strip { grid-template-columns: repeat(2, 1fr); } }

.targa {
    display: inline-flex;
    align-items: stretch;
    border: 1.5px solid var(--asphalt);
    border-radius: 4px;
    overflow: hidden;
    font-family: var(--font-display);
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-size: 0.8rem;
    line-height: 1;
    background: #fff;
    color: var(--asphalt);
}
.targa::before { content: ""; width: 7px; background: var(--sign-blue); }
.targa-driver::before { background: var(--sign-green); }
.targa span { padding: 5px 8px; }

.service-eyebrow { color: var(--sign-brown); }
.service-eyebrow::before { content: "🍴 "; }
.badge-service { border-color: var(--sign-brown); color: var(--sign-brown); }
```

- [ ] **Step 2: Results driver cards as exit signs** — in the plan-card loop, replace each `<div class="assignment">…<div class="assignment-head">…</div>` header with:

```jinja
                    <div class="assignment sign sign-card">
                        <div class="sign-head">
                            <h4><span class="targa targa-driver"><span>{{ optimization.participants[assignment.driver_index].name }}</span></span></h4>
                            <span>{{ '%.0f'|format(assignment.route_distance_km) }} km · EUR {{ '%.2f'|format(assignment.cost_per_person_eur) }}{{ t("/person") }}</span>
                        </div>
                        <div class="sign-towns">
                            {% for passenger_index in assignment.passenger_indices %}
                            <div><span class="targa"><span>{{ optimization.participants[passenger_index].name }}</span></span><span>{{ assignment.pickup_schedule[loop.index0] if assignment.pickup_schedule and loop.index0 < assignment.pickup_schedule|length else '' }}</span></div>
                            {% else %}
                            <div><span>{{ t("Drives alone") }}</span></div>
                            {% endfor %}
                            <div><span>➚ {{ destination.name if destination else t("Destination") }}</span><span>{{ assignment.destination_arrival_time or '' }}</span></div>
                        </div>
                        {% if assignment.time_window_violation_min > 0 %}<span class="badge badge-warning">{{ t("Timing mismatch") }} {{ '%.0f'|format(assignment.time_window_violation_min) }} min</span>{% endif %}
                    </div>
```

(`pickup_schedule` entries are strings like `"Fabio at 19:17"`; showing the whole string on the right is acceptable and avoids parsing.) Keep the return-plan block as is.

- [ ] **Step 3: People table role cell**

```jinja
<td data-label="{{ t('Role') }}"><span class="targa{% if participant.has_car %} targa-driver{% endif %}"><span>{{ t('Driver') if participant.has_car else t('Passenger') }}</span></span></td>
```

- [ ] **Step 4: Food legend** — above the `#food-mode` label add `<span class="eyebrow service-eyebrow">Area di servizio</span>` (brand word, untranslated) and give the food overlay status `class="muted food-overlay-status badge-service"`.

- [ ] **Step 5: Map icons** — in `app.js` replace the destination and meetup raster icons:

```js
    const destinationIcon = buildIcon("destination-pin", "➚");
    const meetupIcon = buildIcon("meetup-pin", "P");
```

and the marker CSS:

```css
.map-pin div {
    width: 30px; height: 30px; border-radius: 4px; display: grid; place-items: center;
    color: #fff; font-family: var(--font-display); font-weight: 800; font-size: 15px;
    border: 2px solid #fff; box-shadow: 0 0 0 1.5px var(--asphalt);
}
.driver-pin div { background: var(--sign-green); }
.passenger-pin div { background: var(--sign-blue); }
.selection-pin div { background: var(--sign-yellow); color: var(--asphalt); }
.destination-pin div { background: var(--sign-green); border-radius: 6px; }
.meetup-pin div { background: var(--sign-blue); font-size: 18px; }
.kebab-pin div, .kfc-pin div, .mcdonalds-pin div, .burger-king-pin div, .pizza-pin div, .cafe-pin div { background: var(--sign-brown); }
.pickup-order-pin div { background: var(--sign-yellow); color: var(--asphalt); border-radius: 50%; }
```

The food raster icons keep their PNGs (they are the brand marks people recognise) but sit on the brown square: keep `buildRasterIcon` for the five food brands. Update `test_static_app_js_uses_raster_icon_paths`: drop the destination and meetup assertions, keep the McDonald's one, and add `self.assertIn('buildIcon("meetup-pin", "P")', response.text)`. Delete `icon-destination-flag-pixel.png`, `icon-meetup-point-pixel.png`, `icon-meetup-spot.svg`, `icon-route-stop.svg`, `icon-burger-stop.svg`, `guest-route-card.svg` if nothing references them (`grep -rn <name> app/`).

Best-route polyline colours: in `drawMap`, `color: isSelectedRouteSet ? "#0E7C3F" : "#5C6570"` instead of `route.color`, keep weight/dash logic.

- [ ] **Step 6: Bump version `…-t9`, tests → `OK`. Screenshot `/` and `/plan?tab=results` with sample data, light and dark. Commit** `style: exit-sign plan cards, targa chips, km markers, sign-coloured map pins`.

---

### Task 10: Landing and login

**Files:**
- Create: `app/static/viaduct.svg`
- Modify: `app/templates/landing.html`, `app/templates/login.html`
- Modify: `app/static/style.css`
- Modify: `app/i18n.py`

- [ ] **Step 1: `viaduct.svg`** — a repeating arch strip, line art only:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 40" preserveAspectRatio="none" fill="none" stroke="#B9C0B6" stroke-width="2">
  <path d="M0 8 H400"/>
  <path d="M0 40 V22 A20 14 0 0 1 40 22 V40 M40 40 V22 A20 14 0 0 1 80 22 V40 M80 40 V22 A20 14 0 0 1 120 22 V40 M120 40 V22 A20 14 0 0 1 160 22 V40 M160 40 V22 A20 14 0 0 1 200 22 V40 M200 40 V22 A20 14 0 0 1 240 22 V40 M240 40 V22 A20 14 0 0 1 280 22 V40 M280 40 V22 A20 14 0 0 1 320 22 V40 M320 40 V22 A20 14 0 0 1 360 22 V40 M360 40 V22 A20 14 0 0 1 400 22 V40"/>
  <path d="M0 8 V22 M400 8 V22"/>
</svg>
```

CSS: `.viaduct { display: block; width: 100%; height: 40px; margin: 8px 0 18px; }`.

- [ ] **Step 2: Landing** — replace the hero with an exit sign and delete the night illustration and its `<img>`; keep the four feature cards and the three steps (they are real content). Delete the `.landing-hero*` styles from the template and use:

```jinja
        <section class="sign sign-hero landing-sign">
            <div class="sign-body">
                <p class="eyebrow" style="color: rgba(255,255,255,0.8)">Drivers Manager</p>
                <h1>{{ t("Group carpools, planned in minutes") }}</h1>
                <p>{{ t("One person sets the destination. Friends reply from a link on their phone. The optimizer picks the drivers, the routes, and even the food stop everyone voted for.") }}</p>
                <div class="landing-cta-row">
                    <a class="button-link landing-cta" href="/">{{ t("Open the planner") }}</a>
                    <form method="post" action="/sample"><button type="submit" class="landing-cta">{{ t("Try it with demo data") }}</button></form>
                </div>
            </div>
            <span class="sign-arrow" aria-hidden="true">➚</span>
        </section>
        <img class="viaduct" src="{{ url_for('static', path='/viaduct.svg') }}" alt="">
```

with `.landing-cta { background: #fff; color: var(--sign-green-deep); border-color: #fff; }` and `.landing-sign h1 { font-size: clamp(2.2rem, 5vw, 3.6rem); }`. Replace the four feature emojis with `.targa`-style eyebrows? No — keep the emoji out: delete `.landing-feature-emoji` spans; the card title carries it. Delete `hero-roadtrip.svg` from `app/static/` and the `Stylized night roadtrip…` translation keys.

- [ ] **Step 3: Login** — above `<h1>`, `<p class="eyebrow">Casello</p>` (brand word) and `<img class="viaduct" …>` above the card; remove `.login-logo` (the plate is enough): replace the `<img class="login-logo">` with `<a class="plate" href="/welcome"><span class="plate-band">I</span><span class="plate-text">DM</span></a>`.

- [ ] **Step 4: Tests → `OK` (the translation test will flag any deleted keys still referenced; remove them). Screenshot `/welcome` and `/login`. Commit** `style: landing and login in Autostrada, viaduct strip`.

---

### Task 11: Copy diet

**Files:**
- Modify: `app/templates/index.html`
- Modify: `app/i18n.py` (new short keys; old long keys deleted once unused)

- [ ] **Step 1: Move each always-visible explanation into a `title` on its heading.** For each pair below, the `<span class="muted">…</span>` (or `<p class="muted">`) under the heading is deleted and the heading gets `title="{{ t('<full sentence>') }}"`. Where a shorter visible line is given, that replaces the sentence.

| Heading | Visible now → visible after | Full sentence goes to `title` |
|---|---|---|
| Interactive Map | "Search or click on the map, drag the selection marker…" → *(nothing)* | yes |
| Pickup Order Rules | "Soft rule, same car only" → keep | "Use this only when two riders should be picked up in a specific order if they end up in the same car…" |
| Ride Together Rules | "Hard rule, keeps a pair together" → keep | "Use this when two participants must stay in the same car…" |
| Return Trip Planning | "Plan the way back separately" → keep | "Enable this when the return trip should be optimized separately…" |
| Current destination card | "To update the destination from the map, set the click target to Destination…" → *(nothing)* | yes |
| Quick Tunnel manager paragraph | keep the two commands, delete the sentence after them | "The tunnel keeps running after you close PowerShell until you stop it." |
| Create Invite | "Create a guest form from the current destination…" → *(nothing)* | yes |
| Security | "No password set: anyone who can reach this app…" → "No password set." | full sentence |
| Best plan card badge row | 9 badges → cars · km · EUR; move Fitness, Longest route, Route spread, Self-transfer, Return regrouping into a `<details class="result-details"><summary>{{ t("Details") }}</summary>` like the other cards |  |
| Best plan explanation list (`explanation-list`) | keep, but wrap in the same details |  |

- [ ] **Step 2: Run tests (translation completeness), delete orphaned keys, commit** `copy: explanations move into tooltips, headline badges only`.

---

### Task 12: Docs and close-out

**Files:**
- Modify: `README.md` (page list; the URL in the "Open" step), `docs/IMPROVEMENT_PLAN.md` (status line), `app/i18n.py` (delete the unused hero/overview keys: `Roadtrip Control Room`, `sleek local planning…`, `Coordinate drivers…`, `Route-first planning`, `Meetup hubs`, `Food-stop vibes`, `Saved Crew`, `Tonight's Pool`, `Active Drivers`, `Ready for future runs`, `Currently in the working trip`, `Cars available right now`, `First-Run Checklist`, `Move between app pages`, `Jump between the main areas…`, `Trip Assembly`, `Map Workspace`, `Roadtrip Overview`, `Quick start hub.`, `Trip dashboard.`, `Trip Setup`, `Planning Workspace`, `Guest Links`, `Past Trips`, the `People, places…` and `Routes, rules…` descriptions).

- [ ] **Step 1: README** — replace the page bullets with Home / Plan / Share / Trips / Settings and one line each from `APP_PAGES` descriptions. Add under "Design": "Autostrada visual system — see `docs/superpowers/specs/2026-09-09-autostrada-redesign-design.md`."

- [ ] **Step 2: IMPROVEMENT_PLAN.md** — under the status paragraph add: "**2026-09-09:** section 3 (UI verbosity) and the overview-page problem are addressed by the Autostrada redesign (`docs/superpowers/plans/2026-09-09-autostrada-redesign.md`)."

- [ ] **Step 3: Final pass** — run the throwaway instance, screenshot all five pages plus `/welcome`, `/login`, a guest invite form, and `/share/current`, at 1360 and 390 wide, light and dark. Fix contrast issues found. Run the full suite:

```bash
.venv\Scripts\python.exe -m unittest tests.test_web_app tests.test_source_hygiene tests.test_apca_refinement tests.test_check
```

Expected: `OK`.

- [ ] **Step 4: Commit** `docs: README and improvement plan reflect the Autostrada redesign`, then merge `autostrada` into `main` (fast-forward or merge commit, no squash — keep the per-task history).

---

## Self-review against the spec

- §2 IA: T2 (slugs, redirects), T3 (Home), T5 (Plan tabs, sticky map, show-on-map), T6 (Share + driver messages), T7 (Settings advanced, Trips). Deleted blocks: T3/T4.
- §3.1–3.2 tokens/type: T8. §3.3 components 1–5 and 7: T9 (sign, targa, km marker, service, P marker, plate/mobile bar via T4); 6 viaduct: T10. §3.4 brand words: T9 (Area di servizio), T10 (Casello). §3.5 map: T9.
- §4 copy: T11. §5 fixes: T1, T8 (share_plan tokens). §7 tests: T2, T3, T5, T6, T7, T8, T9.
- Names used across tasks: `active_tab`, `workspace_tabs` (T2 → T5); `build_next_action` (T3 test ↔ impl); `_build_driver_messages`, `driver_messages`, `share_route_set_index`, `share_plan_name` (T6); `data-show-route-set`, `.ws-panel`, `panel-<slug>` (T5 test ↔ template ↔ JS); `--sign-*` tokens (T4 fallbacks → T8 definitions → T9 use).
