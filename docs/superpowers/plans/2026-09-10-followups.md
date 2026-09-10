# Follow-ups after the Autostrada redesign, help mode, pictograms and motorbike

Written 2026-09-10 at the end of the session that shipped `main` up to `6bd7eef`. Everything below
is either a known small defect or a deferred review finding. None of it blocks using the app.
Work through it top to bottom next session; each item names the file and the shape of the fix.

**Test command (must stay `OK`, currently 119 tests):**
`.venv\Scripts\python.exe -m unittest tests.test_web_app tests.test_source_hygiene tests.test_apca_refinement tests.test_check`
Bump `STATIC_ASSET_VERSION` in `app/main.py` whenever `style.css` or `app.js` changes. The live
instance is started with `start-app.cmd` (no `--reload`): run `stop-app.cmd` then `start-app.cmd`
after merging.

## Already fixed this session (for the record)

- People tab side column inflated to ~1036px by the participant table (`style.css` `.ws-panel > *` and
  grid children now `min-width: 0`; add form is two columns inside the panel).
- Editing a participant reset their consumption to the fuel default on page load (`app.js`).
- Guest motorbike with >2 seats is now rejected with the same error as the organizer form; the invite
  sandbox clamps legacy rows and passes `vehicle_type` to `Car(...)`.
- Dark-mode `i` button off-state contrast (`--sign-blue-ink`); `.rail-sign-blue` defined.
- Destination and CSV-import validation errors re-render on the right tab; participant field errors
  are translated; the page guide no longer re-expands on every load.
- Tests for the legacy-database migration, CSV import of `vehicle_type`, the rendered vehicle cell,
  and the guest seat limit.

## 1. Motorbike loose ends (small)

- [ ] Guest form: default `total_seats` to 2 when `vehicle_type == motorbike` and the field is blank
      (organizer form already does; `app/main.py` guest submission handler, mirror the admin path).
- [ ] History snapshot → restore test for `vehicle_type` (mirror
      `test_saved_group_and_restore_history_flow`; assert the restored participant keeps `motorbike`).
- [ ] `ensure_schema_ready()` (`app/database.py` ~270) short-circuits once `app_settings` exists, so
      `_ensure_column` migrations run only through the FastAPI startup hook. Decide: either call the
      column migrations from `ensure_schema_ready` too, or document that scripts must call `init_db()`.

## 2. Help mode and guides (small)

- [ ] `.guide { display: none }` until JS adds `body.show-help` causes a layout shift on every load and
      hides the guide entirely without JS. Render the panel visible by default and let JS hide it when
      help is off (`style.css` ~2115, `app.js` `applyHelp`).
- [ ] Help mode shows the inline hint *and* the browser's native tooltip for the same `title`. Either
      move the text to a `data-hint` attribute when help is on, or accept the duplication.
- [ ] The Results sign cards' fuel/toll breakdown lives on a `<span title>` that the
      `:is(h2,h3,summary,p,strong)[title]` selector ignores; add `span.sign-cost` (or similar) to the
      selector so the most useful tooltip is surfaced in help mode.
- [ ] Guest invite form has no guide; add a short "How to reply" `<details>` if guests ask.

## 3. Deferred from the redesign's final review (`docs/superpowers/plans/2026-09-09-autostrada-redesign.md`)

- [ ] Dark mode: green `border-color` on non-text elements (`.order-badge`, `.language-menu-option.active`,
      `.impact-strip`) is ~2.1:1; use `--sign-green-ink` for borders too, or accept (non-text).
- [ ] Share report map SVG (`app/main.py::_build_share_map_svg`) is light-only; fine for print, wrong
      inside the dark share page. Either render it on a white card regardless of theme or add dark tokens.
- [ ] No `<h1>` on Plan, Share, Trips, Settings (visually-hidden `<h1>` per page, or promote the first `<h2>`).
- [ ] Tabs have `role="tablist"` but no arrow-key navigation (six-line `keydown` handler in `app.js`).
- [ ] Driver messages, flash messages and most error strings are English-only
      (`_build_driver_messages`, `FLASH_MESSAGES`, `_build_share_summary`); README already states the gap.
- [ ] `index.html` `<head>` duplicates `_brand_head.html` (fonts, theme bootstrap); include the partial
      so the next font change is one edit.
- [ ] Dead translation key `Saved Crew` (kept only because it is a substring of `Saved Crews`);
      `.badge-service` border half is inert on `#food-overlay-status`; `list_trip_history()` is called
      twice per request in `build_context`; DOM-id tripwire regex `[a-z-]+` would miss ids with digits.
- [ ] Downloaded share report still fetches Google Fonts and the manifest over the network (CSS is
      inlined; the fallback font stack works offline).

## 4. Testing gaps worth closing

- [ ] There is no JS test runner, so `app.js` logic (tab switcher, help toggle, consumption defaults,
      show-on-map) is only exercised by hand. Cheapest option: a `tests/test_static_js.py` that runs
      a handful of behaviours through `playwright-cli`/Playwright-for-Python against a `TestClient`-free
      throwaway server, gated behind an env var so the default suite stays fast.
- [ ] CSS contrast is asserted by token literals (`test_dark_theme_has_readable_green_text`), not
      computed ratios. A small helper computing WCAG contrast for the token pairs used as text would
      catch the next palette regression.
- [ ] Screenshot pass after visual changes: the 1360/390 light+dark set from Task 12 is the reference
      (`.superpowers/sdd/.../shots/` was deleted with the worktree; regenerate with `playwright-cli`).

## 5. Housekeeping

- [ ] Delete the leftover worktree folder `.claude/worktrees/autostrada` (git no longer tracks it; the
      editor session held it open).
- [ ] The impeccable design hook's ignore for the tab divider lives in `.impeccable/config.json`
      (git-ignored, per checkout); re-add with
      `hook-admin.mjs ignore-value side-tab "*" --file app/static/style.css` if the hook flags it again.
- [ ] Process note for parallel agents: the harness's worktree isolation branches from `origin/main`,
      not the session head; every parallel dispatch must start with `git reset --hard <base>`.
