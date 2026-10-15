# Keeping sensitive data away from the AI model

*Research only. Nothing has been built. 15 Oct 2026.*

## The idea

When someone asks a question, we send pieces of company documents to an outside AI
model. Those pieces can include people's names, emails, phone numbers, and sometimes
passwords or API keys someone pasted into a page.

The idea is to swap sensitive values for stand-ins (`PERSON_1`, `EMAIL_2`) before
sending, then swap the real values back into the answer.

## Short answer

- **Passwords and keys:** remove them for good. Never send them, never put them back.
Do this first.
- **Emails, phone numbers, ID numbers:** easy to detect reliably, so hiding them is
practical.
- **People's names:** don't hide them by default. It is unreliable and makes answers
worse. Offer it as an optional setting for companies that ask for it.
- Big products (Microsoft Copilot, Slack AI, Notion AI, Glean) don't hide names
either. They rely on strict permissions, and on contracts saying the AI company
keeps nothing and trains on nothing. Some run the AI model themselves.



## What we send today

- **The person asking:** we never send their name or email. We use them only to
decide which documents they can see.
- **Everyone else:** names are sent, inside document text. That includes Slack
authors, Linear assignees, Notion email and phone fields, GitHub authors, and a
"last edited by" line on every passage.
- **Passwords and keys** pasted into documents are stored and can be sent.
- **Nothing is filtered** for personal data or secrets today.
- **The AI model is not the only outside service:** our search provider (Jina)
receives every piece of text too.



## How live API calls are affected

We read Notion, Drive, Slack, Linear and GitHub **live** while answering, to get the
current version of a page or issue. This matters for any plan:

1. **Live text skips the "when we store it" step.** A password removed at storage
  time can still come back through a live read. So every check must also run on live
   text, on every question.
2. **It adds delay.** A live read already takes about 2 seconds. Scanning that text
  for names adds more time while the person waits.
3. **Writes need real values.** The Act feature creates issues and posts messages in
  these tools. It needs real names and @-mentions to work, so it can't use stand-ins.
4. **Live GitHub answers are about people.** "Who opened this PR?" or "what did
  Rahul commit?" are only useful with the real name.
5. The live reads already use stand-ins for document IDs. The model
  sees `[L1]` instead of an ID, and our code only accepts stand-ins it created in
   that request. Swapping names back would work the same way.



## Challenges with hiding names

1. **Detection misses names.** Tools admit they can't catch everything. Accuracy is
  worse for Indian names, mixed Hindi and English, and names that are ordinary words
   (Sana, Asha, Will). One miss and the name leaks while we believe it's protected.
2. **Answers get worse.** One 2026 study saw scores on document questions drop by
  more than half. Our product depends on linking facts about the same person across
   documents.
3. **One person becomes several.** "Sana", "Sana Asiwal", her email and "@sana" each
  get a different stand-in unless we match them first which can only be done by the user manually.
4. **The model changes stand-ins.** It may write "Person 1" or invent `PERSON_7`, and
  guessing what it meant can put the wrong name in the answer.
5. **Some questions are about people.** "What did Sana say?" or "who owns this?" need
  the name.
6. **The lookup table becomes a target.** It must be strictly separate for each
  company, or one company's stand-ins could turn into another company's names.
7. **It touches many parts of the app:** saved answers, chat history, link checks,
  live reads, Act and the Slack bot.



## Limits and constraints

- **No tool is complete.** We must call this "reduces risk", never "guaranteed".
- **No reliable test data on Indian names.** We would have to test on our own data.
- **Several outside services receive text:** the AI model, search, and the optional  
safety checks. Covering only the AI model does half the job.
- **Secrets must never be swapped back.** Putting a password into an answer shows it
in chat history, Slack and logs, to everyone who can see them.



## Recommendation


| Data                       | What to do                                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------------------------- |
| Passwords, keys, tokens    | Remove them when storing and on every live read. Tell an admin so the key can be changed. Never restore. |
| Emails, phones, ID numbers | Hide before sending, and restore in the answer or leave them out.                                        |
| People's names             | Not hidden by default. Optional per company, clearly labelled "best effort".                             |
| Outside services           | Show each company which services see their data. Offer in-house search and a self-hosted model.          |


## Sources

- Microsoft Presidio (detection tool): [https://presidio.dataprivacystack.org/faq/](https://presidio.dataprivacystack.org/faq/)
- LLM Guard (swap and swap back): [https://protectai.github.io/llm-guard/input_scanners/anonymize/](https://protectai.github.io/llm-guard/input_scanners/anonymize/)
- Study on answer quality: [https://arxiv.org/abs/2609.11335](https://arxiv.org/abs/2609.11335)
- OWASP, sensitive data in AI apps: [https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)
- Slack AI: [https://slack.engineering/how-we-built-slack-ai-to-be-secure-and-private/](https://slack.engineering/how-we-built-slack-ai-to-be-secure-and-private/)
- Microsoft 365 Copilot: [https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-privacy](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-privacy)
- Notion AI: [https://www.notion.com/help/notion-ai-security-practices](https://www.notion.com/help/notion-ai-security-practices)
- OpenAI data retention: [https://developers.openai.com/api/docs/guides/your-data](https://developers.openai.com/api/docs/guides/your-data)

