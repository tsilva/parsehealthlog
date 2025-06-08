from dotenv import load_dotenv

load_dotenv(override=True)
import os, re, sys
import logging
from pathlib import Path
from openai import OpenAI
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed
from dateutil.parser import parse as date_parse
import argparse
import hashlib
import pandas as pd

# HACK: temporary env hack
os.environ.pop("SSL_CERT_FILE", None)

LABS_PARSER_OUTPUT_PATH = os.getenv("LABS_PARSER_OUTPUT_PATH")
LAB_SECTION_HEADER = "Lab test results:"


def setup_logging():
    """Configure logging to stdout and an error log file."""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)

    error_handler = logging.FileHandler("error.log", encoding="utf-8")
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    logger.handlers = [stdout_handler, error_handler]

    # Silence verbose HTTP logs from dependencies
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


setup_logging()

logger = logging.getLogger(__name__)


def load_prompt(prompt_name):
    """Load a prompt from the prompts directory."""
    prompts_dir = Path(__file__).parent / "prompts"
    prompt_path = prompts_dir / f"{prompt_name}.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file {prompt_path} does not exist.")
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


PROCESS_SYSTEM_PROMPT = load_prompt("process.system_prompt")
VALIDATE_SYSTEM_PROMPT = load_prompt("validate.system_prompt")
VALIDATE_USER_PROMPT = load_prompt("validate.user_prompt")
SUMMARY_SYSTEM_PROMPT = load_prompt("summary.system_prompt")
NEXT_STEPS_SYSTEM_PROMPT = load_prompt("next_steps.system_prompt")
QUESTIONS_SYSTEM_PROMPT = load_prompt("questions.system_prompt")
MERGE_BULLETS_SYSTEM_PROMPT = load_prompt("merge_bullets.system_prompt")
SPECIALIST_NEXT_STEPS_SYSTEM_PROMPT = load_prompt("specialist_next_steps.system_prompt")
CONSENSUS_NEXT_STEPS_SYSTEM_PROMPT = load_prompt("consensus_next_steps.system_prompt")

# Initialize OpenAI client
client = OpenAI(
    base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY")
)


# Extract date from section header, tolerant to different formats
def extract_date_from_section(section):
    header = section.strip().splitlines()[0].lstrip("#").strip()
    # Normalize dashes to standard hyphen-minus
    header = header.replace("–", "-").replace("—", "-")
    tokens = re.split(r"\s+", header)
    for token in tokens:
        try:
            return date_parse(
                token, fuzzy=False, dayfirst=False, yearfirst=True
            ).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"No valid date found in header: {header}")


def get_short_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def format_lab_results(lab_df):
    """Format lab results DataFrame into markdown bullet list."""
    lines = []
    for _, row in lab_df.iterrows():
        name = str(row.get("lab_name_enum", "")).strip()
        value = row.get("lab_value_final")
        unit = str(row.get("lab_unit_final", "")).strip()
        line = f"- **{name}:** {value}"
        if unit:
            line += f" {unit}"
        rmin = row.get("lab_range_min_final")
        rmax = row.get("lab_range_max_final")
        status = None
        if pd.notna(rmin) and pd.notna(rmax):
            line += f" ({rmin} - {rmax})"
            try:
                v = float(value)
                rmin_f = float(rmin)
                rmax_f = float(rmax)
                if v < rmin_f:
                    status = "BELOW RANGE"
                elif v > rmax_f:
                    status = "ABOVE RANGE"
                else:
                    status = "OK"
            except Exception:
                status = None
        if status:
            line += f" [{status}]"
        lines.append(line)
    return "\n".join(lines)


def write_labs_files(data_dir, labs_by_date):
    """Write lab results to separate `YYYY-MM-DD.labs.md` files."""
    for date, df in labs_by_date.items():
        if df is None or df.empty:
            continue
        labs_text = LAB_SECTION_HEADER + "\n" + format_lab_results(df)
        (data_dir / f"{date}.labs.md").write_text(labs_text + "\n", encoding="utf-8")


