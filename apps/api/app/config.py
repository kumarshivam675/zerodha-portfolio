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

    # Browser-facing URL of the web app; used to redirect back after Kite login.
    web_url: str = "http://localhost:3000"

    timezone: str = "Asia/Kolkata"
    # Cron (minute hour day month day_of_week) for the daily job, in `timezone`.
    daily_job_cron: str = "30 16 * * mon-fri"
    auto_snapshot: bool = True
    auto_brief: bool = True

    # Slack Incoming Webhook for finished analyses and daily briefs (empty = disabled).
    slack_webhook_url: str = ""
    slack_notify_analyses: bool = True
    slack_notify_briefs: bool = True
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
