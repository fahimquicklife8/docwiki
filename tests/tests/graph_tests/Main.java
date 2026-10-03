package com.docwiki.fixture;

public class Main {

    public static void main(String[] args) {
        OrderRepository repository = new OrderRepository();
        PriceCalculator priceCalculator = new PriceCalculator();
        OrderValidator validator = new OrderValidator();

        OrderService service =
                new OrderService(repository, priceCalculator);

        OrderController controller =
                new OrderController(service, validator);

        controller.placeOrder("BOOK", 100.0);
    }
}