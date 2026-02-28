#!/usr/bin/env python3
"""
Phase 1: Validation Analysis

Runs validate_timeline.py on both profiles and assesses validation compliance.

Checks:
- Episode continuity (no gaps in episode IDs)
- Related episodes validity (no orphaned references)
- CSV structure compliance
- Chronological ordering
- Comprehensive stack updates

Output:
- JSON with validation results for both profiles
- Markdown report with compliance assessment
"""

import json
import subprocess
import sys
from pathlib import Path
from collections import defaultdict


def run_validate_timeline(csv_path, entries_path):
    """Run validate_timeline.py checks and capture results."""
    # Import validate_timeline functions
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from validate_timeline import run_all_validations
    except ImportError:
        raise ImportError("Could not import validate_timeline.py - ensure it exists in parsehealthlog root")

    # Run all validations
    results = run_all_validations(str(csv_path), str(entries_path))

    # Categorize issues by severity
    categorized = {
        "episode_continuity": [],
        "related_episodes": [],
        "csv_structure": [],
        "chronological_order": [],
        "comprehensive_stack": []
    }

    for issue in results.get("issues", []):
        issue_type = issue.get("type", "unknown")
        if "episode" in issue_type.lower() and "gap" in issue.get("message", "").lower():
            categorized["episode_continuity"].append(issue)
        elif "related" in issue_type.lower() or "orphaned" in issue.get("message", "").lower():
            categorized["related_episodes"].append(issue)
        elif "csv" in issue_type.lower() or "column" in issue.get("message", "").lower():
            categorized["csv_structure"].append(issue)
        elif "order" in issue_type.lower() or "chronological" in issue.get("message", "").lower():
            categorized["chronological_order"].append(issue)
        elif "stack" in issue.get("message", "").lower() or "comprehensive" in issue.get("message", "").lower():
            categorized["comprehensive_stack"].append(issue)
        else:
            # Uncategorized - add to appropriate bucket based on context
            categorized.setdefault("other", []).append(issue)

    return {
        "total_issues": len(results.get("issues", [])),
        "total_warnings": len(results.get("warnings", [])),
        "by_category": categorized,
        "raw_results": results
    }


def calculate_compliance_score(results):
    """Calculate validation compliance score (0-100)."""
    # Start with perfect score
    score = 100.0

    # Deduct points for issues
    issues = results["total_issues"]
    warnings = results["total_warnings"]

    # Critical deductions (10 points each)
    score -= results["by_category"]["episode_continuity"].__len__() * 10
    score -= results["by_category"]["related_episodes"].__len__() * 10
    score -= results["by_category"]["csv_structure"].__len__() * 10

    # Medium deductions (5 points each)
    score -= results["by_category"]["chronological_order"].__len__() * 5
    score -= results["by_category"]["comprehensive_stack"].__len__() * 5

    # Warning deductions (1 point each)
    score -= warnings * 1

    return max(0.0, score)


def generate_validation_report(tiago_results, cristina_results, output_dir):
    """Generate markdown validation report."""
    report = []
    report.append("# Phase 1: Validation Analysis Report\n")
    report.append("## Overview\n")
    report.append("This report assesses compliance with validation checks from validate_timeline.py.\n")

    # Tiago results
    report.append("## Tiago Profile\n")
    report.append(f"**Validation Score:** {calculate_compliance_score(tiago_results):.1f}/100\n")
    report.append(f"**Total Issues:** {tiago_results['total_issues']}\n")
    report.append(f"**Total Warnings:** {tiago_results['total_warnings']}\n")
    report.append("\n### Issues by Category\n")

    for category, issues in tiago_results["by_category"].items():
        if issues:
            report.append(f"\n#### {category.replace('_', ' ').title()} ({len(issues)})\n")
            for issue in issues[:10]:  # Limit to first 10
                report.append(f"- {issue.get('message', 'No message')}\n")
            if len(issues) > 10:
                report.append(f"- _(and {len(issues) - 10} more)_\n")

    # Cristina results
    report.append("\n## Cristina Profile\n")
    report.append(f"**Validation Score:** {calculate_compliance_score(cristina_results):.1f}/100\n")
    report.append(f"**Total Issues:** {cristina_results['total_issues']}\n")
    report.append(f"**Total Warnings:** {cristina_results['total_warnings']}\n")
    report.append("\n### Issues by Category\n")

    for category, issues in cristina_results["by_category"].items():
        if issues:
            report.append(f"\n#### {category.replace('_', ' ').title()} ({len(issues)})\n")
            for issue in issues[:10]:  # Limit to first 10
                report.append(f"- {issue.get('message', 'No message')}\n")
            if len(issues) > 10:
                report.append(f"- _(and {len(issues) - 10} more)_\n")

    # Summary
    report.append("\n## Summary\n")
    tiago_score = calculate_compliance_score(tiago_results)
    cristina_score = calculate_compliance_score(cristina_results)

    if tiago_score >= 90 and cristina_score >= 90:
        report.append("✅ **Excellent** - Both profiles pass validation with minor or no issues.\n")
    elif tiago_score >= 70 and cristina_score >= 70:
        report.append("⚠️ **Good** - Both profiles have some validation issues that should be addressed.\n")
    else:
        report.append("❌ **Needs Improvement** - Significant validation issues detected.\n")

    # Write report
    report_path = output_dir / "phase1_validation.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return report_path


def run_phase1(tiago_path, cristina_path, output_dir, verbose=False):
    """
    Run Phase 1: Validation Analysis

    Args:
        tiago_path: Path to Tiago's output directory
        cristina_path: Path to Cristina's output directory
        output_dir: Directory for output reports
        verbose: Enable verbose output

    Returns:
        Dict with validation results for both profiles
    """
    if verbose:
        print("Running validation analysis...")

    # Run validation for Tiago
    if verbose:
        print("  Analyzing Tiago profile...")
    tiago_csv = Path(tiago_path) / "health_log.csv"
    tiago_entries = Path(tiago_path) / "entries"
    tiago_results = run_validate_timeline(tiago_csv, tiago_entries)

    # Run validation for Cristina
    if verbose:
        print("  Analyzing Cristina profile...")
    cristina_csv = Path(cristina_path) / "health_log.csv"
    cristina_entries = Path(cristina_path) / "entries"
    cristina_results = run_validate_timeline(cristina_csv, cristina_entries)

    # Save JSON results
    results = {
        "tiago": tiago_results,
        "cristina": cristina_results,
        "scores": {
            "tiago": calculate_compliance_score(tiago_results),
            "cristina": calculate_compliance_score(cristina_results)
        }
    }

    json_path = Path(output_dir) / "phase1_validation.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    if verbose:
        print(f"  JSON results saved to: {json_path}")

    # Generate markdown report
    report_path = generate_validation_report(tiago_results, cristina_results, Path(output_dir))

    if verbose:
        print(f"  Markdown report saved to: {report_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 1: Validation Analysis")
    parser.add_argument("--tiago-path", required=True, type=Path)
    parser.add_argument("--cristina-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = run_phase1(args.tiago_path, args.cristina_path, args.output_dir, args.verbose)

    print(f"\nValidation Scores:")
    print(f"  Tiago:    {results['scores']['tiago']:.1f}/100")
    print(f"  Cristina: {results['scores']['cristina']:.1f}/100")
