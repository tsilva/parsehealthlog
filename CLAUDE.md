# CLAUDE.md

## Project Goal

health-log-parser is a **data extraction and curation tool** that transforms health journal entries into structured, validated data:

1. **`health_log.md`** - All processed entries (newest to oldest) with labs/exams integrated
2. **`current.yaml`** - Active conditions, medications, supplements, and pending TODOs
3. **`history.csv`** - Chronological event log with entity IDs linking related events
4. **`entities.json`** - Single source of truth for all tracked health entities

The tool processes, validates, and enriches health log entries but does **not** generate reports, summaries, or recommendations. Those are left to downstream consumers of the structured data.

## Quick Start

```bash
# Setup
curl -LsSf https://astral.sh/uv/install.sh | sh  # Install uv if needed
uv sync                                           # Install dependencies

# Run
uv run python main.py --profile <profile_name>

# Profile configuration (profiles/<name>.yaml)
health_log_path: /path/to/health.md    # Required
output_path: /path/to/output           # Required
model_id: model-name                   # Required
base_url: http://127.0.0.1:8082/api/v1 # Optional (default shown)
api_key: health-log-parser              # Optional (default shown)
```

See `docs/pipeline.md` for full configuration options.

## Architecture Overview

**Entity-centric design**: Separates extraction (LLM) from state management (code).

**Pipeline flow**:
```
health.md → Split Sections → Process (parallel) → Extract Facts (LLM) → Build Registry (code) → Output
```

**Output structure**:
```
OUTPUT_PATH/
├─ health_log.md          # PRIMARY: All entries (newest to oldest)
├─ current.yaml           # PRIMARY: Active state for downstream consumers
├─ history.csv            # PRIMARY: Flat event log with entity IDs
├─ entities.json          # PRIMARY: Entity registry (source of truth)
└─ entries/               # INTERMEDIATE (kept for caching)
   ├─ YYYY-MM-DD.raw.md
   ├─ YYYY-MM-DD.processed.md
   ├─ YYYY-MM-DD.extracted.json
   └─ YYYY-MM-DD.labs.md
```

**Detailed documentation**: [`docs/pipeline.md`](docs/pipeline.md)

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | All processing logic (HealthLogProcessor, LLM wrapper) |
| `entity_registry.py` | EntityRegistry class, active/inactive tracking, output generators |
| `config.py` | Environment variable loading and validation |
| `docs/pipeline.md` | Detailed pipeline documentation |
| `prompts/process.system_prompt.md` | Entry processing prompt |
| `prompts/validate.system_prompt.md` | Entry validation prompt |
| `prompts/extract.system_prompt.md` | Fact extraction prompt (JSON output) |

## Do NOT

**Caching**:
- Remove hash-based caching (required - sections re-extracted each run, timestamps useless)
- Replace with timestamp-based caching (will not work correctly)
- Simplify dependency tracking (causes stale cache issues)

**Architecture**:
- Remove entity linking (critical for relating treatments to conditions)
- Move active/inactive logic to LLM prompts (causes state inconsistencies)
- Hardcode medical rules in Python (LLM should apply clinical judgment via prompts)
- Remove parallel processing (essential for large logs with hundreds of entries)

**Over-engineering**:
- Add abstractions, helpers, or utilities for one-time operations
- Add configuration for scenarios that don't exist
- Refactor code that isn't directly related to the task

## Common Tasks

### Modify a Prompt
1. Edit `prompts/<name>.system_prompt.md`
2. Delete output files to force regeneration (or use `--force-reprocess`)
3. Run `uv run python main.py --profile <name>`

### Debug Processing Issues
1. Check `entries/<date>.processed.md` for the processed content
2. Check `entries/<date>.failed.md` if processing failed (contains diagnostics)
3. Check `entries/<date>.extracted.json` for extracted facts
4. Review console warnings for state transition issues

### Debug Entity Registry Issues
1. Check `entities.json` for the complete entity state
2. Check `history.csv` for the event log
3. Common issues (logged as warnings):
   - Stop events on unknown entities (may indicate extraction issue)
   - Missing for_condition references (condition not found)
   - Unknown event types (treated as detail updates)
4. Fix by editing extraction prompt or source entries

### Clean Up Stale Active Entities
1. Run `uv run python main.py --profile <name> --generate-audit`
2. Edit the generated `audit_template.md` - delete entries that are still active
3. Add remaining entries (things to stop/resolve) to your health log
4. Re-run processing to apply changes

### Update Documentation
When modifying the pipeline, update `docs/pipeline.md` to reflect changes.
**IMPORTANT:** Keep README.md up to date with any significant project changes.

## Logs

Output written to:
- `logs/all.log` - All INFO+ messages
- `logs/warnings.log` - WARNING+ messages
- Console - All levels
