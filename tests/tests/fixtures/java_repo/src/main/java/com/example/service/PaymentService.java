package com.example.service;

import com.example.repository.PaymentRepository;

public class PaymentService implements Chargeable {
    private final PaymentRepository repo;

    public PaymentService(PaymentRepository repo) {
        this.repo = repo;
    }

    @Override
    public String charge(Order order, double amount) {
        String record = repo.save(order);
        return this.confirm(record);
    }

    public String charge(Order order) {
        return this.charge(order, order.getAmount());
    }

    public String refund(String orderId) {
        String rec = repo.find(orderId);
        return this.confirm(rec);
    }

    private String confirm(String record) {
        return "confirmed:" + record;
    }
}
