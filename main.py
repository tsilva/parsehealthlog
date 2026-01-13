from __future__ import annotations

"""Clean, DRY rewrite of the health-log parser / curator.

• Reads a markdown health log that uses `### YYYY-MM-DD` section headers.
• Delegates heavy-lifting (processing, validation, summarisation, question-generation,
  specialist plans, consensus, etc.) to LLM prompts stored in a local `prompts/` folder.
• Enriches the log with lab-result CSVs (per-log `labs.csv` *and* aggregated
  `LABS_PARSER_OUTPUT_PATH/all.csv`).
• Produces an output folder (default `output/` next to the script,
  configurable via `OUTPUT_PATH`) with:
      ├─ entries/               # dated sections and labs
      │   ├─ <date>.raw.md
      │   ├─ <date>.processed.md
      │   └─ <date>.labs.md
      ├─ intro.md               # any pre-dated content
      └─ reports/               # generated summaries and plans
          ├─ summary.md
          ├─ clarifying_questions.md
          ├─ next_steps_<spec>.md
          ├─ next_steps.md
          └─ output.md          # summary + all processed sections (reverse-chronological)

Configuration is managed through the Config dataclass (see config.py), which loads
and validates these environment variables:
    OPENROUTER_API_KEY           – mandatory (forwarded to openrouter.ai)
    HEALTH_LOG_PATH              – mandatory (path to the markdown health log)
    OUTPUT_PATH                  – mandatory (base directory for generated output)
    MODEL_ID                     – default model (fallback for all roles, default: gpt-4o-mini)
    PROCESS_MODEL_ID             – (optional) override for PROCESS stage
    VALIDATE_MODEL_ID            – (optional) override for VALIDATE stage
    QUESTIONS_MODEL_ID           – (optional) override for questions
    SUMMARY_MODEL_ID             – (optional) override for summary
    NEXT_STEPS_MODEL_ID          – (optional) override for next-steps generation
    LABS_PARSER_OUTPUT_PATH      – (optional) path to aggregated lab CSVs
    MAX_WORKERS                  – (optional) ThreadPoolExecutor size (default 4)
    QUESTIONS_RUNS               – (optional) how many diverse question sets to generate (default 3)

The implementation is ~50 % shorter than the original (~350 → ~170 LoC) while
maintaining identical behaviour.
"""

from dotenv import load_dotenv
load_dotenv(override=True)

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

from config import Config
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
        self.reports_dir = self.OUTPUT_PATH / "reports"
        self.reports_dir.mkdir(exist_ok=True)
        self.internal_dir = self.reports_dir / ".internal"
        self.internal_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger(__name__)

        # Prompts (lazy-load to keep __init__ lightweight)
        self.prompts: dict[str, str] = {}

        # OpenAI client + per-role models
        self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=config.openrouter_api_key)
        self.models = {
            "process": config.process_model_id,
            "validate": config.validate_model_id,
            "summary": config.summary_model_id,
            "questions": config.questions_model_id,
            "next_steps": config.next_steps_model_id,
            "status": config.status_model_id,
        }
        self.llm = {k: LLM(self.client, v) for k, v in self.models.items()}

        # Lab data per date – populated lazily
        self.labs_by_date: dict[str, pd.DataFrame] = {}

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
            "summary.system_prompt",
            "targeted_questions.system_prompt",
            "next_steps_unified.system_prompt",
            "merge_bullets.system_prompt",
            "action_plan.system_prompt",
            "experiments.system_prompt",
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

        # Create placeholder sections for dates with labs but no entries
        sections = self._create_placeholder_sections(sections)

        # Update state with total sections count
        self._update_state(sections_total=len(sections))

        # Write raw sections & compute which ones need processing
        to_process: list[str] = []
        for sec in sections:
            date = extract_date(sec)
            raw_path = self.entries_dir / f"{date}.raw.md"
            raw_path.write_text(sec, encoding="utf-8")

            # Check if processing needed based on dependencies
            labs_content = ""
            if date in self.labs_by_date and not self.labs_by_date[date].empty:
                labs_content = f"{LAB_SECTION_HEADER}\n{format_labs(self.labs_by_date[date])}\n"

            processed_path = self.entries_dir / f"{date}.processed.md"
            deps = self._get_section_dependencies(sec, labs_content)

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

        self.logger.info("Assembling final output and generating summary...")
        final_markdown = self._assemble_output(header_text)
        # Note: final_markdown is used as input for generating other reports
        # but output.md will be assembled at the end with additional sections

        # Build health timeline (replaces entity extraction + aggregation)
        # This is used as input for next_steps, experiments, targeted questions
        timeline_csv = self._build_health_timeline()

        # Targeted clarifying questions - LLM judges what needs follow-up
        # Send timeline CSV; LLM derives current state and identifies items needing follow-up
        self._generate_file(
            "targeted_clarifying_questions.md",
            "targeted_questions.system_prompt",
            role="questions",
            temperature=0.0,  # Deterministic output to avoid duplicates
            extra_messages=[{"role": "user", "content": timeline_csv}],
            description="targeted clarifying questions",
            dependencies=self._get_timeline_deps("targeted_questions.system_prompt"),
            hidden=True,
        )

        # Unified next steps (genius doctor prompt - uses timeline for focused input)
        # This includes lab recommendations in the "Labs & Testing" section
        self._generate_file(
            "next_steps.md",
            "next_steps_unified.system_prompt",
            role="next_steps",
            temperature=0.25,
            extra_messages=[{"role": "user", "content": timeline_csv}],
            description="unified next steps",
            dependencies=self._get_timeline_deps("next_steps_unified.system_prompt"),
            hidden=True,
        )

        # Experiments tracker (uses timeline for experiment context)
        today = datetime.now().strftime("%Y-%m-%d")
        experiments_prompt = self._prompt("experiments.system_prompt").format(today=today)
        self._generate_file(
            "experiments.md",
            experiments_prompt,
            role="next_steps",
            temperature=0.25,
            extra_messages=[{"role": "user", "content": timeline_csv}],
            description="experiments tracker",
            dependencies={
                "timeline": self._get_timeline_deps("experiments.system_prompt")["timeline"],
                "prompt": hash_content(experiments_prompt),
            },
            hidden=True,
        )

        # Action plan (synthesizes summary, next_steps, next_labs, experiments)
        self._generate_action_plan(today)

        # Assemble final output.md with all reports
        self.logger.info("Assembling final output.md with all reports...")
        output_path = self.reports_dir / "output.md"
        output_deps = self._get_output_deps()

        # Check if output.md needs regeneration
        if not self._check_needs_regeneration(output_path, output_deps):
            self.logger.info("output.md is up-to-date")
        else:
            output_markdown = self._assemble_final_output(header_text)
            deps_comment = format_deps_comment(output_deps)
            output_path.write_text(f"{deps_comment}\n{output_markdown}", encoding="utf-8")
            self.logger.info("Saved full report to %s", output_path)

        # Copy to REPORT_OUTPUT_PATH if configured
        if self.config.report_output_path:
            # Ensure parent directory exists
            self.config.report_output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(output_path, self.config.report_output_path)
            self.logger.info("Copied report to %s", self.config.report_output_path)

        # Track run completion
        reports = [f.name for f in self.reports_dir.glob("*.md")]
        self._update_state(
            status="completed" if not failed else "completed_with_errors",
            completed_at=datetime.now().isoformat(),
            reports_generated=reports,
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

    def _get_section_dependencies(self, section: str, labs_content: str) -> dict[str, str]:
        """Compute all dependencies for a processed section."""
        return {
            "raw": hash_content(section),
            "labs": hash_content(labs_content) if labs_content else "none",
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

    def _get_standard_report_deps(self, prompt_name: str) -> dict[str, str]:
        """Get standard dependencies for reports that use all processed sections + intro.

        Used by: summary, questions
        """
        return {
            "processed": self._hash_all_processed(),
            "intro": self._hash_intro(),
            "prompt": self._hash_prompt(prompt_name),
        }

    def _get_timeline_deps(self, prompt_name: str) -> dict[str, str]:
        """Get dependencies for reports that use health timeline.

        Used by: next_steps_unified, experiments, targeted_questions
        """
        timeline_path = self.OUTPUT_PATH / "health_timeline.csv"
        timeline_hash = hash_file(timeline_path) if timeline_path.exists() else "missing"
        return {
            "timeline": timeline_hash,
            "prompt": self._hash_prompt(prompt_name),
        }

    def _get_state_deps(self, prompt_name: str) -> dict[str, str]:
        """Get dependencies for reports that use current state.

        Used by: targeted_questions, next_steps_unified, experiments (after state integration)
        """
        state_path = self.internal_dir / "current_state.md"
        state_hash = hash_file(state_path) if state_path.exists() else "missing"
        return {
            "state": state_hash,
            "prompt": self._hash_prompt(prompt_name),
        }

    def _get_output_deps(self) -> dict[str, str]:
        """Get dependencies for final output.md.

        Depends on summary, next_steps, action_plan, experiments, targeted_questions,
        and all processed sections. All intermediate reports are in .internal/.
        """
        deps = {
            "processed": self._hash_all_processed(),
        }

        # Add hashes of key reports (from .internal/)
        for report_name in ["summary", "next_steps", "action_plan", "experiments", "targeted_clarifying_questions"]:
            report_path = self.internal_dir / f"{report_name}.md"
            report_hash = hash_file(report_path) or "missing"
            deps[report_name] = report_hash

        return deps

    # --------------------------------------------------------------
    # File generation with caching & merging (for multi-call variants)
    # --------------------------------------------------------------

    def _generate_file(
        self,
        filename: str,
        system_prompt_or_name: str,
        *,
        role: str,
        max_tokens: int = 8096,
        temperature: float = 0.0,
        calls: int = 1,
        extra_messages: Iterable[dict[str, str]] | None = None,
        description: str | None = None,
        dependencies: dict[str, str] | None = None,
        hidden: bool = False,
    ) -> str:
        # Hidden files go to .internal/, visible files go to reports/
        target_dir = self.internal_dir if hidden else self.reports_dir
        path = target_dir / filename

        # Check if file exists and is up-to-date
        if path.exists():
            needs_regen = dependencies is not None and self._check_needs_regeneration(path, dependencies)
            if not needs_regen:
                if description:
                    self.logger.info("%s already exists at %s", description.capitalize(), path)
                return self._read_without_deps_comment(path)

        if description:
            self.logger.info("Generating %s...", description)

        system_prompt = (
            system_prompt_or_name
            if "\n" in system_prompt_or_name  # crude check: treat raw prompt as content
            else self._prompt(system_prompt_or_name)
        )
        extra_messages = list(extra_messages or [])

        def make_call() -> str:
            return self.llm[role](
                [{"role": "system", "content": system_prompt}, *extra_messages],
                max_tokens=max_tokens,
                temperature=temperature,
            )

        outputs: list[str] = []
        base = Path(filename).stem
        suffix = Path(filename).suffix
        if calls > 1:
            desc = (description or base).capitalize()
            with tqdm(total=calls, desc=desc) as bar:
                for i in range(calls):
                    out = make_call()
                    outputs.append(out)
                    variant_path = target_dir / f"{base}_{i+1}{suffix}"
                    variant_path.write_text(out, encoding="utf-8")
                    bar.update(1)
        else:
            outputs.append(make_call())

        content = outputs[0] if calls == 1 else self._merge_outputs(outputs)

        # Write with dependencies comment if provided
        if dependencies:
            deps_comment = format_deps_comment(dependencies)
            path.write_text(f"{deps_comment}\n{content}", encoding="utf-8")
        else:
            path.write_text(content, encoding="utf-8")

        if description:
            self.logger.info("Saved %s to %s", description, path)
        return content

    def _merge_outputs(self, variants: list[str]) -> str:
        prompt = self._prompt("merge_bullets.system_prompt")
        merged = self.llm["summary"](
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": "\n\n".join(variants)},
            ],
            max_tokens=4096,
        )
        return merged

    def _generate_action_plan(self, today: str) -> str:
        """Generate action plan by synthesizing summary, next_steps, and experiments.

        The action plan is the primary output - a time-bucketed, prioritized list of
        actions the user should take (this week, this month, this quarter).
        """
        # Read the input reports from .internal/
        sections = []

        summary_path = self.internal_dir / "summary.md"
        if summary_path.exists():
            sections.append(f"## Clinical Summary\n\n{self._read_without_deps_comment(summary_path)}")

        next_steps_path = self.internal_dir / "next_steps.md"
        if next_steps_path.exists():
            sections.append(f"## Recommended Next Steps (includes lab recommendations)\n\n{self._read_without_deps_comment(next_steps_path)}")

        experiments_path = self.internal_dir / "experiments.md"
        if experiments_path.exists():
            sections.append(f"## Current Experiments\n\n{self._read_without_deps_comment(experiments_path)}")

        combined_input = "\n\n---\n\n".join(sections)

        # Find the most recent entry date
        processed_files = sorted(self.entries_dir.glob("*.processed.md"), reverse=True)
        last_entry_date = today
        if processed_files:
            last_entry_date = processed_files[0].stem.split(".")[0]

        # Format the prompt with dates
        action_prompt = self._prompt("action_plan.system_prompt").format(
            today=today,
            last_entry_date=last_entry_date,
        )

        # Dependencies include all input reports (from .internal/)
        deps = {
            "summary": hash_file(summary_path) or "missing",
            "next_steps": hash_file(next_steps_path) or "missing",
            "experiments": hash_file(experiments_path) or "missing",
            "prompt": hash_content(action_prompt),
        }

        return self._generate_file(
            "action_plan.md",
            action_prompt,
            role="next_steps",
            temperature=0.25,
            extra_messages=[{"role": "user", "content": combined_input}],
            description="action plan",
            dependencies=deps,
            hidden=True,
        )

    # --------------------------------------------------------------
    # Health Timeline building (replaces entity extraction + aggregation)
    # --------------------------------------------------------------

    def _build_health_timeline(self) -> str:
        """Build health timeline CSV from processed entries.

        Processes entries chronologically (oldest first), building a CSV timeline
        with episode IDs linking related events. Supports incremental updates.

        Returns the timeline CSV content.
        """
        self.logger.info("Building health timeline from processed entries...")

        timeline_path = self.OUTPUT_PATH / "health_timeline.csv"

        # Get all processed entries sorted chronologically (oldest first)
        entries = self._get_chronological_entries()
        if not entries:
            self.logger.warning("No processed entries found for timeline")
            return self._get_empty_timeline()

        # Parse existing timeline header if it exists
        processed_through, existing_hash, last_episode_num = self._parse_timeline_header(timeline_path)

        # Split entries into historical and new
        historical_entries = [(d, c) for d, c in entries if d <= processed_through] if processed_through else []
        new_entries = [(d, c) for d, c in entries if d > processed_through] if processed_through else entries

        # Compute hash of historical entries
        historical_hash = hash_content("".join(c for _, c in historical_entries)) if historical_entries else ""

        # Determine processing mode
        if processed_through and historical_hash == existing_hash and not new_entries:
            # Cache hit - timeline is up-to-date
            self.logger.info("Health timeline is up-to-date (processed through %s)", processed_through)
            return self._read_timeline_content(timeline_path)

        if processed_through and historical_hash == existing_hash and new_entries:
            # Incremental mode - only process new entries
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
            new_last_date = new_entries[-1][0]
            new_hash = hash_content("".join(c for _, c in entries))
            new_last_ep = self._get_last_episode_num(updated_content)
            self._save_timeline(timeline_path, updated_content, new_last_date, new_hash, new_last_ep)
            return updated_content

        # Full reprocess - historical entry changed or first run
        self.logger.info("Full timeline rebuild: processing %d entries", len(entries))
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
        full_hash = hash_content("".join(c for _, c in entries))
        last_ep = self._get_last_episode_num(timeline_content)
        self._save_timeline(timeline_path, timeline_content, last_date, full_hash, last_ep)

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

    def _parse_timeline_header(self, path: Path) -> tuple[str | None, str | None, int]:
        """Parse timeline header to get processed_through, hash, and last episode ID.

        Returns:
            (processed_through_date, entries_hash, last_episode_num)
        """
        if not path.exists():
            return None, None, 0

        try:
            first_line = path.read_text(encoding="utf-8").split("\n")[0]
            # Expected format: # Last updated: YYYY-MM-DD | Processed through: YYYY-MM-DD | Hash: xxx | LastEp: N
            match = re.match(
                r"#\s*Last updated:.*\|\s*Processed through:\s*(\d{4}-\d{2}-\d{2})\s*\|\s*Hash:\s*(\w+)\s*\|\s*LastEp:\s*(\d+)",
                first_line
            )
            if match:
                return match.group(1), match.group(2), int(match.group(3))
        except (IOError, IndexError):
            pass
        return None, None, 0

    def _read_timeline_content(self, path: Path) -> str:
        """Read timeline content, skipping the header comment line."""
        lines = path.read_text(encoding="utf-8").split("\n")
        # Skip header comment if present
        if lines and lines[0].startswith("#"):
            return "\n".join(lines[1:])
        return "\n".join(lines)

    def _save_timeline(self, path: Path, content: str, processed_through: str, entries_hash: str, last_ep: int) -> None:
        """Save timeline with metadata header."""
        today = datetime.now().strftime("%Y-%m-%d")
        header = f"# Last updated: {today} | Processed through: {processed_through} | Hash: {entries_hash} | LastEp: {last_ep}"
        path.write_text(f"{header}\n{content}", encoding="utf-8")
        self.logger.info("Saved health timeline to %s", path)

    def _get_last_episode_num(self, timeline_content: str) -> int:
        """Extract the highest episode number from timeline content."""
        matches = re.findall(r"ep-(\d+)", timeline_content)
        if matches:
            return max(int(m) for m in matches)
        return 0

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

        # Get labs content for this date
        labs_content = ""
        if date in self.labs_by_date and not self.labs_by_date[date].empty:
            labs_content = f"{LAB_SECTION_HEADER}\n{format_labs(self.labs_by_date[date])}\n"

        # Compute dependencies for this section
        deps = self._get_section_dependencies(section, labs_content)

        last_processed = ""
        last_validation = ""

        for attempt in range(1, 4):
            # 1) PROCESS - include validation feedback on retries
            messages = [
                {"role": "system", "content": self._prompt("process.system_prompt")},
                {"role": "user", "content": section},
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
                            raw_section=section, processed_section=processed
                        ),
                    },
                ]
            )
            last_validation = validation

            if "$OK$" in validation:
                # Include labs in processed file if they exist
                final_content = processed
                if labs_content:
                    final_content = f"{processed}\n\n{labs_content}"

                # Write with dependency hash in first line
                deps_comment = format_deps_comment(deps)
                processed_path.write_text(f"{deps_comment}\n{final_content}", encoding="utf-8")

                # Labs are also written to separate .labs.md file
                if (df := self.labs_by_date.get(date)) is not None and not df.empty:
                    lab_path = self.entries_dir / f"{date}.labs.md"
                    lab_path.write_text(labs_content, encoding="utf-8")
                return date, True

            self.logger.error("Validation failed (%s attempt %d): %s", date, attempt, validation)

        # Save diagnostic info for failed validation
        failed_path = self.entries_dir / f"{date}.failed.md"
        diagnostic = f"""# Validation Failed: {date}

## Raw Section (Input)
```
{section}
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

    def _create_placeholder_sections(self, sections: list[str]) -> list[str]:
        """Create entry files directly for dates with labs but no health log entries.

        Creates .processed.md (just date header) and .labs.md (lab results) files.
        No .raw.md is created since there's no raw health log content.
        Uses dependency tracking to detect when labs data changes.

        Returns the original sections list unchanged.
        """
        # Extract dates from existing sections
        log_dates = {extract_date(sec) for sec in sections}

        # Find dates with labs but no entries
        missing_dates = sorted(set(self.labs_by_date) - log_dates)

        if not missing_dates:
            return sections

        # Create entry files directly for dates with only labs
        for date in missing_dates:
            df = self.labs_by_date[date]
            if df.empty:
                continue

            # Format labs content
            labs_content = f"{LAB_SECTION_HEADER}\n{format_labs(df)}\n"

            # Dependencies for lab-only entries (no raw content, no LLM processing)
            deps = {
                "raw": "none",  # No raw health log content
                "labs": hash_content(labs_content),
                "process_prompt": "none",  # Not processed by LLM
                "validate_prompt": "none",  # Not validated
            }

            # Check if processed file exists and is up-to-date
            processed_path = self.entries_dir / f"{date}.processed.md"
            if not self._check_needs_regeneration(processed_path, deps):
                continue  # up-to-date

            # Write processed file (deps comment + date header + labs)
            # No raw file needed - there's no raw content to store
            # Labs are written to both .labs.md (separate) and .processed.md (for output)
            processed_content = f"### {date}\n\n{labs_content}"
            deps_comment = format_deps_comment(deps)
            processed_path.write_text(f"{deps_comment}\n{processed_content}", encoding="utf-8")

            self.logger.info("Created entry for %s (labs only, no health log entry)", date)

        # Return original sections unchanged (placeholder files were created directly)
        return sections

    # --------------------------------------------------------------
    # Output assembly
    # --------------------------------------------------------------

    def _get_processed_entries_text(self) -> str:
        """Read all processed entries, return them sorted newest-first."""
        items: list[tuple[str, str]] = []
        for path in self.entries_dir.glob("*.processed.md"):
            date = path.stem.split(".")[0]
            items.append((date, self._read_without_deps_comment(path)))
        return "\n\n".join(v for _, v in sorted(items, key=lambda t: t[0], reverse=True))

    def _assemble_output(self, header_text: str) -> str:
        """Assemble summary + processed entries (used as input for other reports)."""
        processed_text = self._get_processed_entries_text()

        # Generate summary with dependency tracking (hidden in .internal/)
        summary = self._generate_file(
            "summary.md",
            "summary.system_prompt",
            role="summary",
            extra_messages=[{"role": "user", "content": "\n\n".join(filter(None, [header_text, processed_text]))}],
            description="summary",
            dependencies=self._get_standard_report_deps("summary.system_prompt"),
            hidden=True,
        )
        return summary + "\n\n" + processed_text

    def _assemble_final_output(self, header_text: str) -> str:
        """Assemble final output.md - the single comprehensive report.

        Structure:
        1. # Health Report
        2. ## Questions to Address (stale items needing updates)
        3. ## Action Plan (prioritized what-to-do-next)
        4. ## Experiments (N=1 tracking)
        5. ## Clinical Summary
        6. --- (clear demarcation)
        7. ## Health Log Entries (all processed entries)
        """
        sections = ["# Health Report"]

        # 1. Questions to Address (stale items needing user input)
        questions_path = self.internal_dir / "targeted_clarifying_questions.md"
        if questions_path.exists():
            questions_content = self._read_without_deps_comment(questions_path).strip()
            # Only include if there are actual questions (not the "no items" placeholder)
            if questions_content and "No items require status updates" not in questions_content:
                sections.append(f"## Questions to Address\n\n{questions_content}")

        # 2. Action Plan (PRIMARY - what to do next)
        action_plan_path = self.internal_dir / "action_plan.md"
        if action_plan_path.exists():
            content = self._read_without_deps_comment(action_plan_path).strip()
            # Action plan usually has its own header, so include as-is
            sections.append(content)

        # 3. Experiments (N=1 tracking)
        experiments_path = self.internal_dir / "experiments.md"
        if experiments_path.exists():
            content = self._read_without_deps_comment(experiments_path).strip()
            sections.append(content)

        # 4. Clinical Summary
        summary_path = self.internal_dir / "summary.md"
        if summary_path.exists():
            sections.append(f"## Clinical Summary\n\n{self._read_without_deps_comment(summary_path).strip()}")

        # 5. Clear demarcation before full entries
        sections.append("---\n<!-- FULL HEALTH LOG ENTRIES BELOW - clip here if sharing -->")

        # 6. All processed entries (newest first)
        processed_text = self._get_processed_entries_text()
        if processed_text:
            sections.append(f"## Health Log Entries\n\n{processed_text}")

        return "\n\n".join(sections)


# --------------------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------------------


def main() -> None:
    """Run the processor using configuration from environment variables."""
    setup_logging()

    try:
        config = Config.from_env()
    except ValueError as e:
        raise SystemExit(f"Configuration error: {e}")

    start = datetime.now()
    HealthLogProcessor(config).run()
    logging.getLogger(__name__).info(
        "Finished in %.1fs",
        (datetime.now() - start).total_seconds(),
    )


if __name__ == "__main__":
    main()
