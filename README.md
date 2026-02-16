<div align="center">
  <img src="logo.png" alt="health-log-parser" width="512"/>

  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
  [![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

  **📓 Transform health journal entries into structured, validated data 🏥**

  [Documentation](docs/pipeline.md)
</div>

## Overview

health-log-parser is a data extraction and curation tool that transforms unstructured health journal entries into structured, validated data ready for downstream analysis.

**What it produces:**
- **`health_log.md`** — All processed entries (newest to oldest) with labs and exams integrated
- **`current.yaml`** — Active conditions, medications, supplements, and pending TODOs
- **`history.csv`** — Chronological event log with entity IDs linking related events
- **`entities.json`** — Single source of truth for all tracked health entities

The tool processes, validates, and enriches health log entries. Reports, summaries, and recommendations are left to downstream consumers of the structured data.

## Features

- **Parallel processing** of hundreds of journal entries
- **Entity tracking** with active/inactive state, entity reactivation, and relationship linking
- **LLM entity resolution** to deduplicate spelling variants, plurals, and abbreviations
- **Lab result integration** with automatic interpretation
- **Hash-based caching** for efficient incremental rebuilds
- **Multi-model support** via OpenRouter (GPT-4, Claude, etc.)
- **Profile-based configuration** for managing multiple health logs

## Quick Start

```bash
# Install uv (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/tsilva/health-log-parser.git
cd health-log-parser
uv sync

# Configure
cp .env.example .env
# Edit .env with your OPENROUTER_API_KEY

# Create a profile (profiles/myprofile.yaml)
# health_log_path: /path/to/health.md
# output_path: /path/to/output

# Run
uv run python main.py --profile myprofile
```

## Output Structure

```
OUTPUT_PATH/
├── health_log.md            # PRIMARY: All entries (newest to oldest)
├── current.yaml             # PRIMARY: Active state for downstream consumers
├── history.csv              # PRIMARY: Flat event log with entity IDs
├── entities.json            # PRIMARY: Entity registry (source of truth)
├── entity_resolution.json   # INTERMEDIATE: Cached entity name mapping
└── entries/                 # INTERMEDIATE (kept for caching)
    ├── YYYY-MM-DD.raw.md
    ├── YYYY-MM-DD.processed.md
    ├── YYYY-MM-DD.extracted.json
    └── YYYY-MM-DD.labs.md
```

## Configuration

### Environment Variables (.env)

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | Your OpenRouter API key |

### Profile Configuration (profiles/\<name\>.yaml)

| Variable | Required | Description |
|----------|----------|-------------|
| `health_log_path` | Yes | Path to your markdown health log |
| `output_path` | Yes | Directory for generated output |
| `model_id` | No | Default LLM model (default: `gpt-4o-mini`) |
| `max_workers` | No | Parallel processing threads (default: `4`) |

See [docs/pipeline.md](docs/pipeline.md) for all configuration options.

## Health Log Format

Your health log should be a markdown file with dated sections:

```markdown
### 2024-01-15

Visited Dr. Smith for annual checkup. Blood pressure 120/80.
Started vitamin D 2000 IU daily.

### 2024-01-20

Feeling better after starting vitamin D. Energy levels improved.
```

## License

[MIT](LICENSE)
