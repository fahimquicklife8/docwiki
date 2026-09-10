package com.example.repository;

public class PaymentRepository {
    public String save(Object order) {
        return String.valueOf(order.hashCode());
    }

    public String find(String orderId) {
        return orderId;
    }

    // Overloaded method
    public String find(String orderId, boolean includeArchived) {
        return orderId + ":" + includeArchived;
    }
}
