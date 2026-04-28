<div align="center">
  <img src="https://raw.githubusercontent.com/tsilva/parsehealthlog/main/logo.png" alt="parsehealthlog" width="512"/>

  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
  [![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

  **📓 Transform health journal entries into structured, validated data 🏥**

  [Documentation](docs/pipeline.md)
</div>

## Overview

[![CI](https://github.com/tsilva/parsehealthlog/actions/workflows/release.yml/badge.svg)](https://github.com/tsilva/parsehealthlog/actions/workflows/release.yml)

parsehealthlog is a data extraction and curation tool that transforms unstructured health journal entries into structured, validated data ready for downstream analysis.

**What it produces:**
- **`health_log.md`** — All processed entries (newest to oldest) with per-date `Journal`, `Lab Results`, and `Medical Exams` sections when present

The tool processes, validates, and enriches health log entries. Reports, summaries, and recommendations are left to downstream consumers of the structured data.

## Features

- **Parallel processing** of hundreds of journal entries
- **Structured lab and exam integration** without adding medical interpretation
- **Hash-based caching** for efficient incremental rebuilds
- **Startup validation** that stops on malformed dates or stale extracted journal entries
- **Multi-model support** via OpenRouter (GPT-4, Claude, etc.)
- **Profile-based configuration** for managing multiple health logs

## Quick Start

```bash
# Install uv (if needed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/tsilva/parsehealthlog.git
cd parsehealthlog
uv sync

# Configure
mkdir -p ~/.config/parsehealthlog/profiles

# Create ~/.config/parsehealthlog/profiles/myprofile.yaml
# health_log_path: /path/to/health.md
# output_path: /path/to/output

# Create ~/.config/parsehealthlog/.env
# OPENROUTER_API_KEY=your-key
# MODEL_ID=your-model

# Run
uv run parsehealthlog --profile myprofile
```

## Output Structure

```
OUTPUT_PATH/
├── health_log.md            # PRIMARY: All entries (newest to oldest), sectioned by source
└── entries/                 # INTERMEDIATE (kept for caching)
    ├── YYYY-MM-DD.raw.md
    ├── YYYY-MM-DD.processed.md
    ├── YYYY-MM-DD.labs.md
    └── YYYY-MM-DD.exams.md
```

## Configuration

### Environment Variables (`~/.config/parsehealthlog/.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | Your OpenRouter API key |
| `MODEL_ID` | No | LLM model to use (default: `gpt-4o-mini`) |

### Profile Configuration (`~/.config/parsehealthlog/profiles/<name>.yaml`)

| Variable | Required | Description |
|----------|----------|-------------|
| `health_log_path` | Yes | Path to your markdown health log |
| `output_path` | Yes | Directory for generated output |
| `workers` | No | Parallel processing threads (default: `4`) |
| `base_url` | No | OpenAI-compatible API base URL (default: `https://openrouter.ai/api/v1`) |

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

Date headers may use `### YYYY-MM-DD` or `### YYYY/MM/DD`; slash dates are
normalized internally to `YYYY-MM-DD`. Dates must be real calendar dates, be
unique, and stay in one sequential order, either oldest-to-newest or
newest-to-oldest. The process exits with an error before extraction if the
source log needs date fixes.

## License

[MIT](LICENSE)
