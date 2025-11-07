"""Configuration management for health-log-parser.

Centralizes all environment variable loading and validation.
"""

import os
from dataclasses import dataclass
from pathlib import Path


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
    questions_runs: int

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
            raise ValueError("OPENROUTER_API_KEY environment variable is required")

        health_log_path = os.getenv("HEALTH_LOG_PATH")
        if not health_log_path:
            raise ValueError("HEALTH_LOG_PATH environment variable is required")

        output_path = os.getenv("OUTPUT_PATH")
        if not output_path:
            raise ValueError("OUTPUT_PATH environment variable is required")

        # Load model configuration with defaults
        default_model = os.getenv("MODEL_ID", "gpt-4o-mini")
        process_model_id = os.getenv("PROCESS_MODEL_ID", default_model)
        validate_model_id = os.getenv("VALIDATE_MODEL_ID", default_model)
        summary_model_id = os.getenv("SUMMARY_MODEL_ID", default_model)
        questions_model_id = os.getenv("QUESTIONS_MODEL_ID", default_model)
        next_steps_model_id = os.getenv("NEXT_STEPS_MODEL_ID", default_model)

        # Load optional path configuration
        labs_parser_output_path_str = os.getenv("LABS_PARSER_OUTPUT_PATH")
        labs_parser_output_path = Path(labs_parser_output_path_str) if labs_parser_output_path_str else None

        report_output_path_str = os.getenv("REPORT_OUTPUT_PATH")
        report_output_path = Path(report_output_path_str) if report_output_path_str else None

        # Load processing configuration with defaults
        max_workers = int(os.getenv("MAX_WORKERS", "4")) or 1
        questions_runs = int(os.getenv("QUESTIONS_RUNS", "3"))

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
            questions_runs=questions_runs,
        )
