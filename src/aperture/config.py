"""Configuration models and loader for Aperture using Pydantic v2 and PyYAML."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

from aperture.models import Severity


class ConfigError(Exception):
    """Raised when configuration validation or loading fails."""


class RuleConfig(BaseModel):
    """Configuration for an individual rule."""

    enabled: bool = True
    severity: Severity | None = None

    model_config = {"extra": "forbid"}

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: Any) -> Any:
        """Validate severity string against Severity enum."""
        if v is None:
            return None
        if isinstance(v, str):
            v_upper = v.strip().upper()
            try:
                return Severity(v_upper)
            except ValueError:
                raise ValueError(
                    f"Invalid severity '{v}'. Must be one of: "
                    f"{', '.join(s.value for s in Severity)}"
                ) from None
        return v


class Config(BaseModel):
    """Top-level configuration for Aperture."""

    fail_on: Severity = Severity.ERROR
    migration_paths: list[str] = Field(
        default_factory=lambda: ["migrations/**/*.sql", "migrations/*.sql"]
    )
    large_tables: list[str] = Field(default_factory=list)
    rules: dict[str, RuleConfig] = Field(default_factory=dict)
    explain_provider: str | None = None
    explain_model: str | None = None
    explain_max_findings: int = 10
    explain_timeout_seconds: float = 10.0

    model_config = {"extra": "forbid"}

    @field_validator("explain_provider", mode="before")
    @classmethod
    def validate_explain_provider(cls, v: Any) -> Any:
        """Validate explain_provider string against supported providers."""
        if v is None:
            return None
        if isinstance(v, str):
            v_lower = v.strip().lower()
            if v_lower not in {"anthropic", "gemini"}:
                raise ValueError(
                    f"Invalid explain_provider '{v}'. Must be one of: 'anthropic', 'gemini'"
                )
            return v_lower
        return v

    @field_validator("fail_on", mode="before")
    @classmethod
    def validate_fail_on(cls, v: Any) -> Any:
        """Validate fail_on string against Severity enum."""
        if isinstance(v, str):
            v_upper = v.strip().upper()
            try:
                return Severity(v_upper)
            except ValueError:
                raise ValueError(
                    f"Invalid fail_on severity '{v}'. Must be one of: "
                    f"{', '.join(s.value for s in Severity)}"
                ) from None
        return v

    def is_rule_enabled(self, rule_id: str) -> bool:
        """Check if a rule is enabled in the configuration."""
        if rule_id in self.rules:
            return self.rules[rule_id].enabled
        return True

    def get_rule_severity(self, rule_id: str, default: Severity) -> Severity:
        """Get effective severity for a rule, taking overrides into account."""
        if rule_id in self.rules and self.rules[rule_id].severity is not None:
            sev = self.rules[rule_id].severity
            assert sev is not None  # type narrowing
            return sev
        return default


def _format_validation_error(err: ValidationError) -> str:
    """Format Pydantic ValidationError into a user-friendly error message naming bad fields."""
    messages: list[str] = []
    for error in err.errors():
        loc = ".".join(str(p) for p in error["loc"])
        msg = error["msg"]
        messages.append(f"Field '{loc}': {msg}")
    return "\n".join(messages)


def load_config_from_yaml(yaml_text: str, source_desc: str = "<string>") -> Config:
    """Parse and validate YAML configuration text.

    Args:
        yaml_text: Raw YAML text.
        source_desc: Source description for error reporting.

    Returns:
        Validated Config instance.

    Raises:
        ConfigError: If YAML is malformed or configuration is invalid.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        raise ConfigError(f"Malformed YAML in {source_desc}: {e}") from e

    if data is None:
        return Config()

    if not isinstance(data, dict):
        raise ConfigError(
            f"Invalid configuration in {source_desc}: Root must be a mapping/dictionary, "
            f"got {type(data).__name__}"
        )

    try:
        return Config.model_validate(data)
    except ValidationError as e:
        formatted = _format_validation_error(e)
        raise ConfigError(f"Configuration error in {source_desc}:\n{formatted}") from e


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from a file path or find aperture.yml in current directory.

    If path is None, looks for aperture.yml or aperture.yaml in cwd.
    If no config file is found, returns default configuration.

    Args:
        path: Optional explicit path to configuration file.

    Returns:
        Config instance.

    Raises:
        ConfigError: If explicit path not found or configuration is invalid.
    """
    if path is not None:
        file_path = Path(path)
        if not file_path.is_file():
            raise ConfigError(f"Configuration file not found: {file_path}")
        content = file_path.read_text(encoding="utf-8")
        return load_config_from_yaml(content, source_desc=str(file_path))

    # Look for default file names in current working directory
    for candidate in ("aperture.yml", "aperture.yaml"):
        p = Path(candidate)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            return load_config_from_yaml(content, source_desc=str(p))

    return Config()
