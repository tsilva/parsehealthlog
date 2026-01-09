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

    Sets up console output (INFO+) and error file logging.
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

    # File handler for errors only
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    error_hdlr = logging.FileHandler(logs_dir / "error.log", encoding="utf-8")
    error_hdlr.setLevel(logging.ERROR)
    error_hdlr.setFormatter(formatter)
    logger.addHandler(error_hdlr)

    # Quiet noisy dependencies
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


# --------------------------------------------------------------------------------------
# Utility functions
# --------------------------------------------------------------------------------------


PROMPTS_DIR: Final = Path(__file__).with_suffix("").parent / "prompts"
LAB_SECTION_HEADER: Final = "Lab test results:"
SPECIALTIES: Final[list[str]] = [
    "endocrinology",
    "gastroenterology",
    "cardiology",
    "dermatology",
    "pulmonology",
    "urology",
    "hematology",
    "neurogastroenterology",
    "neurology",
    "psychiatry",
    "nutrition",
    "rheumatology",
    "internal medicine",
    "genetics"
]

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
    out: list[str] = []
    for row in df.itertuples():
        name = str(row.lab_name_standardized).strip()
        value = row.value_normalized
        unit = str(getattr(row, "unit_normalized", "")).strip()
        rmin, rmax = row.reference_min_normalized, row.reference_max_normalized

        is_bool = unit.lower() in {"boolean", "bool"}
        if is_bool:
            sval = str(value).strip().lower()
            is_positive = sval in {"1", "1.0", "true", "positive", "yes"}
            line = f"- **{name}:** {'Positive' if is_positive else 'Negative'}"
        else:
            line = f"- **{name}:** {value}{f' {unit}' if unit else ''}"
            if pd.notna(rmin) and pd.notna(rmax):
                line += f" ({rmin} - {rmax})"

        if pd.notna(rmin) and pd.notna(rmax):
            try:
                if is_bool:
                    v = 1.0 if is_positive else 0.0
                else:
                    v = float(value)
                lo, hi = map(float, (rmin, rmax))
                status = "BELOW RANGE" if v < lo else "ABOVE RANGE" if v > hi else "OK"
                line += f" [{status}]"
            except (ValueError, TypeError) as e:
                # Log conversion failures (e.g., non-numeric values) at debug level
                logging.getLogger(__name__).debug(
                    "Could not compute range status for %s (value=%r, range=%r-%r): %s",
                    name, value, rmin, rmax, e
                )

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
        return resp.choices[0].message.content.strip()


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
            "questions.system_prompt",
            "specialist_next_steps.system_prompt",
            "consensus_next_steps.system_prompt",
            "next_labs.system_prompt",
            "merge_bullets.system_prompt",
            "action_plan.system_prompt",
            "experiments.system_prompt",
            "extract_entities.system_prompt",
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
        # Match ```json ... ``` or ``` ... ```
        match = re.match(r'^```(?:json)?\s*\n?(.*?)\n?```$', text, re.DOTALL)
        if match:
            text = match.group(1).strip()
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

        # Clarifying questions
        q_runs = self.config.questions_runs
        self._generate_file(
            "clarifying_questions.md",
            "questions.system_prompt",
            role="questions",
            temperature=1.0,
            calls=q_runs,
            extra_messages=[{"role": "user", "content": final_markdown}],
            description="clarifying questions",
            dependencies=self._get_standard_report_deps("questions.system_prompt"),
        )

        # Specialist next steps + consensus
        spec_outputs = []
        for spec in SPECIALTIES:
            spec_file = f"next_steps_{spec.replace(' ', '_')}.md"
            prompt = self._prompt("specialist_next_steps.system_prompt").format(specialty=spec)

            # Dependencies include formatted prompt (with specialty)
            deps = {
                "processed": self._hash_all_processed(),
                "intro": self._hash_intro(),
                "prompt": hash_content(prompt),  # Hash the formatted prompt
            }

            spec_outputs.append(
                self._generate_file(
                    spec_file,
                    prompt,
                    role="next_steps",
                    temperature=0.25,
                    extra_messages=[{"role": "user", "content": final_markdown}],
                    description=f"{spec} next steps",
                    dependencies=deps,
                )
            )

        # Consensus next steps
        self._generate_file(
            "next_steps.md",
            "consensus_next_steps.system_prompt",
            role="next_steps",
            temperature=0.25,
            extra_messages=[{"role": "user", "content": "\n\n".join(spec_outputs)}],
            description="consensus next steps",
            dependencies=self._get_consensus_report_deps(),
        )

        # Next labs
        prompt = self._prompt("next_labs.system_prompt")
        self._generate_file(
            "next_labs.md",
            prompt,
            role="next_steps",
            temperature=0.25,
            extra_messages=[{"role": "user", "content": final_markdown}],
            description="next labs",
            dependencies=self._get_standard_report_deps("next_labs.system_prompt"),
        )

        # Experiments tracker
        today = datetime.now().strftime("%Y-%m-%d")
        experiments_prompt = self._prompt("experiments.system_prompt").format(today=today)
        self._generate_file(
            "experiments.md",
            experiments_prompt,
            role="next_steps",
            temperature=0.25,
            extra_messages=[{"role": "user", "content": final_markdown}],
            description="experiments tracker",
            dependencies={
                "processed": self._hash_all_processed(),
                "intro": self._hash_intro(),
                "prompt": hash_content(experiments_prompt),
            },
        )

        # Action plan (synthesizes summary, next_steps, next_labs, experiments)
        self._generate_action_plan(today)

        # Build state model (entity extraction + aggregation + trend analysis)
        self._build_state_model()

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

    def _hash_all_specialist_reports(self) -> str:
        """Compute combined hash of all specialist next steps reports."""
        paths = [self.reports_dir / f"next_steps_{s.replace(' ', '_')}.md" for s in SPECIALTIES]
        return self._hash_files_without_deps(paths)

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

        Used by: summary, questions, next_labs, specialist next steps
        """
        return {
            "processed": self._hash_all_processed(),
            "intro": self._hash_intro(),
            "prompt": self._hash_prompt(prompt_name),
        }

    def _get_consensus_report_deps(self) -> dict[str, str]:
        """Get dependencies for consensus next steps report.

        Depends on all specialist reports + consensus prompt.
        """
        return {
            "specialist_reports": self._hash_all_specialist_reports(),
            "prompt": self._hash_prompt("consensus_next_steps.system_prompt"),
        }

    def _get_output_deps(self) -> dict[str, str]:
        """Get dependencies for final output.md.

        Depends on summary, next_steps, next_labs, action_plan, experiments, and all processed sections.
        """
        deps = {
            "processed": self._hash_all_processed(),
        }

        # Add hashes of key reports
        for report_name in ["summary", "next_steps", "next_labs", "action_plan", "experiments"]:
            report_path = self.reports_dir / f"{report_name}.md"
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
    ) -> str:
        path = self.reports_dir / filename

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
                    variant_path = self.reports_dir / f"{base}_{i+1}{suffix}"
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
        """Generate action plan by synthesizing summary, next_steps, next_labs, and experiments.

        The action plan is the primary output - a time-bucketed, prioritized list of
        actions the user should take (this week, this month, this quarter).
        """
        # Read the input reports
        sections = []

        summary_path = self.reports_dir / "summary.md"
        if summary_path.exists():
            sections.append(f"## Clinical Summary\n\n{self._read_without_deps_comment(summary_path)}")

        next_steps_path = self.reports_dir / "next_steps.md"
        if next_steps_path.exists():
            sections.append(f"## Recommended Next Steps\n\n{self._read_without_deps_comment(next_steps_path)}")

        next_labs_path = self.reports_dir / "next_labs.md"
        if next_labs_path.exists():
            sections.append(f"## Suggested Lab Tests\n\n{self._read_without_deps_comment(next_labs_path)}")

        experiments_path = self.reports_dir / "experiments.md"
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

        # Dependencies include all input reports
        deps = {
            "summary": hash_file(summary_path) or "missing",
            "next_steps": hash_file(next_steps_path) or "missing",
            "next_labs": hash_file(next_labs_path) or "missing",
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
        )

    # --------------------------------------------------------------
    # State model building (entity extraction + aggregation)
    # --------------------------------------------------------------

    def _build_state_model(self) -> dict:
        """Build comprehensive state model from all processed sections.

        Extracts entities from each section, merges them into a single state,
        and computes trends for labs and symptoms.

        Returns the state dict and saves it to state_model.json.
        """
        self.logger.info("Building state model from processed sections...")

        # Get all processed sections
        processed_files = sorted(self.entries_dir.glob("*.processed.md"))
        if not processed_files:
            self.logger.warning("No processed sections found for state model")
            return {}

        # Check if state model is up-to-date
        state_model_path = self.OUTPUT_PATH / "state_model.json"
        processed_hash = self._hash_all_processed()
        state_deps = {
            "processed": processed_hash,
            "prompt": self._hash_prompt("extract_entities.system_prompt"),
        }

        if state_model_path.exists():
            try:
                existing_state = json.loads(state_model_path.read_text(encoding="utf-8"))
                if existing_state.get("_deps", {}) == state_deps:
                    self.logger.info("State model is up-to-date")
                    return existing_state
            except (json.JSONDecodeError, IOError):
                pass  # Regenerate

        # Extract entities from each section
        entity_prompt = self._prompt("extract_entities.system_prompt")
        all_entities: list[dict] = []

        # Process in batches for efficiency
        with tqdm(total=len(processed_files), desc="Extracting entities") as bar:
            for path in processed_files:
                content = self._read_without_deps_comment(path)
                date = path.stem.split(".")[0]

                try:
                    response = self.llm["process"](
                        [
                            {"role": "system", "content": entity_prompt},
                            {"role": "user", "content": content},
                        ],
                        temperature=0.0,
                    )

                    # Parse JSON response
                    entities = self._parse_json_response(response)
                    entities["date"] = date  # Ensure date is set
                    all_entities.append(entities)
                except (json.JSONDecodeError, Exception) as e:
                    self.logger.warning("Failed to extract entities from %s: %s", date, e)

                bar.update(1)

        # Aggregate entities into state model
        state = self._aggregate_entities(all_entities)

        # Add trend analysis
        state["lab_trends"] = self._compute_lab_trends(all_entities)
        state["symptom_trends"] = self._compute_symptom_trends(all_entities)

        # Add metadata
        state["_deps"] = state_deps
        state["_generated_at"] = datetime.now().isoformat()
        state["_entries_count"] = len(processed_files)

        # Save state model
        state_model_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        self.logger.info("Saved state model to %s", state_model_path)

        return state

    def _aggregate_entities(self, all_entities: list[dict]) -> dict:
        """Aggregate entities from all sections into unified state."""
        state = {
            "conditions": {},
            "medications": {"current": {}, "past": {}},
            "supplements": {"current": {}, "past": {}},
            "symptoms": {},
            "providers": {},
            "experiments": {"active": {}, "completed": {}},
            "todos": [],
        }

        for entry in sorted(all_entities, key=lambda x: x.get("date", "")):
            date = entry.get("date", "")

            # Conditions
            for cond in entry.get("conditions", []):
                name = cond.get("name", "").lower()
                if not name:
                    continue
                if name not in state["conditions"]:
                    state["conditions"][name] = {
                        "name": name,
                        "status": cond.get("status", "active"),
                        "first_noted": date,
                        "last_updated": date,
                        "history": [],
                    }
                state["conditions"][name]["last_updated"] = date
                state["conditions"][name]["status"] = cond.get("status", state["conditions"][name]["status"])
                state["conditions"][name]["history"].append({
                    "date": date,
                    "event": cond.get("event"),
                    "details": cond.get("details"),
                })

            # Medications
            for med in entry.get("medications", []):
                name = med.get("name", "")
                if not name:
                    continue
                event = med.get("event", "mentioned")
                med_entry = {
                    "name": name,
                    "dose": med.get("dose"),
                    "frequency": med.get("frequency"),
                    "started": date if event == "started" else None,
                    "stopped": date if event == "stopped" else None,
                    "last_mentioned": date,
                }
                if event == "stopped":
                    state["medications"]["past"][name] = med_entry
                    state["medications"]["current"].pop(name, None)
                elif event in ("started", "continued", "mentioned", "adjusted"):
                    if name not in state["medications"]["current"]:
                        state["medications"]["current"][name] = med_entry
                    else:
                        state["medications"]["current"][name]["last_mentioned"] = date
                        if med.get("dose"):
                            state["medications"]["current"][name]["dose"] = med.get("dose")

            # Supplements (same logic as medications)
            for supp in entry.get("supplements", []):
                name = supp.get("name", "")
                if not name:
                    continue
                event = supp.get("event", "mentioned")
                supp_entry = {
                    "name": name,
                    "dose": supp.get("dose"),
                    "frequency": supp.get("frequency"),
                    "started": date if event == "started" else None,
                    "stopped": date if event == "stopped" else None,
                    "last_mentioned": date,
                }
                if event == "stopped":
                    state["supplements"]["past"][name] = supp_entry
                    state["supplements"]["current"].pop(name, None)
                elif event in ("started", "continued", "mentioned", "adjusted"):
                    if name not in state["supplements"]["current"]:
                        state["supplements"]["current"][name] = supp_entry
                    else:
                        state["supplements"]["current"][name]["last_mentioned"] = date
                        if supp.get("dose"):
                            state["supplements"]["current"][name]["dose"] = supp.get("dose")

            # Symptoms
            for sym in entry.get("symptoms", []):
                name = sym.get("name", "").lower()
                if not name:
                    continue
                if name not in state["symptoms"]:
                    state["symptoms"][name] = {
                        "name": name,
                        "first_noted": date,
                        "last_noted": date,
                        "observations": [],
                    }
                state["symptoms"][name]["last_noted"] = date
                state["symptoms"][name]["observations"].append({
                    "date": date,
                    "severity": sym.get("severity"),
                    "trend": sym.get("trend"),
                    "details": sym.get("details"),
                })

            # Providers
            for prov in entry.get("providers", []):
                name = prov.get("name", "")
                if not name:
                    continue
                if name not in state["providers"]:
                    state["providers"][name] = {
                        "name": name,
                        "specialty": prov.get("specialty"),
                        "location": prov.get("location"),
                        "visits": [],
                    }
                state["providers"][name]["visits"].append({
                    "date": date,
                    "type": prov.get("visit_type"),
                    "notes": prov.get("notes"),
                })

            # Experiments
            for exp in entry.get("experiments", []):
                name = exp.get("name", "")
                if not name:
                    continue
                event = exp.get("event", "update")
                if event == "end":
                    if name in state["experiments"]["active"]:
                        state["experiments"]["completed"][name] = state["experiments"]["active"].pop(name)
                        state["experiments"]["completed"][name]["ended"] = date
                        state["experiments"]["completed"][name]["outcome"] = exp.get("details")
                elif event == "start":
                    state["experiments"]["active"][name] = {
                        "name": name,
                        "started": date,
                        "hypothesis": exp.get("details"),
                        "updates": [],
                    }
                elif event == "update":
                    if name in state["experiments"]["active"]:
                        state["experiments"]["active"][name]["updates"].append({
                            "date": date,
                            "observation": exp.get("details"),
                        })

            # TODOs
            for todo in entry.get("todos", []):
                if todo and todo not in state["todos"]:
                    state["todos"].append(todo)

        return state

    def _compute_lab_trends(self, all_entities: list[dict]) -> dict:
        """Compute trends for lab values across all entries."""
        labs_by_name: dict[str, list[dict]] = {}

        for entry in all_entities:
            date = entry.get("date", "")
            for lab in entry.get("labs", []):
                name = lab.get("name", "")
                if not name:
                    continue
                if name not in labs_by_name:
                    labs_by_name[name] = []
                labs_by_name[name].append({
                    "date": date,
                    "value": lab.get("value"),
                    "unit": lab.get("unit"),
                    "status": lab.get("status"),
                })

        # Compute trends for each lab
        trends = {}
        for name, values in labs_by_name.items():
            sorted_values = sorted(values, key=lambda x: x["date"])
            if len(sorted_values) >= 2:
                # Try to compute numeric trend
                try:
                    first_val = float(sorted_values[0]["value"])
                    last_val = float(sorted_values[-1]["value"])
                    change_pct = ((last_val - first_val) / first_val) * 100 if first_val != 0 else 0
                    trend = "increasing" if change_pct > 5 else "decreasing" if change_pct < -5 else "stable"
                except (ValueError, TypeError):
                    trend = "unknown"
                    change_pct = None
            else:
                trend = "insufficient_data"
                change_pct = None

            trends[name] = {
                "values": sorted_values,
                "trend": trend,
                "change_pct": round(change_pct, 1) if change_pct is not None else None,
                "latest": sorted_values[-1] if sorted_values else None,
            }

        return trends

    def _compute_symptom_trends(self, all_entities: list[dict]) -> dict:
        """Compute trends for symptoms across all entries."""
        symptoms_by_name: dict[str, list[dict]] = {}

        for entry in all_entities:
            date = entry.get("date", "")
            for sym in entry.get("symptoms", []):
                name = sym.get("name", "").lower()
                if not name:
                    continue
                if name not in symptoms_by_name:
                    symptoms_by_name[name] = []
                symptoms_by_name[name].append({
                    "date": date,
                    "severity": sym.get("severity"),
                    "trend": sym.get("trend"),
                })

        # Compute overall trend for each symptom
        trends = {}
        for name, observations in symptoms_by_name.items():
            sorted_obs = sorted(observations, key=lambda x: x["date"])

            # Count trend directions
            improving = sum(1 for o in sorted_obs if o.get("trend") == "improving")
            worsening = sum(1 for o in sorted_obs if o.get("trend") == "worsening")
            resolved = any(o.get("trend") == "resolved" for o in sorted_obs)

            if resolved:
                overall_trend = "resolved"
            elif worsening > improving:
                overall_trend = "worsening"
            elif improving > worsening:
                overall_trend = "improving"
            else:
                overall_trend = "stable"

            trends[name] = {
                "first_noted": sorted_obs[0]["date"] if sorted_obs else None,
                "last_noted": sorted_obs[-1]["date"] if sorted_obs else None,
                "mention_count": len(sorted_obs),
                "overall_trend": overall_trend,
                "latest_severity": sorted_obs[-1].get("severity") if sorted_obs else None,
            }

        return trends

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
            # 1) PROCESS
            processed = self.llm["process"](
                [
                    {"role": "system", "content": self._prompt("process.system_prompt")},
                    {"role": "user", "content": section},
                ]
            )
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

        # Generate summary with dependency tracking
        summary = self._generate_file(
            "summary.md",
            "summary.system_prompt",
            role="summary",
            extra_messages=[{"role": "user", "content": "\n\n".join(filter(None, [header_text, processed_text]))}],
            description="summary",
            dependencies=self._get_standard_report_deps("summary.system_prompt"),
        )
        return summary + "\n\n" + processed_text

    def _assemble_final_output(self, header_text: str) -> str:
        """Assemble final output.md with action plan, experiments, summary, next steps, next labs, and all entries."""
        sections = []

        # 1. Action Plan (PRIMARY OUTPUT - what to do next)
        action_plan_path = self.reports_dir / "action_plan.md"
        if action_plan_path.exists():
            sections.append(self._read_without_deps_comment(action_plan_path).strip())

        # 2. Experiments (active biohacking/N=1 experiments)
        experiments_path = self.reports_dir / "experiments.md"
        if experiments_path.exists():
            sections.append(self._read_without_deps_comment(experiments_path).strip())

        # 3. Summary (clinical overview)
        summary_path = self.reports_dir / "summary.md"
        if summary_path.exists():
            sections.append(f"# Clinical Summary\n\n{self._read_without_deps_comment(summary_path).strip()}")

        # 4. Next Steps (detailed consensus recommendations)
        next_steps_path = self.reports_dir / "next_steps.md"
        if next_steps_path.exists():
            sections.append(f"# Detailed Next Steps\n\n{self._read_without_deps_comment(next_steps_path).strip()}")

        # 5. Next Labs (detailed lab recommendations)
        next_labs_path = self.reports_dir / "next_labs.md"
        if next_labs_path.exists():
            sections.append(f"# Detailed Lab Recommendations\n\n{self._read_without_deps_comment(next_labs_path).strip()}")

        # 6. All processed entries (newest first)
        processed_text = self._get_processed_entries_text()
        if processed_text:
            sections.append(f"# Health Log Entries\n\n{processed_text}")

        return "\n\n---\n\n".join(sections)


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
