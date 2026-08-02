"""Typed identifiers shared by the procurement domain."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from procurement.domain.errors import DomainValidationError, FieldError

MAX_IDENTIFIER_LENGTH = 128
MAX_REVISION = 9_223_372_036_854_775_807
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", re.ASCII)


class Environment(StrEnum):
    """Deployment environment that owns a domain resource."""

    DEV = "dev"
    PROD = "prod"


@dataclass(frozen=True, slots=True)
class EnvironmentBoundIdentifier:
    """Opaque identifier whose value is meaningful in exactly one environment."""

    environment: Environment
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise DomainValidationError(
                "The environment is invalid.",
                field_errors=(
                    FieldError(
                        field="environment",
                        message="Environment must be dev or prod.",
                    ),
                ),
            )

        if (
            not isinstance(self.value, str)
            or not 1 <= len(self.value) <= MAX_IDENTIFIER_LENGTH
            or _IDENTIFIER_PATTERN.fullmatch(self.value) is None
        ):
            raise DomainValidationError(
                "The identifier is invalid.",
                field_errors=(
                    FieldError(
                        field="identifier",
                        message=("Identifier must be 1 to 128 safe ASCII characters."),
                    ),
                ),
            )

    def __str__(self) -> str:
        return self.value


class CaseId(EnvironmentBoundIdentifier):
    """Stable identifier for one procurement case."""

    __slots__ = ()


class EvidenceId(EnvironmentBoundIdentifier):
    """Stable identifier for one captured procurement evidence record."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Revision:
    """Positive, increasing version used for optimistic concurrency."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 1 <= self.value <= MAX_REVISION:
            raise DomainValidationError(
                "The revision is invalid.",
                field_errors=(
                    FieldError(
                        field="revision",
                        message="Revision must be a positive 64-bit integer.",
                    ),
                ),
            )

    def next(self) -> Revision:
        """Return the next revision, rejecting integer overflow."""

        if self.value == MAX_REVISION:
            raise DomainValidationError(
                "The revision cannot be increased.",
                field_errors=(
                    FieldError(
                        field="revision",
                        message="Revision has reached its maximum value.",
                    ),
                ),
            )
        return Revision(self.value + 1)
