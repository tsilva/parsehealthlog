# Code Improvements for health-log-parser

Generated: 2025-11-06

## Code Architecture & Structure

### 1. Break up the monolithic `main.py` (577 lines)

**Issue:** The single-file architecture is becoming difficult to maintain.

**Recommendation:** Split into modular structure:
```
health_log_parser/
├── __init__.py
├── main.py              # CLI entry point only
├── processor.py         # HealthLogProcessor class
├── llm.py              # LLM wrapper and API logic
├── prompts.py          # Prompt loading utilities
├── labs.py             # Lab data handling (format_labs, loading)
├── utils.py            # Hashing, date extraction
└── config.py           # Configuration management
```

### 2. Add configuration management

**Issue:** Environment variables scattered throughout code.

**Recommendation:** Create a `Config` dataclass:
```python
@dataclass
class Config:
    openrouter_api_key: str
    model_id: str
    health_log_path: Path
    output_path: Path
    max_workers: int = 4
    questions_runs: int = 3
    # ...

    @classmethod
    def from_env(cls) -> Config:
        # Load and validate all env vars in one place
```

### 3. Type hints are incomplete

**Issue:** Missing return type annotations in some places.

**Location:**
- `main.py:363` - `extra_messages` parameter type is complex
- Various functions missing return types

**Recommendation:**
- Complete all type annotations
- Consider using `typing.Protocol` for better LLM interface definition

## Error Handling & Robustness

### 4. Add retry logic for API calls

**Issue:** The LLM wrapper has no retry mechanism for transient failures.

**Location:** `main.py:182`

**Recommendation:**
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def __call__(self, messages, *, max_tokens=2048, temperature=0.0):
    # existing code
```

### 5. Better error context in validation loop

**Issue:** When validation fails 3 times, the error message is logged but the original section isn't saved for debugging.

**Location:** `main.py:426-462`

**Recommendation:**
```python
if attempt == 3:
    error_path = self.entries_dir / f"{date}.failed.md"
    error_path.write_text(
        f"VALIDATION ERRORS:\n{validation}\n\nPROCESSED:\n{processed}",
        encoding="utf-8"
    )
```

### 6. Handle missing prompt files gracefully

**Issue:** Currently raises `FileNotFoundError` when prompt is missing.

**Location:** `main.py:114-118`

**Recommendation:** Validate all required prompts at startup:
```python
def validate_prompts(self):
    required = [
        "process.system_prompt",
        "validate.system_prompt",
        "summary.system_prompt",
        "questions.system_prompt",
        # ... all required prompts
    ]
    missing = [p for p in required if not (PROMPTS_DIR / f"{p}.md").exists()]
    if missing:
        raise ValueError(f"Missing prompts: {missing}")
```

## Testing

### 7. Add comprehensive tests

**Issue:** Currently no tests exist.

**Recommendation:** Create test suite:
- `tests/test_utils.py` - Test `extract_date`, `format_labs`, `short_hash`
- `tests/test_processor.py` - Test section splitting, lab loading
- `tests/test_llm.py` - Mock LLM calls, test retry logic
- `tests/fixtures/` - Sample health logs and expected outputs

### 8. Add integration test with mock LLM

**Recommendation:**
```python
def test_end_to_end_processing(tmp_path, mock_llm):
    # Given a sample health log
    # When processor runs
    # Then verify output structure and content
```

## Performance & Scalability

### 9. Optimize caching strategy

**Issue:** Current cache only checks if hash matches first line. Manual edits to processed files aren't detected properly.

**Location:** `main.py:256`

**Recommendation:**
- Use separate `.cache` file instead of embedding hash in processed file
- Track both raw and processed hashes to detect manual edits
- Add cache versioning for prompt changes

### 10. Add progress indicators for report generation

**Issue:** The specialist next steps generation (14 specialties) has no progress indicator.

**Location:** `main.py:304-317`

**Recommendation:**
```python
with tqdm(total=len(SPECIALTIES), desc="Specialist analysis") as bar:
    for spec in SPECIALTIES:
        # ... existing code
        bar.update(1)
```

### 11. Parallelize specialist report generation

**Issue:** Specialist reports are generated sequentially but are independent.

**Location:** `main.py:304-317`

**Recommendation:**
```python
with ThreadPoolExecutor(max_workers=max_workers) as ex:
    futures = {
        ex.submit(self._generate_specialist_report, spec, final_markdown): spec
        for spec in SPECIALTIES
    }
    for fut in as_completed(futures):
        spec_outputs.append(fut.result())
```

## Code Quality

### 12. Remove debug print statements

**Issue:** Debug prints should be replaced with proper logging.

**Location:** `main.py:205, 207`

**Recommendation:** Replace with:
```python
self.logger.debug("OUTPUT_PATH: %s", os.getenv("OUTPUT_PATH"))
self.logger.debug("Output base path: %s", output_base)
```

### 13. Fix magic strings

**Issue:** Magic strings scattered throughout code.

**Locations:**
- `"$OK$"` at `main.py:452` should be a constant
- File extensions repeated throughout (`.md`, `.csv`)

**Recommendation:**
```python
VALIDATION_SUCCESS_MARKER: Final = "$OK$"
MD_EXTENSION: Final = ".md"
CSV_EXTENSION: Final = ".csv"
```

### 14. Simplify complex expressions

**Issue:** Crude check for raw prompt vs name.

**Location:** `main.py:376`

**Current:**
```python
system_prompt = (
    system_prompt_or_name
    if "\n" in system_prompt_or_name  # crude check
    else self._prompt(system_prompt_or_name)
)
```

**Recommendation:**
```python
def _get_system_prompt(self, prompt_or_name: str) -> str:
    if (PROMPTS_DIR / f"{prompt_or_name}.md").exists():
        return self._prompt(prompt_or_name)
    return prompt_or_name  # Treat as raw content
