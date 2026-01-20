#!/usr/bin/env python3
"""
Phase 2: Data Preservation Analysis

Performs stratified sampling and compares raw.md vs processed.md vs labs.md files
to assess data preservation accuracy.

Checks:
- Lab value preservation
- Medication dosage accuracy
- Clinical context retention
- Provider information completeness
- Date/timing accuracy
- Numeric value preservation

Output:
- JSON with detailed preservation issues
- Markdown report with issue matrix and patterns
"""

import json
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def get_stratified_sample(entries_dir, sample_size=30):
    """
    Get stratified sample of entries across time periods.

    Returns list of (date, raw_file, processed_file, labs_file) tuples.
    """
    # Get all processed files
    processed_files = sorted(list(Path(entries_dir).glob("*.processed.md")))

    if len(processed_files) <= sample_size:
        # Use all files if we have fewer than sample size
        sample_files = processed_files
    else:
        # Stratified sampling: early, middle, recent
        n_early = sample_size // 3
        n_middle = sample_size // 3
        n_recent = sample_size - n_early - n_middle

        early = processed_files[:n_early]
        middle_start = len(processed_files) // 2 - n_middle // 2
        middle = processed_files[middle_start:middle_start + n_middle]
        recent = processed_files[-n_recent:]

        sample_files = early + middle + recent

    # Create tuples with corresponding files
    samples = []
    for processed_file in sample_files:
        date_str = processed_file.stem.replace(".processed", "")
        raw_file = processed_file.parent / f"{date_str}.raw.md"
        labs_file = processed_file.parent / f"{date_str}.labs.md"

        samples.append({
            "date": date_str,
            "raw_file": raw_file if raw_file.exists() else None,
            "processed_file": processed_file,
            "labs_file": labs_file if labs_file.exists() else None
        })

    return samples


def extract_numbers(text):
    """Extract all numbers from text."""
    if not text:
        return set()
    # Match integers and decimals
    return set(re.findall(r'\b\d+\.?\d*\b', text))


def extract_dates(text):
    """Extract date patterns from text."""
    if not text:
        return []
    # Match various date formats
    patterns = [
        r'\d{4}-\d{2}-\d{2}',  # YYYY-MM-DD
        r'\d{2}-\d{2}-\d{2}',  # YY-MM-DD
        r'\d{1,2}/\d{1,2}/\d{2,4}',  # M/D/YY or MM/DD/YYYY
    ]
    dates = []
    for pattern in patterns:
        dates.extend(re.findall(pattern, text))
    return dates


def extract_medications(text):
    """Extract medication dosages (number + unit patterns)."""
    if not text:
        return []
    # Match patterns like "500 mg", "2.5 G", "10ml"
    return re.findall(r'(\d+\.?\d*)\s*(mg|g|ml|ug|mcg|G|Mg)', text, re.IGNORECASE)


def extract_doctors(text):
    """Extract doctor names (capitalized name patterns)."""
    if not text:
        return []
    # Simple heuristic: look for capitalized names
    # More sophisticated pattern: Title + First + Last
    pattern = r'(?:Dr\.?\s+)?([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)'
    return re.findall(pattern, text)


def check_preservation(sample, profile_name):
    """Check data preservation for a single sample."""
    issues = []

    # Read file contents
    raw_content = ""
    processed_content = ""
    labs_content = ""

    if sample["raw_file"] and sample["raw_file"].exists():
        raw_content = sample["raw_file"].read_text()
    if sample["processed_file"].exists():
        processed_content = sample["processed_file"].read_text()
    if sample["labs_file"] and sample["labs_file"].exists():
        labs_content = sample["labs_file"].read_text()

    date = sample["date"]

    # Check numeric values
    raw_numbers = extract_numbers(raw_content)
    processed_numbers = extract_numbers(processed_content)
    labs_numbers = extract_numbers(labs_content)

    missing_numbers = raw_numbers - processed_numbers - labs_numbers
    if missing_numbers and len(missing_numbers) > 0:
        # Filter out year numbers that might be in formatting
        significant_missing = {n for n in missing_numbers if not (len(n) == 4 and n.startswith('20'))}
        if significant_missing:
            issues.append({
                "date": date,
                "profile": profile_name,
                "issue_type": "numeric_value",
                "severity": "critical",
                "description": f"Missing numeric values: {significant_missing}",
                "raw_content": "",
                "processed_content": ""
            })

    # Check medication dosages
    raw_meds = extract_medications(raw_content)
    processed_meds = extract_medications(processed_content)

    if len(raw_meds) != len(processed_meds) and len(raw_meds) > 0:
        issues.append({
            "date": date,
            "profile": profile_name,
            "issue_type": "medication_dosage",
            "severity": "high",
            "description": f"Medication dosages mismatch: {len(raw_meds)} in raw vs {len(processed_meds)} in processed",
            "raw_content": str(raw_meds),
            "processed_content": str(processed_meds)
        })

    # Check lab values if labs file exists
    if labs_content:
        # Significant discrepancy if processed has very different numbers than labs
        unique_processed = set(processed_numbers)
        unique_labs = set(labs_numbers)

        if len(unique_labs) > 5:  # Only check if we have substantial lab data
            overlap = unique_processed & unique_labs
            if len(overlap) < len(unique_labs) * 0.3:  # Less than 30% overlap
                issues.append({
                    "date": date,
                    "profile": profile_name,
                    "issue_type": "lab_value_discrepancy",
                    "severity": "critical",
                    "description": "Significant numeric discrepancies between raw and labs",
                    "raw_content": f"Raw unique values: {sorted(list(raw_numbers))[:10]}",
                    "processed_content": f"Labs unique values: {sorted(list(unique_labs))[:10]}"
                })

    # Check dates
    raw_dates = extract_dates(raw_content)
    processed_dates = extract_dates(processed_content)

    if len(raw_dates) != len(processed_dates):
        issues.append({
            "date": date,
            "profile": profile_name,
            "issue_type": "date_timing",
            "severity": "medium",
            "description": f"Date count mismatch: {len(raw_dates)} in raw vs {len(processed_dates)} in processed",
            "raw_content": str(raw_dates),
            "processed_content": str(processed_dates)
        })

    # Check doctor names
    raw_doctors = extract_doctors(raw_content)
    processed_doctors = extract_doctors(processed_content)

    if set(raw_doctors) != set(processed_doctors):
        issues.append({
            "date": date,
            "profile": profile_name,
            "issue_type": "provider_info",
            "severity": "medium",
            "description": "Doctor name mismatch",
            "raw_content": str(raw_doctors),
            "processed_content": str(processed_doctors)
        })

    # Check for uncertainty markers (?, "possibly", "maybe", "uncertain")
    uncertainty_markers = ["?", "possibly", "maybe", "uncertain", "unclear", "suspected"]
    raw_has_uncertainty = any(marker in raw_content.lower() for marker in uncertainty_markers)
    processed_has_uncertainty = any(marker in processed_content.lower() for marker in uncertainty_markers)

    if raw_has_uncertainty and not processed_has_uncertainty:
        issues.append({
            "date": date,
            "profile": profile_name,
            "issue_type": "clinical_context",
            "severity": "high",
            "description": "Uncertainty markers lost in processing",
            "raw_content": "Contains uncertainty markers",
            "processed_content": "No uncertainty markers found"
        })

    return issues


