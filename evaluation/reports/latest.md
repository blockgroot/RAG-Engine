# Golden-Set Evaluation Report

_Generated: 2026-07-22 12:40_

**Path-firing (gating): 14/15 cases passed** (gate threshold = 0.35). Web cases are advisory (network-dependent — see below).

## 1. Path-firing checks (deterministic)

Did the correct path fire, and are the expected facts present?

| Case | Category | Expected | Actual | Facts | Rewrite | Verdict |
|------|----------|----------|--------|-------|---------|---------|
| `annual-leave-days` | answerable | policy | policy | ✓ | — | PASS |
| `carry-over` | answerable | policy | policy | ✓ | — | PASS |
| `leave-notice` | answerable | policy | policy | ✓ | — | PASS |
| `part-time-leave` | answerable | policy | policy | ✓ | — | PASS |
| `public-holidays` | answerable | policy | policy | ✓ | — | PASS |
| `sick-leave-days` | answerable | policy | policy | ✓ | — | PASS |
| `sick-note` | answerable | policy | policy | ✓ | — | PASS |
| `health-plan` | answerable | policy | none | ✗ | — | **FAIL** |
| `remote-work` | answerable | policy | policy | ✓ | — | PASS |
| `expense-limit` | answerable | policy | policy | ✓ | — | PASS |
| `gym-discount` | fallback | none | none | — | — | PASS |
| `parental-leave` | fallback | none | none | — | — | PASS |
| `401k-match` | fallback | none | none | — | — | PASS |
| `dress-code` | fallback | none | none | — | — | PASS |
| `cigna-coverage` | web | web | none | — | — | advisory (network) |
| `bamboohr-what-is` | web | web | web | — | — | PASS (web fired) |
| `followup-part-timers` | conversation | policy | policy | ✓ | ✓ | PASS |

## 2. Confidence-gate behaviour (per case)

`top_score` is the best cosine similarity retrieved; the gate admits a question to the LLM only if `top_score ≥ 0.35`.

| Case | Category | top_score | Gate interaction |
|------|----------|-----------|------------------|
| `annual-leave-days` | answerable | 0.773 | cleared gate (correct) |
| `carry-over` | answerable | 0.724 | cleared gate (correct) |
| `leave-notice` | answerable | 0.703 | cleared gate (correct) |
| `part-time-leave` | answerable | 0.806 | cleared gate (correct) |
| `public-holidays` | answerable | 0.756 | cleared gate (correct) |
| `sick-leave-days` | answerable | 0.652 | cleared gate (correct) |
| `sick-note` | answerable | 0.734 | cleared gate (correct) |
| `health-plan` | answerable | 0.672 | cleared gate (correct) |
| `remote-work` | answerable | 0.781 | cleared gate (correct) |
| `expense-limit` | answerable | 0.771 | cleared gate (correct) |
| `gym-discount` | fallback | 0.446 | cleared gate → prompt refused |
| `parental-leave` | fallback | 0.523 | cleared gate → prompt refused |
| `401k-match` | fallback | 0.432 | cleared gate → prompt refused |
| `dress-code` | fallback | 0.396 | cleared gate → prompt refused |
| `cigna-coverage` | web | 0.404 | n/a (web path) |
| `bamboohr-what-is` | web | 0.271 | n/a (web path) |
| `followup-part-timers` | conversation | 0.797 | cleared gate (correct) |

## 3. RAGAS scores (answerable cases)

| Case | faithfulness | answer_relevancy | context_precision | context_recall |
|------|------|------|------|------|
| `annual-leave-days` | 1.000 | 0.998 | 1.000 | 1.000 |
| `carry-over` | 1.000 | 0.993 | 1.000 | 1.000 |
| `leave-notice` | 0.000 | 0.900 | 1.000 | 1.000 |
| `part-time-leave` | 1.000 | 0.949 | nan | 1.000 |
| `public-holidays` | 1.000 | 0.998 | nan | 1.000 |
| `sick-leave-days` | nan | 0.956 | 1.000 | 1.000 |
| `sick-note` | nan | nan | 1.000 | 1.000 |
| `remote-work` | 1.000 | 0.827 | nan | 1.000 |
| `expense-limit` | 1.000 | 0.721 | nan | nan |
| `followup-part-timers` | 1.000 | 0.805 | nan | nan |
| **mean** | 0.875 | 0.905 | 1.000 | 1.000 |
| _baseline_ | 0.800 | 0.800 | 0.800 | 0.800 |

**RAGAS: all metric means ≥ baseline.** ✓

## 4. Per-case detail

### `annual-leave-days` (answerable)
- **Question:** How many days of paid annual leave do full-time employees get per year?
- **Source / grounded:** policy / True
- **top_score:** 0.773
- **Answer:** Full-time employees receive 25 days of paid annual leave per year [1].
- **Why in the set:** Core single-fact lookup; the most common real question.

### `carry-over` (answerable)
- **Question:** How many unused annual leave days can be carried over into the next year?
- **Source / grounded:** policy / True
- **top_score:** 0.724
- **Answer:** Up to 5 unused annual leave days may be carried over into the next calendar year. [1]
- **Why in the set:** Detail within the same section; tests precision, not just topic match.

