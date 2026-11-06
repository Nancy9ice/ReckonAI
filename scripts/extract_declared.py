#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Extract the declared PayPal contract - Phase 0, task group 4.

Produces artifacts/declared.json: per record type, every field path the
provider's own contract declares, with optionality, read-only status and type.

Two source grades, because no single source covers all five record types
(requirements.md section 3.1):

  sdk_generated_contract
      The APIMatic-generated PayPal Server SDK at a pinned tag. Covers
      captures, refunds, orders, transactions and balances. Field names come
      from each model's _names map, optionality from _optionals, nullability
      from _nullables, and the Read-only tag from the generated doc/models
      tables - the Python classes do not carry read-only at all, which is why
      both halves of the SDK are read rather than just one.

  openapi_specification
      PayPal's own published OpenAPI 3.0.3 documents at a pinned commit.
      Covers disputes and payouts, which the SDK does not model at all.

Keys are API property names, never Python attribute names. The generator
renames fields, so keying on the Python name would make declared.json and
observed.json undiffable and defeat the point of the artifact
(plan.md group 4 step 4). Where the two differ, the Python name is kept
alongside under python_attr and the field is flagged renamed.

Paths use the same syntax as observed.json - $ for the record root, [] for an
array - so the two artifacts diff mechanically. That diff is the gap section of
specs/paypal-fields.md (validation.md C7).

This script reads the SDK as a read-only reference. It never imports it, and
the SDK is never added as a project dependency (requirements.md section 4).

Usage:
    uv run scripts/extract_declared.py --help
    uv run scripts/extract_declared.py \\
        --sdk /path/to/PayPal-Python-Server-SDK \\
        --openapi /path/to/paypal-rest-api-specifications \\
        --out specs/2026-11-05-paypal-field-discovery/artifacts/declared.json
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SDK_GRADE = "sdk_generated_contract"
OPENAPI_GRADE = "openapi_specification"

# Record type -> (root model class, the endpoint the probe actually called).
# The root model is the controller's own deserializer for that endpoint; see
# .deserialize_into(...) in the generated controllers.
SDK_ROOTS: dict[str, tuple[str, str, str]] = {
    "captures": (
        "CapturedPayment",
        "/v2/payments/captures/{id}",
        "payments_controller.get_captured_payment",
    ),
    "refunds": (
        "Refund",
        "/v2/payments/refunds/{id}",
        "payments_controller.get_refund",
    ),
    "orders": ("Order", "/v2/checkout/orders/{id}", "orders_controller.get_order"),
    "transactions": (
        "SearchResponse",
        "/v1/reporting/transactions",
        "transaction_search_controller.search_transactions",
    ),
    "balances": (
        "BalancesResponse",
        "/v1/reporting/balances",
        "transaction_search_controller.search_balances",
    ),
}

# Record type -> (spec file, path, method). The root schema is read from the
# 200 response rather than named here, so it cannot drift from the document.
OPENAPI_ROOTS: dict[str, tuple[str, str, str]] = {
    "disputes": ("openapi/customer_disputes_v1.json", "/v1/customer/disputes", "get"),
    "payouts": (
        "openapi/payments_payouts_batch_v1.json",
        "/v1/payments/payouts/{id}",
        "get",
    ),
}

SCALARS = {"str": "string", "bool": "boolean", "int": "integer", "float": "number"}

# A doc table's description cell contains escaped pipes, inside the regex
# patterns PayPal publishes for its date fields. Splitting a row on a bare
# pipe therefore corrupts the row.
ROW_SPLIT = re.compile(r"(?<!\\)\|")
BACKTICKED = re.compile(r"`([^`]+)`")
LIST_OF = re.compile(r"^List\[(.+)\]$")
DICT_OF = re.compile(r"^Dict\[\s*str\s*,\s*(.+)\]$")