```

### 15. Fix inconsistent exception handling

**Issue:** Catching too broad an exception.

**Location:** `main.py:163`

**Current:**
```python
except Exception:  # noqa: BLE001
    pass
```

**Recommendation:**
```python
except (ValueError, TypeError) as e:
    self.logger.warning(f"Could not parse range for {name}: {e}")
```

## Security & Privacy

### 16. Sanitize file paths

**Issue:** `extract_date` returns user-controlled dates that become filenames without validation.

**Risk:** Potential path traversal if dates contain unexpected characters.

**Recommendation:**
```python
def sanitize_filename(date: str) -> str:
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        raise ValueError(f"Invalid date format: {date}")
    return date
```

### 17. Add rate limiting for API calls

**Issue:** No protection against accidental high API costs.

**Recommendation:**
```python
from ratelimit import limits, sleep_and_retry

@sleep_and_retry
@limits(calls=100, period=60)  # 100 calls per minute
def __call__(self, messages, ...):
    # existing code
```

### 18. Sensitive data in logs

**Issue:** Ensure health data isn't inadvertently logged.

**Recommendation:** Audit all `logger` calls to ensure no PII/PHI is logged.

## Configuration & Documentation

### 19. Add JSON schema validation for config

**Recommendation:** Consider using `pydantic` for environment variable validation with better error messages:
```python
from pydantic import BaseSettings, Field

class Settings(BaseSettings):
    openrouter_api_key: str = Field(..., env="OPENROUTER_API_KEY")
    model_id: str = Field(default="gpt-4o-mini", env="MODEL_ID")
    health_log_path: Path = Field(..., env="HEALTH_LOG_PATH")

    class Config:
        env_file = '.env'
```

### 20. Missing docstrings

**Issue:** Many functions lack docstrings.

**Functions needing documentation:**
- `_generate_file` (`main.py:353`) - complex function needs documentation
- `_assemble_output` (`main.py:531`)
- `format_labs` (`main.py:136`)

### 21. Add examples in prompts directory

**Recommendation:** Create `prompts/README.md` explaining:
- The prompt system architecture
- How to customize prompts
- Variables available in each prompt
- Best practices for prompt engineering

## Maintenance & Developer Experience

### 22. Add pre-commit hooks

**Recommendation:** Create `.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.1.0
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.7.0
    hooks:
      - id: mypy
```

### 23. Add GitHub Actions CI

**Recommendation:** Create `.github/workflows/ci.yml`:
```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: |
          pip install uv
          uv pip install -e '.[dev]'
      - name: Run tests
        run: pytest
      - name: Run linter
        run: ruff check .
      - name: Run type checker
        run: mypy .
```

### 24. Version the output format

**Issue:** When output structure changes, old outputs may be incompatible.

**Recommendation:** Add version to output:
```markdown
<!-- Generated by health-log-parser v0.1.0 -->
```

### 25. Add `--dry-run` mode

**Recommendation:** Allow users to preview what would be processed without making API calls:
```python
import argparse

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true',
                       help='Show what would be processed without making API calls')
    parser.add_argument('--log-path', help='Override HEALTH_LOG_PATH env var')
    args = parser.parse_args()

    # ... use args.dry_run
```

## Priority Matrix

### Priority 1 (Critical - Do First)

1. **Remove debug print statements** (#12)
   - Easy fix, improves code quality immediately

2. **Add API retry logic** (#4)
   - Prevents failures from transient network issues

3. **Add validation for all required prompts at startup** (#6)
   - Fail fast instead of mid-processing

4. **Sanitize filenames from user input** (#16)
   - Security issue

### Priority 2 (High - Next Sprint)

5. **Split into multiple modules** (#1)
   - Improves maintainability significantly

6. **Add basic unit tests** (#7)
   - Prevents regressions

7. **Add config management** (#2)
   - Makes configuration more robust

8. **Fix broad exception handling** (#15)
   - Improves debugging

### Priority 3 (Medium - Future)

9. **Parallelize specialist report generation** (#11)
   - Significant performance improvement

10. **Add progress indicators** (#10)
    - Better UX

11. **Optimize caching strategy** (#9)
    - Prevents cache invalidation issues

12. **Add pre-commit hooks** (#22)
    - Improves code quality

### Priority 4 (Low - Nice to Have)

13. **Add `--dry-run` mode** (#25)
14. **Version output format** (#24)
15. **Add CI/CD** (#23)
16. **Rate limiting** (#17)

## Estimated Effort

- **Quick wins (< 1 hour):** #12, #13, #15, #16
- **Small (1-4 hours):** #4, #6, #10, #20, #22
- **Medium (1-2 days):** #2, #7, #11, #19, #25
- **Large (3-5 days):** #1, #7, #8, #23
- **Ongoing:** #18 (audit), #21 (documentation)

## Notes

- All line numbers reference the current `main.py` (577 lines)
- Some improvements are interdependent (e.g., #1 and #2 work well together)
- Consider tackling this incrementally to avoid breaking existing functionality
- Always add tests when implementing improvements (#7)
