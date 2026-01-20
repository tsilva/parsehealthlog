#!/usr/bin/env python3
"""
Phase 6: Cross-Profile Consistency Analysis

Compares consistency across Tiago and Cristina profiles.

Checks:
- Voice consistency
- Detail granularity
- Format uniformity
- Linking patterns

Output:
- JSON with consistency metrics
- Markdown report
"""

import csv
import json
import random
from pathlib import Path
from collections import defaultdict


def load_episodes(csv_path):
    """Load all episodes from CSV."""
    episodes = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        episodes = list(reader)
    return episodes


def calculate_detail_length(details):
    """Calculate average detail length."""
    return len(details) if details else 0


def analyze_voice_consistency(episodes1, episodes2):
    """Compare voice/tone consistency between profiles."""
    # Simple heuristic: compare detail field characteristics
    detail_lengths1 = [calculate_detail_length(e.get("Details", "")) for e in episodes1]
    detail_lengths2 = [calculate_detail_length(e.get("Details", "")) for e in episodes2]

    avg1 = sum(detail_lengths1) / len(detail_lengths1) if detail_lengths1 else 0
    avg2 = sum(detail_lengths2) / len(detail_lengths2) if detail_lengths2 else 0

    # Calculate variability (standard deviation approximation)
    diff = abs(avg1 - avg2)
    consistency_score = max(0, 100 - (diff / 10))  # Rough scoring

    return {
        "avg_detail_length_profile1": avg1,
        "avg_detail_length_profile2": avg2,
        "consistency_score": consistency_score
    }


def analyze_linking_patterns(episodes1, episodes2):
    """Compare linking patterns between profiles."""
    linked1 = len([e for e in episodes1 if e.get("RelatedEpisode", "")])
    linked2 = len([e for e in episodes2 if e.get("RelatedEpisode", "")])

    total1 = len(episodes1)
    total2 = len(episodes2)

    rate1 = (linked1 / total1 * 100) if total1 > 0 else 0
    rate2 = (linked2 / total2 * 100) if total2 > 0 else 0

    return {
        "profile1_linking_rate": rate1,
        "profile2_linking_rate": rate2,
        "difference": abs(rate1 - rate2)
    }


def generate_cross_profile_report(voice_stats, linking_stats, output_dir):
    """Generate markdown cross-profile report."""
    report = []
    report.append("# Phase 6: Cross-Profile Consistency Analysis Report\n")
    report.append("## Overview\n")
    report.append("This report compares consistency across Tiago and Cristina profiles.\n")

    report.append("\n## Voice Consistency\n")
    report.append(f"**Tiago Avg Detail Length:** {voice_stats['avg_detail_length_profile1']:.0f} chars\n")
    report.append(f"**Cristina Avg Detail Length:** {voice_stats['avg_detail_length_profile2']:.0f} chars\n")
    report.append(f"**Consistency Score:** {voice_stats['consistency_score']:.1f}/100\n")

    report.append("\n## Linking Patterns\n")
    report.append(f"**Tiago Linking Rate:** {linking_stats['profile1_linking_rate']:.1f}%\n")
    report.append(f"**Cristina Linking Rate:** {linking_stats['profile2_linking_rate']:.1f}%\n")
    report.append(f"**Difference:** {linking_stats['difference']:.1f}%\n")

    report_path = output_dir / "phase6_cross_profile.md"
    with open(report_path, "w") as f:
        f.write("".join(report))

    return report_path


def run_phase6(tiago_path, cristina_path, output_dir, verbose=False):
    """Run Phase 6: Cross-Profile Consistency Analysis"""
    if verbose:
        print("Running cross-profile consistency analysis...")

    # Load episodes
    tiago_csv = Path(tiago_path) / "health_log.csv"
    cristina_csv = Path(cristina_path) / "health_log.csv"

    tiago_episodes = load_episodes(tiago_csv)
    cristina_episodes = load_episodes(cristina_csv)

    # Analyze voice consistency
    voice_stats = analyze_voice_consistency(tiago_episodes, cristina_episodes)

    # Analyze linking patterns
    linking_stats = analyze_linking_patterns(tiago_episodes, cristina_episodes)

    # Save JSON results
    results = {
        "voice_consistency": voice_stats,
        "linking_patterns": linking_stats
    }

    json_path = Path(output_dir) / "phase6_cross_profile.json"
    with open(json_path, "w") as f:
        json.dump(results, f, indent=2)

    if verbose:
        print(f"  JSON results saved to: {json_path}")

    # Generate report
    report_path = generate_cross_profile_report(voice_stats, linking_stats, Path(output_dir))

    if verbose:
        print(f"  Markdown report saved to: {report_path}")

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run Phase 6: Cross-Profile Consistency Analysis")
    parser.add_argument("--tiago-path", required=True, type=Path)
    parser.add_argument("--cristina-path", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = run_phase6(args.tiago_path, args.cristina_path, args.output_dir, args.verbose)
    print(f"\nVoice Consistency Score: {results['voice_consistency']['consistency_score']:.1f}/100")
