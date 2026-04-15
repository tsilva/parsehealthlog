"""Tests for main.py utility functions."""

import logging
import threading
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from parsehealthlog.config import ProfileConfig
from parsehealthlog.exceptions import ConfigurationError, DateExtractionError
from parsehealthlog.main import (
    DryRunHealthLogProcessor,
    HealthLogProcessor,
    extract_date,
    format_deps_comment,
    format_exam_summary,
    format_labs,
    parse_deps_comment,
    short_hash,
)
from parsehealthlog.main import (
    main as cli_main,
)


class TestExtractDate:
    """Tests for extract_date function."""

    def test_standard_date_format(self):
        """Extract date from standard YYYY-MM-DD format."""
        section = "### 2024-01-15\n\nSome content here"
        assert extract_date(section) == "2024-01-15"

    def test_slash_date_format(self):
        """Extract date from YYYY/MM/DD format."""
        section = "### 2024/03/20\n\nContent"
        assert extract_date(section) == "2024-03-20"

    def test_em_dash_replacement(self):
        """Handle em-dash in date."""
        section = "### 2024—01—15\n\nContent"
        assert extract_date(section) == "2024-01-15"

    def test_en_dash_replacement(self):
        """Handle en-dash in date."""
        section = "### 2024–01–15\n\nContent"
        assert extract_date(section) == "2024-01-15"

    def test_date_with_extra_text(self):
        """Extract date when header has additional text."""
        section = "### 2024-01-15 - Doctor Visit\n\nContent"
        assert extract_date(section) == "2024-01-15"

    def test_whitespace_before_date(self):
        """Handle whitespace before section."""
        section = "   \n\n### 2024-01-15\n\nContent"
        assert extract_date(section) == "2024-01-15"

    def test_empty_section_raises(self):
        """Empty section raises DateExtractionError."""
        with pytest.raises(DateExtractionError, match="empty section"):
            extract_date("")

    def test_whitespace_only_raises(self):
        """Whitespace-only section raises DateExtractionError."""
        with pytest.raises(DateExtractionError, match="empty section"):
            extract_date("   \n\n   ")

    def test_no_date_raises(self):
        """Section without date raises DateExtractionError."""
        with pytest.raises(DateExtractionError, match="No valid date found"):
            extract_date("### Just a title\n\nContent")

    def test_malformed_date_raises(self):
        """Section with malformed date raises DateExtractionError."""
        with pytest.raises(DateExtractionError, match="No valid date found"):
            extract_date("### 2024-99-99\n\nContent")


class TestDependencyTracking:
    """Tests for dependency tracking functions."""

    def test_parse_deps_simple(self):
        """Parse simple dependency comment."""
        line = "<!-- DEPS: raw:abc123,prompt:def456 -->"
        deps = parse_deps_comment(line)
        assert deps == {"raw": "abc123", "prompt": "def456"}

    def test_parse_deps_with_whitespace(self):
        """Parse deps with extra whitespace."""
        line = "<!--  DEPS:  raw:abc ,  prompt:def  -->"
        deps = parse_deps_comment(line)
        assert deps == {"raw": "abc", "prompt": "def"}

    def test_parse_deps_empty_line(self):
        """Empty line returns empty dict."""
        assert parse_deps_comment("") == {}

    def test_parse_deps_no_match(self):
        """Non-matching line returns empty dict."""
        assert parse_deps_comment("# Just a header") == {}
        assert parse_deps_comment("<!-- Not a deps comment -->") == {}

    def test_parse_deps_single(self):
        """Parse single dependency."""
        line = "<!-- DEPS: hash:12345678 -->"
        deps = parse_deps_comment(line)
        assert deps == {"hash": "12345678"}

    def test_format_deps_roundtrip(self):
        """Format and parse should be reversible."""
        original = {"raw": "abc123", "labs": "def456", "prompt": "ghi789"}
        formatted = format_deps_comment(original)
        parsed = parse_deps_comment(formatted)
        assert parsed == original

    def test_format_deps_sorted(self):
        """Dependencies should be sorted alphabetically."""
        deps = {"z_last": "1", "a_first": "2", "m_middle": "3"}
        formatted = format_deps_comment(deps)
        # Should appear in order: a_first, m_middle, z_last
        assert "a_first:2,m_middle:3,z_last:1" in formatted


