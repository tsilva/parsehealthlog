> **Keep this doc updated when modifying the pipeline.**

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
       v             |   1. VALIDATE PROMPTS                      |
  [Markdown Log]---->|      Check all required prompts exist      |
       |             |                                            |
       |             |   2. SPLIT SECTIONS                        |
       +------------>|      Parse ### YYYY-MM-DD headers          |
       |             |                                            |
       |             |   3. LOAD LABS                             |
  labs.csv---------->|      CSV -> DataFrame by date              |
       |             |                                            |
       |             |   4. PROCESS SECTIONS (parallel)           |
       |             |      Raw -> LLM Process -> LLM Validate    |-----> entries/*.processed.md
       |             |      (retry up to 3x if validation fails)  |-----> entries/*.raw.md
       |             |                                            |-----> entries/*.labs.md
       |             |   5. SAVE COLLATED LOG                     |
       |             |      All entries newest to oldest          |-----> health_log.md
       |             |                                            |
       +-------------+--------------------------------------------+

                     All steps use hash-based caching for efficiency
```

## Pipeline Steps

### Step 1: Prompt Validation

**What it does:** Validates that all required LLM prompt files exist before any processing begins. Fails fast if prompts are missing.

**Key files/APIs:**
- `main.py:_validate_prompts()` - Validation logic
- `prompts/*.md` - Required prompt files

**Input:** Prompt file paths
**Output:** None (raises `PromptError` if validation fails)

---

### Step 2: Section Splitting

**What it does:** Parses the markdown health log to extract dated sections. Content before the first dated section is ignored.

**Key files/APIs:**
- `main.py:_split_sections()` - Parsing logic
- `main.py:extract_date()` - Date extraction from headers

**Input:** Raw markdown file (`HEALTH_LOG_PATH`)
**Output:** List of section strings, each starting with `### YYYY-MM-DD`

**Behavior notes:**
- Supports both `YYYY-MM-DD` and `YYYY/MM/DD` date formats
- Uses regex: `^###\s*\d{4}[-/]\d{2}[-/]\d{2}`
- Em-dash/en-dash characters are normalized to hyphens
- Content before the first dated section is discarded

---

### Step 3: Lab Data Loading

**What it does:** Loads laboratory test results from CSV files and groups them by date for merging with health log entries.

**Key files/APIs:**
- `main.py:_load_labs()` - CSV loading and normalization
- `main.py:format_labs()` - DataFrame to markdown conversion

**Input:**
- Per-log `labs.csv` (next to health log file)
- Aggregated `LABS_PARSER_OUTPUT_PATH/all.csv` (optional)

**Output:** `self.labs_by_date` dict mapping dates to DataFrames

**Behavior notes:**
- Handles multiple CSV column naming conventions via mapping
- Required columns: `date`, `lab_name_standardized`
- Boolean lab values formatted as Positive/Negative
- Numeric values include units, reference ranges, and status (OK/BELOW RANGE/ABOVE RANGE)
- Creates placeholder entries for dates with labs but no journal entry

---

### Step 4: Parallel Section Processing

**What it does:** Transforms raw health log sections into structured markdown using LLMs. Each section is processed and validated independently.

**Key files/APIs:**
- `main.py:_process_section()` - Per-section processing
- `main.py:_get_section_dependencies()` - Compute cache keys
- `prompts/process.system_prompt.md` - Processing prompt
- `prompts/validate.system_prompt.md` - Validation prompt

**Input:** Raw section text + associated labs
**Output:**
- `entries/<date>.raw.md` - Original section text
- `entries/<date>.processed.md` - Validated LLM output (with DEPS comment)
- `entries/<date>.labs.md` - Formatted lab results

**Behavior notes:**
- Uses `ThreadPoolExecutor` with `MAX_WORKERS` threads (default: 4)
- Validation retries up to 3 times if `$OK$` marker not found
- Failed sections create `.failed.md` diagnostic files
- Caching uses content hashes, not timestamps (sections re-extracted each run)

---

### Step 5: Collated Log Output

**What it does:** Assembles all processed entries into a single markdown file, ordered newest to oldest.

**Key files/APIs:**
- `main.py:_save_collated_health_log()` - Assembly logic

**Input:** All `entries/*.processed.md` files
**Output:** `health_log.md` - Complete health log with all entries

**Behavior notes:**
- Headers normalized to consistent levels
- Includes labs and exams integrated with each entry
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

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | - | OpenRouter API key |
| `HEALTH_LOG_PATH` | Yes | - | Path to markdown health log |
| `OUTPUT_PATH` | Yes | - | Base directory for generated output |
| `MODEL_ID` | No | `gpt-4o-mini` | Default model (fallback for all roles) |
| `PROCESS_MODEL_ID` | No | `MODEL_ID` | Model for processing sections |
| `VALIDATE_MODEL_ID` | No | `MODEL_ID` | Model for validating output |

| `LABS_PARSER_OUTPUT_PATH` | No | - | Path to aggregated lab CSVs |
| `MEDICAL_EXAMS_PARSER_OUTPUT_PATH` | No | - | Path to medical exam summaries |
| `MAX_WORKERS` | No | `4` | Parallel processing threads |

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
