"""Tests for custom exception classes."""

import pytest

from exceptions import (
    HealthLogParserError,
    ConfigurationError,
    DateExtractionError,
    ValidationError,
    LabParsingError,
    PromptError,
    ProcessingError,
)


class TestExceptionHierarchy:
    """Tests for exception class hierarchy."""

    def test_all_exceptions_inherit_from_base(self):
        """All custom exceptions inherit from HealthLogParserError."""
        assert issubclass(ConfigurationError, HealthLogParserError)
        assert issubclass(DateExtractionError, HealthLogParserError)
        assert issubclass(ValidationError, HealthLogParserError)
        assert issubclass(LabParsingError, HealthLogParserError)
        assert issubclass(PromptError, HealthLogParserError)
        assert issubclass(ProcessingError, HealthLogParserError)

    def test_base_inherits_from_exception(self):
        """Base class inherits from Exception."""
        assert issubclass(HealthLogParserError, Exception)


class TestDateExtractionError:
    """Tests for DateExtractionError."""

    def test_basic_message(self):
        """Error stores message correctly."""
        err = DateExtractionError("No date found")
        assert str(err) == "No date found"
        assert err.section is None

    def test_with_section(self):
        """Error stores section context."""
        section = "### Invalid header"
        err = DateExtractionError("No date found", section=section)
        assert err.section == section

    def test_can_be_raised_and_caught(self):
        """Error can be raised and caught."""
        with pytest.raises(DateExtractionError) as exc_info:
            raise DateExtractionError("Test error", section="test")
        assert exc_info.value.section == "test"


class TestValidationError:
    """Tests for ValidationError."""

    def test_basic_message(self):
        """Error stores message and date correctly."""
        err = ValidationError("Validation failed", date="2024-01-15")
        assert str(err) == "Validation failed"
        assert err.date == "2024-01-15"
        assert err.attempts == 3  # default

    def test_with_custom_attempts(self):
        """Error stores custom attempt count."""
        err = ValidationError("Failed", date="2024-01-15", attempts=5)
        assert err.attempts == 5


class TestLabParsingError:
    """Tests for LabParsingError."""

    def test_basic_message(self):
        """Error stores message correctly."""
        err = LabParsingError("CSV parse error")
        assert str(err) == "CSV parse error"
        assert err.path is None

    def test_with_path(self):
        """Error stores path context."""
        err = LabParsingError("CSV parse error", path="/path/to/labs.csv")
        assert err.path == "/path/to/labs.csv"


class TestPromptError:
    """Tests for PromptError."""

    def test_basic_message(self):
        """Error stores message correctly."""
        err = PromptError("Prompt not found")
        assert str(err) == "Prompt not found"
        assert err.prompt_name is None

    def test_with_prompt_name(self):
        """Error stores prompt name context."""
        err = PromptError("Prompt not found", prompt_name="process.system_prompt")
        assert err.prompt_name == "process.system_prompt"


class TestProcessingError:
    """Tests for ProcessingError."""

    def test_basic_message(self):
        """Error stores message correctly."""
        err = ProcessingError("Processing failed")
        assert str(err) == "Processing failed"
        assert err.date is None
        assert err.cause is None

    def test_with_date_and_cause(self):
        """Error stores date and cause context."""
        cause = ValueError("Original error")
        err = ProcessingError("Processing failed", date="2024-01-15", cause=cause)
        assert err.date == "2024-01-15"
        assert err.cause is cause


class TestConfigurationError:
    """Tests for ConfigurationError."""

    def test_basic_message(self):
        """Error stores message correctly."""
        err = ConfigurationError("Missing API key")
        assert str(err) == "Missing API key"

    def test_can_be_caught_as_base(self):
        """ConfigurationError can be caught as HealthLogParserError."""
        with pytest.raises(HealthLogParserError):
            raise ConfigurationError("Test error")
