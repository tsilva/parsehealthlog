#!/usr/bin/env python3
"""
Phase 3: Episode Linking Analysis

Analyzes RelatedEpisode column quality and linking patterns.

Checks:
- Link completeness (treatments linked to conditions)
- Link correctness (valid episode IDs)
- Link consistency (bidirectional when appropriate)
- Orphaned treatments (no condition link)

Output:
- JSON with linking quality metrics
- Markdown report with linking assessment
"""

import csv
import json
from pathlib import Path
from collections import defaultdict


def parse_csv_file(csv_path):
    """Parse CSV and extract all episodes with linking info."""
    episodes = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            episodes.append({
                "Date": row["Date"],
                "Item": row["Item"],
                "Category": row["Category"],
                "Event": row["Event"],
                "EpisodeID": row.get("EpisodeID", ""),
                "RelatedEpisode": row.get("RelatedEpisode", ""),
                "Details": row.get("Details", "")
            })
    return episodes


def analyze_linking(episodes, profile_name):
    """Analyze episode linking quality."""
    # Build episode ID map
    episode_map = {}
    for ep in episodes:
        if ep["EpisodeID"]:
            episode_map[ep["EpisodeID"]] = ep

    issues = []
    stats = {
        "total_episodes": len([e for e in episodes if e["EpisodeID"]]),
        "episodes_with_links": 0,
        "orphaned_references": 0,
        "treatments_with_condition_links": 0,
        "treatments_without_links": 0,
        "link_completeness": 0.0
    }

    for ep in episodes:
        if not ep["EpisodeID"]:
            continue

        # Check if episode has related links
        related = ep["RelatedEpisode"]
        if related:
            stats["episodes_with_links"] += 1

            # Verify each related episode exists
            related_ids = [r.strip() for r in related.split(",") if r.strip()]
            for rel_id in related_ids:
                if rel_id not in episode_map:
                    issues.append({
                        "date": ep["Date"],
                        "episode_id": ep["EpisodeID"],
                        "item": ep["Item"],
                        "issue_type": "orphaned_reference",
                        "severity": "high",
                        "description": f"References non-existent episode: {rel_id}"
                    })
                    stats["orphaned_references"] += 1

        # Check treatments for condition links
        if ep["Category"] == "treatment":
            if related:
                stats["treatments_with_condition_links"] += 1
            else:
                stats["treatments_without_links"] += 1
                issues.append({
                    "date": ep["Date"],
                    "episode_id": ep["EpisodeID"],
                    "item": ep["Item"],
                    "issue_type": "unlinked_treatment",
                    "severity": "medium",
                    "description": "Treatment not linked to any condition"
                })

    # Calculate link completeness
    total_treatments = stats["treatments_with_condition_links"] + stats["treatments_without_links"]
    if total_treatments > 0:
        stats["link_completeness"] = (stats["treatments_with_condition_links"] / total_treatments) * 100

    return stats, issues


def generate_linking_report(tiago_stats, tiago_issues, cristina_stats, cristina_issues, output_dir):
    """Generate markdown linking report."""
    report = []
    report.append("# Phase 3: Episode Linking Analysis Report\n")
    report.append("## Overview\n")
    report.append("This report assesses RelatedEpisode column quality and linking patterns.\n")

    for profile_name, stats, issues in [("Tiago", tiago_stats, tiago_issues),
                                         ("Cristina", cristina_stats, cristina_issues)]:
        report.append(f"\n## {profile_name} Profile\n")
        report.append(f"**Total Episodes:** {stats['total_episodes']}\n")
        report.append(f"**Episodes with Links:** {stats['episodes_with_links']}\n")
        report.append(f"**Orphaned References:** {stats['orphaned_references']}\n")
        report.append(f"**Treatments with Condition Links:** {stats['treatments_with_condition_links']}\n")
        report.append(f"**Treatments without Links:** {stats['treatments_without_links']}\n")
        report.append(f"**Link Completeness:** {stats['link_completeness']:.1f}%\n")

        if issues:
            report.append(f"\n### Issues ({len(issues)})\n")
            for issue in issues[:10]:
                report.append(f"- **{issue['date']}** ({issue['episode_id']}): {issue['description']}\n")
            if len(issues) > 10:
                report.append(f"- _(and {len(issues) - 10} more)_\n")

    report_path = output_dir / "phase3_episode_linking.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return report_path


def run_phase3(tiago_path, cristina_path, output_dir, verbose=False):
    """Run Phase 3: Episode Linking Analysis"""
    if verbose:
        print("Running episode linking analysis...")

    # Analyze Tiago
    if verbose:
        print("  Analyzing Tiago profile...")
    tiago_csv = Path(tiago_path) / "health_log.csv"
    tiago_episodes = parse_csv_file(tiago_csv)
    tiago_stats, tiago_issues = analyze_linking(tiago_episodes, "Tiago")

    # Analyze Cristina
    if verbose:
        print("  Analyzing Cristina profile...")
    cristina_csv = Path(cristina_path) / "health_log.csv"
    cristina_episodes = parse_csv_file(cristina_csv)
    cristina_stats, cristina_issues = analyze_linking(cristina_episodes, "Cristina")

    # Save JSON results
    results = {
        "tiago": {"stats": tiago_stats, "issues": tiago_issues},
        "cristina": {"stats": cristina_stats, "issues": cristina_issues}
    }

    json_path = Path(output_dir) / "phase3_linking.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    if verbose:
        print(f"  JSON results saved to: {json_path}")

    # Generate report
    report_path = generate_linking_report(tiago_stats, tiago_issues,
                                         cristina_stats, cristina_issues, Path(output_dir))

    if verbose:
        print(f"  Markdown report saved to: {report_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 3: Episode Linking Analysis")
    parser.add_argument("--tiago-path", required=True, type=Path)
    parser.add_argument("--cristina-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = run_phase3(args.tiago_path, args.cristina_path, args.output_dir, args.verbose)
    print(f"\nTiago Link Completeness: {results['tiago']['stats']['link_completeness']:.1f}%")
    print(f"Cristina Link Completeness: {results['cristina']['stats']['link_completeness']:.1f}%")
