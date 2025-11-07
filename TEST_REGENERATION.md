# Recursive Hash-Based Dependency Tracking - Test Plan

## What Was Implemented

A comprehensive dependency tracking system that ensures downstream artifacts are automatically regenerated when upstream dependencies change.

### Dependency Chain

```
Raw Section Content + Labs Data + Prompts
    ↓
Processed Sections (*.processed.md)
    ↓
Reports (summary, questions, next_steps_*, next_labs)
    ↓
Consensus Next Steps (next_steps.md)
    ↓
Final Output (output.md)
```

### Hash Storage Format

All generated files now start with an HTML comment containing dependency hashes:
```html
<!-- DEPS: key1:hash1,key2:hash2,... -->
```

### Tracked Dependencies

**Processed Sections:**
- `raw`: Hash of raw section content
- `labs`: Hash of labs data for that date
- `process_prompt`: Hash of process.system_prompt.md
- `validate_prompt`: Hash of validate.system_prompt.md

**Reports (summary, questions, next_labs, specialist next steps):**
- `processed`: Combined hash of ALL processed sections
- `intro`: Hash of intro.md
- `prompt`: Hash of the prompt file

**Consensus Next Steps:**
- `specialist_reports`: Combined hash of all specialist next steps reports
- `prompt`: Hash of consensus_next_steps.system_prompt.md

**Output.md:**
- `processed`: Combined hash of all processed sections
- `summary`: Hash of summary.md
- `next_steps`: Hash of next_steps.md
- `next_labs`: Hash of next_labs.md

## Test Scenarios

### Test 1: Modify a Raw Section
**Expected:** Only that section's processed.md should regenerate

```bash
# Pick a recent entry and modify it
vim entries/<date>.raw.md  # Make a small edit
uv run python main.py
# Should see: Processing 1 section, all reports up-to-date
```

### Test 2: Modify Labs Data
**Expected:** Affected processed sections + all reports should regenerate

```bash
# Modify labs CSV
vim labs.csv  # Change a lab value
uv run python main.py
# Should see: Reprocessing affected sections, regenerating all reports
```

### Test 3: Modify Process Prompt
**Expected:** ALL processed sections + ALL reports should regenerate

```bash
# Modify the process prompt
vim prompts/process.system_prompt.md  # Add a comment or tweak instructions
uv run python main.py
# Should see: Processing ALL sections, regenerating ALL reports
```

### Test 4: Modify Summary Prompt
**Expected:** Only summary.md + output.md should regenerate

```bash
# Modify summary prompt
vim prompts/summary.system_prompt.md  # Make a small change
uv run python main.py
# Should see: All sections up-to-date, regenerating summary, regenerating output
```

### Test 5: Modify Specialist Prompt
**Expected:** All specialist reports + consensus + output should regenerate

```bash
# Modify specialist next steps prompt
vim prompts/specialist_next_steps.system_prompt.md  # Make a change
uv run python main.py
# Should see: All sections up-to-date, regenerating specialist reports, consensus, output
```

### Test 6: Delete a Report
**Expected:** That report + downstream reports should regenerate

```bash
# Delete summary
rm reports/summary.md
uv run python main.py
# Should see: Regenerating summary, regenerating output
```

### Test 7: No Changes
**Expected:** Everything should be cached

```bash
# Run without any changes
uv run python main.py
# Should see: Everything up-to-date, minimal work done
```

## Verification

To verify the dependency tracking is working:

1. **Check first line of any processed file:**
   ```bash
   head -1 entries/2024-11-07.processed.md
   # Should see: <!-- DEPS: labs:...,process_prompt:...,raw:...,validate_prompt:... -->
   ```

2. **Check first line of any report:**
   ```bash
   head -1 reports/summary.md
   # Should see: <!-- DEPS: intro:...,processed:...,prompt:... -->
   ```

3. **Monitor regeneration:**
   ```bash
   # Run with verbose output to see what's being regenerated
   uv run python main.py 2>&1 | grep -E "Generating|already exists|up-to-date"
   ```

## Benefits

1. **Efficiency**: Only regenerates what changed, saves API calls and time
2. **Correctness**: Ensures downstream artifacts are always in sync with dependencies
3. **Transparency**: Dependencies are visible in first line of each file
4. **Robustness**: Handles prompt changes, lab data updates, and content edits

## Migration Notes

- Existing files without dependency comments will be automatically regenerated on first run
- Old format (single hash) is detected and triggers regeneration
- New files are created with dependency tracking from the start
