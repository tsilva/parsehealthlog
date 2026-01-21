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

    # Check for duplicates - only flag if same ID used with different base item names
    # Reusing an ID for follow-up events on the same item is expected behavior
    df_clean = df.dropna(subset=['EpisodeID'])
    for ep_id in df_clean['EpisodeID'].unique():
        ep_rows = df_clean[df_clean['EpisodeID'] == ep_id]
        unique_items = ep_rows['Item'].unique()
        if len(unique_items) > 1:
            errors.append(
                f"Episode {ep_id} used for different items: {', '.join(unique_items)}"
            )

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


def validate_episode_state_consistency(timeline_path: Path) -> list[str]:
    """Validate episodes don't have events after terminal states.

    Terminal events: stopped, resolved, ended, completed
    If an episode is terminated, no subsequent events should occur.
    """
    errors = []
    df = pd.read_csv(timeline_path, comment='#')

    terminal_events = {'stopped', 'resolved', 'ended', 'completed'}

    # Group by episode ID
    for ep_id in df['EpisodeID'].dropna().unique():
        ep_rows = df[df['EpisodeID'] == ep_id].sort_values('Date')

        # Find first terminal event
        terminal_rows = ep_rows[ep_rows['Event'].isin(terminal_events)]
        if terminal_rows.empty:
            continue

        terminal_date = terminal_rows.iloc[0]['Date']
        terminal_event = terminal_rows.iloc[0]['Event']

        # Check for events after terminal date
        events_after = ep_rows[ep_rows['Date'] > terminal_date]
        if not events_after.empty:
            item_name = ep_rows.iloc[0]['Item']
            for _, row in events_after.iterrows():
                errors.append(
                    f"{ep_id} ({item_name}): Event '{row['Event']}' on {row['Date']} "
                    f"after terminal event '{terminal_event}' on {terminal_date}"
                )

    return errors


def validate_comprehensive_stack_updates(
    timeline_path: Path,
    entries_dir: Path
) -> list[str]:
    """Detect comprehensive stack updates and validate all items were stopped.

    Looks for [STACK_UPDATE] markers in Details field to reliably detect
    comprehensive stack updates without keyword guessing.
    """
    errors = []
    df = pd.read_csv(timeline_path, comment='#')

    # Find dates with [STACK_UPDATE] markers
    stack_update_dates = set()
    for _, row in df.iterrows():
        details = str(row.get('Details', ''))
        if '[STACK_UPDATE]' in details:
            stack_update_dates.add(row['Date'])

    for update_date in sorted(stack_update_dates):
        # Get all stopped/ended events on this date
        updates_on_date = df[
            (df['Date'] == update_date) &
            (df['Event'].isin(['stopped', 'ended']))
        ]

        # Check if they all have [STACK_UPDATE] marker (consistency check)
        missing_marker = []
        for _, row in updates_on_date.iterrows():
            details = str(row.get('Details', ''))
            if '[STACK_UPDATE]' not in details and row['Category'] in ['supplement', 'medication', 'experiment']:
                missing_marker.append(f"{row['Item']} ({row['EpisodeID']})")

        if missing_marker:
            errors.append(
                f"Date {update_date}: Some stopped/ended items lack [STACK_UPDATE] marker: "
                f"{', '.join(missing_marker[:3])}"
            )

        # Get active supplements/meds/experiments before this date
        active_before = df[
            (df['Date'] < update_date) &
            (df['Category'].isin(['supplement', 'medication', 'experiment'])) &
            (df['Event'].isin(['started']))
        ]['EpisodeID'].unique()

        # Check each episode: was it stopped/ended by update_date?
        for ep_id in active_before:
            ep_rows = df[df['EpisodeID'] == ep_id]
            category = ep_rows.iloc[0]['Category']
            stopped_event = 'stopped' if category in ['supplement', 'medication'] else 'ended'

            stopped = ep_rows[
                (ep_rows['Event'] == stopped_event) &
                (ep_rows['Date'] <= update_date)
            ]

            if stopped.empty:
                # Not stopped/ended - should it have been continued on update_date?
                continued = df[
                    (df['Date'] == update_date) &
                    (df['Item'] == ep_rows.iloc[0]['Item']) &
                    (df['Event'] == 'started')
                ]

                if continued.empty:
                    item_name = ep_rows.iloc[0]['Item']
                    errors.append(
                        f"Comprehensive update {update_date}: {item_name} "
                        f"({ep_id}) started earlier but not stopped/ended or continued"
                    )

    return errors


def run_all_validations(
    timeline_path: Path,
    entries_dir: Path = None
) -> dict[str, list[str]]:
    """Run all validation checks and return results.

    Returns:
        dict mapping check names to lists of error messages
        Each check has an associated severity ('critical' or 'warning')
        Access via get_validation_severity(check_name)
    """
    timeline_path = Path(timeline_path)

    if not timeline_path.exists():
        return {"error": [f"Timeline file not found: {timeline_path}"]}

    results = {
        'episode_continuity': validate_episode_continuity(timeline_path),
        'related_episodes': validate_related_episodes(timeline_path),
        'csv_structure': validate_csv_structure(timeline_path),
        'chronological_order': validate_chronological_order(timeline_path),
        'episode_state_consistency': validate_episode_state_consistency(timeline_path),
    }

    # Add comprehensive stack validation if entries_dir provided
    if entries_dir:
        entries_dir = Path(entries_dir)
        if entries_dir.exists():
            results['comprehensive_stack_updates'] = validate_comprehensive_stack_updates(
                timeline_path, entries_dir
            )

    return results


def get_validation_severity(check_name: str) -> str:
    """Get severity level for a validation check.

    Args:
        check_name: Name of the validation check

    Returns:
        'critical' or 'warning'
    """
    critical_checks = {
        'episode_continuity',
        'csv_structure',
        'chronological_order',
        'episode_state_consistency',
    }
    return 'critical' if check_name in critical_checks else 'warning'


def print_validation_report(results: dict[str, list[str]]) -> None:
    """Print human-readable validation report."""
    total_errors = sum(len(v) for v in results.values())
    critical_errors = sum(
        len(v) for k, v in results.items()
        if get_validation_severity(k) == 'critical'
    )

    print("\n" + "="*60)
    print("TIMELINE VALIDATION REPORT")
    print("="*60 + "\n")

    if total_errors == 0:
        print("✓ All validation checks passed!\n")
        return

    print(f"Found {total_errors} validation error(s) ")
    print(f"({critical_errors} critical, {total_errors - critical_errors} warnings)\n")

    # Print critical errors first
    for check_name, errors in results.items():
        if errors and get_validation_severity(check_name) == 'critical':
            print(f"[CRITICAL] {check_name.replace('_', ' ').title()}:")
            for error in errors:
                print(f"  - {error}")
            print()

    # Print warnings
    for check_name, errors in results.items():
        if errors and get_validation_severity(check_name) == 'warning':
            print(f"[WARNING] {check_name.replace('_', ' ').title()}:")
            for error in errors:
                print(f"  - {error}")
            print()
