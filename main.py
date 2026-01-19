from __future__ import annotations

"""Health log data extraction and curation tool.

Transforms markdown health journal entries into structured, curated data:
• Reads a markdown health log that uses `### YYYY-MM-DD` section headers.
• Processes and validates entries using LLM prompts stored in `prompts/`.
• Enriches entries with lab results and medical exam summaries.
• Builds a chronological timeline with episode IDs linking related events.

Output structure:
    OUTPUT_PATH/
    ├─ health_log.md          # PRIMARY: All entries (newest to oldest) with labs/exams
    ├─ health_log.csv         # PRIMARY: Chronological timeline with episode IDs
    ├─ entries/               # INTERMEDIATE (kept for caching)
    │   ├─ <date>.raw.md
    │   ├─ <date>.processed.md
    │   ├─ <date>.labs.md
    │   └─ <date>.exams.md
    └─ intro.md               # Pre-dated content (if exists)

Configuration via environment variables (see config.py):
    OPENROUTER_API_KEY           – mandatory (forwarded to openrouter.ai)
    HEALTH_LOG_PATH              – mandatory (path to the markdown health log)
    OUTPUT_PATH                  – mandatory (base directory for generated output)
    MODEL_ID                     – default model (fallback for all roles, default: gpt-4o-mini)
    PROCESS_MODEL_ID             – (optional) override for PROCESS stage
    VALIDATE_MODEL_ID            – (optional) override for VALIDATE stage
    STATUS_MODEL_ID              – (optional) override for timeline building
    LABS_PARSER_OUTPUT_PATH      – (optional) path to aggregated lab CSVs
    MEDICAL_EXAMS_PARSER_OUTPUT_PATH – (optional) path to medical exam summaries
    MAX_WORKERS                  – (optional) ThreadPoolExecutor size (default 4)
"""

from dotenv import load_dotenv
load_dotenv(override=True)

import argparse
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Final

import pandas as pd
from dateutil.parser import parse as date_parse
from openai import OpenAI, APIError, APIConnectionError, RateLimitError, APITimeoutError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from tqdm import tqdm

from config import Config, ProfileConfig
from exceptions import DateExtractionError, PromptError, LabParsingError

# --------------------------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------------------------


def setup_logging() -> None:
    """Configure logging for the application.

    Sets up console output (INFO+) and file logging:
    - logs/all.log: All log entries (INFO+)
    - logs/warnings.log: Warnings and errors only (WARNING+)
    Uses a named logger to avoid interfering with other libraries' root handlers.
    """
    fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt, datefmt=datefmt)

    # Use named logger for this application
    logger = logging.getLogger(__name__)

    # Skip if already configured (prevents duplicate handlers on re-import)
    if logger.handlers:
        return

    logger.setLevel(logging.INFO)
    logger.propagate = False  # Don't propagate to root logger

    # Console handler for all messages
    console_hdlr = logging.StreamHandler(sys.stdout)
    console_hdlr.setFormatter(formatter)
    logger.addHandler(console_hdlr)

    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)

    # File handler for all entries (INFO+)
    all_hdlr = logging.FileHandler(logs_dir / "all.log", encoding="utf-8")
    all_hdlr.setLevel(logging.INFO)
    all_hdlr.setFormatter(formatter)
    logger.addHandler(all_hdlr)

    # File handler for warnings and errors only (WARNING+)
    warn_hdlr = logging.FileHandler(logs_dir / "warnings.log", encoding="utf-8")
    warn_hdlr.setLevel(logging.WARNING)
    warn_hdlr.setFormatter(formatter)
    logger.addHandler(warn_hdlr)

    # Quiet noisy dependencies
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


# --------------------------------------------------------------------------------------
# Utility functions
# --------------------------------------------------------------------------------------


PROMPTS_DIR: Final = Path(__file__).with_suffix("").parent / "prompts"
LAB_SECTION_HEADER: Final = "Lab test results:"
MEDICAL_EXAMS_SECTION_HEADER: Final = "Medical exam results:"

def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise PromptError(f"Prompt file not found: {path}", prompt_name=name)
    return path.read_text(encoding="utf-8")


def short_hash(text: str) -> str:  # 12-char SHA-256 hex prefix (48 bits, collision-resistant)
    return sha256(text.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------------------
# Dependency tracking utilities
# --------------------------------------------------------------------------------------


def hash_content(content: str) -> str:
    """Compute 8-char SHA-256 hash of content."""
    return short_hash(content)


def hash_file(path: Path) -> str | None:
    """Compute hash of file content, return None if file doesn't exist."""
    if not path.exists():
        return None
    return hash_content(path.read_text(encoding="utf-8"))


def parse_deps_comment(line: str) -> dict[str, str]:
    """Parse dependency hash comment from first line.

    Expected format: <!-- DEPS: key1:hash1,key2:hash2,... -->
    Returns empty dict if format doesn't match.
    """
    match = re.match(r"<!--\s*DEPS:\s*(.+?)\s*-->", line.strip())
    if not match:
        return {}

    deps = {}
    for pair in match.group(1).split(","):
        if ":" in pair:
            key, value = pair.split(":", 1)
            deps[key.strip()] = value.strip()
    return deps


def format_deps_comment(deps: dict[str, str]) -> str:
    """Format dependencies as HTML comment for first line of output file."""
    pairs = [f"{k}:{v}" for k, v in sorted(deps.items())]
    return f"<!-- DEPS: {','.join(pairs)} -->"


def extract_date(section: str) -> str:
    """Return YYYY-MM-DD from the section header line (first token that parses).

    Raises:
        DateExtractionError: If section is empty or no valid date found in header.
    """
    lines = section.strip().splitlines()
    if not lines:
        raise DateExtractionError("Cannot extract date from empty section", section=section)
    header = lines[0].lstrip("#").replace("–", "-").replace("—", "-")
    for token in re.split(r"\s+", header):
        try:
            return date_parse(token, fuzzy=False).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise DateExtractionError(f"No valid date found in header: {header}", section=section)


def strip_date_header(section: str) -> str:
    """Remove the date header line from a section (date is in filename)."""
    lines = section.strip().splitlines()
    if not lines:
        return ""
    # Skip the first line (date header) and return the rest
    return "\n".join(lines[1:]).strip()


def format_labs(df: pd.DataFrame) -> str:
    """Format lab results for clinical review.

    Outputs raw values with reference ranges - clinical interpretation is
    delegated to downstream LLMs which can apply medical judgment.
    """
    out: list[str] = []
    for row in df.itertuples():
        name = str(row.lab_name_standardized).strip()
        value = row.value_normalized
        unit = str(getattr(row, "unit_normalized", "")).strip()
        rmin, rmax = row.reference_min_normalized, row.reference_max_normalized

        # Format value with unit and reference range
        line = f"- **{name}:** {value}{f' {unit}' if unit else ''}"
        if pd.notna(rmin) and pd.notna(rmax):
            line += f" (ref: {rmin} - {rmax})"

        out.append(line)
    return "\n".join(out)


def format_medical_exams(exams: list[str]) -> str:
    """Format medical exam summaries for inclusion in health entries.

    Each exam is already in markdown format with its own headers.
    Multiple exams on the same date are separated by blank lines.
    """
    return "\n\n".join(exams)


def normalize_markdown_headers(content: str, target_base_level: int) -> str:
    """Normalize markdown headers to be relative to a target base level.

    Finds the minimum header level in content and shifts all headers so that
    the minimum becomes target_base_level. This ensures consistent hierarchy
    when content is nested under a parent section.

    Args:
        content: Markdown content with headers to normalize.
        target_base_level: The level the highest (smallest #) header should become.

    Returns:
        Content with all headers adjusted to maintain relative hierarchy.
    """
    if not content.strip():
        return content

    lines = content.split('\n')
    header_pattern = re.compile(r'^(#{1,6})\s+(.+)$')
    min_level = 7  # Higher than max possible (6)

    for line in lines:
        match = header_pattern.match(line)
        if match:
            min_level = min(min_level, len(match.group(1)))

    if min_level == 7:  # No headers found
        return content

    offset = target_base_level - min_level
    if offset == 0:
        return content

    result_lines = []
    for line in lines:
        match = header_pattern.match(line)
        if match:
            new_level = max(1, min(6, len(match.group(1)) + offset))
            result_lines.append('#' * new_level + ' ' + match.group(2))
        else:
            result_lines.append(line)

    return '\n'.join(result_lines)


# --------------------------------------------------------------------------------------
# OpenAI wrapper
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class LLM:
    """Lightweight wrapper around OpenAI chat completions with retry logic."""

    client: OpenAI
    model: str

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((APIError, APIConnectionError, RateLimitError, APITimeoutError)),
        reraise=True,
    )
    def __call__(self, messages: list[dict[str, str]], *, max_tokens: int = 2048, temperature: float = 0.0) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=120.0,  # 2 minute timeout per request
        )
        content = resp.choices[0].message.content
        return content.strip() if content else ""


