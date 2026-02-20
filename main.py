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
    ├─ history.csv            # PRIMARY: Chronological event log with entity IDs
    └─ entries/               # INTERMEDIATE (kept for caching)
        ├─ <date>.raw.md
        ├─ <date>.processed.md
        ├─ <date>.labs.md
        └─ <date>.exams.md

Configuration via profile YAML (see config.py):
    model_id                     – mandatory (model identifier)
    base_url                     – API base URL (default: http://127.0.0.1:8082/api/v1)
    api_key                      – API key (default: health-log-parser)
    health_log_path              – mandatory (path to the markdown health log)
    output_path                  – mandatory (base directory for generated output)
    labs_parser_output_path      – (optional) path to aggregated lab CSVs
    medical_exams_parser_output_path – (optional) path to medical exam summaries
    workers                      – (optional) ThreadPoolExecutor size (default 4)
"""

from dotenv import load_dotenv
from pathlib import Path as _Path
import sys as _sys


def _load_dotenv_with_env() -> str | None:
    """Load .env file, or .env.{name} if --env flag is specified."""
    env_name = None
    for i, arg in enumerate(_sys.argv):
        if arg == "--env" and i + 1 < len(_sys.argv):
            env_name = _sys.argv[i + 1]
            break
        if arg.startswith("--env="):
            env_name = arg.split("=", 1)[1]
            break

    if env_name:
        env_file = _Path(f".env.{env_name}")
        if env_file.exists():
            load_dotenv(env_file, override=True)
        else:
            print(f"Warning: .env.{env_name} not found")
    else:
        load_dotenv(override=True)
    return env_name


_load_dotenv_with_env()

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
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from tqdm import tqdm

from config import Config, ProfileConfig, check_api_accessibility
from exceptions import DateExtractionError, ExtractionError, PromptError


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


def short_hash(
    text: str,
) -> str:  # 12-char SHA-256 hex prefix (48 bits, collision-resistant)
    return sha256(text.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------------------
# Dependency tracking utilities
# --------------------------------------------------------------------------------------


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
        raise DateExtractionError(
            "Cannot extract date from empty section", section=section
        )
    header = lines[0].lstrip("#").replace("–", "-").replace("—", "-")
    for token in re.split(r"\s+", header):
        try:
            return date_parse(token, fuzzy=False).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise DateExtractionError(
        f"No valid date found in header: {header}", section=section
    )


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

    lines = content.split("\n")
    header_pattern = re.compile(r"^(#{1,6})\s+(.+)$")
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
            result_lines.append("#" * new_level + " " + match.group(2))
        else:
            result_lines.append(line)

    return "\n".join(result_lines)


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
        retry=retry_if_exception_type(
            (APIError, APIConnectionError, RateLimitError, APITimeoutError)
        ),
        reraise=True,
    )
    def __call__(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 2048,
        temperature: float = 0.0,
    ) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=120.0,  # 2 minute timeout per request
        )
        if not resp or not resp.choices:
            raise ValueError(f"Empty response from API for model {self.model}")
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
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key)
        self.llm = {
            role: LLM(self.client, config.model_id)
            for role in ("process", "validate", "status")
        }

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
        ]

        missing = [
            p for p in required_prompts if not (PROMPTS_DIR / f"{p}.md").exists()
        ]
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
        - extractions_failed: List of failed extraction dates
        - reports_generated: List of generated report names
        """
        state = self._load_state()

        # Count processed sections from filesystem
        processed_files = list(self.entries_dir.glob("*.processed.md"))
        failed_files = list(self.entries_dir.glob("*.failed.md"))
        extraction_failed_files = list(self.entries_dir.glob("*.failed.json"))

        return {
            "status": state.get("status", "not_started"),
            "started_at": state.get("started_at"),
            "completed_at": state.get("completed_at"),
            "sections_total": state.get("sections_total", 0),
            "sections_processed": len(processed_files),
            "sections_failed": [f.stem.replace(".failed", "") for f in failed_files],
            "extractions_failed": [
                f.stem.replace(".failed", "") for f in extraction_failed_files
            ],
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
        match = re.match(r"^```(?:json)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
        if match:
            text = match.group(1).strip()
        # Fallback: try to extract JSON object if it starts with { and ends with }
        elif text.startswith("```"):
            # Code block without proper closing - try to extract JSON anyway
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]  # Skip opening fence
            # Find where JSON ends (last closing brace)
            json_text = "\n".join(lines)
            if "```" in json_text:
                json_text = json_text.split("```")[0]
            text = json_text.strip()
        if not text:
            raise json.JSONDecodeError(
                "Empty JSON content after stripping code block", "", 0
            )
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

        sections = self._split_sections()
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
                labs_content = (
                    f"{LAB_SECTION_HEADER}\n{format_labs(self.labs_by_date[date])}\n"
                )

            exams_content = ""
            if date in self.medical_exams_by_date and self.medical_exams_by_date[date]:
                joined_exams = "\n\n".join(self.medical_exams_by_date[date])
                exams_content = f"{MEDICAL_EXAMS_SECTION_HEADER}\n{joined_exams}\n"

            processed_path = self.entries_dir / f"{date}.processed.md"
            deps = self._get_section_dependencies(sec, labs_content, exams_content)

            if self._check_needs_regeneration(processed_path, deps):
                to_process.append(sec)

        # Process (potentially in parallel)
        max_workers = self.config.max_workers
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex, tqdm(
            total=len(to_process), desc="Processing"
        ) as bar:
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
                    self.logger.error(
                        "Exception processing section %s: %s", date, e, exc_info=True
                    )
                    failed.append(date)
                bar.update(1)

        if failed:
            self.logger.error("Failed to process sections for: %s", ", ".join(failed))
        else:
            self.logger.info("All sections processed successfully")

        # Save collated health log (all processed entries newest to oldest)
        self._save_collated_health_log()

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
            joined_exams = "\n\n".join(exams_list)
            exams_path.write_text(
                f"{MEDICAL_EXAMS_SECTION_HEADER}\n{joined_exams}\n",
                encoding="utf-8",
            )

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
        return short_hash(self._prompt(name))

    def _hash_files_without_deps(self, paths: Iterable[Path]) -> str:
        """Compute combined hash of multiple files, excluding deps comments."""
        contents = []
        for path in sorted(paths):
            if path.exists():
                contents.append(self._read_without_deps_comment(path))
        return short_hash("\n\n".join(contents)) if contents else ""

    def _get_section_dependencies(
        self, section: str, labs_content: str, exams_content: str = ""
    ) -> dict[str, str]:
        """Compute all dependencies for a processed section."""
        return {
            "raw": short_hash(section),
            "labs": short_hash(labs_content) if labs_content else "none",
            "exams": short_hash(exams_content) if exams_content else "none",
            "process_prompt": self._hash_prompt("process.system_prompt"),
            "validate_prompt": self._hash_prompt("validate.system_prompt"),
        }

    def _check_needs_regeneration(
        self, path: Path, expected_deps: dict[str, str]
    ) -> bool:
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
            labs_content = (
                f"{LAB_SECTION_HEADER}\n{format_labs(self.labs_by_date[date])}\n"
            )

        # Get medical exams content for this date
        exams_content = ""
        if date in self.medical_exams_by_date and self.medical_exams_by_date[date]:
            joined_exams = "\n\n".join(self.medical_exams_by_date[date])
            exams_content = f"{MEDICAL_EXAMS_SECTION_HEADER}\n{joined_exams}\n"

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
                messages.append(
                    {
                        "role": "assistant",
                        "content": last_processed,
                    }
                )
                messages.append(
                    {
                        "role": "user",
                        "content": f"Your output was rejected because it was missing details:\n{last_validation}\n\nPlease try again, preserving ALL details including dosages, brand names, and additional ingredients.",
                    }
                )
            processed = self.llm["process"](messages)
            last_processed = processed

            # 2) VALIDATE
            validation = self.llm["validate"](
                [
                    {
                        "role": "system",
                        "content": self._prompt("validate.system_prompt"),
                    },
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
                processed_path.write_text(
                    f"{deps_comment}\n{final_content}", encoding="utf-8"
                )

                return date, True

            self.logger.error(
                "Validation failed (%s attempt %d): %s", date, attempt, validation
            )

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

    def _split_sections(self) -> list[str]:
        text = self.path.read_text(encoding="utf-8")

        # Locate the first dated section header (supports YYYY-MM-DD or YYYY/MM/DD)
        date_regex = r"^###\s*\d{4}[-/]\d{2}[-/]\d{2}"
        match = re.search(date_regex, text, flags=re.MULTILINE)
        if not match:
            raise ValueError(
                "No dated sections found (expected '### YYYY-MM-DD' or '### YYYY/MM/DD')."
            )

        # Content before first dated section is ignored
        body = text[match.start() :]

        # Split the remainder on dated section headers
        sections = [
            s.strip()
            for s in re.split(rf"(?={date_regex})", body, flags=re.MULTILINE)
            if s.strip()
        ]

        return sections

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
                self.logger.warning(
                    "LABS_PARSER_OUTPUT_PATH does not exist: %s", labs_path
                )
            elif not labs_path.is_dir():
                self.logger.warning(
                    "LABS_PARSER_OUTPUT_PATH is not a directory: %s", labs_path
                )
            else:
                agg_csv = labs_path / "all.csv"
                if agg_csv.exists():
                    try:
                        lab_dfs.append(pd.read_csv(agg_csv))
                        self.logger.info("Loaded aggregated labs from %s", agg_csv)
                    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
                        self.logger.error(
                            "Failed to parse aggregated labs CSV %s: %s", agg_csv, e
                        )

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
        labs_df = labs_df.rename(
            columns={k: v for k, v in column_mappings.items() if k in labs_df.columns}
        )

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
            self.logger.info(
                "Lab data: %d rows loaded, %d after filtering",
                initial_count,
                final_count,
            )

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
            self.logger.warning(
                "MEDICAL_EXAMS_PARSER_OUTPUT_PATH does not exist: %s", exams_path
            )
            return
        if not exams_path.is_dir():
            self.logger.warning(
                "MEDICAL_EXAMS_PARSER_OUTPUT_PATH is not a directory: %s", exams_path
            )
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
                    "Skipping directory without date prefix: %s", subdir.name
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
                "Skipped %d directories without valid date prefix", skipped_count
            )
        self.logger.info(
            "Loaded %d medical exam summaries for %d dates from %s",
            loaded_count,
            len(exams_by_date),
            exams_path,
        )

    def _save_collated_health_log(self) -> None:
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
        for date, entry_content in sorted_entries:
            parts.append(f"# {date}")
            parts.append(entry_content)

        content = "\n\n".join(parts)

        # Compute hash for dependency tracking
        content_hash = short_hash(content)
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
        data_dates = set(self.labs_by_date.keys()) | set(
            self.medical_exams_by_date.keys()
        )
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
                joined_exams = "\n\n".join(exams_list)
                exams_content = f"{MEDICAL_EXAMS_SECTION_HEADER}\n{joined_exams}\n"

            # Skip if neither labs nor exams exist
            if not labs_content and not exams_content:
                continue

            # Dependencies for data-only entries (no raw content, no LLM processing)
            deps = {
                "raw": "none",  # No raw health log content
                "labs": short_hash(labs_content) if labs_content else "none",
                "exams": short_hash(exams_content) if exams_content else "none",
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
            processed_path.write_text(
                f"{deps_comment}\n{processed_content}", encoding="utf-8"
            )

            # Describe what data types are present
            data_types = []
            if labs_content:
                data_types.append("labs")
            if exams_content:
                data_types.append("exams")
            self.logger.info(
                "Created entry for %s (%s, no health log entry)",
                date,
                " + ".join(data_types),
            )

        # Return original sections unchanged (placeholder files were created directly)
        return sections


# --------------------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------------------


def main() -> None:
    """Run the processor using configuration from profile."""
    parser = argparse.ArgumentParser(
        description="Process health log entries and generate structured markdown output.",
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

    parser.add_argument(
        "--env",
        type=str,
        default="claude",
        help="Environment name to load (loads .env.{name} instead of .env, default: claude)",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=None,
        help="Number of parallel processing workers (overrides profile/env setting)",
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

    # CLI --workers overrides profile/env setting
    if args.workers is not None:
        import os as _os

        max_cpu = _os.cpu_count() or 8
        config.max_workers = max(1, min(args.workers, max_cpu))

    # Handle --force-reprocess flag: clear cached outputs
    if args.force_reprocess:
        output_path = config.output_path
        entries_dir = output_path / "entries"

        # Delete all generated entry files
        if entries_dir.exists():
            patterns = [
                "*.processed.md",
                "*.labs.md",
                "*.exams.md",
                "*.failed.md",
            ]
            deleted = 0
            for pattern in patterns:
                for f in entries_dir.glob(pattern):
                    f.unlink()
                    deleted += 1
            if deleted:
                logger.info("Cleared %d generated files from %s", deleted, entries_dir)

        # Delete primary output files
        for filename in ["health_log.md"]:
            filepath = output_path / filename
            if filepath.exists():
                filepath.unlink()
                logger.info("Deleted %s", filepath)

        # Delete state file
        state_file = output_path / ".state.json"
        if state_file.exists():
            state_file.unlink()

        # Delete legacy files if they exist
        for legacy_file in [
            "health_timeline.csv",
            "reports",
            "entity_resolution.json",
            "current.yaml",
            "history.csv",
            "entities.json",
            "audit_template.md",
        ]:
            legacy_path = output_path / legacy_file
            if legacy_path.exists():
                if legacy_path.is_dir():
                    shutil.rmtree(legacy_path)
                else:
                    legacy_path.unlink()
                logger.info("Cleared legacy %s", legacy_path)

    if not check_api_accessibility(config.base_url):
        logger.warning("API base URL is not accessible: %s", config.base_url)
        logger.warning("Processing will likely fail on LLM-dependent tasks.")

    start = datetime.now()
    HealthLogProcessor(config).run()
    logger.info(
        "Finished in %.1fs",
        (datetime.now() - start).total_seconds(),
    )


if __name__ == "__main__":
    main()
