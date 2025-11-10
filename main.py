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
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import Final

import pandas as pd
from dateutil.parser import parse as date_parse
from openai import OpenAI
from tqdm import tqdm

from config import Config

# --------------------------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------------------------


def setup_logging() -> None:
    """Configure a root logger that prints to stdout and also persists errors."""
    fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    out_hdlr = logging.StreamHandler(sys.stdout)
    out_hdlr.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    err_hdlr = logging.FileHandler(logs_dir / "error.log", encoding="utf-8")
    err_hdlr.setLevel(logging.ERROR)
    err_hdlr.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    root.handlers = [out_hdlr, err_hdlr]

    # Quiet noisy deps
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
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def short_hash(text: str) -> str:  # 8-char SHA-256 hex prefix
    return sha256(text.encode()).hexdigest()[:8]


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
    """Return YYYY-MM-DD from the section header line (first token that parses)."""
    header = section.strip().splitlines()[0].lstrip("#").replace("–", "-").replace("—", "-")
    for token in re.split(r"\s+", header):
        try:
            return date_parse(token, fuzzy=False).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"No valid date found in header: {header}")


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
            except Exception:  # noqa: BLE001
                pass

        out.append(line)
    return "\n".join(out)


# --------------------------------------------------------------------------------------
# OpenAI wrapper
# --------------------------------------------------------------------------------------


