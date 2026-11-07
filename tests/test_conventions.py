"""The no-emoji gate.

specs/tech-stack.md section 13 bans emoji and pictographs across the whole
codebase - source, comments, docstrings, log messages, test names, commit
messages, CLI output and UI strings - because a financial system's output gets
grepped, piped, parsed and exported to CSV, and pictographs survive none of
that well.

**It bans pictographs, not non-ASCII.** Typographic punctuation, box-drawing
characters in diagrams, currency symbols, and any non-ASCII character occurring
in real marketplace or seller data all stay legal and must never be stripped.
That distinction is the whole difficulty of this test, and getting it wrong in
either direction is worse than not having it:

  - A draft of this check used `unicodedata.category(ch) == "So"`. Box-drawing
    characters are category `So`, so it flagged the topology diagram in
    specs/tech-stack.md section 4 - content that document permits by name.
  - The usable rule is Unicode's `Extended_Pictographic` property, which
    `unicodedata` does not expose. Adding the `regex` package to reach it would
    mean amending specs/tech-stack.md section 1, so explicit codepoint ranges
    are used instead.

This repository's own `specs/` are tracked files full of legitimate non-ASCII,
which makes them the test's best fixture rather than something to exclude.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Codepoint ranges treated as pictographic. Deliberately conservative: it
#: covers what actually appears in practice rather than every codepoint Unicode
#: could call a symbol, because a false positive here blocks legitimate content.
PICTOGRAPHIC_RANGES: tuple[tuple[int, int], ...] = (
    (0x1F000, 0x1FAFF),  # Mahjong through Symbols and Pictographs Extended-A
    (0x2600, 0x26FF),  # Miscellaneous Symbols: weather, warning signs
    (0x2700, 0x27BF),  # Dingbats: check marks, crosses, decorative stars
    (0x2B50, 0x2B50),  # White medium star
    (0x2B55, 0x2B55),  # Heavy large circle
    (0xFE0F, 0xFE0F),  # Variation Selector-16, the emoji presentation selector
)

#: Extensions whose contents are not text and must not be decoded.
_BINARY_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".woff", ".woff2", ".zip"}
)


def is_pictographic(char: str) -> bool:
    codepoint = ord(char)
    return any(low <= codepoint <= high for low, high in PICTOGRAPHIC_RANGES)


def _tracked_text_files() -> list[Path]:
    """Every tracked file.

    Walking the index rather than the working tree puts build output, `.venv`
    and untracked scratch files out of scope by construction.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        PROJECT_ROOT / name
        for name in result.stdout.split("\0")
        if name and Path(name).suffix.lower() not in _BINARY_SUFFIXES
    ]


def test_no_pictographs_in_tracked_files() -> None:
    offenders: list[str] = []

    for path in _tracked_text_files():
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError):
            continue  # binary or a broken symlink; neither carries prose

        for line_number, line in enumerate(content.splitlines(), start=1):
            for char in line:
                if is_pictographic(char):
                    relative = path.relative_to(PROJECT_ROOT)
                    offenders.append(
                        f"{relative}:{line_number}: U+{ord(char):04X}"
                    )

    assert not offenders, "Pictographs found:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "char",
    [
        "—",  # em dash
        "→",  # rightwards arrow
        "│",  # box drawings light vertical
        "─",  # box drawings light horizontal
        "├",  # box drawings light vertical and right
        "§",  # section sign
        "₦",  # naira sign
        "€",  # euro sign
        "•",  # bullet
        "e",  # a plain ASCII letter
    ],
)
def test_permitted_characters_are_not_flagged(char: str) -> None:
    """The direction of this gate that is easy to get wrong.

    Every character here is one specs/tech-stack.md section 13 permits by name,
    or occurs in real seller data. A gate that has only ever been seen passing
    has not been tested.
    """
    assert not is_pictographic(char)


@pytest.mark.parametrize(
    "codepoint",
    [
        0x1F600,  # grinning face
        0x1F680,  # rocket
        0x2705,  # white heavy check mark
        0x274C,  # cross mark
        0x26A0,  # warning sign
        0x2B50,  # star
    ],
)
def test_pictographs_are_flagged(codepoint: int) -> None:
    """Including the cross mark section 13 names as the thing a log must not use.

    These are built with `chr()` rather than written as literals, because this
    file is itself a tracked file and `test_no_pictographs_in_tracked_files`
    above would flag its own fixtures. That is the gate working, not a
    limitation of it - caught on 2026-11-07 when exactly that happened.
    """
    assert is_pictographic(chr(codepoint))
