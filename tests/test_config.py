import pytest

from network_probe.core.config import Settings

BASE = dict(
    DATABASE_URL="postgresql+psycopg://u:p@localhost/db",
    JWT_SECRET="x" * 32,
    FERNET_KEYS="",
    MEMBER_ID_PEPPER="p" * 32,
    APP_ENV="dev",
)


def _mk(env, monkeypatch):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings()


def test_dev_allows_empty_fernet(monkeypatch):
    s = _mk(BASE, monkeypatch)
    assert s.app_env == "dev"


def test_prod_requires_valid_fernet(monkeypatch):
    from cryptography.fernet import Fernet

    env = {**BASE, "APP_ENV": "prod", "FERNET_KEYS": ""}
    with pytest.raises(ValueError):
        _mk(env, monkeypatch)
    env2 = {**BASE, "APP_ENV": "prod", "FERNET_KEYS": Fernet.generate_key().decode(), "MEMBER_ID_PEPPER": "s" * 32}
    _mk(env2, monkeypatch)  # no raise


def test_prod_rejects_default_pepper(monkeypatch):
    from cryptography.fernet import Fernet

    env = {
        **BASE,
        "APP_ENV": "prod",
        "FERNET_KEYS": Fernet.generate_key().decode(),
        "MEMBER_ID_PEPPER": "dev-only-pepper-change-me-to-32plus-bytes",
    }
    with pytest.raises(ValueError):
        _mk(env, monkeypatch)


def test_cors_origins_plain_comma_and_json(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("CORS_ORIGINS", "http://a.com, http://b.com")
    assert Settings().cors_origins == ["http://a.com", "http://b.com"]
    monkeypatch.setenv("CORS_ORIGINS", '["http://c.com"]')
    assert Settings().cors_origins == ["http://c.com"]
    monkeypatch.setenv("CORS_ORIGINS", "http://only.com")
    assert Settings().cors_origins == ["http://only.com"]


def test_fernet_key_list_strips_and_filters(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@l/db")
    monkeypatch.setenv("JWT_SECRET", "x" * 32)
    monkeypatch.setenv("FERNET_KEYS", "k1 , k2,")
    assert Settings().fernet_key_list == ["k1", "k2"]


def test_effective_app_db_url_fallback(monkeypatch):
    # Bypass both the local .env and any env-var (e.g. set by conftest) so the
    # fallback to database_url is deterministic regardless of the test environment.
    monkeypatch.delenv("APP_DB_URL", raising=False)
    s = Settings(_env_file=None, database_url="postgresql+psycopg://owner@l/db", jwt_secret="x" * 32)
    assert s.app_db_url is None and s.effective_app_db_url == "postgresql+psycopg://owner@l/db"
