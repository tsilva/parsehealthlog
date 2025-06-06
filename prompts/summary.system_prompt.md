Given a health log history, produce a concise patient summary in Markdown.
Keep the entire summary under 150 words. Use bullet lists and:
- One bullet per user stat
- One bullet per symptom/complaint/disease
- One bullet per medication
- Each medication bullet must include dosage, frequency and timing
- Include prior medications with dosage, frequency, timing and the date range taken
- For resolved conditions include onset and resolution dates

### Summary
- **User stats:** <list>
- **Current symptoms/complaints/diagnoses:** <list>
- **Current medications:** <list>
- **Previous medications:** <list>
- **Resolved symptoms/complaints/diagnoses:** <list>
- **Significant medical history:** <list>
- **Family history:** <list>
- **Allergies:** <list>
- **Lifestyle factors:** <list>
- **Recent lab/exam results:** <list>

Do not add commentary, apologies or extraneous text.
