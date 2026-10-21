# Masking Personal Identifiers Before They Reach AI Services

*Draft for discussion. Nothing has been built yet. 21 Oct 2026.*
*Follows on from [2026-10-16-protecting-sensitive-data-from-ai.md](2026-10-16-protecting-sensitive-data-from-ai.md). Names are out of scope for this note.*

## Goal

Aadhaar numbers, bank account numbers and mobile numbers in company documents must never reach an outside AI service in readable form. Answers should still work as they do today.

**In scope:**

| Data | What it looks like |
|---|---|
| Aadhaar number | 12 digits, often written `2345 6789 0123`. The last digit is a check digit |
| Aadhaar Virtual ID | 16 digits, also with a check digit |
| PAN | 10 characters: 5 letters, 4 digits, 1 letter (`ABCDE1234F`) |
| Mobile number | 10 digits starting with 6–9, often with `+91` or `0` in front |
| Bank account number | 9 to 18 digits. **No standard format and no check digit** |
| IFSC code | 11 characters: 4 letters, `0`, 6 letters or digits (`HDFC0001234`) |
| Card number | 12 to 19 digits, with a check digit |
| UPI ID | `name@bank` or `9876543210@ybl` |

**Out of scope:** people's names, salary and other details. These were covered in the earlier note.

## How it works

Before any text leaves our servers, each of these numbers is replaced with a placeholder such as `AADHAAR_1`, `ACCOUNT_1` or `PHONE_1`. When the answer comes back, the real value is put back in. Or, if we decide so, only a masked version is shown, such as `XXXX XXXX 0123`. The mapping exists only for that one question and is never shared between companies.

## Frameworks and libraries

**What we have today:** nothing. The app has no code that detects or hides these numbers, and none of these libraries is installed.

### Options we can use

