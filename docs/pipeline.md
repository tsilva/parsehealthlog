# Health Log Parser Pipeline

This document describes the data processing pipeline used by parsehealthlog to transform unstructured health journal entries into structured data.

## Quick Overview

```
                               HEALTH LOG PARSER PIPELINE
                               ==========================

  Input                         Processing                           Output
  -----                         ----------                           ------

  health.md          +--------------------------------------------+
       |             |                                            |
       v             |   1. VALIDATE SOURCE DATES                |
  [Markdown Log]---->|      Check headers are valid and ordered   |
       |             |                                            |
       |             |   2. VALIDATE PROMPTS                      |
       |             |      Check all required prompts exist      |
       |             |                                            |
       |             |   3. SPLIT SECTIONS                        |
       +------------>|      Parse ### YYYY-MM-DD headers          |
       |             |                                            |
       |             |   4. LOAD LABS                             |
  labs.csv---------->|      CSV -> DataFrame by date              |
       |             |                                            |
       |             |   5. PROCESS SECTIONS (parallel)           |
       |             |      Raw -> LLM Process -> LLM Validate    |-----> entries/*.processed.md
       |             |      (retry up to 3x if validation fails)  |-----> entries/*.raw.md
       |             |                                            |-----> entries/*.labs.md
       |             |   6. SAVE COLLATED LOG                     |
       |             |      All entries newest to oldest          |-----> health_log.md
       |             |                                            |
       +-------------+--------------------------------------------+

                     All steps use hash-based caching for efficiency
```

## Pipeline Steps

### Step 1: Source Date Validation

**What it does:** Validates the source health log date headers and existing extracted journal entry files before cache deletion, lab/exam loading, or LLM extraction begins. If validation fails, the process exits nonzero so scripts can stop immediately.

**Key files/APIs:**
- `main.py:validate_health_log_dates()` - Preflight validation logic
- `main.py:validate_extracted_entry_dates()` - Stale extracted entry validation

**Input:** Raw markdown file (`health_log_path`) and existing `entries/` cache directory
**Output:** None (raises `DateValidationError` if validation fails)

**Behavior notes:**
- Date section headers may use `### YYYY-MM-DD` or `### YYYY/MM/DD`
- Accepted date headers are normalized internally to `### YYYY-MM-DD`
- En/em dash dates, malformed dates, and impossible calendar dates fail
- Duplicate dates fail
- Dates must be monotonic in one direction, either oldest-to-newest or newest-to-oldest
- Extracted journal entry files for dates no longer present in the raw health log fail in bulk before any generated files are deleted or rewritten
- Lab/exam-only placeholder entries with `raw:none` dependencies are allowed without matching journal sections

---

### Step 2: Prompt Validation

**What it does:** Validates that all required LLM prompt files exist before any processing begins. Fails fast if prompts are missing.

**Key files/APIs:**
- `main.py:_validate_prompts()` - Validation logic
- `prompts/*.md` - Required prompt files

**Input:** Prompt file paths
**Output:** None (raises `PromptError` if validation fails)

---

### Step 3: Section Splitting

**What it does:** Parses the markdown health log to extract dated sections. Content before the first dated section is ignored.

**Key files/APIs:**
- `main.py:_split_sections()` - Parsing logic
- `main.py:extract_date()` - Date extraction from headers

**Input:** Raw markdown file (`health_log_path`)
**Output:** List of section strings, each starting with `### YYYY-MM-DD`

**Behavior notes:**
- `YYYY-MM-DD` and `YYYY/MM/DD` section headers are accepted
- Returned section headers are normalized to `YYYY-MM-DD`
- Content before the first dated section is discarded

---

### Step 4: Lab Data Loading

**What it does:** Loads laboratory test results from CSV files, groups them by date, and later renders them into readable markdown sections inside each date block.

**Key files/APIs:**
- `main.py:_load_labs()` - CSV loading and normalization
- `main.py:format_labs()` - DataFrame to markdown conversion

**Input:**
- Per-log `labs.csv` (next to the health log file)
- Aggregated `labs_parser_output_path/all.csv` (optional)

**Output:** `self.labs_by_date` dict mapping dates to DataFrames

**Behavior notes:**
- Handles multiple CSV column naming conventions via mapping
- Required columns: `date`, `lab_name_standardized`
- Lab results are grouped by standardized name prefixes (for example `Blood`, `Urine Type II`)
- Boolean and numeric values are preserved as raw extracted values with units and reference ranges when present
- Creates placeholder entries for dates with labs but no journal entry

---

### Step 5: Parallel Section Processing

**What it does:** Transforms raw health log sections into structured markdown using LLMs. Each section is processed and validated independently.

**Key files/APIs:**
- `main.py:_process_section()` - Per-section processing
- `main.py:_get_section_dependencies()` - Compute cache keys
- `prompts/process.system_prompt.md` - Processing prompt
- `prompts/validate.system_prompt.md` - Validation prompt