class TestHashFunctions:
    """Tests for hash utility functions."""

    def test_short_hash_length(self):
        """short_hash returns 12 characters (48 bits for collision resistance)."""
        result = short_hash("test content")
        assert len(result) == 12

    def test_short_hash_deterministic(self):
        """Same input produces same hash."""
        content = "some test content"
        assert short_hash(content) == short_hash(content)

    def test_short_hash_different_inputs(self):
        """Different inputs produce different hashes."""
        assert short_hash("content A") != short_hash("content B")

    def test_short_hash_hex_chars(self):
        """short_hash returns only hex characters."""
        result = short_hash("test")
        assert all(c in "0123456789abcdef" for c in result)


class TestFormatLabs:
    """Tests for lab formatting function."""

    def test_format_labs_basic(self):
        """Format basic lab result with reference range."""
        df = pd.DataFrame({
            "lab_name_standardized": ["Glucose"],
            "value_normalized": [95],
            "unit_normalized": ["mg/dL"],
            "reference_min_normalized": [70],
            "reference_max_normalized": [100],
        })
        result = format_labs(df)
        assert "### Other" in result
        assert "**Glucose:**" in result
        assert "95" in result
        assert "mg/dL" in result
        assert "(ref: 70 - 100)" in result
        # No hardcoded status - LLM applies clinical judgment
        assert "[OK]" not in result
        assert "[BELOW RANGE]" not in result
        assert "[ABOVE RANGE]" not in result

    def test_format_labs_below_range(self):
        """Lab value below range - just shows value and range for LLM interpretation."""
        df = pd.DataFrame({
            "lab_name_standardized": ["Glucose"],
            "value_normalized": [50],
            "unit_normalized": ["mg/dL"],
            "reference_min_normalized": [70],
            "reference_max_normalized": [100],
        })
        result = format_labs(df)
        assert "50" in result
        assert "(ref: 70 - 100)" in result
        # No hardcoded status - LLM applies clinical judgment
        assert "[BELOW RANGE]" not in result

    def test_format_labs_above_range(self):
        """Lab value above range - just shows value and range for LLM interpretation."""
        df = pd.DataFrame({
            "lab_name_standardized": ["Glucose"],
            "value_normalized": [150],
            "unit_normalized": ["mg/dL"],
            "reference_min_normalized": [70],
            "reference_max_normalized": [100],
        })
        result = format_labs(df)
        assert "150" in result
        assert "(ref: 70 - 100)" in result
        # No hardcoded status - LLM applies clinical judgment
        assert "[ABOVE RANGE]" not in result

    def test_format_labs_boolean(self):
        """Boolean lab shows raw value for LLM interpretation."""
        df = pd.DataFrame({
            "lab_name_standardized": ["H. pylori"],
            "value_normalized": ["positive"],
            "unit_normalized": ["boolean"],
            "reference_min_normalized": [0],
            "reference_max_normalized": [0],
        })
        result = format_labs(df)
        assert "**H. pylori:**" in result
        assert "positive" in result
        # LLM interprets the clinical significance

    def test_format_labs_no_range(self):
        """Lab without reference range omits range display."""
        df = pd.DataFrame({
            "lab_name_standardized": ["Vitamin D"],
            "value_normalized": [45],
            "unit_normalized": ["ng/mL"],
            "reference_min_normalized": [None],
            "reference_max_normalized": [None],
        })
        result = format_labs(df)
        assert "**Vitamin D:**" in result
        assert "45" in result
        assert "ref:" not in result

    def test_format_labs_multiple(self):
        """Format grouped lab results with subgroup headings."""
        df = pd.DataFrame({
            "lab_name_standardized": [
                "Blood - Glucose",
                "Blood - HbA1c",
                "Urine Type II - Sediment - Leukocytes",
            ],
            "value_normalized": [95, 5.5, 3.0],
            "unit_normalized": ["mg/dL", "%", "/ul"],
            "reference_min_normalized": [70, 4.0, None],
            "reference_max_normalized": [100, 5.6, None],
        })
        result = format_labs(df)
        assert "### Blood" in result
        assert "#### Sediment" in result
        assert "**Glucose:** 95 mg/dL (ref: 70 - 100)" in result
        assert "**HbA1c:** 5.5 % (ref: 4 - 5.6)" in result
        assert "**Leukocytes:** 3 /ul" in result


