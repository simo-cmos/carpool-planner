# Streamline Drivers Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the known bugs, delete the dead weight, collapse the overlapping workspace concepts, and make the app one-command to set up — so the next feature is cheap to add.

**Architecture:** No rewrites and no new dependencies. Every task is either a deletion, a guard, or a small replacement inside the existing FastAPI + Jinja + vanilla-JS structure. Three new source-level tests (`tests/test_source_hygiene.py`) act as tripwires for the failure classes that already bit this codebase once: duplicate definitions, double-encoded UTF-8, and elements the JS assumes exist. Phases are independently shippable — stop after any phase and the app is in a better state than before.

**Tech Stack:** Python 3.12.4, FastAPI 0.139, Starlette 1.3, Jinja2 3.1, SQLite (stdlib), Leaflet 1.9 via CDN, plain-IIFE `app.js`, `unittest` + `fastapi.testclient.TestClient`, PowerShell launchers.

**Spec:** `docs/IMPROVEMENT_PLAN.md` (audit findings this plan implements)

## Global Constraints

- **Python:** 3.12.4, interpreter at `.venv\Scripts\python.exe`. Never use `pytest` — this repo is `unittest`.
- **Test command:** `.venv\Scripts\python.exe -m unittest tests.test_web_app` — 81 tests, ~23s, must be green (`OK`) at the end of every task.
- **New test file:** `tests/test_source_hygiene.py`, run with `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene`.
- **No new runtime dependencies.** `requirements.txt` stays exactly: `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `qrcode`.
- **No JS build toolchain.** `app/static/app.js` stays one plain IIFE loaded by `<script src>`. Do not add npm, bundlers, or JS test runners.
- **ASCII only in strings that reach the Windows console** (log format strings, `LOG_INFO` messages). The console is cp1252; em-dashes print as `?`. Templates and log *files* are UTF-8 and may keep real punctuation.
- **Branch:** work on `streamline`, not `main`. Create it in Task 0.
- **Commits:** one per task, message ends with:
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
- **Bump `STATIC_ASSET_VERSION`** in `app/main.py` (currently `"20260720-dark-vote"`) in any task that edits `app/static/app.js` or `app/static/style.css`, so browsers and the service worker pick the change up.

---

## File Structure

| File | Responsibility | Touched by |
|---|---|---|
| `app/static/app.js` | Map, autocomplete, drag-reorder, toasts | T1 |
| `app/services/planner.py` | Driver selection + APCA orchestration, map payload | T2, T4 |
| `core/utils.py` | Haversine + sample data | T3 |
| `app/services/settings.py` | App defaults, sharing URLs | T3 |
| `app/services/__init__.py` | Public service surface (`__all__`) | T3, T10 |
| `app/services/destinations.py`, `groups.py`, `history.py`, `workspace.py` | Per-concept persistence | T3 (imports), T10 (workspace) |
| `core/logging_config.py` | LOG_INFO/LOG_DEBUG, file + console handlers | T4 |
| `scripts/start_app.ps1` | Background launcher | T5 |
| `app/database.py` | SQLite connection, schema, settings KV | T7 |
| `app/services/participants.py` | Participant CRUD, CSV | T8 |
| `app/services/invites.py` | Guest invites and responses | T8 |
| `app/main.py` | Routes, page rendering, redirects | T9, T10 |
| `app/templates/index.html` | The whole organizer UI | T11, T12, T13 |
| `tests/test_source_hygiene.py` | **New.** Source-level tripwires | T1, T2, T4 |
| `tests/test_web_app.py` | Route + service tests | T1, T7, T8, T9, T10, T11 |
| `README.md` | Setup instructions | T5 |

---

### Task 0: Branch

- [ ] **Step 1: Create the working branch**

```bash
git checkout -b streamline
git status
```

Expected: `On branch streamline`, working tree clean apart from the untracked `docs/` files from the audit.

- [ ] **Step 2: Confirm the audit docs are committed**

```bash
git log --oneline -1 -- docs/
```

Expected: `795dee3 new assessment and development plan` — both `docs/IMPROVEMENT_PLAN.md` and this
plan are already committed on `main`, so there is nothing to add here.

---

# Phase 1 — Bugs and dead code

## Task 1: Fix the Setup-page map crash

`drawMap()` dereferences `summaryElement` (`#map-summary`), which `index.html` only renders on
`/planning`. On `/setup` the map still loads, so every map load throws a `TypeError` there and
silently skips the `fitToPayload(...)` and `loadFoodOverlay()` calls at the end of the function —
which is why the Setup map never auto-fits to your markers.

