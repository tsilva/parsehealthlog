# Health Log System Redesign: First Principles Analysis

## Current System Summary

Your current system:
1. **Splits** health log by date sections (`### YYYY-MM-DD`)
2. **Processes** each section independently (LLM transforms raw → structured)
3. **Validates** each section (LLM audits for data loss)
4. **Generates reports**: Summary, 14 specialist next-steps, consensus, clarifying questions, next labs
5. **Caches** via hash-based dependency tracking

**Outputs produced:**
- Patient summary (demographics, timeline, problems, meds, labs, lifestyle)
- 14 specialist-specific recommendations + consensus
- Clarifying questions (3 runs merged)
- Next labs suggestions
- Full structured log

---

## First Principles Analysis

### The Core Question
*"What is the minimum viable system that ensures I always know what to do next for my health?"*

### Key Insights

**1. The Goal is Decision Support, Not Documentation**

The current system produces excellent *documentation* (structured entries, comprehensive summary), but the real value is in answering: **"What should I do this week/month/quarter?"**

The summary is a means to an end, not the end itself.

**2. Health Data Has Different "Refresh Rates"**

| Data Type | Change Frequency | Current Handling |
|-----------|------------------|------------------|
| Demographics/history | Rarely | Mixed into summary |
| Chronic conditions | Occasionally | Problem list |
| Active symptoms | Frequently | Per-entry processing |
| Labs | Per test cycle | Separate CSV integration |
| Next steps | Should recalc on new data | Regenerated from scratch |

*Problem:* Everything is recomputed together. A minor symptom update triggers full regeneration.

**3. 14 Specialists is Noise, Not Signal**

For someone with thyroid issues, gastro problems, and headaches, do they really need cardiology, pulmonology, urology, dermatology, hematology, genetics perspectives?

*Better approach:* Analyze what conditions actually exist → get perspectives from relevant specialists only.

**4. "Next Steps" Should Be Primary, Not Secondary**

Current hierarchy: Summary → Specialist Reports → Consensus → Next Steps
Optimal hierarchy: **Next Steps** → Supporting Evidence → Full Record

**5. Missing: Experimentation & Biohacking Track**

The current system treats everything through a clinical lens. But you mentioned biohacking, diet, exercise, sleep — these are **self-experiments**, not clinical management.

They need a different framework: Hypothesis → Intervention → Tracking → Conclusion

**6. Missing: Temporal Intelligence**

- What symptoms are getting worse over time?
- What labs are trending up/down?
- What interventions correlated with improvements?

The current system processes sections independently — it doesn't build temporal understanding.

---

## Proposed Redesign: The "Health Operating System"

### Core Architecture Shift

**FROM:** Document Processing Pipeline (log → sections → reports)
**TO:** Living Knowledge Graph (entities, relationships, time-series)

### Three-Layer Model

```
┌─────────────────────────────────────────────────────────┐
│                    ACTION LAYER                          │
│  "What do I do now?"                                    │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐│
│  │ This Week   │ │ This Month  │ │ This Quarter        ││
│  │ • Doctor X  │ │ • Lab panel │ │ • Annual physical   ││
│  │ • Start med │ │ • Follow-up │ │ • Specialist consult││
│  └─────────────┘ └─────────────┘ └─────────────────────┘│
├─────────────────────────────────────────────────────────┤
│                    STATE LAYER                          │
│  "What's my current health status?"                     │
│  ┌──────────────────┐  ┌─────────────────────────────┐  │
│  │ Active Problems  │  │ Current Medications          │  │
│  │ • Hypothyroid    │  │ • Levothyroxine 75mcg QD    │  │
│  │ • GI issues      │  │ • Probiotic                 │  │
│  └──────────────────┘  └─────────────────────────────┘  │
│  ┌──────────────────┐  ┌─────────────────────────────┐  │
│  │ Active Symptoms  │  │ Active Experiments          │  │
│  │ • Headaches ↑    │  │ • Dairy elimination (day 14)│  │
│  │ • Fatigue ↓      │  │ • Sleep timing experiment   │  │
│  └──────────────────┘  └─────────────────────────────┘  │
├─────────────────────────────────────────────────────────┤
│                    EVIDENCE LAYER                       │
│  "What's the supporting data?"                          │
│  • Lab trends (TSH: 5.2 → 4.1 → 3.8 over 6mo)          │
│  • Symptom timeline                                     │
│  • Provider notes                                       │
│  • Full chronological log                               │
└─────────────────────────────────────────────────────────┘
```

