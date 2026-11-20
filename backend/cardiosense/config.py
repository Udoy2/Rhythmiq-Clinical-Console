"""Application settings (environment prefix ``CARDIOSENSE_``, optional ``backend/.env``)."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

DISCLAIMER = "Research and demonstration system — not a medical device. Not an emergency service."

ROLES = ("patient", "technician", "clinician", "org_admin", "system_admin")
TIERS = ("green", "amber", "orange", "red")
TIER_RANK = {t: i for i, t in enumerate(TIERS)}
BEAT_LABELS = ("N", "S", "V", "F", "Unknown")
ALERT_STATES = ("open", "acknowledged", "escalated", "closed")
DEVICE_TASK_RULES = ("LEAD_OFF", "LOW_SQI")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CARDIOSENSE_", env_file=str(BACKEND_DIR / ".env"), extra="ignore"
    )

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://cardiosense:cardiosense@localhost:5432/cardiosense"
    db_pool_size: int = 10

    # --- auth ---
    jwt_secret: str = "dev-only-change-me-cardiosense-jwt-secret-0123456789"
    access_ttl_min: int = 15
    refresh_ttl_days: int = 14
    device_key_pepper: str = "dev-only-device-key-pepper"
    pairing_code_ttl_min: int = 10
    default_org_name: str = "CardioSense Demo Clinic"

    # --- seed / demo ---
    seed: bool = True
    seed_history_days: int = 14
    seed_soak_devices: int = 0  # create dev-soak-000..N-1 on startup (load tests)
    seed_password: str = "CardioSense!23"
    data_dir: Path = REPO_ROOT / "data" / "records"
    sender_python: str = sys.executable
    sender_script: Path = REPO_ROOT / "sender" / "stream_sender.py"
    self_ws_url: str = "ws://localhost:8000"

    # --- pipeline ---
    model_dir: Path = REPO_ROOT / "ml" / "models" / "cardiosense_beat_model"
    pipeline_threads: int = 4
    torch_threads: int = 1
    signal_chunk_s: float = 1.0
    hello_timeout_s: float = 10.0
    stream_idle_timeout_s: float = 30.0

    # --- web ---
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # --- copilot (cited Insights; Anthropic) ---
    anthropic_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "CARDIOSENSE_ANTHROPIC_API_KEY")
    )
    llm_model: str = "claude-opus-5-5"
    llm_timeout_s: float = 20.0
    llm_enabled: bool = True

    # --- Dr. Rhidoy guide (OpenAI-compatible; Groq now, DeepSeek later via env swap) ---
    llm_base_url: str = Field(
        default="https://api.groq.com/openai/v1",
        validation_alias=AliasChoices("LLM_BASE_URL", "GROQ_BASE_URL", "CARDIOSENSE_LLM_BASE_URL"),
    )
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("LLM_API_KEY", "GROQ_API_KEY", "CARDIOSENSE_LLM_API_KEY"),
    )
    assistant_llm_model: str = Field(
        default="openai/gpt-oss-120b",
        validation_alias=AliasChoices("LLM_MODEL", "GROQ_MODEL", "CARDIOSENSE_ASSISTANT_LLM_MODEL"),
    )
    assistant_llm_timeout_s: float = 60.0

    # --- notifications ---
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "alerts@cardiosense.dev"
    smtp_starttls: bool = True
    alert_webhook_url: str | None = None
    notify_max_attempts: int = 3
    notify_backoff_s: float = 2.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
