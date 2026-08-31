# Used to wait between fake customer requests
import time

# Used to generate random order amounts
import random

# requests lets this script send HTTP requests
import requests


# The Order Service endpoint we want to call
ORDER_URL = "http://127.0.0.1:8000/orders"


# How many fake orders we want to send
NUMBER_OF_ORDERS = 30


# Loop 100 times
for order_id in range(1, NUMBER_OF_ORDERS + 1):

    # Create a random price between $10 and $200
    amount = round(random.uniform(10, 200), 2)

    # Build the JSON for this fake order
    order_data = {
        "order_id": order_id,
        "amount": amount,
    }

    try:
        # Send the fake order to our Order Service
        response = requests.post(
            ORDER_URL,
            json=order_data,
            timeout=10,
        )

        # Print basic information so we can see what happened
        print(
            f"Order {order_id} | "
            f"Amount: {amount} | "
            f"Status: {response.status_code}"
        )

    # If something goes wrong with the HTTP request,
    # print the error instead of crashing the whole script
    except requests.RequestException as error:
        print(
            f"Order {order_id} failed: {error}"
        )

    # Wait a little before sending the next fake order
    # This makes the traffic look more realistic
    time.sleep(0.2)