package com.docwiki.fixture;

public class OrderController {

    private final OrderService orderService;
    private final OrderValidator validator;

    public OrderController(
            OrderService orderService,
            OrderValidator validator) {

        this.orderService = orderService;
        this.validator = validator;
    }

    public void placeOrder(String product, double basePrice) {
        validateRequest(product, basePrice);

        validator.validateProduct(product);

        double finalPrice =
                orderService.createOrder(product, basePrice);

        AuditLogger.log("Order created for " + product
                + " at $" + finalPrice);
    }

    private void validateRequest(String product, double price) {
        if (product == null || product.isBlank()) {
            throw new IllegalArgumentException("Product is required");
        }

        if (price <= 0) {
            throw new IllegalArgumentException("Price must be positive");
        }
    }
}