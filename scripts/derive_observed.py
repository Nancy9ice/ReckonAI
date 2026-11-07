#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Redact probe output and derive the observed column - Phase 0, task group 3.

Takes the UNREDACTED artifacts written by scripts/probe_paypal.py and produces
the two committed outputs of task group 3:

  1. Redacted copies of every raw response, one file per record type.
  2. observed.json - per record type, every field path with whether it was
     populated, on how many of N sampled records, and a sample value.

Why this is a script and not a hand edit: validation.md section 6 item 4 makes a
hand-transcribed observed column a phase failure, and requirements.md section
3.2 wants the observed column machine-produced and re-runnable. Redacting and
summarising by hand would reintroduce exactly the transcription this phase is
required to avoid.

Redaction follows plan.md group 3 step 3. Identifiers and buyer PII are
replaced; amounts, currencies, rates, fees, timestamps, statuses and error codes
are KEPT, because they are the evidence. Two rules make that split mechanical
rather than a judgement call per field:

  - A value is an identifier if its key is "id", ends in "_id", or is
    "account_number".
  - A value is PII if its key is in PII_KEYS, or is "name" directly under a
    cardholder- or party-shaped container (NAME_PARENTS). The second clause is
    the reason this is path-aware: payouts carry errors.name, which is the
    error code RECEIVER_COUNTRY_NOT_ALLOWED and is evidence, not a person.

An empty value is never redacted, so "present but empty" stays visible - the
difference between a populated errors object and an empty one is a finding in
notes.md, and redaction must not erase it.

Money: every value is parsed with parse_float=Decimal and written through a
Decimal encoder, and the output is scanned for floats before it is written.
specs/tech-stack.md section 2.

