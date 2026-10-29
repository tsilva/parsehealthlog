<p align="center">
  <img src="./logo.png" alt="parsehealthlog" width="512" />
  <br />
  <!-- repo-tagline:start -->
  <strong>📓 Transform health journal entries into structured, validated data 🏥</strong>
  <!-- repo-tagline:end -->
</p>

[GitHub](https://github.com/tsilva/parsehealthlog) · [Pipeline docs](docs/pipeline.md)

parsehealthlog is a Python CLI for turning a date-sectioned markdown health journal into structured markdown. It reads journal entries, optional lab CSVs, and optional medical exam summaries, then uses an OpenAI-compatible LLM endpoint to process and validate each date independently.

The primary output is `health_log.md`: one collated log, newest to oldest, with `Journal`, `Lab Results`, and `Medical Exams` sections when those sources are present.

## Install

Requires Python 3.10 or newer.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/tsilva/parsehealthlog.git
cd parsehealthlog
uv sync

mkdir -p ~/.config/parsehealthlog/profiles

cat > ~/.config/parsehealthlog/.env <<'ENV'
AGENTBRIDGE_API_KEY=your-key
MODEL_ID=google/gemini-3.7-flash
ENV

cat > ~/.config/parsehealthlog/profiles/myprofile.yaml <<'YAML'
health_log_path: /path/to/health.md
output_path: /path/to/output
base_url: http://127.0.0.1:8082/api/v1
workers: 4
YAML

uv run parsehealthlog --profile myprofile
```

Open `/path/to/output/health_log.md`.

## Local profile shortcuts

To browse external profiles from this checkout, create the following local
shortcuts after configuring the runtime profiles:

```bash
mkdir -p .config
ln -s "$HOME/.config/parsehealthlog/profiles" .config/profiles
```

`.config/profiles` is a directory symlink to
`~/.config/parsehealthlog/profiles/`. New profiles in that external directory appear
through the shortcut automatically. Keep that external directory limited to
profile files; do not put credentials or other runtime state there.

The entire repo `.config/` directory is Git-ignored. Actual profile files stay
outside the checkout; editing through a shortcut edits the original, and the
link is not a read-only view or a backup. Git cleanup can remove the local
shortcut without deleting its external target. Recreate shortcuts after cloning
or cleanup, and back up the external profiles independently. Shared `.env`
files, credential stores, sessions, databases, caches, logs and parser outputs
remain outside these shortcuts. Runtime discovery paths and CLI behavior are
unchanged.

## Commands

```bash
uv run parsehealthlog --profile myprofile          # process one profile
uv run parsehealthlog --profile myprofile --dry-run # preview planned changes
uv run parsehealthlog --profile myprofile --force-reprocess # ignore cached outputs
uv run parsehealthlog --list-profiles              # list configured profiles
uv run pytest                                      # run tests
```

## Notes

- Source entries use `### YYYY-MM-DD` or `### YYYY/MM/DD` headings. Dates must be real; repeated dates are merged in source order and the final timeline is sorted newest first.
- Runtime config lives in `~/.config/parsehealthlog/.env`; profiles live in `~/.config/parsehealthlog/profiles/<name>.yaml`.
- `AGENTBRIDGE_API_KEY` is optional gateway authentication. `MODEL_ID` defaults to `google/gemini-3.7-flash`, and `base_url` defaults to `http://127.0.0.1:8082/api/v1`.
- Optional profile fields include `labs_parser_output_path`, `medical_exams_parser_output_path`, and `workers`.
- Output is written under `output_path`, with cached per-date artifacts in `output_path/entries/`.
- Caching is hash-based through `DEPS` comments; use `--force-reprocess` after prompt or source changes when you need a full rebuild.
- Logs are written to `logs/all.log` and `logs/warnings.log`.

## Architecture

![parsehealthlog architecture diagram](./architecture.png)

## AgentBridge routing

Model requests use only AgentBridge. Set `AGENTBRIDGE_BASE_URL` to its API root
(default `http://127.0.0.1:8082/api/v1`). `AGENTBRIDGE_API_KEY` is an optional
gateway token. OpenRouter credentials belong to AgentBridge. Existing model
choices are retained; raw upstream IDs gain `openrouter/` only on outbound
requests. There is no direct OpenRouter endpoint or fallback. Hosted deployments
must configure a reachable AgentBridge URL instead of the loopback default.

Health-log processing and validation explicitly request `reasoning_effort="medium"`
for `codex/...` models, overriding AgentBridge's model default. For example,
`MODEL_ID=codex/gpt-6.1-sol` uses medium reasoning for new or regenerated entries.
Completed entries whose dependencies match are still reused; changing reasoning
effort alone does not invalidate their cache.

## Source fidelity

Repeated same-day sections are merged without losing content; source dates may be entered out of order, while the final log remains newest-first. Invalid date formats are still rejected. Lab CSVs are normalized independently before merging, and canonical values, units and reference ranges come from one schema. Detection-limit comparators remain attached to values. Changes only to lab/exam inserts reuse the journal text already validated against identical raw text and prompts. Orphaned lab/exam sidecars are removed when those dates disappear from their corresponding source.

The unified timeline retains an attributed verbatim copy of every journal body, in its original language, alongside successful English curation. If curation fails validation, the timeline keeps the source copy and evidence inserts; `.failed.md` records the curation failure and the run reports errors. Embedded clinical interpretations and historical dates must survive curation.

Exam summaries use their explicit `exam_date`; the directory date is a legacy fallback only. Explicitly undated summaries remain available in the exam corpus and are not assigned a journal date.

## License

[MIT](LICENSE)