**Files:**
- Modify: `app/static/app.js:456-462`
- Modify: `app/main.py:136` (bump `STATIC_ASSET_VERSION`)
- Create: `tests/test_source_hygiene.py`
- Modify: `tests/test_web_app.py` (add one test to `WebAppTests`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `tests/test_source_hygiene.py` with module-level helpers `REPO_ROOT: Path`,
  `SOURCE_DIRS: tuple[Path, ...]`, `_python_files() -> list[Path]`, and class
  `SourceHygieneTests(unittest.TestCase)`. Tasks 2 and 4 add methods to that same class.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_hygiene.py`:

```python
"""Source-level guards for mistakes that are invisible at runtime."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = (REPO_ROOT / "app", REPO_ROOT / "core")


def _python_files() -> list[Path]:
    """Return every first-party Python source file."""
    return [
        path
        for directory in SOURCE_DIRS
        for path in sorted(directory.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


class SourceHygieneTests(unittest.TestCase):
    def test_app_js_guards_the_optional_map_summary(self) -> None:
        """#map-summary only renders on /planning, so app.js must tolerate its absence."""
        source = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("if (summaryElement)", source)
```

Add to `tests/test_web_app.py`, inside `class WebAppTests`, right after
`test_planning_page_renders_map_workspace`:

```python
    def test_map_pages_agree_on_which_elements_exist(self) -> None:
        """Setup shows the map without #map-summary; app.js has to cope with that."""
        setup = self.client.get("/setup")
        self.assertIn('id="planner-map"', setup.text)
        self.assertNotIn('id="map-summary"', setup.text)

        planning = self.client.get("/planning")
        self.assertIn('id="planner-map"', planning.text)
        self.assertIn('id="map-summary"', planning.text)
```

- [ ] **Step 2: Run the tests to verify the new one fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene -v`
Expected: FAIL — `AssertionError: 'if (summaryElement)' not found in ...`

Run: `.venv\Scripts\python.exe -m unittest tests.test_web_app.WebAppTests.test_map_pages_agree_on_which_elements_exist -v`
Expected: PASS (it documents current, correct template behavior — it is the invariant the JS fix relies on).

- [ ] **Step 3: Apply the guard**

In `app/static/app.js`, replace this block (currently at lines 456-462):

```js
        if (selectedRouteSet) {
            summaryElement.classList.remove("muted");
            summaryElement.innerHTML = `<strong>${selectedRouteSet.driver_set_name}</strong><span>Fitness ${selectedRouteSet.fitness.toFixed(3)}</span><span>${selectedRouteSet.total_distance_km.toFixed(1)} km</span><span>${selectedRouteSet.total_duration_min == null ? "time unavailable" : Math.round(selectedRouteSet.total_duration_min) + " min"}</span>`;
        } else {
            summaryElement.classList.add("muted");
            summaryElement.textContent = "Run optimization to preview routes on the map.";
        }
```

with:

```js
        // #map-summary only exists on /planning; /setup shows the same map without it.
        if (summaryElement) {
            if (selectedRouteSet) {
                summaryElement.classList.remove("muted");
                summaryElement.innerHTML = `<strong>${selectedRouteSet.driver_set_name}</strong><span>Fitness ${selectedRouteSet.fitness.toFixed(3)}</span><span>${selectedRouteSet.total_distance_km.toFixed(1)} km</span><span>${selectedRouteSet.total_duration_min == null ? "time unavailable" : Math.round(selectedRouteSet.total_duration_min) + " min"}</span>`;
            } else {
                summaryElement.classList.add("muted");
                summaryElement.textContent = "Run optimization to preview routes on the map.";
            }
        }
```

- [ ] **Step 4: Bump the static asset version**

In `app/main.py` line 136, change:

```python
STATIC_ASSET_VERSION = "20260720-dark-vote"
```

to:

```python
STATIC_ASSET_VERSION = "20260908-streamline"
```

- [ ] **Step 5: Run both test modules**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK` — 83 tests.

- [ ] **Step 6: Verify in the browser (no JS test harness exists, and adding one is not worth it)**

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000/setup`, open DevTools console. Expected: no `TypeError`, and the map
auto-fits to the participant/destination markers on load. Then open `/planning` and confirm the
route summary line still renders after running an optimization. Stop the server with Ctrl+C.

- [ ] **Step 7: Commit**

```bash
git add app/static/app.js app/main.py tests/test_source_hygiene.py tests/test_web_app.py
git commit -m "fix: stop map crash on Setup page when #map-summary is absent

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Delete the shadowed duplicate `run_sandbox_optimization`

`app/services/planner.py` defines `run_sandbox_optimization` twice. Python keeps the second one, so
the first (67 lines, an older implementation with no meetup-pooling and a different participant
transform) is unreachable — and a future edit landing in the wrong copy would silently do nothing.

**Files:**
- Modify: `app/services/planner.py:783-850` (delete)
- Modify: `tests/test_source_hygiene.py` (add one test)

**Interfaces:**
- Consumes: `SourceHygieneTests`, `_python_files()` from Task 1.
- Produces: no signature changes. `run_sandbox_optimization(participants, destination, *, plan_return_separately: bool = False, plan_preference: str = "efficiency") -> dict[str, Any]` keeps the behavior of the surviving (second) definition.

- [ ] **Step 1: Write the failing test**

Add to `class SourceHygieneTests` in `tests/test_source_hygiene.py` (and add `import ast` to the
imports at the top of the file):

```python
    def test_no_duplicate_top_level_definitions(self) -> None:
        """A second def with the same name silently shadows the first one."""
        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = [
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            duplicates = sorted({name for name in names if names.count(name) > 1})
            self.assertEqual(duplicates, [], f"{path.name} defines these more than once: {duplicates}")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene -v`
Expected: FAIL — `planner.py defines these more than once: ['run_sandbox_optimization']`

- [ ] **Step 3: Delete the dead first definition**

In `app/services/planner.py`, delete lines 783 through 850 inclusive: the whole first
`def run_sandbox_optimization(...)` block, which starts with

```python
def run_sandbox_optimization(
    participants,
    destination,
    *,
    plan_return_separately: bool = False,
    plan_preference: str = "efficiency",
) -> dict[str, Any]:
    """Run the normal optimization pipeline on a transient participant pool."""
    validate_ready_state(participants, destination)
    defaults = get_app_settings()["optimization"]
    selector = DriverSelector(participants, destination)
```

and ends with the closing brace of its return dict:

```python
        "destination_data": asdict(destination),
        "fuel_prices": fuel_prices,
    }
```

immediately before `def build_impact_summary(participants, destination, best_result)`.

Keep exactly two blank lines between `def select_best_result(...)`'s preceding neighbor and
`def build_impact_summary(...)`. The **second** definition (the one that calls
`_optimize_single_driver_set` and `_best_meetup_variant`, previously at line 960) stays untouched.

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK` — the duplicate test passes and the guest-preview sandbox tests still pass, proving
the surviving definition is the one in use.

- [ ] **Step 5: Commit**

```bash
git add app/services/planner.py tests/test_source_hygiene.py
git commit -m "refactor: delete shadowed duplicate run_sandbox_optimization

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Delete dead functions and unused imports

**Files:**
- Modify: `core/utils.py:1-10,45-66`
- Modify: `app/services/settings.py:101-116`
- Modify: `app/services/__init__.py:70,148`
- Modify: `app/services/destinations.py:7`, `app/services/groups.py:8`, `app/services/history.py:9`, `app/services/workspace.py:7`

**Interfaces:**
- Consumes: nothing.
- Produces: `core.utils` exports only `haversine_distance` and `get_sample_data`.
  `app.services` no longer exports `save_public_base_url`; `save_public_access_url(*, mode, public_base_url)` and `save_public_access_urls(*, guest_public_base_url, admin_public_base_url)` remain the only sharing-URL writers.

- [ ] **Step 1: Confirm nothing calls what you are about to delete**

```bash
grep -rn "build_distance_matrix\|save_public_base_url" --include=*.py --include=*.html . | grep -v ".venv"
```

Expected: only the definition sites and the two `app/services/__init__.py` lines. If anything else
appears, stop and re-scope this task.

- [ ] **Step 2: Delete `build_distance_matrix` and its now-unused imports**

In `core/utils.py`, delete lines 45-66 (the whole `def build_distance_matrix(...)` block), then
change the imports at the top from:

```python
import math
from typing import List
from core.logging_config import LOG_DEBUG
from core.models import Location
```

to:

```python
import math

from core.models import Location
```

- [ ] **Step 3: Delete `save_public_base_url`**

In `app/services/settings.py`, delete lines 101-116 (the whole `def save_public_base_url(...)` block).

In `app/services/__init__.py` line 70, change:

```python
from app.services.settings import get_app_settings, save_app_settings, save_public_access_url, save_public_access_urls, save_public_base_url
```

to:

```python
from app.services.settings import get_app_settings, save_app_settings, save_public_access_url, save_public_access_urls
```

and delete the `    "save_public_base_url",` line from `__all__`.

- [ ] **Step 4: Drop the four unused `LOG_DEBUG` imports**

In each of `app/services/destinations.py`, `app/services/groups.py`, `app/services/history.py`, and
`app/services/workspace.py`, change:

```python
from core.logging_config import LOG_INFO, LOG_DEBUG
```

to:

```python
from core.logging_config import LOG_INFO
```

(Leave `common.py`, `participants.py`, `settings.py`, `notifier.py`, and `planner.py` alone — those
actually call `LOG_DEBUG`.)

- [ ] **Step 5: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add core/utils.py app/services/
git commit -m "refactor: delete unused build_distance_matrix, save_public_base_url and dead imports

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Fix double-encoded text and console-hostile log formats

Three log strings in `planner.py` contain literal `â€”` and `â‚¬` — UTF-8 bytes that were once read
as cp1252 and re-saved. Separately, `logging_config.py` puts a real em-dash in the console format
string, which prints as `?` on a cp1252 Windows console (visible in every test run today).

**Files:**
- Modify: `app/services/planner.py:921,944,947`
- Modify: `core/logging_config.py:104,141,155`
- Modify: `tests/test_source_hygiene.py` (add one test)

**Interfaces:**
- Consumes: `SourceHygieneTests`, `_python_files()` from Task 1.
- Produces: log lines formatted `"%-30s %-28s | %s"` instead of using an em-dash separator.

- [ ] **Step 1: Write the failing test**

Add to `class SourceHygieneTests`:

```python
    def test_no_double_encoded_utf8_in_sources(self) -> None:
        """UTF-8 re-saved as cp1252 leaves 'a-hat' sequences behind; catch them at the source."""
        broken_sequences = ("â€", "â‚¬")
        for path in _python_files():
            text = path.read_text(encoding="utf-8")
            for sequence in broken_sequences:
                self.assertNotIn(
                    sequence,
                    text,
                    f"{path.name} contains double-encoded UTF-8 - retype the character",
                )
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene -v`
Expected: FAIL — `planner.py contains double-encoded UTF-8 - retype the character`

- [ ] **Step 3: Repair the three `planner.py` strings**

Line 921:

```python
        LOG_INFO(f"optimising driver set '{driver_set.name}' - {len(driver_set.driver_indices)} drivers")
```

Line 944:

```python
    LOG_INFO(f"full optimization completed in {elapsed:.3f}s - {len(results)} trip results")
```

Line 947:

```python
        LOG_INFO(f"best result: '{best_result.driver_set_name}' - fitness={best_result.fitness:.4f}, distance={best_result.total_distance_km:.2f}km, cost=EUR {best_result.total_cost_eur:.2f}")
```

- [ ] **Step 4: Make the log formats console-safe**

In `core/logging_config.py`, line 104:

```python
    _logger.info("=== Drivers Manager Project - log session started ===")
```

line 141 (inside `LOG_INFO`):

```python
    _logger.info("%-30s %-28s | %s", filename, function, message)  # type: ignore[union-attr]
```

line 155 (inside `LOG_DEBUG`):

```python
    _logger.debug("%-30s %-28s | %s", filename, function, message)  # type: ignore[union-attr]
```

- [ ] **Step 5: Run the tests and read the output**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK`, and the streamed log lines now read `main.py  home  | rendering home page` with no
`?` replacement characters anywhere.

- [ ] **Step 6: Commit**

```bash
git add app/services/planner.py core/logging_config.py tests/test_source_hygiene.py
git commit -m "fix: repair double-encoded log strings and use ASCII console separators

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 2 — Robustness and setup

## Task 5: Make `start-app.cmd` bootstrap the environment and prefer the venv

Today `start_app.ps1` tries the **system** `python` first and only falls back to `.venv`, so a
globally installed uvicorn silently wins over the project environment. And if neither has uvicorn it
just prints instructions and exits, which is the whole manual setup story from the README.

**Files:**
- Modify: `scripts/start_app.ps1:93-101`
- Modify: `README.md:22-31`

**Interfaces:**
- Consumes: nothing.
- Produces: `start-app.cmd` becomes the single entry point — it creates `.venv`, installs
  `requirements.txt` into it, and starts the server + notifier.

- [ ] **Step 1: Replace the interpreter-selection block**

In `scripts/start_app.ps1`, replace lines 93-101:

```powershell
if (Test-UvicornAvailable -PythonCommand "python") {
    $pythonExe = "python"
} elseif ((Test-Path $venvPython) -and (Test-UvicornAvailable -PythonCommand $venvPython)) {
    $pythonExe = $venvPython
} else {
    Write-Host "No suitable Python interpreter with uvicorn was found."
    Write-Host "Install dependencies with: python -m pip install -r requirements.txt"
    exit 1
}
```

with:

```powershell
# The project venv always wins: a globally installed uvicorn must never shadow it.
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in .venv ..."
    & python -m venv (Join-Path $repoRoot ".venv")
}

