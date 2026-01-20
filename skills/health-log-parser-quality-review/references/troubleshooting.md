# Troubleshooting Guide

Common issues encountered during quality review and their solutions.

## Setup and Execution Issues

### Issue: ImportError when running scripts

**Symptoms:**
```
ImportError: Could not import validate_timeline.py
```

**Causes:**
- validate_timeline.py not in expected location
- Python path issues

**Solutions:**
1. Ensure validate_timeline.py exists in health-log-parser root directory
2. Run scripts from health-log-parser directory:
   ```bash
   cd /path/to/health-log-parser
   python /path/to/skill/scripts/run_quality_review.py --tiago-path ... --cristina-path ...
   ```
3. Or add health-log-parser to PYTHONPATH:
   ```bash
   export PYTHONPATH=/path/to/health-log-parser:$PYTHONPATH
   ```

---

### Issue: Missing required files

**Symptoms:**
```
❌ Missing health_log.csv in Tiago path
❌ Missing entries/ directory in Cristina path
```

**Causes:**
- Incorrect path provided
- Processing not yet run on profiles

**Solutions:**
1. Verify paths are correct:
   ```bash
   ls /path/to/tiago/health_log.csv
   ls /path/to/cristina/entries/
   ```
2. If files don't exist, run processing first:
   ```bash
   uv run python main.py --profile tiago
   uv run python main.py --profile cristina
   ```
