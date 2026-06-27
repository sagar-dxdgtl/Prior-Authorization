from __future__ import annotations

import os
from typing import Protocol


class SecretsProvider(Protocol):
    def get_secret(self, name: str) -> str | None: ...


class EnvSecrets:
    def get_secret(self, name: str) -> str | None:
        return os.environ.get(name)


class AwsSecrets:
    """Reads from AWS Secrets Manager under prefix preauth/. Used only when AWS creds present.

    Pass ``client`` to inject a fake/stub Secrets Manager client for unit tests so no real
    boto3 session or AWS credentials are needed.
    """

    def __init__(self, prefix: str = "preauth/", region: str | None = None, *, client=None):
        if client is not None:
            self._c = client
        else:
            import boto3

            self._c = boto3.client("secretsmanager", region_name=region or os.environ.get("AWS_DEFAULT_REGION"))
        self._prefix = prefix

    def get_secret(self, name: str) -> str | None:
        try:
            return self._c.get_secret_value(SecretId=self._prefix + name)["SecretString"]
        except Exception:
            return None


def _provider() -> SecretsProvider:
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        try:
            return AwsSecrets()
        except Exception:
            return EnvSecrets()
    return EnvSecrets()


def get_secret(name: str) -> str | None:
    """Env wins for local dev even with AWS configured (so .env overrides cleanly)."""
    return os.environ.get(name) or _provider().get_secret(name)