# --------------------------------------------------------------------------------------
# Core processing class
# --------------------------------------------------------------------------------------


class HealthLogProcessor:
    """End-to-end processor that orchestrates all steps."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.path = config.health_log_path
        if not self.path.exists():
            raise FileNotFoundError(self.path)

        output_base = config.output_path
        self.OUTPUT_PATH = output_base
        self.OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
        self.entries_dir = self.OUTPUT_PATH / "entries"
        self.entries_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger(__name__)

        # Prompts (lazy-load to keep __init__ lightweight)
        self.prompts: dict[str, str] = {}

        # OpenAI client + per-role models
        self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=config.openrouter_api_key)
        self.models = {
            "process": config.process_model_id,
            "validate": config.validate_model_id,
            "status": config.status_model_id,
        }
        self.llm = {k: LLM(self.client, v) for k, v in self.models.items()}

        # Lab data per date – populated lazily
        self.labs_by_date: dict[str, pd.DataFrame] = {}

        # Medical exam data per date – populated lazily
        self.medical_exams_by_date: dict[str, list[str]] = {}

        # State file for progress tracking
        self.state_file = self.OUTPUT_PATH / ".state.json"

        # Validate all required prompts exist at startup
        self._validate_prompts()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_prompts(self) -> None:
        """Validate that all required prompt files exist before processing begins."""
        required_prompts = [
            "process.system_prompt",
            "validate.system_prompt",
            "validate.user_prompt",
            "update_timeline.system_prompt",
        ]

        missing = [p for p in required_prompts if not (PROMPTS_DIR / f"{p}.md").exists()]
        if missing:
            raise PromptError(f"Missing required prompt files: {', '.join(missing)}")

        self.logger.info("All required prompt files validated successfully")

    # ------------------------------------------------------------------
    # State management for resumable runs
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        """Load state from state file, or return empty state if not exists."""
        if not self.state_file.exists():
            return {}
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            self.logger.warning("Could not load state file: %s", e)
            return {}

    def _save_state(self, state: dict) -> None:
        """Save state to state file."""
        try:
            self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except IOError as e:
            self.logger.warning("Could not save state file: %s", e)

    def _update_state(self, **updates) -> None:
        """Update specific fields in state file."""
        state = self._load_state()
        state.update(updates)
        self._save_state(state)

    def get_progress(self) -> dict:
        """Get current processing progress.

        Returns a dict with:
        - status: 'not_started', 'in_progress', 'completed', 'failed'
        - started_at: ISO timestamp when run started
        - completed_at: ISO timestamp when run completed (if completed)
        - sections_total: Total number of sections to process
        - sections_processed: Number of successfully processed sections
        - sections_failed: List of failed section dates
        - reports_generated: List of generated report names
        """
        state = self._load_state()

        # Count processed sections from filesystem
        processed_files = list(self.entries_dir.glob("*.processed.md"))
        failed_files = list(self.entries_dir.glob("*.failed.md"))

        return {
            "status": state.get("status", "not_started"),
            "started_at": state.get("started_at"),
            "completed_at": state.get("completed_at"),
            "sections_total": state.get("sections_total", 0),
            "sections_processed": len(processed_files),
            "sections_failed": [f.stem.replace(".failed", "") for f in failed_files],
            "reports_generated": state.get("reports_generated", []),
        }

    # ------------------------------------------------------------------
    # LLM response parsing
    # ------------------------------------------------------------------

    def _parse_json_response(self, text: str) -> dict:
        """Parse JSON from LLM response, stripping markdown code blocks if present."""
        text = text.strip()
        if not text:
            raise json.JSONDecodeError("Empty response from LLM", "", 0)
        # Match ```json ... ``` or ``` ... ``` (handles trailing whitespace/newlines)
        match = re.match(r'^```(?:json)?\s*\n(.*)\n```\s*$', text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        # Fallback: try to extract JSON object if it starts with { and ends with }
        elif text.startswith('```'):
            # Code block without proper closing - try to extract JSON anyway
            lines = text.split('\n')
            if lines[0].startswith('```'):
                lines = lines[1:]  # Skip opening fence
            # Find where JSON ends (last closing brace)
            json_text = '\n'.join(lines)
            if '```' in json_text:
                json_text = json_text.split('```')[0]
            text = json_text.strip()
        if not text:
            raise json.JSONDecodeError("Empty JSON content after stripping code block", "", 0)
        return json.loads(text)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: C901 – single orchestrator method is clearer here
        # Track run start
        self._update_state(
            status="in_progress",
            started_at=datetime.now().isoformat(),
            completed_at=None,
        )

        header_text, sections = self._split_sections()
        self._load_labs()
        self._load_medical_exams()

        # Create placeholder sections for dates with labs/exams but no entries
        sections = self._create_placeholder_sections(sections)

        # Update state with total sections count
        self._update_state(sections_total=len(sections))

        # Write raw sections & compute which ones need processing
        to_process: list[str] = []
        for sec in sections:
            date = extract_date(sec)
            raw_path = self.entries_dir / f"{date}.raw.md"
            # Strip date header - it's already in the filename
            raw_path.write_text(strip_date_header(sec), encoding="utf-8")

            # Check if processing needed based on dependencies
            labs_content = ""
            if date in self.labs_by_date and not self.labs_by_date[date].empty:
                labs_content = f"{LAB_SECTION_HEADER}\n{format_labs(self.labs_by_date[date])}\n"

            exams_content = ""
            if date in self.medical_exams_by_date and self.medical_exams_by_date[date]:
                exams_content = f"{MEDICAL_EXAMS_SECTION_HEADER}\n{format_medical_exams(self.medical_exams_by_date[date])}\n"

            processed_path = self.entries_dir / f"{date}.processed.md"
            deps = self._get_section_dependencies(sec, labs_content, exams_content)

            if self._check_needs_regeneration(processed_path, deps):
                to_process.append(sec)

        # Process (potentially in parallel)
        max_workers = self.config.max_workers
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex, tqdm(total=len(to_process), desc="Processing") as bar:
            futures = {ex.submit(self._process_section, sec): sec for sec in to_process}
            for fut in as_completed(futures):
                try:
                    date, ok = fut.result()
                    if not ok:
                        failed.append(date)
                except Exception as e:
                    # Get the section that caused the error for context
                    section = futures[fut]
                    try:
                        date = extract_date(section)
                    except Exception:
                        date = "(unknown date)"
                    self.logger.error("Exception processing section %s: %s", date, e, exc_info=True)
                    failed.append(date)
                bar.update(1)

        if failed:
            self.logger.error("Failed to process sections for: %s", ", ".join(failed))
        else:
            self.logger.info("All sections processed successfully")

        # Save collated health log (all processed entries newest to oldest)
        self._save_collated_health_log(header_text)

        # Always regenerate lab markdown files
        # This includes both regular entries and lab-only entries
        for date, df in self.labs_by_date.items():
            if df.empty:
                continue
            lab_path = self.entries_dir / f"{date}.labs.md"
            lab_path.write_text(
                f"{LAB_SECTION_HEADER}\n{format_labs(df)}\n",
                encoding="utf-8",
            )

        # Always regenerate exam markdown files
        # This includes both regular entries and exam-only entries
        for date, exams_list in self.medical_exams_by_date.items():
            if not exams_list:
                continue
            exams_path = self.entries_dir / f"{date}.exams.md"
            exams_path.write_text(
                f"{MEDICAL_EXAMS_SECTION_HEADER}\n{format_medical_exams(exams_list)}\n",
                encoding="utf-8",
            )

        # Build health timeline CSV (chronological events with episode IDs)
        self._build_health_timeline()

        # Track run completion
        self._update_state(
            status="completed" if not failed else "completed_with_errors",
            completed_at=datetime.now().isoformat(),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prompt(self, name: str) -> str:
        """Load and cache a prompt by name.

        Prompts are cached for the lifetime of the processor to ensure
        consistency between content used and content hashed.
        """
        if name not in self.prompts:
            self.prompts[name] = load_prompt(name)
        return self.prompts[name]

    # --------------------------------------------------------------
    # Dependency tracking helpers
    # --------------------------------------------------------------

    def _read_without_deps_comment(self, path: Path) -> str:
        """Read file content, skipping dependency comment on line 1 if present."""
        lines = path.read_text(encoding="utf-8").splitlines()
        if lines and lines[0].startswith("<!--"):
            return "\n".join(lines[1:])
        return "\n".join(lines)

    def _hash_prompt(self, name: str) -> str:
        """Compute hash of a prompt's content.

        Uses cached content from _prompt() to ensure hash matches
        the actual content being used (not file on disk which may have changed).
        """
        return hash_content(self._prompt(name))

    def _hash_intro(self) -> str:
        """Compute hash of intro.md if it exists."""
        intro_path = self.OUTPUT_PATH / "intro.md"
        return hash_file(intro_path) or ""

    def _hash_files_without_deps(self, paths: Iterable[Path]) -> str:
        """Compute combined hash of multiple files, excluding deps comments."""
        contents = []
        for path in sorted(paths):
            if path.exists():
                contents.append(self._read_without_deps_comment(path))
        return hash_content("\n\n".join(contents)) if contents else ""

    def _hash_all_processed(self) -> str:
        """Compute combined hash of all processed sections."""
        return self._hash_files_without_deps(self.entries_dir.glob("*.processed.md"))

    def _get_section_dependencies(self, section: str, labs_content: str, exams_content: str = "") -> dict[str, str]:
        """Compute all dependencies for a processed section."""
        return {
            "raw": hash_content(section),
            "labs": hash_content(labs_content) if labs_content else "none",
            "exams": hash_content(exams_content) if exams_content else "none",
            "process_prompt": self._hash_prompt("process.system_prompt"),
            "validate_prompt": self._hash_prompt("validate.system_prompt"),
        }

    def _check_needs_regeneration(self, path: Path, expected_deps: dict[str, str]) -> bool:
        """Check if a file needs regeneration based on its dependencies.

        Returns True if file doesn't exist or dependencies have changed.
        """
        if not path.exists():
            return True

        lines = path.read_text(encoding="utf-8").splitlines()
        first_line = lines[0] if lines else ""
        existing_deps = parse_deps_comment(first_line)

        # If no deps comment found (old format), regenerate
        if not existing_deps:
            return True

        # Check if any dependency changed
        for key, expected_hash in expected_deps.items():
            if existing_deps.get(key) != expected_hash:
                return True

        return False

    # --------------------------------------------------------------
    # Health Timeline building (replaces entity extraction + aggregation)
    # --------------------------------------------------------------

    def _build_health_timeline(self) -> str:
        """Build health timeline CSV from processed entries.

        Processes entries chronologically (oldest first), building a CSV timeline
        with episode IDs linking related events. Supports incremental updates:
        - Cache hit: No changes detected, return existing timeline
        - Append mode: Only new entries at end, append new rows
        - Incremental rebuild: Entry modified/deleted/inserted, rebuild from change point
        - Full rebuild: First run or migration from old format

        Returns the timeline CSV content.
        """
        self.logger.info("Building health timeline from processed entries...")

        timeline_path = self.OUTPUT_PATH / "health_log.csv"

        # Get all processed entries sorted chronologically (oldest first)
        entries = self._get_chronological_entries()
        if not entries:
            self.logger.warning("No processed entries found for timeline")
            return self._get_empty_timeline()

        # Parse existing timeline header if it exists
        processed_through, last_episode_num, entry_hashes = self._parse_timeline_header(timeline_path)

        # No HASHES line - full rebuild (migration or first run)
        if not entry_hashes:
            self.logger.info("Full timeline rebuild: processing %d entries", len(entries))
            return self._full_timeline_rebuild(timeline_path, entries)

        # Detect what changed
        change = self._find_earliest_change(entries, entry_hashes, processed_through)

        if change is None:
            # Cache hit - timeline is up-to-date
            self.logger.info("Health timeline is up-to-date (processed through %s)", processed_through)
            return self._read_timeline_content(timeline_path)

        if change == "append":
            # Only new entries after processed_through
            new_entries = [(d, c) for d, c in entries if d > processed_through]
            self.logger.info(
                "Incremental timeline update: %d new entries after %s",
                len(new_entries), processed_through
            )
            existing_content = self._read_timeline_content(timeline_path)
            new_rows = self._process_timeline_batch(
                existing_content, new_entries, last_episode_num + 1
            )
            # Append new rows
            if new_rows.strip():
                updated_content = existing_content.rstrip() + "\n" + new_rows
            else:
                updated_content = existing_content
            # Update header and save
            new_last_date = entries[-1][0]
            new_last_ep = self._get_last_episode_num(updated_content)
            self._save_timeline(timeline_path, updated_content, new_last_date, entries, new_last_ep)
            return updated_content

        # Incremental rebuild from change point
        self.logger.info("Rebuilding timeline from %s", change)

        existing_content = self._read_timeline_content(timeline_path)
        truncated, last_ep_kept = self._truncate_timeline_to_date(existing_content, change)

        # Get entries to reprocess (date >= change point)
        to_process = [(d, c) for d, c in entries if d >= change]

        # Process in batches, starting from truncated timeline
        timeline_content = truncated
        next_ep = last_ep_kept + 1

        batch_start = 0
        while batch_start < len(to_process):
            batch_size = self._calculate_batch_size(to_process[batch_start:], len(timeline_content))
            batch = to_process[batch_start:batch_start + batch_size]

            new_rows = self._process_timeline_batch(
                timeline_content, batch, next_ep
            )

            if new_rows.strip():
                timeline_content = timeline_content.rstrip() + "\n" + new_rows
                next_ep = self._get_last_episode_num(timeline_content) + 1

            batch_start += batch_size
            self.logger.info(
                "Processed batch: %d entries through %s (%d remaining)",
                len(batch), batch[-1][0], len(to_process) - batch_start
            )

        # Save with header
        last_date = entries[-1][0]
        last_ep = self._get_last_episode_num(timeline_content)
        self._save_timeline(timeline_path, timeline_content, last_date, entries, last_ep)

        return timeline_content

    def _full_timeline_rebuild(self, timeline_path: Path, entries: list[tuple[str, str]]) -> str:
        """Perform a full timeline rebuild from scratch.

        Args:
            timeline_path: Path to save the timeline.
            entries: All entries sorted chronologically (oldest first).

        Returns:
            The complete timeline CSV content.
        """
        timeline_content = self._get_empty_timeline()

        # Process in batches to stay within context limits
        next_episode_num = 1
        batch_start = 0
        while batch_start < len(entries):
            batch_size = self._calculate_batch_size(entries[batch_start:], len(timeline_content))
            batch = entries[batch_start:batch_start + batch_size]

            new_rows = self._process_timeline_batch(
                timeline_content, batch, next_episode_num
            )

            if new_rows.strip():
                timeline_content = timeline_content.rstrip() + "\n" + new_rows
                next_episode_num = self._get_last_episode_num(timeline_content) + 1

            batch_start += batch_size
            self.logger.info(
                "Processed batch: %d entries through %s (%d remaining)",
                len(batch), batch[-1][0], len(entries) - batch_start
            )

        # Save with header
        last_date = entries[-1][0]
        last_ep = self._get_last_episode_num(timeline_content)
        self._save_timeline(timeline_path, timeline_content, last_date, entries, last_ep)

        return timeline_content

    def _get_chronological_entries(self) -> list[tuple[str, str]]:
        """Get all processed entries sorted chronologically (oldest first).

        Returns:
            List of (date, content) tuples, sorted by date ascending.
        """
        entries = []
        for path in self.entries_dir.glob("*.processed.md"):
            date = path.stem.split(".")[0]
            content = self._read_without_deps_comment(path)
            entries.append((date, content))
        return sorted(entries, key=lambda x: x[0])  # Oldest first

    def _get_empty_timeline(self) -> str:
        """Return empty timeline with just the CSV header."""
        return "Date,EpisodeID,Item,Category,Event,Details"

    def _parse_timeline_header(self, path: Path) -> tuple[str | None, int, dict[str, str]]:
        """Parse timeline header to get processed_through, last episode ID, and per-entry hashes.

        Expected header format (two lines):
            # Last updated: YYYY-MM-DD | Processed through: YYYY-MM-DD | LastEp: N
            # HASHES: 2024-01-15=a1b2c3d4,2024-01-20=b2c3d4e5

        Returns:
            (processed_through_date, last_episode_num, entry_hashes_dict)
        """
        if not path.exists():
            return None, 0, {}

        try:
            lines = path.read_text(encoding="utf-8").split("\n")
            if not lines:
                return None, 0, {}

            first_line = lines[0]

            # New format: # Last updated: YYYY-MM-DD | Processed through: YYYY-MM-DD | LastEp: N
            match = re.match(
                r"#\s*Last updated:.*\|\s*Processed through:\s*(\d{4}-\d{2}-\d{2})\s*\|\s*LastEp:\s*(\d+)",
                first_line
            )
            if match:
                processed_through = match.group(1)
                last_ep = int(match.group(2))
                # Parse second line for HASHES
                entry_hashes = {}
                if len(lines) > 1 and lines[1].startswith("# HASHES:"):
                    entry_hashes = self._parse_hashes_line(lines[1])
                return processed_through, last_ep, entry_hashes

            # Legacy format: # Last updated: ... | Hash: xxx | LastEp: N
            # Return empty entry_hashes to trigger full rebuild
            legacy_match = re.match(
                r"#\s*Last updated:.*\|\s*Processed through:\s*(\d{4}-\d{2}-\d{2})\s*\|\s*Hash:\s*\w+\s*\|\s*LastEp:\s*(\d+)",
                first_line
            )
            if legacy_match:
                # Legacy format - return empty hashes to force migration rebuild
                return legacy_match.group(1), int(legacy_match.group(2)), {}

        except (IOError, IndexError):
            pass
        return None, 0, {}

    def _read_timeline_content(self, path: Path) -> str:
        """Read timeline content, skipping the header comment lines.

        Skips up to two comment lines at the start:
        - Line 1: Metadata (Last updated, Processed through, LastEp)
        - Line 2: HASHES line (per-entry hashes)
        """
        lines = path.read_text(encoding="utf-8").split("\n")
        # Skip header comment lines
        skip = 0
        while skip < len(lines) and lines[skip].startswith("#"):
            skip += 1
        return "\n".join(lines[skip:])

    def _save_timeline(
        self, path: Path, content: str, processed_through: str, entries: list[tuple[str, str]], last_ep: int
    ) -> None:
        """Save timeline with two-line metadata header.

        Header format:
            # Last updated: YYYY-MM-DD | Processed through: YYYY-MM-DD | LastEp: N
            # HASHES: date1=hash1,date2=hash2,...
        """
        today = datetime.now().strftime("%Y-%m-%d")
        header_line1 = f"# Last updated: {today} | Processed through: {processed_through} | LastEp: {last_ep}"
        header_line2 = self._format_hashes_line(entries)
        path.write_text(f"{header_line1}\n{header_line2}\n{content}", encoding="utf-8")
        self.logger.info("Saved health timeline to %s", path)

    def _get_last_episode_num(self, timeline_content: str) -> int:
        """Extract the highest episode number from timeline content."""
        matches = re.findall(r"ep-(\d+)", timeline_content)
        if matches:
            return max(int(m) for m in matches)
        return 0

    def _format_hashes_line(self, entries: list[tuple[str, str]]) -> str:
        """Format per-entry hashes as a comment line.

        Args:
            entries: List of (date, content) tuples.

        Returns:
            Formatted line like: # HASHES: 2024-01-15=a1b2c3d4,2024-01-20=b2c3d4e5
        """
        hashes = []
        for date, content in sorted(entries, key=lambda x: x[0]):
            h = hash_content(content)
            hashes.append(f"{date}={h}")
        return "# HASHES: " + ",".join(hashes)

    def _parse_hashes_line(self, line: str) -> dict[str, str]:
        """Parse HASHES line into a dict of date -> hash.

        Args:
            line: Line like "# HASHES: 2024-01-15=a1b2c3d4,2024-01-20=b2c3d4e5"

        Returns:
            Dict mapping date strings to their content hashes.
        """
        if not line.startswith("# HASHES:"):
            return {}

        hashes_part = line[len("# HASHES:"):].strip()
        if not hashes_part:
            return {}

        result = {}
        for pair in hashes_part.split(","):
            if "=" in pair:
                date, h = pair.split("=", 1)
                result[date.strip()] = h.strip()
        return result

    def _find_earliest_change(
        self,
        entries: list[tuple[str, str]],
        entry_hashes: dict[str, str],
        processed_through: str | None,
    ) -> str | None:
        """Find the earliest date where an entry changed, was deleted, or was inserted.

        Args:
            entries: Current list of (date, content) tuples.
            entry_hashes: Dict of date -> hash from the stored HASHES line.
            processed_through: The date through which the timeline was last processed.

        Returns:
            - A date string if we need to rebuild from that date
            - "append" if only new entries after processed_through exist
            - None if cache is valid (no changes)
        """
        # Build current hashes
        current = {date: hash_content(content) for date, content in entries}
        current_dates = set(current.keys())
        existing_dates = set(entry_hashes.keys())

        change_points = []

        # Case 1: Entry deleted
        for date in existing_dates - current_dates:
            change_points.append(date)

        # Case 2: Entry modified
        for date in current_dates & existing_dates:
            if current[date] != entry_hashes[date]:
                change_points.append(date)

        # Case 3: New entry inserted in middle (not appended)
        for date in current_dates - existing_dates:
            if processed_through and date <= processed_through:
                change_points.append(date)

        if change_points:
            return min(change_points)  # Earliest change

        # No changes to existing - check for append mode
        if processed_through:
            new_entries = [d for d in current_dates if d > processed_through]
            if new_entries:
                return "append"  # Use existing append logic

        return None  # Cache hit

    def _truncate_timeline_to_date(self, content: str, cutoff_date: str) -> tuple[str, int]:
        """Keep timeline rows where Date < cutoff_date.

        Args:
            content: Timeline CSV content (without header comment lines).
            cutoff_date: Date to truncate from (exclusive - rows before this are kept).

        Returns:
            Tuple of (truncated content, max episode number in kept rows).
        """
        lines = content.split("\n")
        kept = []
        last_ep = 0

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Keep CSV header
            if stripped.startswith("Date,"):
                kept.append(line)
                continue

            # Parse date from first column (handle quoted/unquoted)
            row_date = line.split(",")[0].strip().strip('"')

            if row_date < cutoff_date:
                kept.append(line)
                # Track max episode number
                match = re.search(r"ep-(\d+)", line)
                if match:
                    last_ep = max(last_ep, int(match.group(1)))

        return "\n".join(kept), last_ep

    def _calculate_batch_size(self, entries: list[tuple[str, str]], timeline_size: int) -> int:
        """Calculate how many entries fit in a batch given current timeline size.

        Args:
            entries: List of (date, content) tuples to potentially include
            timeline_size: Current character count of timeline

        Returns:
            Number of entries to include in this batch
        """
        CONTEXT_LIMIT = 200_000  # Claude Opus 4.5 context
        SYSTEM_PROMPT_TOKENS = 3_000
        MAX_OUTPUT_TOKENS = 32_768  # Match max_tokens in _process_timeline_batch
        OUTPUT_TOKENS_PER_ENTRY = 60  # ~1.5 rows per entry, ~40 tokens per row
        SAFETY_MARGIN = 10_000
        CHARS_PER_TOKEN = 4

        timeline_tokens = timeline_size // CHARS_PER_TOKEN
        available_input_tokens = CONTEXT_LIMIT - SYSTEM_PROMPT_TOKENS - timeline_tokens - MAX_OUTPUT_TOKENS - SAFETY_MARGIN

        batch_chars = 0
        count = 0
        for _, content in entries:
            entry_chars = len(content)
            # Check both input token limit and output token limit
            estimated_output_tokens = (count + 1) * OUTPUT_TOKENS_PER_ENTRY
            if batch_chars + entry_chars > available_input_tokens * CHARS_PER_TOKEN:
                break
            if estimated_output_tokens > MAX_OUTPUT_TOKENS - 1000:  # Leave buffer
                break
            batch_chars += entry_chars
            count += 1

        # Ensure at least 1 entry per batch
        return max(1, count)

    def _process_timeline_batch(
        self, existing_timeline: str, entries: list[tuple[str, str]], next_episode_num: int
    ) -> str:
        """Process a batch of entries and return new CSV rows to append.

        Args:
            existing_timeline: Current timeline CSV content (for context)
            entries: List of (date, content) tuples to process
            next_episode_num: The next episode ID number to use

        Returns:
            New CSV rows to append (without header)
        """
        system_prompt = self._prompt("update_timeline.system_prompt")

        # Format entries for the batch
        entries_text = "\n\n---\n\n".join(
            f"### Entry: {date}\n\n{content}"
            for date, content in entries
        )

        user_content = f"""## Current Timeline (for context)

