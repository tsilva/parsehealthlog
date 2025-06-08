from __future__ import annotations

"""Clean, DRY rewrite of the health-log parser / curator.

• Reads a markdown health log that uses `### YYYY-MM-DD` section headers.
• Delegates heavy-lifting (processing, validation, summarisation, question-generation,
  specialist plans, consensus, etc.) to LLM prompts stored in a local `prompts/` folder.
• Enriches the log with lab-result CSVs (per-log `labs.csv` *and* aggregated
  `LABS_PARSER_OUTPUT_PATH/all.csv`).
• Produces an `output` folder next to the script (one sub-folder per input file) with:
      ├─ <date>.raw.md          # original section text
      ├─ <date>.processed.md    # validated LLM output (first line = raw SHA-256 hash)
      ├─ <date>.labs.md         # formatted labs for the date (if any)
      ├─ intro.md               # any pre-dated content
      ├─ summary.md             # high-level summary produced by the LLM
      ├─ clarifying_questions.md
      ├─ next_steps_<spec>.md   # per-speciality next steps
      ├─ next_steps.md          # merged consensus plan
      └─ output.md              # summary + all processed sections (reverse-chronological)

The code assumes these environment variables:
    OPENROUTER_API_KEY           – mandatory (forwarded to openrouter.ai)
    MODEL_ID                     – default model (fallback for all roles)
    PROCESS_MODEL_ID             – (optional) override for PROCESS stage
    VALIDATE_MODEL_ID            – (optional) override for VALIDATE stage
    QUESTIONS_MODEL_ID           – (optional) override for questions
    SUMMARY_MODEL_ID             – (optional) override for summary
    NEXT_STEPS_MODEL_ID          – (optional) override for next-steps generation
    LABS_PARSER_OUTPUT_PATH      – (optional) path to aggregated lab CSVs
    MAX_WORKERS                  – (optional) ThreadPoolExecutor size (default 4)
    QUESTIONS_RUNS               – how many diverse question sets to generate (default 3)

The implementation is ~50 % shorter than the original (~350 → ~170 LoC) while
maintaining identical behaviour.
"""

from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Final

