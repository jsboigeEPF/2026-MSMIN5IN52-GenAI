import importlib.util
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = PROJECT_ROOT / "src" / "validator.py"

spec = importlib.util.spec_from_file_location("validator", VALIDATOR_PATH)
validator_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator_module)

validate_json = validator_module.validate_json
load_schema = validator_module.load_schema


def valid_order():
    return {
        "order_id": "ORD-001",
        "customer": {
            "name": "Alice",
            "email": "alice@example.com"
        },
        "items": [
            {
                "product": "Pizza",
                "quantity": 2,
                "unit_price": 12.5
            }
        ],
        "fulfillment_method": "delivery",
        "delivery_address": "10 rue de Paris",
        "payment_method": "card"
    }


def valid_complex_order():
    order = valid_order()
    order.pop("payment_method")
    order["customer"]["address"] = {
        "street": "10 rue de Paris",
        "city": "Paris",
        "postal_code": "75001"
    }
    order["items"][0]["category"] = "main"
    order["items"][0]["modifiers"] = [
        {"name": "Extra cheese", "price_delta": 2.0}
    ]
    order["special_instructions"] = "Cut into slices"
    return order


def valid_order_with_total():
    return {
        "order_id": "ORD-005",
        "customer": {
            "name": "Alice",
            "email": "alice@example.com"
        },
        "items": [
            {"product": "Pizza", "quantity": 2, "unit_price": 12.5},
            {"product": "Water", "quantity": 1, "unit_price": 2.0}
        ],
        "delivery_fee": 3.0,
        "total": 30.0,
        "payment_method": "card"
    }


# Schema files

@pytest.mark.parametrize(
    "schema_name",
    [
        "schema_01.json",
        "schema_02.json",
        "schema_03.json",
        "schema_04.json",
        "schema_05.json"
    ]
)
def test_all_schemas_are_valid(schema_name):
    assert isinstance(load_schema(schema_name), dict)


# S1: Basic object

def test_s1_valid_data():
    result = validate_json(
        {"name": "Alice", "age": 23},
        "schema_01.json"
    )
    assert result["valid"] is True
    assert result["errors"] == []


def test_s1_missing_required_field():
    result = validate_json({"name": "Alice"}, "schema_01.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "missing_required_field"
        for error in result["errors"]
    )


def test_s1_wrong_type():
    result = validate_json(
        {"name": "Alice", "age": "23"},
        "schema_01.json"
    )
    assert result["valid"] is False
    assert any(error["type"] == "wrong_type" for error in result["errors"])


def test_s1_additional_property():
    result = validate_json(
        {"name": "Alice", "age": 23, "city": "Paris"},
        "schema_01.json"
    )
    assert result["valid"] is False
    assert any(
        error["type"] == "additional_property"
        for error in result["errors"]
    )


def test_s1_invalid_email_format():
    result = validate_json(
        {"name": "Alice", "age": 23, "email": "not-an-email"},
        "schema_01.json"
    )
    assert result["valid"] is False
    assert any(error["type"] == "invalid_format" for error in result["errors"])


def test_s1_error_path():
    result = validate_json(
        {"name": "Alice", "age": "23"},
        "schema_01.json"
    )
    assert any(error["path"] == "/age" for error in result["errors"])


# S2: Nested objects and arrays

def test_s2_valid_data():
    data = {
        "customer": {
            "name": "Alice",
            "email": "alice@example.com"
        },
        "items": [
            {"product": "Pizza", "quantity": 2, "unit_price": 12.5}
        ]
    }
    assert validate_json(data, "schema_02.json")["valid"] is True


def test_s2_empty_items():
    data = {
        "customer": {
            "name": "Alice",
            "email": "alice@example.com"
        },
        "items": []
    }
    assert validate_json(data, "schema_02.json")["valid"] is False


def test_s2_invalid_nested_quantity():
    data = {
        "customer": {
            "name": "Alice",
            "email": "alice@example.com"
        },
        "items": [
            {"product": "Pizza", "quantity": 0, "unit_price": 12.5}
        ]
    }
    result = validate_json(data, "schema_02.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "numeric_constraint_violation"
        for error in result["errors"]
    )


# S3: Enums and conditional constraints

def test_s3_valid_delivery_order():
    assert validate_json(valid_order(), "schema_03.json")["valid"] is True


def test_s3_delivery_requires_address():
    data = valid_order()
    data.pop("delivery_address")
    result = validate_json(data, "schema_03.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "missing_required_field"
        for error in result["errors"]
    )


def test_s3_pickup_does_not_allow_delivery_address():
    data = valid_order()
    data["fulfillment_method"] = "pickup"
    data.pop("delivery_address")
    assert validate_json(data, "schema_03.json")["valid"] is True


def test_s3_invalid_payment_method():
    data = valid_order()
    data["payment_method"] = "cryptocurrency"
    result = validate_json(data, "schema_03.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "invalid_choice"
        for error in result["errors"]
    )


# S4: Complex nested objects and arrays

def test_s4_valid_complex_order():
    assert validate_json(
        valid_complex_order(),
        "schema_04.json"
    )["valid"] is True


def test_s4_invalid_item_category():
    data = valid_complex_order()
    data["items"][0]["category"] = "unknown"
    result = validate_json(data, "schema_04.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "invalid_choice"
        for error in result["errors"]
    )


def test_s4_invalid_modifier_structure():
    data = valid_complex_order()
    data["items"][0]["modifiers"][0]["unexpected"] = True
    result = validate_json(data, "schema_04.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "additional_property"
        for error in result["errors"]
    )


def test_s4_too_many_items():
    data = valid_complex_order()
    data["items"] = [
        {
            "product": "Pizza",
            "quantity": 1,
            "unit_price": 10,
            "category": "main"
        }
        for _ in range(51)
    ]
    result = validate_json(data, "schema_04.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "array_constraint_violation"
        for error in result["errors"]
    )


# S5: Business rules

def test_s5_valid_total():
    assert validate_json(
        valid_order_with_total(),
        "schema_05.json"
    )["valid"] is True


def test_s5_incorrect_total():
    data = valid_order_with_total()
    data["total"] = 29.0
    result = validate_json(data, "schema_05.json")
    assert result["valid"] is False
    assert any(
        error["type"] == "business_rule_violation"
        for error in result["errors"]
    )


def test_s5_total_includes_delivery_fee():
    data = valid_order_with_total()
    data["total"] = 27.0
    result = validate_json(data, "schema_05.json")
    assert result["valid"] is False
    assert any(
        error["path"] == "/total"
        and error["type"] == "business_rule_violation"
        for error in result["errors"]
    )


def test_s5_decimal_calculation():
    data = valid_order_with_total()
    data["items"] = [
        {"product": "Coffee", "quantity": 3, "unit_price": 0.1}
    ]
    data["delivery_fee"] = 0.2
    data["total"] = 0.5
    assert validate_json(data, "schema_05.json")["valid"] is True


def test_s5_invalid_quantity():
    data = valid_order_with_total()
    data["items"][0]["quantity"] = 0
    result = validate_json(data, "schema_05.json")
    assert result["valid"] is False


def test_unknown_schema_raises_file_error():
    with pytest.raises(FileNotFoundError):
        validate_json({"name": "Alice"}, "schema_99.json")


def test_schema_path_is_rejected():
    with pytest.raises(ValueError):
        load_schema("../README.md")
