"""Every class a first-party stylesheet styles is used somewhere.

The dead-code ratchet reads Python symbols only, so a rule left behind when a
component moved (to the vendored button, say) shipped unnoticed. This pins the
class-selector side: a `.name` in `styles*.css` must appear as a whole token in
the static HTML/JS or the Python that emits markup. Vendored stylesheets are
out of scope; their classes belong to the vendored components.
"""

from __future__ import annotations

# Standard library imports
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "app" / "webapp" / "static"
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_CLASS = re.compile(r"\.([A-Za-z_][\w-]*)")
_DECL_BLOCK = re.compile(r"\{[^{}]*\}")

# Classes composed at runtime (string concatenation), which no literal scan can
# see. Keep empty unless a class is genuinely built dynamically; say why here.
DYNAMIC_CLASSES: frozenset[str] = frozenset()


def _markup_sources() -> str:
    """All text that can name a class: static html/js plus the Python emitting markup."""
    paths = [*STATIC_DIR.rglob("*.html"), *STATIC_DIR.rglob("*.js")]
    paths += [*(REPO_ROOT / "app").rglob("*.py"), *(REPO_ROOT / "src").rglob("*.py")]
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in paths)


def _styled_classes(css: str) -> set[str]:
    """Class names in selectors only (comments and declaration bodies removed)."""
    css = _COMMENT.sub("", css)
    # Two passes unwrap one level of @media nesting, leaving selectors bare.
    for _ in range(2):
        css = _DECL_BLOCK.sub("{}", css)
    return set(_CLASS.findall(css))


def test_every_first_party_css_class_is_used() -> None:
    sources = _markup_sources()
    orphans: list[str] = []
    for sheet in sorted(STATIC_DIR.glob("styles*.css")):
        for name in sorted(_styled_classes(sheet.read_text(encoding="utf-8"))):
            if name in DYNAMIC_CLASSES:
                continue
            if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", sources):
                orphans.append(f"{sheet.name}: .{name}")
    assert not orphans, "CSS classes no markup or script uses (delete the rule): " + ", ".join(orphans)
