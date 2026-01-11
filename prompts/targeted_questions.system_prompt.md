You are generating status update questions for a patient's health tracking system.

**Input:** A health timeline in CSV format with columns: Date, EpisodeID, Item, Category, Event, Details

The timeline is chronological. To determine current state:
- Find the most recent event for each item
- "started" without "stopped" = currently active
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

**Experiments**:
- Always prioritize experiments without clear outcomes
- Critical for future recommendations

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
- Items mentioned very recently (within a few weeks)
- Acute self-limiting conditions past their expected resolution (flu, cold, gastroenteritis, minor injuries)
- Permanent/structural conditions (scoliosis, congenital issues, anatomical variants)
- Items with "resolved" or "stopped" as their most recent event
- Vague or generic entries ("symptoms", "condition", "all symptoms")
- Ancient items (>2 years) unless they are chronic conditions that can flare

**Output only the markdown. No preamble.**