if ((Test-Path $venvPython) -and -not (Test-UvicornAvailable -PythonCommand $venvPython)) {
    Write-Host "Installing dependencies (first run only, this can take a minute) ..."
    & $venvPython -m pip install --disable-pip-version-check --quiet -r (Join-Path $repoRoot "requirements.txt")
}

if ((Test-Path $venvPython) -and (Test-UvicornAvailable -PythonCommand $venvPython)) {
    $pythonExe = $venvPython
} elseif (Test-UvicornAvailable -PythonCommand "python") {
    Write-Host "Using the system Python: .venv could not be prepared."
    $pythonExe = "python"
} else {
    Write-Host "Could not prepare a Python environment with uvicorn."
    Write-Host "Check that 'python' is on PATH, then retry, or set it up manually:"
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\python -m pip install -r requirements.txt"
    exit 1
}
```

- [ ] **Step 2: Verify a cold start from a clean environment**

```bash
git stash list
mv .venv .venv-backup
cmd //c start-app.cmd
```

Expected: it prints `Creating virtual environment in .venv ...`, then
`Installing dependencies (first run only ...)`, then `Drivers Manager started in background.` with
a URL and PID. Confirm `http://127.0.0.1:8000` loads.

- [ ] **Step 3: Restore the dev environment (it also has the test dependency)**

```bash
cmd //c stop-app.cmd
rm -rf .venv
mv .venv-backup .venv
.venv/Scripts/python.exe -m unittest tests.test_web_app
```

Expected: `OK` — 83 tests.

- [ ] **Step 4: Simplify the README quick start**

In `README.md`, replace the `## Quick Start` block (lines 22-31):

````markdown
## Quick Start

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).
````

with:

````markdown
## Quick Start

From `cmd.exe`, in the repo folder:

```bat
start-app.cmd
```

That creates `.venv`, installs dependencies on first run, and starts the app plus the desktop
notifier in the background. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).
Use `status-app.cmd` and `stop-app.cmd` to check on it and shut it down.

For a foreground dev server with auto-reload:

```bash
.venv\Scripts\python -m uvicorn app.main:app --reload
```
````

- [ ] **Step 5: Commit**