```csv
{existing_timeline}
```

## Next Episode ID

Use ep-{next_episode_num:03d} for the first new episode, then increment as needed.

## Entries to Process (oldest first)

{entries_text}

Output only the new CSV rows to append (no header row):"""

        response = self.llm["status"](
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=32768,
            temperature=0.0,
        )

        # Clean up response - remove any markdown code blocks
        cleaned = response.strip()
        if cleaned.startswith("```"):
            # Remove code block markers
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines)

        # Remove any accidental header row
        lines = cleaned.split("\n")
        if lines and lines[0].strip().startswith("Date,"):
            lines = lines[1:]

        return "\n".join(lines)

    # --------------------------------------------------------------
    # Section processing (one dated section → validated markdown)
    # --------------------------------------------------------------

    def _process_section(self, section: str) -> tuple[str, bool]:
        date = extract_date(section)
        processed_path = self.entries_dir / f"{date}.processed.md"
        # Strip date header - it's in the filename
        section_content = strip_date_header(section)

        # Get labs content for this date
        labs_content = ""
        if date in self.labs_by_date and not self.labs_by_date[date].empty:
            labs_content = f"{LAB_SECTION_HEADER}\n{format_labs(self.labs_by_date[date])}\n"

        # Get medical exams content for this date
        exams_content = ""
        if date in self.medical_exams_by_date and self.medical_exams_by_date[date]:
            exams_content = f"{MEDICAL_EXAMS_SECTION_HEADER}\n{format_medical_exams(self.medical_exams_by_date[date])}\n"

        # Compute dependencies for this section
        deps = self._get_section_dependencies(section, labs_content, exams_content)

        last_processed = ""
        last_validation = ""

        for attempt in range(1, 4):
            # 1) PROCESS - include validation feedback on retries
            messages = [
                {"role": "system", "content": self._prompt("process.system_prompt")},
                {"role": "user", "content": section_content},
            ]
            if attempt > 1 and last_validation:
                messages.append({
                    "role": "assistant",
                    "content": last_processed,
                })
                messages.append({
                    "role": "user",
                    "content": f"Your output was rejected because it was missing details:\n{last_validation}\n\nPlease try again, preserving ALL details including dosages, brand names, and additional ingredients.",
                })
            processed = self.llm["process"](messages)
            last_processed = processed

            # 2) VALIDATE
            validation = self.llm["validate"](
                [
                    {"role": "system", "content": self._prompt("validate.system_prompt")},
                    {
                        "role": "user",
                        "content": self._prompt("validate.user_prompt").format(
                            raw_section=section_content, processed_section=processed
                        ),
                    },
                ]
            )
            last_validation = validation

            if "$OK$" in validation:
                # Include labs and exams in processed file if they exist
                final_content = processed
                if labs_content:
                    final_content = f"{final_content}\n\n{labs_content}"
                if exams_content:
                    final_content = f"{final_content}\n\n{exams_content}"

                # Write with dependency hash in first line
                deps_comment = format_deps_comment(deps)
                processed_path.write_text(f"{deps_comment}\n{final_content}", encoding="utf-8")

                # Labs are also written to separate .labs.md file
                if (df := self.labs_by_date.get(date)) is not None and not df.empty:
                    lab_path = self.entries_dir / f"{date}.labs.md"
                    lab_path.write_text(labs_content, encoding="utf-8")

                # Exams are also written to separate .exams.md file
                if self.medical_exams_by_date.get(date):
                    exams_path = self.entries_dir / f"{date}.exams.md"
                    exams_path.write_text(exams_content, encoding="utf-8")

                return date, True

            self.logger.error("Validation failed (%s attempt %d): %s", date, attempt, validation)

        # Save diagnostic info for failed validation
        failed_path = self.entries_dir / f"{date}.failed.md"
        diagnostic = f"""# Validation Failed: {date}

