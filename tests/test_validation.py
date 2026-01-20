"""Unit tests for timeline validation functions."""

import pytest
from pathlib import Path
from validate_timeline import (
    validate_episode_continuity,
    validate_related_episodes,
    validate_csv_structure,
    validate_chronological_order,
    validate_comprehensive_stack_updates,
)


def test_episode_continuity_detects_gaps(tmp_path):
    """Test that episode ID gaps are detected."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
2024-01-16,ep-003,Omega-3,supplement,started,,Daily
""")

    errors = validate_episode_continuity(timeline)
    assert len(errors) == 1
    assert "gap" in errors[0].lower()
    assert "ep-001" in errors[0]
    assert "ep-003" in errors[0]


def test_episode_continuity_detects_duplicates(tmp_path):
    """Test that duplicate episode IDs are detected."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
2024-01-16,ep-001,Omega-3,supplement,started,,Daily
""")

    errors = validate_episode_continuity(timeline)
    assert len(errors) == 1
    assert "duplicate" in errors[0].lower()


def test_episode_continuity_passes_sequential(tmp_path):
    """Test that sequential episode IDs pass validation."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
2024-01-16,ep-002,Omega-3,supplement,started,,Daily
2024-01-17,ep-003,Magnesium,supplement,started,,Daily
""")

    errors = validate_episode_continuity(timeline)
    assert len(errors) == 0


def test_related_episodes_detects_orphans(tmp_path):
    """Test that orphaned RelatedEpisode references are detected."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Hypothyroidism,condition,diagnosed,,TSH elevated
2024-01-16,ep-002,Levothyroxine 50mcg,medication,started,ep-999,For hypothyroidism
""")

    errors = validate_related_episodes(timeline)
    assert len(errors) == 1
    assert "ep-999" in errors[0]
    assert "does not exist" in errors[0].lower()


def test_related_episodes_passes_valid_references(tmp_path):
    """Test that valid RelatedEpisode references pass validation."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Hypothyroidism,condition,diagnosed,,TSH elevated
2024-01-16,ep-002,Levothyroxine 50mcg,medication,started,ep-001,For hypothyroidism
""")

    errors = validate_related_episodes(timeline)
    assert len(errors) == 0


def test_related_episodes_handles_empty_references(tmp_path):
    """Test that empty RelatedEpisode fields don't cause errors."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
2024-01-16,ep-002,Omega-3,supplement,started,,Daily
""")

    errors = validate_related_episodes(timeline)
    assert len(errors) == 0


def test_csv_structure_validates_columns(tmp_path):
    """Test that missing columns are detected."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
2024-01-16,ep-002,Omega-3,supplement
""")

    errors = validate_csv_structure(timeline)
    assert len(errors) == 1
    assert "7 columns" in errors[0]


def test_csv_structure_detects_missing_header(tmp_path):
    """Test that missing header is detected."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
""")

    errors = validate_csv_structure(timeline)
    assert len(errors) == 1
    assert "header" in errors[0].lower()


def test_csv_structure_passes_valid_csv(tmp_path):
    """Test that valid CSV structure passes validation."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
2024-01-16,ep-002,Omega-3,supplement,started,,Daily
""")

    errors = validate_csv_structure(timeline)
    assert len(errors) == 0


def test_chronological_order_detects_misordering(tmp_path):
    """Test that out-of-order dates are detected."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-20,ep-001,Vitamin D,supplement,started,,Daily
2024-01-15,ep-002,Omega-3,supplement,started,,Daily
""")

    errors = validate_chronological_order(timeline)
    assert len(errors) == 1
    assert "2024-01-20" in errors[0]
    assert "2024-01-15" in errors[0]


def test_chronological_order_passes_sorted_dates(tmp_path):
    """Test that chronologically sorted dates pass validation."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-001,Vitamin D,supplement,started,,Daily
2024-01-16,ep-002,Omega-3,supplement,started,,Daily
2024-01-20,ep-003,Magnesium,supplement,started,,Daily
""")

    errors = validate_chronological_order(timeline)
    assert len(errors) == 0


def test_comprehensive_stack_detects_missing_stops(tmp_path):
    """Test that comprehensive stack updates without proper stops are detected."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-10,ep-001,Vitamin D,supplement,started,,Daily
2024-01-15,ep-002,Omega-3,supplement,started,,Daily
2024-01-20,ep-003,Magnesium,supplement,started,,Only taking Magnesium now
""")

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    # Create processed entry with comprehensive stack keyword
    (entries_dir / "2024-01-20.processed.md").write_text(
        "Only taking Magnesium now. Stopped everything else."
    )

    errors = validate_comprehensive_stack_updates(timeline, entries_dir)
    # Should detect that Vitamin D and Omega-3 weren't explicitly stopped
    assert len(errors) >= 1
    assert any("Vitamin D" in err or "Omega-3" in err for err in errors)


def test_comprehensive_stack_passes_with_proper_stops(tmp_path):
    """Test that comprehensive stack updates with proper stops pass validation."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-10,ep-001,Vitamin D,supplement,started,,Daily
2024-01-15,ep-002,Omega-3,supplement,started,,Daily
2024-01-20,ep-001,Vitamin D,supplement,stopped,,Stopped
2024-01-20,ep-002,Omega-3,supplement,stopped,,Stopped
2024-01-20,ep-003,Magnesium,supplement,started,,Only taking Magnesium now
""")

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    # Create processed entry with comprehensive stack keyword
    (entries_dir / "2024-01-20.processed.md").write_text(
        "Current stack: Only taking Magnesium now. Stopped everything else."
    )

    errors = validate_comprehensive_stack_updates(timeline, entries_dir)
    # Should pass because all previous items were stopped
    assert len(errors) == 0


def test_comprehensive_stack_passes_with_continuation(tmp_path):
    """Test that comprehensive stack updates pass when items are continued."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-10,ep-001,Vitamin D,supplement,started,,Daily
2024-01-15,ep-002,Omega-3,supplement,started,,Daily
2024-01-20,ep-003,Vitamin D,supplement,started,,Still taking (comprehensive update)
2024-01-20,ep-004,Magnesium,supplement,started,,Added to stack
""")

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    # Create processed entry with comprehensive stack keyword
    (entries_dir / "2024-01-20.processed.md").write_text(
        "Current stack: Vitamin D and Magnesium. Stopped Omega-3."
    )

    # Omega-3 should be flagged, but Vitamin D should pass (continued)
    errors = validate_comprehensive_stack_updates(timeline, entries_dir)
    # This should detect Omega-3 wasn't stopped
    assert len(errors) >= 1
    assert any("Omega-3" in err for err in errors)
    assert not any("Vitamin D" in err for err in errors)


def test_comprehensive_stack_ignores_non_comprehensive_updates(tmp_path):
    """Test that non-comprehensive updates don't trigger validation."""
    timeline = tmp_path / "timeline.csv"
    timeline.write_text("""# Header
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-10,ep-001,Vitamin D,supplement,started,,Daily
2024-01-15,ep-002,Omega-3,supplement,started,,Daily
2024-01-20,ep-003,Magnesium,supplement,started,,Added Magnesium
""")

    entries_dir = tmp_path / "entries"
    entries_dir.mkdir()

    # Create processed entry WITHOUT comprehensive stack keyword
    (entries_dir / "2024-01-20.processed.md").write_text(
        "Added Magnesium to my routine."
    )

    errors = validate_comprehensive_stack_updates(timeline, entries_dir)
    # Should pass because it's not a comprehensive update
    assert len(errors) == 0
