from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://portfolio:portfolio@db:5432/portfolio"
    # Separate low-privilege login used by the agent's SQL tool (see infra/postgres).
    agent_database_url: str = "postgresql+psycopg://agent_ro:agent_ro@db:5432/portfolio"

    kite_api_key: str = ""
    kite_api_secret: str = ""

    anthropic_api_key: str = ""
    agent_model: str = "claude-opus-5-5"
    agent_effort: str = "high"
    # Analyse pipeline (see services/analysis). Research agents do the reading-heavy work, so they
    # default to Sonnet; the strategy and risk agents make the decision and check it on Opus.
    research_model: str = "claude-sonnet-5-5"
    research_effort: str = "medium"
    research_timeout_s: int = 300
    strategy_model: str = "claude-opus-5-5"
    strategy_timeout_s: int = 420
    risk_model: str = "claude-opus-5-5"
    analysis_budget_usd: float = 4.0  # stop a run once its estimated API cost passes this
    max_concurrent_analyses: int = 2
    # Hard limits the risk agent enforces on "add" verdicts for stocks (percent of portfolio).
    risk_max_position_pct: float = 15.0
    risk_max_sector_pct: float = 35.0

    # Browser-facing URL of the web app; used to redirect back after Kite login.
    web_url: str = "http://localhost:3000"
    # Host names the API answers to: localhost for direct calls, "api" for the web app's proxy
    # in Docker Compose. Requests with any other Host header are refused (DNS rebinding).
    api_allowed_hosts: list[str] = ["localhost", "127.0.0.1", "::1", "api"]

    timezone: str = "Asia/Kolkata"
    # Cron (minute hour day month day_of_week) for the daily job, in `timezone`.
    daily_job_cron: str = "30 16 * * mon-fri"
    auto_snapshot: bool = True
    auto_brief: bool = True

    # Slack Incoming Webhook for finished analyses and daily briefs (empty = disabled).
    slack_webhook_url: str = ""
    slack_notify_analyses: bool = True  # Analyse runs from the holdings table
    slack_notify_chat: bool = True  # answers to questions on the Analyst page and follow-ups
    slack_notify_briefs: bool = True
    # Model that condenses long reports into the Slack digest.
    slack_digest_model: str = "claude-sonnet-5-5"
    # Mask the investor's own figures ({{...}}-tagged by the analyst) in Slack messages.
    slack_hide_amounts: bool = True

    @property
    def kite_configured(self) -> bool:
        return bool(self.kite_api_key and self.kite_api_secret)

    @property
    def agent_configured(self) -> bool:
        return bool(self.anthropic_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
