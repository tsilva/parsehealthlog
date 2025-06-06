from dotenv import load_dotenv

load_dotenv(override=True)
import os, re, sys
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



    base_url="https://openrouter.ai/api/v1", api_key=os.getenv("OPENROUTER_API_KEY")


            return date_parse(
                token, fuzzy=False, dayfirst=False, yearfirst=True
            ).strftime("%Y-%m-%d")



        """Process a single raw section and return (date, success)."""
        for attempt in range(1, 4):
                    {"role": "system", "content": PROCESS_SYSTEM_PROMPT},
                    {"role": "user", "content": raw_section},
                temperature=0.0,
                    {"role": "system", "content": VALIDATE_SYSTEM_PROMPT},
                        "role": "user",
                            raw_section=raw_section, processed_section=processed_section
                        ),
                    },
                temperature=0.0,

                tqdm.write(f"Validation failed for date {date}: {error_content}")
                tqdm.write("Retrying processing...")

            raw_hash = get_short_hash(raw_section)
            processed_file.write_text(
                f"{raw_hash}\n{processed_section}", encoding="utf-8"
            )
            tqdm.write(f"Processed section for date {date} written to {processed_file}")

            return date, True

        tqdm.write(f"Failed to process section for date {date} after 3 attempts")
        return date, False
    with open(input_path, "r", encoding="utf-8") as f:
        input_text = f.read()
    # Only keep text starting from the first section header (###)
    first_section_idx = input_text.find("###")
    if first_section_idx == -1:
        tqdm.write("No section headers (###) found in input file.")
        sys.exit(1)
    input_text = input_text[first_section_idx:]
    sections = [
        s.strip()
        for s in re.split(r"(?=^###)", input_text, flags=re.MULTILINE)
        if s.strip()
    ]
        count = section.count("###")
            tqdm.write(f"Section does not contain exactly one '###':\n{section}")

        tqdm.write(f"Duplicate dates found: {duplicates}")
        labs_df = labs_df[
            [
                "date",
                "lab_type",
                "lab_name_enum",
                "lab_value_final",
                "lab_unit_final",
                "lab_range_min_final",
                "lab_range_max_final",
            ]
        ]

            lab_results = labs_df[labs_df["date"] == date]
            if lab_results.empty:
                continue
            if _raw_hash == raw_hash:
                continue

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
        tqdm.write("Interrupted by user, cancelling pending tasks...")
        for future in futures:
            future.cancel()
        executor.shutdown(wait=False)
        raise
    executor.shutdown()

    if failed_dates:
        log_path = data_dir / "processing_failures.log"
        log_path.write_text("\n".join(failed_dates) + "\n", encoding="utf-8")
        tqdm.write(f"Failed to process sections for dates: {', '.join(failed_dates)}")
        tqdm.write(f"Failure log written to {log_path}")
    else:
        tqdm.write("All sections processed successfully")
    processed_entries = [
        "\n".join(f.read_text(encoding="utf-8").splitlines()[1:])
        for f in processed_files
    ]
    with open(data_dir / "output.md", "w", encoding="utf-8") as f:
        f.write(processed_text)
    tqdm.write(f"Saved processed health log to {data_dir / 'output.md'}")

        tqdm.write("Generating health summary...")
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": processed_text},
            temperature=0.0,
        with open(summary_file_path, "w", encoding="utf-8") as f:
            f.write(summary)
        tqdm.write(f"Saved processed health summary to {data_dir / 'summary.md'}")
        tqdm.write("Generating next steps...")
                {"role": "system", "content": NEXT_STEPS_SYSTEM_PROMPT},
                {"role": "user", "content": processed_text},
            temperature=0.25,
        with open(next_steps_file_path, "w", encoding="utf-8") as f:
            f.write(next_steps)
        tqdm.write(f"Saved processed health summary to {data_dir / 'next_steps.md'}")
    parser.add_argument("--health_log_path", help="Health log path")
    if not health_log_path:
        tqdm.write("Health log path not provided and HEALTH_LOG_PATH not set")
        sys.exit(1)

                    },
                    {
                        "role": "user", 
                        "content": VALIDATE_USER_PROMPT.format(
                            raw_section=raw_section,
                            processed_section=processed_section
                        )
                    }
                ],
                max_tokens=2048,
                temperature=0.0
            )
            
            # If the validation does not return "$OK$", retry processing
            error_content = completion.choices[0].message.content.strip()
            if "$OK$" not in error_content:
                print(f"Validation failed for date {date}: {error_content}")
                print("Retrying processing...")
                continue
            
            # If validation passes, write the processed section to file
            raw_hash = get_short_hash(raw_section) 
            processed_file = data_dir / f"{date}.processed.md"
            processed_file.write_text(f"{raw_hash}\n{processed_section}", encoding="utf-8")
            print(f"Processed section for date {date} written to {processed_file}")
            
            # Return True to indicate successful processing
            return date, True
        
        # If all retries failed, return False
        print(f"Failed to process section for date {date} after 3 attempts")
        return date, False

    # Read and split input file into sections
    with open(input_path, "r", encoding="utf-8") as f: input_text = f.read()
    # Only keep text starting from the first section header (###)
    first_section_idx = input_text.find("###")
    if first_section_idx == -1:
        print("No section headers (###) found in input file.")
        sys.exit(1)
    input_text = input_text[first_section_idx:]
    sections = [s.strip() for s in re.split(r'(?=^###)', input_text, flags=re.MULTILINE) if s.strip()]

    # Assert that each section contains exactly one '###'
    for section in sections:
        count = section.count('###')
        if count != 1:
            print(f"Section does not contain exactly one '###':\n{section}")
            sys.exit(1)

    # Assert no duplicate dates in sections
    dates = [extract_date_from_section(section) for section in sections]
    if len(dates) != len(set(dates)):
        duplicates = {date for date in dates if dates.count(date) > 1}
        print(f"Duplicate dates found: {duplicates}")
        sys.exit(1)

    # If labs.csv exists, read it and append lab results to sections
    labs_csv = Path(input_path).parent / "labs.csv"
    if labs_csv.exists():
        labs_df = pd.read_csv(labs_csv)
        labs_df = labs_df[["date","lab_type","lab_name_enum","lab_value_final","lab_unit_final","lab_range_min_final","lab_range_max_final"]]

        _sections = []
        for section in sections:
            date = extract_date_from_section(section)
            lab_results = labs_df[labs_df['date'] == date]
            if lab_results.empty: continue

            buffer = io.StringIO()
            try:
                lab_results.to_csv(buffer, index=False)
                csv_string = buffer.getvalue()
            finally:
                buffer.close()

            _section = section + f"\n\nLab Results CSV:\n{csv_string}"
            _sections.append(_section)
        sections = _sections

    # Precompute which sections need processing
    to_process = []
    for section in sections:
        date = extract_date_from_section(section)
        raw_hash = get_short_hash(section)
        processed_file = data_dir / f"{date}.processed.md"
        if processed_file.exists():
            processed_text = processed_file.read_text(encoding="utf-8").strip()
            _raw_hash = processed_text.splitlines()[0].strip()
            if _raw_hash == raw_hash: continue 
        to_process.append(section)
    
    # Process sections in parallel (only those that need processing)
    max_workers = int(os.getenv("MAX_WORKERS", "4"))
    failed_dates = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_process, section) for section in to_process]
        for future in tqdm(as_completed(futures), total=len(futures), desc="Processing sections"):
            date, ok = future.result()
            if not ok:
                failed_dates.append(date)

    if failed_dates:
        log_path = data_dir / "processing_failures.log"
        log_path.write_text("\n".join(failed_dates) + "\n", encoding="utf-8")
        print(f"Failed to process sections for dates: {', '.join(failed_dates)}")
        print(f"Failure log written to {log_path}")
    else:
        print("All sections processed successfully")

    # Write the final curated health log
    processed_files = list(data_dir.glob("*.processed.md"))
    processed_files = sorted(processed_files, key=lambda f: f.stem, reverse=True)
    processed_entries = ["\n".join(f.read_text(encoding="utf-8").splitlines()[1:]) for f in processed_files]
    processed_text = "\n\n".join(processed_entries)
    with open(data_dir / "output.md", "w", encoding="utf-8") as f: f.write(processed_text)
    print(f"Saved processed health log to {data_dir / 'output.md'}")

    # Write the summary using the LLM
    summary_file_path = data_dir / "summary.md"
    if not summary_file_path.exists():
        print("Generating health summary...")
        completion = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system", 
                    "content": SUMMARY_SYSTEM_PROMPT
                },
                {
                    "role": "user", 
                    "content": processed_text
                }
            ],
        # processed file names look like "YYYY-MM-DD.processed.md" so we only
        # want the date portion before the first dot
        log_dates = {f.name.split(".", 1)[0] for f in processed_files}
            temperature=0.0
        )
        summary = completion.choices[0].message.content.strip()
        with open(summary_file_path, "w", encoding="utf-8") as f: f.write(summary)
        print(f"Saved processed health summary to {data_dir / 'summary.md'}")

    # Write next steps using the LLM
    next_steps_file_path = data_dir / "next_steps.md"
    if not next_steps_file_path.exists():
        print("Generating next steps...")
        completion = client.chat.completions.create(
            model=model_id,
            messages=[
                {
                    "role": "system", 
                    "content": NEXT_STEPS_SYSTEM_PROMPT
                },
                {
                    "role": "user", 
                    "content": processed_text
                }
            ],
            max_tokens=4096,
            temperature=0.25
        )
        next_steps = completion.choices[0].message.content.strip()
        with open(next_steps_file_path, "w", encoding="utf-8") as f: f.write(next_steps)
        print(f"Saved processed health summary to {data_dir / 'next_steps.md'}")

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

        log_dates = {f.stem for f in processed_files}
        missing_dates = sorted(lab_dates - log_dates)
        if missing_dates:
            print("Lab output dates missing from health log:")
            for d in missing_dates:
                print(d)
        else:
            print("All lab output dates are present in the health log.")


def main():
    parser = argparse.ArgumentParser(description="Health log parser and validator")
    parser.add_argument("--health-log-path", help="Health log path")  # optional argument
    args = parser.parse_args()

    health_log_path = args.health_log_path or os.getenv("HEALTH_LOG_PATH")
    
    if not health_log_path:
        print("Health log path not provided and HEALTH_LOG_PATH not set")
        sys.exit(1)
    
    process(health_log_path)

if __name__ == "__main__":
    main()