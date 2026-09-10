# Drivers Manager — Improvement Plan

Notes from a full-app audit (backend, frontend, algorithms, templates, CSS/JS) on 2026-09-08.
Goal: a working reference for future sessions — not all of this needs to happen at once.
Priority order assumed (per prior direction): sleek/fun design → easier GUI → PWA/mobile polish →
keep the food-stop feature → algorithm correctness/cheap wins. Sections below are ordered by
severity/effort, not by that priority, but each item notes which goal it serves.

**Executable plan:** `docs/superpowers/plans/2026-09-08-streamline-drivers-manager.md` turns
sections 1-3 plus section 5 below into ordered, testable tasks. This document is the *why*; that one
is the *how*. Findings added after the first audit pass are in section 5.

**Status (2026-09-09): done.** The executable plan above has been fully implemented and reviewed;
section 4's suggested order of work is complete. Two decisions were made along the way:
(a) the section 5.3 git-history scrub of `data/dmproject.db` was explicitly deferred — the file is
untracked going forward, but the old blobs still live in history (commits `5417e99` and `dfddb43`);
this stays an open decision for if the repo is ever made public. (b) the section 2.1 i18n scope
question was resolved as "finish translating everything" — the organizer-facing screens (Setup,
Planning, Settings, results) are now translated into IT/FR/ES; flash/status/error messages remain
English-only as the one known remaining gap.

**2026-09-09:** section 3 (UI verbosity) and the overview-page problem are addressed by the Autostrada
redesign (`docs/superpowers/plans/2026-09-09-autostrada-redesign.md`).

---

## 1. Bugs & broken flows (fix first — all are cheap)

### 1.1 Setup page: map crashes on every load (JS)
`app/static/app.js` → `drawMap()` (~line 456) unconditionally does:
```js
if (selectedRouteSet) { summaryElement.classList.remove("muted"); ... }
else { summaryElement.classList.add("muted"); ... }
```
`summaryElement = document.getElementById("map-summary")`, but `#map-summary` is only rendered
`{% if is_planning %}` in `app/templates/index.html` (~line 229). The map panel itself renders on
**both** `/setup` and `/planning` (`{% if is_planning or is_setup %}`, ~line 173). So on `/setup`,
`summaryElement` is `null` and `drawMap()` throws a `TypeError` every time the map loads — which
silently skips the two calls that come right after it in the same function: `fitToPayload(...)` and
`loadFoodOverlay()`. Net effect: on the Setup page, the map never auto-fits to your markers after
adding a participant/destination (you have to change "Fit View" manually to force a refit).

**Root-cause fix:** guard the block like every other page-conditional element in that file —
`if (summaryElement) { ... }` around the whole if/else, not a fix per-branch.

