# CLAUDE.md

## Project Goal

health-log-parser transforms health journal entries into **expert-quality medical reports**. Success means:

1. **Reports match what a clinician would write** - Accurate, concise, actionable
2. **Implicit state changes are inferred** - If "flu ended" is logged, paracetamol (taken for flu) should be considered stopped unless explicitly continued
3. **Stale items are identified** - Treatments/conditions without recent updates should be flagged for follow-up

The core challenge is **medical inference**: the system must apply clinical reasoning to determine what's active vs. resolved based on context, not just explicit statements.

## Known Gap: Implicit State Inference

**Current problem**: Reports sometimes treat items as "active" when context suggests they've stopped.

**Example**: Patient logs "flu ended" but paracetamol (started for flu) still appears as active medication because no explicit "stopped paracetamol" entry exists.

**Root cause**: Unknown - needs diagnosis. Possible locations:
- `update_timeline.system_prompt.md` - May not add "stopped" events for implicit endings
- Report prompts - May not apply inference when reading timeline CSV
- Episode linking - May not properly connect treatments to conditions

**To diagnose**: Trace an example through the pipeline:
1. Check `health_timeline.csv` for missing "stopped" events
2. Check if prompts instruct LLM to infer implicit state changes
3. Compare report output against expected clinical reasoning

## Quick Start

```bash
# Setup
curl -LsSf https://astral.sh/uv/install.sh | sh  # Install uv if needed
uv sync                                           # Install dependencies

# Run
uv run python main.py

# Configuration (.env file)
OPENROUTER_API_KEY=...     # Required
HEALTH_LOG_PATH=...        # Required: Path to markdown health log
OUTPUT_PATH=...            # Required: Base directory for output
```

See `docs/pipeline.md` for full configuration options.

## Architecture Overview

**Monolithic design**: All logic in `main.py` (`HealthLogProcessor` class).

**Pipeline flow**:
```
health.md → Split Sections → Process (parallel) → Build Timeline → Generate Reports → output.md
```

**Key outputs**:
```
OUTPUT_PATH/
├─ entries/*.processed.md    # Validated section outputs
├─ health_timeline.csv       # Chronological events with episode IDs
└─ reports/
    ├─ output.md             # THE ONLY USER-FACING FILE
    └─ .internal/            # Cached intermediates
```

**Detailed documentation**: [`docs/pipeline.md`](docs/pipeline.md) - Flow diagrams, step-by-step explanations, data formats.

## Key Decision Points for Inference

These are where medical reasoning happens - focus here when diagnosing inference issues:

| File | Role | Inference Responsibility |
|------|------|-------------------------|
| `prompts/update_timeline.system_prompt.md` | Build timeline CSV | Should add "stopped" events when treatments become obsolete |
| `prompts/targeted_questions.system_prompt.md` | Generate questions | Should recognize items as inactive based on context |
| `prompts/next_steps_unified.system_prompt.md` | Recommendations | Should not recommend continuing stopped treatments |
| `prompts/experiments.system_prompt.md` | Track experiments | Should mark experiments as ended when outcomes clear |

**Episode IDs** in timeline link related events (e.g., medication ep-003 treats condition ep-002). This linking is critical for inference - if ep-002 resolves, ep-003 should be considered for stopping.

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
2. Delete `reports/output.md` (or `.internal/` files) to force regeneration
3. Run `uv run python main.py`

### Add a New Report
```python
self._generate_file(
    "report_name.md",
    "prompt_name.system_prompt",
    role="questions",  # Model config: process|validate|summary|questions|next_steps|status
    temperature=0.0,
    extra_messages=[{"role": "user", "content": content}],
    hidden=True,  # True = .internal/, False = reports/
)
```

### Debug Inference Issues
1. Find a specific example where inference fails
2. Check `health_timeline.csv` - is there a "stopped" event? Is episode linking correct?
3. Check relevant prompt - does it instruct LLM to infer implicit state changes?
4. Trace through: journal entry → timeline row → report output

### Update Documentation
When modifying the pipeline, update `docs/pipeline.md` to reflect changes.

## File Reference

| File | Purpose |
|------|---------|
| `main.py` | All processing logic (HealthLogProcessor, LLM wrapper) |
| `config.py` | Environment variable loading and validation |
| `docs/pipeline.md` | Detailed pipeline documentation |
| `prompts/*.md` | LLM prompts (see Key Decision Points above) |

## Logs

Output written to:
- `logs/all.log` - All INFO+ messages
- `logs/warnings.log` - WARNING+ messages
- Console - All levels
