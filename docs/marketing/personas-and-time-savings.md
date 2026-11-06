# Handbook: personas and time saved

Every number below is tagged:

- **[Measured]**: we ran it ourselves on 6 Nov 2026 (method in §5).
- **[Cited]**: published research, linked in §6.
- **[Assumed]**: our input. It is stated plainly so you can change it.

---

## 1. The problem, in numbers

| Fact | Figure | Tag |
|---|---|---|
| Share of the work week spent looking for information | **19%**, about **8.8 h of a 46.5 h week** | [Cited] McKinsey Global Institute, 2012 |
| Share of time teams spend searching for answers | **about 25%** (survey of 12,000 knowledge workers) | [Cited] Atlassian, State of Teams 2025 |
| Hours a week UK knowledge workers spend searching | **9 h** | [Cited] Asana, Anatomy of Work 2024 (UK) |
| Average time before interrupted work is picked up again | **23 min 15 s** | [Cited] Gloria Mark, UC Irvine |

In short, people lose roughly a day a week to looking things up. Every "quick question" sent to a colleague can also cost that colleague up to 23 minutes of focus.

---

## 2. What Handbook does about it [Measured]

We asked 20 real questions on a live test workspace connected to Notion, Google Drive, Slack, Linear and GitHub. Answers came from:

- 34 documents and 220 text passages
- 12,707 spreadsheet rows
- 245 tool activity records

Each question was timed from pressing Enter to the last word.

| Mode | Questions | Median time to first words | Median full answer | Range |
|---|---|---|---|---|
| Ask (normal question) | 12 | 5.1 s | **9.1 s** | 5.1 – 25.4 s |
| Deep analysis | 2 | 6.1 s | **16.0 s** | 8.1 – 23.8 s |
| Chart | 6 | 4.2 s | **4.8 s** | 3.9 – 11.2 s |
| **All** | **20** | | **8.3 s** | 3.9 – 25.4 s |

- **Answer rate:** 16 of 20 (**80%**) were answered correctly with a source, or drawn as a chart.
- **The 4 misses:** Handbook said it couldn't answer and never made something up. The list is in §5.
- **Sources:** every Ask answer that found something linked to the exact page, issue, thread or sheet it used.

---

## 3. Time saved: the calculation

### 3a. Per person, per year

| Step | Value | Tag |
|---|---|---|
| Hours a week spent searching | 8.8 h | [Cited] McKinsey, the lowest of the three studies |
| Share of that search Handbook can take over (facts, docs and status that live in connected tools) | 30% | [Assumed], conservative |
| Share Handbook answers correctly | 80% | [Measured] |
| **Hours saved per person per week** | 8.8 × 0.30 × 0.80 = **2.1 h** | |
| Working weeks a year | 46 | [Assumed] |
| **Hours saved per person per year** | **≈ 97 h**, about 2½ work weeks | |

The time spent asking Handbook (about 8 s per question) is small next to this and is left out.

### 3b. By team size (30% share)

| Team size | Hours saved per year | Full-time people freed (1,840 h per year each) |
|---|---|---|
| 25 | 2,430 h | 1.3 |
| 50 | 4,860 h | 2.6 |
| **100** | **9,715 h** | **5.3** |
| 250 | 24,290 h | 13.2 |

### 3c. If our 30% guess is wrong (100 people)

| Share Handbook takes over | Hours saved per person per week | Hours saved per year | Full-time people freed |
|---|---|---|---|
| 10% (very cautious) | 0.7 h | 3,238 h | 1.8 |
| **30% (used above)** | **2.1 h** | **9,715 h** | **5.3** |
| 50% | 3.5 h | 16,192 h | 8.8 |

**To convert to money:** multiply the hours saved per year by your loaded hourly cost. For example, 9,715 h × ₹1,000 per hour is about ₹97 lakh a year.

### 3d. The cost to the person who answers

Each question a colleague answers by hand can cost them up to **23 min 15 s** before they are back on their own work [Cited].

- An HR or ops lead who answers **10 such pings a week** [Assumed] loses up to **3.9 h a week** to them.
- Handbook's Slack bot answers in the channel, with the source, in a median of **9 s** [Measured]. The lead only sees what the bot couldn't answer.

---

## 4. Personas and their stories

Each story uses a real, timed question from §5.

### Persona 1: Ops / HR lead, "the human FAQ"
- **Who:** runs people operations at a 50–300 person company. Policies live in Notion and Drive.
- **Pain:** answers the same questions on Slack every week, and each one breaks their focus (§3d).
- **What sells it:**
  - The bot answers in Slack with a link to the source.
  - The **"What we couldn't answer"** page shows the topics people asked that the docs don't cover yet, so they fix the docs instead of answering again.
- **Story:** someone asks *"What action items came out of the Engineering Weekly Team Sync on October 16?"* Handbook lists all **7 action items, with owners and status**, in **11.9 s**, linked to the meeting page. No one had to be pinged.

### Persona 2: Engineering manager, "the status chaser"
- **Who:** leads a team across Linear, GitHub and Slack.
- **Pain:** before each standup or review, they open several tools to work out what's happening.
- **What sells it:**
  - Ask for status in plain words.
  - Charts of Linear and GitHub activity.
  - Scheduled reports that arrive on their own.
- **Story:**
  - *"What issues are in the Engineering team's backlog?"* listed both issues with status and priority in **8.1 s**.
  - *"Stacked bar chart of tasks completed per month in Linear, split by assignee"* was drawn in **3.9 s**. Manually, this means exporting from Linear and building a pivot table.