## Raw Section (Input)
```
{section_content}
```

## Last Processed Output
```
{last_processed}
```

## Last Validation Response
```
{last_validation}
```

## Notes
- All 3 validation attempts failed
- Review the validation response to understand what's missing or incorrect
- Consider adjusting the raw input or prompts
"""
        failed_path.write_text(diagnostic, encoding="utf-8")
        self.logger.error("Saved diagnostic info to %s", failed_path)

        return date, False

    # --------------------------------------------------------------
    # Input pre-processing helpers
    # --------------------------------------------------------------

    def _split_sections(self) -> tuple[str, list[str]]:
        text = self.path.read_text(encoding="utf-8")

        # Locate the first dated section header (supports YYYY-MM-DD or YYYY/MM/DD)
        date_regex = r"^###\s*\d{4}[-/]\d{2}[-/]\d{2}"
        match = re.search(date_regex, text, flags=re.MULTILINE)
        if not match:
            raise ValueError(
                "No dated sections found (expected '### YYYY-MM-DD' or '### YYYY/MM/DD')."
            )

        intro_text = text[: match.start()].strip()
        body = text[match.start() :]

        # Split the remainder on dated section headers
        sections = [
            s.strip()
            for s in re.split(fr"(?={date_regex})", body, flags=re.MULTILINE)
            if s.strip()
        ]

        header_text = ""
        if intro_text:
            intro_path = self.OUTPUT_PATH / "intro.md"
            intro_path.write_text(intro_text + "\n", encoding="utf-8")
            header_text = intro_path.read_text(encoding="utf-8")

        return header_text, sections

    def _load_labs(self) -> None:
        lab_dfs: list[pd.DataFrame] = []
        # per-log labs.csv
        csv_local = self.path.parent / "labs.csv"
        if csv_local.exists():
            try:
                lab_dfs.append(pd.read_csv(csv_local))
                self.logger.info("Loaded labs from %s", csv_local)
            except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
                self.logger.error("Failed to parse labs CSV %s: %s", csv_local, e)

        # aggregated labs
        if self.config.labs_parser_output_path:
            labs_path = self.config.labs_parser_output_path
            if not labs_path.exists():
                self.logger.warning("LABS_PARSER_OUTPUT_PATH does not exist: %s", labs_path)
            elif not labs_path.is_dir():
                self.logger.warning("LABS_PARSER_OUTPUT_PATH is not a directory: %s", labs_path)
            else:
                agg_csv = labs_path / "all.csv"
                if agg_csv.exists():
                    try:
                        lab_dfs.append(pd.read_csv(agg_csv))
                        self.logger.info("Loaded aggregated labs from %s", agg_csv)
                    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
                        self.logger.error("Failed to parse aggregated labs CSV %s: %s", agg_csv, e)

        if not lab_dfs:
            self.logger.info("No lab CSV files found")
            return

        labs_df = pd.concat(lab_dfs, ignore_index=True)
        initial_count = len(labs_df)

        # Handle multiple column naming conventions (before validation)
        column_mappings = {
            "lab_name_enum": "lab_name_standardized",
            "lab_name": "lab_name_standardized",
            "lab_value_final": "value_normalized",
            "lab_unit_final": "unit_normalized",
            "lab_range_min_final": "reference_min_normalized",
            "lab_range_max_final": "reference_max_normalized",
            # Additional mappings for different CSV formats
            "value": "value_normalized",
            "unit": "unit_normalized",
            "reference_min": "reference_min_normalized",
            "reference_max": "reference_max_normalized",
            "lab_unit_standardized": "unit_normalized",
            # Mappings for _primary suffix columns
            "value_primary": "value_normalized",
            "lab_unit_primary": "unit_normalized",
            "reference_min_primary": "reference_min_normalized",
            "reference_max_primary": "reference_max_normalized",
        }
        # Rename columns if they exist
        labs_df = labs_df.rename(columns={k: v for k, v in column_mappings.items() if k in labs_df.columns})

        # Validate required columns exist
        required_cols = ["date", "lab_name_standardized"]
        missing_cols = [c for c in required_cols if c not in labs_df.columns]
        if missing_cols:
            self.logger.error(
                "Lab CSV missing required columns: %s. Available: %s",
                missing_cols,
                list(labs_df.columns),
            )
            return

        # Parse dates with logging for coercion failures
        original_dates = labs_df["date"].copy()
        parsed_dates = pd.to_datetime(labs_df["date"], errors="coerce")
        coercion_failures = labs_df[parsed_dates.isna() & original_dates.notna()]
        if len(coercion_failures) > 0:
            self.logger.warning(
                "Dropped %d lab rows with unparseable dates. Sample bad dates: %s",
                len(coercion_failures),
                coercion_failures["date"].head(5).tolist(),
            )
        labs_df["date"] = parsed_dates.dt.strftime("%Y-%m-%d")

        # Filter to relevant columns
        keep_cols = [
            "date",
            "lab_name_standardized",
            "value_normalized",
            "unit_normalized",
            "reference_min_normalized",
            "reference_max_normalized",
        ]
        labs_df = labs_df[[c for c in keep_cols if c in labs_df.columns]]

        # Drop rows with empty dates (from coercion failures)
        labs_df = labs_df[labs_df["date"].notna() & (labs_df["date"] != "")]
        final_count = len(labs_df)

        if initial_count != final_count:
            self.logger.info("Lab data: %d rows loaded, %d after filtering", initial_count, final_count)

        self.labs_by_date = {d: df for d, df in labs_df.groupby("date")}

    def _load_medical_exams(self) -> None:
        """Load medical exam summaries from the configured output path.

        Scans directories matching YYYY-MM-DD pattern, reads .summary.md files,
        and groups them by date. Directories without valid date prefixes are
        skipped with a warning.
        """
        if not self.config.medical_exams_parser_output_path:
            self.logger.info("No MEDICAL_EXAMS_PARSER_OUTPUT_PATH configured")
            return

        exams_path = self.config.medical_exams_parser_output_path
        if not exams_path.exists():
            self.logger.warning("MEDICAL_EXAMS_PARSER_OUTPUT_PATH does not exist: %s", exams_path)
            return
        if not exams_path.is_dir():
            self.logger.warning("MEDICAL_EXAMS_PARSER_OUTPUT_PATH is not a directory: %s", exams_path)
            return

        # Date pattern for directory names: YYYY-MM-DD - description
        date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*-\s*")

        exams_by_date: dict[str, list[str]] = {}
        skipped_count = 0
        loaded_count = 0

        for subdir in sorted(exams_path.iterdir()):
            if not subdir.is_dir():
                continue

            # Extract date from directory name
            match = date_pattern.match(subdir.name)
            if not match:
                self.logger.debug(
                    "Skipping directory without date prefix: %s",
                    subdir.name
                )
                skipped_count += 1
                continue

            date = match.group(1)

            # Find .summary.md file in this directory
            summary_files = list(subdir.glob("*.summary.md"))
            if not summary_files:
                self.logger.debug("No .summary.md file found in %s", subdir.name)
                continue

            # Read the summary file (there should typically be one)
            for summary_file in sorted(summary_files):
                try:
                    content = summary_file.read_text(encoding="utf-8").strip()
                    if content:
                        if date not in exams_by_date:
                            exams_by_date[date] = []
                        exams_by_date[date].append(content)
                        loaded_count += 1
                except IOError as e:
                    self.logger.warning("Failed to read %s: %s", summary_file, e)

        self.medical_exams_by_date = exams_by_date

        if skipped_count > 0:
            self.logger.warning(
                "Skipped %d directories without valid date prefix",
                skipped_count
            )
        self.logger.info(
            "Loaded %d medical exam summaries for %d dates from %s",
            loaded_count, len(exams_by_date), exams_path
        )

    def _save_collated_health_log(self, header_text: str) -> None:
        """Save collated health log with all processed entries (newest to oldest).

        Creates the primary output file containing the full health log in reverse
        chronological order. Uses processed entries (not raw) so the collated log
        reflects validated, cleaned content including integrated labs and exams.
        """
        collated_path = self.OUTPUT_PATH / "health_log.md"

        # Get all processed entries sorted newest-first, with normalized headers
        processed_entries = []
        for path in self.entries_dir.glob("*.processed.md"):
            date = path.stem.split(".")[0]
            content = self._read_without_deps_comment(path)
            # Normalize entry headers to level 4 (#### YYYY-MM-DD)
            normalized = normalize_markdown_headers(content, target_base_level=4)
            processed_entries.append((date, normalized))

        sorted_entries = sorted(processed_entries, key=lambda x: x[0], reverse=True)

        # Assemble collated content
        parts = []
        if header_text:
            parts.append(header_text.strip())
        for date, entry_content in sorted_entries:
            parts.append(f"# {date}")
            parts.append(entry_content)

        content = "\n\n".join(parts)

        # Compute hash for dependency tracking
        content_hash = hash_content(content)
        deps_comment = format_deps_comment({"content": content_hash})

        # Check if file needs update
        if collated_path.exists():
            existing_deps = parse_deps_comment(
                collated_path.read_text(encoding="utf-8").split("\n")[0]
            )
            if existing_deps.get("content") == content_hash:
                self.logger.info("Collated health log is up-to-date")
                return

        collated_path.write_text(f"{deps_comment}\n{content}", encoding="utf-8")
        self.logger.info(
            "Saved health log (%d entries, newest to oldest) to %s",
            len(sorted_entries),
            collated_path,
        )

    def _create_placeholder_sections(self, sections: list[str]) -> list[str]:
        """Create entry files directly for dates with labs/exams but no health log entries.

        Creates .processed.md (just labs/exams content) and separate files.
        No .raw.md is created since there's no raw health log content.
        Uses dependency tracking to detect when data changes.

        Returns the original sections list unchanged.
        """
        # Extract dates from existing sections
        log_dates = {extract_date(sec) for sec in sections}

        # Find dates with labs or exams but no entries
        data_dates = set(self.labs_by_date.keys()) | set(self.medical_exams_by_date.keys())
        missing_dates = sorted(data_dates - log_dates)

        if not missing_dates:
            return sections

        # Create entry files directly for dates with only labs/exams
        for date in missing_dates:
            # Get labs content if available
            labs_content = ""
            df = self.labs_by_date.get(date)
            if df is not None and not df.empty:
                labs_content = f"{LAB_SECTION_HEADER}\n{format_labs(df)}\n"

            # Get exams content if available
            exams_content = ""
            exams_list = self.medical_exams_by_date.get(date)
            if exams_list:
                exams_content = f"{MEDICAL_EXAMS_SECTION_HEADER}\n{format_medical_exams(exams_list)}\n"

            # Skip if neither labs nor exams exist
            if not labs_content and not exams_content:
                continue

            # Dependencies for data-only entries (no raw content, no LLM processing)
            deps = {
                "raw": "none",  # No raw health log content
                "labs": hash_content(labs_content) if labs_content else "none",
                "exams": hash_content(exams_content) if exams_content else "none",
                "process_prompt": "none",  # Not processed by LLM
                "validate_prompt": "none",  # Not validated
            }

            # Check if processed file exists and is up-to-date
            processed_path = self.entries_dir / f"{date}.processed.md"
            if not self._check_needs_regeneration(processed_path, deps):
                continue  # up-to-date

            # Assemble processed content (no date header - it's in the filename)
            content_parts = []
            if labs_content:
                content_parts.append(labs_content)
            if exams_content:
                content_parts.append(exams_content)

            processed_content = "\n\n".join(content_parts)
            deps_comment = format_deps_comment(deps)
            processed_path.write_text(f"{deps_comment}\n{processed_content}", encoding="utf-8")

            # Describe what data types are present
            data_types = []
            if labs_content:
                data_types.append("labs")
            if exams_content:
                data_types.append("exams")
            self.logger.info(
                "Created entry for %s (%s, no health log entry)",
                date,
                " + ".join(data_types)
            )

        # Return original sections unchanged (placeholder files were created directly)
        return sections


# --------------------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------------------


def main() -> None:
    """Run the processor using configuration from profile."""
    parser = argparse.ArgumentParser(
        description="Process health log entries and extract structured data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python main.py --profile tiago
  uv run python main.py --profile tiago --force-reprocess
  uv run python main.py --list-profiles
        """,
    )
    parser.add_argument(
        "--profile",
        "-p",
        type=str,
        help="Profile name (without extension) - REQUIRED for processing",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List available profiles and exit",
    )
    parser.add_argument(
        "--force-reprocess",
        action="store_true",
        help="Clear all cached outputs and reprocess everything from scratch",
    )
    args = parser.parse_args()

    setup_logging()
    logger = logging.getLogger(__name__)

    # Handle --list-profiles
    if args.list_profiles:
        profiles = ProfileConfig.list_profiles()
        if profiles:
            print("Available profiles:")
            for name in profiles:
                print(f"  - {name}")
        else:
            print("No profiles found in profiles/ directory.")
            print("Create a profile by copying profiles/_template.yaml")
        sys.exit(0)

    # Profile is required for processing
    if not args.profile:
        print("Error: --profile is required.")
        print()
        profiles = ProfileConfig.list_profiles()
        if profiles:
            print("Available profiles:")
            for name in profiles:
                print(f"  - {name}")
            print()
            print(f"Example: uv run python main.py --profile {profiles[0]}")
        else:
            print("No profiles found. Create one by copying profiles/_template.yaml")
        sys.exit(1)

    # Load profile
    profile_path = None
    for ext in (".yaml", ".yml", ".json"):
        candidate = Path("profiles") / f"{args.profile}{ext}"
        if candidate.exists():
            profile_path = candidate
            break

    if not profile_path:
        print(f"Error: Profile '{args.profile}' not found.")
        print("Use --list-profiles to see available profiles.")
        sys.exit(1)

    try:
        profile = ProfileConfig.from_file(profile_path)
        logger.info("Using profile: %s", profile.name)
    except Exception as e:
        print(f"Error loading profile: {e}")
        sys.exit(1)

    try:
        config = Config.from_profile(profile)
    except ValueError as e:
        raise SystemExit(f"Configuration error: {e}")

    # Handle --force-reprocess flag: clear cached outputs
    if args.force_reprocess:
        output_path = config.output_path
        entries_dir = output_path / "entries"

        # Delete all generated entry files
        if entries_dir.exists():
            patterns = ["*.processed.md", "*.labs.md", "*.exams.md", "*.failed.md"]
            deleted = 0
            for pattern in patterns:
                for f in entries_dir.glob(pattern):
                    f.unlink()
                    deleted += 1
            if deleted:
                logger.info("Cleared %d generated files from %s", deleted, entries_dir)

        # Delete primary output files
        for filename in ["health_log.md", "health_log.csv"]:
            filepath = output_path / filename
            if filepath.exists():
                filepath.unlink()
                logger.info("Deleted %s", filepath)

        # Delete state file
        state_file = output_path / ".state.json"
        if state_file.exists():
            state_file.unlink()

        # Delete legacy files if they exist
        for legacy_file in ["health_timeline.csv", "reports"]:
            legacy_path = output_path / legacy_file
            if legacy_path.exists():
                if legacy_path.is_dir():
                    shutil.rmtree(legacy_path)
                else:
                    legacy_path.unlink()
                logger.info("Cleared legacy %s", legacy_path)

    start = datetime.now()
    HealthLogProcessor(config).run()
    logger.info(
        "Finished in %.1fs",
        (datetime.now() - start).total_seconds(),
    )


if __name__ == "__main__":
    main()