def git_fact(repo: Path, *args: str) -> str:
    """One fact from a local clone, or the empty string."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


# --------------------------------------------------------------------------
# The SDK half
# --------------------------------------------------------------------------


def parse_doc_page(path: Path) -> tuple[str, list[dict[str, str]]] | None:
    """Read one doc/models page into (class name, field rows).

    The doc pages are the only place the Read-only tag exists; the generated
    Python classes do not record it.
    """
    text = path.read_text(encoding="utf-8")
    structure = re.search(r"## Structure\s*\n+`([^`]+)`", text)
    if not structure:
        return None
    class_name = structure.group(1).strip()

    fields: list[dict[str, str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("| Name | Type | Tags |"):
            in_table = True
            continue
        if in_table:
            if not stripped.startswith("|"):
                break
            cells = [c.strip() for c in ROW_SPLIT.split(stripped)[1:-1]]
            if len(cells) < 3 or set(cells[0]) <= {"-", " "}:
                continue
            name = BACKTICKED.search(cells[0])
            if not name:
                continue
            fields.append(
                {
                    "python_attr": name.group(1).strip(),
                    "type_cell": cells[1],
                    "tags": cells[2],
                }
            )
    return class_name, fields


def parse_model_module(path: Path) -> dict[str, Any] | None:
    """Read one generated model class without importing it.

    Returns the class name, its _names map, and its _optionals and _nullables
    lists. An enum class has no _names and is reported with its constants.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None

    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        info: dict[str, Any] = {
            "class_name": node.name,
            "names": {},
            "optionals": [],
            "nullables": [],
            "enum_values": [],
        }
        for stmt in node.body:
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                try:
                    value = ast.literal_eval(stmt.value)
                except (ValueError, SyntaxError):
                    continue
                if target.id == "_names" and isinstance(value, dict):
                    info["names"] = value
                elif target.id == "_optionals" and isinstance(value, list):
                    info["optionals"] = value
                elif target.id == "_nullables" and isinstance(value, list):
                    info["nullables"] = value
                elif target.id.isupper() and isinstance(value, str):
                    info["enum_values"].append(value)
        info["is_enum"] = not info["names"] and bool(info["enum_values"])
        return info
    return None


def load_sdk(sdk: Path) -> dict[str, dict[str, Any]]:
    """Join the doc tables and the model classes on the class name."""
    doc_dir = sdk / "doc" / "models"
    model_dir = sdk / "paypalserversdk" / "models"
    if not doc_dir.is_dir() or not model_dir.is_dir():
        sys.exit(f"error: {sdk} does not look like the PayPal Python Server SDK")

    docs: dict[str, list[dict[str, str]]] = {}
    for page in sorted(doc_dir.glob("*.md")):
        parsed = parse_doc_page(page)
        if parsed:
            class_name, fields = parsed
            docs[class_name] = fields

    models: dict[str, dict[str, Any]] = {}
    for module in sorted(model_dir.glob("*.py")):
        if module.name == "__init__.py":
            continue
        info = parse_model_module(module)
        if info:
            info["doc_fields"] = docs.get(info["class_name"], [])
            models[info["class_name"]] = info
    return models


def classify_sdk_type(type_cell: str) -> dict[str, Any]:
    """Turn a doc Type cell into a type decision.

    Cells look like `str`, [`Money`](...), [`List[LinkDescription]`](...) or
    `List[str]`. The first backticked token is the type expression.
    """
    token = BACKTICKED.search(type_cell)
    if not token:
        return {"type": "unknown", "type_spec": type_cell}
    spec = token.group(1).strip()

    is_array = False
    inner = spec
    matched = LIST_OF.match(spec)
    if matched:
        is_array = True
        inner = matched.group(1).strip()

    mapped = DICT_OF.match(inner)
    if mapped:
        return {
            "type": "object",
            "type_spec": spec,
            "map_of": mapped.group(1).strip(),
            "is_array": is_array,
        }

    if inner in SCALARS:
        return {"type": SCALARS[inner], "type_spec": spec, "is_array": is_array}
    if inner in ("Any", "object", "dict"):
        return {"type": "unknown", "type_spec": spec, "is_array": is_array}
    return {"type": "model", "type_spec": spec, "model": inner, "is_array": is_array}


