# Health Log Parser Improvement Plan

## Overview

This document outlines a systematic plan to improve the health-log-parser codebase. Each task is designed to be completed independently and includes clear acceptance criteria.

## Phase 1: Foundation (Cleanup & Documentation)

### 1.1 Fix Documentation-Code Mismatch
**Priority:** High  
**Effort:** 1-2 hours

**Problem:** README.md and docs/pipeline.md describe outputs (`current.yaml`, `history.csv`, `entities.json`) that don't exist in the current codebase.

**Tasks:**
- [x] Audit all documented outputs vs actual outputs
- [x] Update README.md to reflect actual current behavior
- [x] Update docs/pipeline.md to remove references to unimplemented features
- [x] Add "Future Features" section for planned but not yet implemented outputs

**Acceptance Criteria:**
- Documentation accurately describes what the tool currently produces
- No references to non-existent files or features
- Clear distinction between current and planned features

### 1.2 Add Comprehensive Error Handling
**Priority:** High  
**Effort:** 2-3 hours

**Problem:** Limited error handling for edge cases in file I/O, API calls, and data parsing.

**Tasks:**
- [ ] Add specific exception types for each failure mode
- [ ] Implement graceful degradation for missing optional data (labs, exams)
- [ ] Add user-friendly error messages with actionable suggestions
- [ ] Create error recovery strategies (e.g., skip bad sections, continue processing)

**Acceptance Criteria:**
- All file operations have try/catch with meaningful errors
- API failures provide clear retry guidance
- Bad data in one section doesn't stop entire pipeline
- Error messages include file paths and line numbers where applicable

## Phase 2: Observability & Metrics

### 2.1 Add Processing Metrics
**Priority:** Medium  
**Effort:** 3-4 hours

**Problem:** No visibility into processing performance, costs, or quality.

**Tasks:**
- [ ] Track LLM token usage per section and total
- [ ] Calculate estimated API costs (add pricing config per model)
- [ ] Log cache hit/miss rates for each cache type
- [ ] Track validation retry counts and success rates
- [ ] Save metrics to `metrics.json` in output directory

**Acceptance Criteria:**
- metrics.json contains: total_tokens, estimated_cost, cache_hits, cache_misses, validation_retries, processing_time
- Console shows real-time progress with ETA
- Cost estimates are accurate for OpenRouter pricing

### 2.2 Add Structured Logging
**Priority:** Medium  
**Effort:** 2-3 hours

**Problem:** Logs are text-only, making programmatic analysis difficult.

**Tasks:**
- [ ] Add JSON logging option for machine parsing
- [ ] Include structured fields: timestamp, level, component, section_date, operation
- [ ] Add correlation IDs for tracking requests across retries
- [ ] Create log rotation to prevent disk bloat

**Acceptance Criteria:**
- `--log-format json` flag outputs structured logs
- Each log entry has consistent schema
- Logs rotate at 10MB with 5 backup files

## Phase 3: CLI Enhancements

### 3.1 Add Dry-Run Mode
**Priority:** High  
**Effort:** 2-3 hours

**Problem:** No way to preview what would change without actually processing.

**Tasks:**
- [ ] Implement `--dry-run` flag
- [ ] Show which sections would be processed (cache miss)
- [ ] Display estimated API calls and costs
- [ ] List files that would be created/modified/deleted
- [ ] Return exit code 0 if no changes needed, 1 if processing required

**Acceptance Criteria:**
- `--dry-run` performs all steps except LLM calls and file writes
- Output clearly shows what actions would be taken
- Exit codes work for CI integration

### 3.2 Add Date Range Filtering
**Priority:** Medium  
**Effort:** 2-3 hours

**Problem:** Must process entire log even when only recent entries changed.

**Tasks:**
- [ ] Add `--from-date` and `--to-date` CLI arguments
- [ ] Filter sections before processing
- [ ] Ensure collated output still includes all entries (not just filtered range)
- [ ] Add `--recent N` shorthand for last N entries

**Acceptance Criteria:**
- Date filtering works with various formats (YYYY-MM-DD, relative like "30d")
- Only filtered sections are processed (API calls saved)
- Final health_log.md still contains all entries
- Works correctly with cache

### 3.3 Add Cache Management Commands
**Priority:** Low  
**Effort:** 2 hours

**Tasks:**
- [ ] Add `--cache-status` to show cache statistics
- [ ] Add `--cache-clear` to remove all cached files
- [ ] Add `--cache-clear-date DATE` to clear specific date
- [ ] Show cache size on disk

**Acceptance Criteria:**
- Cache commands work independently of processing
- Status shows: number of entries, total size, oldest/newest cached
- Clear commands require confirmation or --force flag

## Phase 4: Code Quality

### 4.1 Modularize main.py
**Priority:** High  
**Effort:** 6-8 hours

**Problem:** Single 1,313-line file with mixed responsibilities.

