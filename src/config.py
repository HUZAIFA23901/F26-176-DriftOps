"""DriftOps configuration module.

Provides strongly typed configuration management parsed from environment variables
and .env files using pydantic-settings.

Project ID: F26-176-DriftOps
"""

from functools import lru_cache
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration and gate thresholds for DriftOps."""

    # PostgreSQL Connection
    POSTGRES_HOST: str = Field(
        default="localhost",
        description="PostgreSQL hostname or IP address",
    )
    POSTGRES_PORT: int = Field(
        default=5432,
        ge=1,
        le=65535,
        description="PostgreSQL port number",
    )
    POSTGRES_USER: str = Field(
        default="postgres",
        description="PostgreSQL user",
    )
    POSTGRES_PASSWORD: str = Field(
        default="postgres",
        description="PostgreSQL password",
    )
    POSTGRES_DB: str = Field(
        default="driftops_db",
        description="PostgreSQL database name",
    )

    # Docker Sandbox Settings
    DOCKER_SOCKET: str = Field(
        default="unix:///var/run/docker.sock",
        description="Docker daemon socket URI or Windows named pipe",
    )
    SANDBOX_POSTGRES_IMAGE: str = Field(
        default="postgres:15-alpine",
        description="Base Docker image for ephemeral sandbox PostgreSQL instances",
    )

    # Risk Gate Thresholds
    HARD_GATE_C_AST_FLOOR: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Minimum structural AST similarity score (C_AST) required to pass the hard gate",
    )
    TIER_1_MAX_RISK: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Upper risk threshold for autonomous Tier 1 inline repairs",
    )
    TIER_2_MAX_RISK: float = Field(
        default=0.60,
        ge=0.0,
        le=1.0,
        description="Upper risk threshold for bounded Tier 2 candidate repairs",
    )

    # GitHub & App Settings
    GITHUB_TOKEN: str = Field(
        default="mock_token",
        description="GitHub personal access or app token for status checks and PR comments",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Application logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )

    @field_validator("LOG_LEVEL")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper_v = v.upper()
        if upper_v not in valid_levels:
            raise ValueError(f"Invalid LOG_LEVEL '{v}'. Must be one of {valid_levels}")
        return upper_v

    @field_validator("TIER_2_MAX_RISK")
    @classmethod
    def validate_tier_order(cls, v: float, info) -> float:
        tier_1 = info.data.get("TIER_1_MAX_RISK", 0.20)
        if v < tier_1:
            raise ValueError(f"TIER_2_MAX_RISK ({v}) cannot be lower than TIER_1_MAX_RISK ({tier_1})")
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def postgres_uri(self) -> str:
        """Construct standard PostgreSQL connection URI."""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    """Retrieve cached singleton Settings instance."""
    return Settings()
