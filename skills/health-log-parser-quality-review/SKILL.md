---
name: health-log-parser-quality-review
description: Comprehensive 7-phase quality review for health-log-parser extraction quality. Assesses data integrity, preservation accuracy, episode linking, categorization quality, cross-profile consistency, and timeline continuity. Use when (1) After significant changes to prompts or processing logic, (2) Before production deployment, (3) Periodic quality audits, (4) Investigating suspected data quality issues, (5) Comparing quality across profiles.
---

# Health Log Parser Quality Review

## Overview

This skill provides a comprehensive, automated quality review process for health-log-parser outputs. It executes 7 analytical phases covering validation, data preservation, episode linking, categorization, labs integration, cross-profile consistency, and timeline continuity.

**Key Outputs:**
- Overall quality scores (0-100) per profile
- Executive summary with actionable recommendations
- Detailed per-profile quality reports
- Issue prioritization matrix
- Remediation action plan

**Typical Use Cases:**
- Post-deployment quality verification
- After prompt modifications
- Periodic quality audits (quarterly recommended)
- Investigation of suspected quality issues
- Comparing extraction quality across profiles

## Prerequisites

Before running the quality review:

1. **Complete Processing:** Both profiles must have been fully processed
   - `health_log.csv` must exist in each output directory
   - `entries/` directory must contain processed files

2. **File Structure:**
   ```
   OUTPUT_PATH/
   ├── health_log.csv           # Required
   ├── health_log.md            # Required
   └── entries/                 # Required
       ├── YYYY-MM-DD.raw.md
       ├── YYYY-MM-DD.processed.md
       └── YYYY-MM-DD.labs.md    # Optional but recommended
   ```

3. **Dependencies:**
   - Python 3.8+
   - pandas (for CSV processing)
   - Access to validate_timeline.py from health-log-parser

## Quick Start

### Running the Complete Review

```bash
# Navigate to health-log-parser directory
cd /Users/tsilva/repos/tsilva/health-log-parser

# Run complete quality review
python /Users/tsilva/.claude/skills/health-log-parser-quality-review/scripts/run_quality_review.py \
  --tiago-path "/Users/tsilva/Library/CloudStorage/GoogleDrive-eng.tiago.silva.sync@gmail.com/My Drive/healthlogparser-tiago" \
  --cristina-path "/Users/tsilva/Library/CloudStorage/GoogleDrive-eng.tiago.silva.sync@gmail.com/My Drive/healthlogparser-cristina" \
  --output-dir ./quality_review_reports \
  --verbose
```

**Expected Runtime:** 10-15 minutes for typical datasets (500-1000 entries per profile)

**Outputs:**
```
quality_review_reports/
├── executive_summary.md          # Start here - Overall assessment
├── tiago_quality_report.md       # Detailed Tiago analysis
├── cristina_quality_report.md    # Detailed Cristina analysis
├── issue_prioritization.md       # Issues ranked by priority
├── remediation_plan.md           # Action plan for fixes
└── complete_results.json         # Machine-readable results
```

## Understanding the Review Phases

### Phase 1: Validation Analysis (20% weight)

**What it checks:**
- Episode continuity (no gaps in episode IDs)
- Related episodes validity (no orphaned references)
- CSV structure compliance
- Chronological ordering
- Comprehensive stack updates

**Target Score:** ≥ 90/100

**Common Issues:**
- Episode ID gaps (ep-042 → ep-045)
- References to non-existent episodes
- Entries out of chronological order

**See:** `references/methodology.md#phase-1` for detailed methodology

---

### Phase 2: Data Preservation Analysis (20% weight)

**What it checks:**
- Lab value preservation (raw → processed)
- Medication dosage accuracy
- Clinical context retention
- Provider information completeness
- Date/timing accuracy
- Numeric value preservation

**Sampling:** 30 entries per profile (stratified: early, middle, recent)

**Target:** < 5% critical issues, < 10% total issues

**Common Issues:**
- Missing numeric values
- Medication dosages lost
- Uncertainty markers removed ("possibly" → "confirmed")
- Lab values not properly integrated

**See:** `references/methodology.md#phase-2` for detailed methodology

---

### Phase 3: Episode Linking Analysis (15% weight)

**What it checks:**
- Link completeness (treatments linked to conditions)
- Link correctness (valid episode IDs only)
- Link consistency
- Orphaned treatments identification

**Target:** Link completeness ≥ 80%, Zero orphaned references

**Common Issues:**
- Treatments not linked to conditions
- References to non-existent episodes
- Missing bidirectional links

**See:** `references/methodology.md#phase-3` for detailed methodology

---

### Phase 4: Categorization Analysis (15% weight)

**What it checks:**
- Diagnosis vs suspected distinction
- Event type validity (e.g., "diagnosed" for condition, not "started")
- Watch category misuse
- Status progression logic
- Details field quality

**Sampling:** 100 entries per profile (stratified by category)

**Target:** < 5% high severity issues, < 15% total issues

**Common Issues:**
- Wrong event types (condition with "started" instead of "diagnosed")
- Diagnosis/suspected mismatch
- Watch category misused for confirmed conditions

**See:** `references/methodology.md#phase-4` for detailed methodology

---

### Phase 5: Labs Integration Analysis (10% weight)

**What it checks:**
- Labs files exist and are integrated
- Formatting consistency
- Completeness of integration

**Target:** Integration rate ≥ 95%

**Common Issues:**
- Labs files not merged into processed.md
- Lab values missing from processed output
- Integration silently failing

**See:** `references/methodology.md#phase-5` for detailed methodology

