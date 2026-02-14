"""Tests for extraction validation and retry behavior."""

import json
from unittest.mock import MagicMock, patch

import pytest

from entity_registry import (
    EXTRACTION_SCHEMA_VERSION,
    START_EVENTS,
    STOP_EVENTS,
    VALID_ENTITY_TYPES,
    VALID_EVENTS,
    validate_extracted_facts,
)
from exceptions import ExtractionError


# ---------------------------------------------------------------------------
# validate_extracted_facts – pure logic tests
# ---------------------------------------------------------------------------


class TestValidateExtractedFacts:
    """Tests for the validate_extracted_facts validation function."""

    def test_valid_minimal_empty_items(self):
        """Empty items list is valid."""
        assert validate_extracted_facts({"items": []}) == []

    def test_valid_full_item(self):
        """Item with all fields populated is valid."""
        facts = {
            "items": [
                {
                    "type": "condition",
                    "name": "Gastritis",
                    "event": "diagnosed",
                    "details": "Stress-triggered",
                    "for_condition": None,
                }
            ]
        }
        assert validate_extracted_facts(facts) == []

    def test_valid_item_with_string_for_condition(self):
        """for_condition as a string is valid."""
        facts = {
            "items": [
                {
                    "type": "medication",
                    "name": "Pantoprazole 20mg",
                    "event": "started",
                    "details": "For acid reflux",
                    "for_condition": "Gastritis",
                }
            ]
        }
        assert validate_extracted_facts(facts) == []

    def test_valid_item_minimal_required_fields_only(self):
        """Item with only required fields (no details/for_condition) is valid."""
        facts = {
            "items": [
                {"type": "symptom", "name": "Headache", "event": "noted"}
            ]
        }
        assert validate_extracted_facts(facts) == []

    def test_non_dict_input(self):
        """Non-dict input returns error."""
        errors = validate_extracted_facts("not a dict")
        assert len(errors) == 1
        assert "Expected dict" in errors[0]

    def test_list_input(self):
        """List input returns error."""
        errors = validate_extracted_facts([{"items": []}])
        assert len(errors) == 1
        assert "Expected dict" in errors[0]

    def test_missing_items_key(self):
        """Dict without 'items' key returns error."""
        errors = validate_extracted_facts({"data": []})
        assert len(errors) == 1
        assert "Missing required key 'items'" in errors[0]

    def test_items_not_a_list(self):
        """'items' as a dict returns error."""
        errors = validate_extracted_facts({"items": {}})
        assert len(errors) == 1
        assert "'items' must be a list" in errors[0]

    def test_item_not_a_dict(self):
        """Non-dict item in list returns error."""
        errors = validate_extracted_facts({"items": ["not a dict"]})
        assert len(errors) == 1
        assert "expected dict" in errors[0]

    def test_missing_required_field_type(self):
        """Missing 'type' field returns error."""
        facts = {"items": [{"name": "Gastritis", "event": "diagnosed"}]}
        errors = validate_extracted_facts(facts)
        assert any("'type' must be a non-empty string" in e for e in errors)

    def test_missing_required_field_name(self):
        """Missing 'name' field returns error."""
        facts = {"items": [{"type": "condition", "event": "diagnosed"}]}
        errors = validate_extracted_facts(facts)
        assert any("'name' must be a non-empty string" in e for e in errors)

    def test_missing_required_field_event(self):
        """Missing 'event' field returns error."""
        facts = {"items": [{"type": "condition", "name": "Gastritis"}]}
        errors = validate_extracted_facts(facts)
        assert any("'event' must be a non-empty string" in e for e in errors)

    def test_empty_name_string(self):
        """Empty name string returns error."""
        facts = {"items": [{"type": "condition", "name": "", "event": "diagnosed"}]}
        errors = validate_extracted_facts(facts)
        assert any("'name' must be a non-empty string" in e for e in errors)

    def test_whitespace_only_name(self):
        """Whitespace-only name returns error."""
        facts = {"items": [{"type": "condition", "name": "   ", "event": "diagnosed"}]}
        errors = validate_extracted_facts(facts)
        assert any("'name' must be a non-empty string" in e for e in errors)

    def test_invalid_entity_type(self):
        """Invalid entity type returns error."""
        facts = {"items": [{"type": "disease", "name": "Flu", "event": "diagnosed"}]}
        errors = validate_extracted_facts(facts)
        assert any("invalid type 'disease'" in e for e in errors)

    def test_invalid_event_for_type(self):
        """Event not valid for the given type returns error."""
        facts = {"items": [{"type": "medication", "name": "Aspirin", "event": "diagnosed"}]}
        errors = validate_extracted_facts(facts)
        assert any("invalid event 'diagnosed' for type 'medication'" in e for e in errors)

    def test_all_valid_type_event_combinations(self):
        """Every valid type+event combination passes validation."""
        for entity_type, valid_events in VALID_EVENTS.items():
            for event in valid_events:
                facts = {
                    "items": [
                        {"type": entity_type, "name": "Test Entity", "event": event}
                    ]
                }
                errors = validate_extracted_facts(facts)
                assert errors == [], (
                    f"type={entity_type}, event={event} should be valid but got: {errors}"
                )

    def test_multiple_items_validated_independently(self):
        """Multiple items: valid ones don't mask invalid ones."""
        facts = {
            "items": [
                {"type": "condition", "name": "Gastritis", "event": "diagnosed"},
                {"type": "invalid_type", "name": "Bad", "event": "bad_event"},
            ]
        }
        errors = validate_extracted_facts(facts)
        # First item is valid, second has errors
        assert any("items[1]" in e for e in errors)
        assert not any("items[0]" in e for e in errors)

    def test_optional_details_non_string_returns_error(self):
        """details as a non-string, non-null value returns error."""
        facts = {
            "items": [
                {"type": "condition", "name": "Gastritis", "event": "diagnosed", "details": 123}
            ]
        }
        errors = validate_extracted_facts(facts)
        assert any("'details' must be a string or null" in e for e in errors)

    def test_optional_for_condition_non_string_returns_error(self):
        """for_condition as a non-string, non-null value returns error."""
        facts = {
            "items": [
                {
                    "type": "medication",
                    "name": "Aspirin",
                    "event": "started",
                    "for_condition": 42,
                }
            ]
        }
        errors = validate_extracted_facts(facts)
        assert any("'for_condition' must be a string or null" in e for e in errors)

    def test_consistency_valid_events_subset_of_start_stop(self):
        """All VALID_EVENTS values are subsets of START_EVENTS | STOP_EVENTS."""
        all_known = START_EVENTS | STOP_EVENTS
        for entity_type, events in VALID_EVENTS.items():
            assert events <= all_known, (
                f"VALID_EVENTS['{entity_type}'] contains events not in "
                f"START_EVENTS | STOP_EVENTS: {events - all_known}"
            )


