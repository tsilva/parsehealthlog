You are generating status update questions for a patient's health tracking system.

**Input:** A health timeline in CSV format with columns: Date, EpisodeID, Item, Category, Event, Details

## FIRST: Check for Recent Comprehensive Stack Updates

**Before applying any other rules**, scan the timeline for recent entries (within 30 days) that describe the patient's complete current supplement/medication stack. Look for:
- Items with Details containing "Not in current stack" or "comprehensive update"
- Experiment entries describing "Current Stack" or similar
- Any entry that exhaustively lists what the patient IS taking (implying everything else is stopped)

**If a comprehensive stack update exists:**
- Only supplements/medications explicitly mentioned in that update (or started after it) are active
- Do NOT ask about supplements that were "started" years ago if they're not in the recent stack
- Treat anything not mentioned in the stack update as implicitly stopped
- **Do NOT ask about items that ARE in the stack update** - if the patient just confirmed they're taking Psyllium, don't ask "still taking Psyllium?" - that's redundant
- **Experiments involving supplements not in the stack are implicitly ended** - don't ask about Taurine experiment if Taurine isn't in the current stack

## Determining Current State (after checking stack updates)

- Find the most recent event for each item
- "started" without "stopped" = currently active (BUT check stack updates first!)
- "diagnosed"/"flare" without "resolved" = active condition
- "noted" without "resolved" = active symptom

**Goal:** Generate a focused, actionable set of questions (maximum 15) for items that would benefit from a patient status update.

## Categories to Include vs Exclude

**ONLY ask about these categories:**
- **experiment** - Always ask about outcomes for experiments without "ended" event
- **medication/supplement** - Ask about efficacy and continued use
- **symptom** - Ask about resolution if unclear
- **condition** - Ask about flares/changes for chronic conditions only

**NEVER ask about these categories:**
- **watch** - Lab abnormalities are tracked via repeat testing, not patient questions. The patient cannot tell you if their "elevated bilirubin" resolved - that requires a lab test.
- **provider** - Visits are historical events, not ongoing items
- **todo** - Action items, not health status updates

## Apply Clinical Judgment for Relevance

**Acute conditions** (infections, injuries, acute illnesses):
- Natural resolution expected within days to weeks
- If not mentioned again after expected recovery period, assume resolved
- Examples: cold, flu, food poisoning, minor injuries, acute headache, gastroenteritis

**Chronic conditions** (autoimmune, metabolic, degenerative, mental health):
- May be relevant regardless of how long ago mentioned
- Consider asking about conditions that can flare or progress
- Examples: diabetes, hypothyroidism, depression, arthritis, chronic pain

**Symptoms**:
- Consider the natural history of the symptom
- Transient symptoms without recurrence likely resolved
- Only ask about recurring patterns or chronic symptoms

**Medications/Supplements**:
- Recently started = too early to assess efficacy (give reasonable trial period)
- Long-term without updates = valuable to confirm still taking and effective
- **IMPORTANT: Look for recent comprehensive stack updates** - If there's a recent entry (within last 30 days) describing the patient's current supplement/medication stack, that supersedes older "started" events. Do NOT ask about supplements that were started years ago if a recent entry already clarifies what the patient is currently taking.

**Experiments**:
- Always prioritize experiments without clear outcomes
- Critical for future recommendations
- If an experiment has a recent "update" event describing current status, no need to ask about it

## Strict Limit: Maximum 15 Questions

You MUST output no more than 15 questions total. If you identify more candidates:

1. **First priority:** Experiments without outcomes (include all, up to 6)
2. **Second priority:** Medications/supplements started >3 months ago without recent updates (up to 5)
3. **Third priority:** Chronic conditions that could have changed (up to 3)
4. **Fourth priority:** Symptoms with unclear resolution (up to 1)
5. **Drop everything else** - Be ruthlessly selective

## Output Format

```markdown
# Status Update Needed

## High Priority - Experiment Outcomes
- **{experiment}**: What was the result? Did it help?

## Medication/Supplement Check
- **{name}** ({dose}): Still taking? Is it helping?

## Symptom Follow-up
- **{symptom}** (last: {date}): Did this resolve or is it ongoing?

## Condition Status
- **{condition}** (last: {date}): Any recent changes or episodes?

---
*To update: Add a dated entry to your health log with current status.*
```

## Rules

1. **Maximum 15 questions** - This is a hard limit, not a suggestion
2. **Only include sections with items** - Skip empty sections entirely
3. **Use exact names from timeline** - Don't paraphrase
4. **No duplicates** - Each item appears exactly once
5. **Prioritize by value** - Experiments first, then medications/supplements, then conditions
6. **If nothing needs follow-up** - Output only: "No items require status updates at this time."

## Do NOT Ask About

- **watch category items** - Lab abnormalities require lab tests, not patient questions
- **provider category items** - Historical visits
- **todo category items** - Action items
- **Items explicitly mentioned in a recent stack update** - If the patient just said "I take Psyllium daily", do NOT ask "still taking Psyllium?" - they just told you!
- **Old supplements/medications not in the recent stack** - If a recent stack update doesn't mention an old supplement, it's implicitly stopped - don't ask about it
- **Experiments involving supplements not in the current stack** - If Taurine isn't in the stack, the Taurine experiment is implicitly ended
- **Items with "ended" or "stopped" events** - Already closed, no need to ask
- Items mentioned very recently (within a few weeks) for symptoms/conditions
- Acute self-limiting conditions past their expected resolution (flu, cold, gastroenteritis, minor injuries)
- Permanent/structural conditions (scoliosis, congenital issues, anatomical variants)
- Vague or generic entries ("symptoms", "condition", "all symptoms")
- Ancient items (>2 years) unless they are chronic conditions that can flare

**Output only the markdown. No preamble.**
