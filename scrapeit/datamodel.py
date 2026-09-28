"""The output data model -- customization surface #3.

Deliberately loose. This file exists to do two jobs:

  1. tell the extraction LLM what to produce (``prompt_block``)
  2. sanity-check what it produced (``validate``)

It is *not* a database schema, so there are no foreign keys, no migrations, and
no strict typing. "Missing beats guessed" -- an absent field is fine, an
invented one is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

TYPES = ("string", "int", "float", "bool", "date", "enum", "list", "object")


class DataModelError(ValueError):
    """The data model file is malformed."""


@dataclass
class Field:
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    values: list[str] = field(default_factory=list)
    of: str | None = None


@dataclass
class DataModel:
    name: str
    description: str
    fields: list[Field]

    def prompt_block(self) -> str:
        """Render the fields as instructions for the extraction LLM."""
        lines: list[str] = []
        for f in self.fields:
            bits = [f"type={f.type}"]
            if f.type == "enum" and f.values:
                bits.append("one of [" + ", ".join(f.values) + "]")
            if f.type == "list" and f.of:
                bits.append(f"of {f.of}")
            bits.append("REQUIRED" if f.required else "optional")
            head = f"- {f.name} ({', '.join(bits)})"
            if f.description:
                head += f": {f.description}"
            lines.append(head)
        return "\n".join(lines)


def load_data_model(path: str | Path = "data_model.yaml") -> DataModel:
    p = Path(path)
    if not p.exists():
        raise DataModelError(f"data model not found: {p}")

    raw = yaml.safe_load(p.read_text()) or {}
    if not isinstance(raw, dict):
        raise DataModelError(f"{p} must contain a mapping")

    raw_fields = raw.get("fields")
    if not isinstance(raw_fields, list) or not raw_fields:
        raise DataModelError(f"{p}: 'fields' must be a non-empty list")

    fields: list[Field] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw_fields):
        if not isinstance(entry, dict) or not entry.get("name"):
            raise DataModelError(f"{p}: field {i} is missing 'name'")
        name = str(entry["name"])
        if name in seen:
            raise DataModelError(f"{p}: duplicate field {name!r}")
        seen.add(name)

        ftype = str(entry.get("type", "string"))
        if ftype not in TYPES:
            raise DataModelError(
                f"{p}: field {name!r} has unknown type {ftype!r} (known: {', '.join(TYPES)})"
            )
        values = entry.get("values") or []
        if ftype == "enum" and not values:
            raise DataModelError(f"{p}: enum field {name!r} needs 'values'")

        fields.append(
            Field(
                name=name,
                type=ftype,
                required=bool(entry.get("required", False)),
                description=str(entry.get("description", "") or ""),
                values=[str(v) for v in values],
                of=(str(entry["of"]) if entry.get("of") else None),
            )
        )

    return DataModel(
        name=str(raw.get("name", "record")),
        description=str(raw.get("description", "") or ""),
        fields=fields,
    )


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def validate(model: DataModel, payload: Any) -> tuple[dict[str, Any], list[str]]:
    """Loose validation. Returns ``(clean_row, problems)``.

    Unknown keys are dropped. Required-but-missing fields are reported. Type
    mismatches are reported but the value is kept where coercion is obvious.
    """
    problems: list[str] = []
    if not isinstance(payload, dict):
        return {}, [f"expected an object, got {type(payload).__name__}"]

    known = {f.name: f for f in model.fields}
    for key in payload:
        if key not in known:
            problems.append(f"dropped unknown field {key!r}")

    clean: dict[str, Any] = {}
    for f in model.fields:
        value = payload.get(f.name)
        if _is_empty(value):
            if f.required:
                problems.append(f"required field {f.name!r} is missing")
            continue

        if f.type == "int" and not isinstance(value, int):
            try:
                value = int(value)
            except (TypeError, ValueError):
                problems.append(f"field {f.name!r} is not an int: {value!r}")
                continue
        elif f.type == "float" and not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (TypeError, ValueError):
                problems.append(f"field {f.name!r} is not a float: {value!r}")
                continue
        elif f.type == "bool" and not isinstance(value, bool):
            problems.append(f"field {f.name!r} is not a bool: {value!r}")
            continue
        elif f.type == "list" and not isinstance(value, list):
            value = [value]
        elif f.type == "enum" and f.values and value not in f.values:
            problems.append(
                f"field {f.name!r} is {value!r}, not one of {f.values}"
            )

        clean[f.name] = value

    return clean, problems
