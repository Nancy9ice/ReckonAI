#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Validation gate for the field availability table - Phase 0, task group 7.

Implements the automated checks in
specs/2026-11-05-paypal-field-discovery/validation.md. Every check exits 0 or
names what failed; none of them asserts anything a reader has to take on trust.

    C2   --reproduce PATH       a re-probe yields the same presence verdicts
    C3   --columns              declared and observed are distinct columns
    C4   --coverage             every canonical field of requirements s7 has a row
    C5   --verdicts             verdicts well-formed, conditions and reasons stated
    C6   --verdict-source       every verdict traces to observed.json
    C7   --gap-section          the declared-but-not-observed set is complete
    C8   --decisions            every absent or unreliable field is decided
    C8a  --declared-provenance  declared.json is pinned and keyed on API names
    C9   --conventions          emojis, credentials, PII, floats
         --all                  all of the above except --reproduce

Two constructs in the document need handling, or the checks report failures
that are not there:

  Gated rows. A few fx_conversion rows report min(field, gate) rather than a
  raw lookup, because a capture's gross_amount is only the source amount OF A
  CONVERSION where a conversion happened. Such a row names its gate, and C6
  recomputes the same minimum.

  The unanswered boundary. C5 enforces what validation.md states: a field
  marked unanswered must carry a stated unexercised condition, which in this
  document means an entry in the Pending answers table. An unanswered row
  without one is an absent field in disguise and fails.

Presence parsing here is deliberately a second, independent implementation
rather than an import of derive_observed.py, so that a bug in the deriver
cannot hide behind the checker agreeing with it.

Usage:
    uv run scripts/check_fields_doc.py --all
    uv run scripts/check_fields_doc.py --reproduce /tmp/observed-recheck.json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "specs" / "paypal-fields.md"
ARTIFACTS = REPO / "specs" / "2026-11-05-paypal-field-discovery" / "artifacts"

PRESENT = "present"
UNRELIABLE = "present-but-unreliable"
ABSENT = "absent"
UNANSWERED = "unanswered"
VERDICTS = (PRESENT, UNRELIABLE, ABSENT, UNANSWERED)
DECISION_KINDS = ("derive", "limitation", "no_action", "connector_metadata")

# requirements.md section 7: the canonical entities, and the fx_conversion
# fields it enumerates. A canonical field with no row is the exact failure this
# phase exists to prevent (validation.md C4).
REQUIRED_ENTITIES = (
    "payment",
    "capture",
    "refund",
    "dispute",
    "payout_batch",
    "payout_item",
    "fx_conversion",
)
REQUIRED_FX_FIELDS = (
    "source_amount",
    "source_currency",
    "target_amount",
    "target_currency",
    "rate",
    "timestamp",
    "fee",
    "provider",
    "is_derived",
)

# Pictograph ranges as integers, deliberately. A character class written with
# escapes put the literal characters into this file, which made the checker
# violate the rule it enforces (CLAUDE.md bans pictographs in source) and went
# unnoticed until this file was first tracked and so first scanned. Integers
# cannot be mangled by an escape, and this checker has to pass its own check.
EMOJI_RANGES = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0xFE0F, 0xFE0F),
    (0x2B00, 0x2BFF),
)
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Value-shaped, not name-shaped: a long opaque token, never the bare word
# "secret". Rewritten 2026-11-07 after testing showed the previous pattern
# missed the likeliest leak of all - an uppercase environment assignment such
# as PAYPAL_CLIENT_SECRET=<value> - because it was case-sensitive and allowed
# no quote between the key and its separator. validation.md section 6 makes a
# committed credential one of five ways this phase fails outright, so the
# check has to fire on the shapes a credential actually arrives in.
BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{20,}")
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(client[_-]?secret|client[_-]?id|access[_-]?token|refresh[_-]?token"
    r"|api[_-]?key|bn[_-]?code)"
    r"[\"']?\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9._~+/-]{20,})",
    re.IGNORECASE,
)
# A template's placeholder is not a credential. Without this the .env.example
# that exists to prevent leaks would itself fail the check.
PLACEHOLDER = re.compile(
    r"replace[-_]with|your[-_]|example|placeholder|dummy|redacted|x{4,}|\.\.\.",
    re.IGNORECASE,
)


