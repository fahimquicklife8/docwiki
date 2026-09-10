"""models.py — fixture for DocWiki tests."""

from __future__ import annotations


class Order:
    def __init__(self, order_id: str, amount: float) -> None:
        self.order_id = order_id
        self.amount = amount