class TestFormatExamSummary:
    """Tests for exam formatting helpers."""

    def test_format_exam_summary_strips_front_matter_and_formats_metadata(self):
        """Exam summaries should render title, metadata, and body bullets."""
        content = """---
exam_date: '2025-09-04'
title: Lumbar Spine MRI
doctor: Valentina Ribeiro
facility: Unilabs Clínica Dragão
department: Neurorradiologia
category: imaging
---

Lumbar spine MRI dated 2025-09-04 was performed for persistent low back pain.

Conclusion: No discopathy identified.
"""
        result = format_exam_summary(content)
        assert result.startswith("### Lumbar Spine MRI")
        assert "---" not in result
        assert (
            "- Date: 2025-09-04; Doctor: Valentina Ribeiro; Facility: Unilabs Clínica Dragão; "
            "Department: Neurorradiologia; Category: imaging"
        ) in result
        assert (
            "- Lumbar spine MRI dated 2025-09-04 was performed for persistent low back pain."
            in result
        )
        assert "- Conclusion: No discopathy identified." in result

    def test_format_exam_summary_preserves_existing_lists(self):
        """Existing markdown lists should be preserved under the exam block."""
        content = """---
title: Sleep Study
---

Findings:
- AHI 1.1/h
- Mean SpO2 95%
"""
        result = format_exam_summary(content)
        assert "### Sleep Study" in result
        assert "- Findings:" in result
        assert "- AHI 1.1/h" in result
        assert "- Mean SpO2 95%" in result


class TestCollatedHealthLog:
    """Tests for collated output structure."""

    def test_save_collated_health_log_uses_sectioned_date_blocks(self, tmp_path):
        """Collated log should keep dates top-level and source sections underneath."""
        entries_dir = tmp_path / "entries"
        entries_dir.mkdir()

        (entries_dir / "2025-09-22.processed.md").write_text(
            "<!-- DEPS: raw:a -->\n"
            "## Journal\n\n"
            "- Slept poorly\n\n"
            "## Medical Exams\n\n"
            "### Sleep Study\n\n"
            "- Date: 2025-09-22; Category: other\n\n"
            "- AHI 1.1/h\n",
            encoding="utf-8",
        )
        (entries_dir / "2025-09-08.processed.md").write_text(
            "<!-- DEPS: raw:b -->\n"
            "## Lab Results\n\n"
            "### Blood\n"
            "- **Glucose:** 82 mg/dL\n",
            encoding="utf-8",
        )

        processor = HealthLogProcessor.__new__(HealthLogProcessor)
        processor.OUTPUT_PATH = tmp_path
        processor.entries_dir = entries_dir
        processor.generated_files = set()
        processor._generated_files_lock = threading.Lock()
        processor.logger = logging.getLogger("test.collated")

        processor._save_collated_health_log()

        content = (tmp_path / "health_log.md").read_text(encoding="utf-8")
        assert "# 2025-09-22" in content
        assert "# 2025-09-08" in content
        assert content.index("# 2025-09-22") < content.index("# 2025-09-08")
        assert "## Journal" in content
        assert "## Medical Exams" in content
        assert "## Lab Results" in content
        assert "### Sleep Study" in content
        assert "### Blood" in content


class TestExtractionSummary:
    """Tests for extraction summary output."""

    def test_print_extraction_summary_includes_generated_files(self, capsys):
        """Summary prints generated file paths relative to the output directory."""
        processor = HealthLogProcessor.__new__(HealthLogProcessor)
        processor.OUTPUT_PATH = Path("/tmp/output")
        processor.generated_files = {
            processor.OUTPUT_PATH / "health_log.md",
            processor.OUTPUT_PATH / "entries/2024-01-15.raw.md",
            processor.OUTPUT_PATH / "entries/2024-01-15.processed.md",
        }

        processor._print_extraction_summary(
            {"converted": 1, "deleted": 0, "failed": 0, "total": 1}
        )

        captured = capsys.readouterr().out
        assert "Generated files:" in captured
        assert "entries/2024-01-15.processed.md" in captured
        assert "entries/2024-01-15.raw.md" in captured
        assert "health_log.md" in captured


