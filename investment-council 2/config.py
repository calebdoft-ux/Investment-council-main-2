from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    ANTHROPIC_API_KEY: str
    MODEL: str = "claude-sonnet-4-6"
    DATABASE_PATH: str = "investment_council.db"
    MAX_TOKENS: int = 1500
    HOST: str = "0.0.0.0"
    PORT: int = 8000


settings = Settings()