| Option | What it gives us | Size and speed | License | Feasibility |
|---|---|---|---|---|
| **python-stdnum** | Ready-made checks for Aadhaar, Virtual ID, PAN, GSTIN and voter ID (EPIC), plus card-number checks. Confirms a 12-digit number really is a valid Aadhaar (check digit, cannot start with 0 or 1) | Very small, pure Python, milliseconds | LGPL (fine for a hosted service) | **High.** Does exactly the India IDs we need |
| **phonenumbers** (Google's phone library) | Finds phone numbers in text, India included, with any spacing or `+91`/`0` prefix | Small, milliseconds | Apache-2.0 | **High.** Industry standard |
| **Our own patterns** (Python's built-in regex) | Bank account numbers, IFSC, UPI IDs. No library covers these | No extra install | n/a | **High for IFSC and UPI. Hard for bank accounts** (see Challenges) |
| **Microsoft Presidio** | A complete framework. India checks for Aadhaar, PAN, passport, voter ID, GSTIN (switched off by default, must be turned on), phones, cards. Can use "nearby words" like "A/c" to boost confidence. Can run without its heavy language model | Medium. Larger install than the two above | MIT | **Medium.** Works, but heavier than we need when names are out of scope |
| **OpenAI Privacy Filter** (open AI model, runs locally) | Detects account numbers, phones, emails and more using AI | Large model, slower, no published speed figures | Apache-2.0 | **Low for now.** Overkill for numbers with fixed formats |
| **Cloud services** (Google DLP, AWS Comprehend, Azure) | Detection as a service | Network call per question | Paid | **Not suitable.** We'd be sending the numbers to another outside company to find them, the opposite of the goal. AWS Comprehend also supports only English and Spanish |

**Not suitable:** LLM Guard and LangChain's anonymizer are both archived (no longer maintained).

### Recommendation

Build a **small detector of our own** using **python-stdnum + phonenumbers + a few patterns**. This is the lightest option, runs in milliseconds and needs no AI model. If we later want a ready-made framework with "nearby words" scoring, Presidio is the fallback, used without its language model.

The replace-and-restore step is ours to build either way. No maintained library does it.

**Why this option fits:**

- **No new outside company involved.** Detection runs inside our app. A cloud detection service would mean sending the numbers to another company just to find them.
- **No change to our current providers.** Gemini (and the other AI models companies can pick), Jina and Groq stay as they are. The masking sits in front of them, so no new contracts are needed.
- **No extra server capacity.** The libraries are small and fast. A heavier option (Presidio, or an AI-based detector) would need more memory on our hosting.

## How much of the app changes

This is **smaller than the names plan**. Numbers don't need the AI to understand them, and nobody searches documents by the meaning of an account number.

1. **One privacy layer at the AI gateway.** Every message to the AI already passes through one place in our code. Numbers are replaced on the way out and restored on the way back. Chat, live reads, charts, Agent mode and background jobs are all covered without changing them one by one.
2. **Search can stay with Jina.** Unlike names, we can replace numbers *before* sending text to Jina for search, with no real loss. Searching for an exact number still works, because our keyword search runs on our own servers on the real text. **We don't need to run our own search for this.**
3. **The safety checks (Groq)**, when switched on, go through the same layer.
4. **Logs:** the short question snippets we log are masked too.
5. **Tests** that fail if a known Aadhaar, account or phone number appears in anything sent out.

**What stays the same:** documents are still stored with real values in our own database. Permissions still decide who can see what. Charts are unaffected, because chart numbers are calculated by our database, not the AI.

## Effect on live features

- **Live reads** (Notion, Drive, Slack, Linear, GitHub): covered automatically, because the fresh text goes through the same layer.
- **Agent mode** (creating issues, sending messages): if a draft needs a phone or account number, the real value is restored before the person sees the card and before it's sent. Agent mode's existing check on where content may be posted still applies.
- **Slack bot:** if we restore full numbers, they appear in the Slack reply. This is one reason to consider showing masked numbers instead (see Decisions).
- **Speed:** pattern checks take milliseconds. No noticeable delay.

## Challenges

1. **Bank account numbers have no fixed format.** They are 9 to 18 plain digits with no check digit, the same shape as order IDs, invoice numbers, transaction IDs and timestamps. We must choose between two approaches:
   - **Only mask long numbers with an account label nearby** ("A/c", "account no.", "acct"). This gives fewer false alarms, but numbers written without a label slip through.
   - **Mask every long run of digits.** Safer, but it also hides order numbers and IDs people genuinely ask about, which makes some answers worse.
2. **Mobile numbers look like other numbers.** Any 10-digit number starting with 6–9 may be a phone number or an ID. The phone library helps, but some false alarms remain.
3. **Many ways of writing the same number:** spaces, dashes, dots, `+91-98765 43210`, `098765 43210`, or split across a table cell or line break. Each has to be tested.
4. **Check digits don't catch everything.** About 1 in 10 random 12-digit numbers passes the Aadhaar check, so some ordinary numbers will be masked. Nearby words help.
5. **Already-masked numbers** like `XXXX XXXX 0123` are safe and must be left alone, not counted as a match.
6. **The AI changing placeholders.** If it writes "Account 1" instead of `ACCOUNT_1`, we can't safely restore it, so that value is left out of the answer.
7. **Every exit must be covered:** the main AI, the background AI, Jina search and reranking, and Groq. Missing one undoes the rest, so tests must check every path.
8. **Past data has already been sent.** Text sent to the AI and Jina before this change can't be recalled. This only protects from now on.

## Limitations

- **Only recognisable formats are caught.** A number written in words ("nine eight seven…"), with letters mixed in, or inside an image is not detected.
- **It doesn't control what our own users see.** Someone allowed to open a document can still see the number. That's handled by permissions.
- **It's not a guarantee.** We should describe it as "reduces what outside AI services see", not "nothing sensitive ever leaves".
- **Names and context aren't hidden.** "Sana's account `ACCOUNT_1`" still tells the AI whose account it is, just not the number.

## Constraints

- **We don't control which AI model answers.** Companies and users can pick different AI models. Every one of them must copy placeholders like `ACCOUNT_1` back exactly, so each model we offer has to be tested with masking on.

## Suggested order

1. Build the detector (Aadhaar, Virtual ID, PAN, mobile, card, IFSC, UPI) and the privacy layer at the AI gateway, with leak tests.
2. Apply the same masking to Jina search and Groq.
3. Add bank account numbers once the team has picked an approach (label-only or all long numbers), tested against our own data.
4. Mask phone and ID numbers in logs.

## Decisions needed

1. **What should the answer show:** the full real number restored, or a masked version like `XXXX XXXX 0123`? Masked is safer for Slack replies, saved chat history and cached answers.
2. **Bank accounts:** mask only numbers labelled as accounts, or every long number?
3. **Always on, or a per-company switch?** We recommend always on, because the impact on answers is small.
4. **If detection fails** (an unexpected error), should the question be blocked with a clear error, or answered without masking? Blocking is safer; answering keeps the service running. We recommend blocking.

## Sources

- Presidio supported entities, India: https://presidio.dataprivacystack.org/supported_entities/
- python-stdnum (Aadhaar, Virtual ID, PAN, GSTIN, EPIC): https://pypi.org/project/python-stdnum/ · Virtual ID rules: https://arthurdejong.org/python-stdnum/doc/stdnum.in_.vid
- phonenumbers: https://pypi.org/project/phonenumbers/
- Bank account number lengths (9–18 digits, varies by bank): https://groww.in/banking/indian-bank-account-number · https://www.paisabazaar.com/banking/indian-bank-account-number/
- IFSC format: https://www.tutorialspoint.com/article/how-to-validate-ifsc-code-using-regular-expression · RBI committee report: https://website.rbi.org.in/documents/87730/39711208/TCEUR160113.pdf
- Aadhaar Virtual ID (16 digits, check digit): https://www.thenewsminute.com/article/plugging-aadhaar-leaks-here-how-new-aadhaar-virtual-id-will-work-74520
- AWS Comprehend language support: https://docs.aws.amazon.com/comprehend/latest/dg/realtime-pii-api.html