class TestContentAwareWrites:
    """Tests for content-aware file writes and generated file tracking."""

    def test_write_text_if_changed_skips_unchanged_content(self, tmp_path):
        """Existing files with identical content should not be rewritten or tracked."""
        path = tmp_path / "entries" / "2024-01-15.labs.md"
        path.parent.mkdir()
        path.write_text("same content\n", encoding="utf-8")

        processor = HealthLogProcessor.__new__(HealthLogProcessor)
        processor.generated_files = set()
        processor._generated_files_lock = threading.Lock()

        changed = processor._write_text_if_changed(path, "same content\n")

        assert changed is False
        assert processor.generated_files == set()
        assert path.read_text(encoding="utf-8") == "same content\n"

    def test_write_text_if_changed_tracks_actual_writes(self, tmp_path):
        """New or changed content should be written and included in generated files."""
        path = tmp_path / "entries" / "2024-01-15.labs.md"
        path.parent.mkdir()

        processor = HealthLogProcessor.__new__(HealthLogProcessor)
        processor.generated_files = set()
        processor._generated_files_lock = threading.Lock()

        changed = processor._write_text_if_changed(path, "new content\n")

        assert changed is True
        assert processor.generated_files == {path}
        assert path.read_text(encoding="utf-8") == "new content\n"


class TestInternalValidation:
    def test_validate_date_consistency_surfaces_invalid_sections(self, tmp_path):
        processor = HealthLogProcessor.__new__(HealthLogProcessor)
        processor.entries_dir = tmp_path / "entries"
        processor.entries_dir.mkdir()

        with pytest.raises(DateExtractionError):
            processor._validate_date_consistency(["### invalid header\n\nContent"])


class TestDryRunProcessor:
    def test_placeholder_sections_do_not_write_files_in_dry_run(self, tmp_path):
        processor = DryRunHealthLogProcessor.__new__(DryRunHealthLogProcessor)
        processor.entries_dir = tmp_path / "entries"
        processor.entries_dir.mkdir()
        processor.files_to_create = []
        processor.files_to_modify = []
        processor.labs_by_date = {
            "2024-01-15": pd.DataFrame(
                {
                    "lab_name_standardized": ["Glucose"],
                    "value_normalized": [95],
                    "unit_normalized": ["mg/dL"],
                    "reference_min_normalized": [70],
                    "reference_max_normalized": [100],
                }
            )
        }
        processor.medical_exams_by_date = {}
        processor.logger = logging.getLogger("test.dry-run")
        processor.prompts = {}

        result = processor._create_placeholder_sections([])

        assert result == []
        assert processor.files_to_create == [
            processor.entries_dir / "2024-01-15.processed.md"
        ]
        assert not (processor.entries_dir / "2024-01-15.processed.md").exists()


class TestCliErrors:
    def test_main_reports_configuration_errors(self, capsys):
        profile = ProfileConfig(
            name="test",
            health_log_path=Path("/path/to/log.md"),
            output_path=Path("/path/to/output"),
        )

        with patch(
            "parsehealthlog.main.sys.argv",
            ["parsehealthlog", "--profile", "test"],
        ), patch(
            "parsehealthlog.main.setup_logging"
        ), patch(
            "parsehealthlog.main.ProfileConfig.find_profile_path",
            return_value=Path("/tmp/test.yaml"),
        ), patch(
            "parsehealthlog.main.ProfileConfig.from_file",
            return_value=profile,
        ), patch(
            "parsehealthlog.main.Config.from_profile",
            side_effect=ConfigurationError("Missing API key"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                cli_main()

        assert exc_info.value.code == 1
        assert (
            "Configuration error for profile 'test': Missing API key"
            in capsys.readouterr().out
        )
