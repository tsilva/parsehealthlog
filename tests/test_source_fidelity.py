"""Regressions for lab import fidelity and repeated journal dates."""

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from parsehealthlog.main import HealthLogProcessor, format_labs


def processor(tmp_path):
    obj = object.__new__(HealthLogProcessor)
    obj.path = tmp_path / "journal.md"
    obj.logger = logging.getLogger(__name__)
    obj._generated_files_lock = __import__("threading").Lock()
    obj.generated_files = set()
    obj.config = SimpleNamespace(labs_parser_output_path=tmp_path / "labs")
    obj.config.labs_parser_output_path.mkdir()
    return obj


def test_current_export_keeps_canonical_value_unit_and_comparator(tmp_path):
    obj = processor(tmp_path)
    pd.DataFrame(
        [
            {
                "date": "2024-01-15",
                "lab_name": "Blood - CRP",
                "lab_name_standardized": "Blood - CRP",
                "value": 18.8,
                "lab_unit": "mg/L",
                "lab_unit_standardized": "mg/dL",
                "reference_min": 0,
                "reference_max": 5,
                "raw_value": "<1.88",
                "is_below_limit": True,
            }
        ]
    ).to_csv(obj.config.labs_parser_output_path / "all.csv", index=False)
    obj._load_labs()
    text = format_labs(obj.labs_by_date["2024-01-15"])
    assert "<18.8 mg/L" in text
    assert "mg/dL" not in text
    assert "ref: 0 - 5" in text


def test_mixed_csv_schemas_are_normalized_before_concatenating(tmp_path):
    obj = processor(tmp_path)
    pd.DataFrame(
        [
            {
                "date": "2024-01-15",
                "lab_name_enum": "Blood - Legacy",
                "lab_value_final": 3,
                "lab_unit_final": "mg/dL",
                "lab_range_min_final": 1,
                "lab_range_max_final": 4,
            }
        ]
    ).to_csv(tmp_path / "labs.csv", index=False)
    pd.DataFrame(
        [
            {
                "date": "2024-01-15",
                "lab_name": "Blood - Current",
                "value": 20,
                "lab_unit": "mg/L",
                "reference_min": 1,
                "reference_max": 30,
            }
        ]
    ).to_csv(obj.config.labs_parser_output_path / "all.csv", index=False)
    obj._load_labs()
    text = format_labs(obj.labs_by_date["2024-01-15"])
    assert "3 mg/dL" in text
    assert "20 mg/L" in text


def test_duplicate_day_preserves_both_sections_in_source_order(tmp_path):
    obj = processor(tmp_path)
    source = (
        "### 2024-01-15\n\nFirst event.\n\n### 2024-01-16\n\nNext day.\n\n"
        "### 2024-01-15\n\nSecond event.\n"
    )
    obj.path.write_text(source)
    sections = obj._split_sections()
    assert len(sections) == 2
    assert "First event." in sections[0] and "Second event." in sections[0]
    assert sections[0].index("First event.") < sections[0].index("Second event.")
    assert obj.path.read_text() == source


def test_failed_curation_preserves_doses_and_historical_dates(tmp_path):
    obj = processor(tmp_path)
    obj.entries_dir = tmp_path / "entries"
    obj.entries_dir.mkdir()
    raw = "Visit was on 2023-12-20. Took 7.5 mg at bedtime.\n## Medical Exams\nResult negative."
    plan = HealthLogProcessor.EntryPlan(
        "2024-01-15",
        raw,
        obj.entries_dir / "2024-01-15.raw.md",
        obj.entries_dir / "2024-01-15.processed.md",
        "",
        "",
        {},
    )
    obj._build_entry_plan = Mock(return_value=plan)
    obj._prompt = Mock(return_value="{raw_section} {processed_section}")
    obj.llm = {
        "process": Mock(return_value="Omitted dose."),
        "validate": Mock(return_value="$FAILED$"),
    }
    assert obj._process_section("synthetic") == ("2024-01-15", False)
    text = plan.processed_path.read_text()
    assert "Original journal (verbatim)" in text
    assert "7.5 mg" in text and "2023-12-20" in text and "> ## Medical Exams" in text
    assert "Omitted dose" not in text


def test_evidence_refresh_reuses_validated_journal_and_removes_stale_insert(tmp_path):
    from parsehealthlog.main import format_deps_comment

    obj = processor(tmp_path)
    obj.entries_dir = tmp_path / "entries"
    obj.entries_dir.mkdir()
    deps = {"raw": "same", "process_prompt": "same", "validate_prompt": "same", "labs": "new"}
    plan = HealthLogProcessor.EntryPlan(
        "2024-01-15",
        "source",
        obj.entries_dir / "raw.md",
        obj.entries_dir / "processed.md",
        "## Lab Results\n\nNew canonical value.",
        "",
        deps,
    )
    previous = {**deps, "labs": "old"}
    plan.processed_path.write_text(
        format_deps_comment(previous)
        + "\n## Journal\n\nValidated dose 7.5 mg.\n\n## Lab Results\n\nWrong old unit."
    )
    obj._build_entry_plan = Mock(return_value=plan)
    obj.llm = {"process": Mock(side_effect=AssertionError("Must reuse validated journal"))}
    assert obj._process_section("synthetic")[1]
    text = plan.processed_path.read_text()
    assert "Validated dose 7.5 mg." in text and "New canonical value." in text
    assert "Wrong old unit" not in text


def test_exam_metadata_date_overrides_directory_and_null_stays_undated(tmp_path):
    obj = processor(tmp_path)
    obj.config.medical_exams_parser_output_path = tmp_path / "exams"
    for dirname, date in [
        ("2024-06-12 - synthetic", "2024-04-03"),
        ("2024-01-15 - undated", "null"),
    ]:
        directory = obj.config.medical_exams_parser_output_path / dirname
        directory.mkdir(parents=True)
        (directory / "synthetic.summary.md").write_text(
            f"---\nexam_date: {date}\ntitle: Synthetic\n---\nSummary text."
        )
    obj._load_medical_exams()
    assert set(obj.medical_exams_by_date) == {"2024-04-03"}
