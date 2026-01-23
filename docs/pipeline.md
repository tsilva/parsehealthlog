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
       |             |   6. BUILD ENTITY REGISTRY                 |
       |             |      LLM extracts facts -> active/inactive |-----> current.yaml
       |             |      Simple state model                    |-----> history.csv
       |             |                                            |-----> entities.json
       |             |                                            |
       +-------------+--------------------------------------------+

                     All steps use hash-based caching for efficiency
```

## Architecture Overview

The pipeline uses an **entity-centric architecture** with a simplified active/inactive model:

| Responsibility | Component | Implementation |
|----------------|-----------|----------------|
| Extract facts from prose | LLM | `extract.system_prompt.md` |
| Normalize entity names | Code | `EntityRegistry._normalize_name()` |
| Track active/inactive state | Code | Simple `active: bool` field |
| Assign IDs | Code | `EntityRegistry._generate_id()` |
| Link related entities | Code | Validated at creation time |

**Key simplification:** Instead of a complex state machine with many transitions, entities have a simple `active: true/false` state plus an `origin` field that records how they started.

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

### Step 6: Entity Registry Building

**What it does:** Builds a deterministic entity registry from processed entries using a two-phase approach: LLM extraction followed by code-managed state tracking.

**Key files/APIs:**
- `main.py:_build_entity_registry()` - Orchestration
- `main.py:_extract_entry_facts()` - LLM extraction with caching
- `entity_registry.py` - `EntityRegistry` class
- `prompts/extract.system_prompt.md` - Fact extraction prompt

**Phase 1: LLM Extraction (per entry)**

The LLM extracts structured facts from each processed entry as JSON:

```json
{
  "items": [
    {
      "type": "condition",
      "name": "Gastritis",
      "event": "noted",
      "details": "Improving, less epigastric pain",
      "for_condition": null
    },
    {
      "type": "supplement",
      "name": "DGL",
      "event": "started",
      "details": "Maintenance",
      "for_condition": "Gastritis"
    }
  ]
}
```

**Key principle:** LLM extracts facts only. No IDs. No state checking. Just "what does this entry say?"

Extracted facts are cached in `entries/<date>.extracted.json` with content hash for invalidation.

**Phase 2: Active/Inactive Tracking (deterministic)**

For each extracted fact, the `EntityRegistry` code:
1. **Matches/creates entity** - Fuzzy name matching (case-insensitive, ignores dosage)
2. **Updates active state** - Start events activate, stop events deactivate
3. **Assigns entity ID** - Sequential, guaranteed no gaps
4. **Links relationships** - Validated at creation time
5. **Records event in history**

**Simplified Event Model:**

| Type | Start Events | Stop Events |
|------|--------------|-------------|
| condition | `diagnosed`, `suspected`, `noted` | `resolved` |
| symptom | `noted` | `resolved` |
| medication | `started` | `stopped` |
| supplement | `started` | `stopped` |
| experiment | `started` | `ended` |
| provider | `visit` | - |
| todo | `added` | `completed` |

Status changes, dosage adjustments, and observations are captured in the `details` field rather than as separate event types.

**Output Files:**

| File | Purpose |
|------|---------|
| `current.yaml` | Active conditions and treatments (medications/supplements) |
| `history.csv` | Flat event log (Date, EntityID, Name, Type, Event, Details, RelatedEntity) |
| `entities.json` | Single source of truth for all entities |

**current.yaml format:**
```yaml
last_updated: 2024-03-20

active_conditions:
  - id: ent-001
    name: Gastritis
    origin: diagnosed
    since: 2024-03-10
    treatments: [ent-003]

active_treatments:
  - id: ent-003
    name: DGL
    origin: started
    since: 2024-03-20
    related_to: ent-001