**Proposed Structure:**
```
health_log_parser/
├── __init__.py
├── cli.py                    # Argument parsing and main entry point
├── config.py                 # Configuration classes (already exists)
├── exceptions.py             # Custom exceptions (already exists)
├── models/
│   ├── __init__.py
│   ├── llm_client.py         # LLM wrapper with retry logic
│   ├── section.py            # Section dataclass and parsing
│   └── lab_result.py         # Lab result data structures
├── processors/
│   ├── __init__.py
│   ├── base_processor.py     # Abstract base class
│   ├── section_processor.py  # Main processing logic
│   ├── lab_processor.py      # Lab data loading and formatting
│   └── exam_processor.py     # Medical exam processing
├── utils/
│   ├── __init__.py
│   ├── cache.py              # Dependency tracking and caching
│   ├── dates.py              # Date parsing utilities
│   ├── hashing.py            # Hash computation utilities
│   └── markdown.py           # Markdown manipulation
└── prompts/
    ├── __init__.py
    └── loader.py             # Prompt loading and caching
```

**Tasks:**
- [ ] Create package structure
- [ ] Move LLM client to models/llm_client.py
- [ ] Move section processing to processors/section_processor.py
- [ ] Move lab loading to processors/lab_processor.py
- [ ] Move exam loading to processors/exam_processor.py
- [ ] Move utilities to utils/ directory
- [ ] Update imports and ensure tests still pass
- [ ] Update pyproject.toml entry point

**Acceptance Criteria:**
- No file exceeds 300 lines
- Each module has single responsibility
- All existing tests pass
- No circular imports
- CLI behavior unchanged

### 4.2 Add Type Hints Throughout
**Priority:** Medium  
**Effort:** 3-4 hours

**Problem:** Inconsistent type hints, some functions untyped.

**Tasks:**
- [ ] Add mypy to dev dependencies
- [ ] Add type hints to all function signatures
- [ ] Add return type annotations
- [ ] Create type stubs for external dependencies if needed
- [ ] Add mypy check to pre-commit hooks

**Acceptance Criteria:**
- mypy passes with --strict flag
- All public functions have type annotations
- No `Any` types except where truly necessary

### 4.3 Improve Test Coverage
**Priority:** High  
**Effort:** 8-10 hours

**Problem:** Limited test coverage, no integration tests.

**Tasks:**
- [ ] Add unit tests for each utility function
- [ ] Add integration test for full pipeline
- [ ] Add tests for error handling paths
- [ ] Add tests for cache invalidation logic
- [ ] Add property-based tests for date parsing
- [ ] Create test fixtures for sample health logs
- [ ] Add tests for CLI argument parsing

**Acceptance Criteria:**
- 80%+ code coverage
- Tests for all error conditions
- Integration test runs full pipeline with mock LLM
- All tests pass in CI

## Phase 5: Advanced Features

### 5.1 Add Prompt Versioning
**Priority:** Low  
**Effort:** 3-4 hours

**Problem:** No way to track prompt changes or A/B test prompts.

**Tasks:**
- [ ] Add version to prompt filenames (e.g., `process.v1.system_prompt.md`)
- [ ] Track prompt version in cache dependencies
- [ ] Add `--prompt-version` flag to select specific version
- [ ] Log prompt version used for each section
- [ ] Create prompt performance tracking (validation success rate per version)

**Acceptance Criteria:**
- Multiple prompt versions can coexist
- Cache correctly invalidates when prompt version changes
- Performance metrics tracked per prompt version

### 5.2 Add Incremental Processing Improvements
**Priority:** Medium  
**Effort:** 4-5 hours

**Problem:** Cache invalidation could be more granular.

**Tasks:**
- [ ] Add section-level dependency tracking (not just date-level)
- [ ] Detect when only metadata changed (no content change)
- [ ] Add smart diff to detect actual content changes vs formatting
- [ ] Implement partial reprocessing (only changed paragraphs)

**Acceptance Criteria:**
- Minor formatting changes don't trigger reprocessing
- Only changed sections trigger LLM calls
- Processing time proportional to changes, not total size

### 5.3 Add Export Formats
**Priority:** Low  
**Effort:** 4-6 hours

**Problem:** Only markdown output is supported.

**Tasks:**
- [ ] Add `--output-format json` for structured data export
- [ ] Add `--output-format csv` for spreadsheet analysis
- [ ] Create JSON schema for health log entries
- [ ] Add entry metadata (processing timestamp, model used, etc.)

**Acceptance Criteria:**
- JSON output validates against schema
- CSV contains all entry data in flat format
- Metadata included in all formats

## Implementation Order

**Recommended sequence:**

1. **Phase 1.1** - Fix documentation (foundation for everything else)
2. **Phase 3.1** - Dry-run mode (improves development workflow)
3. **Phase 4.1** - Modularize main.py (enables other improvements)
4. **Phase 1.2** - Error handling (stability improvement)
5. **Phase 4.3** - Test coverage (ensures refactoring safety)
6. **Phase 2.1** - Processing metrics (visibility into operations)
7. **Phase 3.2** - Date filtering (performance optimization)
8. **Phase 4.2** - Type hints (code quality)
9. **Phase 2.2** - Structured logging (observability)
10. **Phase 3.3** - Cache management (developer experience)
11. **Phase 5.x** - Advanced features (as needed)

## Success Metrics

- **Maintainability:** Cyclomatic complexity <10 per function, no file >300 lines
- **Reliability:** Zero unhandled exceptions in normal operation
- **Performance:** Processing time <2x the time spent on LLM calls
- **Observability:** All operations logged, metrics available
- **Test Coverage:** 80%+ line coverage, 100% of error paths tested

## Notes

- Each phase can be completed independently
- Priorities are based on impact vs effort
- Some tasks (like modularization) are prerequisites for others
- Consider user feedback when prioritizing Phase 5 features
