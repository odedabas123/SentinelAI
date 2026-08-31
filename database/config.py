# Database configuration for SentinelAI.
#
# Docker Compose passes these values into the application containers, while
# local development can override them through environment variables.

import os


def get_database_url():
    """Use a hosted URL when provided, otherwise build the local URL."""
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url

    db_host = os.getenv("POSTGRES_HOST", "localhost")
    db_port = os.getenv("POSTGRES_PORT", "5432")
    db_name = os.getenv("POSTGRES_DB", "sentinelai")
    db_user = os.getenv("POSTGRES_USER", "sentinelai")
    db_password = os.getenv("POSTGRES_PASSWORD", "sentinelai")

    return (
        f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"
    )
