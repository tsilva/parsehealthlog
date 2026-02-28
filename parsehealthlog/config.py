"""Configuration management for parsehealthlog.

Centralizes profile-based configuration loading and validation.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from parsehealthlog.exceptions import ConfigurationError


# OpenRouter pricing per 1M tokens (input/output) in USD
# Prices as of 2024 - update as needed
MODEL_PRICING = {
    # OpenAI models
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "gpt-4": {"input": 30.00, "output": 60.00},
    "gpt-3.5-turbo": {"input": 0.50, "output": 1.50},
    # Anthropic models
    "claude-3-haiku": {"input": 0.25, "output": 1.25},
    "claude-3-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    "claude-3.5-sonnet": {"input": 3.00, "output": 15.00},
    # Google models
    "gemini-pro": {"input": 0.50, "output": 1.50},
    "gemini-ultra": {"input": 1.00, "output": 3.00},
    # Meta models
    "llama-3.1-8b": {"input": 0.05, "output": 0.10},
    "llama-3.1-70b": {"input": 0.50, "output": 1.00},
    "llama-3.1-405b": {"input": 2.00, "output": 4.00},
    # Default fallback
    "default": {"input": 1.00, "output": 3.00},
}


def get_model_pricing(model_id: str) -> dict[str, float]:
    """Get pricing for a model, falling back to default if not found."""
    return MODEL_PRICING.get(model_id, MODEL_PRICING["default"])


def check_api_accessibility(base_url: str, timeout: int = 10) -> bool:
    """Check if the API base URL is accessible."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(base_url, method="HEAD")
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, TimeoutError):
        return False


@dataclass
class ProfileConfig:
    """Profile configuration loaded from YAML file.

    Contains user-specific paths, API configuration, and optional setting overrides.
    """

    name: str
    health_log_path: Path | None = None
    output_path: Path | None = None
    labs_parser_output_path: Path | None = None
    medical_exams_parser_output_path: Path | None = None

    # Processing configuration
    workers: int | None = None

    # API configuration
    base_url: str = "http://127.0.0.1:8082/api/v1"
    api_key: str = "parsehealthlog"
    model_id: str | None = None

    @classmethod
    def from_file(cls, profile_path: Path) -> "ProfileConfig":
        """Load profile from YAML or JSON file."""
        if not profile_path.exists():
            raise FileNotFoundError(f"Profile not found: {profile_path}")

        content = profile_path.read_text(encoding="utf-8")

        if profile_path.suffix in (".yaml", ".yml"):
            data = yaml.safe_load(content)
        else:
            data = json.load(open(profile_path, encoding="utf-8"))

        def get_path(key: str) -> Path | None:
            val = data.get(key)
            return Path(val) if val else None

        return cls(
            name=data.get("name", profile_path.stem),
            health_log_path=get_path("health_log_path"),
            output_path=get_path("output_path"),
            labs_parser_output_path=get_path("labs_parser_output_path"),
            medical_exams_parser_output_path=get_path(
                "medical_exams_parser_output_path"
            ),
            workers=data.get("workers"),
            base_url=data.get("base_url", "http://127.0.0.1:8082/api/v1"),
            api_key=data.get("api_key", "parsehealthlog"),
            model_id=data.get("model_id"),
        )

    @classmethod
    def list_profiles(cls, profiles_dir: Path = Path("profiles")) -> list[str]:
        """List available profile names (excludes templates starting with _)."""
        if not profiles_dir.exists():
            return []
        profiles = []
        for ext in ("*.yaml", "*.yml", "*.json"):
            for f in profiles_dir.glob(ext):
                if not f.name.startswith("_"):
                    profiles.append(f.stem)
        return sorted(set(profiles))


@dataclass
class Config:
    """Configuration for the health log parser.

    All configuration values are loaded from profile YAML files.
    Required fields will raise an error if not set.
    """

    # API Configuration
    base_url: str
    api_key: str

    # Model Configuration
    model_id: str

    # Path Configuration
    health_log_path: Path
    output_path: Path
    labs_parser_output_path: Path | None
    medical_exams_parser_output_path: Path | None

    # Processing Configuration
    max_workers: int

    @classmethod
    def from_profile(cls, profile: ProfileConfig) -> "Config":
        """Load configuration from a profile.

        Args:
            profile: ProfileConfig loaded from YAML/JSON file.

        Returns:
            Config: Validated configuration object.

        Raises:
            ConfigurationError: If required fields are missing.
        """
        # Validate required profile fields
        if not profile.health_log_path:
            raise ConfigurationError(
                f"Profile '{profile.name}' missing required field: health_log_path"
            )
        if not profile.output_path:
            raise ConfigurationError(
                f"Profile '{profile.name}' missing required field: output_path"
            )
        if not profile.model_id:
            raise ConfigurationError(
                f"Profile '{profile.name}' missing required field: model_id"
            )

        # Workers with priority: profile > env > default (clamped to CPU count)
        if profile.workers is not None:
            max_workers_raw = profile.workers
        else:
            try:
                max_workers_raw = int(os.getenv("MAX_WORKERS", "4"))
            except ValueError:
                max_workers_raw = 4
        max_cpu = os.cpu_count() or 8
        max_workers = max(1, min(max_workers_raw, max_cpu))

        return cls(
            base_url=profile.base_url,
            api_key=profile.api_key,
            model_id=profile.model_id,
            health_log_path=profile.health_log_path,
            output_path=profile.output_path,
            labs_parser_output_path=profile.labs_parser_output_path,
            medical_exams_parser_output_path=profile.medical_exams_parser_output_path,
            max_workers=max_workers,
        )
