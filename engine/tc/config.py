"""Engine settings: non-secret config.yml + secrets from .env / environment.

Fail-fast by design (spec §9): a missing key raises at startup, never at the
moment an order or a re-auth needs it.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = "America/New_York"
    data_dir: Path
    http_bind: str = "127.0.0.1:8080"
    reserve_usd: Decimal = Decimal("900.00")


class TokenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reauth_after_days: int = Field(ge=1)
    hard_expiry_days: int = Field(ge=1)
    callback_url: AnyHttpUrl

    @model_validator(mode="after")
    def _ordered(self) -> TokenConfig:
        if self.reauth_after_days >= self.hard_expiry_days:
            raise ValueError("token.reauth_after_days must be < token.hard_expiry_days")
        return self


class FileConfig(BaseModel):
    """The whole of config.yml. Unknown keys are errors, not warnings."""

    model_config = ConfigDict(extra="forbid")
    engine: EngineConfig
    token: TokenConfig


class Settings(BaseSettings):
    """Secrets come only from the environment / .env, prefixed TC_."""

    model_config = SettingsConfigDict(env_prefix="TC_", extra="ignore")

    schwab_app_key: str
    schwab_app_secret: str
    discord_webhook_url: AnyHttpUrl
    healthchecks_base_url: AnyHttpUrl | None = None

    # populated from config.yml, not env
    engine: EngineConfig
    token: TokenConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top level must be a mapping")
    return data


def load_settings(config_path: Path, env_file: Path | None = None) -> Settings:
    file_cfg = FileConfig.model_validate(_read_yaml(config_path))
    kwargs: dict[str, Any] = {"engine": file_cfg.engine, "token": file_cfg.token}
    if env_file is not None:
        return Settings(_env_file=str(env_file), **kwargs)  # type: ignore[call-arg]
    return Settings(**kwargs)
