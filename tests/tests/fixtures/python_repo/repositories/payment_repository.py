"""repositories/payment_repository.py — fixture for DocWiki tests."""

from __future__ import annotations


class PaymentRepository:
    """Persists payment records."""

    def save(self, order) -> str:
        return str(id(order))

    def find(self, order_id: str) -> str:
        return order_id