3. Ensure OUTPUT_PATH in profiles/*.yaml is correct

---

### Issue: No processed files found

**Symptoms:**
```
❌ No processed.md files found in Tiago entries/
```

**Causes:**
- Processing failed silently
- entries/ directory exists but is empty

**Solutions:**
1. Check for failed processing:
   ```bash
   ls /path/to/entries/*.failed.md
   ```
2. Review logs for errors:
   ```bash
   tail -100 logs/warnings.log
   ```
3. Re-run processing with verbose logging:
   ```bash
   uv run python main.py --profile tiago --verbose
   ```

---

## Phase-Specific Issues

### Phase 1: Validation Issues

#### Issue: False positive episode continuity gaps

**Symptoms:**
- Episode IDs appear sequential (ep-001, ep-002, ep-003)
- But validator reports gaps

**Causes:**
- Actual gaps in episode numbering (ep-001, ep-003, ep-005)
- Episode IDs assigned in batches with gaps

**Solutions:**
1. Review CSV for actual episode ID sequence:
   ```bash
   grep "^[^,]*," health_log.csv | cut -d',' -f5 | sort -u
   ```
2. If gaps are intentional (rare episodes removed), document in CLAUDE.md
3. If unintentional, fix update_timeline prompt to ensure sequential IDs

#### Issue: Orphaned references detected

**Symptoms:**
```
Episode ep-042 references non-existent ep-038
```

**Causes:**
- Referenced episode was removed/filtered
- Typo in episode ID
- LLM hallucinated episode ID

**Solutions:**
1. Search for the referenced episode:
   ```bash
   grep "ep-038" health_log.csv
   ```
2. If episode exists with different ID, update reference:
   ```bash
   # Manually edit CSV or re-process with fixed prompt
   ```
3. If episode truly doesn't exist, remove orphaned reference or add the missing episode

---

### Phase 2: Data Preservation Issues

#### Issue: High rate of lab value discrepancies

**Symptoms:**
- 30%+ of sampled entries show lab value mismatches
- Raw numbers don't appear in processed or labs files

**Causes:**
- Lab extraction not working correctly
- Numbers in raw file are not lab values (e.g., dates, ages)
- Unit conversions causing number changes

**Root Cause Analysis:**
1. Examine specific failing entry:
   ```bash
   cat entries/2023-05-15.raw.md
   cat entries/2023-05-15.processed.md
   cat entries/2023-05-15.labs.md
   ```
2. Check if numbers are truly lab values or contextual numbers
3. Review process.system_prompt.md for lab extraction instructions

**Solutions:**
- If unit conversions: Document as expected behavior
- If extraction failing: Update process.system_prompt.md
- If false positives: Improve detection heuristics in phase2_preservation.py

#### Issue: Lost uncertainty markers

**Symptoms:**
```
Raw: "possibly diabetes, unclear etiology"
Processed: "diabetes, etiology unknown"
```

**Causes:**
- LLM rephrasing removes uncertainty
- Prompt doesn't emphasize preserving uncertainty

**Solutions:**
1. Update process.system_prompt.md to emphasize:
   ```
   CRITICAL: Preserve ALL uncertainty markers. Use:
   - "suspected" for uncertain diagnoses
   - "possibly", "maybe", "unclear" for ambiguous information
   - "?" for questions/uncertainties
   Never remove or weaken uncertainty language.
   ```
2. Add to validation checks in validate.system_prompt.md
3. Re-process affected entries with --force-reprocess

#### Issue: Medication dosage mismatches

**Symptoms:**
- Raw: "500 mg aspirin"
- Processed: Lists aspirin but no dosage

**Causes:**
- Dosage extraction pattern not matching
- Information lost during summarization
- Format inconsistencies

**Solutions:**
1. Review extraction patterns in phase2_preservation.py
2. Update process.system_prompt.md to require dosage preservation:
   ```
   For ALL medications, preserve:
   - Drug name
   - Dosage + unit (e.g., "500 mg", "10 ml")
   - Frequency if mentioned
   ```
3. Add medication dosage check to validate.system_prompt.md

---

### Phase 3: Episode Linking Issues

#### Issue: Low link completeness (<60%)

**Symptoms:**
- Many treatments not linked to conditions
- Orphaned treatments in timeline

**Causes:**
- update_timeline prompt doesn't emphasize linking
- Conditions and treatments in different time periods
- LLM doesn't recognize relationships

**Solutions:**
1. Enhance update_timeline.system_prompt.md:
   ```
   CRITICAL: Link all treatments to conditions.
   When adding a treatment episode:
   - Search current_stack for related condition
   - Add condition's EpisodeID to RelatedEpisode field
   - If no condition in stack, search full timeline
   ```
2. Add post-processing step to auto-link obvious relationships:
   ```python
   # Find treatments without links
   # Search timeline for matching conditions
   # Auto-add links if confident match
   ```
3. Re-process with updated prompt

#### Issue: Orphaned references (references to non-existent episodes)

**Symptoms:**
```
Treatment ep-042 references ep-038 (doesn't exist)
```

**Causes:**
- Episode numbering gaps
- Referenced episode was later removed
- LLM hallucinated episode ID

**Solutions:**
1. Add validation to update_timeline prompt:
   ```
   Before adding RelatedEpisode:
   - Verify episode ID exists in current_stack or complete timeline
   - Only use confirmed episode IDs
   ```
2. Add post-processing validation:
   ```python
   # Check all RelatedEpisode values
   # Remove references to non-existent episodes
   # Log warnings for manual review
   ```

---

### Phase 4: Categorization Issues

#### Issue: Invalid event types for categories

**Symptoms:**
- condition with event="started"
- treatment with event="diagnosed"

**Causes:**
- LLM confusion about categories
- Prompt doesn't clearly define valid events
- Complex cases (e.g., "started monitoring X condition")

**Solutions:**
1. Add validation to update_timeline.system_prompt.md:
   ```
   VALID EVENT TYPES:
   - condition: diagnosed, suspected, noted, worsened, improved, resolved, stable
   - symptom: noted, worsened, improved, resolved
   - treatment: started, stopped, adjusted, continued
   - test: ordered, completed
   - watch: noted

   NEVER use events outside these lists.
   ```
2. Add event type validation to validate.system_prompt.md
3. Consider adding Python-based validation in main.py

#### Issue: Diagnosis vs suspected confusion

**Symptoms:**
- Event="diagnosed" but details say "suspected"
- Event="suspected" but details are definitive

**Causes:**
- Inconsistent language in raw entries
- LLM misinterpreting certainty level
- Prompt ambiguity

**Solutions:**
1. Clarify in update_timeline.system_prompt.md:
   ```
   DIAGNOSIS CERTAINTY:
   - Use "diagnosed" ONLY for confirmed diagnoses (doctor confirmed, test confirmed)
   - Use "suspected" for unconfirmed conditions, pending tests, clinical suspicion
   - Details field must match event certainty
   ```
2. Add examples in prompt:
   ```
   ✓ CORRECT:
   Event: diagnosed | Details: "Doctor confirmed diabetes via HbA1c test"
   Event: suspected | Details: "Doctor suspects diabetes, pending confirmation test"

   ✗ WRONG:
   Event: diagnosed | Details: "Possibly diabetes, test pending"
   ```

---

### Phase 5: Labs Integration Issues

#### Issue: Low integration rate (<85%)

**Symptoms:**
- Many .labs.md files exist
- Corresponding processed.md files don't contain lab data

**Causes:**
- Labs not being merged during processing
- Merging logic failing silently
- Labs formatted differently than expected

**Solutions:**
1. Check if labs are being read:
   ```python
   # Add debug logging to main.py
   print(f"Found labs file: {labs_file}")
   print(f"Labs content length: {len(labs_content)}")
   ```
2. Verify merge logic in main.py:
   - Are labs being appended to processed content?
   - Is labs section clearly marked?
3. Update process.system_prompt.md to ensure labs preservation:
   ```
   If labs/exam data provided, integrate into output:
   ## Labs/Exams
   [lab data here]
   ```

#### Issue: Labs integrated but undetectable by script

**Symptoms:**
- Manual inspection shows labs present
- Script reports labs missing

**Causes:**
- Detection heuristics too strict
- Labs formatted differently than expected
- Section headers vary

**Solutions:**
1. Improve detection in phase5_labs_integration.py:
   ```python
   # Add more header variations
   has_labs_section = any(marker in processed_content.lower()
       for marker in ["labs", "results", "tests", "laboratory", "bloodwork"])
   ```
2. Check for lab-like patterns (number + unit + test name)
3. Manual review of false negatives to refine detection

---

### Phase 6: Cross-Profile Consistency Issues

#### Issue: Large voice consistency differences

**Symptoms:**
- Tiago avg detail: 200 chars
- Cristina avg detail: 50 chars

**Causes:**
- Different source material verbosity
- Different processing approaches
- Intentional style differences

**Solutions:**
1. **If Intentional:**
   - Document in CLAUDE.md
   - Adjust success criteria for this project
   - Accept lower consistency score

2. **If Unintentional:**
   - Review source health.md files for verbosity differences
   - Check if prompts are being applied consistently
   - Consider whether detail length differences are problematic

#### Issue: Linking rate differences

**Symptoms:**
- Tiago: 45% linking rate
- Cristina: 15% linking rate

**Causes:**
- Different health complexity (Tiago has more chronic conditions)
- Inconsistent prompt application
- Different timeline structures

**Solutions:**
1. Analyze episode distributions:
   ```bash
   # Count episodes by category
   cut -d',' -f3 health_log.csv | sort | uniq -c
   ```
2. If complexity explains difference: Document as expected
3. If inconsistent: Review update_timeline.system_prompt.md application

---

### Phase 7: Timeline Continuity Issues

#### Issue: Low coherence rate (<70%)

**Symptoms:**
- Many episodes flagged as incoherent
- Unusual event sequences detected

**Causes:**
- Complex health trajectories (not actually incoherent)
- Detection heuristics too strict
- Real issues with event sequencing

**Solutions:**
1. Manual review of flagged episodes:
   ```bash
   # Check specific episode
   grep "ep-042" health_log.csv
   ```
2. Determine if false positive or real issue
3. If false positive: Adjust coherence detection in phase7_continuity.py
4. If real issue: Fix update_timeline prompt for better sequencing logic

#### Issue: Events after resolution

**Symptoms:**
```
Warning: Events continue after 'resolved' on 2023-05-15
```

**Causes:**
- Condition recurred (legitimate)
- Should have used episode ID (ep-042-2)
- Resolution incorrectly marked

**Solutions:**
1. For recurrences: Use new episode IDs
   ```
   ep-042: Initial episode (2020-2023, resolved)
   ep-043: Recurrence (2024-, ongoing)
   ```
2. Update update_timeline.system_prompt.md:
   ```
   RECURRENCES:
   If a resolved condition recurs, create NEW episode ID.
   Add to Details: "Recurrence of ep-042"
   ```
3. Add RelatedEpisode link between original and recurrence

---

## Quality Score Interpretation Issues

### Issue: Good individual phase scores but low overall

**Example:**
```
Phase 1: 85
Phase 2: 82
Phase 3: 80
Phase 4: 78
Phase 5: 90
Phase 6: 75
Phase 7: 85
Overall: 81.9 (Target: 90)
```

**Causes:**
- No single phase is excellent
- Consistent moderate issues across all phases
- Need systematic improvements

**Solutions:**
1. Focus on phases with lowest scores (Phase 6: 75)
2. Bring weakest phases above 85
3. Then push strong phases above 90
4. Incremental improvement across all phases

---

### Issue: Vastly different scores between profiles

**Example:**
```
Tiago: 88/100
Cristina: 62/100
```

**Causes:**
- Different source data quality
- Processing issues specific to one profile
- Profile-specific patterns LLM struggles with

**Solutions:**
1. Analyze phase-by-phase differences:
   ```
   Which phases differ most?
   Are issues consistent or varied?
   ```
2. Review Cristina's source health.md:
   - Formatting differences?
   - Language/style differences?
   - Missing information?
3. Test processing on small Cristina sample with extra logging
4. May need profile-specific prompt adjustments

---

## Performance Issues

### Issue: Quality review takes too long

**Symptoms:**
- Review taking >30 minutes
- Script appears hung

**Causes:**
- Large number of entries (>1000 per profile)
- Complex regex patterns
- No progress indicators

**Solutions:**
1. Enable verbose mode to see progress:
   ```bash
   python run_quality_review.py --tiago-path ... --verbose
   ```
2. Run individual phases to isolate slowness:
   ```bash
   python phase2_preservation.py --tiago-path ... --verbose
   ```
3. Reduce sample sizes during development (edit scripts)
4. Skip heavy phases during iteration:
   ```bash
   python run_quality_review.py --skip-phases 2 7 ...
   ```

---

### Issue: Out of memory errors

**Symptoms:**
```
MemoryError: Unable to allocate array
```

**Causes:**
- Loading very large CSV files
- Processing all entries at once
- Insufficient system RAM

**Solutions:**
1. Process profiles separately
2. Reduce sample sizes in scripts
3. Add batch processing to memory-intensive phases
4. Close other applications during review

---

## False Positives and Edge Cases

### Issue: Numbers flagged as missing but are dates/ages

**Example:**
```
Missing numeric values: {'2023', '45', '10'}
```

**Causes:**
- Detection treats all numbers as significant
- Dates, ages, counts all flagged
- Can't distinguish lab values from contextual numbers

**Solutions:**
1. Accept some false positives as cost of detection
2. Filter known false positive patterns:
   ```python
   # Ignore 4-digit years
   if len(number) == 4 and number.startswith('20'):
       continue
   ```
3. Focus on patterns: If 20+ numbers missing, likely real issue
4. Manual review of flagged entries when count is low

---

### Issue: Legitimate variations flagged as issues

**Example:**
```
Doctor name mismatch:
Raw: "Dr. John Smith at General Hospital"
Processed: "Dr. John Smith"
```

**Causes:**
- Processing intentionally simplifies
- Facility names removed for consistency
- Detection too strict

**Solutions:**
1. Determine if variation is acceptable:
   - Is essential information preserved? (doctor name)
   - Is lost information important? (facility name)
2. Update detection heuristics if acceptable:
   ```python
   # Only flag if doctor name itself changes, not facility
   raw_name = extract_core_name(raw_doctors)
   processed_name = extract_core_name(processed_doctors)
   if raw_name != processed_name:
       # Flag as issue
   ```
3. Or document as expected behavior in success_criteria.md

---

## Getting Help

### Debug checklist

Before asking for help:
1. [ ] Run with --verbose flag
2. [ ] Check logs/all.log for errors
3. [ ] Manually inspect failing entries
4. [ ] Verify paths are correct
5. [ ] Confirm health-log-parser is latest version
6. [ ] Try on smaller sample to isolate issue

### Useful debug commands

```bash
# Check CSV structure
head -20 health_log.csv
wc -l health_log.csv

# Find entries with specific issue
grep "ep-042" health_log.csv

# Check processed files
ls entries/*.processed.md | wc -l
ls entries/*.failed.md

# Review recent logs
tail -100 logs/all.log

# Test single entry processing
python -c "from main import process_entry; process_entry('2023-05-15')"
```

### Reporting issues

When reporting quality review issues, include:
1. Command run
2. Error message or unexpected output
3. Complete results JSON (complete_results.json)
4. Sample of affected entries (3-5 examples)
5. health-log-parser version
6. Profile configuration (profiles/*.yaml)
