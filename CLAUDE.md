# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

health-log-parser is an AI-powered tool that transforms unstructured personal health journal entries into clean, standardized Markdown summaries. It uses LLMs (via OpenRouter) to extract, format, and analyze medical visits, medications, symptoms, and lab results.

The tool processes health logs structured with `### YYYY-MM-DD` section headers, validates the output for accuracy, generates patient summaries, and provides specialist-specific recommendations.

## Development Commands

### Setup
```bash
# Install uv package manager (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

### Running the Application
```bash
# Run the parser
uv run python main.py

# Or activate the virtual environment first
source .venv/bin/activate  # macOS/Linux
python main.py
```

### Configuration
Create a `.env` file with required environment variables (see `.env.example`):
- `OPENROUTER_API_KEY` - Required: OpenRouter API key
- `HEALTH_LOG_PATH` - Required: Path to the markdown health log
- `OUTPUT_PATH` - Required: Base directory for generated output
- `MODEL_ID` - Default model (fallback for all roles, default: gpt-4o-mini)
- `PROCESS_MODEL_ID`, `VALIDATE_MODEL_ID`, `QUESTIONS_MODEL_ID`, `SUMMARY_MODEL_ID`, `NEXT_STEPS_MODEL_ID` - Optional model overrides for specific stages
- `LABS_PARSER_OUTPUT_PATH` - Optional: Path to aggregated lab CSVs
- `MAX_WORKERS` - Parallel processing threads (default: 4)
- `QUESTIONS_RUNS` - Number of clarifying question generation runs (default: 3)

## Architecture

### Core Components

**main.py** (609 lines) - Monolithic implementation containing:
- `HealthLogProcessor` class: Orchestrates the entire processing pipeline
- `LLM` dataclass: Lightweight wrapper around OpenAI chat completions
- Utility functions: `load_prompt()`, `extract_date()`, `format_labs()`, `short_hash()`

**config.py** - Configuration management via `Config` dataclass that loads and validates environment variables

**prompts/** directory - LLM prompts stored as separate markdown files:
- `process.system_prompt.md` - Transforms raw sections into structured output
- `validate.system_prompt.md` & `validate.user_prompt.md` - Validates processed sections
- `summary.system_prompt.md` - Generates patient summaries
- `questions.system_prompt.md` - Generates clarifying questions
- `specialist_next_steps.system_prompt.md` - Generates specialist-specific recommendations
- `consensus_next_steps.system_prompt.md` - Merges specialist recommendations
- `next_labs.system_prompt.md` - Suggests next lab tests
- `merge_bullets.system_prompt.md` - Merges multiple bullet lists

### Processing Pipeline

1. **Validation** (`_validate_prompts()`): Ensures all required prompt files exist before processing begins (fails fast)

2. **Section Splitting** (`_split_sections()`): Parses the input markdown to extract:
   - Pre-dated content (saved as `intro.md`)
   - Dated sections (regex: `^###\s*\d{4}[-/]\d{2}[-/]\d{2}`)

3. **Lab Data Loading** (`_load_labs()`): Loads lab results from:
   - Per-log `labs.csv` (next to health log)
   - Aggregated `LABS_PARSER_OUTPUT_PATH/all.csv`
   - Groups by date for merging with entries

4. **Parallel Section Processing** (`_process_section()`):
   - Caching: Checks if processed file exists and hash matches raw section (first line stores hash)
   - Processing: Uses PROCESS model to transform raw → structured
   - Validation: Uses VALIDATE model to check accuracy; retries up to 3 times if validation fails (looks for `$OK$` marker)
   - Uses `ThreadPoolExecutor` with `MAX_WORKERS` threads

5. **Lab Formatting** (`format_labs()`): Converts lab DataFrames to markdown:
   - Boolean values formatted as Positive/Negative
   - Numeric values with units and reference ranges
   - Status indicators: OK, BELOW RANGE, ABOVE RANGE

6. **Output Assembly** (`_assemble_output()`):
   - Combines processed sections in reverse chronological order
   - Merges lab results with corresponding dates
   - Generates summary using SUMMARY model

7. **Report Generation** (`_generate_file()`):
   - Caching: Skips generation if report already exists
   - Multi-call support: For diverse outputs (e.g., clarifying questions runs 3 times by default)
   - Merging: Uses `merge_bullets` prompt to consolidate multiple outputs
   - Clarifying questions: Runs `QUESTIONS_RUNS` times with temperature=1.0 for diversity
   - Specialist next steps: Generates for 14 specialties (endocrinology, gastroenterology, cardiology, dermatology, pulmonology, urology, hematology, neurogastroenterology, neurology, psychiatry, nutrition, rheumatology, internal medicine, genetics)
   - Consensus next steps: Merges all specialist recommendations

