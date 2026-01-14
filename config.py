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
    report_output_path: Path | None = None

    # Model overrides (None means use default)
    model: str | None = None
    process_model: str | None = None
    validate_model: str | None = None
    summary_model: str | None = None
    questions_model: str | None = None
    next_steps_model: str | None = None
    status_model: str | None = None

    # Processing overrides
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
            report_output_path=get_path("report_output_path"),
            model=data.get("model"),
            process_model=data.get("process_model"),
            validate_model=data.get("validate_model"),
            summary_model=data.get("summary_model"),
            questions_model=data.get("questions_model"),
            next_steps_model=data.get("next_steps_model"),
            status_model=data.get("status_model"),
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
    openrouter_api_key: str

    # Model Configuration
    model_id: str
    process_model_id: str
    validate_model_id: str
    summary_model_id: str
    questions_model_id: str
    next_steps_model_id: str
    status_model_id: str

    # Path Configuration
    health_log_path: Path
    output_path: Path
    labs_parser_output_path: Path | None
    medical_exams_parser_output_path: Path | None
    report_output_path: Path | None

    # Processing Configuration
    max_workers: int

    @classmethod
    def from_env(cls) -> "Config":
        """Load and validate all environment variables.

        Returns:
            Config: Validated configuration object.

        Raises:
            ValueError: If required environment variables are missing.
        """
        # Load required variables
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_api_key:
            raise ConfigurationError("OPENROUTER_API_KEY environment variable is required")

        health_log_path = os.getenv("HEALTH_LOG_PATH")
        if not health_log_path:
            raise ConfigurationError("HEALTH_LOG_PATH environment variable is required")

        output_path = os.getenv("OUTPUT_PATH")
        if not output_path:
            raise ConfigurationError("OUTPUT_PATH environment variable is required")

        # Load model configuration with defaults
        default_model = os.getenv("MODEL_ID", "gpt-4o-mini")

        def get_model_id(role: str) -> str:
            return os.getenv(f"{role.upper()}_MODEL_ID", default_model)

        process_model_id = get_model_id("process")
        validate_model_id = get_model_id("validate")
        summary_model_id = get_model_id("summary")
        questions_model_id = get_model_id("questions")
        next_steps_model_id = get_model_id("next_steps")
        # Status model defaults to Claude Opus 4.5 for reasoning-heavy timeline building
        status_model_id = os.getenv("STATUS_MODEL_ID", "anthropic/claude-opus-4.5")

        # Load optional path configuration
        def get_optional_path(env_var: str) -> Path | None:
            val = os.getenv(env_var)
            return Path(val) if val else None

        labs_parser_output_path = get_optional_path("LABS_PARSER_OUTPUT_PATH")
        medical_exams_parser_output_path = get_optional_path("MEDICAL_EXAMS_PARSER_OUTPUT_PATH")
        report_output_path = get_optional_path("REPORT_OUTPUT_PATH")

        # Load processing configuration with defaults and validation
        try:
            max_workers_raw = int(os.getenv("MAX_WORKERS", "4"))
        except ValueError as e:
            raise ConfigurationError(f"MAX_WORKERS must be an integer: {e}")
        # Clamp to valid range: 1 to CPU count (or 8 if unavailable)
        max_cpu = os.cpu_count() or 8
        max_workers = max(1, min(max_workers_raw, max_cpu))

        return cls(
            openrouter_api_key=openrouter_api_key,
            model_id=default_model,
            process_model_id=process_model_id,
            validate_model_id=validate_model_id,
            summary_model_id=summary_model_id,
            questions_model_id=questions_model_id,
            next_steps_model_id=next_steps_model_id,
            status_model_id=status_model_id,
            health_log_path=Path(health_log_path),
            output_path=Path(output_path),
            labs_parser_output_path=labs_parser_output_path,
            medical_exams_parser_output_path=medical_exams_parser_output_path,
            report_output_path=report_output_path,
            max_workers=max_workers,
        )

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
        # API key always from environment (security)
        openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
        if not openrouter_api_key:
            raise ConfigurationError("OPENROUTER_API_KEY environment variable is required")

        # Validate required profile fields
        if not profile.health_log_path:
            raise ConfigurationError(f"Profile '{profile.name}' missing required field: health_log_path")
        if not profile.output_path:
            raise ConfigurationError(f"Profile '{profile.name}' missing required field: output_path")

        # Model configuration with cascading defaults: profile > env > default
        default_model = profile.model or os.getenv("MODEL_ID", "gpt-4o-mini")

        def get_model(profile_override: str | None, role: str) -> str:
            """Get model ID with priority: profile override > env var > default."""
            if profile_override:
                return profile_override
            env_val = os.getenv(f"{role.upper()}_MODEL_ID")
            if env_val:
                return env_val
            return default_model

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
            openrouter_api_key=openrouter_api_key,
            model_id=default_model,
            process_model_id=get_model(profile.process_model, "process"),
            validate_model_id=get_model(profile.validate_model, "validate"),
            summary_model_id=get_model(profile.summary_model, "summary"),
            questions_model_id=get_model(profile.questions_model, "questions"),
            next_steps_model_id=get_model(profile.next_steps_model, "next_steps"),
            status_model_id=profile.status_model or os.getenv("STATUS_MODEL_ID", "anthropic/claude-opus-4.5"),
            health_log_path=profile.health_log_path,
            output_path=profile.output_path,
            labs_parser_output_path=profile.labs_parser_output_path,
            medical_exams_parser_output_path=profile.medical_exams_parser_output_path,
            report_output_path=profile.report_output_path,
            max_workers=max_workers,
        )