def load_or_generate_file(
    file_path: Path,
    description: str,
    model_id: str,
    messages: list,
    max_tokens: int,
    temperature: float = 0.0,
    *,
    calls: int = 1,
    merge_system_prompt: str | None = None,
):
    """Load file if it exists, otherwise generate it using the LLM."""
    if file_path.exists():
        return file_path.read_text(encoding="utf-8").strip()

    logger.info(f"Generating {description}...")
    outputs = []
    for i in range(calls):
        completion = client.chat.completions.create(
            model=model_id,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        output = completion.choices[0].message.content.strip()
        outputs.append(output)
        if calls > 1:
            alt_path = file_path.with_name(
                f"{file_path.stem}_{i + 1}{file_path.suffix}"
            )
            alt_path.write_text(output, encoding="utf-8")
            logger.info(
                f"Saved alternative {i + 1} for {description} to {alt_path}"
            )

    if calls == 1:
        content = outputs[0]
    else:
        assert merge_system_prompt, "merge_system_prompt required when calls > 1"
        content = merge_outputs(outputs, merge_system_prompt, model_id)

    file_path.write_text(content, encoding="utf-8")
    logger.info(f"Saved {description} to {file_path}")
    return content


def merge_outputs(outputs: list[str], system_prompt: str, model_id: str) -> str:
    """Merge multiple outputs using an LLM to remove redundancy."""
    if not outputs:
        return ""
    user_content = "\n\n".join(outputs)
    completion = client.chat.completions.create(
        model=model_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=4096,
        temperature=0.0,
    )
    return completion.choices[0].message.content.strip()





def process(input_path):
    default_model = os.getenv("MODEL_ID")

    process_model_id = os.getenv("PROCESS_MODEL_ID", default_model)
    validate_model_id = os.getenv("VALIDATE_MODEL_ID", default_model)
    questions_model_id = os.getenv("QUESTIONS_MODEL_ID", default_model)
    summary_model_id = os.getenv("SUMMARY_MODEL_ID", default_model)
    next_steps_model_id = os.getenv("NEXT_STEPS_MODEL_ID", default_model)

    # Create output path
    data_dir = Path("output") / Path(input_path).stem
    data_dir.mkdir(parents=True, exist_ok=True)

    # Run LLM to process a section, return formatted markdown
    def _process(raw_section):
        """Process a single raw section and return (date, success)."""
        date = extract_date_from_section(raw_section)

        # The existence/up-to-date check is now outside
        for attempt in range(1, 4):
            # Run LLM to process the section
            completion = client.chat.completions.create(
                model=process_model_id,
                messages=[
                    {"role": "system", "content": PROCESS_SYSTEM_PROMPT},
                    {"role": "user", "content": raw_section},
                ],
                max_tokens=2048,
                temperature=0.0,
            )
            processed_section = completion.choices[0].message.content.strip()

            # Run LLM to validate the processed section
            completion = client.chat.completions.create(
                model=validate_model_id,
                messages=[
                    {"role": "system", "content": VALIDATE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": VALIDATE_USER_PROMPT.format(
                            raw_section=raw_section, processed_section=processed_section
                        ),
                    },
                ],
                max_tokens=2048,
                temperature=0.0,
            )

            # If the validation does not return "$OK$", capture the failure
            error_content = completion.choices[0].message.content.strip()
            if "$OK$" not in error_content:
                logger.error(f"Validation failed for date {date}: {error_content}")

                # Save sample of the failure for analysis on the last attempt
                if attempt == 3:
                    failed_dir = data_dir / "failed_samples"
                    failed_dir.mkdir(exist_ok=True)
                    failure_path = failed_dir / f"{date}.failure.md"
                    failure_text = (
                        "===== RAW TEXT =====\n" + raw_section +
                        "\n\n===== PROCESSED TEXT =====\n" + processed_section +
                        "\n\n===== VALIDATION OUTPUT =====\n" + error_content + "\n"
                    )
                    failure_path.write_text(failure_text, encoding="utf-8")
                    logger.info(f"Saved failed sample to {failure_path}")
                else:
                    logger.info("Retrying processing...")
                continue

            # If validation passes, write the processed section to file
            processed_section = processed_section.strip()
            raw_hash = get_short_hash(raw_section)
            processed_file = data_dir / f"{date}.processed.md"
            processed_file.write_text(
                f"{raw_hash}\n{processed_section}", encoding="utf-8"
            )
            logger.info(
                f"Processed section for date {date} written to {processed_file}"
            )

            # Write labs for this date if available
            lab_df = labs_by_date.get(date)
            if lab_df is not None and not lab_df.empty:
                lab_path = data_dir / f"{date}.labs.md"
                labs_text = LAB_SECTION_HEADER + "\n" + format_lab_results(lab_df)
                lab_path.write_text(labs_text + "\n", encoding="utf-8")
                labs_written.add(date)

            # Return True to indicate successful processing
            return date, True

        # If all retries failed, return False
        logger.error(f"Failed to process section for date {date} after 3 attempts")
        return date, False

    # Read and split input file into sections
    with open(input_path, "r", encoding="utf-8") as f:
        input_text = f.read()
    # Preserve any notes before the first section header for later summarization
    first_section_idx = input_text.find("###")
    if first_section_idx == -1:
        logger.error("No section headers (###) found in input file.")
        sys.exit(1)
    header_text = input_text[:first_section_idx].strip()
    input_text = input_text[first_section_idx:]
    sections = [
        s.strip()
        for s in re.split(r"(?=^###)", input_text, flags=re.MULTILINE)
        if s.strip()
    ]

    # Only keep sections that contain "[ANALISES]"
    sections = [section for section in sections if "[ANALISES]" in section]

    # Separate intro sections that come before the first dated section
    intro_sections = []
    dated_sections = []
    found_first_date = False
    for section in sections:
        try:
            extract_date_from_section(section)
            dated_sections.append(section)
            found_first_date = True
        except ValueError:
            if not found_first_date:
                intro_sections.append(section)
            else:
                logger.warning(
                    "Skipping section without parseable date after first dated section"
                )

    sections = dated_sections

    # Assert that each remaining section contains exactly one '###'
    for section in sections:
        count = section.count("###")
        if count != 1:
            logger.error(f"Section does not contain exactly one '###':\n{section}")
            sys.exit(1)

    # Assert no duplicate dates in sections
    dates = [extract_date_from_section(section) for section in sections]
    if len(dates) != len(set(dates)):
        duplicates = {date for date in dates if dates.count(date) > 1}
        logger.error(f"Duplicate dates found: {duplicates}")
        sys.exit(1)

    # Save intro text (header and non-date sections) for later use
    intro_parts = []
    if header_text:
        intro_parts.append(header_text)
    if intro_sections:
        intro_parts.append("\n\n".join(intro_sections))
    intro_text = "\n\n".join(intro_parts).strip()
    if intro_text:
        (data_dir / "intro.md").write_text(intro_text + "\n", encoding="utf-8")

    # Combine lab data from labs.csv and labs parser output if available
    lab_dfs = []

    # Legacy per-log labs.csv
    labs_csv = Path(input_path).parent / "labs.csv"
    if labs_csv.exists():
        labs_df = pd.read_csv(labs_csv)
        lab_dfs.append(labs_df)

    # Aggregated labs from LABS_PARSER_OUTPUT_PATH/all.csv
    if LABS_PARSER_OUTPUT_PATH:
        all_csv = Path(LABS_PARSER_OUTPUT_PATH) / "all.csv"
        if all_csv.exists():
            labs_df = pd.read_csv(all_csv)
            lab_dfs.append(labs_df)

    labs_by_date = {}
    labs_written = set()
    if lab_dfs:
        labs_df = pd.concat(lab_dfs, ignore_index=True)

        if "date" in labs_df.columns:
            labs_df["date"] = (
                pd.to_datetime(labs_df["date"], errors="coerce")
                .dt.strftime("%Y-%m-%d")
            )

        keep_cols = [
            "date",
            "lab_type",
            "lab_name_enum",
            "lab_value_final",
            "lab_unit_final",
            "lab_range_min_final",
            "lab_range_max_final",
        ]

        labs_df = labs_df[[c for c in keep_cols if c in labs_df.columns]]
        labs_by_date = {str(d): df for d, df in labs_df.groupby("date")}

    # Rewrite all raw files
    for section in sections:
        date = extract_date_from_section(section)
        raw_file = data_dir / f"{date}.raw.md"
        raw_file.write_text(section, encoding="utf-8")

    # Precompute which sections need processing
    to_process = []
    for section in sections:
        date = extract_date_from_section(section)
        raw_hash = get_short_hash(section)
        processed_file = data_dir / f"{date}.processed.md"
        if processed_file.exists():
            processed_text = processed_file.read_text(encoding="utf-8").strip()
            _raw_hash = processed_text.splitlines()[0].strip()
            if _raw_hash == raw_hash:
                continue
        to_process.append(section)

    # Process sections in parallel (only those that need processing)
    max_workers = int(os.getenv("MAX_WORKERS", "4"))
    failed_dates = []
    executor = ThreadPoolExecutor(max_workers=max_workers)
    futures = [executor.submit(_process, section) for section in to_process]
    try:
        with tqdm(total=len(futures), desc="Processing sections") as pbar:
            for future in as_completed(futures):
                date, ok = future.result()
                if not ok:
                    failed_dates.append(date)
                pbar.update(1)
    except KeyboardInterrupt:
        logger.error("Interrupted by user, cancelling pending tasks...")
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False)
        raise
    executor.shutdown()

    if failed_dates:
        log_path = data_dir / "processing_failures.log"
        log_path.write_text("\n".join(failed_dates) + "\n", encoding="utf-8")
        logger.error(
            f"Failed to process sections for dates: {', '.join(failed_dates)}"
        )
        logger.info(f"Failure log written to {log_path}")
    else:
        logger.info("All sections processed successfully")

    # Write labs for any dates that weren't processed above
    if labs_by_date:
        remaining = {d: df for d, df in labs_by_date.items() if d not in labs_written}
        if remaining:
            write_labs_files(data_dir, remaining)

    # Build the final curated health log text
    def date_key(path: Path) -> str:
        """Return the YYYY-MM-DD portion from a file path."""
        return path.name.split(".")[0]

    processed_map = {date_key(f): f for f in data_dir.glob("*.processed.md")}
    labs_map = {date_key(f): f for f in data_dir.glob("*.labs.md")}
    all_dates = sorted(set(processed_map) | set(labs_map), reverse=True)

    processed_entries = []
    for date in all_dates:
        parts = []
        pf = processed_map.get(date)
        if pf:
            part = "\n".join(pf.read_text(encoding="utf-8").splitlines()[1:])
            parts.append(part)
        lf = labs_map.get(date)
        if lf:
            labs_text = lf.read_text(encoding="utf-8").strip()
            if labs_text:
                parts.append(labs_text)
        processed_entries.append("\n".join(parts).strip())

    processed_text = "\n\n".join(processed_entries)

    # Generate or load the summary and prepend it to the processed text
    summary_file_path = data_dir / "summary.md"
    summary_source = processed_text
    if intro_text:
        summary_source = intro_text + "\n\n" + processed_text
    summary = load_or_generate_file(
        summary_file_path,
        "health summary",
        summary_model_id,
        [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": summary_source},
        ],
        max_tokens=2048,
        temperature=0.0,
    )

    final_text = summary + "\n\n" + processed_text
    with open(data_dir / "output.md", "w", encoding="utf-8") as f:
        f.write(final_text)
    logger.info(f"Saved processed health log to {data_dir / 'output.md'}")

    # Ask the LLM for clarifying questions about the log multiple times
    questions_runs = int(os.getenv("QUESTIONS_RUNS", "3"))
    questions_file_path = data_dir / "clarifying_questions.md"
    load_or_generate_file(
        questions_file_path,
        "clarifying questions",
        questions_model_id,
        [
            {"role": "system", "content": QUESTIONS_SYSTEM_PROMPT},
            {"role": "user", "content": final_text},
        ],
        max_tokens=4096,
        temperature=1.0,
        calls=questions_runs,
        merge_system_prompt=MERGE_BULLETS_SYSTEM_PROMPT,
    )

    # Write specialist-specific next steps and consensus plan
    specialties = [
        "gastroenterology",
        "neurology",
        "psychiatry",
        "nutrition",
        "rheumatology",
        "internal medicine",
    ]
    specialist_outputs = []
    for spec in specialties:
        spec_file = data_dir / f"next_steps_{spec.replace(' ', '_')}.md"
        spec_prompt = SPECIALIST_NEXT_STEPS_SYSTEM_PROMPT.format(specialty=spec)
        content = load_or_generate_file(
            spec_file,
            f"{spec} next steps",
            next_steps_model_id,
            [
                {"role": "system", "content": spec_prompt},
                {"role": "user", "content": final_text},
            ],
            max_tokens=8192,
            temperature=0.25,
        )
        specialist_outputs.append(content)

    consensus_file = data_dir / "next_steps.md"
    load_or_generate_file(
        consensus_file,
        "consensus next steps",
        next_steps_model_id,
        [
            {"role": "system", "content": CONSENSUS_NEXT_STEPS_SYSTEM_PROMPT},
            {"role": "user", "content": "\n\n".join(specialist_outputs)},
        ],
        max_tokens=8192,
        temperature=0.25,
    )

    # If labs parser output path is set, ensure all lab dates are present in the log
    if LABS_PARSER_OUTPUT_PATH:
        labs_dir = Path(LABS_PARSER_OUTPUT_PATH)
        date_pattern = re.compile(r"^(\d{4}-\d{2}-\d{2})")
        lab_dates = set()
        if labs_dir.exists():
            for fname in os.listdir(labs_dir):
                m = date_pattern.match(fname)
                if m:
                    lab_dates.add(m.group(1))

        # processed file names look like "YYYY-MM-DD.processed.md" so we only
        # want the date portion before the first dot
        log_dates = set(processed_map.keys())
        missing_dates = sorted(lab_dates - log_dates)
        if missing_dates:
            logger.info("Lab output dates missing from health log:")
            for d in missing_dates:
                logger.info(d)
        else:
            logger.info("All lab output dates are present in the health log.")

def main():
    parser = argparse.ArgumentParser(description="Health log parser and validator")
    parser.add_argument("--health_log_path", help="Health log path")
    args = parser.parse_args()
    health_log_path = args.health_log_path if args.health_log_path else os.getenv("HEALTH_LOG_PATH")
    if not health_log_path:
        logger.error("Health log path not provided and HEALTH_LOG_PATH not set")
        sys.exit(1)
    process(health_log_path)


if __name__ == "__main__":
    main()
