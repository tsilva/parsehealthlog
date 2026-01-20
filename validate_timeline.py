"""Timeline integrity validation."""

import re
import csv
from pathlib import Path
from collections import Counter, defaultdict
import pandas as pd


def validate_episode_continuity(timeline_path: Path) -> list[str]:
    """Validate episode IDs are sequential without gaps or duplicates."""
    errors = []
    df = pd.read_csv(timeline_path, comment='#')

    # Extract episode numbers
    episode_nums = []
    for ep_id in df['EpisodeID'].dropna():
        match = re.match(r'ep-(\d+)', str(ep_id))
        if match:
            episode_nums.append(int(match.group(1)))

    episode_nums.sort()

    # Check for gaps
    for i in range(len(episode_nums) - 1):
        if episode_nums[i+1] - episode_nums[i] > 1:
            errors.append(
                f"Episode ID gap: ep-{episode_nums[i]:03d} → "
                f"ep-{episode_nums[i+1]:03d}"
            )

    # Check for duplicates
    counts = Counter(episode_nums)
    duplicates = [f"ep-{k:03d}" for k, v in counts.items() if v > 1]
    if duplicates:
        errors.append(f"Duplicate episode IDs: {', '.join(duplicates)}")

    return errors


def validate_related_episodes(timeline_path: Path) -> list[str]:
    """Validate all RelatedEpisode references point to existing episodes."""
    errors = []
    df = pd.read_csv(timeline_path, comment='#')

    # Get all episode IDs
    all_episodes = set(df['EpisodeID'].dropna())

    # Check each RelatedEpisode reference
    for idx, row in df.iterrows():
        related = row.get('RelatedEpisode', '')
        if pd.notna(related) and str(related).strip():
            if related not in all_episodes:
                errors.append(
                    f"Line {idx+2}: {row['Date']} {row['Item']} - "
                    f"RelatedEpisode '{related}' does not exist"
                )

    return errors


def validate_csv_structure(timeline_path: Path) -> list[str]:
    """Validate timeline CSV structure and format."""
    errors = []

    with open(timeline_path, 'r', encoding='utf-8') as f:
        lines = [line for line in f if not line.startswith('#')]

    if not lines:
        return ["Timeline is empty"]

    # Check header
    expected_header = "Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details"
    if not lines[0].strip().startswith("Date,"):
        errors.append(f"Missing or invalid header. Expected: {expected_header}")

    # Check each row
    reader = csv.reader(lines)
    header = next(reader)

    for i, row in enumerate(reader, 2):  # Line 2+ (after header)
        if len(row) != 7:
            errors.append(f"Line {i}: Expected 7 columns, got {len(row)}")

    return errors


def validate_chronological_order(timeline_path: Path) -> list[str]:
    """Validate entries are in chronological order."""
    errors = []
    df = pd.read_csv(timeline_path, comment='#')

    dates = df['Date'].tolist()
    sorted_dates = sorted(dates)

    if dates != sorted_dates:
        # Find first out-of-order entry
        for i in range(len(dates) - 1):
            if dates[i] > dates[i+1]:
                errors.append(
                    f"Out of order: {dates[i]} followed by {dates[i+1]} "
                    f"(lines {i+2}, {i+3})"
                )
                break

    return errors


def validate_comprehensive_stack_updates(
    timeline_path: Path,
    entries_dir: Path
) -> list[str]:
    """Detect comprehensive stack updates and validate all items were stopped."""
    errors = []
    df = pd.read_csv(timeline_path, comment='#')

    # Keywords indicating comprehensive update
    stack_keywords = [
        "current stack", "only taking", "stopped all",
        "not taking any", "complete list", "current supplements",
        "only supplement"
    ]

    # Find dates with comprehensive updates
    comprehensive_dates = []
    for entry_file in sorted(entries_dir.glob("*.processed.md")):
        content = entry_file.read_text(encoding='utf-8').lower()
        if any(kw in content for kw in stack_keywords):
            date = entry_file.stem.split('.')[0]
            comprehensive_dates.append(date)

    for update_date in comprehensive_dates:
        # Get active supplements/meds before this date
        active_before = df[
            (df['Date'] < update_date) &
            (df['Category'].isin(['supplement', 'medication'])) &
            (df['Event'] == 'started')
        ]['EpisodeID'].unique()

        # Check each episode: was it stopped by update_date?
        for ep_id in active_before:
            ep_rows = df[df['EpisodeID'] == ep_id]
            stopped = ep_rows[
                (ep_rows['Event'] == 'stopped') &
                (ep_rows['Date'] <= update_date)
            ]

            if stopped.empty:
                # Not stopped - should it have been continued on update_date?
                continued = df[
                    (df['Date'] == update_date) &
                    (df['Item'] == ep_rows.iloc[0]['Item']) &
                    (df['Event'] == 'started')
                ]

                if continued.empty:
                    item_name = ep_rows.iloc[0]['Item']
                    errors.append(
                        f"Comprehensive update {update_date}: {item_name} "
                        f"({ep_id}) started earlier but not stopped or continued"
                    )

    return errors


def run_all_validations(
    timeline_path: Path,
    entries_dir: Path = None
) -> dict[str, list[str]]:
    """Run all validation checks and return results."""
    timeline_path = Path(timeline_path)

    if not timeline_path.exists():
        return {"error": [f"Timeline file not found: {timeline_path}"]}

    results = {
        'episode_continuity': validate_episode_continuity(timeline_path),
        'related_episodes': validate_related_episodes(timeline_path),
        'csv_structure': validate_csv_structure(timeline_path),
        'chronological_order': validate_chronological_order(timeline_path),
    }

    # Add comprehensive stack validation if entries_dir provided
    if entries_dir:
        entries_dir = Path(entries_dir)
        if entries_dir.exists():
            results['comprehensive_stack_updates'] = validate_comprehensive_stack_updates(
                timeline_path, entries_dir
            )

    return results


def print_validation_report(results: dict[str, list[str]]) -> None:
    """Print human-readable validation report."""
    total_errors = sum(len(v) for v in results.values())

    print("\n" + "="*60)
    print("TIMELINE VALIDATION REPORT")
    print("="*60 + "\n")

    if total_errors == 0:
        print("✓ All validation checks passed!\n")
        return

    print(f"⚠  Found {total_errors} validation error(s):\n")

    for check_name, errors in results.items():
        if errors:
            print(f"{check_name.replace('_', ' ').title()}:")
            for error in errors:
                print(f"  - {error}")
            print()
