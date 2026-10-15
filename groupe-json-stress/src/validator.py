import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_DIR = Path(__file__).parent / "schemas"


def load_schema(schema_name: str = "schema_01.json") -> dict:
    """Charge et vérifie un schéma JSON."""
    if Path(schema_name).name != schema_name:
        raise ValueError("Le nom du schéma doit être un nom de fichier simple.")

    schema_path = SCHEMA_DIR / schema_name

    with schema_path.open("r", encoding="utf-8") as file:
        schema = json.load(file)

    Draft202012Validator.check_schema(schema)
    return schema


def classify_error(error) -> str:
    """Classe une erreur de validation JSON Schema."""
    if error.validator == "required":
        return "missing_required_field"
    if error.validator == "type":
        return "wrong_type"
    if error.validator == "additionalProperties":
        return "additional_property"
    if error.validator == "enum" or error.validator == "const":
        return "invalid_choice"
    if error.validator == "format":
        return "invalid_format"
    if error.validator in {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}:
        return "numeric_constraint_violation"
    if error.validator in {"minLength", "maxLength", "pattern"}:
        return "string_constraint_violation"
    if error.validator in {"minItems", "maxItems", "uniqueItems"}:
        return "array_constraint_violation"
    if error.validator in {"if", "then", "else", "not"}:
        return "conditional_constraint_violation"
    return "constraint_violation"


def _business_errors(data: Any) -> list[dict]:
    """Vérifie les règles métier du schéma S5."""
    if not isinstance(data, dict):
        return []

    items = data.get("items")
    delivery_fee = data.get("delivery_fee")
    total = data.get("total")

    # Les erreurs structurelles sont déjà signalées par JSON Schema.
    if not isinstance(items, list):
        return []
    if not isinstance(delivery_fee, (int, float)) or isinstance(delivery_fee, bool):
        return []
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        return []

    expected_total = Decimal(str(delivery_fee))

    try:
        for item in items:
            if not isinstance(item, dict):
                return []
            quantity = item.get("quantity")
            unit_price = item.get("unit_price")

            if not isinstance(quantity, int) or isinstance(quantity, bool):
                return []
            if not isinstance(unit_price, (int, float)) or isinstance(unit_price, bool):
                return []

            expected_total += Decimal(str(quantity)) * Decimal(str(unit_price))

        actual_total = Decimal(str(total))
    except (InvalidOperation, ValueError):
        return []

    if actual_total != expected_total:
        return [
            {
                "path": "/total",
                "message": (
                    f"Le total doit être égal à {expected_total}, "
                    f"mais la valeur reçue est {actual_total}."
                ),
                "type": "business_rule_violation",
            }
        ]

    return []


def validate_json(data: Any, schema_name: str = "schema_01.json") -> dict:
    """Valide les données et retourne les erreurs structurées."""
    schema = load_schema(schema_name)
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    )

    schema_errors = sorted(
        validator.iter_errors(data),
        key=lambda error: (
            list(map(str, error.absolute_path)),
            error.message,
        ),
    )

    errors = [
        {
            "path": "/" + "/".join(str(part) for part in error.absolute_path),
            "message": error.message,
            "type": classify_error(error),
        }
        for error in schema_errors
    ]

    if schema_name == "schema_05.json":
        errors.extend(_business_errors(data))

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }
