"""Process health journal entries into structured markdown.

The processor reads `### YYYY-MM-DD` sections, validates prompt availability,
runs LLM-backed processing for journal content, merges lab and medical exam
sidecars, and writes both per-date cached outputs and a collated `health_log.md`.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Final

import pandas as pd
from dateutil.parser import parse as date_parse
from dotenv import load_dotenv
from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm
from yaml import YAMLError, safe_load

from parsehealthlog.config import (
    Config,
    ProfileConfig,
    check_api_accessibility,
    get_config_dir,
    get_env_file,
    get_model_pricing,
)
from parsehealthlog.exceptions import ConfigurationError, DateExtractionError, PromptError
from parsehealthlog.types import (
    ChatMessage,
    DependencyMap,
    ExamFrontMatter,
    ExtractionStats,
    LabGroupPayload,
    PersistedState,
    ProgressSnapshot,
    ScalarLike,
)


def load_dotenv_for_env(env_name: str | None) -> None:
    """Load dotenv files from the user config directory."""
    if env_name:
        env_file = get_env_file(env_name)
        if env_file.exists():
            load_dotenv(env_file, override=True)
        else:
            print(f"Warning: {env_file} not found")
        return
    load_dotenv(get_env_file(), override=True)


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
JOURNAL_SECTION_HEADER: Final = "## Journal"
LAB_SECTION_HEADER: Final = "## Lab Results"
MEDICAL_EXAMS_SECTION_HEADER: Final = "## Medical Exams"


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


def format_journal_section(content: str) -> str:
    """Wrap processed journal content in a normalized Journal section."""
    normalized = normalize_markdown_headers(content.strip(), target_base_level=3)
    if not normalized.strip():
        return ""
    return f"{JOURNAL_SECTION_HEADER}\n\n{normalized}"


def split_lab_name(name: str) -> tuple[str, str | None, str]:
    """Split a standardized lab name into group, subgroup, and test label."""
    cleaned = str(name).strip()
    parts = [part.strip() for part in cleaned.split(" - ") if part.strip()]

    if len(parts) >= 3:
        return parts[0], parts[1], " - ".join(parts[2:])
    if len(parts) == 2:
        return parts[0], None, parts[1]
    return "Other", None, cleaned


def format_lab_line(
    name: str,
    value: ScalarLike,
    unit: str,
    reference_min: ScalarLike,
    reference_max: ScalarLike,
) -> str:
    """Format a single lab result line."""
    formatted_value = format_scalar(value)
    line = f"- **{name}:** {formatted_value}{f' {unit}' if unit else ''}"
    if pd.notna(reference_min) and pd.notna(reference_max):
        line += f" (ref: {format_scalar(reference_min)} - {format_scalar(reference_max)})"
    return line


def format_scalar(value: ScalarLike) -> str:
    """Render scalars without unnecessary trailing .0 for integers."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric.is_integer():
            return str(int(numeric))
    return str(value)


def format_labs(df: pd.DataFrame) -> str:
    """Format lab results for clinical review.

    Outputs raw values with reference ranges - clinical interpretation is
    delegated to downstream LLMs which can apply medical judgment.
    """
    grouped: dict[str, LabGroupPayload] = {}
    for row in df.itertuples():
        group, subgroup, test_name = split_lab_name(row.lab_name_standardized)
        bucket = grouped.setdefault(group, {"tests": [], "subgroups": {}})
        unit = str(getattr(row, "unit_normalized", "")).strip()
        rmin, rmax = row.reference_min_normalized, row.reference_max_normalized
        line = format_lab_line(test_name, row.value_normalized, unit, rmin, rmax)

        if subgroup:
            bucket["subgroups"].setdefault(subgroup, []).append(line)
        else:
            bucket["tests"].append(line)

    out: list[str] = []
    for group, payload in grouped.items():
        out.append(f"### {group}")

        if payload["tests"]:
            out.extend(payload["tests"])

        for subgroup_name, subgroup_lines in payload["subgroups"].items():
            out.append(f"#### {subgroup_name}")
            out.extend(subgroup_lines)

        out.append("")

    return "\n".join(out).strip()


def format_labs_section(df: pd.DataFrame) -> str:
    """Wrap formatted labs in a Lab Results section."""
    formatted = format_labs(df).strip()
    if not formatted:
        return ""
    return f"{LAB_SECTION_HEADER}\n\n{formatted}"


def parse_front_matter(content: str) -> tuple[ExamFrontMatter, str]:
    """Parse optional YAML front matter from markdown content."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return {}, stripped

    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", stripped, re.DOTALL)
    if not match:
        return {}, stripped

    metadata: ExamFrontMatter = {}
    try:
        loaded = safe_load(match.group(1)) or {}
        if isinstance(loaded, dict):
            for key in (
                "title",
                "exam_name_raw",
                "exam_date",
                "doctor",
                "facility",
                "department",
                "category",
            ):
                value = loaded.get(key)
                if value is not None:
                    metadata[key] = str(value)
    except YAMLError:
        metadata = {}

    return metadata, match.group(2).strip()


def is_markdown_list_block(block: str) -> bool:
    """Return True if a markdown block is entirely a list."""
    lines = [line for line in block.splitlines() if line.strip()]
    if not lines:
        return False
    return all(re.match(r"^\s*(?:[-*]|\d+\.)\s+", line) for line in lines)


def is_markdown_list_line(line: str) -> bool:
    """Return True if a line is a markdown list item."""
    return bool(re.match(r"^\s*(?:[-*]|\d+\.)\s+", line))


def flatten_markdown_block(block: str) -> str:
    """Flatten a markdown block into a single line."""
    parts = [re.sub(r"^#+\s*", "", line.strip()) for line in block.splitlines()]
    return " ".join(part for part in parts if part).strip()


def indent_markdown_block(block: str, spaces: int = 4) -> str:
    """Indent a markdown block for nesting under a bullet."""
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line.strip() else "" for line in block.splitlines())


def format_exam_metadata(metadata: ExamFrontMatter) -> str:
    """Render selected exam metadata fields as one bullet."""
    fields = [
        ("exam_date", "Date"),
        ("doctor", "Doctor"),
        ("facility", "Facility"),
        ("department", "Department"),
        ("category", "Category"),
    ]
    parts = []
    for key, label in fields:
        value = metadata.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(f"{label}: {text}")
    return f"- {'; '.join(parts)}" if parts else ""


def format_exam_summary(content: str) -> str:
    """Normalize a medical exam summary into structured markdown."""
    metadata, body = parse_front_matter(content)
    title = str(
        metadata.get("title") or metadata.get("exam_name_raw") or "Medical Exam"
    ).strip()

    parts = [f"### {title}"]
    metadata_line = format_exam_metadata(metadata)
    if metadata_line:
        parts.append(metadata_line)

    for block in re.split(r"\n\s*\n+", body):
        block = block.strip()
        if not block:
            continue
        if is_markdown_list_block(block):
            parts.append(block)
            continue

        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        list_start = next(
            (index for index, line in enumerate(lines) if is_markdown_list_line(line)),
            None,
        )
        if list_start is not None:
            intro = flatten_markdown_block("\n".join(lines[:list_start]))
            if intro:
                parts.append(f"- {intro}")

            list_block = "\n".join(lines[list_start:])
            if is_markdown_list_block(list_block):
                parts.append(indent_markdown_block(list_block) if intro else list_block)
                continue

        flattened = flatten_markdown_block(block)
        if flattened:
            parts.append(f"- {flattened}")

    return "\n\n".join(parts).strip()


def format_medical_exams_section(exams_list: list[str]) -> str:
    """Wrap normalized exam summaries in a Medical Exams section."""
    formatted_exams = [format_exam_summary(exam) for exam in exams_list if exam.strip()]
    if not formatted_exams:
        return ""
    return f"{MEDICAL_EXAMS_SECTION_HEADER}\n\n" + "\n\n".join(formatted_exams)


def assemble_entry_content(
    journal_content: str = "",
    labs_content: str = "",
    exams_content: str = "",
) -> str:
    """Assemble journal, lab, and exam sections for one date."""
    sections = [
        section.strip()
        for section in (journal_content, labs_content, exams_content)
        if section.strip()
    ]
    return "\n\n".join(sections)


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
        messages: list[ChatMessage],
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
        self.generated_files: set[Path] = set()
        self._generated_files_lock = threading.Lock()

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

    def _load_state(self) -> PersistedState:
        """Load state from state file, or return empty state if not exists."""
        if not self.state_file.exists():
            return {}
        try:
            state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, IOError) as e:
            self.logger.warning("Could not load state file: %s", e)
            return {}
        if not isinstance(state, dict):
            self.logger.warning("Could not load state file: expected an object")
            return {}
        return state

    def _save_state(self, state: PersistedState) -> None:
        """Save state to state file."""
        try:
            self.state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
        except IOError as e:
            self.logger.warning("Could not save state file: %s", e)

    def _update_state(self, **updates) -> None:
        """Update specific fields in state file."""
        state: PersistedState = self._load_state()
        state.update(updates)
        self._save_state(state)

    def _track_generated_file(self, path: Path) -> None:
        """Record a file written during the current run."""
        with self._generated_files_lock:
            self.generated_files.add(path)

    def _content_differs(self, path: Path, content: str) -> bool:
        """Return True when a file is missing or its content differs."""
        if not path.exists():
            return True
        return path.read_text(encoding="utf-8") != content

    def _write_text_if_changed(self, path: Path, content: str) -> bool:
        """Write text only when content changed, and track actual writes."""
        if not self._content_differs(path, content):
            return False
        path.write_text(content, encoding="utf-8")
        self._track_generated_file(path)
        return True

    def get_progress(self) -> ProgressSnapshot:
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
        state: PersistedState = self._load_state()

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
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: C901 – single orchestrator method is clearer here
        self._update_state(
            status="in_progress",
            started_at=datetime.now().isoformat(),
            completed_at=None,
        )

        stats: ExtractionStats = {
            "converted": 0,
            "deleted": 0,
            "failed": 0,
            "total": 0,
        }

        sections = self._split_sections()
        self._load_labs()
        self._load_medical_exams()

        orphaned = self._get_orphaned_entries(sections)
        if orphaned:
            for orphan_file in orphaned:
                orphan_file.unlink()
                self.logger.info("Deleted orphaned entry file: %s", orphan_file.name)
                stats["deleted"] += 1

        sections = self._create_placeholder_sections(sections)
        self._update_state(sections_total=len(sections))

        to_process: list[str] = []
        for sec in sections:
            plan = self._build_entry_plan(section=sec)
            self._write_text_if_changed(plan.raw_path, plan.raw_content)
            if self._check_needs_regeneration(plan.processed_path, plan.deps):
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
                        stats["failed"] += 1
                    else:
                        stats["converted"] += 1
                except Exception as e:
                    section = futures[fut]
                    try:
                        date = extract_date(section)
                    except DateExtractionError:
                        date = "(unknown date)"
                    self.logger.error(
                        "Exception processing section %s: %s", date, e, exc_info=True
                    )
                    failed.append(date)
                    stats["failed"] += 1
                bar.update(1)
                stats["total"] += 1

        if failed:
            self.logger.error("Failed to process sections for: %s", ", ".join(failed))
        else:
            self.logger.info("All sections processed successfully")

        self._save_collated_health_log()

        for date, df in self.labs_by_date.items():
            if df.empty:
                continue
            lab_path = self.entries_dir / f"{date}.labs.md"
            self._write_text_if_changed(
                lab_path,
                f"{format_labs_section(df)}\n",
            )

        for date, exams_list in self.medical_exams_by_date.items():
            if not exams_list:
                continue
            exams_path = self.entries_dir / f"{date}.exams.md"
            self._write_text_if_changed(
                exams_path,
                f"{format_medical_exams_section(exams_list)}\n",
            )

        self._validate_date_consistency(sections)
        self._print_extraction_summary(stats)
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

    @dataclass(slots=True)
    class EntryPlan:
        date: str
        raw_content: str
        raw_path: Path
        processed_path: Path
        labs_content: str
        exams_content: str
        deps: DependencyMap

    def _get_date_sidecar_content(self, date: str) -> tuple[str, str]:
        """Return rendered lab and exam sidecars for a date."""
        labs_content = ""
        df = self.labs_by_date.get(date)
        if df is not None and not df.empty:
            labs_content = format_labs_section(df)

        exams_content = ""
        exams_list = self.medical_exams_by_date.get(date)
        if exams_list:
            exams_content = format_medical_exams_section(exams_list)

        return labs_content, exams_content

    def _build_entry_plan(
        self, *, section: str | None = None, date: str | None = None
    ) -> EntryPlan:
        """Build the file paths, rendered sidecars, and deps for one date."""
        if section is None and date is None:
            raise ValueError("section or date is required")

        if section is not None:
            resolved_date = extract_date(section)
            raw_content = strip_date_header(section)
            raw_hash = short_hash(section)
            process_prompt_hash = self._hash_prompt("process.system_prompt")
            validate_prompt_hash = self._hash_prompt("validate.system_prompt")
        else:
            resolved_date = date
            raw_content = ""
            raw_hash = "none"
            process_prompt_hash = "none"
            validate_prompt_hash = "none"

        assert resolved_date is not None
        labs_content, exams_content = self._get_date_sidecar_content(resolved_date)
        deps = self._get_section_dependencies(
            raw_hash=raw_hash,
            labs_content=labs_content,
            exams_content=exams_content,
            process_prompt_hash=process_prompt_hash,
            validate_prompt_hash=validate_prompt_hash,
        )
        return self.EntryPlan(
            date=resolved_date,
            raw_content=raw_content,
            raw_path=self.entries_dir / f"{resolved_date}.raw.md",
            processed_path=self.entries_dir / f"{resolved_date}.processed.md",
            labs_content=labs_content,
            exams_content=exams_content,
            deps=deps,
        )

    def _get_section_dependencies(
        self,
        *,
        raw_hash: str,
        labs_content: str,
        exams_content: str = "",
        process_prompt_hash: str,
        validate_prompt_hash: str,
    ) -> DependencyMap:
        """Compute all dependencies for a processed section."""
        return {
            "raw": raw_hash,
            "labs": short_hash(labs_content) if labs_content else "none",
            "exams": short_hash(exams_content) if exams_content else "none",
            "process_prompt": process_prompt_hash,
            "validate_prompt": validate_prompt_hash,
        }

    def _check_needs_regeneration(
        self, path: Path, expected_deps: DependencyMap
    ) -> bool:
        """Check if a file needs regeneration based on its dependencies.

        Returns True if file doesn't exist or dependencies have changed.
        """
        if not path.exists():
            self.logger.info("Cache miss for %s: file does not exist", path.name)
            return True

        lines = path.read_text(encoding="utf-8").splitlines()
        first_line = lines[0] if lines else ""
        existing_deps = parse_deps_comment(first_line)

        # If no deps comment found (old format), regenerate
        if not existing_deps:
            self.logger.info("Cache miss for %s: no deps comment found", path.name)
            return True

        # Check if any dependency changed
        for key, expected_hash in expected_deps.items():
            if existing_deps.get(key) != expected_hash:
                self.logger.info(
                    "Cache miss for %s: %s changed (%s -> %s)",
                    path.name, key, existing_deps.get(key), expected_hash,
                )
                return True

        return False

    def _write_processed_entry(self, plan: EntryPlan, content: str) -> bool:
        """Write one processed entry with its dependency comment."""
        rendered = f"{format_deps_comment(plan.deps)}\n{content}"
        return self._write_text_if_changed(plan.processed_path, rendered)

    # --------------------------------------------------------------
    # Section processing (one dated section → validated markdown)
    # --------------------------------------------------------------

    def _process_section(self, section: str) -> tuple[str, bool]:
        plan = self._build_entry_plan(section=section)

        last_processed = ""
        last_validation = ""

        for attempt in range(1, 4):
            messages = [
                {"role": "system", "content": self._prompt("process.system_prompt")},
                {"role": "user", "content": plan.raw_content},
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
                        "content": (
                            "Your output was rejected because it was missing details:\n"
                            f"{last_validation}\n\nPlease try again, preserving ALL "
                            "details including dosages, brand names, and additional "
                            "ingredients."
                        ),
                    }
                )
            processed = self.llm["process"](messages)
            last_processed = processed

            validation = self.llm["validate"](
                [
                    {
                        "role": "system",
                        "content": self._prompt("validate.system_prompt"),
                    },
                    {
                        "role": "user",
                        "content": self._prompt("validate.user_prompt").format(
                            raw_section=plan.raw_content, processed_section=processed
                        ),
                    },
                ]
            )
            last_validation = validation

            if "$OK$" in validation:
                final_content = assemble_entry_content(
                    format_journal_section(processed),
                    plan.labs_content,
                    plan.exams_content,
                )
                self._write_processed_entry(plan, final_content)
                return plan.date, True

            self.logger.error(
                "Validation failed (%s attempt %d): %s", plan.date, attempt, validation
            )

        failed_path = self.entries_dir / f"{plan.date}.failed.md"
        diagnostic = f"""# Validation Failed: {plan.date}