@dataclass(slots=True)
class LLM:
    """Lightweight wrapper around OpenAI chat completions to minimise boilerplate."""

    client: OpenAI
    model: str

    def __call__(self, messages: list[dict[str, str]], *, max_tokens: int = 2048, temperature: float = 0.0) -> str:
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
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

        print(config.output_path)
        output_base = config.output_path
        print(f"Output base path: {output_base}")
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
        ]

        missing = [p for p in required_prompts if not (PROMPTS_DIR / f"{p}.md").exists()]
        if missing:
            raise ValueError(f"Missing required prompt files: {', '.join(missing)}")

        self.logger.info("All required prompt files validated successfully")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: C901 – single orchestrator method is clearer here
        header_text, sections = self._split_sections()
        self._load_labs()

        # Create placeholder sections for dates with labs but no entries
        sections = self._create_placeholder_sections(sections)

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
                date, ok = fut.result()
                if not ok:
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
    
    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prompt(self, name: str) -> str:
        if name not in self.prompts:
            self.prompts[name] = load_prompt(name)
        return self.prompts[name]

    # --------------------------------------------------------------
    # Dependency tracking helpers
    # --------------------------------------------------------------

    def _hash_prompt(self, name: str) -> str:
        """Compute hash of a prompt file."""
        return hash_file(PROMPTS_DIR / f"{name}.md") or ""

    def _hash_intro(self) -> str:
        """Compute hash of intro.md if it exists."""
        intro_path = self.OUTPUT_PATH / "intro.md"
        return hash_file(intro_path) or ""

    def _hash_all_processed(self) -> str:
        """Compute combined hash of all processed sections (excluding hash lines)."""
        contents = []
        for processed_path in sorted(self.entries_dir.glob("*.processed.md")):
            lines = processed_path.read_text(encoding="utf-8").splitlines()
            # Skip first line (hash comment) and join the rest
            body = "\n".join(lines[1:]) if len(lines) > 1 else ""
            contents.append(body)
        return hash_content("\n\n".join(contents)) if contents else ""

    def _hash_all_specialist_reports(self) -> str:
        """Compute combined hash of all specialist next steps reports."""
        contents = []
        for spec in SPECIALTIES:
            spec_file = self.reports_dir / f"next_steps_{spec.replace(' ', '_')}.md"
            if spec_file.exists():
                lines = spec_file.read_text(encoding="utf-8").splitlines()
                # Skip first line (hash comment) if present
                body = "\n".join(lines[1:]) if len(lines) > 1 else ""
                contents.append(body)
        return hash_content("\n\n".join(contents)) if contents else ""

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

        first_line = path.read_text(encoding="utf-8").splitlines()[0] if path.exists() else ""
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

        Depends on summary, next_steps, next_labs, and all processed sections.
        """
        deps = {
            "processed": self._hash_all_processed(),
        }

        # Add hashes of key reports
        for report_name in ["summary", "next_steps", "next_labs"]:
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
            # If no dependencies provided, use old behavior (file exists = skip)
            if dependencies is None:
                if description:
                    self.logger.info("%s already exists at %s", description.capitalize(), path)
                lines = path.read_text(encoding="utf-8").splitlines()
                # Skip deps comment if present, return rest
                return "\n".join(lines[1:]) if lines and lines[0].startswith("<!--") else "\n".join(lines)

            # Check if dependencies changed
            if not self._check_needs_regeneration(path, dependencies):
                if description:
                    self.logger.info("%s already exists at %s", description.capitalize(), path)
                lines = path.read_text(encoding="utf-8").splitlines()
                # Skip deps comment, return rest
                return "\n".join(lines[1:]) if lines and lines[0].startswith("<!--") else "\n".join(lines)

        if description:
            self.logger.info("Generating %s...", description)

        system_prompt = (
            system_prompt_or_name
            if "\n" in system_prompt_or_name  # crude check: treat raw prompt as content
            else self._prompt(system_prompt_or_name)
        )
        extra_messages = list(extra_messages or [])

        outputs: list[str] = []
        base = Path(filename).stem
        suffix = Path(filename).suffix
        if calls > 1:
            desc = (description or base).capitalize()
            with tqdm(total=calls, desc=desc) as bar:
                for i in range(calls):
                    out = self.llm[role](
                        [{"role": "system", "content": system_prompt}, *extra_messages],
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
                    outputs.append(out)
                    variant_path = self.reports_dir / f"{base}_{i+1}{suffix}"
                    variant_path.write_text(out, encoding="utf-8")
                    bar.update(1)
        else:
            out = self.llm[role](
                [{"role": "system", "content": system_prompt}, *extra_messages],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            outputs.append(out)

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

        for attempt in range(1, 4):
            # 1) PROCESS
            processed = self.llm["process"](
                [
                    {"role": "system", "content": self._prompt("process.system_prompt")},
                    {"role": "user", "content": section},
                ]
            )

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
            lab_dfs.append(pd.read_csv(csv_local))

        # aggregated labs
        if self.config.labs_parser_output_path:
            agg_csv = self.config.labs_parser_output_path / "all.csv"
            if agg_csv.exists():
                lab_dfs.append(pd.read_csv(agg_csv))

        if not lab_dfs:
            return

        labs_df = pd.concat(lab_dfs, ignore_index=True)
        if "date" in labs_df.columns:
            labs_df["date"] = pd.to_datetime(labs_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        keep_cols = [
            "date",
            "lab_name_standardized",
            "value_normalized",
            "unit_normalized",
            "reference_min_normalized",
            "reference_max_normalized",
        ]
        # Handle multiple column naming conventions
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
        labs_df = labs_df[[c for c in keep_cols if c in labs_df.columns]]
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

    def _assemble_output(self, header_text: str) -> str:
        """Assemble summary + processed entries (used as input for other reports)."""
        # Gather all processed files, newest first
        # Processed files already include labs content, so no need to read .labs.md separately
        items: list[tuple[str, str]] = []  # date → markdown chunk
        for processed_path in self.entries_dir.glob("*.processed.md"):
            date = processed_path.stem.split(".")[0]
            body = "\n".join(processed_path.read_text(encoding="utf-8").splitlines()[1:])
            items.append((date, body))

        processed_text = "\n\n".join(v for _d, v in sorted(items, key=lambda t: t[0], reverse=True))

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
        """Assemble final output.md with summary, next steps, next labs, and all entries."""
        sections = []

        # 1. Summary
        summary_path = self.reports_dir / "summary.md"
        if summary_path.exists():
            sections.append(f"# Summary\n\n{summary_path.read_text(encoding='utf-8').strip()}")

        # 2. Next Steps (consensus)
        next_steps_path = self.reports_dir / "next_steps.md"
        if next_steps_path.exists():
            sections.append(f"# Next Steps\n\n{next_steps_path.read_text(encoding='utf-8').strip()}")

        # 3. Next Labs
        next_labs_path = self.reports_dir / "next_labs.md"
        if next_labs_path.exists():
            sections.append(f"# Next Labs\n\n{next_labs_path.read_text(encoding='utf-8').strip()}")

        # 4. All processed entries (newest first)
        items: list[tuple[str, str]] = []
        for processed_path in self.entries_dir.glob("*.processed.md"):
            date = processed_path.stem.split(".")[0]
            body = "\n".join(processed_path.read_text(encoding="utf-8").splitlines()[1:])
            items.append((date, body))

        processed_text = "\n\n".join(v for _d, v in sorted(items, key=lambda t: t[0], reverse=True))
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