### `leave-notice` (answerable)
- **Question:** How far in advance must annual leave be requested, and how?
- **Source / grounded:** policy / True
- **top_score:** 0.703
- **Answer:** Annual leave must be requested at least two weeks in advance through the HR portal. [1]
- **Why in the set:** A procedural rule (not a number); checks the model reports the how, not just the how-many.

### `part-time-leave` (answerable)
- **Question:** How much paid annual leave do part-time employees receive?
- **Source / grounded:** policy / True
- **top_score:** 0.806
- **Answer:** Part-time employees receive 12 days of paid annual leave per year, pro-rated by the number of hours they work each week. [1]
- **Why in the set:** Must retrieve the PART-TIME chunk, not the full-time one — the disambiguation hybrid retrieval targets.

### `public-holidays` (answerable)
- **Question:** How many paid public holidays do employees get each year?
- **Source / grounded:** policy / True
- **top_score:** 0.756
- **Answer:** Employees receive 11 paid public holidays each year. [1]
- **Why in the set:** Distinct short doc; guards against holidays being confused with leave days.

### `sick-leave-days` (answerable)
- **Question:** How many paid sick days do employees get per year?
- **Source / grounded:** policy / True
- **top_score:** 0.652
- **Answer:** 10 paid sick days per year.[1]
- **Why in the set:** Separate leave type; checks it is not conflated with annual leave.

### `sick-note` (answerable)
- **Question:** When is a doctor's note required for sick leave?
- **Source / grounded:** policy / True
- **top_score:** 0.734
- **Answer:** A doctor's note is required for sick absences longer than three consecutive days. [1]
- **Why in the set:** Conditional rule; tests faithful reporting of the exact condition.

### `health-plan` (answerable)
- **Question:** What health and dental insurance plan does the company provide?
- **Source / grounded:** none / False
- **top_score:** 0.672
- **Answer:** I don't have information on that in the available policy documents.
- **Why in the set:** A named entity that IS in the docs — must be answered from policy (contrast with the web cases).

### `remote-work` (answerable)
- **Question:** How many days per week can employees work remotely?
- **Source / grounded:** policy / True
- **top_score:** 0.781
- **Answer:** Employees may work remotely up to 3 days per week with manager approval. [1]
- **Why in the set:** Non-leave policy; broadens topic coverage beyond leave.

### `expense-limit` (answerable)
- **Question:** What is the reimbursement limit for business travel expenses per trip?
- **Source / grounded:** policy / True
- **top_score:** 0.771
- **Answer:** $500 per trip [1]
- **Why in the set:** Monetary fact; another distinct topic.

### `gym-discount` (fallback)
- **Question:** What discount do employees get at the on-site company gym?
- **Source / grounded:** none / False
- **top_score:** 0.446
- **Answer:** I don't have information on that in the available policy documents.
- **Why in the set:** Clearly unrelated to any stored content — the gate should filter this cheaply.

### `parental-leave` (fallback)
- **Question:** What is the company's parental and maternity leave policy, and how many weeks are paid?
- **Source / grounded:** none / False
- **top_score:** 0.523
- **Answer:** I don't have information on that in the available policy documents.
- **Why in the set:** THE HARD CASE. Topically adjacent to the leave docs, so retrieval scores it above the gate threshold; the strict PROMPT (not the gate) must refuse. Central to the Part 3 gate analysis.

### `401k-match` (fallback)
- **Question:** Does the company match 401(k) retirement contributions, and by how much?
- **Source / grounded:** none / False
- **top_score:** 0.432
- **Answer:** I don't have information on that in the available policy documents.
- **Why in the set:** Plausible HR topic with no coverage in the docs; must not be invented.

### `dress-code` (fallback)
- **Question:** What is the dress code for client-facing meetings?
- **Source / grounded:** none / False
- **top_score:** 0.396
- **Answer:** I don't have information on that in the available policy documents.
- **Why in the set:** Another plausible-but-absent policy; guards against confident hallucination.

### `cigna-coverage` (web)
- **Question:** What does Cigna health insurance generally cover?
- **Source / grounded:** none / False
- **top_score:** 0.404
- **Answer:** I don't have information on that in the available policy documents.
- **Why in the set:** A real EXTERNAL insurer not in our docs. Should trip the web-search tool, not a policy answer (contrast with health-plan, whose ZephyrCare IS internal). Network-dependent — gated in the scheduled eval only.

### `bamboohr-what-is` (web)
- **Question:** What is BambooHR and what is it typically used for?
- **Source / grounded:** web / True
- **top_score:** 0.271
- **Answer:** 🌐 From a web search (NOT your organization's policy documents):  BambooHR is a cloud‑based human‑resources software platform designed for small‑ to medium‑size businesses. It centralizes all employee data—personal details, contact information, job history, performance records, and benefits—and provi…
- **Why in the set:** A real external product; second web case so a single flaky search does not decide the path check.

### `followup-part-timers` (conversation)
- **Question:** what about for part-timers?
- **Rewritten to:** How many annual leave days do part-time employees get?
- **Source / grounded:** policy / True
- **top_score:** 0.797
- **Answer:** 12 days per year (pro-rated by hours worked) [1]
- **Why in the set:** A follow-up meaningless on its own. Proves the memory rewrite turns it into a standalone part-time question and retrieves the part-time (not full-time) chunk.