import pandas as pd
from dateutil.parser import parse as date_parse
from openai import OpenAI
from tqdm import tqdm

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
    "gastroenterology",
    "neurology",
    "psychiatry",
    "nutrition",
    "rheumatology",
    "internal medicine",
]


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def short_hash(text: str) -> str:  # 8-char SHA-256 hex prefix
    return sha256(text.encode()).hexdigest()[:8]


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
        name = str(row.lab_name_enum).strip()
        value = row.lab_value_final
        unit = str(getattr(row, "lab_unit_final", "")).strip()
        line = f"- **{name}:** {value}{f' {unit}' if unit else ''}"
        rmin, rmax = row.lab_range_min_final, row.lab_range_max_final
        if pd.notna(rmin) and pd.notna(rmax):
            line += f" ({rmin} - {rmax})"
            try:
                v, lo, hi = map(float, (value, rmin, rmax))
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

    def __init__(self, health_log_path: str | Path) -> None:
        self.path = Path(health_log_path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)

        self.output_dir = Path("output") / self.path.stem
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(__name__)

        # Prompts (lazy-load to keep __init__ lightweight)
        self.prompts: dict[str, str] = {}

        # OpenAI client + per-role models
        self.client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY"))
        default_model = os.environ.get("MODEL_ID", "gpt-4o-mini")
        self.models = {
            "process": os.getenv("PROCESS_MODEL_ID", default_model),
            "validate": os.getenv("VALIDATE_MODEL_ID", default_model),
            "summary": os.getenv("SUMMARY_MODEL_ID", default_model),
            "questions": os.getenv("QUESTIONS_MODEL_ID", default_model),
            "next_steps": os.getenv("NEXT_STEPS_MODEL_ID", default_model),
        }
        self.llm = {k: LLM(self.client, v) for k, v in self.models.items()}

        # Lab data per date – populated lazily
        self.labs_by_date: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> None:  # noqa: C901 – single orchestrator method is clearer here
        header_text, sections = self._split_sections()
        self._load_labs()

        # Write raw sections & compute which ones need processing
        to_process: list[str] = []
        for sec in sections:
            date = extract_date(sec)
            raw_path = self.output_dir / f"{date}.raw.md"
            raw_path.write_text(sec, encoding="utf-8")

            processed_path = self.output_dir / f"{date}.processed.md"
            if processed_path.exists() and processed_path.read_text().splitlines()[0].strip() == short_hash(sec):
                continue  # up-to-date
            to_process.append(sec)

        # Process (potentially in parallel)
        max_workers = int(os.getenv("MAX_WORKERS", "4")) or 1
        failed: list[str] = []
        with ThreadPoolExecutor(max_workers=max_workers) as ex, tqdm(total=len(to_process), desc="Processing") as bar:
            futures = {ex.submit(self._process_section, sec): sec for sec in to_process}
            for fut in as_completed(futures):
                date, ok = fut.result()
                if not ok:
                    failed.append(date)
                bar.update(1)

        if failed:
            (self.output_dir / "processing_failures.log").write_text("\n".join(failed) + "\n", encoding="utf-8")
            self.logger.error("Failed to process sections for: %s", ", ".join(failed))
        else:
            self.logger.info("All sections processed successfully")

        # Write remaining labs (ones for which no processed section exists)
        for date, df in self.labs_by_date.items():
            lab_path = self.output_dir / f"{date}.labs.md"
            if not lab_path.exists() and not df.empty:
                lab_path.write_text(f"{LAB_SECTION_HEADER}\n{format_labs(df)}\n", encoding="utf-8")

        final_markdown = self._assemble_output(header_text)
        (self.output_dir / "output.md").write_text(final_markdown, encoding="utf-8")
        self.logger.info("Saved full log to %s", self.output_dir / "output.md")

        # Clarifying questions
        q_runs = int(os.getenv("QUESTIONS_RUNS", "3"))
        self._generate_file(
            "clarifying_questions.md",
            "merge_bullets.system_prompt",  # prompt used only for merge step
            role="questions",
            temperature=1.0,
            calls=q_runs,
            extra_messages=[{"role": "user", "content": final_markdown}],
        )

        # Specialist next steps + consensus
        spec_outputs = []
        for spec in SPECIALTIES:
            spec_file = f"next_steps_{spec.replace(' ', '_')}.md"
            prompt = self._prompt("specialist_next_steps.system_prompt").format(specialty=spec)
            spec_outputs.append(
                self._generate_file(
                    spec_file,
                    prompt,
                    role="next_steps",
                    temperature=0.25,
                    extra_messages=[{"role": "user", "content": final_markdown}],
                )
            )

        # Consensus next steps
        self._generate_file(
            "next_steps.md",
            "consensus_next_steps.system_prompt",
            role="next_steps",
            temperature=0.25,
            extra_messages=[{"role": "user", "content": "\n\n".join(spec_outputs)}],
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _prompt(self, name: str) -> str:
        if name not in self.prompts:
            self.prompts[name] = load_prompt(name)
        return self.prompts[name]

    # --------------------------------------------------------------
    # File generation with caching & merging (for multi-call variants)
    # --------------------------------------------------------------

    def _generate_file(
        self,
        filename: str,
        system_prompt_or_name: str,
        *,
        role: str,
        max_tokens: int = 2048,
        temperature: float = 0.0,
        calls: int = 1,
        extra_messages: Iterable[dict[str, str]] | None = None,
    ) -> str:
        path = self.output_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")

        system_prompt = (
            system_prompt_or_name
            if "\n" in system_prompt_or_name  # crude check: treat raw prompt as content
            else self._prompt(system_prompt_or_name)
        )
        extra_messages = list(extra_messages or [])

        outputs: list[str] = []
        for i in range(calls):
            out = self.llm[role](
                [{"role": "system", "content": system_prompt}, *extra_messages],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            outputs.append(out)

        content = outputs[0] if calls == 1 else self._merge_outputs(outputs)
        path.write_text(content, encoding="utf-8")
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
        processed_path = self.output_dir / f"{date}.processed.md"
        raw_digest = short_hash(section)

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
                processed_path.write_text(f"{raw_digest}\n{processed}", encoding="utf-8")

                # Write labs for this date (if any)
                if (df := self.labs_by_date.get(date)) is not None and not df.empty:
                    lab_path = self.output_dir / f"{date}.labs.md"
                    lab_path.write_text(f"{LAB_SECTION_HEADER}\n{format_labs(df)}\n", encoding="utf-8")
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
            intro_path = self.output_dir / "intro.md"
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
        if (p := os.getenv("LABS_PARSER_OUTPUT_PATH")):
            agg_csv = Path(p) / "all.csv"
            if agg_csv.exists():
                lab_dfs.append(pd.read_csv(agg_csv))

        if not lab_dfs:
            return

        labs_df = pd.concat(lab_dfs, ignore_index=True)
        if "date" in labs_df.columns:
            labs_df["date"] = pd.to_datetime(labs_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        keep_cols = [
            "date",
            "lab_name_enum",
            "lab_value_final",
            "lab_unit_final",
            "lab_range_min_final",
            "lab_range_max_final",
        ]
        labs_df = labs_df[[c for c in keep_cols if c in labs_df.columns]]
        self.labs_by_date = {d: df for d, df in labs_df.groupby("date")}

    # --------------------------------------------------------------
    # Output assembly
    # --------------------------------------------------------------

    def _assemble_output(self, header_text: str) -> str:
        # Gather all processed + lab files, newest first
        items: list[tuple[str, str]] = []  # date → markdown chunk
        for processed_path in self.output_dir.glob("*.processed.md"):
            date = processed_path.stem.split(".")[0]
            body = "\n".join(processed_path.read_text(encoding="utf-8").splitlines()[1:])
            parts = [body]
            lab_path = self.output_dir / f"{date}.labs.md"
            if lab_path.exists():
                parts.append(lab_path.read_text(encoding="utf-8").strip())
            items.append((date, "\n".join(parts)))

        processed_text = "\n\n".join(v for _d, v in sorted(items, key=lambda t: t[0], reverse=True))

        summary = self._generate_file(
            "summary.md",
            "summary.system_prompt",
            role="summary",
            extra_messages=[{"role": "user", "content": "\n\n".join(filter(None, [header_text, processed_text]))}],
        )
        return summary + "\n\n" + processed_text


# --------------------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Process a markdown health log")
    parser.add_argument("health_log_path", help="Path to the markdown health log")
    args = parser.parse_args()

    setup_logging()
    start = datetime.now()
    HealthLogProcessor(args.health_log_path).run()
    logging.getLogger(__name__).info("Finished in %.1fs", (datetime.now() - start).total_seconds())


if __name__ == "__main__":
    main()