### Output Structure

```
OUTPUT_PATH/<LOG>/
├─ entries/
│   ├─ <date>.raw.md           # Original section text
│   ├─ <date>.processed.md     # Hash + validated LLM output
│   └─ <date>.labs.md          # Structured lab results
├─ intro.md                     # Pre-dated content
└─ reports/
    ├─ summary.md               # Patient summary
    ├─ clarifying_questions_<N>.md  # Raw questions from each run
    ├─ clarifying_questions.md      # Merged questions
    ├─ next_steps_<specialty>.md    # Per-specialty recommendations
    ├─ next_steps.md            # Consensus recommendations
    ├─ next_labs.md             # Suggested lab tests
    ├─ output.md                # Summary + curated log
    └─ clinical_data_missing_report.md  # Missing data audit
```

Logs are written to `logs/error.log` (errors only) and echoed to console (all levels).

### Caching Strategy

- **Section processing**: Cached if `<date>.processed.md` exists and first line matches `short_hash(section)` (8-char SHA-256 prefix)
  - **IMPORTANT**: Hash-based caching is REQUIRED and should NOT be simplified. Since sections are re-extracted from the source markdown on every run, file timestamps are useless for cache invalidation. The content hash is the only reliable way to detect if a section has changed without reprocessing everything.
- **Report generation**: Cached if report file exists (regenerate by deleting the file)
- **Prompt loading**: Lazy-loaded and cached in `self.prompts` dict

### Specialties

The tool generates next steps for these 14 medical specialties (main.py:100-115):
endocrinology, gastroenterology, cardiology, dermatology, pulmonology, urology, hematology, neurogastroenterology, neurology, psychiatry, nutrition, rheumatology, internal medicine, genetics

## Important Implementation Details

### Date Extraction
`extract_date()` (main.py:128) parses dates from section headers:
- Supports `YYYY-MM-DD` and `YYYY/MM/DD` formats
- Handles em-dash/en-dash replacements
- Returns standardized `YYYY-MM-DD` format
- **Security note**: Dates become filenames without sanitization (see IMPROVEMENTS.md #16)

### Validation Loop
Processing retries up to 3 times if validation fails (main.py:461-492):
- Failed sections are logged but not saved for debugging (see IMPROVEMENTS.md #5)
- Validation succeeds when response contains `$OK$` marker

### LLM Configuration
- OpenRouter base URL: `https://openrouter.ai/api/v1`
- Separate model configs for each role (process, validate, summary, questions, next_steps)
- Default temperature: 0.0 (except questions: 1.0, next steps: 0.25)
- Default max_tokens: 2048 (reports: 8096)

### Prompt System
All prompts are external markdown files in `prompts/` directory:
- Loaded lazily via `load_prompt(name)`
- Validated at startup via `_validate_prompts()` to fail fast
- Some prompts support string formatting (e.g., specialist_next_steps uses `{specialty}`)

## Known Issues & Improvements

See IMPROVEMENTS.md for detailed simplification opportunities. Major areas:

**High-impact simplifications:**
- Remove validation step (50% API call reduction)
- Consolidate specialist reports (15 calls → 1 call)
- Single questions run instead of multi-run + merge (4 calls → 1 call)

**Code cleanup:**
- Remove debug print statements (main.py:209, 211)
- Add API retry logic for transient failures
- Sanitize filenames from user input (security risk at main.py:128)
- Fix broad exception handling (main.py:166)

**Do NOT suggest:**
- Removing hash-based caching (required for correct cache invalidation since sections are re-extracted each run)
- Removing parallel processing (essential for fast regeneration of large logs with hundreds of entries)
- Timestamp-based caching (won't work since sections are re-extracted each run)

## Common Development Tasks

### Adding a New Specialty
1. Add specialty name to `SPECIALTIES` list (main.py:100-115)
2. The system will automatically generate `next_steps_<specialty>.md` report

### Modifying Prompts
1. Edit the appropriate `.md` file in `prompts/` directory
2. Delete cached report files in `OUTPUT_PATH/<LOG>/reports/` to regenerate
3. Processed sections will be revalidated only if raw content hash changes

### Changing Output Structure
1. Modify `_assemble_output()` for final output format
2. Modify `_generate_file()` for report generation behavior
3. Consider versioning output format (see IMPROVEMENTS.md #24)

### Adding New Report Types
Use `_generate_file()` method:
```python
self._generate_file(
    "report_name.md",
    "prompt_name.system_prompt",
    role="questions",  # which model config to use
    temperature=0.0,
    extra_messages=[{"role": "user", "content": content}],
    description="human-readable description"
)
```
