"""
Application settings using Pydantic-Settings.

Pydantic-Settings reads from environment variables and .env files,
validates types, and provides IDE-friendly access. This is the single
source of truth for all runtime configuration.

Precedence (highest to lowest):
  1. Environment variables (UPPERCASE)
  2. .env.local file
  3. .env file
  4. Defaults defined here
"""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── API ──────────────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_debug: bool = False
    api_title: str = "Mini HFT System"
    api_version: str = "0.1.0"

    # Comma-separated origins for CORS. JSON list also accepted.
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:3001"]
    )

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://hft:hft@localhost:5432/hft"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    persistence_enabled: bool = True
    persistence_batch_size: int = 250
    persistence_flush_interval_ms: int = 25
    persistence_max_retries: int = 3
    persistence_retry_backoff_ms: int = 20

    # ── Engine ───────────────────────────────────────────────────────────────
    matching_backend: str = "python"
    event_queue_depth: int = 10_000

    # ── Market Data ──────────────────────────────────────────────────────────
    # Tick interval in milliseconds. Lower = more CPU, richer real-time feed.
    market_data_tick_interval_ms: int = 100   # 10 Hz default

    @property
    def market_data_tick_interval(self) -> float:
        return self.market_data_tick_interval_ms / 1_000.0

    # ── WebSocket ────────────────────────────────────────────────────────────
    websocket_heartbeat_interval_s: int = 30

    # ── Logging ──────────────────────────────────────────────────────────────
    log_level: str = "INFO"


# Module-level singleton — import this everywhere
settings = Settings()
