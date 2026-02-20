# CLAUDE.md

## Project Goal

health-log-parser is a **data extraction and curation tool** that transforms health journal entries into structured, validated markdown:

1. **`health_log.md`** - All processed entries (newest to oldest) with labs/exams integrated

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

**Pipeline flow**:
```
health.md → Split Sections → Process (parallel) → Validate → Output
```

**Output structure**:
```
OUTPUT_PATH/
├─ health_log.md          # PRIMARY: All entries (newest to oldest)
└─ entries/               # INTERMEDIATE (kept for caching)
   ├─ YYYY-MM-DD.raw.md
   ├─ YYYY-MM-DD.processed.md
   ├─ YYYY-MM-DD.labs.md
   └─ YYYY-MM-DD.exams.md
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

## Do NOT

**Caching**:
- Remove hash-based caching (required - sections re-extracted each run, timestamps useless)
- Replace with timestamp-based caching (will not work correctly)
- Simplify dependency tracking (causes stale cache issues)

**Architecture**:
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
3. Review console warnings for processing issues

### Update Documentation
When modifying the pipeline, update `docs/pipeline.md` to reflect changes.
**IMPORTANT:** Keep README.md up to date with any significant project changes.

## Logs

Output written to:
- `logs/all.log` - All INFO+ messages
- `logs/warnings.log` - WARNING+ messages
- Console - All levels
