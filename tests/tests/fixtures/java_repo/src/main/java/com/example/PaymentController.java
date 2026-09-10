package com.example;

import com.example.service.PaymentService;
import com.example.repository.PaymentRepository;

public class PaymentController {
    private final PaymentService service;

    public PaymentController(PaymentService service) {
        this.service = service;
    }

    public String checkout(Order order) {
        return service.charge(order, order.getAmount());
    }

    public String refund(String orderId) {
        return service.refund(orderId);
    }
}
