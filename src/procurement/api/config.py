"""Validated runtime configuration for the procurement API process."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

from procurement.domain.identifiers import Environment

SERVICE_NAME = "procurement-api"
_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Small, typed configuration surface for the T03 API process."""

    environment: Environment = Environment.DEV
    log_level: int = logging.INFO
    service_name: str = SERVICE_NAME

    def __post_init__(self) -> None:
        if not isinstance(self.environment, Environment):
            raise ValueError("environment must be dev or prod")
        if self.log_level not in _LOG_LEVELS.values():
            raise ValueError("log_level must be a supported logging level")
        if self.service_name != SERVICE_NAME:
            raise ValueError(f"service_name must be {SERVICE_NAME}")

    @classmethod
    def from_environment(
        cls,
        environment_variables: Mapping[str, str] | None = None,
    ) -> ApiSettings:
        """Load bounded settings from process environment variables."""

        values = os.environ if environment_variables is None else environment_variables
        raw_environment = values.get("PROCUREMENT_ENVIRONMENT", Environment.DEV.value)
        raw_log_level = values.get("PROCUREMENT_LOG_LEVEL", "INFO").upper()
        try:
            environment = Environment(raw_environment)
        except ValueError as error:
            raise ValueError("PROCUREMENT_ENVIRONMENT must be dev or prod") from error
        try:
            log_level = _LOG_LEVELS[raw_log_level]
        except KeyError as error:
            raise ValueError(
                "PROCUREMENT_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            ) from error
        return cls(environment=environment, log_level=log_level)