def credential_in(line: str) -> str | None:
    """The kind of credential a line exposes, or None."""
    if BEARER.search(line):
        return "bearer token"
    found = CREDENTIAL_ASSIGNMENT.search(line)
    if found and not PLACEHOLDER.search(found.group("value")):
        return "credential assignment"
    return None

CANONICAL_ROW = re.compile(
    r"^\| `(?P<field>[a-z_0-9]+)` \| (?P<path>.+?) \| (?P<declared>.+?) \| "
    r"(?P<observed>.+?) \| `(?P<verdict>[a-z-]+)` \| (?P<detail>.*?) \|$"
)
PATH_CELL = re.compile(
    r"^`(?P<rt>\w+)` `(?P<path>[^`]+)`(?:<br>gated on `(?P<gate>[^`]+)`)?$"
)
DECISION_ROW = re.compile(
    r"^\| `(?P<entity>[a-z_]+)\.(?P<field>[a-z_0-9]+)` \| `(?P<verdict>[a-z-]+)` \| "
    r"`(?P<kind>[a-z_]+)` \| (?P<flag>[^|]+?) \| (?P<text>.*?) \|$"
)
PENDING_ROW = re.compile(
    r"^\| `(?P<entity>[a-z_]+)\.(?P<field>[a-z_0-9]+)` \| (?P<answered>[^|]+?) \| "
    r"(?P<interim>.*?) \|$"
)
GAP_ROW = re.compile(r"^\| `(?P<path>\$[^`]*)` \| (?P<declared>[^|]+?) \| (?P<means>.*?) \|$")
SECTION = re.compile(r"^## (?:(?P<num>\d+)\. )?(?P<title>.+)$")
SUBSECTION = re.compile(r"^### (?P<title>.+)$")


def unescape(text: str) -> str:
    return text.replace("\\|", "|")


def entity_of(title: str) -> str:
    return title.strip().lower().replace(" ", "_")


