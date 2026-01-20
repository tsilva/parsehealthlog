# Success Criteria and Scoring

This document defines success criteria, scoring formulas, and target thresholds for each phase.

## Overall Quality Targets

| Grade | Score Range | Status | Action Required |
|-------|-------------|--------|-----------------|
| A | 90-100 | ✅ Excellent | Production ready |
| B | 80-89 | ⚠️ Good | Monitor and refine |
| C | 70-79 | ⚠️ Fair | Improvements needed before production |
| D | 60-69 | ❌ Poor | Significant rework required |
| F | < 60 | ❌ Critical | Major overhaul needed |

## Phase-Specific Success Criteria

### Phase 1: Validation Analysis

**Target Score:** ≥ 90/100

**Scoring Formula:**
```
Score = 100
  - (episode_continuity_issues × 10)
  - (related_episode_issues × 10)
  - (csv_structure_issues × 10)
  - (chronological_order_issues × 5)
  - (comprehensive_stack_issues × 5)
  - (warnings × 1)
Score = max(0, Score)
```

**Success Criteria:**
- Zero episode continuity gaps
- Zero orphaned references
- All CSV columns properly formatted
- All entries in chronological order
- Comprehensive stack properly maintained

**Acceptable Thresholds:**
- Production: Score ≥ 90, Critical issues = 0
- Pre-production: Score ≥ 80, Critical issues ≤ 2
- Development: Score ≥ 70

**Weight in Overall Score:** 20%

---

### Phase 2: Data Preservation Analysis

**Target:** < 5% critical issues, < 10% total issues in sample

**Issue Categories and Severity:**
- **Critical (10 points each):**
  - Missing numeric values
  - Lab value discrepancies (>30% mismatch)

- **High (5 points each):**
  - Medication dosage mismatches
  - Lost uncertainty markers

- **Medium (2 points each):**
  - Date/timing mismatches
  - Provider info changes

- **Low (1 point each):**
  - Minor formatting differences

**Scoring Formula:**
```
IssueCount = critical + high + medium + low
Score = max(0, 100 - IssueCount)
```

**Success Criteria:**
- Critical issues ≤ 1 (in 30-entry sample)
- High issues ≤ 3
- Total issues ≤ 6 (20% of sample)
- No systematic patterns (issues spread across time periods)

**Acceptable Thresholds:**
- Production: Critical ≤ 1, Total ≤ 6
- Pre-production: Critical ≤ 2, Total ≤ 9
- Development: Critical ≤ 3, Total ≤ 15

**Weight in Overall Score:** 20%

---

### Phase 3: Episode Linking Analysis

**Target:** Link completeness ≥ 80%, Zero orphaned references

**Metrics:**
- **Link Completeness:** (Treatments with condition links / Total treatments) × 100
- **Orphaned References:** Count of RelatedEpisode values pointing to non-existent episodes
- **Linking Rate:** (Episodes with any links / Total episodes) × 100

**Scoring Formula:**
```
Score = LinkCompleteness
  - (OrphanedReferences × 10)
  - max(0, (40 - LinkingRate))
Score = max(0, min(100, Score))
```

**Success Criteria:**
- Link completeness ≥ 80%
- Zero orphaned references
- Linking rate ≥ 40%
- All treatments for ongoing conditions are linked

**Acceptable Thresholds:**
- Production: Completeness ≥ 80%, Orphaned = 0
- Pre-production: Completeness ≥ 70%, Orphaned ≤ 2
- Development: Completeness ≥ 60%, Orphaned ≤ 5

**Weight in Overall Score:** 15%

---

### Phase 4: Categorization Analysis

**Target:** < 5% high severity issues, < 15% total issues

**Issue Types and Severity:**
- **High (10 points each):**
  - Invalid event type for category

- **Medium (5 points each):**
  - Diagnosis/suspected distinction mismatch
  - Watch category misuse

- **Low (2 points each):**
  - Insufficient details
  - Missing uncertainty qualifiers

**Scoring Formula:**
```
IssueScore = (high_issues × 10) + (medium_issues × 5) + (low_issues × 2)
Score = max(0, 100 - IssueScore)
```