### Persona 3: Sales / recruiting lead, "the spreadsheet digger"
- **Who:** runs the pipeline from large Google Sheets.
- **Pain:** answering one number means filtering thousands of rows across tabs.
- **What sells it:** questions about sheet numbers are counted directly from the sheet's rows by the database, not guessed by AI.
- **Story:** *"How many leads are in the Recruit Lead Tracker for November 2026?"* searched a **12,707-row workbook** across 3 monthly tabs. It returned the November count per recruiter (about 1,240–1,270 each) in **8.4 s**.

### Persona 4: Founder / leadership, "the one-question reader"
- **Who:** wants the answer, not the tool.
- **Pain:** waits on someone else to pull numbers before a decision.
- **What sells it:** charts and Deep analysis in seconds, every answer with its source, and access rules kept per person.
- **Story:**
  - *"Average first-response time in the Customer Support Performance Update?"* answered **18 → 14 → 11 minutes (Jan–Mar)** with the source in **5.6 s**.
  - The same figures as a chart took **4.8 s**.

### Persona 5 (buyer, not a daily user): IT / security admin
- **Pain:** fear of one team seeing another's data, or of data leaving the company.
- **What sells it:**
  - Each person only gets answers from documents they can already open.
  - Spaces keep team data apart.
  - It can be self-hosted.
  - ID numbers (PAN, Aadhaar, cards, phone numbers) are masked before any text leaves.
- **Proof point:** when Handbook can't find something, it says so instead of guessing. All 4 misses in our test did exactly that (§5).

---

## 5. Method and raw results [Measured]

- **When:** 6 Nov 2026.
- **Setup:** Handbook running locally against the test workspace's database. Questions were sent one after another through the same streaming endpoint the web app uses.
- **What was asked:** the app's own suggested questions plus everyday follow-ups.
- **Timing:** wall clock from send to the last word.

**Limits:**
- This is a small test workspace (34 documents). Larger workspaces may be slower or faster.
- Each question was run once.
- Times include the AI model's own response time, which varies.

| # | Mode | Question (shortened) | First words | Total | Result |
|---|---|---|---|---|---|
| 1 | Ask | What does "Engineering Weekly Team Sync" cover? | 6.1 s | 14.4 s | ✅ 1 source |
| 2 | Ask | What does "Recruit Lead Tracker 2026" cover? | 6.4 s | 12.9 s | ✅ 1 source |
| 3 | Ask | What was discussed in #social recently? | 15.7 s | 20.7 s | ✅ 1 source |
| 4 | Ask | ENG-1 current status? | 5.0 s | 6.3 s | ✅ 1 source |
| 5 | Ask | What does the Chain-Guard repository do? | 3.8 s | 5.1 s | ❌ repo not in the connected installation; said so |
| 6 | Ask | Key rules in "Webhook Testing Page"? | 5.2 s | 8.8 s | ✅ 1 source |
| 7 | Deep | Every rule, exception, deadline in "Webhook Testing Page" | 4.8 s | 8.1 s | ✅ correctly said the page has none |
| 8 | Chart | Tickets resolved by period (Support update) | 4.2 s | 4.8 s | ✅ chart |
| 9 | Chart | Linear tasks completed per month by assignee | 3.6 s | 3.9 s | ✅ chart |
| 10 | Chart | Slack conversations by channel | 3.7 s | 3.9 s | ✅ chart |
| 11 | Ask | Leads in Recruit Lead Tracker for November 2026 | 5.5 s | 8.4 s | ✅ counted from 12,707 rows |
| 12 | Ask | Which lead status has the most leads? | 24.2 s | 25.4 s | ❌ suggested Deep analysis instead |
| 13 | Ask | Average first-response time (Support update) | 4.5 s | 5.6 s | ✅ 1 source |
| 14 | Ask | Action items from the Oct 16 team sync | 4.7 s | 11.9 s | ✅ 2 sources |
| 15 | Ask | Engineering team's backlog | 4.5 s | 8.1 s | ✅ 2 sources |
| 16 | Ask | Team's decision on the Slack public tunnel | 4.8 s | 9.3 s | ✅ 1 source |
| 17 | Deep | Team sync, section by section | 7.4 s | 23.8 s | ✅ 1 source |
| 18 | Chart | First-response time by period | 4.1 s | 4.8 s | ✅ chart |
| 19 | Chart | GitHub pull requests per month | 4.6 s | 5.4 s | ❌ not drawn |
| 20 | Chart | Leads by lead status | 8.1 s | 11.2 s | ❌ said it can't chart this, and why |

---

## 6. Sources

- McKinsey Global Institute, *The social economy* (2012): 19% of the work week, 8.8 of 46.5 h, spent searching and gathering information. Summary: https://www.mckinsey.com/capabilities/people-and-organization/our-insights/rethinking-knowledge-work-a-strategic-approach
  - Note: the often-quoted "1.8 hours a day" is not in the report; use 19% or 8.8 h.
- Atlassian, *State of Teams 2025*: about 25% of time spent searching for answers, 12,000 knowledge workers. https://www.atlassian.com/blog/innovation/how-the-atlassian-system-of-work-connects-distributed-teams and https://www.unleash.ai/artificial-intelligence/atlassian-ai-could-help-workers-stop-wasting-2-4-billion-hours-searching-for-information-every-year/
- Asana, *Anatomy of Work* (UK, 2024): 9 h a week searching for information. https://hrreview.co.uk/hr-news/strategy-news/over-half-of-uk-workers-time-spent-on-busywork-in-2024/377447
- Gloria Mark (UC Irvine), interview with Gallup: interrupted work is resumed after 23 min 15 s on average. https://news.gallup.com/businessjournal/23146/too-many-interruptions-work.aspx
