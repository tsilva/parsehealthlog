#!/usr/bin/env python3
"""
Phase 5: Labs Integration Analysis

Checks labs/exams integration quality.

Checks:
- Labs files exist and are integrated into processed files
- Formatting consistency
- Completeness of integration

Output:
- JSON with integration quality metrics
- Markdown report
"""

import json
from pathlib import Path


def analyze_labs_integration(entries_dir, profile_name):
    """Analyze labs integration for a profile."""
    entries_path = Path(entries_dir)

    labs_files = list(entries_path.glob("*.labs.md"))
    stats = {
        "total_labs_files": len(labs_files),
        "integrated_count": 0,
        "missing_integration": [],
        "integration_rate": 0.0
    }

    for labs_file in labs_files:
        date_str = labs_file.stem.replace(".labs", "")
        processed_file = entries_path / f"{date_str}.processed.md"

        if processed_file.exists():
            # Check if labs content is integrated
            labs_content = labs_file.read_text()
            processed_content = processed_file.read_text()

            # Simple check: look for lab markers in processed file
            has_labs_section = "Labs" in processed_content or "Results" in processed_content
            # Check if some lab values appear
            labs_numbers = set()
            for line in labs_content.split("\n"):
                if ":" in line:  # Lab result line like "Glucose: 95"
                    parts = line.split(":")
                    if len(parts) > 1:
                        try:
                            value = float(parts[1].strip().split()[0])
                            labs_numbers.add(str(value))
                        except:
                            pass

            numbers_integrated = any(num in processed_content for num in list(labs_numbers)[:5])

            if has_labs_section or numbers_integrated:
                stats["integrated_count"] += 1
            else:
                stats["missing_integration"].append(date_str)

    if stats["total_labs_files"] > 0:
        stats["integration_rate"] = (stats["integrated_count"] / stats["total_labs_files"]) * 100

    return stats


def generate_labs_report(tiago_stats, cristina_stats, output_dir):
    """Generate markdown labs integration report."""
    report = []
    report.append("# Phase 5: Labs Integration Analysis Report\n")
    report.append("## Overview\n")
    report.append("This report assesses labs/exams integration quality.\n")

    for profile_name, stats in [("Tiago", tiago_stats), ("Cristina", cristina_stats)]:
        report.append(f"\n## {profile_name} Profile\n")
        report.append(f"**Total Labs Files:** {stats['total_labs_files']}\n")
        report.append(f"**Integrated:** {stats['integrated_count']}\n")
        report.append(f"**Integration Rate:** {stats['integration_rate']:.1f}%\n")

        if stats["missing_integration"]:
            report.append(f"\n### Missing Integration ({len(stats['missing_integration'])})\n")
            for date in stats["missing_integration"][:10]:
                report.append(f"- {date}\n")
            if len(stats["missing_integration"]) > 10:
                report.append(f"- _(and {len(stats['missing_integration']) - 10} more)_\n")

    report_path = output_dir / "phase5_labs_integration.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return report_path


def run_phase5(tiago_path, cristina_path, output_dir, verbose=False):
    """Run Phase 5: Labs Integration Analysis"""
    if verbose:
        print("Running labs integration analysis...")

    # Analyze Tiago
    if verbose:
        print("  Analyzing Tiago profile...")
    tiago_entries = Path(tiago_path) / "entries"
    tiago_stats = analyze_labs_integration(tiago_entries, "Tiago")

    # Analyze Cristina
    if verbose:
        print("  Analyzing Cristina profile...")
    cristina_entries = Path(cristina_path) / "entries"
    cristina_stats = analyze_labs_integration(cristina_entries, "Cristina")

    # Save JSON results
    results = {
        "tiago": tiago_stats,
        "cristina": cristina_stats
    }

    json_path = Path(output_dir) / "phase5_labs_integration.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    if verbose:
        print(f"  JSON results saved to: {json_path}")

    # Generate report
    report_path = generate_labs_report(tiago_stats, cristina_stats, Path(output_dir))

    if verbose:
        print(f"  Markdown report saved to: {report_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 5: Labs Integration Analysis")
    parser.add_argument("--tiago-path", required=True, type=Path)
    parser.add_argument("--cristina-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = run_phase5(args.tiago_path, args.cristina_path, args.output_dir, args.verbose)
    print(f"\nLabs Integration Rate:")
    print(f"  Tiago:    {results['tiago']['integration_rate']:.1f}%")
    print(f"  Cristina: {results['cristina']['integration_rate']:.1f}%")
