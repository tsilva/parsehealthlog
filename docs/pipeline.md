> **Keep this doc updated when modifying the pipeline.**

# Health Log Parser Pipeline

This document describes the data processing pipeline used by health-log-parser to transform unstructured health journal entries into structured data.

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
       +------------>|      Parse ### YYYY-MM-DD headers          |-----> intro.md
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
       |             |   6. BUILD HEALTH TIMELINE                 |
       |             |      Chronological CSV with episode IDs    |-----> health_log.csv
       |             |      Incremental processing (oldest→newest)|
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

**What it does:** Parses the markdown health log to extract dated sections. Separates pre-dated introductory content from dated entries.

**Key files/APIs:**
- `main.py:_split_sections()` - Parsing logic
- `main.py:extract_date()` - Date extraction from headers

**Input:** Raw markdown file (`HEALTH_LOG_PATH`)
**Output:**
- `intro.md` - Pre-dated content (background info, patient history)
- List of section strings, each starting with `### YYYY-MM-DD`

**Behavior notes:**
- Supports both `YYYY-MM-DD` and `YYYY/MM/DD` date formats
- Uses regex: `^###\s*\d{4}[-/]\d{2}[-/]\d{2}`
- Em-dash/en-dash characters are normalized to hyphens

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

### Step 6: Health Timeline Building

**What it does:** Builds a chronological CSV timeline from all processed sections. Each row represents a health event with episode IDs linking related events across time.

**Key files/APIs:**
- `main.py:_build_health_timeline()` - Orchestration with incremental logic
- `main.py:_get_chronological_entries()` - Get entries sorted oldest→newest
- `main.py:_parse_timeline_header()` - Extract processed_through, hash, last episode
- `main.py:_process_timeline_batch()` - Call LLM to generate new CSV rows
- `prompts/update_timeline.system_prompt.md` - Timeline building prompt

**Input:** All `entries/*.processed.md` files (sorted chronologically)
**Output:** `health_log.csv` - CSV timeline with metadata header

**CSV Format:**
```csv
Date,EpisodeID,Item,Category,Event,Details
2024-01-15,ep-001,Vitamin D 2000IU,supplement,started,"Optimization, daily"
2024-03-10,ep-002,Gastritis,condition,flare,Stress-triggered
2024-03-12,ep-003,Pantoprazole 20mg,medication,started,"For ep-002, PRN"
```

**Categories & Events:**
| Category | Events |
|----------|--------|
| condition | diagnosed, suspected, flare, improved, worsened, resolved, stable |
| symptom | noted, improved, worsened, resolved, stable |
| medication | started, adjusted, stopped |
| supplement | started, adjusted, stopped |
| experiment | started, update, ended |
| provider | visit |
| watch | noted, resolved |
| todo | added, completed |

**Episode ID Semantics:**
- New condition/symptom → new EpisodeID (ep-001, ep-002, ...)
- Follow-up events for same episode → reuse EpisodeID
- Medication/supplement for a condition → new EpisodeID, Details references what it treats ("For ep-002")
- Experiments → new EpisodeID, updates use same ID

**Incremental Processing:**

The timeline uses per-entry hash tracking to enable incremental rebuilds from any change point.

Header format (two lines):
```
# Last updated: 2024-01-20 | Processed through: 2024-01-20 | LastEp: 42
# HASHES: 2024-01-15=a1b2c3d4,2024-01-20=b2c3d4e5
```

Processing modes:
- **Cache hit**: All entry hashes match, no new entries → return existing timeline
- **Append mode**: All existing hashes match, new entries after `processed_through` → append new rows only
- **Incremental rebuild**: Entry modified, deleted, or inserted in middle → truncate timeline to change point and rebuild from there
- **Full rebuild**: First run, migration from old format, or no HASHES line → rebuild from scratch

Change detection algorithm:
1. Compare current entry hashes against stored HASHES line
2. Find earliest date where: entry deleted, entry modified, or new entry inserted before `processed_through`
3. Truncate timeline to keep rows before change point
4. Reprocess entries from change point forward

**Design Principle:** Trust the LLM for medical reasoning rather than encoding rules in Python. The timeline captures events; downstream consumers interpret significance.

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
        v                                               |
   intro.md                                             |
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
                         |
                         v
               +------------------+
               |  BUILD TIMELINE  |  (batched, chronological)
               |                  |
               |  entries sorted  |
               |  oldest→newest   |
               |  -> LLM appends  |
               |     CSV rows     |
               +------------------+
                         |
                         v
                  health_log.csv
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
| `prompts/update_timeline.system_prompt.md` | Builds chronological CSV timeline with episode IDs |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | - | OpenRouter API key |
| `HEALTH_LOG_PATH` | Yes | - | Path to markdown health log |
| `OUTPUT_PATH` | Yes | - | Base directory for generated output |
| `MODEL_ID` | No | `gpt-4o-mini` | Default model (fallback for all roles) |
| `PROCESS_MODEL_ID` | No | `MODEL_ID` | Model for processing sections |
| `VALIDATE_MODEL_ID` | No | `MODEL_ID` | Model for validating output |
| `STATUS_MODEL_ID` | No | `anthropic/claude-opus-4.5` | Model for building health timeline |
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
- `.processed.md`: `raw` (section content), `labs`, `process_prompt`, `validate_prompt`
- `health_log.csv`: Per-entry hashes stored in HASHES line
- `health_log.md`: Content hash of assembled content

**Timeline incremental processing:**
- Header stores: `processed_through` date, `last_episode_id`, and per-entry HASHES
- If all entry hashes match and new entries exist → append new rows only
- If entry modified/deleted/inserted in middle → rebuild from earliest change point
- Earlier rows before change point are preserved unchanged

### Reprocessing Logic

Files are regenerated when:
1. File doesn't exist
2. No DEPS comment found (old format migration)
3. Any dependency hash differs from stored hash

### Parallel Processing

- Section processing uses `ThreadPoolExecutor` with configurable `MAX_WORKERS`
- Timeline building processes entries in batches (chronological order)

### Error Handling

- Failed section processing creates `.failed.md` with diagnostic info
- LLM calls use exponential backoff retry (3 attempts)
- Validation failures retry up to 3 times with feedback loop

### State Tracking

Progress tracked in `.state.json`:
- `status`: not_started | in_progress | completed | completed_with_errors
- `started_at`, `completed_at`: ISO timestamps
- `sections_total`, `sections_processed`: Progress counters
