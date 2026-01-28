"""Custom exceptions for health-log-parser.

Provides domain-specific error types for better error handling and debugging.
"""


class HealthLogParserError(Exception):
    """Base exception for all health-log-parser errors."""

    pass


class ConfigurationError(HealthLogParserError):
    """Raised when configuration is invalid or missing."""

    pass


class DateExtractionError(HealthLogParserError):
    """Raised when a date cannot be extracted from a section header."""

    def __init__(self, message: str, section: str | None = None):
        super().__init__(message)
        self.section = section


class PromptError(HealthLogParserError):
    """Raised when a required prompt file is missing or invalid."""

    def __init__(self, message: str, prompt_name: str | None = None):
        super().__init__(message)
        self.prompt_name = prompt_name


