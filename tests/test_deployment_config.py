from database.config import get_database_url


def test_hosted_database_url_takes_precedence(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://hosted.example/sentinelai")
    monkeypatch.setenv("POSTGRES_HOST", "local-only")

    assert get_database_url() == "postgresql://hosted.example/sentinelai"


def test_component_database_settings_remain_local_fallback(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_DB", "sentinelai")
    monkeypatch.setenv("POSTGRES_USER", "sentinelai")
    monkeypatch.setenv("POSTGRES_PASSWORD", "sentinelai")

    assert get_database_url() == "postgresql://sentinelai:sentinelai@postgres:5432/sentinelai"