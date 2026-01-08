"""Tests for config.py configuration management."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from config import Config


class TestConfigFromEnv:
    """Tests for Config.from_env() method."""

    def test_missing_api_key_raises(self):
        """Missing OPENROUTER_API_KEY raises ValueError."""
        env = {
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
                Config.from_env()

    def test_missing_health_log_path_raises(self):
        """Missing HEALTH_LOG_PATH raises ValueError."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="HEALTH_LOG_PATH"):
                Config.from_env()

    def test_missing_output_path_raises(self):
        """Missing OUTPUT_PATH raises ValueError."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="OUTPUT_PATH"):
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
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.model_id == "custom-default"
            assert config.process_model_id == "process-model"
            assert config.validate_model_id == "validate-model"
            # Unset roles fall back to MODEL_ID
            assert config.summary_model_id == "custom-default"

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

    def test_default_questions_runs(self):
        """Default questions_runs is 3."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.questions_runs == 3

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
            assert config.report_output_path is None

    def test_optional_paths_when_set(self):
        """Optional paths are set when provided."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "LABS_PARSER_OUTPUT_PATH": "/path/to/labs",
            "REPORT_OUTPUT_PATH": "/path/to/report.md",
        }
        with patch.dict(os.environ, env, clear=True):
            config = Config.from_env()
            assert config.labs_parser_output_path == Path("/path/to/labs")
            assert config.report_output_path == Path("/path/to/report.md")

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
        """Non-integer MAX_WORKERS raises ValueError."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "MAX_WORKERS": "not-a-number",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError):
                Config.from_env()

    def test_invalid_questions_runs_raises(self):
        """Non-integer QUESTIONS_RUNS raises ValueError."""
        env = {
            "OPENROUTER_API_KEY": "test-key",
            "HEALTH_LOG_PATH": "/path/to/log.md",
            "OUTPUT_PATH": "/path/to/output",
            "QUESTIONS_RUNS": "abc",
        }
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError):
                Config.from_env()
