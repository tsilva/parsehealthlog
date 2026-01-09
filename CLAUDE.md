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
- `STALENESS_THRESHOLD_DAYS` - Days before an item is considered stale (default: 90)
- `STALENESS_MAX_AGE_DAYS` - Upper bound; items older than this are assumed resolved (default: 365)

## Architecture

### Core Components

**main.py** - Monolithic implementation containing:
- `HealthLogProcessor` class: Orchestrates the entire processing pipeline
- `LLM` dataclass: Lightweight wrapper around OpenAI chat completions
- Utility functions: `load_prompt()`, `extract_date()`, `format_labs()`, `short_hash()`

**config.py** - Configuration management via `Config` dataclass that loads and validates environment variables

**prompts/** directory - LLM prompts stored as separate markdown files:
- `process.system_prompt.md` - Transforms raw sections into structured output
- `validate.system_prompt.md` & `validate.user_prompt.md` - Validates processed sections
- `summary.system_prompt.md` - Generates patient summaries
- `targeted_questions.system_prompt.md` - Generates concise questions about stale items
- `next_steps_unified.system_prompt.md` - Unified "genius doctor" prompt for comprehensive next steps (includes lab recommendations)
- `action_plan.system_prompt.md` - Generates prioritized action plan
- `experiments.system_prompt.md` - Tracks N=1 health experiments
- `extract_entities.system_prompt.md` - Extracts entities for state model
- `merge_bullets.system_prompt.md` - Merges multiple bullet lists
- `self_resolving_conditions.yaml` - Configuration for acute conditions that auto-resolve (flu, cold, headache, etc.)

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
   - Targeted questions: Uses state model with staleness metadata to ask about items not mentioned in 90+ days
   - Unified next steps: Single "genius doctor" prompt combining all medical specialties + biohacking (includes lab recommendations)
   - Action plan: Synthesizes next_steps and experiments into prioritized action items
   - State model: Extracts entities from all sections into `state_model.json` with staleness tracking

8. **Staleness Detection** (`_compute_staleness()`):
   - Marks conditions/symptoms/medications as `potentially_stale` if not mentioned in `STALENESS_THRESHOLD_DAYS` (default 90 days)
   - **Smart filtering with sensible defaults**:
     - Self-resolving conditions (flu, cold, headache, etc.) auto-resolve after their typical resolution period
     - Items older than `STALENESS_MAX_AGE_DAYS` (default 365) are assumed resolved
     - Tracks `staleness_reason` for debugging (e.g., "self_resolved_30d", "too_old_400d")
   - Enables targeted questions workflow: pipeline asks about stale items, user adds status update entry to log, next run updates state model

### Output Structure

```
OUTPUT_PATH/<LOG>/
├─ entries/
│   ├─ <date>.raw.md           # Original section text
│   ├─ <date>.processed.md     # Hash + validated LLM output
│   ├─ <date>.entities.json    # Cached entity extraction (per-entry)
│   └─ <date>.labs.md          # Structured lab results
├─ intro.md                     # Pre-dated content
├─ state_model.json             # Aggregated entities + trends + staleness metadata
└─ reports/
    ├─ summary.md               # Patient summary
    ├─ targeted_clarifying_questions.md  # Questions about stale items
    ├─ next_steps.md            # Unified next steps (genius doctor, includes lab recommendations)
    ├─ experiments.md           # N=1 experiment tracker
    ├─ action_plan.md           # Prioritized action items
    ├─ output.md                # Full compiled report
    └─ clinical_data_missing_report.md  # Missing data audit
```

Logs are written to `logs/error.log` (errors only) and echoed to console (all levels).

### Caching Strategy

**Recursive Dependency Tracking** - All generated files use hash-based dependency tracking to ensure correct regeneration:

- **Hash Storage**: All generated files store dependencies in first line as HTML comment:
  ```html
  <!-- DEPS: key1:hash1,key2:hash2,... -->
  ```

- **Section processing** (`<date>.processed.md`):
  - Dependencies: `raw` (section content), `labs` (labs data), `process_prompt`, `validate_prompt`
  - Regenerates if: Section content changes, labs data changes, or prompts change
  - **IMPORTANT**: Hash-based caching is REQUIRED and should NOT be simplified. Since sections are re-extracted from the source markdown on every run, file timestamps are useless for cache invalidation.

- **Entity extraction** (`<date>.entities.json`):
  - Per-entry caching for entity extraction (avoids re-extracting all entries when one changes)
  - Dependencies: `processed` (processed section content hash), `prompt` (extract_entities prompt hash)
  - Regenerates if: Processed content changes or entity extraction prompt changes
  - Logs show "Entity extraction: X cached, Y extracted" to track cache performance

- **Report generation** (summary, targeted_questions, next_steps, etc.):
  - Dependencies: `processed` (all processed sections), `intro` (intro.md), `prompt` (specific prompt)
  - Targeted questions depend on: state_model.json (includes staleness metadata)
  - Regenerates if: Any processed section changes, intro changes, or prompt changes
  - output.md depends on summary + next_steps + action_plan + experiments

- **Prompt loading**: Lazy-loaded and cached in `self.prompts` dict

- **Migration**: Old format files (single hash) are automatically detected and regenerated with new dependency tracking

## Important Implementation Details

### Date Extraction
`extract_date()` parses dates from section headers:
- Supports `YYYY-MM-DD` and `YYYY/MM/DD` formats
- Handles em-dash/en-dash replacements
- Returns standardized `YYYY-MM-DD` format

### Validation Loop
Processing retries up to 3 times if validation fails:
- Validation succeeds when response contains `$OK$` marker

### LLM Configuration
- OpenRouter base URL: `https://openrouter.ai/api/v1`
- Separate model configs for each role (process, validate, summary, questions, next_steps)
- Default temperature: 0.0 (except next steps: 0.25)
- Default max_tokens: 2048 (reports: 8096)

### Prompt System
All prompts are external markdown files in `prompts/` directory:
- Loaded lazily via `load_prompt(name)`
- Validated at startup via `_validate_prompts()` to fail fast

## Do NOT Suggest

- Removing hash-based caching (required for correct cache invalidation since sections are re-extracted each run)
- Removing parallel processing (essential for fast regeneration of large logs with hundreds of entries)
- Timestamp-based caching (won't work since sections are re-extracted each run)

## Common Development Tasks

### Modifying Prompts
1. Edit the appropriate `.md` file in `prompts/` directory
2. Delete cached report files in `OUTPUT_PATH/<LOG>/reports/` to regenerate
3. Processed sections will be revalidated only if raw content hash changes

### Changing Output Structure
1. Modify `_assemble_output()` for final output format
2. Modify `_generate_file()` for report generation behavior

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
