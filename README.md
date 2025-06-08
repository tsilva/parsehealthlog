# 🩺 health-log-parser

<p align="center">
  <img src="logo.png" alt="Logo" width="400"/>
</p>

🔹 **AI-powered tool for structuring and auditing personal health logs**  

## 📖 Overview

health-log-parser transforms unstructured or semi-structured health journal entries into clean, standardized Markdown summaries. It uses an LLM (via OpenRouter) to extract and format details about medical visits, medications, and symptoms. The tool also audits the structured output, reporting any clinical data missing from the conversion.

This project is ideal for patients, caregivers, or clinicians who want to organize health notes and ensure no important information is lost in the process.

## 🚀 Installation

```bash
pipx install . --force
```

To work in a local development environment you can use the helper scripts
provided in this repo:

- **Linux/macOS:** `./activate-env.sh`
- **Windows (PowerShell):** `./activate-env.ps1`

Running either script will create a `.venv` directory managed by `uv` if one
does not already exist and install packages from `requirements.txt`.

 - Requires Python 3.8+
 - Set up your `.env` file (see `.env.example`) with your OpenRouter API key, preferred model, and health log path

## 🛠️ Usage

**Basic usage:**

```bash
python main.py
```

- Input: A Markdown file with health log entries (can be unstructured)
- Output:
  - `OUTPUT_PATH/<LOG>/entries/<DATE>.raw.md` — original section text
  - `OUTPUT_PATH/<LOG>/entries/<DATE>.processed.md` — validated LLM output
  - `OUTPUT_PATH/<LOG>/entries/<DATE>.labs.md` — structured lab results for each date
  - `OUTPUT_PATH/<LOG>/intro.md` — any pre-dated content
  - `OUTPUT_PATH/<LOG>/reports/summary.md` — short patient summary
  - `OUTPUT_PATH/<LOG>/reports/clarifying_questions_<N>.md` — raw questions from each run
  - `OUTPUT_PATH/<LOG>/reports/clarifying_questions.md` — clarifying questions merged from multiple runs
  - `OUTPUT_PATH/<LOG>/reports/next_steps_<SPECIALTY>.md` — recommended actions from each specialist
  - `OUTPUT_PATH/<LOG>/reports/next_steps.md` — consensus recommendations
  - `OUTPUT_PATH/<LOG>/reports/output.md` — patient summary followed by the curated log
  - `OUTPUT_PATH/<LOG>/reports/clinical_data_missing_report.md` — report of any clinical data missing from the structured output
  - `logs/error.log` — errors captured during processing
  - Logs are also echoed to the console

**Environment variables (`.env`):**

- `OPENROUTER_API_KEY` — your OpenRouter API key
- `MODEL_ID` — default LLM model (e.g., `openai/gpt-4.1`)
- `PROCESS_MODEL_ID` — model for transforming raw sections
- `VALIDATE_MODEL_ID` — model for validating processed sections
- `QUESTIONS_MODEL_ID` — model for generating clarifying questions
- `QUESTIONS_RUNS` — how many times to generate clarifying questions (default: 3)
- `SUMMARY_MODEL_ID` — model for creating summaries
- `NEXT_STEPS_MODEL_ID` — model for recommended next steps
- `HEALTH_LOG_PATH` — path to the markdown health log
- `LABS_PARSER_OUTPUT_PATH` — path to aggregated lab CSVs
- `MAX_WORKERS` — number of parallel processing threads (default: 1)
- `OUTPUT_PATH` — base directory for generated output (default: `output`)

**Example workflow:**

1. Prepare your `.env` file with API credentials and the path to your health log.
2. Run the tool:
   ```bash
   python main.py
   ```
3. Review the structured log and reports in the `OUTPUT_PATH/<LOG>/` directory (defaults to `output/<LOG>/`).

## 📄 License

This project is licensed under the [MIT License](LICENSE).