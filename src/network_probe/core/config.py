from __future__ import annotations

import json
from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV = {"dev", "test", "local"}
_DEFAULT_PEPPERS = {"dev-pepper", "dev-only-pepper-change-me-to-32plus-bytes"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    app_env: str = "dev"
    database_url: str
    app_db_url: str | None = None
    jwt_secret: str
    jwt_access_ttl: int = 1800
    jwt_refresh_ttl: int = 1209600
    quota_daily: int = 1000
    quota_monthly: int = 20000
    fernet_keys: str = ""
    fernet_keys_kms: bool = False
    member_id_pepper: str = "dev-pepper"
    stedi_api_key: str | None = None
    stedi_eligibility_url: str = "https://healthcare.us.stedi.com/2024-04-01/change/medicalnetwork/eligibility/v3"
    # Anthem / Elevance provider-directory FHIR (OAuth2 client-credentials). The same CMS-mandated
    # PDEX Plan-Net API as the public payers, but token-gated — creds come from the Elevance portal.
    anthem_fhir_base_url: str | None = None
    anthem_fhir_token_url: str | None = None
    anthem_fhir_client_id: str | None = None
    anthem_fhir_client_secret: str | None = None
    anthem_fhir_scope: str | None = None
    # Kept as a raw str (not list[str]) so pydantic-settings does not attempt to
    # JSON-decode it at the source level. Plain, comma-separated, and JSON-array
    # forms are all supported via the `cors_origins` property below.
    cors_origins_raw: str = Field(
        default="http://localhost:5173",
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins_raw"),
    )
    aws_default_region: str = "us-east-1"

    @field_validator("app_env", mode="before")
    @classmethod
    def _norm_env(cls, v):
        return v.strip().lower() if isinstance(v, str) else v

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret_length(cls, v):
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be >= 32 chars")
        return v

    @property
    def cors_origins(self) -> list[str]:
        raw = (self.cors_origins_raw or "").strip()
        if not raw:
            return []
        if raw.startswith("["):
            return [str(x) for x in json.loads(raw)]
        return [x.strip() for x in raw.split(",") if x.strip()]

    @property
    def anthem_fhir_ready(self) -> bool:
        """True only when every credential needed to call the Anthem OAuth2 FHIR directory is set."""
        return bool(
            self.anthem_fhir_base_url
            and self.anthem_fhir_token_url
            and self.anthem_fhir_client_id
            and self.anthem_fhir_client_secret
        )

    @property
    def fernet_key_list(self) -> list[str]:
        return [k.strip() for k in self.fernet_keys.split(",") if k.strip()]

    @property
    def effective_app_db_url(self) -> str:
        return self.app_db_url or self.database_url

    @model_validator(mode="after")
    def _phi_crypto_required_outside_dev(self):
        if self.app_env in _DEV:
            return self
        keys = self.fernet_key_list
        if not keys:
            raise ValueError("FERNET_KEYS required outside dev")
        for k in keys:
            Fernet(k.encode())
        if len(self.member_id_pepper) < 32 or self.member_id_pepper in _DEFAULT_PEPPERS:
            raise ValueError("MEMBER_ID_PEPPER must be strong and non-default outside dev")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
