# Health Log Parser: Issues & Improvement Plan

## Implementation Status

| Phase | Status | Description |
|-------|--------|-------------|
| Phase 1 | **COMPLETE** | Critical: ThreadPoolExecutor exceptions, CSV errors, empty sections, test suite |
| Phase 2 | **COMPLETE** | High: Config validation, diagnostic output for validation failures |
| Phase 3 | **COMPLETE** | Medium: Hash length (12 chars), API retry with tenacity, error logging, prompt cache fix |
| Phase 4 | **COMPLETE** | Low: Logging isolation (named logger), optional path validation |
| Phase 5 | Pending | Architectural: Extended test suite, error types, progress persistence |

---

## Executive Summary

Deep analysis of the health-log-parser codebase revealed **24 distinct issues** across categories: concurrency bugs, error handling gaps, missing validation, zero test coverage, and architectural concerns. The issues range from critical (race conditions, silent failures) to low-priority (logging improvements).

---

## Critical Issues (Must Fix)

### 1. Race Condition in Parallel Processing
**Location:** `main.py:336-345, 652-702`

Multiple threads write to files without synchronization:
```python
futures = {ex.submit(self._process_section, sec): sec for sec in to_process}
```
Inside `_process_section()`:
- Line 693: `processed_path.write_text()` - no locking
- Line 698: `lab_path.write_text()` - no locking

**Risk:** Data corruption if two threads process same date (edge case) or file system race conditions.

**Fix:** Add file locking with `fcntl.flock()` or use a dedicated write queue.

---

### 2. Silent Failures in ThreadPoolExecutor
**Location:** `main.py:339-345`

```python
for fut in as_completed(futures):
    date, ok = fut.result()  # Can raise - not caught!
```

If `_process_section()` raises an exception (API timeout, file error), the entire program crashes without logging.

**Fix:** Wrap in try-except:
```python
try:
    date, ok = fut.result()
except Exception as e:
    self.logger.error("Thread exception: %s", e)
    failed.append("(exception)")
```

---

### 3. No Test Coverage
**Finding:** Zero test files in repository.

Critical untested functions:
- `extract_date()` - regex-based date parsing
- `format_labs()` - lab data formatting with status indicators
- `parse_deps_comment()` - dependency hash parsing
- `_process_section()` - 3-retry validation loop
- `_load_labs()` - CSV parsing with column mappings
- `Config.from_env()` - environment variable loading

**Fix:** Add pytest test suite covering at minimum:
- Date extraction edge cases (empty sections, malformed dates)
- Lab formatting (boolean values, numeric ranges, missing data)
- Dependency comment parsing
- Configuration validation

---

### 4. CSV Parsing Has No Error Handling
**Location:** `main.py:742, 748`

```python
lab_dfs.append(pd.read_csv(csv_local))   # No try-except
lab_dfs.append(pd.read_csv(agg_csv))     # No try-except
```

Malformed CSVs crash the application.

**Fix:** Wrap in try-except with informative error message:
```python
try:
    lab_dfs.append(pd.read_csv(csv_local))
except pd.errors.ParserError as e:
    self.logger.error("Failed to parse %s: %s", csv_local, e)
```

---

## High Priority Issues

### 5. Configuration Accepts Invalid Values
**Location:** `config.py:84-85`

```python
max_workers = int(os.getenv("MAX_WORKERS", "4")) or 1
questions_runs = int(os.getenv("QUESTIONS_RUNS", "3"))
```

**Problems:**
- `MAX_WORKERS=-5` crashes ThreadPoolExecutor
- `QUESTIONS_RUNS=0` causes empty loops
- Non-integer values (`MAX_WORKERS=abc`) raise uncaught ValueError

**Fix:** Add validation:
```python
max_workers = max(1, min(int(os.getenv("MAX_WORKERS", "4")), os.cpu_count() or 8))
questions_runs = max(1, int(os.getenv("QUESTIONS_RUNS", "3")))
```

---

### 6. Memory Issues with Large Lab CSVs
**Location:** `main.py:737-754`

```python
lab_dfs.append(pd.read_csv(csv_local))   # Full file in memory
lab_dfs.append(pd.read_csv(agg_csv))     # Another full file
labs_df = pd.concat(lab_dfs)             # Third copy in memory
```

For million-row CSVs, this triples memory usage.

**Fix:** Use chunked reading or filter early:
```python
for chunk in pd.read_csv(csv_local, chunksize=10000):
    # Process each chunk
```

---

### 7. Validation Failures Leave No Diagnostic Trail
**Location:** `main.py:664-702`

After 3 failed validation attempts, no diagnostic info is saved:
```python
return date, False  # What went wrong? No record.
```

**Fix:** Save failed outputs to `.failed.md` files for debugging:
```python
if attempt == 3:
    failed_path = self.entries_dir / f"{date}.failed.md"
    failed_path.write_text(f"Raw:\n{section}\n\nProcessed:\n{processed}")
```

---

### 8. IndexError Risk in Date Extraction
**Location:** `main.py:172`

```python
header = section.strip().splitlines()[0]  # IndexError if empty!
```

Empty or whitespace-only sections crash the parser.

**Fix:** Add bounds check:
```python
lines = section.strip().splitlines()
if not lines:
    raise ValueError(f"Empty section cannot be parsed")
header = lines[0]
```

---

## Medium Priority Issues

### 9. Hash Collision Risk (8-char hash)
**Location:** `main.py:125-126`