---

## Refined Design (Based on User Preferences)

**Confirmed requirements:**
- ✅ Primary output: Action Plan ("what do I do next")
- ✅ Biohacking/experimentation: Essential
- ✅ Input: Keep freeform (system extracts structure)
- ⚖️ Specialists: TBD (keep flexible, maybe reduce)

### Proposed Architecture: Action-Centric Health OS

```
┌──────────────────────────────────────────────────────────────┐
│                     INPUT (Unchanged)                         │
│  Freeform markdown with ### YYYY-MM-DD sections               │
│  + Labs CSV                                                   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                  EXTRACTION LAYER                             │
│  Per-section processing (current) + Entity Extraction (new)  │
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────────────────┐│
│  │ Conditions  │ │ Medications │ │ Experiments              ││
│  │ • Active    │ │ • Current   │ │ • Hypothesis             ││
│  │ • Resolved  │ │ • Past      │ │ • Protocol               ││
│  │ • Suspected │ │ • Changes   │ │ • Metrics                ││
│  └─────────────┘ └─────────────┘ └──────────────────────────┘│
│  ┌─────────────┐ ┌─────────────┐ ┌──────────────────────────┐│
│  │ Symptoms    │ │ Providers   │ │ Lab Trends               ││
│  │ • Pattern   │ │ • Who       │ │ • Trajectory             ││
│  │ • Trend     │ │ • Specialty │ │ • Alerts                 ││
│  │ • Severity  │ │ • Last seen │ │ • Next due               ││
│  └─────────────┘ └─────────────┘ └──────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                     STATE MODEL                               │
│  state.json - Structured representation of current health    │
│  (Enables incremental updates, trend analysis, action gen)   │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                  ANALYSIS LAYER                               │
│  ┌───────────────────┐  ┌───────────────────────────────────┐│
│  │ Clinical Analysis │  │ Biohacking Analysis               ││
│  │ • Problem-focused │  │ • Experiment design               ││
│  │ • Relevant specs  │  │ • Self-order opportunities        ││
│  │ • Care gaps       │  │ • Optimization protocols          ││
│  └───────────────────┘  └───────────────────────────────────┘│
│  ┌───────────────────┐  ┌───────────────────────────────────┐│
│  │ Trend Analysis    │  │ Priority Engine                   ││
│  │ • Symptom deltas  │  │ • Urgency scoring                 ││
│  │ • Lab trajectories│  │ • ROI estimation                  ││
│  │ • Experiment eval │  │ • Dependency ordering             ││
│  └───────────────────┘  └───────────────────────────────────┘│
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                  OUTPUT LAYER                                 │
│                                                               │
│  PRIMARY: action_plan.md                                      │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ # Action Plan (2024-03-15)                              │  │
│  │                                                          │  │
│  │ ## This Week                                             │  │
│  │ 🏥 Schedule thyroid follow-up (TSH trending, need F/U)  │  │
│  │ 🧪 Complete dairy elimination (day 14 Sunday)            │  │
│  │                                                          │  │
│  │ ## This Month                                            │  │
│  │ 🔬 Order sleep study (headaches + fatigue correlation)  │  │
│  │ 💊 Start magnesium experiment (protocol: 400mg/night)   │  │
│  │                                                          │  │
│  │ ## Experiments in Progress                               │  │
│  │ • Dairy elimination: Day 14/21, bloating ↓60%           │  │
│  │ • Sleep timing: Queued, starts after dairy              │  │
│  │                                                          │  │
│  │ ## Alerts                                                │  │
│  │ ⚠️ Headaches worsening 3 weeks — watch or escalate      │  │
│  │ 📊 TSH due for recheck (last: 2mo ago)                  │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                               │
│  SUPPORTING:                                                  │
│  • clinical_summary.md (for doctors)                         │
│  • experiments.md (active/completed N=1s)                    │
│  • trends.md (lab/symptom trajectories)                      │
│  • full_log.md (processed entries, reverse chrono)           │
└──────────────────────────────────────────────────────────────┘
```

