"""Tests for config.py configuration management."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from config import Config, ProfileConfig
from exceptions import ConfigurationError


def _profile(**overrides):
    """Create a minimal valid ProfileConfig."""
    defaults = {
        "name": "test",
        "health_log_path": Path("/path/to/log.md"),
        "output_path": Path("/path/to/output"),
    }
    defaults.update(overrides)
    return ProfileConfig(**defaults)


def _minimal_env(**overrides):
    """Minimal valid env vars."""
    env = {
        "OPENROUTER_API_KEY": "test-key",
        "MODEL_ID": "test-model",
    }
    env.update(overrides)
    return env


class TestConfigFromProfile:
    """Tests for Config.from_profile() method."""

    def test_missing_api_key_raises(self):
        env = {"MODEL_ID": "test-model"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
                Config.from_profile(_profile())

    def test_missing_model_id_raises(self):
        env = {"OPENROUTER_API_KEY": "test-key"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="MODEL_ID"):
                Config.from_profile(_profile())

    def test_missing_health_log_path_raises(self):
        with patch.dict(os.environ, _minimal_env(), clear=True):
            with pytest.raises(ConfigurationError, match="health_log_path"):
                Config.from_profile(_profile(health_log_path=None))

    def test_missing_output_path_raises(self):
        with patch.dict(os.environ, _minimal_env(), clear=True):
            with pytest.raises(ConfigurationError, match="output_path"):
                Config.from_profile(_profile(output_path=None))

    def test_valid_minimal_config(self):
        with patch.dict(os.environ, _minimal_env(), clear=True):
            config = Config.from_profile(_profile())
            assert config.openrouter_api_key == "test-key"
            assert config.model_id == "test-model"
            assert config.health_log_path == Path("/path/to/log.md")
            assert config.output_path == Path("/path/to/output")

    def test_default_base_url(self):
        with patch.dict(os.environ, _minimal_env(), clear=True):
            config = Config.from_profile(_profile())
            assert config.openrouter_base_url == "https://openrouter.ai/api/v1"

    def test_custom_base_url(self):
        env = _minimal_env(OPENROUTER_BASE_URL="https://custom.api/v1")
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_profile(_profile())
            assert config.openrouter_base_url == "https://custom.api/v1"

    def test_default_max_workers(self):
        with patch.dict(os.environ, _minimal_env(), clear=True):
            config = Config.from_profile(_profile())
            assert config.max_workers == 4

    def test_custom_max_workers_from_env(self):
        env = _minimal_env(MAX_WORKERS="8")
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_profile(_profile())
            assert config.max_workers == 8

    def test_profile_workers_override_env(self):
        env = _minimal_env(MAX_WORKERS="8")
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_profile(_profile(workers=2))
            assert config.max_workers == 2

    def test_max_workers_zero_becomes_one(self):
        env = _minimal_env(MAX_WORKERS="0")
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_profile(_profile())
            assert config.max_workers == 1

    def test_negative_max_workers_becomes_one(self):
        env = _minimal_env(MAX_WORKERS="-5")
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_profile(_profile())
            assert config.max_workers == 1

    def test_large_max_workers_clamped_to_cpu_count(self):
        env = _minimal_env(MAX_WORKERS="9999")
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_profile(_profile())
            max_cpu = os.cpu_count() or 8
            assert config.max_workers == max_cpu

    def test_optional_paths_none_by_default(self):
        with patch.dict(os.environ, _minimal_env(), clear=True):
            config = Config.from_profile(_profile())
            assert config.labs_parser_output_path is None
            assert config.medical_exams_parser_output_path is None

    def test_optional_paths_from_profile(self):
        with patch.dict(os.environ, _minimal_env(), clear=True):
            config = Config.from_profile(_profile(
                labs_parser_output_path=Path("/path/to/labs"),
                medical_exams_parser_output_path=Path("/path/to/exams"),
            ))
            assert config.labs_parser_output_path == Path("/path/to/labs")
            assert config.medical_exams_parser_output_path == Path("/path/to/exams")
