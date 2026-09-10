"""Source-level guards for mistakes that are invisible at runtime."""

from __future__ import annotations

import ast
import os
import re
import unittest
from pathlib import Path

from app.i18n import TRANSLATIONS

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRS = (REPO_ROOT / "app", REPO_ROOT / "core")
TEMPLATE_DIR = REPO_ROOT / "app" / "templates"
T_CALL_PATTERN = re.compile(
    # Negative lookbehind avoids matching "...ct(" inside unrelated calls
    # (e.g. Jinja's select('equalto', ...)); the backreference lets the
    # captured text contain the other quote character (e.g. an apostrophe
    # inside a double-quoted string).
    r"""(?<!\w)t\(\s*(["'])((?:(?!\1).)*)\1"""
)


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

    @unittest.skipIf(os.environ.get("DM_SKIP_I18N"), "translations are filled in a dedicated task")
    def test_all_template_translation_keys_exist_in_every_language(self) -> None:
        """A t("...") call with no matching dict entry falls back to the raw English key."""
        keys: set[str] = set()
        for path in sorted(TEMPLATE_DIR.rglob("*.html")):
            text = path.read_text(encoding="utf-8")
            keys.update(match.group(2) for match in T_CALL_PATTERN.finditer(text))
        for language in ("it", "fr", "es"):
            translations = TRANSLATIONS[language]
            missing = sorted(key for key in keys if key not in translations)
            self.assertEqual(missing, [], f"'{language}' translation dict is missing keys: {missing}")

    def test_every_dom_id_app_js_looks_up_exists_in_index_html(self) -> None:
        """app.js reaching for an id no template renders is how the Setup map broke before."""
        source = (REPO_ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        # index.html plus the partials it includes (#theme-toggle lives in _topbar.html)
        html = "".join(
            path.read_text(encoding="utf-8")
            for path in [TEMPLATE_DIR / "index.html", *sorted(TEMPLATE_DIR.glob("_*.html"))]
        )
        looked_up = set(re.findall(r'getElementById\("([a-z-]+)"\)', source))
        missing = sorted(element_id for element_id in looked_up if f'id="{element_id}"' not in html)
        self.assertEqual(missing, [], f"app.js looks up ids index.html never renders: {missing}")

    def test_old_palette_does_not_survive_the_redesign(self) -> None:
        """The Autostrada tokens replaced the cream/terracotta palette everywhere."""
        old_colors = ("#ef6a3a", "#cb4d1f", "#f2bf4d", "#4ebd91", "#f4ede2", "#0f1d35", "Space Grotesk")
        files = [REPO_ROOT / "app" / "static" / "style.css", *sorted(TEMPLATE_DIR.rglob("*.html"))]
        for path in files:
            text = path.read_text(encoding="utf-8").lower()
            leftovers = [color for color in old_colors if color.lower() in text]
            self.assertEqual(leftovers, [], f"{path.name} still uses the old palette: {leftovers}")

    def test_style_css_has_balanced_braces_and_no_empty_rules(self) -> None:
        """Deleting selectors by hand has already orphaned a rule body once."""
        text = (REPO_ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        self.assertEqual(text.count("{"), text.count("}"), "style.css has unbalanced braces")
        self.assertIsNone(re.search(r"\{\s*\}", text), "style.css has an empty rule body")

    def test_dark_theme_has_readable_green_text(self) -> None:
        """Dark-mode green-on-paper text needs its own lighter token to clear AA."""
        text = (REPO_ROOT / "app" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertIn("--sign-green-ink: #5BC98A", text)
        dark_block = text.split(':root[data-theme="dark"] {', 1)[1].split("}", 1)[0]
        self.assertNotIn("--sign-green: #12904A", dark_block)

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