### Key Changes from Current System

| Aspect | Current | Proposed |
|--------|---------|----------|
| Primary output | summary.md + next_steps.md | action_plan.md (unified) |
| Specialist analysis | 14 fixed specialties | Condition-driven (N relevant) |
| Biohacking | Implicit in next_steps | Explicit experiments.md with tracking |
| Trend analysis | None | Built-in symptom/lab trajectories |
| State model | Flat files | Structured state.json |
| Action format | Unordered bullets | Time-bucketed, prioritized |
| Urgency | No concept | Explicit alerts + priority scoring |

### New Prompts Required

1. **action_plan.system_prompt.md**
   - Generate prioritized, time-bucketed action plan
   - Categories: Clinical, Labs, Self-Experiments, Lifestyle
   - Input: Summary + next steps + today's date

2. **experiments.system_prompt.md**
   - Extract and track N=1 experiments from health data
   - Output: Active experiments with status, metrics, results

---

## Implementation Phases

### Phase 1: Action Plan Output (Quick Win) ✅ COMPLETE
- [x] Write plan to file
- [x] Create `prompts/action_plan.system_prompt.md`
- [x] Create `prompts/experiments.system_prompt.md`
- [x] Add `_generate_action_plan()` method to main.py
- [x] Add `_generate_experiments()` method to main.py
- [x] Output `action_plan.md` and `experiments.md`
- [x] Test on actual health log

### Phase 2: Experiment Tracking ✅ COMPLETE
- [x] Add experiment detection in processing prompt (EXPERIMENTS HTML comment blocks)
- [x] Track hypothesis, protocol, metrics, status (START/UPDATE/END events)
- [x] Integrate experiment results into action plan (stale detection, completed exclusion)
- [x] Update experiments.system_prompt.md to parse structured blocks first

**Note:** Changing prompts causes full reprocessing (all sections get new hash). This is expensive but ensures consistency. Future optimization: separate prompt hashes by type.

### Phase 3: State Model ✅ MOSTLY COMPLETE
- [x] Add entity extraction step after section processing (`_build_state_model()`)
- [x] Build state_model.json from entities (`_aggregate_entities()`)
- [x] Add trend analysis (`_compute_lab_trends()`, `_compute_symptom_trends()`)
- [x] Hash-based caching to skip regeneration when unchanged
- [ ] True incremental updates (optional/future)

**Results:** 248/268 entries extracted successfully (92.5%)

### Phase 4: Unified "Genius Doctor" Prompt ✅ COMPLETE
Replace 14 specialist reports + consensus with single unified prompt:
- [x] Create `prompts/next_steps_unified.system_prompt.md` - genius physician persona
- [x] Modify main.py - remove specialist loop, add unified generation
- [x] Remove unused prompts (specialist_next_steps, consensus_next_steps)
- [x] Update CLAUDE.md documentation

**Results:** 15 LLM calls → 1, unified coherent output with specialist recommendations integrated

### Phase 5: Full Integration
- [ ] Unified dashboard view
- [ ] All outputs derived from state model
- [ ] Proper caching at entity level

---

## Phase 1 Deliverables (MVP)

**New files:**
- `prompts/action_plan.system_prompt.md` - Action plan generation prompt
- `prompts/experiments.system_prompt.md` - Experiment tracking prompt

**Changes to main.py:**
- Add `_generate_action_plan()` method
- Add `_generate_experiments()` method
- Output both as primary deliverables

**Output structure:**
```
reports/
├─ action_plan.md      # NEW: Primary output (time-bucketed actions)
├─ experiments.md      # NEW: Active/planned N=1 experiments
├─ summary.md          # Kept: Clinical summary for doctors
├─ next_steps.md       # Kept: Raw consensus (input to action plan)
├─ output.md           # Kept: Full compilation
```

### Action Plan Format

