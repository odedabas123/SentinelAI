# Use a small official Python image
FROM python:3.12-slim

# All SentinelAI files will live inside /app
WORKDIR /app

# Copy dependency list first
COPY requirements.txt .

# Install Python packages used by the services and ML monitor
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire SentinelAI project into the image
COPY . .
