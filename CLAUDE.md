# CLAUDE.md

## Project Goal

health-log-parser is a **data extraction and curation tool** that transforms health journal entries into structured, validated data:

1. **`health_log.md`** - All processed entries (newest to oldest) with labs/exams integrated
2. **`health_log.csv`** - Chronological timeline with episode IDs linking related events

The tool processes, validates, and enriches health log entries but does **not** generate reports, summaries, or recommendations. Those are left to downstream consumers of the structured data.

## Quick Start

```bash
# Setup
curl -LsSf https://astral.sh/uv/install.sh | sh  # Install uv if needed
uv sync                                           # Install dependencies

# Run
uv run python main.py --profile <profile_name>

# Configuration (.env file)
OPENROUTER_API_KEY=...     # Required

# Profile configuration (profiles/<name>.yaml)
health_log_path: /path/to/health.md    # Required
output_path: /path/to/output           # Required
```

See `docs/pipeline.md` for full configuration options.

## Architecture Overview

**Monolithic design**: All logic in `main.py` (`HealthLogProcessor` class).

**Pipeline flow**:
```
health.md → Split Sections → Process (parallel) → Build Timeline → Output
```

**Output structure**:
```
OUTPUT_PATH/
├─ health_log.md          # PRIMARY: All entries (newest to oldest)
├─ health_log.csv         # PRIMARY: Timeline with episode IDs
├─ entries/               # INTERMEDIATE (kept for caching)
│  ├─ YYYY-MM-DD.raw.md
│  ├─ YYYY-MM-DD.processed.md
│  └─ YYYY-MM-DD.labs.md
└─ intro.md               # Pre-dated content (if exists)
```

**Detailed documentation**: [`docs/pipeline.md`](docs/pipeline.md)

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | All processing logic (HealthLogProcessor, LLM wrapper) |
| `config.py` | Environment variable loading and validation |
| `docs/pipeline.md` | Detailed pipeline documentation |
| `prompts/process.system_prompt.md` | Entry processing prompt |
| `prompts/validate.system_prompt.md` | Entry validation prompt |
| `prompts/update_timeline.system_prompt.md` | Timeline building prompt |

## Do NOT

**Caching**:
- Remove hash-based caching (required - sections re-extracted each run, timestamps useless)
- Replace with timestamp-based caching (will not work correctly)
- Simplify dependency tracking (causes stale cache issues)

**Architecture**:
- Remove episode linking in timeline (critical for relating treatments to conditions)
- Simplify timeline to "active items only" (loses context needed for inference)
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
3. Check `health_log.csv` for timeline entries

### Update Documentation
When modifying the pipeline, update `docs/pipeline.md` to reflect changes.

## Logs

Output written to:
- `logs/all.log` - All INFO+ messages
- `logs/warnings.log` - WARNING+ messages
- Console - All levels