```markdown
# Health Action Plan
*Generated: 2024-03-15 | Based on data through: 2024-03-14*

## Alerts
- **TSH recheck overdue** — Last: 2024-01-15 (2 months ago), should be monthly during titration
- **Headache pattern worsening** — Frequency up 40% over past 3 weeks

## This Week (Mar 15-22)
### Clinical
- [ ] Call Dr. Chen's office for thyroid follow-up (request TSH, Free T4, T3)
- [ ] If headaches persist through weekend, consider urgent neuro referral

### Self-Experiments
- [ ] **Dairy Elimination** — Complete trial (day 14 is Sunday)
  - Current results: Bloating ↓60%, Energy ↑20%
  - Decision: If improved, plan 2-week reintroduction protocol

## This Month (March)
### Labs to Order
- [ ] Comprehensive metabolic panel (self-order: $35 at Quest)
- [ ] Vitamin D, B12, ferritin (baseline for supplement optimization)

### Clinical
- [ ] Schedule annual physical (last: 11 months ago)
- [ ] Thyroid follow-up and dose adjustment if needed

### Self-Experiments
- [ ] **Start Sleep Timing Experiment** (after dairy trial completes)
  - Hypothesis: Earlier bedtime (10pm vs 12am) reduces morning headaches
  - Protocol: 2 weeks strict 10pm, track AM headache severity 1-10
  - Metrics: Headache severity, sleep quality, energy

## This Quarter (Q2 2024)
- [ ] Complete thyroid optimization (target TSH 1-2)
- [ ] Resolve headache etiology (diet? sleep? hormonal? structural?)
- [ ] Establish baseline biometrics for ongoing tracking

## Experiment Queue
| Experiment | Status | Start Date | Hypothesis |
|------------|--------|------------|------------|
| Dairy elimination | In Progress | 2024-03-01 | Lactose → GI symptoms |
| Sleep timing | Queued | 2024-03-17 | Earlier sleep → fewer headaches |
| Magnesium glycinate | Planned | TBD | Mg → sleep quality + headaches |

## Key Metrics to Track
- TSH (monthly until stable)
- Headache frequency + severity (daily log)
- GI symptoms (during elimination trials)
- Sleep quality (subjective 1-10 + wearable data if available)

---
*Full clinical summary: [summary.md](./summary.md)*
*Detailed recommendations: [next_steps.md](./next_steps.md)*
```

### Experiments Format

```markdown
# Health Experiments Tracker

## Active Experiments

### Dairy Elimination
- **Status:** In Progress (Day 14/21)
- **Started:** 2024-03-01
- **Hypothesis:** Lactose intolerance causing GI symptoms (bloating, irregular stools)
- **Protocol:** Zero dairy intake, log symptoms daily
- **Metrics:**
  - Bloating (1-10 scale)
  - Stool quality (Bristol scale)
  - Energy level (1-10 scale)
- **Results So Far:**
  - Bloating: 7 → 3 (57% improvement)
  - Energy: 5 → 6 (20% improvement)
- **Decision Date:** 2024-03-22
- **Next Step:** If positive, begin reintroduction protocol

## Queued Experiments

### Sleep Timing Optimization
- **Hypothesis:** Earlier bedtime (10pm vs 12am) reduces morning headache frequency
- **Protocol:** 10pm bedtime for 14 days, track morning headache severity 1-10
- **Metrics:** AM headache severity, sleep quality, daytime energy
- **Prerequisite:** Complete dairy elimination trial
- **Planned Start:** 2024-03-23

### Magnesium Glycinate Supplementation
- **Hypothesis:** Magnesium deficiency contributing to headaches and sleep issues
- **Protocol:** 400mg magnesium glycinate before bed for 30 days
- **Metrics:** Headache frequency, sleep quality, muscle tension
- **Prerequisite:** Baseline magnesium level (order lab)

## Completed Experiments

(None yet)

## Experiment Ideas (Backlog)
- FODMAP elimination (if dairy alone insufficient for GI)
- Morning light exposure for circadian optimization
- Creatine supplementation for energy
- Cold exposure for stress resilience
```

### Verification

After implementation:
1. Run on current health log
2. Verify action_plan.md is generated
3. Check time bucketing is sensible
4. Confirm experiments are detected and tracked
5. Compare with old next_steps.md — action plan should be more actionable