class TestExtractionError:
    """Tests for the ExtractionError exception class."""

    def test_basic_creation(self):
        err = ExtractionError("extraction failed", date="2024-01-15", errors=["bad type"])
        assert str(err) == "extraction failed"
        assert err.date == "2024-01-15"
        assert err.errors == ["bad type"]

    def test_defaults(self):
        err = ExtractionError("fail")
        assert err.date is None
        assert err.errors == []


# ---------------------------------------------------------------------------
# _extract_entry_facts – retry behavior tests (mocked LLM)
# ---------------------------------------------------------------------------


VALID_FACTS_JSON = json.dumps(
    {"items": [{"type": "condition", "name": "Gastritis", "event": "diagnosed"}]}
)

INVALID_FACTS_JSON = json.dumps(
    {"items": [{"type": "disease", "name": "Flu", "event": "diagnosed"}]}
)


def _make_processor(tmp_path):
    """Create a minimal HealthLogProcessor with mocked dependencies."""
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    processor = MagicMock()
    processor.entries_dir = entries_dir
    processor.logger = MagicMock()
    processor.prompts = {}

    # Wire up the real methods we're testing
    from main import HealthLogProcessor

    processor._extract_entry_facts = HealthLogProcessor._extract_entry_facts.__get__(
        processor
    )
    processor._write_extraction_failure = (
        HealthLogProcessor._write_extraction_failure.__get__(processor)
    )
    processor._parse_json_response = HealthLogProcessor._parse_json_response.__get__(
        processor
    )
    processor._prompt = MagicMock(return_value="You are a clinical data extractor.")

    return processor


