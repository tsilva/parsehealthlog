# Quality Review Methodology

This document describes the detailed methodology for each phase of the quality review.

## Phase 1: Validation Analysis

**Objective:** Assess compliance with validate_timeline.py checks

**Methodology:**
1. Run validate_timeline.py on both profiles
2. Categorize issues by type:
   - Episode continuity (no gaps in episode IDs)
   - Related episodes (no orphaned references)
   - CSV structure (proper column formatting)
   - Chronological ordering (dates in correct order)
   - Comprehensive stack (all items explicitly stopped)
3. Calculate compliance score (100 - deductions for issues)
4. Generate validation report

**Scoring:**
- Start with 100 points
- Deduct 10 points per critical issue (episode continuity, related episodes, CSV structure)
- Deduct 5 points per medium issue (chronological order, comprehensive stack)
- Deduct 1 point per warning
- Minimum score: 0

**Success Criteria:** Score ≥ 90 for production readiness

---

## Phase 2: Data Preservation Analysis

**Objective:** Verify raw data is accurately preserved in processed outputs

**Methodology:**
1. **Stratified Sampling:** Select 30 entries per profile across three time periods:
   - Early period (first 33% of entries)
   - Middle period (middle 33% of entries)
   - Recent period (last 33% of entries)

2. **Comparison Checks:**
   - Extract numeric values from raw.md and processed.md
   - Compare medication dosages (number + unit patterns)
   - Check lab value preservation (compare with labs.md if exists)
   - Verify date/timing information
   - Check provider names (doctor extraction)
   - Detect lost uncertainty markers (?, "possibly", "maybe", etc.)

3. **Issue Classification:**
   - **Critical:** Missing numeric values, lab value discrepancies
   - **High:** Medication dosage mismatches, lost uncertainty markers
   - **Medium:** Date/timing mismatches, provider info changes
   - **Low:** Minor formatting differences

4. Generate preservation report with issue matrix

**Success Criteria:**
- < 5% critical issues in sample
- < 10% high issues in sample

---

## Phase 3: Episode Linking Analysis

**Objective:** Assess RelatedEpisode column quality and linking patterns

**Methodology:**
1. Parse CSV and build episode ID map
2. For each episode with RelatedEpisode:
   - Verify referenced episodes exist (no orphans)
   - Check if bidirectional links are appropriate
3. For treatment episodes:
   - Calculate percentage linked to conditions
   - Identify unlinked treatments
4. Calculate link completeness metrics
5. Generate linking quality report

**Metrics:**
- **Link Completeness:** (Treatments with links / Total treatments) × 100
- **Orphaned References:** Count of references to non-existent episodes
- **Linking Rate:** (Episodes with any links / Total episodes) × 100

**Success Criteria:**
- Link completeness ≥ 80%
- Zero orphaned references
- Linking rate ≥ 40%

---

## Phase 4: Categorization Analysis

**Objective:** Evaluate categorization accuracy and consistency

**Methodology:**
1. **Stratified Sampling:** Select 100 entries per profile, proportional to category distribution

2. **Categorization Checks:**
   - **Diagnosis vs Suspected:** Verify event matches details certainty
   - **Event Type Validity:** Check event is valid for category
     - condition: diagnosed, suspected, noted, worsened, improved, resolved, stable
     - symptom: noted, worsened, improved, resolved
     - treatment: started, stopped, adjusted, continued
     - test: ordered, completed
     - watch: noted
   - **Watch Misuse:** Watch should be for monitoring, not confirmed items
   - **Details Quality:** Check for sufficient detail in key categories

3. **Issue Severity:**
   - **High:** Invalid event type for category
   - **Medium:** Diagnosis/suspected mismatch, watch misuse
   - **Low:** Insufficient details, missing qualifiers

4. Generate categorization report

**Success Criteria:**
- < 5% high severity issues
- < 15% total issues

---

## Phase 5: Labs Integration Analysis

**Objective:** Verify labs/exams are properly integrated into processed files

**Methodology:**
1. Find all *.labs.md files in entries/ directory
2. For each labs file:
   - Check if corresponding processed.md file exists
   - Verify labs section exists in processed file (look for "Labs", "Results")
   - Extract sample lab values from labs.md
   - Check if lab values appear in processed.md
3. Calculate integration rate
4. List entries with missing integration
5. Generate integration report

**Metrics:**
- **Integration Rate:** (Labs files integrated / Total labs files) × 100

**Success Criteria:**
- Integration rate ≥ 95%

---

## Phase 6: Cross-Profile Consistency Analysis

**Objective:** Compare consistency between Tiago and Cristina profiles

**Methodology:**
1. **Voice Consistency:**
   - Calculate average detail field length for each profile
   - Compare detail verbosity and style
   - Score consistency (100 - |difference|/10)

2. **Linking Patterns:**
   - Calculate linking rate for each profile
   - Compare episode linking behavior
   - Assess pattern consistency

3. **Format Uniformity:**
   - Compare event type distributions
   - Check for systematic differences in categorization

4. Generate cross-profile report

**Metrics:**
- **Voice Consistency Score:** Measure of detail length similarity
- **Linking Pattern Difference:** Absolute difference in linking rates