```bash
git add scripts/start_app.ps1 README.md
git commit -m "feat: start-app.cmd bootstraps .venv and prefers it over system python

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Untrack the committed database

`data/` is in `.gitignore`, but `data/dmproject.db` was committed before that rule existed, so git
still tracks it. It holds real participant names, home addresses and coordinates, plus 11 trip
history entries. The live database has moved to `%LOCALAPPDATA%\DMProject`, so the tracked copy is
stale as well as private.

**Files:**
- Modify: git index only (the file stays on disk)

**Interfaces:**
- Consumes: nothing.
- Produces: nothing importable. `app/database.py::_ensure_data_dir()` keeps copying
  `data/dmproject.db` to the user data dir when present, which still works for this machine.

- [ ] **Step 1: Confirm what is tracked**

```bash
git ls-files data
```

Expected: `data/dmproject.db`

- [ ] **Step 2: Untrack it, keeping the file on disk**

```bash
git rm --cached data/dmproject.db
git ls-files data
ls -la data/dmproject.db
```

Expected: `git ls-files data` prints nothing; the file still exists on disk.

- [ ] **Step 3: Run the tests (they use `DMPROJECT_DATA_DIR`, not this file)**

Run: `.venv\Scripts\python.exe -m unittest tests.test_web_app`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: untrack local database with personal trip data

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Note the follow-up decision (do not act on it here)**

Untracking removes the file going forward; the old blobs stay in history (commits `5417e99`,
`dfddb43`). If this repo is ever made public or handed to an investor/collaborator, that history
needs scrubbing with `git filter-repo --path data/dmproject.db --invert-paths` followed by a force
push. Record that decision in `docs/IMPROVEMENT_PLAN.md` rather than doing it mid-plan — it rewrites
every commit hash.

---

## Task 7: Turn on WAL mode and a real busy timeout

Three writers share one SQLite file: the web server, the fuel-price sync thread started in
`on_startup`, and the separate `notifier_watcher.py` process polling every few seconds. Default
journal mode makes writers block the whole file.

**Files:**
- Modify: `app/database.py:30-35,60-62`
- Modify: `tests/test_web_app.py` (add one test)

**Interfaces:**
- Consumes: nothing.
- Produces: `get_connection() -> sqlite3.Connection` now opens with a 10-second busy timeout;
  the database file is in WAL mode after `init_db()`.

- [ ] **Step 1: Write the failing test**

Add to `class WebAppTests` in `tests/test_web_app.py`:

```python
    def test_database_uses_wal_journal_mode(self) -> None:
        """WAL lets the notifier process read while the web app writes."""
        init_db()
        with database.get_connection() as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(journal_mode.lower(), "wal")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_web_app.WebAppTests.test_database_uses_wal_journal_mode -v`
Expected: FAIL — `'delete' != 'wal'`

- [ ] **Step 3: Set the busy timeout on every connection**

In `app/database.py`, change `get_connection`:

```python
def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection with row-style access."""
    _ensure_data_dir()
    # 10s busy timeout: the web app, the fuel-price thread and the notifier
    # process all write to this file.
    connection = sqlite3.connect(DB_PATH, timeout=10.0)
    connection.row_factory = sqlite3.Row
    return connection
```

- [ ] **Step 4: Switch the database to WAL once, at init**

In `app/database.py::init_db`, inside the `with get_connection() as connection:` block, add the
pragma as the first statement — before `connection.executescript(...)` (a journal-mode change cannot
run inside a transaction, and `executescript` commits first):

```python
        with get_connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
```

- [ ] **Step 5: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK` — 84 tests.

- [ ] **Step 6: Commit**

```bash
git add app/database.py tests/test_web_app.py
git commit -m "feat: enable SQLite WAL mode and a 10s busy timeout

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Return the new participant id instead of guessing it

`save_participant` throws away the insert id, so `import_trip_response` recovers it with
`SELECT id FROM participants ORDER BY id DESC LIMIT 1` — correct only while nothing else writes.

**Files:**
- Modify: `app/services/participants.py:71-160`
- Modify: `app/services/invites.py:289-311`
- Modify: `tests/test_web_app.py` (add one test)

**Interfaces:**
- Consumes: `execute_insert(query, parameters) -> int` from `app.database` (already imported by `invites.py`; add it to the `participants.py` import).
- Produces: `save_participant(...) -> int` — returns the new row id on insert, or the
  `participant_id` it was given on update. All existing call sites ignore the return value and keep working.

- [ ] **Step 1: Write the failing test**

Add to `class WebAppTests` in `tests/test_web_app.py`:

```python
    def test_save_participant_returns_the_new_row_id(self) -> None:
        new_id = save_participant(
            participant_id=None,
            name="Zoe",
            address_text=None,
            location_name="Carpi",
            latitude=44.7839,
            longitude=10.8853,
            has_car=False,
            fuel_type=None,
            consumption_l_per_100km=None,
            total_seats=None,
            habit_score=0.0,
        )
        self.assertIsInstance(new_id, int)
        self.assertEqual(get_participant(new_id)["name"], "Zoe")
        self.assertEqual(save_participant(
            participant_id=new_id,
            name="Zoe Renamed",
            address_text=None,
            location_name="Carpi",
            latitude=44.7839,
            longitude=10.8853,
            has_car=False,
            fuel_type=None,
            consumption_l_per_100km=None,
            total_seats=None,
            habit_score=0.0,
        ), new_id)
```

Extend the `from app.services import ...` line (line 24 of `tests/test_web_app.py`) with
`get_participant` and `save_participant`.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_web_app.WebAppTests.test_save_participant_returns_the_new_row_id -v`
Expected: FAIL — `None is not an instance of int`

- [ ] **Step 3: Return the id from `save_participant`**

In `app/services/participants.py`, change the import on line 10:

```python
from app.database import execute, execute_insert, fetch_all, fetch_one
```

change the signature terminator on line 99 from `) -> None:` to `) -> int:`, then change the insert
branch (lines 129-145) from `execute(` to `execute_insert(` and return the id:

```python
    if participant_id is None:
        new_participant_id = execute_insert(
            """
            INSERT INTO participants(
                name, address_text, location_name, latitude, longitude,
                pickup_mode, pickup_location_name, pickup_address_text, pickup_latitude, pickup_longitude,
                has_car, fuel_type,
                consumption_l_per_100km, total_seats, habit_score, role_tag, availability_tag,
                pickup_flexible, priority_rank, active_in_trip, force_drive_alone,
                outbound_earliest_time, outbound_latest_time, return_earliest_time, return_latest_time
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        _mark_dataset_customized()
        return new_participant_id
```

and end the update branch with a return:

```python
    _mark_dataset_customized()
    return participant_id
```

- [ ] **Step 4: Use it in `import_trip_response`**

In `app/services/invites.py`, replace lines 296-302:

```python
    participant_snapshot = _build_participant_snapshot_from_response(response, len(list_participants()) + 1)
    save_participant(
        participant_id=None,
        **participant_snapshot,
    )
    participant = fetch_one("SELECT id FROM participants ORDER BY id DESC LIMIT 1")
    participant_id = int(participant["id"]) if participant is not None else None
```

with:

```python
    participant_snapshot = _build_participant_snapshot_from_response(response, len(list_participants()) + 1)
    participant_id = save_participant(participant_id=None, **participant_snapshot)
```

- [ ] **Step 5: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK` — 85 tests, including the existing guest-response import tests.

- [ ] **Step 6: Commit**

```bash
git add app/services/participants.py app/services/invites.py tests/test_web_app.py
git commit -m "refactor: return participant id from save_participant instead of re-querying

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 3 — Workflow simplification

## Task 9: Delete the `view` query parameter

Results only render at `/planning?view=optimization`. Click "Planning" in the nav and your results
vanish — even though they are cached and still valid. The cache is already workspace-keyed
(`current_workspace_key()`), so it returns `None` by itself once the workspace changes. The `view`
parameter is pure ceremony on top of that.

**Files:**
- Modify: `app/main.py:481-494` (`_build_page_url`), `541-550` (`_redirect_to_page`), `856-875` (`_render_app_page`), `1773`, `1787`
- Modify: `tests/test_web_app.py:1211-1221`

**Interfaces:**
- Consumes: `get_cached_optimization_result() -> dict | None` and `get_cached_selection_result() -> dict | None` from `app.services` (already imported in `main.py`).
- Produces: `_build_page_url(page: str, flash: str | None = None, scroll: str | None = None) -> str` and `_redirect_to_page(request, page=None, flash=None, scroll=None) -> RedirectResponse` — both lose the `view` keyword. Nothing else calls them with `view`.

- [ ] **Step 1: Update the test to describe the behavior you want**

In `tests/test_web_app.py`, replace lines 1211-1221:

```python
        selection_response = self.client.post("/selection", follow_redirects=False)
        self.assertEqual(selection_response.status_code, 303)
        self.assertIn("view=selection", selection_response.headers["location"])

        optimization_response = self.client.post("/optimization", follow_redirects=False)
        self.assertEqual(optimization_response.status_code, 303)
        self.assertIn("view=optimization", optimization_response.headers["location"])

        rendered = self.client.get(optimization_response.headers["location"])
        self.assertEqual(rendered.status_code, 200)
        self.assertIn("Optimization Results", rendered.text)
```

with:

```python
        selection_response = self.client.post("/selection", follow_redirects=False)
        self.assertEqual(selection_response.status_code, 303)
        self.assertIn("/planning", selection_response.headers["location"])
        self.assertNotIn("view=", selection_response.headers["location"])

        optimization_response = self.client.post("/optimization", follow_redirects=False)
        self.assertEqual(optimization_response.status_code, 303)
        self.assertIn("/planning", optimization_response.headers["location"])

        rendered = self.client.get(optimization_response.headers["location"])
        self.assertEqual(rendered.status_code, 200)
        self.assertIn("Optimization Results", rendered.text)

        # Cached results survive plain navigation, not just the post-run redirect.
        revisited = self.client.get("/planning")
        self.assertEqual(revisited.status_code, 200)
        self.assertIn("Optimization Results", revisited.text)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_web_app.WebAppTests.test_selection_and_optimization_use_redirect_flow -v`

Expected: FAIL — `'view=' unexpectedly found in '/planning?flash=...&view=selection'`

- [ ] **Step 3: Always load the cached results on the planning page**

In `app/main.py`, replace `_render_app_page` (lines 856-875):

```python
def _render_app_page(request: Request, page: str):
    """Render one top-level app page with whatever planner results are still valid."""
    flash = request.query_params.get("flash", "")
    scroll_target = request.query_params.get("scroll", "")
    optimization = get_cached_optimization_result() if page == "planning" else None
    # The optimization payload carries scores and driver sets too, so it also
    # feeds the Driver Selection panel; fall back to the selection-only cache.
    selection = optimization or (get_cached_selection_result() if page == "planning" else None)
    return render_home(
        request,
        active_page=page,
        success=FLASH_MESSAGES.get(flash),
        scroll_target=scroll_target or None,
        selection=selection,
        optimization=optimization,
    )
```

- [ ] **Step 4: Remove `view` from the URL builders**

In `app/main.py`, change `_build_page_url` (line 481) to:

```python
def _build_page_url(page: str, flash: str | None = None, scroll: str | None = None) -> str:
```

and delete its two `view` lines (490-491):

```python
    if view:
        query_parts.append(f"view={quote(view)}")
```

Change `_redirect_to_page` (lines 541-550) to:

```python
def _redirect_to_page(
    request: Request,
    page: str | None = None,
    flash: str | None = None,
    scroll: str | None = None,
) -> RedirectResponse:
    """Redirect the user back to the most relevant app page."""
    resolved_page = _infer_request_page(request, explicit_page=page, scroll_target=scroll)
    return RedirectResponse(_build_page_url(resolved_page, flash=flash, scroll=scroll), status_code=303)
```

- [ ] **Step 5: Drop `view` from the two call sites**

Line 1773 becomes:

```python
    return _redirect_to_page(request, page="planning", flash="selection-completed", scroll="driver-selection-section")
```

Line 1787 becomes:

```python
    return _redirect_to_page(request, page="planning", flash="optimization-completed", scroll="optimization-results-section")
```

- [ ] **Step 6: Confirm no `view` plumbing is left**

```bash
grep -n "view" app/main.py | grep -v "review\|preview\|Review\|Preview\|viewBox\|overview\|Overview"
```

Expected: no matches.

- [ ] **Step 7: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add app/main.py tests/test_web_app.py
git commit -m "feat: keep planner results visible without the view query parameter

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Make the workspace backup actually restorable

`/workspace/export` produces `dmproject-workspace.json` that nothing can read back — there is no
import route. Worse, the export is lossy: `list_groups()` and `list_trip_history()` return metadata
only (no `snapshot_json` / `result_json`), so saved crews and past trips could not be restored even
by hand. This is the "move to another machine" story, so it should work end to end.

**Files:**
- Modify: `app/services/workspace.py` (whole file)
- Modify: `app/services/__init__.py:71,109` (export the new function)
- Modify: `app/main.py:122-134` (flash), `2382-2389` (next to the export route)
- Modify: `app/templates/index.html:278-282` (import form beside the export link)
- Modify: `tests/test_web_app.py` (add one round-trip test)

**Interfaces:**
- Consumes: `load_workspace_snapshot(snapshot: dict) -> None`, `backup_current_workspace(reason: str) -> None`, `current_workspace_snapshot() -> dict` from `app.services.common`; `execute`, `fetch_all` from `app.database`.
- Produces: `import_workspace_backup(payload_json: str) -> dict[str, int]` returning
  `{"participants": int, "destinations": int, "groups": int, "history": int}`; raises `ValueError`
  for anything that is not a Drivers Manager export.

- [ ] **Step 1: Write the failing test**

Add to `class WebAppTests` in `tests/test_web_app.py`:

```python
    def test_workspace_backup_round_trips_through_import(self) -> None:
        self.client.post(
            "/destination",
            data={"name": "Office", "latitude": "44.6471", "longitude": "10.9252"},
        )
        self.client.post(
            "/participants",
            data={
                "name": "Alice",
                "location_name": "Reggio Emilia",
                "latitude": "44.6989",
                "longitude": "10.6310",
                "has_car": "true",
                "fuel_type": "gasoline",
                "total_seats": "5",
            },
        )
        self.client.post("/groups", data={"group_name": "Weekend crew"})
        exported = self.client.get("/workspace/export").text

        clear_all_data()
        execute("DELETE FROM saved_groups")
        self.assertEqual(list_participants(), [])

        response = self.client.post(
            "/workspace/import",
            files={"backup_file": ("dmproject-workspace.json", exported, "application/json")},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual([person["name"] for person in list_participants()], ["Alice"])
        self.assertEqual([group["name"] for group in list_groups()], ["Weekend crew"])

    def test_workspace_import_rejects_a_foreign_file(self) -> None:
        response = self.client.post(
            "/workspace/import",
            files={"backup_file": ("notes.json", '{"hello": "world"}', "application/json")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a Drivers Manager workspace backup", response.text)
```

Extend the `from app.services import ...` line with `list_groups`.

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv\Scripts\python.exe -m unittest tests.test_web_app.WebAppTests.test_workspace_backup_round_trips_through_import -v`
Expected: FAIL — 405 or 404, because `/workspace/import` does not exist.

- [ ] **Step 3: Rewrite `app/services/workspace.py`**

```python
"""Workspace export and import helpers."""

from __future__ import annotations

import json
from typing import Any

from core.logging_config import LOG_INFO
from app.database import execute, fetch_all
from app.services.common import (
    backup_current_workspace,
    current_workspace_snapshot,
    load_workspace_snapshot,
)
from app.services.destinations import list_saved_destinations, save_destination_favorite

BACKUP_FORMAT = "dmproject-workspace"
BACKUP_VERSION = 1


def export_workspace_backup() -> str:
    """Serialize the whole local workspace into a restorable JSON backup."""
    LOG_INFO("exporting full workspace backup")
    return json.dumps(
        {
            "format": BACKUP_FORMAT,
            "version": BACKUP_VERSION,
            "workspace": current_workspace_snapshot(),
            "destinations": list_saved_destinations(),
            "groups": [
                dict(row)
                for row in fetch_all("SELECT name, snapshot_json FROM saved_groups ORDER BY id ASC")
            ],
            "history": [
                dict(row)
                for row in fetch_all(
                    """
                    SELECT trip_name, trip_date, preset_name, notes, snapshot_json, result_json, created_at
                    FROM trip_history
                    ORDER BY id ASC
                    """
                )
            ],
        },
        indent=2,
    )


def import_workspace_backup(payload_json: str) -> dict[str, int]:
    """Restore a backup produced by export_workspace_backup()."""
    try:
        payload = json.loads(payload_json)
    except json.JSONDecodeError as error:
        raise ValueError("That file is not valid JSON.") from error
    workspace = payload.get("workspace") if isinstance(payload, dict) else None
    if not isinstance(workspace, dict) or "participants" not in workspace:
        raise ValueError("That file is not a Drivers Manager workspace backup.")

    LOG_INFO("importing workspace backup")
    backup_current_workspace("import_workspace_backup")
    load_workspace_snapshot(workspace)

    destinations: list[dict[str, Any]] = payload.get("destinations") or []
    for destination in destinations:
        save_destination_favorite(
            name=destination["name"],
            address_text=destination.get("address_text"),
            latitude=float(destination["latitude"]),
            longitude=float(destination["longitude"]),
            target_arrival_time=destination.get("target_arrival_time"),
        )

    groups: list[dict[str, Any]] = payload.get("groups") or []
    for group in groups:
        execute(
            """
            INSERT INTO saved_groups(name, snapshot_json)
            VALUES(?, ?)
            ON CONFLICT(name) DO UPDATE SET snapshot_json = excluded.snapshot_json
            """,
            (group["name"], group["snapshot_json"]),
        )

    history: list[dict[str, Any]] = payload.get("history") or []
    for entry in history:
        execute(
            """
            INSERT INTO trip_history(trip_name, trip_date, preset_name, notes, snapshot_json, result_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["trip_name"],
                entry.get("trip_date"),
                entry.get("preset_name"),
                entry.get("notes"),
                entry["snapshot_json"],
                entry["result_json"],
                entry.get("created_at"),
            ),
        )

    return {
        "participants": len(workspace.get("participants", [])),
        "destinations": len(destinations),
        "groups": len(groups),
        "history": len(history),
    }
```

- [ ] **Step 4: Export the new function**

In `app/services/__init__.py`, change line 71:

```python
from app.services.workspace import export_workspace_backup, import_workspace_backup
```

and add `    "import_workspace_backup",` to `__all__` next to `"export_workspace_backup"`.

- [ ] **Step 5: Add the route**

In `app/main.py`, add `import_workspace_backup` to the big `from app.services import (...)` block
(alphabetically, after `import_trip_response`). Add a flash entry to `FLASH_MESSAGES`:

```python
    "workspace-imported": "Workspace backup imported. Your previous workspace was backed up automatically.",
```

and add this route immediately after `export_workspace`:

```python
@app.post("/workspace/import")
async def import_workspace(request: Request, backup_file: UploadFile = File(...)):
    """Restore a previously exported workspace backup file."""
    content = await backup_file.read()
    try:
        result = import_workspace_backup(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        LOG_INFO(f"workspace import rejected: {error}")
        return render_home(
            request,
            status_code=400,
            error=str(error) or "Workspace import failed.",
            scroll_target="destination-panel",
        )
    LOG_INFO(f"workspace import restored {result}")
    return _redirect_to_page(request, page="setup", flash="workspace-imported", scroll="participants-panel")
```

- [ ] **Step 6: Add the upload control next to the export link**

In `app/templates/index.html`, replace the export link block (lines 278-282):

```html
                        <div class="inline-actions">
                            <form method="post" action="/sample"><button type="submit">Load sample data</button></form>
                            <form method="post" action="/reset"><button class="secondary" type="submit" data-confirm="Reset all participants and the active destination? Saved favorites, groups, history, and settings will stay.">Reset active workspace</button></form>
                            <a class="button-link secondary-link" href="/workspace/export">Export workspace backup</a>
                        </div>
```

with:

```html
                        <div class="inline-actions">
                            <form method="post" action="/sample"><button type="submit">Load sample data</button></form>
                            <form method="post" action="/reset"><button class="secondary" type="submit" data-confirm="Reset the current trip? Saved crews, favorites and past trips stay.">Reset current trip</button></form>
                            <a class="button-link secondary-link" href="/workspace/export">Export backup</a>
                        </div>
                        <form method="post" action="/workspace/import" enctype="multipart/form-data" class="stack-form compact-stack-form">
                            <label><span>Restore a backup file</span><input type="file" name="backup_file" accept=".json" required></label>
                            <button class="secondary" type="submit" data-confirm="Replace the current trip with this backup? Your current trip is backed up automatically first.">Import backup</button>
                        </form>
```

- [ ] **Step 7: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK` — 87 tests. If `test_reset_button_copy`-style assertions fail on the reset wording,
update those assertions to the new copy in the same commit.

- [ ] **Step 8: Commit**

```bash
git add app/services/workspace.py app/services/__init__.py app/main.py app/templates/index.html tests/test_web_app.py
git commit -m "feat: make workspace backups restorable with a /workspace/import route

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 4 — UI and verbosity

> These three tasks change user-visible copy. Run the full suite after each one — several existing
> tests assert on exact strings (`tests/test_web_app.py` lines 56-63, 592, 633-634).

## Task 11: One vocabulary for the five overlapping concepts

The app currently calls overlapping things by eight different names: "working dataset", "current
dataset", "live workspace", "saved snapshots", "saved groups", "trip history", "saved trip
snapshot", "automatic workspace backup". Users cannot tell "Save snapshot" (participants only) from
"Save run to history" (workspace + result). Fix the words, not the schema.

| Concept | Storage | Old names | New name |
|---|---|---|---|
| Live participants + destination | `participants` table + `destination` setting | working dataset / live workspace / current dataset | **Current trip** |
| Reusable people list | `saved_groups` | saved snapshots / saved groups | **Saved crew** |
| Finished runs | `trip_history` | trip history / saved trip snapshot | **Past trips** |
| Pre-restore safety copy | `workspace_backup` setting | automatic workspace backup | **Undo last replace** |
| Guest intake session | `trip_invites` + `trip_responses` | guest session workspace | **Guest session** (unchanged) |

**Files:**
- Modify: `app/templates/index.html` (headings, summaries, button labels, `data-confirm` text)
- Modify: `app/main.py:122-134` (`FLASH_MESSAGES` wording)
- Modify: `tests/test_web_app.py:592,633-634` (assertions on flash copy)

**Interfaces:**
- Consumes: nothing. No database, route, or function names change — this is copy only.
- Produces: no code interface. Later tasks assume these headings exist:
  `Current trip`, `Saved crews`, `Past trips`.

- [ ] **Step 1: Rename the flash messages**

In `app/main.py`, replace these four entries in `FLASH_MESSAGES`:

```python
    "workspace-backup-restored": "Undid the last replace. Your previous trip is back.",
    "history-duplicated": "Latest past trip loaded. Your previous trip was saved first.",
    "history-restored": "Past trip loaded. Your previous trip was saved first.",
    "history-deleted": "Past trip deleted.",
    "invite-workspace-loaded": "Guest session loaded as the current trip. Your previous trip was saved first.",
```

- [ ] **Step 2: Update the two tests that assert on that copy**

In `tests/test_web_app.py` line 592, change:

```python
        self.assertIn("Saved trip snapshot deleted.", response.text)
```

to:

```python
        self.assertIn("Past trip deleted.", response.text)
```

and lines 633-634:

```python
        self.assertIn("Guest session loaded as the current trip", response.text)
        self.assertIn("Current trip", response.text)
```

- [ ] **Step 3: Rename the headings and labels in `index.html`**

Apply these replacements (left → right), keeping surrounding markup intact:

| Current text | Replace with |
|---|---|
| `Working Dataset & CSV` | `Current Trip & CSV` |
| `Current working dataset` | `Current trip` |
| `This trip is currently saved as a custom local copy.` | `Not linked to a saved crew or guest session.` |
| `Admin edits apply to the current trip. You can start from guest responses, then adjust the trip manually.` | *(delete this line entirely)* |
| `Saved Snapshots` | `Saved Crews` |
| `Save current workspace as a reusable snapshot` | `Save these people as a crew` |
| `Save snapshot` | `Save crew` |
| `Load snapshot` | `Load crew` |
| `No saved snapshots yet.` | `No saved crews yet.` |
| `Delete this saved snapshot?` | `Delete this crew?` |
| `Use as working dataset` | `Use as current trip` |
| `Refresh workspace` | `Refresh from guests` |
| `Replace the current working dataset with this guest session? Your current workspace will be backed up automatically.` | `Replace the current trip with this guest session? Your current trip is saved first.` |
| `Automatic workspace backup available` | `Undo available` |
| `Created before a duplicate/restore action` | `From the last trip replacement` |
| `Restore automatic backup` | `Undo last replace` |
| `Restore the automatic workspace backup? This will replace the current live workspace.` | `Undo the last replace and bring back the previous trip?` |
| `Replace with latest saved trip` | `Load latest past trip` |
| `Replace the current workspace with the latest saved trip? An automatic backup of your current workspace will be created first.` | `Load the latest past trip? Your current trip is saved first.` |
| `Save run to history` | `Save this trip` |
| `Restore live workspace` | `Load this trip` |
| `Replace the current workspace with this saved trip? An automatic backup of your current workspace will be created first.` | `Load this past trip? Your current trip is saved first.` |
| `Delete snapshot` | `Delete` |
| `Delete this saved trip snapshot from history? This will not change your current live workspace or reusable snapshots.` | `Delete this past trip?` |
| `No saved trip history yet.` | `No past trips yet.` |

Also update `APP_PAGES` in `app/main.py`: the `history` entry's `"label"` becomes `"Past Trips"`,
its `"title"` becomes `"Past Trips"`, and its `"description"` becomes `"Load or delete past trips."` —
otherwise the top nav tab still says "History" while the panel underneath says "Past Trips".

- [ ] **Step 4: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK`. If an assertion fails on renamed copy, update the assertion — do not revert the copy.

- [ ] **Step 5: Click through the renamed flows**

Start the app, then confirm each of these still works and reads consistently: save a crew, load a
crew, save a trip, load a past trip, delete a past trip, undo a replace, use a guest session as the
current trip.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/templates/index.html tests/test_web_app.py
git commit -m "refactor: one vocabulary for current trip, saved crews and past trips

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: Move the always-on explanations into tooltips

Nearly every panel pairs a label with a full paragraph of prose that is visible on every page load.
Shorten the visible line to a phrase and keep the full explanation in a `title` attribute.

**Files:**
- Modify: `app/templates/index.html` (lines ~493, 581, 585, 595, 621, 642, 616, 224, 257, 270, 376, 483)
- Modify: `app/static/style.css` (one new rule)
- Modify: `app/main.py:136` (bump `STATIC_ASSET_VERSION` again if CSS changed)

**Interfaces:**
- Consumes: the headings from Task 11.
- Produces: a `.hint` CSS class used by later UI work.

- [ ] **Step 1: Add the hint style**

Append to `app/static/style.css`:

```css
/* Short inline hint with the full explanation in the title attribute. */
.hint {
    border-bottom: 1px dotted currentColor;
    cursor: help;
    opacity: 0.75;
    font-size: 0.85rem;
}
```

- [ ] **Step 2: Replace the long explanations**

Apply each of these. The pattern is: visible phrase stays short, the old sentence moves into `title`.

Pickup Order Rules (line ~595):

```html
                            <span class="hint" title="Outbound-only soft rule: it nudges the pickup order when both riders share a car, without forcing an impossible route.">Soft rule, same car only</span>
```

Ride Together Rules (line ~621):

```html
                            <span class="hint" title="Hard planning constraint: the optimizer will not split this pair, so only use it for real requirements.">Hard rule, keeps a pair together</span>
```

Return Trip Planning (line ~585):

```html
                            <span class="hint" title="Plans the way back separately from the outbound trip, for when the group or the time windows change later in the night.">Plan the way back separately</span>
```

"Before you run" card (lines ~579-582) — delete the whole `<article>`; its advice is now covered by
the two hints above and the checklist on the Overview page.

Participants table intro (line ~493):

```html
                    <p class="hint" title="Habit score affects driver selection. List order is display only. Drag rows by the handle to reorder.">Toggle who is in tonight's trip without deleting anyone.</p>
```

Map cards (lines ~224 and ~257) — delete both `<span class="muted">` lines about the checkered
marker and the red selection marker; the map legend directly above already says the same thing.

Meetup spots empty state (line ~483):

```html
                                    <p class="muted">No meetup spots yet. Add parking, park-and-ride or EV charging points.</p>
```

Pickup/ride-rule empty states (lines ~616 and ~642):

```html
                            <p class="muted">No rules yet.</p>
```

Coordinates hint (line ~270):

```html
                                <p class="hint" title="Filled automatically from the address search or a map click.">Optional</p>
```

- [ ] **Step 3: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK`

- [ ] **Step 4: Check the pages still explain themselves**

Start the app and read `/setup` and `/planning` as if for the first time. Every control should still
be understandable from its label plus a hover. Bump `STATIC_ASSET_VERSION` in `app/main.py` to
`"20260908-streamline-2"` so the new CSS reaches browsers.

- [ ] **Step 5: Commit**

```bash
git add app/templates/index.html app/static/style.css app/main.py
git commit -m "refactor: replace always-on help paragraphs with short hints

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 13: Collapse the badge walls in the results cards

Each trip-result card renders up to ten badges and each assignment renders six. Show the four that
matter at a glance and put the rest behind a disclosure.

**Files:**
- Modify: `app/templates/index.html:830-842` (trip card badges), `856-866` (assignment badges)

**Interfaces:**
- Consumes: the `.hint` class from Task 12 (not required, but keep the styling consistent).
- Produces: no code interface.

- [ ] **Step 1: Split the trip-card badge row**

Replace the badge row at lines 830-842 with a headline row plus a details disclosure:

```html
                    <div class="badge-row">
                        {% if cheapest_cost is not none and trip.total_cost_eur == cheapest_cost and not trip.unassigned_passenger_indices %}<span class="badge badge-cheapest">{{ t("Cheapest plan") }}</span>{% endif %}
                        <span class="badge">{{ trip.assignments|length }} cars</span>
                        <span class="badge">{{ '%.0f'|format(trip.total_distance_km) }} km</span>
                        <span class="badge">EUR {{ '%.2f'|format(trip.total_cost_eur) }}</span>
                        {% if trip.plan_variant == 'meetup' %}<span class="badge">Meetup pooling</span>{% endif %}
                    </div>
                    <details class="result-details">
                        <summary>Details</summary>
                        <div class="badge-row">
                            <span class="badge">Fitness {{ '%.4f'|format(trip.fitness) }}</span>
                            <span class="badge">Longest route {{ '%.0f'|format(trip.max_route_duration_min) }} min</span>
                            <span class="badge">Route spread {{ '%.0f'|format(trip.route_duration_spread_min) }} min</span>
                            <span class="badge">Timing mismatch {{ '%.0f'|format(trip.total_time_window_violation_min) }} min</span>
                            {% if trip.plan_variant == 'meetup' %}<span class="badge">Self-transfer {{ '%.1f'|format(trip.meetup_total_self_transfer_km) }} km</span>{% endif %}
                            {% if trip.return_assignments %}<span class="badge">Return mismatch {{ '%.0f'|format(trip.return_total_time_window_violation_min) }} min</span>{% endif %}
                        </div>
                    </details>
```

- [ ] **Step 2: Trim the per-assignment badges**

Replace the assignment badge row at lines 858-865 with:

```html
                            <div class="badge-row">
                                <span class="badge">{{ assignment.passenger_indices|length }} passengers</span>
                                <span class="badge">{{ '%.0f'|format(assignment.route_distance_km) }} km</span>
                                <span class="badge" title="Fuel EUR {{ '%.2f'|format(assignment.fuel_cost_eur) }} + estimated toll EUR {{ '%.2f'|format(assignment.toll_cost_eur) }}">EUR {{ '%.2f'|format(assignment.cost_per_person_eur) }}/person</span>
                                {% if assignment.time_window_violation_min > 0 %}<span class="badge">Timing mismatch {{ '%.0f'|format(assignment.time_window_violation_min) }} min</span>{% endif %}
                            </div>
```

> **Careful:** `index.html:898` renders the same `Estimated toll EUR` text inside the *return plan*
> block. That line is a different element and stays exactly as it is — edit the badge row at 858-865
> only, never with a global find/replace.

- [ ] **Step 3: Run the tests**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK`. Existing tests assert on `"Optimization Results"` and driver-set names, not on badge
text, so this should pass unchanged — if a badge assertion fails, update it.

- [ ] **Step 4: Verify with real results**

Start the app, load the sample data, run a full optimization, and confirm each plan card shows four
headline badges with the rest under "Details".

- [ ] **Step 5: Commit**

```bash
git add app/templates/index.html
git commit -m "refactor: show four headline metrics per plan and hide the rest

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 5 — Translations (decision-gated)

## Task 14: Finish or formally scope the i18n coverage

`app/i18n.py` carries full IT/FR/ES dictionaries, and the guest form, landing page and login are
fully wrapped in `t()`. The organizer cockpit is not: the participant table, planner controls,
settings form and the whole optimization-results section are hardcoded English. A non-English
organizer gets a translated shell around an English workspace.

**Do this task after Phase 4, not before** — Tasks 11-13 delete and shorten a large fraction of the
strings, so translating first means translating text that is about to disappear.

**Decision to make first (record it in `docs/IMPROVEMENT_PLAN.md`):**
- **Option A — finish it.** Wrap the remaining organizer strings and add the three translations.
  Roughly 120 strings after Phase 4. Right choice if non-Italian-speaking users matter for the pitch.
- **Option B — scope it.** Keep translations for guest-facing pages only (the guests are the ones who
  never saw the app before), and state that in the README. Zero new work, and it stops the
  half-finished feeling by making it a deliberate boundary.

**Files (Option A):**
- Modify: `app/templates/index.html` (wrap remaining literals in `t(...)`)
- Modify: `app/i18n.py` (add the same keys to the `it`, `fr` and `es` dictionaries)
- Modify: `README.md` (state the coverage either way)

**Interfaces:**
- Consumes: `t(key, **kwargs)` injected into every template context by `_base_template_context`, and
  `translate(language, key, **kwargs)` in `app/i18n.py`. Keys are the English source strings; a
  missing key falls back to English, so partial coverage never breaks a page.
- Produces: no code interface.

- [ ] **Step 1: List what is still untranslated**

```bash
grep -n "<summary>\|<h2>\|<h3>\|<button type=\"submit\">" app/templates/index.html | grep -v "{{ t(" | head -60
```

Expected: the working list of headings and buttons to wrap, in file order.

- [ ] **Step 2: Wrap one section, verify, repeat**

For each region in this order — Destination panel, Participants panel, Planner panel, Settings form,
Optimization results — change `<summary>Participants</summary>` style literals into
`<summary>{{ t("Participants") }}</summary>`, then add the key to all three dictionaries in
`app/i18n.py`, for example:

```python
        "Participants": "Partecipanti",
```

Do one region per commit so a broken translation is easy to bisect.

- [ ] **Step 3: Verify each language renders**

Start the app and switch language with the flag selector. Check `/setup` and `/planning` in `it`,
then confirm an untranslated key still shows English rather than a crash.

- [ ] **Step 4: Run the tests after each region**

Run: `.venv\Scripts\python.exe -m unittest tests.test_source_hygiene tests.test_web_app`
Expected: `OK`. Tests assert English strings on the default (`en`) language, so wrapping alone does
not break them — a wrong key name will surface as an assertion failure.

- [ ] **Step 5: Commit per region**

```bash
git add app/templates/index.html app/i18n.py
git commit -m "i18n: translate the participants panel

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Finishing the branch

- [ ] **Run everything one more time**

```bash
.venv/Scripts/python.exe -m unittest tests.test_source_hygiene tests.test_web_app
.venv/Scripts/python.exe -m tests.test_check
```

Expected: `OK` from both.

- [ ] **Update the audit doc**

Mark the items in `docs/IMPROVEMENT_PLAN.md` that this branch closed, and record the two deferred
decisions: the `data/dmproject.db` history scrub (Task 6, Step 5) and the i18n scope choice
(Task 14).

- [ ] **Merge**

```bash
git checkout main
git merge --no-ff streamline
```

---

## Deliberately not in this plan

- **Rewriting the APCA scoring.** The pheromone-starvation edge case in
  `core/apca.py::_update_pheromones` degrades gracefully (the run falls back to random construction
  and the deterministic 2-opt pass still cleans up). Logging it is a one-liner if it ever matters;
  reworking the deposit rule is a research task, not a cleanup.
- **Merging `saved_groups` into `trip_history`.** The schemas overlap, but Task 11 removes the actual
  user-facing confusion for none of the migration risk. Revisit only if the two concepts still feel
  redundant after using the renamed UI for a while.
- **Multi-tenancy / hosted accounts.** A real decision (see `docs/IMPROVEMENT_PLAN.md` §2.3), not a
  cleanup task. It changes the data model everywhere and should get its own plan.
- **A JS test toolchain.** Two source-level tripwires in `tests/test_source_hygiene.py` cover the
  bug class that actually bit this app. npm, a bundler and a headless browser would cost more to
  maintain than `app.js` costs to read.