class TestExtractEntryFactsRetry:
    """Tests for _extract_entry_facts retry and caching behavior."""

    def test_happy_path_valid_first_try(self, tmp_path):
        """Valid extraction on first attempt: cache written, facts returned."""
        processor = _make_processor(tmp_path)
        processor.llm = {"status": MagicMock(return_value=VALID_FACTS_JSON)}

        result = processor._extract_entry_facts("2024-01-15", "Some entry content")

        assert result is not None
        assert result["items"][0]["name"] == "Gastritis"

        # Cache file written with schema version
        cache_path = tmp_path / "entries" / "2024-01-15.extracted.json"
        assert cache_path.exists()
        cached = json.loads(cache_path.read_text())
        assert cached["_schema_version"] == EXTRACTION_SCHEMA_VERSION
        assert cached["facts"]["items"][0]["name"] == "Gastritis"

        # LLM called exactly once
        assert processor.llm["status"].call_count == 1

    def test_retry_success_invalid_then_valid(self, tmp_path):
        """Invalid first attempt, valid second: succeeds on retry."""
        processor = _make_processor(tmp_path)
        processor.llm = {
            "status": MagicMock(side_effect=[INVALID_FACTS_JSON, VALID_FACTS_JSON])
        }

        result = processor._extract_entry_facts("2024-01-15", "Some entry content")

        assert result is not None
        assert result["items"][0]["name"] == "Gastritis"
        assert processor.llm["status"].call_count == 2

    def test_feedback_includes_validation_errors(self, tmp_path):
        """Retry messages include validation errors as user feedback."""
        processor = _make_processor(tmp_path)
        processor.llm = {
            "status": MagicMock(side_effect=[INVALID_FACTS_JSON, VALID_FACTS_JSON])
        }

        processor._extract_entry_facts("2024-01-15", "Some entry content")

        # Second call should have 4 messages: system, user, assistant (bad), user (feedback)
        second_call_messages = processor.llm["status"].call_args_list[1][0][0]
        assert len(second_call_messages) == 4
        assert second_call_messages[2]["role"] == "assistant"
        assert second_call_messages[3]["role"] == "user"
        assert "validation errors" in second_call_messages[3]["content"]
        assert "invalid type 'disease'" in second_call_messages[3]["content"]

    def test_all_retries_fail_writes_diagnostic(self, tmp_path):
        """All 3 attempts fail: .failed.json written, returns None."""
        processor = _make_processor(tmp_path)
        processor.llm = {
            "status": MagicMock(
                return_value=INVALID_FACTS_JSON
            )
        }

        result = processor._extract_entry_facts("2024-01-15", "Some entry content")

        assert result is None
        assert processor.llm["status"].call_count == 3

        # Diagnostic file written
        failed_path = tmp_path / "entries" / "2024-01-15.failed.json"
        assert failed_path.exists()
        diagnostic = json.loads(failed_path.read_text())
        assert diagnostic["date"] == "2024-01-15"
        assert len(diagnostic["last_errors"]) > 0
        assert "input_content_preview" in diagnostic

    def test_cache_hit_skips_llm(self, tmp_path):
        """Cached extraction with matching hash and schema version skips LLM."""
        processor = _make_processor(tmp_path)
        processor.llm = {"status": MagicMock()}

        from main import short_hash

        content = "Some entry content"
        content_hash = short_hash(content)

        # Pre-populate cache
        cache_path = tmp_path / "entries" / "2024-01-15.extracted.json"
        cache_data = {
            "_content_hash": content_hash,
            "_schema_version": EXTRACTION_SCHEMA_VERSION,
            "facts": {"items": [{"type": "symptom", "name": "Headache", "event": "noted"}]},
        }
        cache_path.write_text(json.dumps(cache_data))

        result = processor._extract_entry_facts("2024-01-15", content)

        assert result is not None
        assert result["items"][0]["name"] == "Headache"
        # LLM never called
        processor.llm["status"].assert_not_called()

    def test_cache_miss_on_schema_version_change(self, tmp_path):
        """Old schema version in cache forces re-extraction."""
        processor = _make_processor(tmp_path)
        processor.llm = {"status": MagicMock(return_value=VALID_FACTS_JSON)}

        from main import short_hash

        content = "Some entry content"
        content_hash = short_hash(content)

        # Pre-populate cache with old schema version
        cache_path = tmp_path / "entries" / "2024-01-15.extracted.json"
        cache_data = {
            "_content_hash": content_hash,
            "_schema_version": EXTRACTION_SCHEMA_VERSION - 1,
            "facts": {"items": []},
        }
        cache_path.write_text(json.dumps(cache_data))

        result = processor._extract_entry_facts("2024-01-15", content)

        assert result is not None
        # LLM was called (cache miss)
        assert processor.llm["status"].call_count == 1
        # Cache updated with new schema version
        new_cache = json.loads(cache_path.read_text())
        assert new_cache["_schema_version"] == EXTRACTION_SCHEMA_VERSION