```

**history.csv format:**
```csv
Date,EntityID,Name,Type,Event,Details,RelatedEntity
2024-03-10,ent-001,Gastritis,condition,diagnosed,"Stress-triggered",
2024-03-20,ent-001,Gastritis,condition,noted,"Improving, less pain",
2024-03-20,ent-003,DGL,supplement,started,"Maintenance",ent-001
```

**entities.json format:**
```json
{
  "entities": {
    "ent-001": {
      "type": "condition",
      "canonical_name": "Gastritis",
      "active": true,
      "origin": "diagnosed",
      "first_seen": "2024-03-10",
      "last_updated": "2024-03-20"
    }
  },
  "next_id": 6
}
```

---

## State Reset Feature

The state reset feature allows you to create a "snapshot" entry that represents your complete current health state. When an entry contains the `<!-- RESET_STATE -->` marker, all previously active entities are marked as inactive, and only items mentioned in that entry become the new active state.

**Use case:** When you want to say "as of this date, here's my complete current state - forget everything else that was active."

**Marker format:** `<!-- RESET_STATE -->` (HTML comment, won't render in markdown viewers)

**Example:**
```markdown
### 2024-03-20
<!-- RESET_STATE -->
- Currently taking Vitamin D 5000IU
- Currently taking Magnesium 400mg
- Active condition: Mild scoliosis
```

**Result:**
- All previously active conditions, symptoms, medications, supplements, and experiments are stopped/resolved
- Only items from this entry become active
- TODOs and provider visits are NOT reset (they persist)

**What gets reset:**

| Type | Stop Event | Reset? |
|------|------------|--------|
| condition | resolved | Yes |
| symptom | resolved | Yes |
| medication | stopped | Yes |
| supplement | stopped | Yes |
| experiment | ended | Yes |
| todo | - | **No** (tasks persist) |
| provider | - | **No** (visits are events) |

**History output:** Reset entities get a stop event with `details: "State reset"` in `history.csv`.

---

## Audit Feature

The audit template (`audit_template.md`) is **generated automatically** at the end of each run, listing all active entities with pre-filled stop events. This allows users to periodically review and clean up stale items.

To generate the audit template **without running full processing** (e.g., to quickly check current state):

```bash
uv run python main.py --profile <name> --generate-audit
```

**Audit template format:**

```markdown
### 2024-03-20 - State Audit

<!--
Instructions:
1. DELETE entries for items that are still active
2. KEEP entries for items you want to stop/resolve
3. Add this file content to your health log
4. Re-run processing to apply changes
-->

## Conditions

- Gastritis: resolved
  <!-- Last: 2024-03-15 | Origin: diagnosed -->

## Medications

- Pantoprazole 20mg: stopped
  <!-- Last: 2024-03-15 | For: Gastritis -->
```

The user edits this file, removes entries for items still active, and adds the remainder to their health log. Re-running processing will apply the stop events.

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
               |  EXTRACT FACTS   |  (per entry, LLM)
               |                  |
               |  Prose -> JSON   |
               |  (cached)        |
               +------------------+
                         |
                         v
               +------------------+
               |  BUILD REGISTRY  |  (code, deterministic)
               |                  |
               |  - Match entity  |
               |  - Update active |
               |  - Assign ID     |
               |  - Record event  |
               +------------------+
                         |
          +--------------+--------------+
          |              |              |
          v              v              v
     current.yaml   history.csv   entities.json
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Monolithic implementation: `HealthLogProcessor`, `LLM` wrapper, utilities |
| `entity_registry.py` | `EntityRegistry` class, active/inactive tracking, output generators |
| `config.py` | `Config` dataclass: loads/validates environment variables |
| `exceptions.py` | Custom exception classes: `ConfigurationError`, `PromptError`, etc. |
| `prompts/process.system_prompt.md` | Transforms raw entries into structured markdown |
| `prompts/validate.system_prompt.md` | Validates processed output (checks for `$OK$`) |
| `prompts/validate.user_prompt.md` | User prompt template for validation |
| `prompts/extract.system_prompt.md` | Extracts structured facts from processed entries |

## Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENROUTER_API_KEY` | Yes | - | OpenRouter API key |
| `HEALTH_LOG_PATH` | Yes | - | Path to markdown health log |
| `OUTPUT_PATH` | Yes | - | Base directory for generated output |
| `MODEL_ID` | No | `gpt-4o-mini` | Default model (fallback for all roles) |
| `PROCESS_MODEL_ID` | No | `MODEL_ID` | Model for processing sections |
| `VALIDATE_MODEL_ID` | No | `MODEL_ID` | Model for validating output |
| `STATUS_MODEL_ID` | No | `anthropic/claude-opus-4.5` | Model for fact extraction |
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
- `.extracted.json`: Content hash of the processed entry
- `health_log.md`: Content hash of assembled content

**Entity registry:** Rebuilt from scratch each run by replaying all extracted facts chronologically. This ensures consistency and simplicity.

### Reprocessing Logic

Files are regenerated when:
1. File doesn't exist
2. No DEPS comment found (old format migration)
3. Any dependency hash differs from stored hash

### Parallel Processing

- Section processing uses `ThreadPoolExecutor` with configurable `MAX_WORKERS`
- Fact extraction processes entries sequentially (chronological order required for state tracking)

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
