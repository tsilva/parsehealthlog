"""Gradio app for browsing health_log.csv and health_log.md side-by-side.

Provides bidirectional linking between CSV timeline entries and markdown
journal sections for manual verification.

Usage:
    uv run python viewer.py                    # Start with profile selector
    uv run python viewer.py --profile <name>   # Start with specific profile
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import gradio as gr
import pandas as pd

from config import ProfileConfig


def get_available_profiles() -> list[str]:
    """Get list of available profile names."""
    return ProfileConfig.list_profiles(Path("profiles"))


def get_profile_paths(profile_name: str) -> tuple[Path, Path] | None:
    """Get CSV and MD paths for a profile. Returns None if profile invalid."""
    if not profile_name:
        return None

    profile_path = Path("profiles") / f"{profile_name}.yaml"
    if not profile_path.exists():
        profile_path = Path("profiles") / f"{profile_name}.yml"

    if not profile_path.exists():
        return None

    try:
        profile = ProfileConfig.from_file(profile_path)
        if not profile.output_path:
            return None

        csv_path = profile.output_path / "health_log.csv"
        md_path = profile.output_path / "health_log.md"

        if not csv_path.exists() or not md_path.exists():
            return None

        return csv_path, md_path
    except Exception:
        return None


def load_csv(path: Path) -> pd.DataFrame:
    """Load health_log.csv, skipping header comment lines."""
    lines = path.read_text(encoding="utf-8").splitlines()

    # Skip comment lines at the start (lines starting with #)
    data_start = 0
    for i, line in enumerate(lines):
        if not line.startswith("#"):
            data_start = i
            break

    # Read CSV from the data portion
    from io import StringIO

    csv_content = "\n".join(lines[data_start:])
    df = pd.read_csv(StringIO(csv_content))

    return df


def load_md(path: Path) -> dict[str, str]:
    """Parse health_log.md into dict mapping date -> full entry content."""
    content = path.read_text(encoding="utf-8")
    entries: dict[str, str] = {}

    # Split on date headers (# YYYY-MM-DD or ## YYYY-MM-DD)
    # The MD is newest to oldest
    pattern = r"^(#{1,2})\s*(\d{4}-\d{2}-\d{2})"

    sections = re.split(r"(?=^#{1,2}\s*\d{4}-\d{2}-\d{2})", content, flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue

        match = re.match(pattern, section, re.MULTILINE)
        if match:
            date = match.group(2)
            entries[date] = section

    return entries


def get_md_for_date(md_entries: dict[str, str], date: str) -> str:
    """Get markdown content for a specific date."""
    if date in md_entries:
        return md_entries[date]
    return f"*No entry found for {date}*"


def filter_dataframe(
    df: pd.DataFrame,
    category: str | None,
    episode: str | None,
) -> pd.DataFrame:
    """Filter dataframe by category and/or episode."""
    filtered = df.copy()

    if category and category != "All":
        filtered = filtered[filtered["Category"] == category]

    if episode and episode.strip():
        # Filter by episode ID (exact match or partial)
        episode = episode.strip()
        filtered = filtered[
            filtered["EpisodeID"].str.contains(episode, case=False, na=False)
        ]

    return filtered


def create_app(initial_profile: str | None = None) -> gr.Blocks:
    """Create the Gradio app with profile switching support."""
    profiles = get_available_profiles()

    # Create empty dataframe for initial state
    empty_df = pd.DataFrame(
        columns=["Date", "EpisodeID", "Item", "Category", "Event", "RelatedEpisode", "Details"]
    )

    with gr.Blocks(title="Health Log Viewer") as app:
        # State for current data
        df_state = gr.State(value=empty_df)
        md_state = gr.State(value={})
        current_date = gr.State(value="")

        gr.Markdown("# Health Log Viewer")
        gr.Markdown(
            "Browse CSV timeline and markdown entries side-by-side. Click a row to view the corresponding entry."
        )

        # Profile selector row
        with gr.Row():
            with gr.Column(scale=2):
                profile_dropdown = gr.Dropdown(
                    choices=profiles,
                    label="Profile",
                    value=initial_profile if initial_profile in profiles else None,
                    allow_custom_value=False,
                )
            with gr.Column(scale=3):
                status_label = gr.Markdown("*Select a profile to load data*")

        with gr.Row():
            # Filters
            with gr.Column(scale=1):
                date_dropdown = gr.Dropdown(
                    choices=[],
                    label="Jump to Date",
                    value="",
                    allow_custom_value=True,
                )
            with gr.Column(scale=1):
                category_filter = gr.Dropdown(
                    choices=["All"],
                    label="Filter by Category",
                    value="All",
                )
            with gr.Column(scale=1):
                episode_filter = gr.Textbox(
                    label="Filter by Episode ID",
                    placeholder="e.g., ep-001",
                )

        with gr.Row():
            with gr.Column(scale=1):
                prev_btn = gr.Button("← Previous Date")
            with gr.Column(scale=1):
                next_btn = gr.Button("Next Date →")

        with gr.Row(equal_height=True):
            # Left panel: CSV
            with gr.Column(scale=1):
                gr.Markdown("### Timeline (CSV)")
                csv_display = gr.Dataframe(
                    value=empty_df,
                    interactive=False,
                    wrap=True,
                )

            # Right panel: Markdown
            with gr.Column(scale=1):
                gr.Markdown("### Entry (Markdown)")
                selected_date_label = gr.Markdown("*Select a row or date to view entry*")
                md_display = gr.Markdown(
                    value="*Select a profile and click a row to view the corresponding markdown entry*",
                )

        def on_profile_change(profile_name: str):
            """Handle profile selection change."""
            if not profile_name:
                return (
                    empty_df,
                    {},
                    empty_df,
                    gr.update(choices=[], value=""),
                    gr.update(choices=["All"], value="All"),
                    "*Select a profile to load data*",
                    "*Select a row or date to view entry*",
                    "*Select a profile first*",
                    "",
                )

            paths = get_profile_paths(profile_name)
            if not paths:
                return (
                    empty_df,
                    {},
                    empty_df,
                    gr.update(choices=[], value=""),
                    gr.update(choices=["All"], value="All"),
                    f"*Profile '{profile_name}' has no output files*",
                    "*Select a row or date to view entry*",
                    "*No data available*",
                    "",
                )

            csv_path, md_path = paths
            df = load_csv(csv_path)
            md_entries = load_md(md_path)

            dates = sorted(df["Date"].dropna().unique().tolist())
            categories = ["All"] + sorted(df["Category"].dropna().unique().tolist())

            return (
                df,
                md_entries,
                df,
                gr.update(choices=[""] + dates, value=""),
                gr.update(choices=categories, value="All"),
                f"**Loaded:** {len(df)} rows, {len(md_entries)} entries",
                "*Select a row or date to view entry*",
                "*Click a row in the CSV to view the corresponding markdown entry*",
                "",
            )

        def on_row_select(
            df_data: pd.DataFrame, md_entries: dict, evt: gr.SelectData
        ) -> tuple[str, str, str]:
            """Handle row selection in the dataframe."""
            if evt.index is None or df_data.empty:
                return "*No selection*", "", ""

            row_idx = evt.index[0] if isinstance(evt.index, (list, tuple)) else evt.index

            if row_idx >= len(df_data):
                return "*Invalid row*", "", ""

            date = str(df_data.iloc[row_idx]["Date"])
            md_content = get_md_for_date(md_entries, date)
            return f"**Selected: {date}**", md_content, date

        def on_date_change(
            date: str, category: str, episode: str, df: pd.DataFrame, md_entries: dict
        ) -> tuple[pd.DataFrame, str, str, str]:
            """Handle date dropdown change."""
            if df.empty:
                return df, "*No data loaded*", "", ""

            filtered = filter_dataframe(df, category, episode)

            if date and date.strip():
                # Filter to show only that date
                filtered = filtered[filtered["Date"] == date]
                md_content = get_md_for_date(md_entries, date)
                return filtered, f"**Selected: {date}**", md_content, date

            return filtered, "*Select a date*", "", ""

        def on_filter_change(
            category: str, episode: str, current: str, df: pd.DataFrame, md_entries: dict
        ) -> tuple[pd.DataFrame, str, str]:
            """Handle category/episode filter change."""
            if df.empty:
                return df, "*No data loaded*", ""

            filtered = filter_dataframe(df, category, episode)

            if current and current in md_entries:
                return filtered, f"**Selected: {current}**", get_md_for_date(md_entries, current)

            return filtered, "*Select a row or date*", ""

        def navigate_date(
            current: str,
            direction: int,
            category: str,
            episode: str,
            df: pd.DataFrame,
            md_entries: dict,
        ) -> tuple[pd.DataFrame, str, str, str, str]:
            """Navigate to previous/next date."""
            if df.empty:
                return df, "*No data loaded*", "", "", ""

            filtered = filter_dataframe(df, category, episode)
            available_dates = sorted(filtered["Date"].dropna().unique().tolist())

            if not available_dates:
                return filtered, "*No dates available*", "", "", ""

            if not current or current not in available_dates:
                # Start from first or last depending on direction
                new_date = available_dates[0] if direction > 0 else available_dates[-1]
            else:
                current_idx = available_dates.index(current)
                new_idx = current_idx + direction
                new_idx = max(0, min(new_idx, len(available_dates) - 1))
                new_date = available_dates[new_idx]

            # Filter to show only that date
            date_filtered = filtered[filtered["Date"] == new_date]
            md_content = get_md_for_date(md_entries, new_date)

            return date_filtered, f"**Selected: {new_date}**", md_content, new_date, new_date

        # Wire up events
        profile_dropdown.change(
            on_profile_change,
            inputs=[profile_dropdown],
            outputs=[
                df_state,
                md_state,
                csv_display,
                date_dropdown,
                category_filter,
                status_label,
                selected_date_label,
                md_display,
                current_date,
            ],
        )

        csv_display.select(
            on_row_select,
            inputs=[csv_display, md_state],
            outputs=[selected_date_label, md_display, current_date],
        )

        date_dropdown.change(
            on_date_change,
            inputs=[date_dropdown, category_filter, episode_filter, df_state, md_state],
            outputs=[csv_display, selected_date_label, md_display, current_date],
        )

        category_filter.change(
            on_filter_change,
            inputs=[category_filter, episode_filter, current_date, df_state, md_state],
            outputs=[csv_display, selected_date_label, md_display],
        )

        episode_filter.change(
            on_filter_change,
            inputs=[category_filter, episode_filter, current_date, df_state, md_state],
            outputs=[csv_display, selected_date_label, md_display],
        )

        prev_btn.click(
            lambda c, cat, ep, df, md: navigate_date(c, -1, cat, ep, df, md),
            inputs=[current_date, category_filter, episode_filter, df_state, md_state],
            outputs=[csv_display, selected_date_label, md_display, current_date, date_dropdown],
        )

        next_btn.click(
            lambda c, cat, ep, df, md: navigate_date(c, 1, cat, ep, df, md),
            inputs=[current_date, category_filter, episode_filter, df_state, md_state],
            outputs=[csv_display, selected_date_label, md_display, current_date, date_dropdown],
        )

        # Auto-load initial profile if specified
        if initial_profile and initial_profile in profiles:
            app.load(
                on_profile_change,
                inputs=[profile_dropdown],
                outputs=[
                    df_state,
                    md_state,
                    csv_display,
                    date_dropdown,
                    category_filter,
                    status_label,
                    selected_date_label,
                    md_display,
                    current_date,
                ],
            )

    return app


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Health Log Viewer")
    parser.add_argument("--profile", help="Initial profile to load")
    parser.add_argument("--port", type=int, default=7860, help="Port to run on")
    parser.add_argument("--share", action="store_true", help="Create public link")

    args = parser.parse_args()

    profiles = get_available_profiles()
    if not profiles:
        print("No profiles found in profiles/ directory")
        return

    print(f"Available profiles: {', '.join(profiles)}")
    if args.profile:
        print(f"Initial profile: {args.profile}")

    app = create_app(initial_profile=args.profile)
    app.launch(server_port=args.port, share=args.share)


if __name__ == "__main__":
    main()