class Doc:
    """specs/paypal-fields.md, parsed into the pieces the checks need."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.rows: list[dict[str, Any]] = []
        self.decisions: dict[tuple[str, str], dict[str, str]] = {}
        self.pending: dict[tuple[str, str], dict[str, str]] = {}
        self.gap: dict[str, dict[str, str]] = {}
        self.headers: dict[str, list[str]] = {}
        self.sections: list[str] = []

        section = ""
        numbered = False
        subsection = ""
        for line in text.splitlines():
            head = SECTION.match(line)
            if head:
                section = head.group("title").strip()
                numbered = head.group("num") is not None
                subsection = ""
                self.sections.append(section)
                continue
            sub = SUBSECTION.match(line)
            if sub:
                subsection = sub.group("title").strip()
                continue

            if line.startswith("| Canonical field |") and numbered:
                self.headers[section] = [c.strip() for c in line.split("|")[1:-1]]
                continue

            if numbered:
                row = CANONICAL_ROW.match(line)
                if row and row.group("verdict") in VERDICTS:
                    captured = row.groupdict()
                    cell = PATH_CELL.match(captured["path"])
                    captured["entity"] = entity_of(section)
                    captured["rt"] = cell.group("rt") if cell else None
                    captured["field_path"] = (
                        unescape(cell.group("path")) if cell else None
                    )
                    captured["gate"] = (
                        unescape(cell.group("gate")) if cell and cell.group("gate") else None
                    )
                    self.rows.append(captured)
                continue

            if section == "Decisions":
                row = DECISION_ROW.match(line)
                if row and row.group("kind") in DECISION_KINDS:
                    self.decisions[(row.group("entity"), row.group("field"))] = {
                        "verdict": row.group("verdict"),
                        "kind": row.group("kind"),
                        "flag": row.group("flag").strip(),
                        "text": row.group("text").strip(),
                    }
                continue

            if section == "Pending answers":
                row = PENDING_ROW.match(line)
                if row and not row.group("answered").strip().startswith("`"):
                    self.pending[(row.group("entity"), row.group("field"))] = {
                        "answered": row.group("answered").strip(),
                        "interim": row.group("interim").strip(),
                    }
                continue

            if section == "Declared but not observed" and subsection:
                row = GAP_ROW.match(line)
                if row:
                    self.gap[f"{subsection}:{unescape(row.group('path'))}"] = {
                        "record_type": subsection,
                        "means": row.group("means").strip(),
                    }
                continue


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    )
    return [REPO / line for line in out.stdout.splitlines() if line.strip()]


def populated_set(record: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {
        path: {"populated": entry["populated_on"], "of": entry["of"]}
        for path, entry in record["fields"].items()
    }


def first_pictograph(line: str) -> str | None:
    """The first pictograph in a line, or None."""
    for character in line:
        codepoint = ord(character)
        for low, high in EMOJI_RANGES:
            if low <= codepoint <= high:
                return character
    return None


def display(path: Path) -> str:
    """A repo-relative path for messages, without assuming it is inside one."""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def parent_of(path: str) -> str | None:
    if path.endswith("[]"):
        return path[:-2]
    return path.rsplit(".", 1)[0] if "." in path else None


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------


def check_columns(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    if not doc.headers:
        return ["C3: no canonical field tables found in the document"]
    for section, header in doc.headers.items():
        lowered = [cell.strip("* ").lower() for cell in header]
        if "declared" not in lowered:
            failures.append(f"C3: section '{section}' has no Declared column")
        if "observed" not in lowered:
            failures.append(f"C3: section '{section}' has no Observed column")
        if lowered.count("declared") > 1 or lowered.count("observed") > 1:
            failures.append(f"C3: section '{section}' has duplicate columns")
    return failures


def check_coverage(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    entities = {row["entity"] for row in doc.rows}
    for required in REQUIRED_ENTITIES:
        if required not in entities:
            failures.append(f"C4: no section for canonical entity '{required}'")
    fx = {row["field"] for row in doc.rows if row["entity"] == "fx_conversion"}
    for field in REQUIRED_FX_FIELDS:
        if field not in fx:
            failures.append(f"C4: fx_conversion.{field} has no row")
    for entity in REQUIRED_ENTITIES:
        if entity in entities and not any(r["entity"] == entity for r in doc.rows):
            failures.append(f"C4: entity '{entity}' has a section but no rows")
    return failures


def check_verdicts(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    for row in doc.rows:
        name = f"{row['entity']}.{row['field']}"
        verdict, detail = row["verdict"], row["detail"].strip()
        if verdict not in VERDICTS:
            failures.append(f"C5: {name} has verdict '{verdict}', not in the allowed set")
        if not detail:
            failures.append(f"C5: {name} has a blank condition/note cell")
        for hedge in ("probably", "possibly", "maybe", "unclear", "tbd"):
            if re.search(rf"\b{hedge}\b", detail, re.I):
                failures.append(f"C5: {name} hedges with '{hedge}'")
        if verdict == UNRELIABLE and "only on" not in detail:
            failures.append(f"C5: {name} is {UNRELIABLE} but states no condition")
        if verdict == UNANSWERED:
            # validation.md C5: an unanswered row must carry a stated
            # unexercised condition and what would answer it, or it is an
            # absent field in disguise.
            key = (row["entity"], row["field"])
            if key not in doc.pending:
                failures.append(
                    f"C5: {name} is {UNANSWERED} with no Pending answers entry - "
                    "an absent field in disguise"
                )
            else:
                entry = doc.pending[key]
                if not entry["answered"]:
                    failures.append(f"C5: {name} does not say what would answer it")
                if not entry["interim"]:
                    failures.append(f"C5: {name} has no interim handling")
    return failures


def check_verdict_source(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    for row in doc.rows:
        name = f"{row['entity']}.{row['field']}"
        verdict = row["verdict"]
        cell = row["observed"].strip()

        if row["field_path"] is None:
            if verdict in (PRESENT, UNRELIABLE):
                failures.append(f"C6: {name} is '{verdict}' with no PayPal path")
            if cell != "no path":
                failures.append(f"C6: {name} has no path but observed cell '{cell}'")
            continue

        record = observed["record_types"].get(row["rt"])
        if record is None:
            failures.append(f"C6: {name} cites unprobed record type '{row['rt']}'")
            continue
        entry = record["fields"].get(row["field_path"])
        if entry is None:
            populated, total = 0, record.get("samples", 0)
        else:
            populated, total = entry["populated_on"], entry["of"]

        gated = False
        if row["gate"]:
            gate_entry = record["fields"].get(row["gate"])
            gate_populated = gate_entry["populated_on"] if gate_entry else 0
            if gate_populated < populated:
                populated, gated = gate_populated, True

        expected = f"{populated}/{total}" + (" gated" if gated else "")
        if cell != expected:
            failures.append(
                f"C6: {name} observed cell is '{cell}' but observed.json gives "
                f"'{expected}'"
            )

        if verdict == PRESENT and not (total > 0 and populated == total):
            failures.append(
                f"C6: {name} is 'present' but was populated on {populated} of {total}"
            )
        if verdict == UNRELIABLE and not (0 < populated < total):
            failures.append(
                f"C6: {name} is '{UNRELIABLE}' but was populated on "
                f"{populated} of {total}"
            )
        if verdict in (ABSENT, UNANSWERED) and populated != 0:
            failures.append(
                f"C6: {name} is '{verdict}' but was populated on {populated} "
                f"of {total} - a delivered field marked as missing"
            )
    return failures


def check_gap_section(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    if "Declared but not observed" not in doc.sections:
        return ["C7: no 'Declared but not observed' section"]
    if "parent object was observed" not in doc.text:
        failures.append(
            "C7: the section does not state its scoping (validation.md C7 "
            "requires the parent-observed scope to be explicit)"
        )

    expected: set[str] = set()
    for record_type, record in observed["record_types"].items():
        declared_rt = declared["record_types"].get(record_type)
        if declared_rt is None:
            continue
        populated = {p for p, v in record["fields"].items() if v["populated_on"]}
        for path in set(declared_rt["fields"]) - set(record["fields"]):
            if parent_of(path) in populated:
                expected.add(f"{record_type}:{path}")

    if not expected and "the set is empty" not in doc.text.lower():
        failures.append("C7: the gap set is empty but the section does not say so")

    for key in sorted(expected - set(doc.gap)):
        failures.append(f"C7: {key} is declared, unobserved, parent observed, and unlisted")
    for key, entry in doc.gap.items():
        if not entry["means"]:
            failures.append(f"C7: {key} has no statement of what its absence means")
    return failures


def check_decisions(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    for row in doc.rows:
        if row["verdict"] not in (ABSENT, UNRELIABLE):
            continue
        key = (row["entity"], row["field"])
        name = f"{row['entity']}.{row['field']}"
        decision = doc.decisions.get(key)
        if decision is None:
            failures.append(f"C8: {name} is '{row['verdict']}' with no decision")
            continue
        if decision["kind"] not in DECISION_KINDS:
            failures.append(f"C8: {name} has decision kind '{decision['kind']}'")
        if not decision["text"]:
            failures.append(f"C8: {name} has an empty decision")
        if decision["verdict"] != row["verdict"]:
            failures.append(
                f"C8: {name} verdict is '{row['verdict']}' in the table but "
                f"'{decision['verdict']}' in the decisions section"
            )
        # tech-stack.md section 2: a derived value must be labelled derived.
        if decision["kind"] == "derive" and "yes" not in decision["flag"].lower():
            failures.append(
                f"C8: {name} is derived but is_derived is '{decision['flag']}'"
            )
    for key, decision in doc.decisions.items():
        if not any((r["entity"], r["field"]) == key for r in doc.rows):
            failures.append(
                f"C8: decision for {key[0]}.{key[1]} has no row in the table"
            )
    return failures


def check_declared_provenance(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    meta = declared.get("declared", {})
    provenance = meta.get("provenance", {})
    if not provenance:
        return ["C8a: declared.json carries no provenance block"]

    sdk = provenance.get("sdk_generated_contract", {})
    for field in ("repository", "tag", "commit"):
        if not sdk.get(field):
            failures.append(f"C8a: provenance sdk_generated_contract lacks '{field}'")
    if sdk.get("commit") and len(sdk["commit"]) < 40:
        failures.append("C8a: the SDK commit is not a full SHA")
    if sdk.get("imported") is not False:
        failures.append("C8a: provenance does not record that the SDK was not imported")

    api = provenance.get("openapi_specification", {})
    for field in ("repository", "commit"):
        if not api.get(field):
            failures.append(f"C8a: provenance openapi_specification lacks '{field}'")

    grades = meta.get("source_grade_by_record_type", {})
    for record_type in observed["record_types"]:
        if record_type not in grades:
            failures.append(f"C8a: no source grade recorded for '{record_type}'")

    # Keyed on API property names, not Python attribute names. Where the
    # generator renamed a field, the key must carry the API spelling.
    for record_type, record in declared["record_types"].items():
        for path, entry in record["fields"].items():
            if not entry.get("renamed"):
                continue
            leaf = path.rsplit(".", 1)[-1]
            if leaf == entry.get("python_attr"):
                failures.append(
                    f"C8a: {record_type} {path} is keyed on the Python attribute "
                    f"name '{entry['python_attr']}', not the API property name"
                )
    return failures


def check_conventions(doc: Doc, declared: Any, observed: Any) -> list[str]:
    failures = []
    for path in tracked_files():
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = display(path)
        for number, line in enumerate(text.splitlines(), 1):
            pictograph = first_pictograph(line)
            if pictograph:
                failures.append(
                    f"C9: pictograph U+{ord(pictograph):04X} at {relative}:{number}"
                )
            kind = credential_in(line)
            if kind:
                failures.append(f"C9: {kind} at {relative}:{number}")

    for artifact in sorted(ARTIFACTS.rglob("*.json")):
        relative = display(artifact)
        raw = artifact.read_text(encoding="utf-8")
        for match in EMAIL.finditer(raw):
            # A documentation link is not buyer PII; a real address is.
            if "developer.paypal.com" in match.group() or "@example" in match.group():
                continue
            failures.append(f"C9: unredacted email address in {relative}: {match.group()}")
        floats = find_floats(json.loads(raw))
        for where in floats:
            failures.append(f"C9: JSON float in {relative} at {where}")
    return failures


def find_floats(node: Any, path: str = "$") -> list[str]:
    if isinstance(node, float):
        return [path]
    if isinstance(node, dict):
        return [h for k, v in node.items() for h in find_floats(v, f"{path}.{k}")]
    if isinstance(node, list):
        return [h for i, v in enumerate(node) for h in find_floats(v, f"{path}[{i}]")]
    return []


def walk_presence(node: Any, path: str, out: dict[str, bool]) -> None:
    """Independent presence walk, for C2. True means non-empty somewhere."""
    empty = node is None or node == "" or node == [] or node == {}
    out[path] = out.get(path, False) or not empty
    if isinstance(node, dict):
        for key, value in node.items():
            walk_presence(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for value in node:
            walk_presence(value, f"{path}[]", out)


def check_reproduce(observed: Any, recheck_path: Path) -> list[str]:
    failures = []
    if not recheck_path.exists():
        return [f"C2: no such re-probe artifact: {recheck_path}"]
    fresh = json.loads(recheck_path.read_text(encoding="utf-8"))

    by_type: dict[str, dict[str, bool]] = {}
    skipped = []
    for result in fresh.get("results", []):
        record_type = result.get("record_type")
        if result.get("status") != "ok" or "body" not in result:
            skipped.append(f"{record_type}({result.get('status')})")
            continue
        presence = by_type.setdefault(record_type, {})
        walk_presence(result["body"], "$", presence)

    if not by_type:
        return ["C2: the re-probe artifact contains no successful results"]

    for record_type, presence in sorted(by_type.items()):
        committed = observed["record_types"].get(record_type)
        if committed is None:
            failures.append(f"C2: re-probe has '{record_type}', observed.json does not")
            continue
        varying = 0
        for path, entry in committed["fields"].items():
            populated, total = entry["populated_on"], entry["of"]
            now = presence.get(path, False)
            # A field the artifact already records as varying between samples
            # is expected to differ in any single re-probe - the document marks
            # it present-but-unreliable and names the condition. Flagging it
            # would make C2 fail by design. What C2 exists to catch is a field
            # recorded as reliably present that has stopped arriving, which
            # would invalidate a 'present' verdict.
            if 0 < populated < total:
                varying += 1
                continue
            if populated == total and total > 0 and not now:
                failures.append(
                    f"C2: {record_type} {path} is recorded present on every "
                    f"sample ({populated}/{total}) and is now absent"
                )
            if populated == 0 and now:
                failures.append(
                    f"C2: {record_type} {path} is recorded as never populated "
                    "and is now populated"
                )
        if varying:
            print(
                f"  C2: {record_type}: {varying} path(s) already recorded as "
                "varying between samples, not compared"
            )
    if skipped:
        print(f"  C2: not compared (no successful read): {', '.join(skipped)}")
    return failures


CHECKS = {
    "columns": ("C3", check_columns),
    "coverage": ("C4", check_coverage),
    "verdicts": ("C5", check_verdicts),
    "verdict_source": ("C6", check_verdict_source),
    "gap_section": ("C7", check_gap_section),
    "decisions": ("C8", check_decisions),
    "declared_provenance": ("C8a", check_declared_provenance),
    "conventions": ("C9", check_conventions),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="check_fields_doc.py",
        description=(
            "Validation gate for specs/paypal-fields.md and the Phase 0 "
            "artifacts. Implements validation.md C2 through C9."
        ),
    )
    parser.add_argument("--all", action="store_true", help="Run every check but C2.")
    parser.add_argument("--columns", action="store_true", help="C3.")
    parser.add_argument("--coverage", action="store_true", help="C4.")
    parser.add_argument("--verdicts", action="store_true", help="C5.")
    parser.add_argument("--verdict-source", action="store_true", help="C6.")
    parser.add_argument("--gap-section", action="store_true", help="C7.")
    parser.add_argument("--decisions", action="store_true", help="C8.")
    parser.add_argument("--declared-provenance", action="store_true", help="C8a.")
    parser.add_argument("--conventions", action="store_true", help="C9.")
    parser.add_argument(
        "--reproduce",
        type=Path,
        metavar="PATH",
        help="C2: a fresh probe_paypal.py artifact to compare presence against.",
    )
    args = parser.parse_args()

    for path in (DOC, ARTIFACTS / "declared.json", ARTIFACTS / "observed.json"):
        if not path.exists():
            sys.exit(f"error: missing {path.relative_to(REPO)}")

    doc = Doc(DOC.read_text(encoding="utf-8"))
    declared = json.loads((ARTIFACTS / "declared.json").read_text(encoding="utf-8"))
    observed = json.loads((ARTIFACTS / "observed.json").read_text(encoding="utf-8"))

    selected = [
        name
        for name in CHECKS
        if args.all or getattr(args, name.replace("-", "_"), False)
    ]
    if not selected and not args.reproduce:
        selected = list(CHECKS)

    failures: list[str] = []
    print(f"checking {DOC.relative_to(REPO)} ({len(doc.rows)} canonical rows)")

    for name in selected:
        label, function = CHECKS[name]
        found = function(doc, declared, observed)
        status = "FAIL" if found else "ok"
        print(f"  {label:<4} {name:<20} {status}")
        failures.extend(found)

    if args.reproduce:
        found = check_reproduce(observed, args.reproduce)
        print(f"  {'C2':<4} {'reproduce':<20} {'FAIL' if found else 'ok'}")
        failures.extend(found)

    if failures:
        print(f"\n{len(failures)} failure(s):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