### 1.2 Duplicate `run_sandbox_optimization` definition (dead/shadowed code)
`app/services/planner.py` defines `run_sandbox_optimization` **twice** — once at line 783 and again
at line 960. Python silently keeps only the second one; everything that imports it
(`app/services/__init__.py`, then `app/main.py`'s guest-preview sandbox) gets the second definition.
The first definition (67 lines) is unreachable dead code, and it's not a harmless duplicate — it's
a *different, older* implementation (no meetup-pooling variant, doesn't reuse
`_optimize_single_driver_set`). This is exactly the kind of thing that causes a real bug later: a
future edit to "the function" made in the first copy would have zero effect at runtime, silently.
**Fix:** delete the first definition (lines ~783–849), keep the second (current, feature-complete)
one. No behavior change today since the second already wins — this is a maintainability/safety fix.

### 1.3 Dead code
- `core/utils.py::build_distance_matrix` — defined, never called anywhere in the codebase.
- `app/services/settings.py::save_public_base_url` — defined and exported in
  `app/services/__init__.py`, but never called; superseded by `save_public_access_url(s)` when the
  guest/admin URL split was added. Safe to delete both.

### 1.4 Fragile "get the row I just inserted" pattern
`app/services/participants.py::save_participant` does an `INSERT` via `execute()` and throws away
the new row id. Callers that need the new id — e.g.
`app/services/invites.py::import_trip_response` — work around this with
`fetch_one("SELECT id FROM participants ORDER BY id DESC LIMIT 1")` right after saving. That's
correct today only because this is a single-user, single-connection-per-call local app; it's a latent
bug if the app ever gains concurrent writers. **Cheap fix:** change `save_participant` to use
`execute_insert()` and return the id; update `import_trip_response` to use it directly.

### 1.5 APCA pheromone starvation under heavy penalties (edge case, self-healing)

**Fixed 2026-09-10** by the route-solver work: the ant colony now uses an elitist deposit
(iteration best + global best, no fitness threshold) and is no longer the default solver.
`core/apca.py::_update_pheromones` only deposits pheromone for solutions with `fitness > 0`
(~line 866). If every ant in an iteration scores ≤ 0 (plausible with many participants, tight time
windows, or a `ride_together` rule that's hard to satisfy), pheromone evaporates every iteration with
nothing replacing it, so the algorithm degrades toward pure random search for the rest of the run. It
doesn't crash and results are still evaluated/ranked correctly (the deterministic 2-opt refinement
pass at the end still runs), but optimization quality quietly gets worse instead of erroring or
logging. Low priority; worth a one-line `LOG_DEBUG` when this happens, or depositing a small amount
proportional to *relative* rank instead of absolute fitness.

---

## 2. Functionality gaps

### 2.1 Inconsistent translation coverage (serves: "easier GUI" + the 4-language claim)
`app/i18n.py` implements full IT/FR/ES translations and the infra is used properly on the landing
page, login, and guest-facing invite form (`invite_form.html`: 71 `t()` calls). But the app's densest
screens — the participant table, planner controls, and the entire optimization-results section in
`index.html` (badges, route comparison table, share summary) — are almost entirely hardcoded English
(`index.html` has 107 `t()` calls total across ~990 lines, and nearly all of them are in the
overview/hero copy, not the working screens). A non-English organizer gets a translated shell around
an English cockpit. Either finish wrapping the remaining strings in `t()`, or explicitly scope
translation to guest-facing pages only and say so (currently it reads like a half-finished feature).

### 2.2 SQLite concurrency hardening
`app/database.py::get_connection()` opens a plain `sqlite3.connect(DB_PATH)` with no
`PRAGMA journal_mode=WAL` and no explicit busy timeout. Three separate processes/threads touch the
same file today: the web server, the background fuel-price-sync thread (`app/costs.py`, started in
`on_startup`), and the separate `notifier_watcher.py` process polling every few seconds. Python's
default 5s connect timeout papers over most contention, but under real concurrent writes you can hit
`database is locked`. Turning on WAL mode (`connection.execute("PRAGMA journal_mode=WAL")` once at
startup) is a one-line, no-downside fix for a local multi-process app like this.

### 2.3 "Become a smartphone app" — current state vs. the goal
The PWA pieces are in place and reasonable (`manifest.webmanifest`, `sw.js` with a sensible
stale-while-revalidate/network-first split, install icons). But the phone still has to reach the
laptop's Cloudflare Quick Tunnel URL, which rotates on every tunnel restart and expires when the
laptop sleeps or the tunnel process dies — fine for "share tonight's plan with friends," not yet
"install once and it always works." If the investor pitch needs the app to feel like a real hosted
product, the actual gap isn't the PWA layer, it's hosting: this is a single-process, single-SQLite-file,
single-global-settings-row app (one workspace, one password, one map center for the whole
install). Moving to always-on hosting doesn't require solving multi-tenancy on day one, but it's
worth deciding now whether "one Drivers Manager install per group" (simple, current model, just
needs a stable host) or "one shared multi-tenant deployment" (bigger change: per-organizer accounts,
scoped data) is the actual target, since UI/algorithm polish doesn't move that needle.

---

## 3. UI & text-reduction (this is what you flagged — "many verbose parts")

Concrete pattern, repeated across almost every panel in `index.html`: a bold label followed by a
full sentence of explanatory prose in a `<span class="muted">`, e.g.
- "Use this only when two riders should be picked up in a specific order if they end up in the same
  car. These are outbound-only soft rules, so they nudge the score without forcing an impossible
  route." (pickup-order rules panel)
- "Use this when two participants must stay in the same car. This is treated as a hard planning
  constraint, so it should only be used for real requirements." (ride-together rules panel)
- "Enable this when the return trip should be optimized separately from the outbound trip because
  the group or time windows change later in the night." (return-planning panel)

These are all correct and helpful once — but they're always-visible body text on every page load,
not progressive disclosure. Suggested pattern: shorten the always-visible line to a phrase ("Soft
rule — nudges pickup order, doesn't force it"), and move the full explanation into a `title`
attribute or a small "?" affordance next to the label. That alone would cut a meaningful chunk of
text on the Setup and Planning pages without losing the explanation for people who want it.

Other concrete spots:
- **Badge overload**: each trip-result card in the optimization section stacks up to 9 badges
  (fitness, cars, distance, meetup tag, longest route, spread, cost, timing mismatch, self-transfer,
  return mismatch). Consider a "headline" row of 3–4 (cost, distance, cars) with the rest behind a
  "Details" disclosure per card — most of these numbers only matter when comparing plans, not on
  first glance.
- **Repeated confirm-dialog copy**: every destructive action re-explains what will/won't be affected
  inline in `data-confirm` text (e.g. "Replace the current workspace with this guest session? Your
  current workspace will be backed up automatically." appears with small variations 5+ times). Fine
  as-is functionally, but if trimmed to "Replace the workspace? (auto-backed up)" consistently, it
  reads faster without losing the safety reassurance.
- **Working Dataset & CSV panel** (`index.html` ~340-490): three stacked cards (current dataset,
  guest sessions, CSV tools) each with their own muted explanation line — this is the single busiest
  region of the Setup page. Good candidate for the progressive-disclosure treatment first, since it's
  the first thing a new organizer sees after adding participants.

None of this needs a redesign to fix — it's a copy-editing + progressive-disclosure pass over
existing panels, which fits "much better easy-to-use GUI" without touching functionality.

---

## 4. Suggested order of work

1. Fix 1.1 and 1.2 (both are one-line-ish, zero-risk, no behavior change to anything working today).
2. Delete the dead code in 1.3 while touching those files anyway.
3. Do a copy pass on Section 3 (biggest visible win toward "easier GUI" for the least engineering
   risk — no backend changes needed).
4. Decide the i18n scope question (2.1) before investing more translation work either direction.
5. Turn on WAL mode (2.2) — cheap insurance, do it whenever `database.py` is next touched.
6. Revisit hosting/multi-tenancy (2.3) as a deliberate decision, not a side effect of a UI sprint.

---

## 5. Second-pass findings (setup, encoding, portability)

These came out of a follow-up look at the setup scripts, repo hygiene, and the page-rendering flow.

### 5.1 `start-app.cmd` prefers the system Python over `.venv`
`scripts/start_app.ps1:93-101` tests plain `python` for uvicorn **first** and only falls back to
`.venv\Scripts\python.exe`. If uvicorn is installed globally, the project venv is silently ignored,
so you can run the app against different dependency versions than the ones you pinned. The same
block also just prints instructions and exits when nothing has uvicorn, which is the entire manual
setup story in the README. Making that script create the venv and install `requirements.txt` turns
setup into one command (`start-app.cmd`) and removes four README steps.

### 5.2 Double-encoded text in source, and console-hostile log formats
`app/services/planner.py:921,944,947` contain literal `a-hat` mojibake (`â€"`, `â‚¬`) — UTF-8 that
was once read as cp1252 and re-saved. Separately, `core/logging_config.py:141,155` put a real
em-dash in the console format string, which the cp1252 Windows console renders as `?` on every log
line (visible in every test run today). Log *files* are opened as UTF-8 and are fine. Using ASCII in
the strings that reach the console removes the whole class of problem.

### 5.3 The local database is still tracked by git
`data/` is in `.gitignore`, but `data/dmproject.db` was committed before that rule existed, so git
still tracks it (`git ls-files data` confirms). It holds real participant names, home addresses and
coordinates plus 11 trip-history entries. The live database moved to `%LOCALAPPDATA%\DMProject` in
July, so the tracked copy is stale as well as private. `git rm --cached` fixes it going forward;
the old blobs stay in commits `5417e99` and `dfddb43`, which only matters if this repo is ever made
public or shown to someone outside — at that point it needs a `git filter-repo` scrub.

### 5.4 Results disappear when you navigate to Planning
Optimization output only renders at `/planning?view=optimization` (`app/main.py:856-875`). Click
"Planning" in the nav and the results vanish, even though they are cached and still valid — the
cache is already keyed on a hash of the workspace (`current_workspace_key()`), so it invalidates
itself when the data changes. The `view` parameter adds plumbing to `_build_page_url`,
`_redirect_to_page`, and both run handlers to reproduce information the cache already has. Deleting
it removes code *and* fixes the "where did my plan go" moment.

### 5.5 The workspace backup cannot be restored, and is lossy
`/workspace/export` downloads `dmproject-workspace.json`, but there is no import route — nothing can
read it back. It is also incomplete: `list_groups()` and `list_trip_history()` select metadata only,
without `snapshot_json` / `result_json`, so saved crews and past trips could not be restored even by
hand. For a tool whose whole premise is "everything runs on your laptop", the move-to-a-new-laptop
path should work end to end.

### 5.6 Five overlapping names for the same few ideas
The app calls overlapping concepts by eight different names — "working dataset", "current dataset",
"live workspace", "saved snapshots", "saved groups", "trip history", "saved trip snapshot",
"automatic workspace backup" — each with its own explanatory paragraph. The underlying storage is
fine; the vocabulary is what makes the Setup page hard to read. Four words cover all of it:
**Current trip**, **Saved crews**, **Past trips**, **Undo last replace**. That is a copy change with
no migration risk, and it deletes several paragraphs of explanation along the way.

### 5.7 Things confirmed healthy (so they are not on the list)
- All 81 tests pass (`python -m unittest tests.test_web_app`, ~23s).
- `style.css` uses zero `!important` — the cascade is not being fought.
- `httpx2` / `httpcore2` in `requirements-dev.txt` are correct for Starlette 1.3's `TestClient`;
  they are not typos.
- The service worker's caching split (stale-while-revalidate for static, network-first for pages) is
  sensible as written.