def walk_sdk(
    models: dict[str, dict[str, Any]],
    class_name: str,
    path: str,
    out: dict[str, Any],
    stack: tuple[str, ...],
    max_depth: int,
) -> None:
    model = models.get(class_name)
    if model is None:
        out[path] = {
            "type": "unknown",
            "source_grade": SDK_GRADE,
            "note": f"model {class_name} not found in the SDK",
        }
        return

    names: dict[str, str] = model["names"]
    optionals = set(model["optionals"])
    nullables = set(model["nullables"])

    for field in model["doc_fields"]:
        python_attr = field["python_attr"]
        api_name = names.get(python_attr)
        if api_name is None:
            # A doc row with no entry in _names would mean the two halves of
            # the SDK disagree. Record it rather than dropping it.
            out[f"{path}.{python_attr}"] = {
                "type": "unknown",
                "source_grade": SDK_GRADE,
                "python_attr": python_attr,
                "note": "present in doc/models but absent from the model's "
                "_names map; API property name unknown",
            }
            continue

        tags = {t.strip().lower() for t in field["tags"].split(",") if t.strip()}
        decided = classify_sdk_type(field["type_cell"])
        is_array = bool(decided.pop("is_array", False))
        field_path = f"{path}.{api_name}"

        entry: dict[str, Any] = {
            "type": "array" if is_array else decided["type"],
            "type_spec": decided["type_spec"],
            "required": python_attr not in optionals and "optional" not in tags,
            "read_only": "read-only" in tags,
            "nullable": python_attr in nullables,
            "source_grade": SDK_GRADE,
            "declared_in_model": class_name,
            "python_attr": python_attr,
        }
        if python_attr != api_name:
            entry["renamed"] = True
        if "map_of" in decided:
            entry["map_of"] = decided["map_of"]
        out[field_path] = entry

        element_path = f"{field_path}[]" if is_array else field_path
        if is_array:
            out[element_path] = {
                "type": decided["type"],
                "type_spec": decided["type_spec"],
                "source_grade": SDK_GRADE,
                "array_element_of": field_path,
            }

        if decided["type"] != "model":
            continue

        nested = decided["model"]
        nested_model = models.get(nested)
        if nested_model and nested_model["is_enum"]:
            target = out[element_path]
            target["type"] = "string"
            target["enum"] = nested
            target["enum_values"] = nested_model["enum_values"]
            continue

        target = out[element_path]
        target["type"] = "object"
        target["model"] = nested

        if nested in stack:
            target["recursive_model"] = True
            continue
        if len(stack) >= max_depth:
            target["truncated_at_depth"] = max_depth
            continue
        walk_sdk(models, nested, element_path, out, stack + (nested,), max_depth)


# --------------------------------------------------------------------------
# The OpenAPI half
# --------------------------------------------------------------------------