**Success Criteria:**
- Voice consistency score ≥ 70
- Linking pattern difference < 20%

---

## Phase 7: Timeline Continuity Analysis

**Objective:** Assess long-running episode coherence and narrative quality

**Methodology:**
1. **Episode Selection:**
   - Group all entries by EpisodeID
   - Filter for episodes with:
     - ≥ 5 events
     - Duration > 1 year

2. **Continuity Checks:**
   - **Event Sequence Logic:** Check for logical progression
     - Should typically start with diagnosed/suspected/noted/started
     - Check for events after resolved/stopped (potential issue)
   - **Detail Completeness:** Verify major transitions have sufficient detail
     - Major events: diagnosed, started, stopped, resolved, worsened
     - Calculate % of major events with details > 20 chars
   - **Related Links:** Check if episode has appropriate links

3. **Coherence Assessment:**
   - Episode is coherent if:
     - No logical issues detected
     - Detail completeness ≥ 70%
     - Appropriate related links present

4. Generate continuity report with sample episode analyses

**Metrics:**
- **Coherence Rate:** (Coherent episodes / Total analyzed) × 100
- **Detail Completeness:** % of major transitions with adequate detail

**Success Criteria:**
- Coherence rate ≥ 85%
- Detail completeness ≥ 80%

---

## Quality Score Calculation

The overall quality score is a weighted average of phase scores:

| Phase | Weight | Description |
|-------|--------|-------------|
| Phase 1: Validation | 20% | Structural integrity |
| Phase 2: Data Preservation | 20% | Accuracy of extraction |
| Phase 3: Episode Linking | 15% | Relationship quality |
| Phase 4: Categorization | 15% | Classification accuracy |
| Phase 5: Labs Integration | 10% | Integration completeness |
| Phase 6: Cross-Profile | 10% | Consistency across profiles |
| Phase 7: Timeline Continuity | 10% | Narrative coherence |

**Total:** 100%

**Interpretation:**
- **90-100:** Excellent - Production ready
- **80-89:** Good - Minor improvements needed
- **70-79:** Fair - Moderate improvements needed
- **60-69:** Poor - Significant improvements required
- **< 60:** Critical - Major rework needed

---

## Sampling Strategy

### Stratified Sampling Rationale

The review uses stratified sampling to ensure representative coverage:

1. **Time-based Stratification (Phase 2, 7):**
   - Early entries may have different patterns (learning period)
   - Recent entries show current processing quality
   - Middle entries represent typical processing

2. **Category-based Stratification (Phase 4):**
   - Each category (condition, treatment, symptom, etc.) has unique rules
   - Proportional sampling ensures all categories are checked
   - Maintains statistical validity across diverse entry types

3. **Sample Size Selection:**
   - 30 entries (Phase 2): Sufficient for pattern detection, manageable for manual review
   - 100 entries (Phase 4): Larger sample for categorization due to higher variance
   - All long episodes (Phase 7): Complete coverage of critical narrative paths

### Statistical Confidence

With 30-100 entry samples from datasets of 500-2000 entries:
- 95% confidence level
- ±10% margin of error
- Sufficient to detect systematic issues

---

## Issue Classification

### Severity Levels

**Critical:**
- Data loss (missing numeric values)
- Structural integrity failures (broken references)
- Major accuracy issues (lab value discrepancies)

**High:**
- Significant data quality issues (dosage mismatches)
- Lost context (uncertainty markers removed)
- Invalid categorization (wrong event types)

**Medium:**
- Minor data discrepancies (date formatting)
- Incomplete information (missing provider details)
- Suboptimal linking (unlinked treatments)

**Low:**
- Formatting inconsistencies
- Missing qualifiers
- Insufficient detail in non-critical fields

---

## Automation vs Manual Review

### Automated Analysis
- Phase 1: Fully automated (validate_timeline.py)
- Phase 2: Automated extraction + pattern detection
- Phase 3: Fully automated (CSV parsing)
- Phase 4: Automated checks on sampled entries
- Phase 5: Automated integration verification
- Phase 6: Automated metric calculation
- Phase 7: Automated coherence assessment

### Manual Review Triggers
Manual review recommended when:
- Quality score < 70
- Critical issues detected (any count > 0)
- High issue count > 20% of sample
- Unusual patterns detected (e.g., all issues in one time period)

---

## Continuous Improvement

### Feedback Loop

1. **Run Quality Review** → Identify issues
2. **Prioritize Issues** → Focus on high-impact problems
3. **Update Prompts/Logic** → Fix root causes
4. **Re-process Data** → Apply fixes
5. **Re-run Quality Review** → Verify improvements
6. **Iterate** → Repeat until quality targets met

### Baseline Establishment

First review establishes baseline:
- Document current quality scores
- Identify systematic issues
- Set improvement targets
- Schedule next review

Subsequent reviews measure:
- Score improvements
- Issue reduction
- New issue types
- Quality trends over time

---

## Review Frequency Recommendations

**Regular Schedule:**
- Monthly: During active development
- Quarterly: In production maintenance
- Ad-hoc: After significant prompt changes

**Triggers for Immediate Review:**
- Major prompt modifications
- Model version changes
- New category additions
- User-reported data quality issues
