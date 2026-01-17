<div align="center">
  <img src="logo.png" alt="health-log-parser" width="400"/>

  [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
  [![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

  **Transform messy health journals into expert-quality medical reports with clinical reasoning**

  [Documentation](docs/pipeline.md)
</div>

## Overview

health-log-parser uses LLMs to transform unstructured health journal entries into structured, clinician-quality reports. It builds a chronological timeline of health events, links related episodes (e.g., medications to the conditions they treat), and generates actionable summaries, recommendations, and follow-up questions.

**Key capabilities:**
- Parallel processing of hundreds of journal entries
- Episode linking to track treatments, conditions, and experiments over time
- Lab result integration with automatic interpretation
- Multi-model support via OpenRouter (GPT-4, Claude, etc.)

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
# Edit .env with your OPENROUTER_API_KEY, HEALTH_LOG_PATH, and OUTPUT_PATH

# Run
uv run python main.py
```

## How It Works

```
health.md ─→ Split Sections ─→ Process (parallel) ─→ Build Timeline ─→ Generate Reports ─→ output.md
```

The pipeline:
1. **Splits** your markdown health log into dated sections
2. **Processes** each section in parallel using LLMs
3. **Builds** a chronological timeline with episode IDs linking related events
4. **Generates** reports: summary, recommendations, experiments, follow-up questions

## Output Structure

```
OUTPUT_PATH/
├── entries/
│   ├── 2024-01-15.raw.md        # Original section
│   ├── 2024-01-15.processed.md  # Structured output
│   └── 2024-01-15.labs.md       # Lab results
├── health_timeline.csv          # Chronological events with episode IDs
└── reports/
    └── output.md                # Final compiled report
```

## Configuration

Create a `.env` file with:

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | Your OpenRouter API key |
| `HEALTH_LOG_PATH` | Yes | Path to your markdown health log |
| `OUTPUT_PATH` | Yes | Directory for generated output |
| `MODEL_ID` | No | Default LLM model (default: `gpt-4o-mini`) |
| `MAX_WORKERS` | No | Parallel processing threads (default: `4`) |

See [docs/pipeline.md](docs/pipeline.md) for all configuration options and model overrides per task.

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