def generate_preservation_report(all_issues, output_dir):
    """Generate markdown preservation report."""
    report = []
    report.append("# Phase 2: Data Preservation Analysis Report\n")
    report.append("## Overview\n")
    report.append("This report assesses data preservation accuracy by comparing raw.md vs processed.md vs labs.md files.\n")

    # Group issues by profile and type
    tiago_issues = [i for i in all_issues if i["profile"] == "Tiago"]
    cristina_issues = [i for i in all_issues if i["profile"] == "Cristina"]

    for profile_name, issues in [("Tiago", tiago_issues), ("Cristina", cristina_issues)]:
        report.append(f"\n## {profile_name} Profile\n")
        report.append(f"**Total Issues:** {len(issues)}\n")

        # Count by severity
        by_severity = defaultdict(int)
        for issue in issues:
            by_severity[issue["severity"]] += 1

        report.append(f"- Critical: {by_severity['critical']}\n")
        report.append(f"- High: {by_severity['high']}\n")
        report.append(f"- Medium: {by_severity['medium']}\n")
        report.append(f"- Low: {by_severity['low']}\n")

        # Count by type
        by_type = defaultdict(int)
        for issue in issues:
            by_type[issue["issue_type"]] += 1

        report.append("\n### Issues by Type\n")
        for issue_type, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            report.append(f"- **{issue_type.replace('_', ' ').title()}**: {count}\n")

        # Show sample issues
        report.append("\n### Sample Issues\n")
        for issue_type in list(by_type.keys())[:3]:  # Top 3 types
            type_issues = [i for i in issues if i["issue_type"] == issue_type]
            report.append(f"\n#### {issue_type.replace('_', ' ').title()}\n")
            for issue in type_issues[:3]:  # First 3 of each type
                report.append(f"- **{issue['date']}**: {issue['description']}\n")

    # Write report
    report_path = output_dir / "phase2_data_preservation.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return report_path


def run_phase2(tiago_path, cristina_path, output_dir, verbose=False):
    """
    Run Phase 2: Data Preservation Analysis

    Args:
        tiago_path: Path to Tiago's output directory
        cristina_path: Path to Cristina's output directory
        output_dir: Directory for output reports
        verbose: Enable verbose output

    Returns:
        Dict with preservation analysis results
    """
    if verbose:
        print("Running data preservation analysis...")

    all_issues = []

    # Analyze Tiago profile
    if verbose:
        print("  Analyzing Tiago profile...")
    tiago_entries = Path(tiago_path) / "entries"
    tiago_samples = get_stratified_sample(tiago_entries, sample_size=30)

    for sample in tiago_samples:
        issues = check_preservation(sample, "Tiago")
        all_issues.extend(issues)

    # Analyze Cristina profile
    if verbose:
        print("  Analyzing Cristina profile...")
    cristina_entries = Path(cristina_path) / "entries"
    cristina_samples = get_stratified_sample(cristina_entries, sample_size=30)

    for sample in cristina_samples:
        issues = check_preservation(sample, "Cristina")
        all_issues.extend(issues)

    # Save JSON results
    results = {
        "total_issues": len(all_issues),
        "tiago_issues": len([i for i in all_issues if i["profile"] == "Tiago"]),
        "cristina_issues": len([i for i in all_issues if i["profile"] == "Cristina"]),
        "issues": all_issues
    }

    json_path = Path(output_dir) / "phase2_preservation.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    if verbose:
        print(f"  JSON results saved to: {json_path}")

    # Generate markdown report
    report_path = generate_preservation_report(all_issues, Path(output_dir))

    if verbose:
        print(f"  Markdown report saved to: {report_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 2: Data Preservation Analysis")
    parser.add_argument("--tiago-path", required=True, type=Path)
    parser.add_argument("--cristina-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = run_phase2(args.tiago_path, args.cristina_path, args.output_dir, args.verbose)

    print(f"\nData Preservation Issues:")
    print(f"  Total:    {results['total_issues']}")
    print(f"  Tiago:    {results['tiago_issues']}")
    print(f"  Cristina: {results['cristina_issues']}")
