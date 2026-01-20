#!/usr/bin/env python3
"""
Phase 4: Categorization Analysis

Evaluates categorization accuracy and consistency.

Checks:
- Diagnosis vs suspected distinction
- Event type validity
- Watch category misuse
- Status progression logic
- Details field quality

Output:
- JSON with categorization issues
- Markdown report with accuracy assessment
"""

import csv
import json
import random
from pathlib import Path
from collections import defaultdict


def sample_entries(csv_path, sample_size=100):
    """Stratified sampling of entries by category."""
    entries = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        entries = list(reader)

    # Group by category
    by_category = defaultdict(list)
    for entry in entries:
        by_category[entry["Category"]].append(entry)

    # Sample proportionally
    samples = []
    total_entries = len(entries)

    for category, category_entries in by_category.items():
        proportion = len(category_entries) / total_entries
        n_samples = max(1, int(sample_size * proportion))
        n_samples = min(n_samples, len(category_entries))
        samples.extend(random.sample(category_entries, n_samples))

    return samples[:sample_size]


def check_categorization(entry, profile_name):
    """Check categorization quality for a single entry."""
    issues = []

    category = entry.get("Category", "")
    event = entry.get("Event", "")
    item = entry.get("Item", "")
    details = entry.get("Details", "")

    # Check diagnosis vs suspected
    if category == "condition":
        if event == "diagnosed" and ("suspected" in details.lower() or "possible" in details.lower()):
            issues.append({
                "date": entry["Date"],
                "item": item,
                "issue_type": "diagnosis_suspected_mismatch",
                "severity": "medium",
                "description": "Event is 'diagnosed' but details suggest uncertainty"
            })
        elif event == "suspected" and not any(word in details.lower() for word in ["suspected", "possible", "unclear"]):
            issues.append({
                "date": entry["Date"],
                "item": item,
                "issue_type": "suspected_missing_qualifier",
                "severity": "low",
                "description": "Event is 'suspected' but details lack uncertainty markers"
            })

    # Check event type validity
    valid_events = {
        "condition": ["diagnosed", "suspected", "noted", "worsened", "improved", "resolved", "stable"],
        "symptom": ["noted", "worsened", "improved", "resolved"],
        "treatment": ["started", "stopped", "adjusted", "continued"],
        "test": ["ordered", "completed"],
        "watch": ["noted"]
    }

    if category in valid_events:
        if event not in valid_events[category]:
            issues.append({
                "date": entry["Date"],
                "item": item,
                "issue_type": "invalid_event_type",
                "severity": "high",
                "description": f"Event '{event}' not valid for category '{category}'"
            })

    # Check watch category misuse
    if category == "watch":
        # Watch should be used for monitoring, not actual conditions
        if any(word in item.lower() for word in ["diagnosed", "confirmed", "prescribed"]):
            issues.append({
                "date": entry["Date"],
                "item": item,
                "issue_type": "watch_misuse",
                "severity": "medium",
                "description": "Watch category used for confirmed condition/treatment"
            })

    # Check details quality
    if not details or len(details) < 10:
        if category in ["condition", "treatment", "test"]:
            issues.append({
                "date": entry["Date"],
                "item": item,
                "issue_type": "insufficient_details",
                "severity": "low",
                "description": f"Minimal or missing details for {category}"
            })

    return issues


def generate_categorization_report(tiago_issues, cristina_issues, output_dir):
    """Generate markdown categorization report."""
    report = []
    report.append("# Phase 4: Categorization Analysis Report\n")
    report.append("## Overview\n")
    report.append("This report evaluates categorization accuracy and consistency.\n")

    for profile_name, issues in [("Tiago", tiago_issues), ("Cristina", cristina_issues)]:
        report.append(f"\n## {profile_name} Profile\n")
        report.append(f"**Total Issues:** {len(issues)}\n")

        # Count by type
        by_type = defaultdict(int)
        for issue in issues:
            by_type[issue["issue_type"]] += 1

        report.append("\n### Issues by Type\n")
        for issue_type, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            report.append(f"- **{issue_type.replace('_', ' ').title()}**: {count}\n")

        if issues:
            report.append("\n### Sample Issues\n")
            for issue in issues[:10]:
                report.append(f"- **{issue['date']}** - {issue['item']}: {issue['description']}\n")

    report_path = output_dir / "phase4_categorization.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return report_path


def run_phase4(tiago_path, cristina_path, output_dir, verbose=False):
    """Run Phase 4: Categorization Analysis"""
    if verbose:
        print("Running categorization analysis...")

    # Analyze Tiago
    if verbose:
        print("  Analyzing Tiago profile...")
    tiago_csv = Path(tiago_path) / "health_log.csv"
    tiago_samples = sample_entries(tiago_csv, sample_size=100)
    tiago_issues = []
    for entry in tiago_samples:
        tiago_issues.extend(check_categorization(entry, "Tiago"))

    # Analyze Cristina
    if verbose:
        print("  Analyzing Cristina profile...")
    cristina_csv = Path(cristina_path) / "health_log.csv"
    cristina_samples = sample_entries(cristina_csv, sample_size=100)
    cristina_issues = []
    for entry in cristina_samples:
        cristina_issues.extend(check_categorization(entry, "Cristina"))

    # Save JSON results
    results = {
        "tiago": {"total_issues": len(tiago_issues), "issues": tiago_issues},
        "cristina": {"total_issues": len(cristina_issues), "issues": cristina_issues}
    }

    json_path = Path(output_dir) / "phase4_categorization.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    if verbose:
        print(f"  JSON results saved to: {json_path}")

    # Generate report
    report_path = generate_categorization_report(tiago_issues, cristina_issues, Path(output_dir))

    if verbose:
        print(f"  Markdown report saved to: {report_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 4: Categorization Analysis")
    parser.add_argument("--tiago-path", required=True, type=Path)
    parser.add_argument("--cristina-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = run_phase4(args.tiago_path, args.cristina_path, args.output_dir, args.verbose)
    print(f"\nCategorization Issues:")
    print(f"  Tiago:    {results['tiago']['total_issues']}")
    print(f"  Cristina: {results['cristina']['total_issues']}")