```python
return sha256(text.encode()).hexdigest()[:8]  # Only 32 bits
```

With thousands of entries, birthday paradox applies. Collision probability:
- 1000 items: ~0.01%
- 10000 items: ~1.2%

**Fix:** Use 12+ characters (48 bits minimum):
```python
return sha256(text.encode()).hexdigest()[:12]
```

---

### 10. Silent Date Coercion Drops Lab Rows
**Location:** `main.py:755`

```python
labs_df["date"] = pd.to_datetime(labs_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
```

Invalid dates become NaT → empty string, silently orphaning those lab rows.

**Fix:** Log coercion failures:
```python
coerced = pd.to_datetime(labs_df["date"], errors="coerce")
bad_dates = labs_df[coerced.isna() & labs_df["date"].notna()]
if len(bad_dates) > 0:
    self.logger.warning("Dropped %d rows with invalid dates", len(bad_dates))
```

---

### 11. Missing Column Validation in Lab Data
**Location:** `main.py:785`

```python
labs_df = labs_df[[c for c in keep_cols if c in labs_df.columns]]
```

Silently continues if critical columns (`date`, `lab_name_standardized`) are missing.

**Fix:** Validate required columns exist:
```python
required = ["date", "lab_name_standardized"]
missing = [c for c in required if c not in labs_df.columns]
if missing:
    raise ValueError(f"Lab CSV missing required columns: {missing}")
```

---

### 12. No API Timeout or Retry Logic
**Location:** `main.py:267-275`

```python
self.client = OpenAI(base_url="https://openrouter.ai/api/v1", ...)
```

Uses default 300s timeout. Transient network failures crash processing.

**Fix:** Add retry with exponential backoff:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=60))
def __call__(self, messages, ...):
    ...
```

---

### 13. Bare Exception Swallows Errors
**Location:** `main.py:208`

```python
except Exception:  # noqa: BLE001
    pass
```

Silently ignores errors during lab value formatting. No way to know what failed.

**Fix:** Log the error:
```python
except Exception as e:
    self.logger.debug("Lab value format error for %s: %s", lab_name, e)
```

---

### 14. Prompt Cache vs File Hash Inconsistency
**Location:** `main.py:454-457, 472`

```python
def _prompt(self, name: str) -> str:
    if name not in self.prompts:
        self.prompts[name] = load_prompt(name)  # Cached at runtime
    return self.prompts[name]
```

But `_hash_prompt()` reads file directly. If prompt modified mid-run, cache and hash diverge.

**Fix:** Use hash to invalidate cache:
```python
def _prompt(self, name: str) -> str:
    current_hash = hash_file(PROMPTS_DIR / name)
    if name not in self.prompts or self._prompt_hashes.get(name) != current_hash:
        self.prompts[name] = load_prompt(name)
        self._prompt_hashes[name] = current_hash
    return self.prompts[name]
```

---

## Low Priority Issues

### 15. Debug Print Statements in Production
**Location:** `main.py:251-253`

```python
print(config.output_path)
print(f"Output base path: {output_base}")
```

Should use logging instead.

---

### 16. Logging Replaces All Root Handlers
**Location:** `main.py:87`

```python
root.handlers = [out_hdlr, err_hdlr]
```

May interfere with handlers from imported libraries.

**Fix:** Append instead of replace, or use a dedicated logger.

---

### 17. Optional Path Validation Missing
**Location:** `main.py:745-746`

`labs_parser_output_path` used without checking it's a valid directory.

---

### 18. Empty File IndexError
**Location:** `main.py:513`

```python
first_line = path.read_text().splitlines()[0] if path.exists() else ""
```

IndexError if file exists but is empty.

---

## Architectural Improvements

### A. Add Comprehensive Test Suite
Create `tests/` directory with:
- `test_date_extraction.py` - edge cases for date parsing
- `test_lab_formatting.py` - lab data formatting scenarios
- `test_config.py` - configuration validation
- `test_dependency_tracking.py` - hash/cache behavior
- `test_integration.py` - end-to-end pipeline with fixtures

### B. Add Structured Error Types
Replace generic exceptions with domain-specific ones:
```python
class HealthLogParserError(Exception): pass
class DateExtractionError(HealthLogParserError): pass
class ValidationError(HealthLogParserError): pass
class LabParsingError(HealthLogParserError): pass
```

### C. Add Progress Persistence
Save state after each section so interrupted runs can resume:
```python
state_file = self.OUTPUT_PATH / ".state.json"
```

### D. Add Dry-Run Mode
Allow running without making API calls to validate input:
```bash
python main.py --dry-run
```

---

## Verification Plan

1. **Unit Tests:** Run `pytest tests/` with >80% coverage
2. **Integration Test:** Process a sample health log end-to-end
3. **Error Injection:** Test with malformed CSVs, empty sections, invalid configs
4. **Concurrency Test:** Run with MAX_WORKERS=8 on large log to check for race conditions
5. **Memory Test:** Profile with large lab CSV (100K+ rows)

---

## Priority Implementation Order

1. **Phase 1 (Critical):** Issues #1-4 - Race conditions, exception handling, tests, CSV errors
2. **Phase 2 (High):** Issues #5-8 - Config validation, memory, diagnostics, empty sections
3. **Phase 3 (Medium):** Issues #9-14 - Hash length, date coercion, columns, API retry
4. **Phase 4 (Low):** Issues #15-18 - Cleanup and polish
5. **Phase 5 (Architectural):** Test suite, error types, progress persistence
