"""Tests for config.py configuration management."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from parsehealthlog.config import (
    Config,
    ProfileConfig,
    get_config_dir,
    get_env_file,
    get_profiles_dir,
)
from parsehealthlog.exceptions import ConfigurationError


def _profile(**overrides):
    """Create a minimal valid ProfileConfig."""
    defaults = {
        "name": "test",
        "health_log_path": Path("/path/to/log.md"),
        "output_path": Path("/path/to/output"),
    }
    defaults.update(overrides)
    return ProfileConfig(**defaults)


class TestConfigFromProfile:
    """Tests for Config.from_profile() method."""

    def test_missing_openrouter_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
            Config.from_profile(_profile())

    def test_missing_health_log_path_raises(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with pytest.raises(ConfigurationError, match="health_log_path"):
                Config.from_profile(_profile(health_log_path=None))

    def test_missing_output_path_raises(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with pytest.raises(ConfigurationError, match="output_path"):
                Config.from_profile(_profile(output_path=None))

    def test_valid_minimal_config(self):
        with patch.dict(
            os.environ,
            {"OPENROUTER_API_KEY": "test-key", "MODEL_ID": "test-model"},
        ):
            config = Config.from_profile(_profile())
        assert config.api_key == "test-key"
        assert config.model_id == "test-model"
        assert config.health_log_path == Path("/path/to/log.md")
        assert config.output_path == Path("/path/to/output")

    def test_default_base_url(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile())
        assert config.base_url == "https://openrouter.ai/api/v1"

    def test_custom_base_url(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile(base_url="https://custom.api/v1"))
        assert config.base_url == "https://custom.api/v1"

    def test_api_key_comes_from_env(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "my-key"}):
            config = Config.from_profile(_profile())
        assert config.api_key == "my-key"

    def test_model_id_defaults_from_env_fallback(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}, clear=False):
            os.environ.pop("MODEL_ID", None)
            config = Config.from_profile(_profile())
        assert config.model_id == "gpt-4o-mini"

    def test_default_max_workers(self):
        with patch("os.cpu_count", return_value=8):
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
                config = Config.from_profile(_profile())
        assert config.max_workers == 4

    def test_profile_workers(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile(workers=2))
        assert config.max_workers == 2

    def test_max_workers_zero_becomes_one(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile(workers=0))
        assert config.max_workers == 1

    def test_negative_max_workers_becomes_one(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile(workers=-5))
        assert config.max_workers == 1

    def test_large_max_workers_clamped_to_cpu_count(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile(workers=9999))
        max_cpu = os.cpu_count() or 8
        assert config.max_workers == max_cpu

    def test_optional_paths_none_by_default(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile())
        assert config.labs_parser_output_path is None
        assert config.medical_exams_parser_output_path is None

    def test_optional_paths_from_profile(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            config = Config.from_profile(_profile(
                labs_parser_output_path=Path("/path/to/labs"),
                medical_exams_parser_output_path=Path("/path/to/exams"),
            ))
        assert config.labs_parser_output_path == Path("/path/to/labs")
        assert config.medical_exams_parser_output_path == Path("/path/to/exams")


class TestConfigPaths:
    """Tests for config directory path helpers."""

    def test_get_config_dir_uses_home_config_path(self):
        with patch("parsehealthlog.config.Path.home", return_value=Path("/tmp/test-home")):
            assert get_config_dir() == Path("/tmp/test-home/.config/parsehealthlog")

    def test_get_profiles_dir_uses_config_dir(self):
        with patch("parsehealthlog.config.Path.home", return_value=Path("/tmp/test-home")):
            assert get_profiles_dir() == Path("/tmp/test-home/.config/parsehealthlog/profiles")

    def test_get_env_file_default(self):
        with patch("parsehealthlog.config.Path.home", return_value=Path("/tmp/test-home")):
            assert get_env_file() == Path("/tmp/test-home/.config/parsehealthlog/.env")

    def test_get_env_file_named(self):
        with patch("parsehealthlog.config.Path.home", return_value=Path("/tmp/test-home")):
            assert get_env_file("claude") == Path(
                "/tmp/test-home/.config/parsehealthlog/.env.claude"
            )


class TestProfileDiscovery:
    """Tests for profile discovery helpers."""

    def test_find_profile_path_uses_config_profiles_dir(self, tmp_path):
        home = tmp_path / "home"
        profiles_dir = home / ".config" / "parsehealthlog" / "profiles"
        profiles_dir.mkdir(parents=True)
        profile_path = profiles_dir / "test.yaml"
        profile_path.write_text("name: test\n", encoding="utf-8")

        with patch("parsehealthlog.config.Path.home", return_value=home):
            assert ProfileConfig.find_profile_path("test") == profile_path

    def test_list_profiles_uses_config_profiles_dir(self, tmp_path):
        home = tmp_path / "home"
        profiles_dir = home / ".config" / "parsehealthlog" / "profiles"
        profiles_dir.mkdir(parents=True)
        (profiles_dir / "alpha.yaml").write_text("name: alpha\n", encoding="utf-8")
        (profiles_dir / "beta.json").write_text('{"name": "beta"}\n', encoding="utf-8")
        (profiles_dir / "_template.yaml").write_text("name: template\n", encoding="utf-8")

        with patch("parsehealthlog.config.Path.home", return_value=home):
            assert ProfileConfig.list_profiles() == ["alpha", "beta"]


class TestProfileLoading:
    def test_non_mapping_profile_raises_configuration_error(self, tmp_path):
        profile_path = tmp_path / "invalid.yaml"
        profile_path.write_text("- not-a-mapping\n", encoding="utf-8")

        with pytest.raises(ConfigurationError, match="must contain a mapping"):
            ProfileConfig.from_file(profile_path)
