# ╔══════════════════════════════════════════════════════════════════╗
# ║  DDB — manual                                                    ║
# ║  « one Markdown file, read in the app and on the web »           ║
# ╠══════════════════════════════════════════════════════════════════╣
# ║  Locates the user manual (docs/manual/index.md) and parses its   ║
# ║  headings into sections with mkdocs-compatible slugs, so the     ║
# ║  in-app Help window and the docs site share one source of truth. ║
# ╚══════════════════════════════════════════════════════════════════╝
"""User-manual helpers that need no Qt.

The manual is written in the subset of Markdown that both renderers
understand: Qt's ``QTextDocument.setMarkdown`` (CommonMark + GitHub
tables) and mkdocs. That rules out admonitions (``!!! tip``), attribute
lists and tabs; use a blockquote starting with ``**Tip:**`` instead.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

# ┌────────────────────────────────────────────────────────────┐
# │ Locating  « the manual ships inside the repo clone »       │
# └────────────────────────────────────────────────────────────┘

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANUAL_DIR = REPO_ROOT / "docs" / "manual"
MANUAL_PATH = MANUAL_DIR / "index.md"
SCREENSHOT_DIR = MANUAL_DIR / "img"

# Main-window tab title → manual heading that F1 opens on that tab.
TAB_SECTIONS: dict[str, str] = {
    "Scan": "Scan tab",
    "Reports": "Reports tab",
    "Genotypes": "Genotypes tab",
    "Settings": "Settings tab",
}


def load_manual(path: Path = MANUAL_PATH) -> str:
    """Return the manual's Markdown source."""
    return path.read_text(encoding="utf-8")


# ┌────────────────────────────────────────────────────────────┐
# │ Sections  « headings → slugs, the way mkdocs makes them »  │
# └────────────────────────────────────────────────────────────┘

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Section:
    level: int
    title: str
    slug: str
    line: int  # 1-based line number in the source


def slugify(title: str) -> str:
    """Python-Markdown's default ``toc`` slug, so ``#links`` work in both renderers."""
    text = re.sub(r"[*_`]", "", title)  # inline emphasis/code markers
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def parse_sections(markdown: str) -> list[Section]:
    """ATX headings in document order, skipping fenced code blocks."""
    sections: list[Section] = []
    in_fence = False
    for number, line in enumerate(markdown.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            title = match.group(2)
            sections.append(Section(len(match.group(1)), title, slugify(title), number))
    return sections


# ┌────────────────────────────────────────────────────────────┐
# │ Link checks  « for the docs-drift tests »                  │
# └────────────────────────────────────────────────────────────┘

_LINK = re.compile(r"(!?)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def image_targets(markdown: str) -> list[str]:
    """Relative paths of every ``![alt](path)`` image."""
    return [target for bang, target in _LINK.findall(markdown) if bang]


def anchor_targets(markdown: str) -> list[str]:
    """Slugs of every in-page ``[text](#slug)`` link."""
    return [
        target[1:]
        for bang, target in _LINK.findall(markdown)
        if not bang and target.startswith("#")
    ]