**Success Criteria:**
- High severity issues < 5 (in 100-entry sample)
- Total issues < 15
- Correct diagnosis vs suspected distinction > 95%
- Valid event types > 95%
- Adequate details in key categories > 85%

**Acceptable Thresholds:**
- Production: High ≤ 5, Total ≤ 15
- Pre-production: High ≤ 10, Total ≤ 25
- Development: High ≤ 15, Total ≤ 35

**Weight in Overall Score:** 15%

---

### Phase 5: Labs Integration Analysis

**Target:** Integration rate ≥ 95%

**Metric:**
- **Integration Rate:** (Labs files properly integrated / Total labs files) × 100

**Integration Definition:**
A labs file is considered "integrated" if:
- Corresponding processed.md exists
- Labs section present in processed.md ("Labs", "Results", etc.)
- OR sample lab values appear in processed content

**Scoring Formula:**
```
Score = IntegrationRate
```

**Success Criteria:**
- Integration rate ≥ 95%
- All recent labs (last 30 days) integrated at 100%
- No systematic gaps in any time period

**Acceptable Thresholds:**
- Production: Rate ≥ 95%
- Pre-production: Rate ≥ 90%
- Development: Rate ≥ 80%

**Weight in Overall Score:** 10%

---

### Phase 6: Cross-Profile Consistency Analysis

**Target:** Voice consistency ≥ 70, Linking difference < 20%

**Metrics:**
- **Voice Consistency Score:** 100 - (|AvgDetailLength1 - AvgDetailLength2| / 10)
- **Linking Pattern Difference:** |LinkingRate1 - LinkingRate2|

**Scoring Formula:**
```
Score = (VoiceConsistencyScore × 0.6) + ((100 - LinkingDifference) × 0.4)
Score = max(0, Score)
```

**Success Criteria:**
- Voice consistency score ≥ 70
- Detail length difference < 100 chars
- Linking rate difference < 20%
- Similar event type distributions (within 15%)
- Consistent categorization patterns

**Acceptable Thresholds:**
- Production: Voice ≥ 70, Linking diff < 20%
- Pre-production: Voice ≥ 60, Linking diff < 30%
- Development: Voice ≥ 50, Linking diff < 40%

**Weight in Overall Score:** 10%

---

### Phase 7: Timeline Continuity Analysis

**Target:** Coherence rate ≥ 85%, Detail completeness ≥ 80%

**Metrics:**
- **Coherence Rate:** (Coherent episodes / Total analyzed) × 100
- **Detail Completeness:** (Major transitions with details / Total major transitions) × 100
- **Related Links Rate:** (Episodes with appropriate links / Total analyzed) × 100

**Coherence Definition:**
An episode is "coherent" if:
- No logical sequencing issues
- Detail completeness ≥ 70%
- No events after final resolution (unless explained)

**Scoring Formula:**
```
Score = (CoherenceRate × 0.6) + (DetailCompleteness × 0.4)
Score = max(0, Score)
```

**Success Criteria:**
- Coherence rate ≥ 85%
- Detail completeness ≥ 80%
- All long-running conditions (>2 years) are coherent
- No unexplained event sequences
- Major transitions well-documented

**Acceptable Thresholds:**
- Production: Coherence ≥ 85%, Details ≥ 80%
- Pre-production: Coherence ≥ 75%, Details ≥ 70%
- Development: Coherence ≥ 65%, Details ≥ 60%

**Weight in Overall Score:** 10%

---

## Overall Quality Score Calculation

**Formula:**
```
OverallScore =
  (Phase1_Score × 0.20) +
  (Phase2_Score × 0.20) +
  (Phase3_Score × 0.15) +
  (Phase4_Score × 0.15) +
  (Phase5_Score × 0.10) +
  (Phase6_Score × 0.10) +
  (Phase7_Score × 0.10)
```

**Example Calculation:**
```
Profile: Tiago
Phase 1: 95/100 × 0.20 = 19.0
Phase 2: 85/100 × 0.20 = 17.0
Phase 3: 88/100 × 0.15 = 13.2
Phase 4: 78/100 × 0.15 = 11.7
Phase 5: 100/100 × 0.10 = 10.0
Phase 6: 75/100 × 0.10 = 7.5
Phase 7: 90/100 × 0.10 = 9.0
Total: 87.4/100 (Grade: B, Good)
```

