# Used to read our JSON metric files
import json

# Lets us build file paths safely
from pathlib import Path

# Isolation Forest is the ML algorithm we use
from sklearn.ensemble import IsolationForest


# Get the folder where detector.py is located
BASE_DIR = Path(__file__).resolve().parent


# Folder containing our datasets
DATA_DIR = BASE_DIR / "data"


# Historical normal Payment Service traffic
NORMAL_FILE = DATA_DIR / "normal_payments.jsonl"


# Historical slow Payment Service traffic
# We currently use this only for testing the model
SLOW_FILE = DATA_DIR / "slow_payments.jsonl"


# Read payment latencies from a JSONL metrics file
def load_payment_latencies(file_path):

    # Each item will look like:
    # [100.35]
    #
    # ML models expect each example to be a list of features.
    latencies = []

    # Open the metrics file
    with open(file_path, "r") as file:

        # JSONL stores one JSON object on each line
        for line in file:

            # Convert JSON text into a Python dictionary
            metric = json.loads(line)

            # Ignore endpoints such as:
            # /health
            # /docs
            # /openapi.json
            #
            # Right now our model only learns /payments behavior.
            if metric["path"] != "/payments":
                continue

            # Get the latency of this request
            latency = metric["latency_ms"]

            # Add it as one ML example
            latencies.append([latency])

    return latencies


# Train a new anomaly detection model
def train_model(normal_data):

    model = IsolationForest(
    # We expect almost all training traffic to be normal.
    # contamination=0.02 tells the model that only about
    # 2% of the training examples should be treated as unusual.
    contamination=0.02,

    # Makes results repeatable between runs.
    random_state=42,
)
   

    # IMPORTANT:
    # Train only on healthy traffic.
    model.fit(normal_data)

    return model


# Analyze one new live request
def predict_latency(model, latency_ms):

    # The model expects a list of examples,
    # and every example must be a list of features.
    #
    # So one latency becomes:
    # [[latency_ms]]
    prediction = model.predict(
        [[latency_ms]]
    )[0]

    # Isolation Forest returns:
    #
    #  1 = normal
    # -1 = anomaly
    is_anomaly = prediction == -1

    return is_anomaly


# This function is only used when we manually run detector.py.
#
# It lets us verify that the refactored detector
# still behaves like our original experiment.
def run_test():

    # Load healthy historical requests
    normal_data = load_payment_latencies(
        NORMAL_FILE
    )

    # Load intentionally slow requests
    slow_data = load_payment_latencies(
        SLOW_FILE
    )

    print(
        f"Normal requests loaded: {len(normal_data)}"
    )

    print(
        f"Slow requests loaded: {len(slow_data)}"
    )

    # Train the model using ONLY normal traffic
    model = train_model(normal_data)

    # Count anomalies among normal traffic
    normal_anomalies = 0

    for request in normal_data:

        # request looks like:
        # [100.42]
        latency = request[0]

        if predict_latency(model, latency):
            normal_anomalies += 1

    # Count anomalies among slow traffic
    slow_anomalies = 0

    for request in slow_data:

        latency = request[0]

        if predict_latency(model, latency):
            slow_anomalies += 1

    print("\n--- SentinelAI Results ---")

    print(
        f"Normal traffic: "
        f"{normal_anomalies}/{len(normal_data)} "
        f"marked as anomalies"
    )

    print(
        f"Slow traffic: "
        f"{slow_anomalies}/{len(slow_data)} "
        f"marked as anomalies"
    )

    print("\nSlow request predictions:")

    # Show the prediction for every slow request
    for request in slow_data:

        latency = request[0]

        # Ask our reusable function to classify it
        is_anomaly = predict_latency(
            model,
            latency,
        )

        if is_anomaly:
            result = "ANOMALY"
        else:
            result = "NORMAL"

        print(
            f"{latency:.2f} ms -> {result}"
        )


# Python runs this section only when we execute:
#
# python detector.py
#
# If another SentinelAI file imports detector.py,
# this test will NOT automatically run.
if __name__ == "__main__":
    run_test()