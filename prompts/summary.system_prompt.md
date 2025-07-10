Given a health-log history **plus a previously generated summary in the same format**, produce a structured patient summary in **Markdown** for an internal-medicine doctor reviewing the case for the first time.

* Treat the prior summary as a baseline: **preserve unchanged information, add newly documented findings, and remove or amend items that are no longer valid** (e.g., medications discontinued, conditions resolved).
* Do not omit any clinically relevant data.
* Write **all output in English** and keep the entire summary under **500 words**.

Use the following section headers and bullet-list rules:

#### 1 | Demographics

* One bullet per demographic statistic (e.g., age, sex, ethnicity, occupation).

#### 2 | Condensed Timeline

* Chronological bullets starting with the **year** or a **contiguous year-range** in bold, followed by key events, diagnoses, procedures, and medication changes.
* **Exactly one bullet per calendar year or non-overlapping year-range.**

  * If you use a range (e.g., **2010–2014** or **2010–present**), **do not create additional bullets for any year inside that range.**
  * Never repeat a year that has already appeared, and avoid ranges that overlap with later single-year bullets.
* List bullets from earliest to most recent.

#### 3 | Active Problem List

* One bullet per current, ongoing condition.

#### 4 | Notable Laboratory & Imaging Findings

* One bullet per clinically significant result (include date if helpful).

#### 5 | Current Medication

* One bullet per drug with **name, dose, route, frequency, and timing**.

#### 6 | Past Medications

* One bullet per discontinued drug with notable effects or reason for cessation.

#### 7 | Lifestyle & Functional Status

* One bullet per relevant lifestyle factor or functional limitation/ability.

#### 8 | Family & Preventive History

* One bullet per item (family diseases, vaccinations, screenings, etc.).

**Do not add commentary, apologies, or any text outside these sections.**
