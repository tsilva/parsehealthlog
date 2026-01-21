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
       |             |   6. BUILD HEALTH TIMELINE                 |
       |             |      Chronological CSV with episode IDs    |-----> health_log.csv
       |             |      Incremental processing (oldest→newest)|
       |             |                                            |
       |             |   7. VALIDATE TIMELINE                     |
       |             |      Episode continuity, references, etc   |-----> validation report
       |             |                                            |
       |             |   7.5 CORRECT EPISODE ERRORS (if needed)   |
       |             |       LLM fixes state consistency errors   |-----> corrected CSV
       |             |       (Type A/B/C corrections, max 3x)     |
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

### Step 7: Timeline Validation

**What it does:** Validates the generated timeline CSV for structural integrity, episode continuity, and logical consistency. Runs automatically after timeline building and reports any issues found. Critical validation failures block processing to prevent corrupted output.

**Key files/APIs:**
- `validate_timeline.py` - All validation functions
- `validate_timeline.run_all_validations()` - Orchestrator function
- `validate_timeline.get_validation_severity()` - Returns 'critical' or 'warning' for each check
- `validate_timeline.print_validation_report()` - Human-readable output
- `main.py:_validate_timeline_batch_output()` - Per-batch CSV validation during LLM generation

**Validation Severity Levels:**

Validation checks are categorized by severity:
- **CRITICAL**: Blocks processing, raises `ValueError` if failed (data integrity at risk)
- **WARNING**: Logged but processing continues (potential issues to review)

**Post-Processing Validation Checks:**

1. **Episode Continuity** [CRITICAL] (`validate_episode_continuity`)
   - Detects gaps in episode numbering (ep-001, ep-003 missing ep-002)
   - Detects duplicate episode IDs
   - Ensures sequential assignment

2. **CSV Structure** [CRITICAL] (`validate_csv_structure`)
   - Validates 7-column format: Date, EpisodeID, Item, Category, Event, RelatedEpisode, Details
   - Checks header row format
   - Detects malformed rows

3. **Chronological Order** [CRITICAL] (`validate_chronological_order`)
   - Ensures entries are sorted by date (oldest to newest)
   - Detects out-of-order entries

4. **Episode State Consistency** [CRITICAL] (`validate_episode_state_consistency`)
   - Detects events after terminal states (stopped, resolved, ended, completed)
   - Ensures no activity on terminated episodes
   - Example error: "ep-005 (Vitamin D 5000IU): Event 'adjusted' on 2024-05-01 after terminal event 'stopped' on 2024-03-01"