**Input:** Raw section text + associated labs/exams
**Output:**
- `entries/<date>.raw.md` - Original section text
- `entries/<date>.processed.md` - Validated, sectioned markdown (with DEPS comment)
- `entries/<date>.labs.md` - `## Lab Results` section for that date
- `entries/<date>.exams.md` - `## Medical Exams` section for that date

**Behavior notes:**
- Uses `ThreadPoolExecutor` with `MAX_WORKERS` threads (default: 4)
- Validation retries up to 3 times if `$OK$` marker not found
- Processed date blocks are assembled in source order: `## Journal`, `## Lab Results`, `## Medical Exams`
- Imported exam summaries have YAML front matter stripped and are normalized into titled bullet-based blocks
- Failed sections create `.failed.md` diagnostic files
- Caching uses content hashes, not timestamps (sections re-extracted each run)

---

### Step 6: Collated Log Output

**What it does:** Assembles all processed entries into a single markdown file, ordered newest to oldest, while preserving the per-date source subsections.

**Key files/APIs:**
- `main.py:_save_collated_health_log()` - Assembly logic

**Input:** All `entries/*.processed.md` files
**Output:** `health_log.md` - Complete health log with all entries

**Behavior notes:**
- Top-level date headers use `# YYYY-MM-DD`
- Nested content is normalized so each date block reads as one record with `## Journal`, `## Lab Results`, and `## Medical Exams` sections when present
- Uses content hash for caching

---

## Data Flow Diagram

```
                           DATA FLOW
                           =========

    health.md                                        labs.csv
        |                                               |
        v                                               v
   +----------+                                  +-----------+
   |  SPLIT   |                                  |  LOAD     |
   | SECTIONS |                                  |  LABS     |
   +----------+                                  +-----------+
        |                                               |
        +----------------+     +------------------------+
                         |     |
                         v     v
                   +--------------+
                   |   PROCESS    |  (parallel, per-section)
                   |   SECTION    |
                   |              |
                   |  raw -> LLM  |
                   |  -> validate |
                   +--------------+
                         |
       +-----------------+-----------------+
       |                 |                 |
       v                 v                 v
  <date>.raw.md   <date>.processed.md  <date>.labs.md
                         |
                         v
               +------------------+
               |  COLLATE LOG     |
               |                  |
               |  All entries     |
               |  newest→oldest   |
               +------------------+
                         |
                         v
                   health_log.md
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Monolithic implementation: `HealthLogProcessor`, `LLM` wrapper, utilities |
| `config.py` | `Config` dataclass: loads/validates environment variables |
| `exceptions.py` | Custom exception classes: `ConfigurationError`, `PromptError`, etc. |
| `prompts/process.system_prompt.md` | Transforms raw entries into structured markdown |
| `prompts/validate.system_prompt.md` | Validates processed output (checks for `$OK$`) |
| `prompts/validate.user_prompt.md` | User prompt template for validation |

## Configuration

Runtime configuration is loaded from `~/.config/parsehealthlog`:
- Environment variables: `~/.config/parsehealthlog/.env` or `~/.config/parsehealthlog/.env.<name>`
- Profiles: `~/.config/parsehealthlog/profiles/<name>.yaml`

Environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | - | OpenRouter API key |
| `MODEL_ID` | No | `gpt-4o-mini` | Model used for processing and validation |
| `MAX_WORKERS` | No | `4` | Parallel processing threads when the profile omits `workers` |

Profile fields:

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `health_log_path` | Yes | - | Path to the markdown health log |
| `output_path` | Yes | - | Base directory for generated output |
| `base_url` | No | `https://openrouter.ai/api/v1` | OpenAI-compatible API base URL |
| `workers` | No | `4` | Parallel processing threads |
| `labs_parser_output_path` | No | - | Path to aggregated lab CSVs |
| `medical_exams_parser_output_path` | No | - | Path to medical exam summaries |

## Behavioral Notes

### Caching Strategy

All generated files use hash-based dependency tracking stored in the first line:

```html
<!-- DEPS: key1:hash1,key2:hash2,... -->
```

**Why hash-based caching?** Sections are re-extracted from the source markdown on every run, so file timestamps are useless for cache invalidation.

**Cache dependencies by file type:**
- `.processed.md`: `raw` (section content), `labs`, `exams`, `process_prompt`, `validate_prompt`
- `health_log.md`: Content hash of assembled content

### Reprocessing Logic

Files are regenerated when:
1. File doesn't exist
2. No DEPS comment found (old format migration)
3. Any dependency hash differs from stored hash

### Parallel Processing

- Section processing uses `ThreadPoolExecutor` with configurable `MAX_WORKERS`

### Error Handling

- Failed section processing creates `.failed.md` with diagnostic info
- LLM calls use exponential backoff retry (3 attempts)
- Validation failures retry up to 3 times with feedback loop
- Unknown events logged as warnings, processing continues

### State Tracking

Progress tracked in `.state.json`:
- `status`: not_started | in_progress | completed | completed_with_errors
- `started_at`, `completed_at`: ISO timestamps
- `sections_total`, `sections_processed`: Progress counters
