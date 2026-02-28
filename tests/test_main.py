"""Tests for main.py utility functions."""

import pytest
import pandas as pd

from parsehealthlog.main import (
    extract_date,
    parse_deps_comment,
    format_deps_comment,
    short_hash,
    format_labs,
)
from parsehealthlog.exceptions import DateExtractionError


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
        """Format multiple lab results."""
        df = pd.DataFrame({
            "lab_name_standardized": ["Glucose", "HbA1c"],
            "value_normalized": [95, 5.5],
            "unit_normalized": ["mg/dL", "%"],
            "reference_min_normalized": [70, 4.0],
            "reference_max_normalized": [100, 5.6],
        })
        result = format_labs(df)
        lines = result.strip().split("\n")
        assert len(lines) == 2
        assert "Glucose" in lines[0]
        assert "HbA1c" in lines[1]
