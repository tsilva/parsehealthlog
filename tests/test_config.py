"""Tests for config.py configuration management."""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from parsehealthlog.config import Config, ProfileConfig
from parsehealthlog.exceptions import ConfigurationError


def _profile(**overrides):
    """Create a minimal valid ProfileConfig."""
    defaults = {
        "name": "test",
        "health_log_path": Path("/path/to/log.md"),
        "output_path": Path("/path/to/output"),
        "model_id": "test-model",
    }
    defaults.update(overrides)
    return ProfileConfig(**defaults)


class TestConfigFromProfile:
    """Tests for Config.from_profile() method."""

    def test_missing_model_id_raises(self):
        with pytest.raises(ConfigurationError, match="model_id"):
            Config.from_profile(_profile(model_id=None))

    def test_missing_health_log_path_raises(self):
        with pytest.raises(ConfigurationError, match="health_log_path"):
            Config.from_profile(_profile(health_log_path=None))

    def test_missing_output_path_raises(self):
        with pytest.raises(ConfigurationError, match="output_path"):
            Config.from_profile(_profile(output_path=None))

    def test_valid_minimal_config(self):
        config = Config.from_profile(_profile())
        assert config.api_key == "parsehealthlog"
        assert config.model_id == "test-model"
        assert config.health_log_path == Path("/path/to/log.md")
        assert config.output_path == Path("/path/to/output")

    def test_default_base_url(self):
        config = Config.from_profile(_profile())
        assert config.base_url == "http://127.0.0.1:8082/api/v1"

    def test_custom_base_url(self):
        config = Config.from_profile(_profile(base_url="https://custom.api/v1"))
        assert config.base_url == "https://custom.api/v1"

    def test_custom_api_key(self):
        config = Config.from_profile(_profile(api_key="my-key"))
        assert config.api_key == "my-key"

    def test_default_max_workers(self):
        config = Config.from_profile(_profile())
        assert config.max_workers == 4

    def test_profile_workers(self):
        config = Config.from_profile(_profile(workers=2))
        assert config.max_workers == 2

    def test_max_workers_zero_becomes_one(self):
        config = Config.from_profile(_profile(workers=0))
        assert config.max_workers == 1

    def test_negative_max_workers_becomes_one(self):
        config = Config.from_profile(_profile(workers=-5))
        assert config.max_workers == 1

    def test_large_max_workers_clamped_to_cpu_count(self):
        config = Config.from_profile(_profile(workers=9999))
        max_cpu = os.cpu_count() or 8
        assert config.max_workers == max_cpu

    def test_optional_paths_none_by_default(self):
        config = Config.from_profile(_profile())
        assert config.labs_parser_output_path is None
        assert config.medical_exams_parser_output_path is None

    def test_optional_paths_from_profile(self):
        config = Config.from_profile(_profile(
            labs_parser_output_path=Path("/path/to/labs"),
            medical_exams_parser_output_path=Path("/path/to/exams"),
        ))
        assert config.labs_parser_output_path == Path("/path/to/labs")
        assert config.medical_exams_parser_output_path == Path("/path/to/exams")
