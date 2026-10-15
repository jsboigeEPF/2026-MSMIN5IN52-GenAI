
"""
JSON Stress project: text generators.

The generator returns raw JSON text. JSON parsing and schema validation
are handled by other modules.

The mock generator works offline and supports deterministic test scenarios.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Protocol


class GeneratorError(Exception):
    """Raised when the generator cannot produce a response."""


class JSONGenerator(Protocol):
    """Common interface for mock and real model generators."""

    provider: str
    model: str

    def generate(
        self,
        schema_name: str,
        feedback: list[dict] | None = None,
    ) -> str:
        """Generate raw JSON text for the requested schema."""
        ...


def _example_for_schema(schema_name: str) -> dict:
    """Return a valid example for one of the five project schemas."""

    examples = {
        "schema_01.json": {
            "name": "Alice",
            "age": 23,
        },
        "schema_02.json": {
            "customer": {
                "name": "Alice",
                "email": "alice@example.com",
            },
            "items": [
                {
                    "product": "Pizza",
                    "quantity": 2,
                    "unit_price": 12.5,
                }
            ],
        },
        "schema_03.json": {
            "order_id": "ORD-003",
            "customer": {
                "name": "Alice",
                "email": "alice@example.com",
            },
            "items": [
                {
                    "product": "Pizza",
                    "quantity": 2,
                    "unit_price": 12.5,
                }
            ],
            "fulfillment_method": "delivery",
            "delivery_address": "10 rue de Paris",
            "payment_method": "card",
        },
        "schema_04.json": {
            "order_id": "ORD-004",
            "customer": {
                "name": "Alice",
                "email": "alice@example.com",
                "address": {
                    "street": "10 rue de Paris",
                    "city": "Paris",
                    "postal_code": "75001",
                },
            },
            "items": [
                {
                    "product": "Pizza",
                    "quantity": 2,
                    "unit_price": 12.5,
                    "category": "main",
                    "modifiers": [
                        {
                            "name": "Extra cheese",
                            "price_delta": 2.0,
                        }
                    ],
                }
            ],
            "fulfillment_method": "delivery",
            "delivery_address": "10 rue de Paris",
            "special_instructions": "Cut into slices",
        },
        "schema_05.json": {
            "order_id": "ORD-005",
            "customer": {
                "name": "Alice",
                "email": "alice@example.com",
            },
            "items": [
                {
                    "product": "Pizza",
                    "quantity": 2,
                    "unit_price": 12.5,
                },
                {
                    "product": "Water",
                    "quantity": 1,
                    "unit_price": 2.0,
                },
            ],
            "delivery_fee": 3.0,
            "total": 30.0,
            "payment_method": "card",
        },
    }

    try:
        return examples[schema_name]
    except KeyError as exc:
        raise ValueError(
            f"Unsupported schema: {schema_name}"
        ) from exc


class MockGenerator:
    """
    Offline generator for development and automated tests.

    scripted_outputs can define a sequence of responses for each schema.
    Once a sequence is exhausted, the generator returns a valid example.

    Example:
        MockGenerator(
            scripted_outputs={
                "schema_01.json": [
                    '{"name": "Alice"}',
                    '{"name": "Alice", "age": 23}',
                ]
            }
        )
    """

    provider = "mock"
    model = "mock-v1"

    def __init__(
        self,
        scripted_outputs: dict[str, list[str]] | None = None,
    ) -> None:
        self._scripted_outputs = {
            name: list(outputs)
            for name, outputs in (scripted_outputs or {}).items()
        }
        self._positions: defaultdict[str, int] = defaultdict(int)
        self.calls = 0

    def generate(
        self,
        schema_name: str,
        feedback: list[dict] | None = None,
    ) -> str:
        """
        Return raw JSON text.

        feedback is accepted to keep the same interface as a future
        real-model adapter. The mock does not need it to select a response.
        """
        del feedback  # The mock uses predefined responses instead.

        self.calls += 1

        if schema_name in self._scripted_outputs:
            outputs = self._scripted_outputs[schema_name]
            position = self._positions[schema_name]

            if position < len(outputs):
                self._positions[schema_name] += 1
                return outputs[position]

        example = _example_for_schema(schema_name)
        return json.dumps(
            example,
            ensure_ascii=False,
        )
