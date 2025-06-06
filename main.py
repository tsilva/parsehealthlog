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
import io
import pandas as pd

# HACK: temporary env hack
os.environ.pop("SSL_CERT_FILE", None)

LABS_PARSER_OUTPUT_PATH = os.getenv("LABS_PARSER_OUTPUT_PATH")


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
            raw_hash = get_short_hash(raw_section)
            processed_file = data_dir / f"{date}.processed.md"
            processed_file.write_text(
                f"{raw_hash}\n{processed_section}", encoding="utf-8"
            )
            logger.info(f"Processed section for date {date} written to {processed_file}")

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

    # Assert that each section contains exactly one '###'
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

    if lab_dfs:
        labs_df = pd.concat(lab_dfs, ignore_index=True)
        # Keep only the relevant columns if present
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

        new_sections = []
        for section in sections:
            date = extract_date_from_section(section)
            lab_results = labs_df[labs_df["date"] == date]
            if not lab_results.empty:
                buffer = io.StringIO()
                try:
                    lab_results.to_csv(buffer, index=False)
                    csv_string = buffer.getvalue()
                finally:
                    buffer.close()

                section = section + f"\n\nLab Results CSV:\n{csv_string}"
            new_sections.append(section)
        sections = new_sections

    # Rewrite all raw files with lab data mixed in
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

    # Write the final curated health log
    processed_files = list(data_dir.glob("*.processed.md"))
    processed_files = sorted(processed_files, key=lambda f: f.stem, reverse=True)
    processed_entries = [
        "\n".join(f.read_text(encoding="utf-8").splitlines()[1:])
        for f in processed_files
    ]
    processed_text = "\n\n".join(processed_entries)
    with open(data_dir / "output.md", "w", encoding="utf-8") as f:
        f.write(processed_text)
    logger.info(f"Saved processed health log to {data_dir / 'output.md'}")

    # Ask the LLM for clarifying questions about the log
    questions_file_path = data_dir / "clarifying_questions.md"
    if not questions_file_path.exists():
        logger.info("Generating clarifying questions...")
        completion = client.chat.completions.create(
            model=questions_model_id,
            messages=[
                {"role": "system", "content": QUESTIONS_SYSTEM_PROMPT},
                {"role": "user", "content": processed_text},
            ],
            max_tokens=1024,
            temperature=0.0,
        )
        questions = completion.choices[0].message.content.strip()
        with open(questions_file_path, "w", encoding="utf-8") as f:
            f.write(questions)
        logger.info(f"Saved clarifying questions to {questions_file_path}")

    # Write the summary using the LLM
    summary_file_path = data_dir / "summary.md"
    if not summary_file_path.exists():
        logger.info("Generating health summary...")
        summary_source = processed_text
        if header_text:
            summary_source = header_text + "\n\n" + processed_text
        completion = client.chat.completions.create(
            model=summary_model_id,
            messages=[
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": summary_source},
            ],
            max_tokens=2048,
            temperature=0.0,
        )
        summary = completion.choices[0].message.content.strip()
        with open(summary_file_path, "w", encoding="utf-8") as f:
            f.write(summary)
        logger.info(f"Saved processed health summary to {data_dir / 'summary.md'}")

    # Write next steps using the LLM
    next_steps_file_path = data_dir / "next_steps.md"
    if not next_steps_file_path.exists():
        logger.info("Generating next steps...")
        completion = client.chat.completions.create(
            model=next_steps_model_id,
            messages=[
                {"role": "system", "content": NEXT_STEPS_SYSTEM_PROMPT},
                {"role": "user", "content": processed_text},
            ],
            max_tokens=4096,
            temperature=0.25,
        )
        next_steps = completion.choices[0].message.content.strip()
        with open(next_steps_file_path, "w", encoding="utf-8") as f:
            f.write(next_steps)
        logger.info(
            f"Saved processed health summary to {data_dir / 'next_steps.md'}"
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
        log_dates = {f.name.split(".", 1)[0] for f in processed_files}
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