---

## Interpreting Results

### Score Ranges and Actions

**90-100 (Excellent - Grade A)**
- Production ready
- Monitor for regression
- Document as quality baseline
- Consider minor refinements

**80-89 (Good - Grade B)**
- Near production ready
- Address high/critical issues
- Review and refine prompts
- Plan re-review after fixes

**70-79 (Fair - Grade C)**
- Not production ready
- Significant improvements needed
- Systematic issues likely present
- Review prompts and processing logic
- Re-process data after fixes

**60-69 (Poor - Grade D)**
- Significant rework required
- Multiple systematic issues
- Consider prompt redesign
- May need additional validation checks
- Extensive testing after fixes

**< 60 (Critical - Grade F)**
- Major overhaul needed
- Fundamental issues in processing
- Redesign prompts from scratch
- Add comprehensive validation
- Complete re-processing required

### Red Flags

Immediate attention required if:
- Any critical issues detected (Phase 2)
- Orphaned references exist (Phase 3)
- Invalid event types > 10% (Phase 4)
- Integration rate < 85% (Phase 5)
- Multiple phases score < 70
- Systematic patterns in any phase

---

## Quality Improvement Targets

### Improvement Milestones

After implementing fixes, target improvements:

**First Iteration (30 days):**
- Eliminate all critical issues
- Reduce high issues by 50%
- Achieve minimum 70/100 overall

**Second Iteration (60 days):**
- Reduce high issues by 80%
- Reduce medium issues by 50%
- Achieve minimum 80/100 overall

**Third Iteration (90 days):**
- Near-zero critical/high issues
- Medium issues < 10%
- Achieve target 90/100 overall

### Tracking Progress

Maintain quality score history:
```
Date       | Overall | P1  | P2  | P3  | P4  | P5  | P6  | P7  | Notes
-----------|---------|-----|-----|-----|-----|-----|-----|-----|------
2026-01-20 | 72.8    | 88  | 65  | 75  | 70  | 95  | 68  | 82  | Baseline
2026-02-20 | 78.5    | 92  | 72  | 78  | 75  | 97  | 72  | 85  | After prompt fixes
2026-03-20 | 85.2    | 95  | 82  | 85  | 82  | 98  | 78  | 88  | After validation adds
2026-04-20 | 91.0    | 98  | 90  | 90  | 88  | 100 | 85  | 92  | Production ready
```

---

## Profile-Specific Adjustments

Some profiles may have inherent differences:

**Different Detail Levels:**
- Medical professional vs patient perspective
- Verbose vs concise writing styles
- Different information availability

**Acceptable Variations:**
- Detail length: ±50% between profiles
- Linking rates: ±15% for different health complexities
- Integration rates: Should be consistent (±5%)
- Validation scores: Should be identical (structural)

**When Consistency Matters:**
- Data preservation (should be 95%+ for both)
- Validation compliance (structural integrity)
- Labs integration (process should be identical)

**When Variation is Normal:**
- Episode linking (depends on condition complexity)
- Categorization patterns (different health profiles)
- Timeline continuity (depends on care continuity)

---

## Using Criteria for Decision-Making

### Release Readiness Checklist

Before production deployment:
- [ ] Overall score ≥ 90 for all profiles
- [ ] Zero critical issues
- [ ] High issues < 5 per profile
- [ ] All phases score ≥ 80
- [ ] Validation score ≥ 95
- [ ] Data preservation issues < 5%
- [ ] Labs integration ≥ 95%
- [ ] Manual spot-check confirms quality

### Prompt Update Checklist

After modifying prompts:
- [ ] Run quality review on test dataset
- [ ] Compare before/after scores
- [ ] Verify no regression in any phase
- [ ] Ensure improvements in target areas
- [ ] Document changes and impacts
- [ ] Update baseline scores

### Issue Prioritization

Use severity and frequency to prioritize:
1. Critical issues (any count) → Immediate fix
2. High issues (>10% of sample) → High priority
3. Medium issues (>20% of sample) → Medium priority
4. Low issues → Low priority, batch fixes

Prioritize phases with lowest scores first.
