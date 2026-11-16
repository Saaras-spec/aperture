"""Unit tests for config module."""

from pathlib import Path

import pytest

from aperture.config import (
    Config,
    ConfigError,
    RuleConfig,
    load_config,
    load_config_from_yaml,
)
from aperture.models import Severity


def test_default_config() -> None:
    """Verify default configuration values."""
    config = Config()
    assert config.fail_on == Severity.ERROR
    assert "migrations/**/*.sql" in config.migration_paths
    assert config.large_tables == []
    assert config.rules == {}
    assert config.is_rule_enabled("MG001") is True
    assert config.get_rule_severity("MG001", Severity.ERROR) == Severity.ERROR


def test_load_valid_yaml() -> None:
    """Verify loading valid YAML string with rule overrides."""
    yaml_text = """
    fail_on: WARNING
    migration_paths:
      - "db/migrate/*.sql"
    large_tables:
      - users
      - payments
    rules:
      MG001:
        enabled: false
      MG002:
        severity: ERROR
    """
    config = load_config_from_yaml(yaml_text)
    assert config.fail_on == Severity.WARNING
    assert config.migration_paths == ["db/migrate/*.sql"]
    assert config.large_tables == ["users", "payments"]
    assert config.is_rule_enabled("MG001") is False
    assert config.is_rule_enabled("MG002") is True
    assert config.get_rule_severity("MG002", Severity.WARNING) == Severity.ERROR
    assert config.get_rule_severity("MG003", Severity.ERROR) == Severity.ERROR


def test_load_empty_yaml() -> None:
    """Verify empty YAML returns default config."""
    config = load_config_from_yaml("")
    assert config.fail_on == Severity.ERROR


def test_load_malformed_yaml() -> None:
    """Verify syntax error in YAML raises ConfigError."""
    with pytest.raises(ConfigError, match="Malformed YAML"):
        load_config_from_yaml("fail_on: [unclosed list")


def test_load_non_dict_yaml() -> None:
    """Verify YAML that is a scalar or list raises ConfigError."""
    with pytest.raises(ConfigError, match="Root must be a mapping/dictionary"):
        load_config_from_yaml("- item 1\n- item 2")


def test_invalid_fail_on_severity() -> None:
    """Verify invalid fail_on severity raises ConfigError naming field."""
    bad_yaml = "fail_on: INVALID_LEVEL"
    with pytest.raises(ConfigError) as exc_info:
        load_config_from_yaml(bad_yaml)
    assert "fail_on" in str(exc_info.value)
    assert "Invalid fail_on severity" in str(exc_info.value)


def test_invalid_rule_severity() -> None:
    """Verify invalid rule severity raises ConfigError naming field."""
    bad_yaml = """
    rules:
      MG001:
        severity: NOT_A_SEVERITY
    """
    with pytest.raises(ConfigError) as exc_info:
        load_config_from_yaml(bad_yaml)
    assert "rules.MG001.severity" in str(exc_info.value)


def test_extra_forbidden_field() -> None:
    """Verify extra unknown fields raise ConfigError naming the bad field."""
    bad_yaml = "unknown_top_level_key: true"
    with pytest.raises(ConfigError) as exc_info:
        load_config_from_yaml(bad_yaml)
    assert "unknown_top_level_key" in str(exc_info.value)


def test_load_config_from_file(tmp_path: Path) -> None:
    """Verify load_config reads an explicit file path."""
    cfg_file = tmp_path / "custom.yml"
    cfg_file.write_text("fail_on: INFO\n", encoding="utf-8")
    config = load_config(cfg_file)
    assert config.fail_on == Severity.INFO


def test_load_config_file_not_found(tmp_path: Path) -> None:
    """Verify load_config raises ConfigError if explicit file is missing."""
    with pytest.raises(ConfigError, match="Configuration file not found"):
        load_config(tmp_path / "nonexistent.yml")


def test_load_config_default_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify load_config falls back to default config if no file exists in cwd."""
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.fail_on == Severity.ERROR


def test_load_config_discovers_in_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify load_config automatically discovers aperture.yml in cwd."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "aperture.yml").write_text("fail_on: WARNING\n", encoding="utf-8")
    config = load_config()
    assert config.fail_on == Severity.WARNING


def test_rule_config_model() -> None:
    """Verify RuleConfig handles None severity cleanly."""
    rc = RuleConfig(enabled=True)
    assert rc.severity is None
    assert rc.enabled is True
