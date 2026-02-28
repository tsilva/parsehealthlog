"""Custom exceptions for parsehealthlog.

Provides domain-specific error types for better error handling and debugging.
"""


class HealthLogParserError(Exception):
    """Base exception for all parsehealthlog errors."""

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


class ExtractionError(HealthLogParserError):
    """Raised when fact extraction fails validation."""

    def __init__(self, message: str, date: str | None = None, errors: list[str] | None = None):
        super().__init__(message)
        self.date = date
        self.errors = errors or []


