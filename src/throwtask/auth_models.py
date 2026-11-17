from typing import Any
from uuid import UUID

from pydantic import Field, SecretStr

from .models import Model


class RegistrationStart(Model):
    invitation: SecretStr = Field(min_length=32, max_length=256)
    username: str = Field(min_length=3, max_length=32)


class PasskeyProof(Model):
    challengeID: UUID
    credential: dict[str, Any]


class RefreshCredential(Model):
    refreshToken: SecretStr = Field(min_length=32, max_length=256)


class RecoveryStart(Model):
    username: str = Field(min_length=3, max_length=32)
    recoveryCode: SecretStr = Field(min_length=32, max_length=256)
