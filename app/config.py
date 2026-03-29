from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    bot_token: str
    database_url: str

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
