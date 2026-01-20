#!/usr/bin/env python3
"""
Phase 7: Timeline Continuity Analysis

Assesses long-running episode coherence and narrative quality.

Checks:
- Event sequence logic
- Timeline narrative coherence
- Related episode connections
- Detail completeness for major transitions

Output:
- JSON with continuity analysis
- Markdown report
"""

import csv
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime


def parse_date(date_str):
    """Parse date string to datetime."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except:
        return None


def load_episodes_by_id(csv_path):
    """Load episodes grouped by EpisodeID."""
    episodes_by_id = defaultdict(list)

    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            episode_id = row.get("EpisodeID", "")
            if episode_id:
                episodes_by_id[episode_id].append(row)

    return episodes_by_id


def analyze_episode_continuity(episode_id, events, profile_name):
    """Analyze continuity for a single episode."""
    if len(events) < 5:  # Only analyze substantial episodes
        return None

    # Sort by date
    events = sorted(events, key=lambda e: e["Date"])

    # Calculate duration
    first_date = parse_date(events[0]["Date"])
    last_date = parse_date(events[-1]["Date"])
    duration_days = (last_date - first_date).days if first_date and last_date else 0
    duration_years = duration_days / 365.25

    if duration_years < 1.0:  # Only analyze episodes spanning > 1 year
        return None

    # Check event sequence logic
    event_sequence = [e["Event"] for e in events]

    issues = []
    warnings = []

    # Check for logical progression
    # Example: diagnosed -> treated -> improved/worsened -> resolved
    if events[0]["Event"] not in ["diagnosed", "suspected", "noted", "started"]:
        warnings.append({
            "type": "unusual_start",
            "message": f"Episode starts with '{events[0]['Event']}' instead of initial event"
        })

    # Check for resolved followed by more events
    for i, event in enumerate(events):
        if event["Event"] in ["resolved", "stopped"] and i < len(events) - 1:
            warnings.append({
                "type": "post_resolution_event",
                "message": f"Events continue after '{event['Event']}' on {event['Date']}"
            })

    # Check detail completeness for major transitions
    major_events = ["diagnosed", "started", "stopped", "resolved", "worsened"]
    transitions_with_details = sum(1 for e in events if e["Event"] in major_events and len(e.get("Details", "")) > 20)
    major_transitions = sum(1 for e in events if e["Event"] in major_events)

    detail_completeness = (transitions_with_details / major_transitions * 100) if major_transitions > 0 else 0

    # Check related episode links
    has_related_links = any(e.get("RelatedEpisode", "") for e in events)

    analysis = {
        "episode_id": episode_id,
        "item": events[0]["Item"],
        "category": events[0]["Category"],
        "event_count": len(events),
        "duration": f"{duration_years:.2f} years",
        "date_range": f"{events[0]['Date']} to {events[-1]['Date']}",
        "event_sequence": event_sequence,
        "issues": issues,
        "warnings": warnings,
        "is_coherent": len(issues) == 0,
        "detail_completeness": detail_completeness,
        "has_related_links": has_related_links
    }

    return analysis


def generate_continuity_report(tiago_analysis, cristina_analysis, output_dir):
    """Generate markdown continuity report."""
    report = []
    report.append("# Phase 7: Timeline Continuity Analysis Report\n")
    report.append("## Overview\n")
    report.append("This report assesses long-running episode coherence and narrative quality.\n")

    for profile_name, analyses in [("Tiago", tiago_analysis), ("Cristina", cristina_analysis)]:
        report.append(f"\n## {profile_name} Profile\n")
        report.append(f"**Episodes Analyzed:** {len(analyses)}\n")

        coherent = sum(1 for a in analyses if a["is_coherent"])
        report.append(f"**Coherent Episodes:** {coherent}/{len(analyses)}\n")

        total_issues = sum(len(a["issues"]) for a in analyses)
        total_warnings = sum(len(a["warnings"]) for a in analyses)
        report.append(f"**Total Issues:** {total_issues}\n")
        report.append(f"**Total Warnings:** {total_warnings}\n")

        avg_completeness = sum(a["detail_completeness"] for a in analyses) / len(analyses) if analyses else 0
        report.append(f"**Avg Detail Completeness:** {avg_completeness:.1f}%\n")

        with_links = sum(1 for a in analyses if a["has_related_links"])
        report.append(f"**Episodes with Related Links:** {with_links}/{len(analyses)}\n")

        # Show sample episodes
        if analyses:
            report.append("\n### Sample Episode Analysis\n")
            for analysis in analyses[:3]:
                report.append(f"\n#### {analysis['item']} ({analysis['episode_id']})\n")
                report.append(f"- **Duration:** {analysis['duration']}\n")
                report.append(f"- **Event Count:** {analysis['event_count']}\n")
                report.append(f"- **Event Sequence:** {' → '.join(analysis['event_sequence'][:5])}{'...' if len(analysis['event_sequence']) > 5 else ''}\n")
                report.append(f"- **Detail Completeness:** {analysis['detail_completeness']:.1f}%\n")
                report.append(f"- **Has Related Links:** {'Yes' if analysis['has_related_links'] else 'No'}\n")

                if analysis["issues"]:
                    report.append("- **Issues:**\n")
                    for issue in analysis["issues"]:
                        report.append(f"  - {issue['message']}\n")

                if analysis["warnings"]:
                    report.append("- **Warnings:**\n")
                    for warning in analysis["warnings"]:
                        report.append(f"  - {warning['message']}\n")

    report_path = output_dir / "phase7_timeline_continuity.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return report_path


def run_phase7(tiago_path, cristina_path, output_dir, verbose=False):
    """Run Phase 7: Timeline Continuity Analysis"""
    if verbose:
        print("Running timeline continuity analysis...")

    # Analyze Tiago
    if verbose:
        print("  Analyzing Tiago profile...")
    tiago_csv = Path(tiago_path) / "health_log.csv"
    tiago_episodes = load_episodes_by_id(tiago_csv)

    tiago_analysis = []
    for episode_id, events in tiago_episodes.items():
        analysis = analyze_episode_continuity(episode_id, events, "Tiago")
        if analysis:
            tiago_analysis.append(analysis)

    # Analyze Cristina
    if verbose:
        print("  Analyzing Cristina profile...")
    cristina_csv = Path(cristina_path) / "health_log.csv"
    cristina_episodes = load_episodes_by_id(cristina_csv)

    cristina_analysis = []
    for episode_id, events in cristina_episodes.items():
        analysis = analyze_episode_continuity(episode_id, events, "Cristina")
        if analysis:
            cristina_analysis.append(analysis)

    # Save JSON results
    results = {
        "tiago": {
            "total_analyzed": len(tiago_analysis),
            "coherent_count": sum(1 for a in tiago_analysis if a["is_coherent"]),
            "episodes": tiago_analysis
        },
        "cristina": {
            "total_analyzed": len(cristina_analysis),
            "coherent_count": sum(1 for a in cristina_analysis if a["is_coherent"]),
            "episodes": cristina_analysis
        }
    }

    json_path = Path(output_dir) / "phase7_continuity.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    if verbose:
        print(f"  JSON results saved to: {json_path}")

    # Generate report
    report_path = generate_continuity_report(tiago_analysis, cristina_analysis, Path(output_dir))

    if verbose:
        print(f"  Markdown report saved to: {report_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 7: Timeline Continuity Analysis")
    parser.add_argument("--tiago-path", required=True, type=Path)
    parser.add_argument("--cristina-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = run_phase7(args.tiago_path, args.cristina_path, args.output_dir, args.verbose)
    print(f"\nTimeline Continuity:")
    print(f"  Tiago:    {results['tiago']['coherent_count']}/{results['tiago']['total_analyzed']} coherent")
    print(f"  Cristina: {results['cristina']['coherent_count']}/{results['cristina']['total_analyzed']} coherent")
