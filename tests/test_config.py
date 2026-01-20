"""Tests for config.py configuration management."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from config import Config
from exceptions import ConfigurationError


class TestConfigFromEnv:
    """Tests for Config.from_env() method."""

    def test_missing_api_key_raises(self):
        """Missing OPENROUTER_API_KEY raises ConfigurationError."""
        env = {
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="OPENROUTER_API_KEY"):
                Config.from_env()

    def test_missing_health_log_path_raises(self):
        """Missing HEALTH_LOG_PATH raises ConfigurationError."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="HEALTH_LOG_PATH"):
                Config.from_env()

    def test_missing_output_path_raises(self):
        """Missing OUTPUT_PATH raises ConfigurationError."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="OUTPUT_PATH"):
                Config.from_env()

    def test_valid_minimal_config(self):
        """Minimal valid config loads successfully."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.openrouter_api_key == "test-key"
            assert config.health_log_path == Path("/path/to/log.md")
            assert config.output_path == Path("/path/to/output")

    def test_default_model_id(self):
        """Default model ID is gpt-4o-mini."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.model_id == "gpt-4o-mini"
            assert config.process_model_id == "gpt-4o-mini"
            assert config.validate_model_id == "gpt-4o-mini"

    def test_custom_model_ids(self):
        """Custom model IDs override defaults."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "MODEL_ID": "custom-default",
            "PROCESS_MODEL_ID": "process-model",
            "VALIDATE_MODEL_ID": "validate-model",
            "STATUS_MODEL_ID": "status-model",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.model_id == "custom-default"
            assert config.process_model_id == "process-model"
            assert config.validate_model_id == "validate-model"
            assert config.status_model_id == "status-model"

    def test_default_max_workers(self):
        """Default max_workers is 4."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.max_workers == 4

    def test_custom_max_workers(self):
        """Custom MAX_WORKERS value is used."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "MAX_WORKERS": "8",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.max_workers == 8

    def test_optional_paths_none_by_default(self):
        """Optional paths are None when not set."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.labs_parser_output_path is None

    def test_optional_paths_when_set(self):
        """Optional paths are set when provided."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "LABS_PARSER_OUTPUT_PATH": "/path/to/labs",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.labs_parser_output_path == Path("/path/to/labs")

    def test_max_workers_zero_becomes_one(self):
        """MAX_WORKERS=0 falls back to 1."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "MAX_WORKERS": "0",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.max_workers == 1

    def test_invalid_max_workers_raises(self):
        """Non-integer MAX_WORKERS raises ConfigurationError with clear message."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "MAX_WORKERS": "not-a-number",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ConfigurationError, match="MAX_WORKERS must be an integer"):
                Config.from_env()

    def test_negative_max_workers_becomes_one(self):
        """Negative MAX_WORKERS is clamped to 1."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "MAX_WORKERS": "-5",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.max_workers == 1

    def test_large_max_workers_clamped_to_cpu_count(self):
        """Very large MAX_WORKERS is clamped to CPU count."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "MAX_WORKERS": "9999",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            import os as os_module
            max_cpu = os_module.cpu_count() or 8
            assert config.max_workers == max_cpu
