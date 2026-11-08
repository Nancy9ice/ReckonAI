"""The two gates over the money path: no inexact arithmetic, and no I/O.

specs/roadmap.md Phase 2 requires "no `float` anywhere in the money path -
enforced by a test that greps the module, so it cannot regress silently", and
specs/tech-stack.md section 6 requires the engine to be pure functions with no
I/O. This module enforces both, and both are enforced over **packages** rather
than named files, so a module added in Phase 3 is covered the moment it exists.

**Why an AST parse rather than a grep.** The roadmap says "greps", and a text
search over this repository's source cannot work: the specs require explaining
why `float` is banned, so the docstrings discuss it constantly - `value.py`
alone mentions it several times by name. A grep would be either noisy or
riddled with exclusions, and an exclusion list is how a gate stops gating.
`ast` sees code and not strings or comments, so the word can be discussed
freely and the type still cannot be used. That is stricter in the direction the
roadmap wanted, and `requirements.md` D10 records the decision.

Both gates carry **self-tests built from source strings**, so each has been
observed failing as well as passing. `tests/test_conventions.py` records what
skipping that costs: its own fixtures tripped its own check.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_ROOT = PROJECT_ROOT / "src" / "reckonai"

#: Packages whose arithmetic reaches a financial conclusion. Every module under
#: each is parsed. specs/tech-stack.md section 2 applies "at any layer", so the
#: ORM models and the JSON schemas are in scope as much as the value object -
#: a `float` column or a `float` field would defeat the whole thing.
MONEY_PATH_PACKAGES = ("money", "engine", "models", "schemas")

#: Imports forbidden inside the pure packages, per specs/tech-stack.md
#: section 6: "No database handle, no HTTP client, no clock reads inside the
#: calculation."
FORBIDDEN_IMPORTS = ("sqlalchemy", "psycopg", "httpx", "requests", "reckonai.db")

#: The packages that rule applies to. `models` and `schemas` are deliberately
#: absent - a declarative model *is* the database mapping, and excluding it
#: would make the gate meaningless rather than strict.
PURE_PACKAGES = ("money", "engine")


def money_path_modules() -> list[Path]:
    """Every Python file under the money-path packages."""
    paths: list[Path] = []
    for package in MONEY_PATH_PACKAGES:
        paths.extend(sorted((SOURCE_ROOT / package).rglob("*.py")))
    return paths


# ---------------------------------------------------------------------------
# The float gate
# ---------------------------------------------------------------------------


def _annotation_subtrees(tree: ast.AST) -> list[ast.expr]:
    """Every annotation expression in `tree`.

    Collected explicitly rather than by walking all string constants, because
    the two are not the same thing: `x = 'float'` is an ordinary string and
    `x: 'float' = None` is an annotation. A gate that cannot tell them apart
    flags legitimate source, and a flagged gate gets suppressed.

    Note that `from __future__ import annotations` does **not** turn
    annotations into strings in the AST - it only changes runtime evaluation -
    so `def f(x: float)` still parses as an `ast.Name`. The string case here is
    only for an annotation someone wrote in quotes deliberately.
    """
    found: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            found.append(node.annotation)
        elif isinstance(node, ast.arg) and node.annotation is not None:
            found.append(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if node.returns is not None:
                found.append(node.returns)
    return found


def inexact_arithmetic_offences(source: str, label: str) -> list[str]:
    """Return every use of inexact arithmetic in `source`.

    Four things are refused, and each has a reason beyond `float` itself:

      - a reference to the `float` builtin, in any position - a call, an
        `isinstance` argument, a default, a dict value
      - a `float` annotation, including inside `list[float]`, `float | None`,
        or written as a string
      - a call to `round`, which operates on binary floats and whose
        half-to-even behaviour is not the `ROUND_HALF_UP` the spec states.
        Rounding is spelled `Money.quantize()`
      - any attribute on `math`, whose functions return floats
    """
    tree = ast.parse(source, filename=label)
    offences: list[str] = []

    def report(node: ast.AST, what: str) -> None:
        offences.append(f"{label}:{getattr(node, 'lineno', 0)}: {what}")

    for node in ast.walk(tree):
        # A bare reference to the name, which covers calls, isinstance
        # arguments, annotations spelled as plain names, and anything else.
        if isinstance(node, ast.Name):
            if node.id == "float":
                report(node, "reference to the float builtin")
            elif node.id == "round":
                report(node, "call to round(); use Money.quantize()")

        # `math.floor`, `math.fsum`, and friends.
        if isinstance(node, ast.Attribute):
            value = node.value
            if isinstance(value, ast.Name) and value.id == "math":
                report(node, f"math.{node.attr}: returns a float")

    # Annotations written as strings, parsed as the expressions they are.
    for annotation in _annotation_subtrees(tree):
        for node in ast.walk(annotation):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            try:
                inner = ast.parse(node.value, mode="eval")
            except SyntaxError:
                continue  # not a type expression; nothing to judge
            if any(
                isinstance(name, ast.Name) and name.id == "float"
                for name in ast.walk(inner)
            ):
                report(node, "float in a string annotation")

    return offences


@pytest.mark.parametrize(
    "path", money_path_modules(), ids=lambda p: str(p.relative_to(SOURCE_ROOT))
)
def test_no_inexact_arithmetic_in_the_money_path(path: Path) -> None:
    """The roadmap's fourth exit criterion for this phase.

    Parameterised per module so a failure names the file rather than handing
    back one combined list.
    """
    label = str(path.relative_to(PROJECT_ROOT))
    offences = inexact_arithmetic_offences(path.read_text(encoding="utf-8"), label)

    assert not offences, "Inexact arithmetic in the money path:\n" + "\n".join(
        offences
    )


def test_the_money_path_is_not_empty() -> None:
    """A gate over an empty file list passes trivially.

    Phase 1's C14 asserted the money path was *absent* so that its green suite
    could not be mistaken for evidence that this rule was enforced. This is the
    same guard, inverted now that the path exists: if a package were renamed
    and `MONEY_PATH_PACKAGES` not updated, every test above would pass over
    nothing.
    """
    modules = money_path_modules()

    assert len(modules) >= 10, f"only {len(modules)} modules found"
    for package in MONEY_PATH_PACKAGES:
        assert (SOURCE_ROOT / package).is_dir(), f"{package} is missing"


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ("x = float(1)", "reference to the float builtin"),
        ("def f(x: float) -> None: ...", "reference to the float builtin"),
        ("def f() -> float: ...", "reference to the float builtin"),
        ("def f(x: list[float]) -> None: ...", "reference to the float builtin"),
        ("isinstance(x, float)", "reference to the float builtin"),
        ("y = round(x, 2)", "call to round()"),
        ("import math\nmath.floor(x)", "math.floor"),
        ("x: 'float' = None", "float in a string annotation"),
        ("def f(x: 'float | None') -> None: ...", "float in a string annotation"),
    ],
)
def test_the_float_gate_catches_what_it_should(snippet: str, expected: str) -> None:
    """The gate, seen failing.

    Built from source strings rather than fixture files, because this file is
    itself under no exclusion and a fixture containing a real `float`
    annotation would be found by the project's own conventions gate - which is
    exactly what happened to `tests/test_conventions.py` in Phase 1.
    """
    offences = inexact_arithmetic_offences(snippet, "<snippet>")

    assert offences, f"gate missed: {snippet!r}"
    assert any(expected in offence for offence in offences)


@pytest.mark.parametrize(
    "snippet",
    [
        # The word in a docstring, which is most of why this is an AST gate.
        '"""Never use float for a monetary value."""',
        "# float is banned here, see tech-stack section 2",
        "x = 'float'",
        "from decimal import Decimal\nx = Decimal('1.5')",
        "x = y.quantize(Decimal('0.01'))",
        # A variable merely named for it.
        "floating_point_is_banned = True",
        # `Decimal.to_integral_value` is exact, unlike `round`.
        "x = y.to_integral_value()",
    ],
)
def test_the_float_gate_permits_legitimate_source(snippet: str) -> None:
    """The direction that is easy to get wrong.

    A gate that flagged the word in prose would be suppressed rather than
    fixed, and the ban would quietly stop being enforced.
    """
    assert not inexact_arithmetic_offences(snippet, "<snippet>")


# ---------------------------------------------------------------------------
# The purity gate
# ---------------------------------------------------------------------------


def import_offences(source: str, label: str) -> list[str]:
    """Return every forbidden import in `source`."""
    tree = ast.parse(source, filename=label)
    offences: list[str] = []

    def is_forbidden(name: str) -> str | None:
        for forbidden in FORBIDDEN_IMPORTS:
            if name == forbidden or name.startswith(f"{forbidden}."):
                return forbidden
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if (hit := is_forbidden(alias.name)) is not None:
                    offences.append(f"{label}:{node.lineno}: imports {hit}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if (hit := is_forbidden(node.module)) is not None:
                offences.append(f"{label}:{node.lineno}: imports from {hit}")

    return offences


@pytest.mark.parametrize("package", PURE_PACKAGES)
def test_the_pure_packages_import_no_io(package: str) -> None:
    """specs/tech-stack.md section 6, as source analysis.

    This is why `reckonai.engine.rules` takes a `typing.Protocol` rather than
    `reckonai.models.CommissionRule`: importing the ORM class would pull in
    SQLAlchemy and fail here.
    """
    offences: list[str] = []
    for path in sorted((SOURCE_ROOT / package).rglob("*.py")):
        label = str(path.relative_to(PROJECT_ROOT))
        offences.extend(import_offences(path.read_text(encoding="utf-8"), label))

    assert not offences, f"I/O imports in reckonai.{package}:\n" + "\n".join(
        offences
    )


@pytest.mark.parametrize("package", PURE_PACKAGES)
def test_the_pure_packages_import_no_io_at_runtime(package: str) -> None:
    """The same rule, checked by actually importing it.

    Source analysis misses an import performed inside a function or through
    `importlib`. This imports every module in the package into a fresh
    interpreter state and inspects what landed in `sys.modules` - which catches
    a transitive pull-in that no `import` statement in these files shows.
    """
    import subprocess
    import sys

    modules = [
        f"reckonai.{package}.{info.name}"
        for info in pkgutil.iter_modules([str(SOURCE_ROOT / package)])
        if info.name != "__init__"
    ]
    modules.append(f"reckonai.{package}")

    # `reckonai.db` is checked by full name while the third-party packages are
    # checked by top-level name. Collapsing both to the top level would match
    # `reckonai` itself and report every module in the package as forbidden,
    # which is what the first version of this test did.
    third_party = sorted(
        {name.split(".")[0] for name in FORBIDDEN_IMPORTS if "." not in name}
    )
    dotted = sorted(name for name in FORBIDDEN_IMPORTS if "." in name)

    program = (
        "import sys\n"
        f"for name in {modules!r}:\n"
        "    __import__(name)\n"
        f"third_party = {third_party!r}\n"
        f"dotted = {dotted!r}\n"
        "forbidden = [\n"
        "    m for m in sys.modules\n"
        "    if m.split('.')[0] in third_party\n"
        "    or any(m == d or m.startswith(d + '.') for d in dotted)\n"
        "]\n"
        "print(','.join(sorted(set(forbidden))))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        check=True,
        cwd=PROJECT_ROOT,
    )

    leaked = [name for name in result.stdout.strip().split(",") if name]
    assert not leaked, (
        f"importing reckonai.{package} pulled in {leaked}. Something imports "
        "I/O transitively, which source analysis alone would not show."
    )


@pytest.mark.parametrize(
    "snippet",
    [
        "import sqlalchemy",
        "import sqlalchemy.orm",
        "from sqlalchemy import Numeric",
        "from sqlalchemy.orm import Mapped",
        "import psycopg",
        "from reckonai.db import create_app_engine",
        "import httpx",
    ],
)
def test_the_purity_gate_catches_what_it_should(snippet: str) -> None:
    """Seen failing, like the gate above."""
    assert import_offences(snippet, "<snippet>")


@pytest.mark.parametrize(
    "snippet",
    [
        "from decimal import Decimal",
        "from reckonai.money import Money",
        "import uuid",
        "from typing import Protocol",
        # A name that merely contains a forbidden one.
        "import sqlalchemy_is_not_this",
        "from reckonai.dbutils_not_real import thing",
    ],
)
def test_the_purity_gate_permits_legitimate_imports(snippet: str) -> None:
    assert not import_offences(snippet, "<snippet>")


def test_the_models_package_is_deliberately_not_pure() -> None:
    """`models` is in the float gate but not the purity gate, on purpose.

    A declarative model *is* the database mapping; excluding SQLAlchemy from it
    would make the gate meaningless rather than strict. Asserting it here keeps
    the asymmetry a decision rather than an oversight someone later "fixes".
    """
    assert "models" in MONEY_PATH_PACKAGES
    assert "models" not in PURE_PACKAGES

    source = (SOURCE_ROOT / "models" / "base.py").read_text(encoding="utf-8")
    assert import_offences(source, "models/base.py"), (
        "models/base.py no longer imports SQLAlchemy, which means either the "
        "ORM mapping moved or this assumption is stale"
    )


def test_the_money_package_is_importable_without_the_database_installed() -> None:
    """The practical form of the purity rule.

    `reckonai.money` must not need a database URL, a driver or a live
    connection. If it did, Phase 10's engine could not be unit tested against
    hand-computed fixtures, which is the whole reason section 6 exists.
    """
    importlib.invalidate_caches()
    money = importlib.import_module("reckonai.money")

    assert money.Money("1.00", "USD").amount is not None