def resolve(document: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Follow a local $ref, keeping any sibling description."""
    seen = 0
    while "$ref" in schema and seen < 20:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return schema
        node: Any = document
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        merged = dict(node)
        for key, value in schema.items():
            if key != "$ref":
                merged.setdefault(key, value)
        schema = merged
        seen += 1
    return schema


def schema_name(schema: dict[str, Any]) -> str | None:
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
        return ref.rsplit("/", 1)[-1]
    return schema.get("title")


def walk_openapi(
    document: dict[str, Any],
    schema: dict[str, Any],
    path: str,
    out: dict[str, Any],
    stack: tuple[str, ...],
    max_depth: int,
) -> None:
    resolved = resolve(document, schema)
    properties = resolved.get("properties") or {}
    required = set(resolved.get("required") or [])

    for api_name, raw in properties.items():
        child = resolve(document, raw)
        field_path = f"{path}.{api_name}"
        declared_type = child.get("type")
        is_array = declared_type == "array"

        entry: dict[str, Any] = {
            "type": "array" if is_array else (declared_type or "object"),
            "required": api_name in required,
            "read_only": bool(child.get("readOnly")),
            "nullable": bool(child.get("nullable")),
            "source_grade": OPENAPI_GRADE,
        }
        name = schema_name(raw) or schema_name(child)
        if name:
            entry["declared_schema"] = name
        if child.get("format"):
            entry["format"] = child["format"]
        if child.get("enum"):
            entry["enum_values"] = list(child["enum"])
        if child.get("oneOf"):
            entry["union_of"] = [
                schema_name(branch) or "anonymous" for branch in child["oneOf"]
            ]
        out[field_path] = entry

        element_path = field_path
        if is_array:
            items = resolve(document, child.get("items") or {})
            element_path = f"{field_path}[]"
            element: dict[str, Any] = {
                "type": items.get("type") or "object",
                "source_grade": OPENAPI_GRADE,
                "array_element_of": field_path,
            }
            element_name = schema_name(child.get("items") or {}) or schema_name(items)
            if element_name:
                element["declared_schema"] = element_name
            if items.get("enum"):
                element["enum_values"] = list(items["enum"])
            out[element_path] = element
            child = items

        if not (child.get("properties") or child.get("type") == "object"):
            continue

        marker = schema_name(child) or element_path
        if marker in stack:
            out[element_path]["recursive_schema"] = True
            continue
        if len(stack) >= max_depth:
            out[element_path]["truncated_at_depth"] = max_depth
            continue
        walk_openapi(document, child, element_path, out, stack + (marker,), max_depth)


# --------------------------------------------------------------------------


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=False)
    path.write_text(text + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="extract_declared.py",
        description=(
            "Extract the declared PayPal contract into declared.json, keyed on "
            "API property names. Reads the SDK and the OpenAPI documents as "
            "read-only references; imports neither."
        ),
    )
    parser.add_argument(
        "--sdk",
        type=Path,
        required=True,
        metavar="DIR",
        help="A clone of paypal/PayPal-Python-Server-SDK, checked out at a tag.",
    )
    parser.add_argument(
        "--openapi",
        type=Path,
        required=True,
        metavar="DIR",
        help="A clone of paypal/paypal-rest-api-specifications.",
    )
    parser.add_argument(
        "--out", type=Path, required=True, metavar="PATH", help="Where declared.json goes."
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        metavar="N",
        help="Nesting depth to expand. Default 8, which covers the deepest "
        "path observed.json contains.",
    )
    args = parser.parse_args()

    models = load_sdk(args.sdk)
    record_types: dict[str, Any] = {}

    for record_type, (root, endpoint, operation) in SDK_ROOTS.items():
        if root not in models:
            sys.exit(f"error: root model {root} not found in {args.sdk}")
        # observed.json records the record root as "$". Declaring it too keeps
        # the two artifacts' key sets comparable, so a path present in one and
        # not the other is a real divergence rather than a walk artefact.
        fields: dict[str, Any] = {
            "$": {
                "type": "object",
                "source_grade": SDK_GRADE,
                "model": root,
                "record_root": True,
            }
        }
        walk_sdk(models, root, "$", fields, (root,), args.max_depth)
        record_types[record_type] = {
            "source_grade": SDK_GRADE,
            "endpoint": endpoint,
            "sdk_operation": operation,
            "root_model": root,
            "field_count": len(fields),
            "fields": dict(sorted(fields.items())),
        }

    for record_type, (spec_file, endpoint, method) in OPENAPI_ROOTS.items():
        document_path = args.openapi / spec_file
        if not document_path.exists():
            sys.exit(f"error: no such OpenAPI document: {document_path}")
        document = json.loads(document_path.read_text(encoding="utf-8"))
        operation = (document.get("paths", {}).get(endpoint, {}) or {}).get(method)
        if not operation:
            sys.exit(f"error: {spec_file} declares no {method.upper()} {endpoint}")
        ok = operation.get("responses", {}).get("200", {})
        schema = (ok.get("content", {}).get("application/json", {}) or {}).get("schema")
        if not schema:
            sys.exit(f"error: no 200 application/json schema for {endpoint}")

        root_name = schema_name(schema) or "anonymous"
        fields = {
            "$": {
                "type": "object",
                "source_grade": OPENAPI_GRADE,
                "declared_schema": root_name,
                "record_root": True,
            }
        }
        walk_openapi(document, schema, "$", fields, (root_name,), args.max_depth)
        record_types[record_type] = {
            "source_grade": OPENAPI_GRADE,
            "endpoint": endpoint,
            "spec_file": spec_file,
            "spec_version": document.get("info", {}).get("version"),
            "openapi_version": document.get("openapi"),
            "root_schema": root_name,
            "field_count": len(fields),
            "fields": dict(sorted(fields.items())),
        }

    sdk_commit = git_fact(args.sdk, "rev-parse", "HEAD")
    sdk_tag = git_fact(args.sdk, "describe", "--tags")
    openapi_commit = git_fact(args.openapi, "rev-parse", "HEAD")
    openapi_date = git_fact(args.openapi, "log", "-1", "--format=%cI")

    declared = {
        "declared": {
            "phase": "0",
            "task_group": "4",
            "generated_by": "scripts/extract_declared.py",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "max_depth": args.max_depth,
            "keyed_on": (
                "API property names - the value side of each SDK model's "
                "_names map, and the property key in the OpenAPI documents. "
                "Never the Python attribute name, which the generator renames "
                "(plan.md group 4 step 4)."
            ),
            "path_syntax": (
                "Same as observed.json: $ is the record root and [] is an "
                "array element, so the two artifacts diff mechanically."
            ),
            "not_evidence": (
                "A field here is declared, not delivered. Verdicts in "
                "specs/paypal-fields.md are judged on observed.json alone "
                "(requirements.md D1). This column is context."
            ),
            "provenance": {
                "apimatic_context_plugin": {
                    "installed_as": "paypal@context-plugins-local",
                    "role": (
                        "Named the SDK distribution and version; holds no "
                        "PayPal field names. See requirements.md section 1 and "
                        "notes.md, 'Prerequisite 1'."
                    ),
                },
                "sdk_generated_contract": {
                    "repository": "https://github.com/paypal/PayPal-Python-Server-SDK",
                    "tag": sdk_tag,
                    "commit": sdk_commit,
                    "read_from": [
                        "doc/models/*.md - Name, Type and Tags, including Read-only",
                        "paypalserversdk/models/*.py - _names, _optionals, _nullables",
                    ],
                    "models_parsed": len(models),
                    "imported": False,
                },
                "openapi_specification": {
                    "repository": "https://github.com/paypal/paypal-rest-api-specifications",
                    "commit": openapi_commit,
                    "commit_date": openapi_date,
                    "documents": sorted(
                        {spec for spec, _e, _m in OPENAPI_ROOTS.values()}
                    ),
                },
            },
            "source_grade_by_record_type": {
                name: entry["source_grade"] for name, entry in sorted(record_types.items())
            },
            "source_grades": {
                SDK_GRADE: (
                    "Machine-extracted from the APIMatic-generated SDK at a "
                    "pinned tag. The contract the SDK would enforce."
                ),
                OPENAPI_GRADE: (
                    "Machine-extracted from PayPal's own published OpenAPI "
                    "3.0.3 document at a pinned commit. Authoritative and "
                    "pinnable, but not the contract the tested SDK generates, "
                    "and it may lag the deployed API. See notes.md, Group 4."
                ),
            },
        },
        "record_types": dict(sorted(record_types.items())),
    }

    write_json(args.out, declared)
    print(f"wrote {args.out}")
    for name, entry in declared["record_types"].items():
        print(
            f"  {name:<14} {entry['source_grade']:<24} "
            f"paths={entry['field_count']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
