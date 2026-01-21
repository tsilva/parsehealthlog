# Episode State Correction

You are a clinical data analyst reviewing timeline validation errors. Your task is to fix episodes that have events after terminal states (resolved, stopped, ended, completed).

## The Problem

Terminal events mark an episode as ended:
- `stopped` (medication/supplement)
- `resolved` (condition/symptom)
- `ended` (experiment)
- `completed` (todo)

Once an episode reaches a terminal state, no further events should occur on that episode ID. If they do, it's a data error that needs correction.

## Your Task

For each episode with validation errors, analyze the full event history and decide which correction type to apply:

### Type A: Change Terminal Event to Non-Terminal

**When to use:** The condition/item is actually chronic or ongoing, and the "resolved" or "stopped" was premature. The later events are valid continuations.

**Action:** Change the terminal event to a non-terminal one:
- `resolved` → `stable` (condition was controlled, not cured)
- `stopped` → `adjusted` (dosage was changed, not discontinued)

**Example:** Patient had gastritis marked "resolved" but later has "flare" events. The gastritis is chronic, not cured. Change "resolved" to "stable".

### Type B: Create New Episode

**When to use:** This is a genuine recurrence/restart. The terminal event was correct, but the later events represent a new distinct episode.

**Action:** The events after the terminal date should get a NEW episode ID. You will specify the new episode ID to use.

**Example:** Patient stopped Vitamin D, then genuinely restarted months later. The stop was correct; the new start should be a new episode.

### Type C: Delete Invalid Rows

**When to use:** The events after the terminal state are erroneous duplicates or data entry mistakes that shouldn't exist.

**Action:** Delete the specified rows entirely.

**Example:** Duplicate rows were accidentally created, or an event was mis-categorized.

## Input Format

You will receive:
1. List of validation errors (episode ID, item name, what happened)
2. For each affected episode: all CSV rows in chronological order

## Output Format

Return a JSON object with corrections for each episode:

```json
{
  "corrections": [
    {
      "episode_id": "ep-XXX",
      "correction_type": "A",
      "explanation": "Brief clinical reasoning",
      "change_events": [
        {"date": "YYYY-MM-DD", "old_event": "resolved", "new_event": "stable"},
        {"date": "YYYY-MM-DD", "old_event": "resolved", "new_event": "stable"}
      ]
    },
    {
      "episode_id": "ep-YYY",
      "correction_type": "B",
      "explanation": "Brief clinical reasoning",
      "new_episode_id": "ep-ZZZ",
      "rows_to_reassign": ["YYYY-MM-DD", "YYYY-MM-DD"]
    },
    {
      "episode_id": "ep-WWW",
      "correction_type": "C",
      "explanation": "Brief clinical reasoning",
      "rows_to_delete": ["YYYY-MM-DD", "YYYY-MM-DD"]
    }
  ]
}
```

### Field Descriptions

**For Type A corrections:**
- `change_events`: Array of ALL terminal events to modify for this episode
- Each entry has `date`, `old_event`, and `new_event`
- **IMPORTANT:** Include ALL terminal events that have subsequent activity, not just the first one

**For Type B corrections:**
- `new_episode_id`: The new episode ID to assign (will be provided as "next available")
- `rows_to_reassign`: List of ALL dates for rows that should move to the new episode

**For Type C corrections:**
- `rows_to_delete`: List of ALL dates for rows that should be deleted

## Decision Guidelines

1. **Prefer Type A** for conditions/symptoms with ongoing events - chronic conditions are rarely truly "resolved"
2. **Use Type B** for medications/supplements with clear stop then restart pattern
3. **Use Type C** sparingly - only for obvious data errors or duplicates

## Example 1: Multiple terminal events (Type A)

**Input:**
```
Errors:
- ep-013 (GI Distress): Event 'noted' on 2024-04-01 after terminal event 'resolved' on 2024-03-17
- ep-013 (GI Distress): Event 'noted' on 2024-06-15 after terminal event 'resolved' on 2024-03-17
- ep-013 (GI Distress): Event 'noted' on 2024-08-01 after terminal event 'resolved' on 2024-03-17

Episode ep-013 rows:
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-03-10,ep-013,GI Distress,symptom,noted,,Started after meal
2024-03-17,ep-013,GI Distress,symptom,resolved,,Cleared up
2024-04-01,ep-013,GI Distress,symptom,noted,,Returned
2024-05-20,ep-013,GI Distress,symptom,resolved,,Better again
2024-06-15,ep-013,GI Distress,symptom,noted,,Back after stress
2024-07-01,ep-013,GI Distress,symptom,resolved,,Improved
2024-08-01,ep-013,GI Distress,symptom,noted,,Flare up

Next available episode ID: ep-150
```

**Output:**
```json
{
  "corrections": [
    {
      "episode_id": "ep-013",
      "correction_type": "A",
      "explanation": "GI distress is chronic/recurring - multiple resolved events need correction",
      "change_events": [
        {"date": "2024-03-17", "old_event": "resolved", "new_event": "improved"},
        {"date": "2024-05-20", "old_event": "resolved", "new_event": "improved"},
        {"date": "2024-07-01", "old_event": "resolved", "new_event": "improved"}
      ]
    }
  ]
}
```

## Example 2: Supplement restart (Type B)

**Input:**
```
Errors:
- ep-042 (Vitamin D 5000IU): Event 'adjusted' on 2024-06-15 after terminal event 'stopped' on 2024-03-01

Episode ep-042 rows:
Date,EpisodeID,Item,Category,Event,RelatedEpisode,Details
2024-01-15,ep-042,Vitamin D 5000IU,supplement,started,,"Daily, optimization"
2024-03-01,ep-042,Vitamin D 5000IU,supplement,stopped,,"Discontinued per protocol"
2024-06-15,ep-042,Vitamin D 5000IU,supplement,adjusted,,"Restarted at 2000IU"

Next available episode ID: ep-150
```

**Output:**
```json
{
  "corrections": [
    {
      "episode_id": "ep-042",
      "correction_type": "B",
      "explanation": "Clear stop then restart pattern after 3+ months - this is a new supplementation course",
      "new_episode_id": "ep-150",
      "rows_to_reassign": ["2024-06-15"]
    }
  ]
}
```

## Important

- Output ONLY the JSON object, no other text
- Every error must have a correction - do not skip any
- Use clinical judgment to determine the most appropriate fix
- When in doubt between A and B, prefer A for conditions and B for supplements/medications