Usage:
    uv run scripts/derive_observed.py --help
    uv run scripts/derive_observed.py \\
        --raw /path/to/probe/*.raw.json \\
        --out-raw specs/2026-11-05-paypal-field-discovery/artifacts/raw \\
        --out-observed specs/2026-11-05-paypal-field-discovery/artifacts/observed.json \\
        --label 8RW04003P5846784X=same-currency
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ID_MARKER = "<redacted:id>"
PII_MARKER = "<redacted:pii>"
HREF_ID_MARKER = "{id}"

# Keys whose value is a person or an instrument belonging to one.
PII_KEYS = frozenset(
    {
        "email_address",
        "receiver",
        "last_digits",
        "expiry",
        "phone",
        "phone_number",
        "national_number",
        "address_line_1",
        "address_line_2",
        "admin_area_1",
        "admin_area_2",
        "postal_code",
        "given_name",
        "surname",
        "full_name",
    }
)

# "name" is PII only directly under one of these. Under "errors" it is an error
# code and must survive redaction.
NAME_PARENTS = frozenset(
    {"card", "payer", "payee", "recipient", "shipping", "billing_address", "name"}
)

# An id-shaped path segment in a HATEOAS href. PayPal resource ids are
# uppercase alphanumeric; the surrounding path segments are lowercase words.
HREF_ID_RE = re.compile(r"^(?=.*\d)[A-Z0-9]{8,}$")

EMPTY: tuple[Any, ...] = (None, "", [], {})


def is_identifier(key: str) -> bool:
    return key == "id" or key.endswith("_id") or key == "account_number"


def is_pii(key: str, parent_key: str | None) -> bool:
    if key in PII_KEYS:
        return True
    return key == "name" and parent_key in NAME_PARENTS


def is_empty(value: Any) -> bool:
    return any(value is e or value == e for e in EMPTY) and not isinstance(value, bool)


def redact_href(href: str) -> str:
    """Replace id-shaped segments in a link, keeping the route and the query.

    The route is kept deliberately: notes.md records that a refund's only link
    back to its capture is links[rel=up], so the shape of that href is the
    evidence for refund-to-capture linkage.
    """
    head, sep, query = href.partition("?")
    parts = [HREF_ID_MARKER if HREF_ID_RE.match(p) else p for p in head.split("/")]
    return "/".join(parts) + sep + query


def redact(node: Any, key: str | None = None, parent_key: str | None = None) -> Any:
    """Return a redacted copy. Structure and presence are always preserved."""
    if isinstance(node, dict):
        return {k: redact(v, k, key) for k, v in node.items()}
    if isinstance(node, list):
        return [redact(v, key, parent_key) for v in node]
    if key is None or is_empty(node):
        return node
    if key in ("href", "request_path") and isinstance(node, str):
        return redact_href(node)
    if is_identifier(key):
        return ID_MARKER
    if is_pii(key, parent_key):
        return PII_MARKER
    return node


def redact_value(value: Any, key: str | None, parent_key: str | None) -> Any:
    """Redact one sample value, for observed.json."""
    return redact(value, key, parent_key)


def json_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Decimal):
        return "number"
    if isinstance(value, int):
        return "int"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def money_envelope(node: Any) -> str | None:
    """Which currency-key spelling a money object used, or None.

    PayPal uses currency_code in Orders v2 and Payments v2 and currency in
    Payouts v1. notes.md, "PayPal uses two different money envelopes". The
    spelling is recorded per field path so the observed column carries the
    difference instead of flattening it.
    """
    if not isinstance(node, dict) or "value" not in node:
        return None
    for candidate in ("currency_code", "currency"):
        if candidate in node:
            return candidate
    return None


def walk(
    node: Any,
    path: str,
    out: dict[str, list[tuple[Any, str | None, str | None]]],
    key: str | None = None,
    parent_key: str | None = None,
) -> None:
    """Collect every field path in one record, array indices collapsed to [].

    Collapsing indices is what lets items[].payout_item_fee.value be one row
    carrying a count, rather than two rows that happen to be the same field.
    """
    out.setdefault(path, []).append((node, key, parent_key))
    if isinstance(node, dict):
        for k, v in node.items():
            walk(v, f"{path}.{k}", out, k, key)
    elif isinstance(node, list):
        for v in node:
            walk(v, f"{path}[]", out, key, parent_key)


def summarise(
    samples: list[tuple[str, Any]],
) -> dict[str, Any]:
    """Build the field table for one record type from its sampled records."""
    per_path: dict[str, dict[str, Any]] = {}
    labels = [label for label, _ in samples]

    for label, body in samples:
        collected: dict[str, list[tuple[Any, str | None, str | None]]] = {}
        walk(body, "$", collected)
        for path, occurrences in collected.items():
            entry = per_path.setdefault(
                path,
                {
                    "populated_on": 0,
                    "present_empty_on": 0,
                    "of": len(samples),
                    "occurrences": 0,
                    "populated_on_labels": [],
                    "empty_on_labels": [],
                    "absent_on_labels": [],
                    "json_type": None,
                    "sample_value": None,
                },
            )
            entry["occurrences"] += len(occurrences)
            populated = [o for o in occurrences if not is_empty(o[0])]
            if populated:
                entry["populated_on"] += 1
                entry["populated_on_labels"].append(label)
                value, key, parent_key = populated[0]
                if entry["json_type"] is None:
                    entry["json_type"] = json_type(value)
                if entry["sample_value"] is None and not isinstance(
                    value, (dict, list)
                ):
                    entry["sample_value"] = redact_value(value, key, parent_key)
                envelope = money_envelope(value)
                if envelope:
                    entry["money_envelope"] = envelope
            else:
                entry["present_empty_on"] += 1
                entry["empty_on_labels"].append(label)
                if entry["json_type"] is None:
                    entry["json_type"] = json_type(occurrences[0][0])

    for entry in per_path.values():
        # Absent means the key was not in the payload at all. A key that was
        # there holding null or an empty object is a different fact and is
        # counted separately, because conflating the two is how an "absent"
        # verdict gets manufactured out of an explicit empty.
        present = set(entry["populated_on_labels"]) | set(entry["empty_on_labels"])
        entry["absent_on_labels"] = [lbl for lbl in labels if lbl not in present]
        if entry["populated_on"] and entry["populated_on"] < len(samples):
            entry["condition"] = "populated only on: " + ", ".join(
                entry["populated_on_labels"]
            )

    return {"fields": dict(sorted(per_path.items()))}


# The five record types requirements.md section 3.1 puts in scope, and the one
# field path whose population proves a record of that type was actually seen.
# A witness is needed because presence of a payload is not presence of a
# record: GET /v1/customer/disputes answers 200 with items [], which populates
# the envelope's own links and would otherwise read as dispute coverage.
WITNESSES: dict[str, tuple[str, str, str]] = {
    "transactions_captures": (
        "captures",
        "$.id",
        "a capture was retrieved",
    ),
    "refunds": ("refunds", "$.id", "a refund was retrieved"),
    "settlement_and_fx": (
        "captures",
        "$.seller_receivable_breakdown.exchange_rate.value",
        "a provider conversion record was retrieved",
    ),
    "disputes": (
        "disputes",
        "$.items[].dispute_id",
        "a dispute record was present in the list response",
    ),
    "payouts": (
        "payouts",
        "$.batch_header.payout_batch_id",
        "a payout batch was retrieved",
    ),
}


def coverage(record_types: dict[str, Any], notes: dict[str, str]) -> dict[str, Any]:
    """Status of each in-scope record type, judged on a witness field."""
    out: dict[str, Any] = {}
    for required, (source, witness, meaning) in WITNESSES.items():
        field = (record_types.get(source, {}).get("fields", {})).get(witness, {})
        populated = bool(field.get("populated_on"))
        entry: dict[str, Any] = {
            "status": "observed" if populated else "not_observed",
            "witness_record_type": source,
            "witness_field": witness,
            "witness_means": meaning,
            "populated_on": field.get("populated_on", 0),
            "of": field.get("of", record_types.get(source, {}).get("samples", 0)),
        }
        if not populated and source in notes:
            entry["reason"] = notes[source]
        out[required] = entry
    return out


def load_raw(paths: list[Path]) -> list[dict[str, Any]]:
    artifacts = []
    for path in paths:
        if not path.exists():
            sys.exit(f"error: no such raw artifact: {path}")
        artifacts.append(json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal))
    return artifacts


def find_floats(node: Any, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(node, float):
        hits.append(path)
    elif isinstance(node, dict):
        for key, value in node.items():
            hits.extend(find_floats(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(find_floats(value, f"{path}[{index}]"))
    return hits


class DecimalEncoder(json.JSONEncoder):
    """Serialize Decimal as a JSON string, never as a float."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def write_json(path: Path, payload: Any) -> None:
    floats = find_floats(payload)
    if floats:
        sys.exit(
            "error: float found in output, which specs/tech-stack.md section 2 "
            f"forbids: {floats[:5]}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, cls=DecimalEncoder, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")


def parse_pairs(raw: list[str], flag: str) -> dict[str, str]:
    pairs = {}
    for item in raw:
        key, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"error: {flag} expects KEY=VALUE, got {item!r}")
        pairs[key.strip()] = value.strip()
    return pairs


def label_for(
    result: dict[str, Any], labels: dict[str, str], session: str | None = None
) -> str:
    path = result.get("request_path", "")
    base = f"{result['record_type']}:list"
    for identifier, label in labels.items():
        if identifier in path:
            base = label
            break
    # When artifacts from more than one probe session are combined, the same
    # fixture appears twice and the labels must distinguish them - otherwise a
    # field present in one session and not the other reports a condition that
    # names the same label twice and says nothing.
    return f"{base}@{session}" if session else base


def session_of(artifact: dict[str, Any]) -> str:
    return (artifact.get("probe", {}).get("run_at") or "")[:10]


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="derive_observed.py",
        description=(
            "Redact probe output and derive observed.json for the observed "
            "column of specs/paypal-fields.md. Reads no network and no "
            "credentials."
        ),
    )
    parser.add_argument(
        "--raw",
        nargs="+",
        type=Path,
        required=True,
        metavar="PATH",
        help="Unredacted probe artifacts written by probe_paypal.py --out.",
    )
    parser.add_argument(
        "--out-raw",
        type=Path,
        required=True,
        metavar="DIR",
        help="Directory for the redacted copies, one file per record type.",
    )
    parser.add_argument(
        "--out-observed",
        type=Path,
        required=True,
        metavar="PATH",
        help="Path for the derived observed.json.",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        metavar="ID=LABEL",
        help="Name a sample after its fixture id, e.g. "
        "8RW04003P5846784X=same-currency. Repeatable.",
    )
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        metavar="RECORD_TYPE=TEXT",
        help="Attach a limitation note to a record type. Repeatable.",
    )
    args = parser.parse_args()

    labels = parse_pairs(args.label, "--label")
    notes = parse_pairs(args.note, "--note")

    artifacts = load_raw(args.raw)
    api_bases = {a["probe"]["api_base"] for a in artifacts}
    if len(api_bases) != 1:
        sys.exit(f"error: raw artifacts disagree on api_base: {sorted(api_bases)}")

    sessions = sorted({session_of(a) for a in artifacts if session_of(a)})
    tag_sessions = len(sessions) > 1

    by_type: dict[str, list[tuple[dict[str, Any], str | None]]] = {}
    for artifact in artifacts:
        session = session_of(artifact) if tag_sessions else None
        for result in artifact["results"]:
            by_type.setdefault(result["record_type"], []).append((result, session))

    record_types: dict[str, Any] = {}
    for record_type, entries in sorted(by_type.items()):
        results = [r for r, _s in entries]
        samples = [
            (label_for(r, labels, s), r["body"])
            for r, s in entries
            if r.get("status") == "ok" and "body" in r
        ]

        redacted_results = [redact(r) for r in results]
        write_json(
            args.out_raw / f"{record_type}.json",
            {
                "raw": {
                    "record_type": record_type,
                    "source": "scripts/probe_paypal.py",
                    "redacted_by": "scripts/derive_observed.py",
                    "redacted": True,
                    "api_base": sorted(api_bases)[0],
                    "sample_labels": [label for label, _ in samples],
                    "redaction_note": (
                        "Identifiers and buyer PII replaced with "
                        f"{ID_MARKER} and {PII_MARKER}. Amounts, currencies, "
                        "rates, fees, timestamps, statuses and error codes are "
                        "kept: they are the evidence (plan.md group 3 step 3). "
                        "Unredacted fixture ids are in fixtures.md."
                    ),
                },
                "results": redacted_results,
            },
        )

        entry: dict[str, Any] = {
            "request_paths": sorted(
                {redact_href(r.get("request_path", "")) for r in results}
            ),
            "http_statuses": sorted({r.get("http_status") for r in results if r.get("http_status")}),
            "samples": len(samples),
            "sample_labels": [label for label, _ in samples],
            "raw": f"raw/{record_type}.json",
        }
        if record_type in notes:
            entry["limitation"] = notes[record_type]
        entry.update(summarise(samples) if samples else {"fields": {}})
        record_types[record_type] = entry

    observed = {
        "observed": {
            "phase": "0",
            "task_group": "3",
            "probe": "scripts/probe_paypal.py",
            "generated_by": "scripts/derive_observed.py",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "api_base": sorted(api_bases)[0],
            "fixtures": "fixtures.md",
            "redacted": True,
            "probe_sessions": sessions,
            "sessions_note": (
                "More than one session means the same fixtures were probed on "
                "different days and sample labels carry an @date suffix. A "
                "field populated in one session and not another is genuinely "
                "unreliable, and the condition names which session saw it."
            )
            if tag_sessions
            else "Single probe session.",
            "reading_this_file": (
                "Field paths use $ for the record root and [] for an array, so "
                "one path covers every element. populated_on counts sampled "
                "records in which the path held a non-empty value; "
                "present_empty_on counts records where the key was there but "
                "null or empty. populated_on_labels and absent_on_labels name "
                "which samples, which is what a present-but-unreliable row "
                "needs to state its condition (requirements.md D5)."
            ),
            "verdict_rule": (
                "This file is the only admissible basis for a verdict in "
                "specs/paypal-fields.md (requirements.md D1). A field declared "
                "by the SDK but with populated_on 0 here is absent."
            ),
        },
        "required_record_types": coverage(record_types, notes),
        "record_types": record_types,
    }

    write_json(args.out_observed, observed)

    print(f"wrote {args.out_observed}")
    for record_type, entry in record_types.items():
        populated = sum(
            1 for f in entry["fields"].values() if f["populated_on"]
        )
        print(
            f"  {record_type:<14} samples={entry['samples']:<2} "
            f"paths={len(entry['fields']):<4} populated={populated}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
