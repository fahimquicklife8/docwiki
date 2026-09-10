"""controllers/payment_controller.py — fixture for DocWiki tests."""

from __future__ import annotations

import external_lib as ext  # alias
from models import Order  # alias import test
from services.payment_service import PaymentService  # noqa: F401


class PaymentController:
    """Handles HTTP payment endpoints."""

    def __init__(self, service: PaymentService) -> None:
        self.service = service

    def checkout(self, order: Order) -> dict:
        """Process a checkout request."""
        result = self.service.charge(order, order.amount)
        ext.log("charged")  # unresolved external call
        return {"status": "ok", "result": result}

    def refund(self, order_id: str) -> dict:
        receipt = self.service.refund(order_id)
        return {"refunded": receipt}