---

### Phase 6: Cross-Profile Consistency Analysis (10% weight)

**What it checks:**
- Voice consistency (detail verbosity)
- Linking patterns consistency
- Format uniformity

**Target:** Voice consistency ≥ 70, Linking difference < 20%

**Common Issues:**
- Large detail length differences (200 chars vs 50 chars)
- Inconsistent linking rates (45% vs 15%)
- Format variations

**Note:** Some variation is expected due to different source materials and health complexities.

**See:** `references/methodology.md#phase-6` for detailed methodology

---

### Phase 7: Timeline Continuity Analysis (10% weight)

**What it checks:**
- Event sequence logic (diagnosed → treated → resolved)
- Timeline narrative coherence
- Detail completeness for major transitions
- Related episode connections

**Selection:** Long-running episodes (≥5 events, >1 year duration)

**Target:** Coherence rate ≥ 85%, Detail completeness ≥ 80%

**Common Issues:**
- Events after resolution without explanation
- Missing details for major transitions
- Illogical event sequences

**See:** `references/methodology.md#phase-7` for detailed methodology

---

## Running Individual Phases

You can run phases independently for faster iteration:

```bash
# Phase 1: Validation
python scripts/phase1_validation.py \
  --tiago-path /path/to/tiago/output \
  --cristina-path /path/to/cristina/output \
  --output-dir ./reports \
  --verbose
```

**Use Cases for Individual Phases:**
- Faster iteration when fixing specific issues
- Debugging a particular phase
- Skipping phases during development

---

## Interpreting Results

### Quality Score Ranges

| Score | Grade | Status | Action Required |
|-------|-------|--------|-----------------|
| 90-100 | A | ✅ Excellent | Production ready |
| 80-89 | B | ⚠️ Good | Monitor and refine |
| 70-79 | C | ⚠️ Fair | Improvements needed |
| 60-69 | D | ❌ Poor | Significant rework |
| < 60 | F | ❌ Critical | Major overhaul |

### Reading the Executive Summary

Start with `executive_summary.md`:

```markdown
## Overall Quality Scores
- Tiago: 87.4/100
- Cristina: 78.2/100

Status: ⚠️ Good
Assessment: Both profiles show good quality with some areas for improvement.

## Issue Summary
Total Issues: 45
- Critical: 2    ← Address immediately
- High: 8        ← High priority
- Medium: 25     ← Medium priority
- Low: 10        ← Low priority
```

**Action Priority:**
1. **Critical issues (2)** → Fix immediately before any production use
2. **High issues (8)** → Address before deployment
3. **Medium issues (25)** → Include in next iteration
4. **Low issues (10)** → Batch fixes, low priority

---

## Common Workflows

### After Modifying Prompts

```bash
# 1. Re-process both profiles
uv run python main.py --profile tiago --force-reprocess
uv run python main.py --profile cristina --force-reprocess

# 2. Run quality review
python /path/to/skill/scripts/run_quality_review.py \
  --tiago-path /path/to/tiago \
  --cristina-path /path/to/cristina \
  --output-dir ./quality_after_prompt_fix \
  --verbose

# 3. Compare with previous review
# - Check if scores improved
# - Verify no new issues introduced
# - Confirm target issues were fixed
```

### Pre-Production Checklist

Before deploying to production:

```bash
# 1. Run complete quality review
python /path/to/skill/scripts/run_quality_review.py ...

# 2. Verify requirements:
# - Overall score ≥ 90 for both profiles
# - Zero critical issues
# - High issues < 5 per profile
# - All phases score ≥ 80

# 3. Manual spot-check
# - Review 10 random entries per profile
# - Verify they look correct
# - Check edge cases

# 4. Document baseline
# - Save quality_review_reports/ directory
# - Note scores in CHANGELOG or deployment notes
# - Set up monitoring for regressions
```

---

## Troubleshooting

### Common Issues

#### "ImportError: Could not import validate_timeline.py"

**Solution:**
```bash
# Run from health-log-parser directory
cd /Users/tsilva/repos/tsilva/health-log-parser
python /path/to/skill/scripts/run_quality_review.py ...
```

#### "Missing health_log.csv"

**Solution:**
```bash
# Process profiles first
uv run python main.py --profile tiago
uv run python main.py --profile cristina
```

#### "No processed.md files found"

**Solution:**
```bash
# Check for failed processing
ls /path/to/entries/*.failed.md

# Review logs
tail -100 logs/warnings.log

# Re-run processing with verbose
uv run python main.py --profile tiago --verbose
```

**For more troubleshooting:** See `references/troubleshooting.md`

---

## Resources

### scripts/

Automated analysis scripts for all 7 phases:

- `run_quality_review.py` - Master orchestrator (runs all phases)
- `phase1_validation.py` - Validation analysis
- `phase2_preservation.py` - Data preservation audit
- `phase3_linking.py` - Episode linking assessment
- `phase4_categorization.py` - Categorization accuracy
- `phase5_labs_integration.py` - Labs integration check
- `phase6_cross_profile.py` - Cross-profile consistency
- `phase7_continuity.py` - Timeline continuity analysis
- `generate_reports.py` - Report synthesis

All scripts can be run independently or via the master orchestrator.

### references/

Comprehensive documentation:

- `methodology.md` - Detailed methodology for each phase
- `success_criteria.md` - Success criteria and scoring formulas
- `troubleshooting.md` - Common issues and solutions

Use these references when:
- Understanding how phases work
- Interpreting quality scores
- Debugging issues
- Planning improvements
