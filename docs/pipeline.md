> **Keep this doc updated when modifying the pipeline.**

# Health Log Parser Pipeline

This document describes the data processing pipeline used by health-log-parser to transform unstructured health journal entries into structured reports.

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
       |             |   5. BUILD STATE MODEL                     |
       |             |      Entity extraction (cached per-entry)  |-----> state_model.json
       |             |      Aggregate + compute trends/staleness  |-----> entries/*.entities.json
       |             |                                            |
       |             |   6. GENERATE REPORTS                      |
       |             |      Summary, Questions, Next Steps, etc.  |-----> reports/*.md
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

### Step 5: State Model Building

**What it does:** Extracts structured entities (conditions, medications, symptoms, etc.) from all processed sections, aggregates them, and adds recency metadata.

**Key files/APIs:**
- `main.py:_build_state_model()` - Orchestration
- `main.py:_aggregate_entities()` - Merge entities across dates
- `main.py:_add_recency()` - Add days_since_mention to all items
- `prompts/extract_entities.system_prompt.md` - Entity extraction prompt

**Input:** All `entries/*.processed.md` files
**Output:**
- `state_model.json` - Aggregated entities with recency metadata
- `entries/<date>.entities.json` - Per-entry cached extractions

**Behavior notes:**
- Per-entry caching avoids re-extracting unchanged entries
- Logs show "Entity extraction: X cached, Y extracted" for cache performance
- `days_since_mention` is computed for all items (pure math, no hardcoded thresholds)
- LLM uses its medical knowledge to judge relevance based on recency
- Design principle: Defer medical knowledge to the LLM rather than encoding rules in Python

---

### Step 6: Report Generation

**What it does:** Generates various reports using the state model and processed entries as input.

**Key files/APIs:**
- `main.py:_generate_file()` - Generic report generation with caching
- `main.py:_generate_action_plan()` - Synthesize action plan
- `main.py:_generate_dashboard()` - Create dashboard view
- `main.py:_assemble_output()` - Combine summary + entries
- `main.py:_assemble_final_output()` - Combine all reports

**Input:** `state_model.json`, processed entries, intro.md
**Output (in `reports/` directory):**

| Report | Description | Input |
|--------|-------------|-------|
| `summary.md` | Clinical overview | Processed entries + intro |
| `targeted_clarifying_questions.md` | Questions about stale items | Filtered stale items from state model |
| `next_steps.md` | Comprehensive recommendations | State model markdown |
| `experiments.md` | N=1 experiment tracker | State model markdown |
| `action_plan.md` | Prioritized action items | Summary + next_steps + experiments |
| `dashboard.md` | Quick stats and links | State model |
| `output.md` | Full compiled report | All above + entries |

**Behavior notes:**
- Each report uses dependency tracking for caching
- Multi-call variants (multiple LLM calls) merged via `merge_bullets.system_prompt.md`
- Temperature settings: 0.0 for most, 0.25 for next_steps and experiments

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
                 +---------------+
                 |    EXTRACT    |  (parallel, cached per-entry)
                 |   ENTITIES    |
                 +---------------+
                         |
                         v
               <date>.entities.json
                         |
                         v
                 +---------------+
                 |   AGGREGATE   |
                 |   + RECENCY   |
                 +---------------+
                         |
                         v
                  state_model.json
                         |
         +---------------+---------------+
         |               |               |
         v               v               v
   +-----------+   +-----------+   +-----------+
   |  SUMMARY  |   | NEXT STEPS|   |EXPERIMENTS|
   +-----------+   +-----------+   +-----------+
         |               |               |
         +-------+-------+-------+-------+
                 |               |
                 v               v
           action_plan.md    dashboard.md
                 |
                 v
            output.md (final assembled report)
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
| `prompts/summary.system_prompt.md` | Generates patient clinical summary |
| `prompts/targeted_questions.system_prompt.md` | Generates questions about stale items |
| `prompts/next_steps_unified.system_prompt.md` | "Genius doctor" comprehensive recommendations |
| `prompts/action_plan.system_prompt.md` | Time-bucketed prioritized actions |
| `prompts/experiments.system_prompt.md` | N=1 experiment tracking |
| `prompts/extract_entities.system_prompt.md` | JSON entity extraction for state model |
| `prompts/merge_bullets.system_prompt.md` | Merges multiple LLM outputs |
| `prompts/standard_treatments.yaml` | Treatments excluded from experiment tracking |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | - | OpenRouter API key |
| `HEALTH_LOG_PATH` | Yes | - | Path to markdown health log |
| `OUTPUT_PATH` | Yes | - | Base directory for generated output |
| `MODEL_ID` | No | `gpt-4o-mini` | Default model (fallback for all roles) |
| `PROCESS_MODEL_ID` | No | `MODEL_ID` | Model for processing sections |
| `VALIDATE_MODEL_ID` | No | `MODEL_ID` | Model for validating output |
| `SUMMARY_MODEL_ID` | No | `MODEL_ID` | Model for generating summary |
| `QUESTIONS_MODEL_ID` | No | `MODEL_ID` | Model for generating questions |
| `NEXT_STEPS_MODEL_ID` | No | `MODEL_ID` | Model for generating recommendations |
| `LABS_PARSER_OUTPUT_PATH` | No | - | Path to aggregated lab CSVs |
| `REPORT_OUTPUT_PATH` | No | - | Copy final report to this path |
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
- `.entities.json`: `processed` (content hash), `prompt` (extraction prompt hash)
- Reports: `processed` (all sections), `intro`, `prompt` (specific prompt hash)

### Reprocessing Logic

Files are regenerated when:
1. File doesn't exist
2. No DEPS comment found (old format migration)
3. Any dependency hash differs from stored hash

### Parallel Processing

- Section processing uses `ThreadPoolExecutor` with configurable `MAX_WORKERS`
- Entity extraction runs in parallel with per-entry caching
- Reports generate sequentially (dependencies between them)

### Error Handling

- Failed section processing creates `.failed.md` with diagnostic info
- LLM calls use exponential backoff retry (3 attempts)
- Validation failures retry up to 3 times with feedback loop

### State Tracking

Progress tracked in `.state.json`:
- `status`: not_started | in_progress | completed | completed_with_errors
- `started_at`, `completed_at`: ISO timestamps
- `sections_total`, `sections_processed`: Progress counters
- `reports_generated`: List of generated report filenames