5. **Related Episodes** [WARNING] (`validate_related_episodes`)
   - Validates all RelatedEpisode references point to existing episodes
   - Detects orphaned references (ep-999 doesn't exist)

6. **Comprehensive Stack Updates** [WARNING] (`validate_comprehensive_stack_updates`)
   - Detects entries with `[STACK_UPDATE]` marker in Details field
   - Validates that all previously active supplements/medications/experiments were either stopped/ended or continued
   - Prevents "lost" items that weren't explicitly stopped during comprehensive updates
   - Checks marker consistency: all stopped/ended items on that date should have marker

**Batch Output Validation (during generation):**

Runs during LLM generation with automatic retry on critical failures (up to 3 attempts):

- Validates Date format (YYYY-MM-DD)
- Validates EpisodeID format (ep-XXX)
- Validates Category is in allowed set
- Validates Event matches Category
- Validates RelatedEpisode format **and existence** [Triggers Retry]
- Validates Date is in expected batch
- Validates EpisodeID >= expected minimum
- Detects duplicate entries (Date, Item, Category, Event) [Warning]
- Checks first episode ID matches expected (detects gaps) [Triggers Retry]

**Retry Logic:**
- Critical batch validation errors trigger automatic retry (max 2 retries = 3 total attempts)
- Retry triggers: Orphaned RelatedEpisode references, Episode ID gaps
- Non-critical errors (duplicates, format issues) logged as warnings only
- After exhausting retries, final output returned but will be caught by post-processing validation

**Output:** Console report showing validation results with severity indicators:
```
============================================================
TIMELINE VALIDATION REPORT
============================================================

✓ All validation checks passed!
```

Or if errors found:
```
============================================================
TIMELINE VALIDATION REPORT
============================================================

Found 5 validation error(s) (3 critical, 2 warnings)

[CRITICAL] Episode Continuity:
  - Episode ID gap: ep-001 → ep-003

[CRITICAL] Episode State Consistency:
  - ep-005 (Vitamin D 5000IU): Event 'started' on 2024-05-01 after terminal event 'stopped' on 2024-03-01

[WARNING] Related Episodes:
  - Line 45: 2024-03-15 Levothyroxine 50mcg - RelatedEpisode 'ep-999' does not exist

[WARNING] Comprehensive Stack Updates:
  - Date 2024-06-01: Some stopped items lack [STACK_UPDATE] marker: Omega-3 2000mg (ep-015)
```

**Behavioral notes:**
- Validation runs automatically at end of `run()` method
- **CRITICAL errors block processing:** Raises `ValueError` to prevent corrupted CSV from being saved
- WARNING errors are logged but processing continues
- All errors are displayed to user via console report with severity indicators
- Episode ID extraction parses only the EpisodeID column (not Details field)
- Batch validation with retry improves data quality by catching LLM errors early
- Post-processing validation provides final safety check before saving

**Testing:**
- Unit tests in `tests/test_validation.py`
- Integration tests in `tests/test_timeline_processing.py`

---

### Step 7.5: Episode State Correction Loop

**What it does:** Automatically corrects `episode_state_consistency` validation errors by feeding them back to an LLM for repair. This handles cases where the timeline builder loses visibility of old terminal events due to context compression.

**Key files/APIs:**
- `main.py:_correct_timeline_errors()` - Main correction loop orchestrator
- `main.py:_parse_episode_state_errors()` - Parse validation errors into structured data
- `main.py:_extract_episode_context()` - Get all CSV rows for affected episodes
- `main.py:_get_episode_corrections()` - Call LLM for correction decisions
- `main.py:_apply_episode_corrections()` - Apply corrections to CSV file
- `prompts/correct_episode_state.system_prompt.md` - LLM prompt for correction decisions

**When it runs:** After initial validation (Step 7), only if `episode_state_consistency` errors are found.

**Correction Types:**

The LLM chooses one of three correction strategies per episode:

| Type | Description | When to Use | Example |
|------|-------------|-------------|---------|
| **A** | Change terminal event to non-terminal | Condition is chronic, not cured | `resolved` → `stable` |
| **B** | Create new episode ID | Genuine recurrence/restart | Assign post-terminal rows to new ep-ID |
| **C** | Delete invalid rows | Erroneous duplicates | Remove the problematic rows entirely |

**Correction Flow:**

```
┌─────────────────────────────────────────────────────────────┐
│                    CORRECTION LOOP                          │
│                                                             │
│  Validation finds episode_state_consistency errors          │
│                         │                                   │
│                         v                                   │
│  Parse errors → group by episode ID                         │
│                         │                                   │
│                         v                                   │
│  Extract all CSV rows for affected episodes                 │
│                         │                                   │
│                         v                                   │
│  LLM decides: Type A, B, or C correction per episode        │
│                         │                                   │
│                         v                                   │
│  Apply corrections to CSV file                              │
│                         │                                   │
│                         v                                   │
│  Re-validate → loop if errors remain (max 3 iterations)     │
│                         │                                   │
│                         v                                   │
│  Return updated validation results                          │
└─────────────────────────────────────────────────────────────┘
```

**Error Format Parsed:**
```
ep-XXX (Item Name): Event 'event' on YYYY-MM-DD after terminal event 'terminal' on YYYY-MM-DD
```

**LLM Output Format (JSON):**
```json
{
  "corrections": [
    {
      "episode_id": "ep-042",
      "correction_type": "A",
      "explanation": "Chronic condition, not cured",
      "change_event": {
        "date": "2024-03-01",
        "old_event": "resolved",
        "new_event": "stable"
      }
    }
  ]
}
```

**Behavioral notes:**
- Runs up to 3 iterations (configurable via `max_iterations`)
- Stops early if all errors are resolved
- Uses the same `status` LLM model as timeline building
- Type A is preferred for conditions (chronic by nature)
- Type B is preferred for supplements/medications (clear stop/restart pattern)
- Type C is used sparingly for obvious data errors
- After correction loop, final validation report is printed
- If errors remain after max iterations, warning is logged but processing continues to critical failure check

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
                         |
                         v
               +------------------+
               | VALIDATE         |
               | TIMELINE         |
               |                  |
               | - Episode IDs    |
               | - References     |
               | - CSV structure  |
               | - Order          |
               +------------------+
                         |
                         v
                 validation report
                         |
          (if episode_state_consistency errors)
                         |
                         v
               +------------------+
               | CORRECT EPISODE  |
               | STATE ERRORS     |
               |                  |
               | - Parse errors   |
               | - LLM chooses    |
               |   Type A/B/C     |
               | - Apply fixes    |
               | - Re-validate    |
               | (max 3 loops)    |
               +------------------+
                         |
                         v
                 final validation
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Monolithic implementation: `HealthLogProcessor`, `LLM` wrapper, utilities |
| `config.py` | `Config` dataclass: loads/validates environment variables |
| `exceptions.py` | Custom exception classes: `ConfigurationError`, `PromptError`, etc. |
| `validate_timeline.py` | Timeline integrity validation functions |
| `prompts/process.system_prompt.md` | Transforms raw entries into structured markdown |
| `prompts/validate.system_prompt.md` | Validates processed output (checks for `$OK$`) |
| `prompts/validate.user_prompt.md` | User prompt template for validation |
| `prompts/update_timeline.system_prompt.md` | Builds chronological CSV timeline with episode IDs |
| `prompts/correct_episode_state.system_prompt.md` | Corrects episode state consistency errors |
| `tests/test_validation.py` | Unit tests for validation functions |
| `tests/test_timeline_processing.py` | Integration tests for timeline processing |

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
