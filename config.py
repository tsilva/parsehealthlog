"""Configuration management for health-log-parser.

Centralizes all environment variable loading and validation.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from exceptions import ConfigurationError


@dataclass
class ProfileConfig:
    """Profile configuration loaded from YAML file.

    Contains user-specific paths and optional setting overrides.
    API keys are NOT stored in profiles (kept in environment).
    """

    name: str
    health_log_path: Path | None = None
    output_path: Path | None = None
    labs_parser_output_path: Path | None = None
    medical_exams_parser_output_path: Path | None = None

    # Processing configuration
    workers: int | None = None

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
            medical_exams_parser_output_path=get_path("medical_exams_parser_output_path"),
            workers=data.get("workers"),
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

    All configuration values are loaded from environment variables.
    Required variables will raise an error if not set.
    """

    # API Configuration
    openrouter_base_url: str
    openrouter_api_key: str

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
        """Load configuration from a profile, with API key from environment.

        Args:
            profile: ProfileConfig loaded from YAML/JSON file.

        Returns:
            Config: Validated configuration object.

        Raises:
            ConfigurationError: If required fields are missing.
        """
        # API config always from environment (security)
        openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_api_key:
            raise ConfigurationError("OPENROUTER_API_KEY environment variable is required")

        # Validate required profile fields
        if not profile.health_log_path:
            raise ConfigurationError(f"Profile '{profile.name}' missing required field: health_log_path")
        if not profile.output_path:
            raise ConfigurationError(f"Profile '{profile.name}' missing required field: output_path")

        # Model configuration from environment variables
        model_id = os.getenv("MODEL_ID")
        if not model_id:
            raise ConfigurationError("MODEL_ID environment variable is required")

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
            openrouter_base_url=openrouter_base_url,
            openrouter_api_key=openrouter_api_key,
            model_id=model_id,
            health_log_path=profile.health_log_path,
            output_path=profile.output_path,
            labs_parser_output_path=profile.labs_parser_output_path,
            medical_exams_parser_output_path=profile.medical_exams_parser_output_path,
            max_workers=max_workers,
        )
