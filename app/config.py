"""Runtime configuration, read from environment variables (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass
class Settings:
    # --- Hindsight (memory) ---
    hindsight_base_url: str = field(default_factory=lambda: os.getenv("HINDSIGHT_BASE_URL", "https://api.hindsight.vectorize.io"))
    hindsight_api_key: str = field(default_factory=lambda: os.getenv("HINDSIGHT_API_KEY", ""))
    bank_id: str = field(default_factory=lambda: os.getenv("HINDSIGHT_BANK_ID", "dejavu-payments-desk"))

    # --- LLM (Groq, OpenAI-compatible) ---
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "openai/gpt-oss-120b"))
    llm_fallback_model: str = field(default_factory=lambda: os.getenv("LLM_FALLBACK_MODEL", "openai/gpt-oss-20b"))
    llm_reasoning_effort: str = field(default_factory=lambda: os.getenv("LLM_REASONING_EFFORT", "low"))
    # Groq free tier for gpt-oss-120b is 8K tokens/min and 30 requests/min. Stay under it.
    llm_tpm_limit: int = field(default_factory=lambda: _int("LLM_TPM_LIMIT", 7000))
    llm_rpm_limit: int = field(default_factory=lambda: _int("LLM_RPM_LIMIT", 28))

    # --- Modes ---
    # "hindsight" (default) or "local" (offline stub used by tests / no-key dev only)
    memory_backend: str = field(default_factory=lambda: os.getenv("MEMORY_BACKEND", "hindsight"))
    # "groq" (default) or "offline" (deterministic heuristic stub used by tests / no-key dev only)
    llm_backend: str = field(default_factory=lambda: os.getenv("LLM_BACKEND", "groq"))
    show_demo_hints: bool = field(default_factory=lambda: _bool("SHOW_DEMO_HINTS", True))
    # Hosted copies start from an empty bank: load the 6 weeks of resolved history into it in the background.
    seed_history: bool = field(default_factory=lambda: _bool("SEED_HISTORY", False))

    # --- Paths ---
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", str(ROOT / "data"))))
    db_path: Path = field(default_factory=lambda: Path(os.getenv("DB_PATH", str(ROOT / "data" / "dejavu.db"))))

    # --- Recall tuning ---
    recall_max_tokens: int = field(default_factory=lambda: _int("RECALL_MAX_TOKENS", 1200))
    recall_budget: str = field(default_factory=lambda: os.getenv("RECALL_BUDGET", "mid"))

    def resolved_modes(self) -> tuple[str, str]:
        """Fall back to offline stubs automatically when keys are missing, and say so."""
        memory = self.memory_backend
        llm = self.llm_backend
        is_local_server = any(h in self.hindsight_base_url for h in ("localhost", "127.0.0.1"))
        if memory == "hindsight" and not self.hindsight_api_key and not is_local_server:
            memory = "local"
        if llm == "groq" and not self.groq_api_key:
            llm = "offline"
        return memory, llm


settings = Settings()
