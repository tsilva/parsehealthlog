"""Configuration management for health-log-parser.

Centralizes all environment variable loading and validation.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from exceptions import ConfigurationError


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

    # Path Configuration
    health_log_path: Path
    output_path: Path
    labs_parser_output_path: Path | None
    report_output_path: Path | None

    # Processing Configuration
    max_workers: int
    staleness_threshold_days: int
    staleness_max_age_days: int

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

        # Load optional path configuration
        def get_optional_path(env_var: str) -> Path | None:
            val = os.getenv(env_var)
            return Path(val) if val else None

        labs_parser_output_path = get_optional_path("LABS_PARSER_OUTPUT_PATH")
        report_output_path = get_optional_path("REPORT_OUTPUT_PATH")

        # Load processing configuration with defaults and validation
        try:
            max_workers_raw = int(os.getenv("MAX_WORKERS", "4"))
        except ValueError as e:
            raise ConfigurationError(f"MAX_WORKERS must be an integer: {e}")
        # Clamp to valid range: 1 to CPU count (or 8 if unavailable)
        max_cpu = os.cpu_count() or 8
        max_workers = max(1, min(max_workers_raw, max_cpu))

        try:
            staleness_threshold_days = int(os.getenv("STALENESS_THRESHOLD_DAYS", "90"))
        except ValueError as e:
            raise ConfigurationError(f"STALENESS_THRESHOLD_DAYS must be an integer: {e}")
        # Ensure at least 1 day
        staleness_threshold_days = max(1, staleness_threshold_days)

        try:
            staleness_max_age_days = int(os.getenv("STALENESS_MAX_AGE_DAYS", "365"))
        except ValueError as e:
            raise ConfigurationError(f"STALENESS_MAX_AGE_DAYS must be an integer: {e}")
        # Must be at least staleness_threshold_days
        staleness_max_age_days = max(staleness_threshold_days, staleness_max_age_days)

        return cls(
            openrouter_api_key=openrouter_api_key,
            model_id=default_model,
            process_model_id=process_model_id,
            validate_model_id=validate_model_id,
            summary_model_id=summary_model_id,
            questions_model_id=questions_model_id,
            next_steps_model_id=next_steps_model_id,
            health_log_path=Path(health_log_path),
            output_path=Path(output_path),
            labs_parser_output_path=labs_parser_output_path,
            report_output_path=report_output_path,
            max_workers=max_workers,
            staleness_threshold_days=staleness_threshold_days,
            staleness_max_age_days=staleness_max_age_days,
        )
