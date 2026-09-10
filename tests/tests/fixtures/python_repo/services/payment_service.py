"""services/payment_service.py — fixture for DocWiki tests."""

from __future__ import annotations

from repositories.payment_repository import PaymentRepository


class PaymentService:
    """Business logic for payment processing."""

    def __init__(self, repo: PaymentRepository) -> None:
        self._repo = repo

    def charge(self, order, amount) -> str:
        """Charge the order and persist the record."""
        record = self._repo.save(order)  # self call resolved
        return self._confirm(record)

    def refund(self, order_id: str) -> str:
        rec = self._repo.find(order_id)
        return self._confirm(rec)

    def _confirm(self, record) -> str:
        return f"confirmed:{record}"