## Raw Section (Input)
```
{plan.raw_content}
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

        return plan.date, False

    # --------------------------------------------------------------
    # Input pre-processing helpers
    # --------------------------------------------------------------

    def _split_sections(self) -> list[str]:
        text = self.path.read_text(encoding="utf-8")

        date_regex = r"^###\s*\d{4}[-/]\d{2}[-/]\d{2}"
        match = re.search(date_regex, text, flags=re.MULTILINE)
        if not match:
            raise ValueError(
                "No dated sections found (expected '### YYYY-MM-DD' or '### YYYY/MM/DD')."
            )

        body = text[match.start() :]
        sections = [
            s.strip()
            for s in re.split(rf"(?={date_regex})", body, flags=re.MULTILINE)
            if s.strip()
        ]

        seen: dict[str, int] = {}
        for sec in sections:
            date = extract_date(sec)
            seen[date] = seen.get(date, 0) + 1
        duplicates = [date for date, count in seen.items() if count > 1]
        if duplicates:
            raise ValueError(
                "Duplicate date sections found in source file — fix before running:\n"
                + "\n".join(f"  - {d}" for d in sorted(duplicates))
            )

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

        processed_entries = []
        for path in self.entries_dir.glob("*.processed.md"):
            date = path.stem.split(".")[0]
            content = self._read_without_deps_comment(path)
            normalized = normalize_markdown_headers(content, target_base_level=2)
            processed_entries.append((date, normalized))

        sorted_entries = sorted(processed_entries, key=lambda x: x[0], reverse=True)

        parts = []
        for date, entry_content in sorted_entries:
            parts.append(f"# {date}")
            parts.append(entry_content)

        content = "\n\n".join(parts)
        content_hash = short_hash(content)
        deps_comment = format_deps_comment({"content": content_hash})
        if collated_path.exists():
            existing_deps = parse_deps_comment(
                collated_path.read_text(encoding="utf-8").split("\n")[0]
            )
            if existing_deps.get("content") == content_hash:
                self.logger.info("Collated health log is up-to-date")
                return

        collated_path.write_text(f"{deps_comment}\n{content}", encoding="utf-8")
        self._track_generated_file(collated_path)
        self.logger.info(
            "Saved health log (%d entries, newest to oldest) to %s",
            len(sorted_entries),
            collated_path,
        )

    def _get_orphaned_entries(self, sections: list[str]) -> list[Path]:
        """Find entry files for dates that no longer exist in the source log.

        Compares dates from source log sections against existing entry files
        in the entries directory. Returns list of orphaned file paths that
        should be deleted.

        Args:
            sections: List of section strings from the source health log.

        Returns:
            List of Path objects for orphaned entry files.
        """
        source_dates = {extract_date(sec) for sec in sections}
        data_dates = set(self.labs_by_date.keys()) | set(
            self.medical_exams_by_date.keys()
        )
        valid_dates = source_dates | data_dates

        orphaned: list[Path] = []

        for entry_file in self.entries_dir.iterdir():
            date_match = re.match(r"(\d{4}-\d{2}-\d{2})", entry_file.name)
            if not date_match:
                continue
            file_date = date_match.group(1)

            if entry_file.name.endswith(".raw.md"):
                if file_date not in source_dates:
                    orphaned.append(entry_file)
            elif file_date not in valid_dates:
                orphaned.append(entry_file)

        return orphaned

    def _create_placeholder_sections(self, sections: list[str]) -> list[str]:
        """Create entry files directly for dates with labs/exams but no health log entries.

        Creates .processed.md (just labs/exams content) and separate files.
        No .raw.md is created since there's no raw health log content.
        Uses dependency tracking to detect when data changes.

        Returns the original sections list unchanged.
        """
        log_dates = {extract_date(sec) for sec in sections}
        data_dates = set(self.labs_by_date.keys()) | set(
            self.medical_exams_by_date.keys()
        )
        missing_dates = sorted(data_dates - log_dates)

        if not missing_dates:
            return sections

        for date in missing_dates:
            plan = self._build_entry_plan(date=date)
            if not plan.labs_content and not plan.exams_content:
                continue

            if not self._check_needs_regeneration(plan.processed_path, plan.deps):
                continue

            processed_content = assemble_entry_content(
                labs_content=plan.labs_content,
                exams_content=plan.exams_content,
            )
            self._write_processed_entry(plan, processed_content)

            data_types = []
            if plan.labs_content:
                data_types.append("labs")
            if plan.exams_content:
                data_types.append("exams")
            self.logger.info(
                "Created entry for %s (%s, no health log entry)",
                date,
                " + ".join(data_types),
            )

        return sections

    def _validate_date_consistency(self, sections: list[str]) -> None:
        """Assert that extracted dates match source file dates exactly.

        Compares dates from source log sections against dates found in the
        entries directory (from .raw.md files). Raises AssertionError if:
        - Dates in entries don't exist in source
        - Dates in source don't exist in entries
        - Any mismatch is detected

        Args:
            sections: List of section strings from the source health log.

        Raises:
            AssertionError: If date sets don't match exactly.
        """
        source_dates = {extract_date(sec) for sec in sections}
        entry_dates: set[str] = set()
        if self.entries_dir.exists():
            for raw_file in self.entries_dir.glob("*.raw.md"):
                date_match = re.match(r"(\d{4}-\d{2}-\d{2})", raw_file.name)
                if date_match:
                    entry_dates.add(date_match.group(1))

        missing_in_entries = source_dates - entry_dates
        extra_in_entries = entry_dates - source_dates

        if missing_in_entries or extra_in_entries:
            error_msg = "Date validation failed:\n"
            if missing_in_entries:
                error_msg += (
                    "  - Dates in source but missing from entries: "
                    f"{sorted(missing_in_entries)}\n"
                )
            if extra_in_entries:
                error_msg += (
                    "  - Dates in entries but not in source: "
                    f"{sorted(extra_in_entries)}\n"
                )
            raise AssertionError(error_msg)

        self.logger.info(
            "Date validation passed: %d dates in source match %d dates in entries",
            len(source_dates),
            len(entry_dates),
        )

    def _print_extraction_summary(self, stats: ExtractionStats) -> None:
        """Print extraction statistics summary."""
        print("\n" + "=" * 60)
        print("EXTRACTION SUMMARY")
        print("=" * 60)
        print(f"  Converted: {stats['converted']}")
        print(f"  Deleted:   {stats['deleted']}")
        print(f"  Failed:    {stats['failed']}")
        print(f"  Total:     {stats['total']}")
        if self.generated_files:
            print("\nGenerated files:")
            for path in sorted(self.generated_files):
                try:
                    display_path = path.relative_to(self.OUTPUT_PATH)
                except ValueError:
                    display_path = path
                print(f"  - {display_path}")
        print("=" * 60 + "\n")


# --------------------------------------------------------------------------------------
# Dry-run processor
# --------------------------------------------------------------------------------------


class DryRunHealthLogProcessor(HealthLogProcessor):
    """Dry-run processor that simulates processing without making changes.

    Tracks what would be processed, files that would be created/modified/deleted,
    and estimates API costs without actually calling LLMs or writing files.
    """

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self.sections_to_process: list[str] = []
        self.cache_hits: list[str] = []
        self.files_to_create: list[Path] = []
        self.files_to_modify: list[Path] = []
        self.files_to_delete: list[Path] = []
        self.estimated_input_tokens: int = 0
        self.estimated_output_tokens: int = 0
        self._force_reprocess: bool = False

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate: ~4 characters per token."""
        return len(text) // 4

    def _write_text_if_changed(self, path: Path, content: str) -> bool:
        """Track file changes without writing them during a dry run."""
        if not self._content_differs(path, content):
            return False
        targets = self.files_to_modify if path.exists() else self.files_to_create
        if path not in targets:
            targets.append(path)
        return True

    def _process_section(self, section: str) -> tuple[str, bool]:
        """Track what would be processed without calling LLM."""
        plan = self._build_entry_plan(section=section)
        process_prompt = self._prompt("process.system_prompt")
        self.estimated_input_tokens += self._estimate_tokens(process_prompt)
        self.estimated_input_tokens += self._estimate_tokens(plan.raw_content)
        self.estimated_input_tokens += self._estimate_tokens(plan.labs_content)
        self.estimated_input_tokens += self._estimate_tokens(plan.exams_content)
        self.estimated_output_tokens += self._estimate_tokens(plan.raw_content)

        validate_prompt = self._prompt("validate.system_prompt")
        validate_user = self._prompt("validate.user_prompt").format(
            raw_section=plan.raw_content, processed_section=plan.raw_content
        )
        self.estimated_input_tokens += self._estimate_tokens(validate_prompt)
        self.estimated_input_tokens += self._estimate_tokens(validate_user)
        self.estimated_output_tokens += 50

        final_content = assemble_entry_content(
            format_journal_section(plan.raw_content),
            plan.labs_content,
            plan.exams_content,
        )
        self._write_processed_entry(plan, final_content)
        return plan.date, True

    def run_dry(self) -> bool:
        """Simulate processing and return True if changes would be made.

        Returns:
            bool: True if processing would occur, False if all cache hits.
        """
        sections = self._split_sections()
        self._load_labs()
        self._load_medical_exams()

        orphaned = self._get_orphaned_entries(sections)
        self.files_to_delete.extend(orphaned)
        sections = self._create_placeholder_sections(sections)
        if hasattr(self, "_force_reprocess") and self._force_reprocess:
            if self.entries_dir.exists():
                for pattern in [
                    "*.processed.md",
                    "*.labs.md",
                    "*.exams.md",
                    "*.failed.md",
                ]:
                    for f in self.entries_dir.glob(pattern):
                        if f not in self.files_to_delete:
                            self.files_to_delete.append(f)

            collated_path = self.OUTPUT_PATH / "health_log.md"
            if collated_path.exists() and collated_path not in self.files_to_delete:
                self.files_to_delete.append(collated_path)

            state_file = self.OUTPUT_PATH / ".state.json"
            if state_file.exists() and state_file not in self.files_to_delete:
                self.files_to_delete.append(state_file)

        # Check each section
        for sec in sections:
            plan = self._build_entry_plan(section=sec)
            self._write_text_if_changed(plan.raw_path, plan.raw_content)
            if self._check_needs_regeneration(plan.processed_path, plan.deps):
                self.sections_to_process.append(plan.date)
            else:
                self.cache_hits.append(plan.date)

        for date, df in self.labs_by_date.items():
            if df.empty:
                continue
            lab_path = self.entries_dir / f"{date}.labs.md"
            lab_content = f"{format_labs_section(df)}\n"
            self._write_text_if_changed(lab_path, lab_content)

        for date, exams_list in self.medical_exams_by_date.items():
            if not exams_list:
                continue
            exams_path = self.entries_dir / f"{date}.exams.md"
            exams_content = f"{format_medical_exams_section(exams_list)}\n"
            self._write_text_if_changed(exams_path, exams_content)

        collated_path = self.OUTPUT_PATH / "health_log.md"
        if collated_path.exists():
            self.files_to_modify.append(collated_path)
        else:
            self.files_to_create.append(collated_path)

        return len(self.sections_to_process) > 0 or len(self.files_to_delete) > 0

    def print_summary(self) -> None:
        """Print dry-run summary to console."""
        print("\n" + "=" * 60)
        print("DRY RUN SUMMARY")
        print("=" * 60)
        print(
            "\nSections: "
            f"{len(self.sections_to_process)} to process, "
            f"{len(self.cache_hits)} up-to-date (cache hits)"
        )

        print(f"\nFiles to create: {len(self.files_to_create)}")
        print(f"Files to modify: {len(self.files_to_modify)}")
        print(f"Files to delete: {len(self.files_to_delete)}")

        num_sections = len(self.sections_to_process)
        api_calls = int(num_sections * (1 + 1.2))

        print(f"\nEstimated API calls: {api_calls}")

        if num_sections > 0:
            pricing = get_model_pricing(self.config.model_id)
            input_cost = (self.estimated_input_tokens / 1_000_000) * pricing["input"]
            output_cost = (self.estimated_output_tokens / 1_000_000) * pricing["output"]
            total_cost = input_cost + output_cost

            print(
                "Estimated tokens: "
                f"{self.estimated_input_tokens:,} input, "
                f"{self.estimated_output_tokens:,} output"
            )
            print(f"Estimated cost: ${total_cost:.4f} (model: {self.config.model_id})")

        print("\n" + "-" * 60)
        if num_sections == 0 and len(self.files_to_delete) == 0:
            print("No changes needed. All entries are up-to-date.")
        else:
            print("Processing required. Remove --dry-run to apply changes.")
        print("=" * 60 + "\n")


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
  parsehealthlog --profile tiago
  parsehealthlog --profile tiago --force-reprocess
  parsehealthlog --list-profiles
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
        default=None,
        help=(
            "Environment name to load from "
            f"{get_config_dir()}/.env.{{name}} instead of {get_config_dir()}/.env"
        ),
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=None,
        help="Number of parallel processing workers (overrides profile/env setting)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be processed without making any changes",
    )
    args = parser.parse_args()

    load_dotenv_for_env(args.env)

    setup_logging()
    logger = logging.getLogger(__name__)

    if args.list_profiles:
        profiles = ProfileConfig.list_profiles()
        if profiles:
            print("Available profiles:")
            for name in profiles:
                print(f"  - {name}")
        else:
            print(f"No profiles found in {get_config_dir() / 'profiles'}/")
            print("Create a YAML or JSON profile in that directory.")
        sys.exit(0)

    def run_profile(profile_name, args, logger):
        """Run processing for a single profile. Returns True on success, False on failure."""
        profile_path = ProfileConfig.find_profile_path(profile_name)

        if not profile_path:
            print(f"Error: Profile '{profile_name}' not found.")
            print("Use --list-profiles to see available profiles.")
            return False

        try:
            profile = ProfileConfig.from_file(profile_path)
            logger.info("Using profile: %s", profile.name)
        except (ConfigurationError, OSError) as e:
            print(f"Error loading profile '{profile_name}': {e}")
            return False

        try:
            config = Config.from_profile(profile)
        except ConfigurationError as e:
            print(f"Configuration error for profile '{profile_name}': {e}")
            return False

        if args.workers is not None:
            import os as _os

            max_cpu = _os.cpu_count() or 8
            config.max_workers = max(1, min(args.workers, max_cpu))

        if args.force_reprocess:
            output_path = config.output_path
            entries_dir = output_path / "entries"

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

            for filename in ["health_log.md"]:
                filepath = output_path / filename
                if filepath.exists():
                    filepath.unlink()
                    logger.info("Deleted %s", filepath)

            state_file = output_path / ".state.json"
            if state_file.exists():
                state_file.unlink()

        if not check_api_accessibility(config.base_url):
            logger.warning("API base URL is not accessible: %s", config.base_url)
            logger.warning("Processing will likely fail on LLM-dependent tasks.")

        if args.dry_run:
            processor = DryRunHealthLogProcessor(config)
            processor._force_reprocess = args.force_reprocess
            changes_needed = processor.run_dry()
            processor.print_summary()
            return not changes_needed

        start = datetime.now()
        HealthLogProcessor(config).run()
        logger.info(
            "Finished in %.1fs",
            (datetime.now() - start).total_seconds(),
        )
        return True

    if args.profile:
        success = run_profile(args.profile, args, logger)
        sys.exit(0 if success else 1)
    else:
        profiles = ProfileConfig.list_profiles()
        if not profiles:
            print(
                "No profiles found. Create a YAML or JSON profile in "
                f"{get_config_dir() / 'profiles'}"
            )
            sys.exit(1)

        all_succeeded = True
        for i, profile_name in enumerate(profiles):
            if i > 0:
                logger.info("---")
            logger.info("Processing profile: %s", profile_name)
            success = run_profile(profile_name, args, logger)
            if not success:
                all_succeeded = False

        if all_succeeded:
            logger.info("All profiles completed successfully.")
        else:
            logger.warning("One or more profiles failed.")
        sys.exit(0 if all_succeeded else 1)


if __name__ == "__main__":
    main()
