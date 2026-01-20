"""Integration tests for timeline processing with validation."""

import pytest
from pathlib import Path
from validate_timeline import run_all_validations


def test_validation_integration_with_timeline(tmp_path):
    """Test that validation can be run on a generated timeline."""
    # Create a sample timeline CSV
    timeline_path = tmp_path / "health_log.csv"
    timeline_path.write_text("""# Last updated: 2024-01-20 | Processed through: 2024-01-20 | LastEp: ep-003
# HASHES: 2024-01-15=abc123,2024-01-16=def456,2024-01-17=ghi789
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Hypothyroidism,condition,diagnosed,,TSH elevated to 8.5
2024-01-16,ep-002,Levothyroxine 50mcg,medication,started,ep-001,For hypothyroidism
2024-01-17,ep-003,Fatigue,symptom,improved,ep-001,Energy levels improving
""")

    # Create entries directory
    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    # Run validation
    results = run_all_validations(timeline_path, entries_dir)

    # Should pass all validations
    assert 'episode_continuity' in results
    assert 'related_episodes' in results
    assert 'csv_structure' in results
    assert 'chronological_order' in results

    # All validations should pass
    for check, errors in results.items():
        assert len(errors) == 0, f"{check} failed: {errors}"


def test_validation_catches_real_errors(tmp_path):
    """Test that validation catches actual errors in timeline."""
    # Create a timeline with multiple issues
    timeline_path = tmp_path / "health_log.csv"
    timeline_path.write_text("""# Last updated: 2024-01-20 | Processed through: 2024-01-20 | LastEp: ep-003
# HASHES: 2024-01-15=abc123,2024-01-16=def456
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-20,ep-001,Hypothyroidism,condition,diagnosed,,TSH elevated
2024-01-15,ep-003,Levothyroxine 50mcg,medication,started,ep-999,For hypothyroidism
""")

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    # Run validation
    results = run_all_validations(timeline_path, entries_dir)

    # Should detect multiple errors
    # 1. Episode ID gap (ep-001 to ep-003)
    assert len(results['episode_continuity']) > 0

    # 2. Orphaned reference (ep-999 doesn't exist)
    assert len(results['related_episodes']) > 0

    # 3. Out of order (2024-01-20 before 2024-01-15)
    assert len(results['chronological_order']) > 0


def test_batch_validation_integration(tmp_path):
    """Test CSV batch validation with realistic data."""
    from main import HealthLogProcessor

    # This is a simplified test that validates the _validate_timeline_batch_output method
    # We can't easily test the full processor without LLM dependencies

    csv_text = """2024-01-15,ep-001,Vitamin D 5000IU,supplement,started,,Daily with breakfast
2024-01-15,ep-002,Omega-3,supplement,started,,Daily with dinner
2024-01-16,ep-003,Magnesium,supplement,started,,Before bed"""

    entries_dates = ["2024-01-15", "2024-01-16"]
    next_episode_num = 1

    # Create a minimal processor instance (without full initialization)
    # Note: This is a simplified test - full integration would require mocking LLM
    processor = type('obj', (object,), {
        '_validate_timeline_batch_output': HealthLogProcessor._validate_timeline_batch_output
    })()

    cleaned_csv, errors = processor._validate_timeline_batch_output(
        csv_text, entries_dates, next_episode_num
    )

    # Should pass validation with no errors
    assert len(errors) == 0
    assert cleaned_csv == csv_text


def test_batch_validation_catches_invalid_rows(tmp_path):
    """Test that batch validation catches invalid CSV rows."""
    from main import HealthLogProcessor

    csv_text = """2024-01-15,INVALID,Vitamin D,supplement,started,,Daily
2024-01-99,ep-001,Omega-3,supplement,started,,Daily
2024-01-16,ep-002,Magnesium,invalid_category,started,,Daily
2024-01-16,ep-003,Zinc,supplement,invalid_event,,Daily"""

    entries_dates = ["2024-01-15", "2024-01-16"]
    next_episode_num = 1

    processor = type('obj', (object,), {
        '_validate_timeline_batch_output': HealthLogProcessor._validate_timeline_batch_output
    })()

    cleaned_csv, errors = processor._validate_timeline_batch_output(
        csv_text, entries_dates, next_episode_num
    )

    # Should detect multiple validation errors
    assert len(errors) >= 4
    # Invalid episode ID format
    assert any("Invalid episode ID format" in err for err in errors)
    # Invalid date format
    assert any("Invalid date format" in err for err in errors)
    # Invalid category
    assert any("Invalid category" in err for err in errors)
    # Invalid event
    assert any("Invalid event" in err for err in errors)


def test_episode_id_extraction_from_column_only(tmp_path):
    """Test that episode ID extraction only looks at EpisodeID column."""
    from main import HealthLogProcessor

    # Timeline with "ep-999" in Details column but max episode is ep-003
    timeline_content = """# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily dose
2024-01-16,ep-002,Omega-3,supplement,started,,Mentioned ep-999 in notes
2024-01-17,ep-003,Magnesium,supplement,started,,Regular dose
"""

    processor = type('obj', (object,), {
        '_get_last_episode_num': HealthLogProcessor._get_last_episode_num
    })()

    max_episode = processor._get_last_episode_num(timeline_content)

    # Should return 3, not 999
    assert max_episode == 3


def test_episode_id_extraction_handles_empty_timeline(tmp_path):
    """Test that episode ID extraction handles empty timeline."""
    from main import HealthLogProcessor

    timeline_content = """# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
"""

    processor = type('obj', (object,), {
        '_get_last_episode_num': HealthLogProcessor._get_last_episode_num
    })()

    max_episode = processor._get_last_episode_num(timeline_content)

    # Should return 0 for empty timeline
    assert max_episode == 0


def test_comprehensive_stack_validation_workflow(tmp_path):
    """Test comprehensive stack update validation in realistic scenario."""
    timeline_path = tmp_path / "health_log.csv"
    timeline_path.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-10,ep-001,Vitamin D 5000IU,supplement,started,,Daily
2024-01-15,ep-002,Omega-3 1000mg,supplement,started,,Daily
2024-01-20,ep-003,Magnesium 400mg,supplement,started,,Only taking this now
""")

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    # Create entry with comprehensive stack keyword
    (entries_dir / "2024-01-20.processed.md").write_text(
        """# Current Health Status

Current stack: Only taking Magnesium 400mg daily. Stopped all other supplements.

Feeling better with simplified routine.
"""
    )

    results = run_all_validations(timeline_path, entries_dir)

    # Should detect that Vitamin D and Omega-3 weren't stopped
    assert 'comprehensive_stack_updates' in results
    stack_errors = results['comprehensive_stack_updates']
    assert len(stack_errors) >= 2
    assert any("Vitamin D" in err for err in stack_errors)
    assert any("Omega-3" in err for err in stack_errors)
