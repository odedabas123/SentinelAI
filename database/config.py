# Database configuration for SentinelAI.
#
# Docker Compose passes these values into the application containers, while
# local development can override them through environment variables.

import os


DB_HOST = os.getenv("POSTGRES_HOST", "localhost")
DB_PORT = os.getenv("POSTGRES_PORT", "5432")
DB_NAME = os.getenv("POSTGRES_DB", "sentinelai")
DB_USER = os.getenv("POSTGRES_USER", "sentinelai")
DB_PASSWORD = os.getenv("POSTGRES_PASSWORD", "sentinelai")


def get_database_url():
    """Build the PostgreSQL connection string used by the app."""
    return (
        f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
