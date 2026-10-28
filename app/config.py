from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Настройки загружаются из окружения и локального файла .env."""

    database_url: str
    session_secret: SecretStr = Field(min_length=32)
    session_https_only: bool = False
    allowed_hosts: str = "localhost,127.0.0.1"

    admin_username: str = "admin"
    admin_password: SecretStr
    storekeeper_username: str = "storekeeper"
    storekeeper_password: SecretStr
    viewer_username: str = "viewer"
    viewer_password: SecretStr

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def allowed_hosts_list(self) -> list[str]:
        """Преобразует строку узлов в список для TrustedHostMiddleware."""

        return [item.strip() for item in self.allowed_hosts.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """Возвращает единый неизменяемый набор настроек процесса."""

    return Settings()

