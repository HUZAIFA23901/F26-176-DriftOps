"""Tests for DriftOps configuration module."""

import pytest
from pydantic import ValidationError
from src.config import Settings, get_settings


def test_default_settings():
    """Verify that default settings instantiate with expected values."""
    settings = Settings()
    assert settings.POSTGRES_HOST == "localhost"
    assert settings.POSTGRES_PORT == 5432
    assert settings.POSTGRES_USER == "postgres"
    assert settings.POSTGRES_PASSWORD == "postgres"
    assert settings.POSTGRES_DB == "driftops_db"
    assert settings.DOCKER_SOCKET == "unix:///var/run/docker.sock"
    assert settings.SANDBOX_POSTGRES_IMAGE == "postgres:15-alpine"
    assert settings.HARD_GATE_C_AST_FLOOR == 0.80
    assert settings.TIER_1_MAX_RISK == 0.20
    assert settings.TIER_2_MAX_RISK == 0.60
    assert settings.GITHUB_TOKEN == "mock_token"
    assert settings.LOG_LEVEL == "INFO"


def test_postgres_uri_property():
    """Verify the PostgreSQL URI property formatting."""
    settings = Settings(
        POSTGRES_HOST="pg.internal",
        POSTGRES_PORT=5433,
        POSTGRES_USER="db_user",
        POSTGRES_PASSWORD="secure_pass",
        POSTGRES_DB="analytics",
    )
    expected_uri = "postgresql://db_user:secure_pass@pg.internal:5433/analytics"
    assert settings.postgres_uri == expected_uri


def test_log_level_validation():
    """Verify that invalid log levels raise a ValidationError."""
    with pytest.raises(ValidationError):
        Settings(LOG_LEVEL="INVALID_LEVEL")


def test_tier_order_validation():
    """Verify that TIER_2_MAX_RISK must be >= TIER_1_MAX_RISK."""
    with pytest.raises(ValidationError):
        Settings(TIER_1_MAX_RISK=0.50, TIER_2_MAX_RISK=0.30)


def test_threshold_bounds():
    """Verify threshold boundary validation (0.0 to 1.0)."""
    with pytest.raises(ValidationError):
        Settings(HARD_GATE_C_AST_FLOOR=1.5)

    with pytest.raises(ValidationError):
        Settings(TIER_1_MAX_RISK=-0.1)


def test_singleton_get_settings():
    """Verify that get_settings returns a cached instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
