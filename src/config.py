import os
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Carrega .env apenas em dev/local. No Cloud Run as VARs já vêm do serviço.
load_dotenv()

TRUE_SET = {"sim", "yes", "true", "1"}


class Settings(BaseModel):
    # --- Gemini / IA ---
    gemini_api_key: str = Field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = Field(
        default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
    )
    gemini_temperature: float = Field(
        default_factory=lambda: float(os.getenv("GEMINI_TEMPERATURE", "0.2"))
    )
    gemini_top_p: float = Field(
        default_factory=lambda: float(os.getenv("GEMINI_TOP_P", "0.9"))
    )
    # single | manager_critic
    refine_mode: str = Field(
        default_factory=lambda: os.getenv("REFINE_MODE", "manager_critic")
    )
    # limite de caracteres para a resposta final (0 = sem corte)
    refine_max_chars: int = Field(
        default_factory=lambda: int(os.getenv("REFINE_MAX_CHARS", "0"))
    )

    # --- App / Robô ---
    douke_url: str = Field(
        default_factory=lambda: os.getenv(
            "DOUKE_URL", "https://web.duoke.com/?lang=en#/dk/main/chat"
        )
    )
    max_conversations: int = Field(
        default_factory=lambda: int(os.getenv("MAX_CONVERSATIONS", "50"))
    )
    history_depth: int = Field(
        default_factory=lambda: int(os.getenv("HISTORY_DEPTH", "20"))
    )
    apply_needs_reply_filter: bool = Field(
        default_factory=lambda: os.getenv("APPLY_NEEDS_REPLY_FILTER", "nao").lower()
        in TRUE_SET
    )
    loop_interval: int = Field(
        default_factory=lambda: int(os.getenv("LOOP_INTERVAL", "10"))
    )
    delay_after_nav: float = Field(
        default_factory=lambda: float(os.getenv("DELAY_AFTER_NAV", "1"))
    )
    delay_between_actions: float = Field(
        default_factory=lambda: float(os.getenv("DELAY_BETWEEN_ACTIONS", "0.1"))
    )
    goto_timeout_ms: int = Field(
        default_factory=lambda: int(os.getenv("GOTO_TIMEOUT_MS", "60000"))
    )


    # --- Cloud Run / Servidor ---
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    host: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))


settings = Settings()
