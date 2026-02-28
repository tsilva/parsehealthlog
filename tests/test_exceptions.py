"""Tests for custom exception classes."""

import pytest

from parsehealthlog.exceptions import (
    HealthLogParserError,
    ConfigurationError,
    DateExtractionError,
    PromptError,
)


class TestExceptionHierarchy:
    """Tests for exception class hierarchy."""

    def test_all_exceptions_inherit_from_base(self):
        """All custom exceptions inherit from HealthLogParserError."""
        assert issubclass(ConfigurationError, HealthLogParserError)
        assert issubclass(DateExtractionError, HealthLogParserError)
        assert issubclass(PromptError, HealthLogParserError)

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
