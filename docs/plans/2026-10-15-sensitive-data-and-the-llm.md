# Keeping sensitive data away from the AI model

*Research only, nothing built yet. Written 15 Oct 2026.*

## The question

When someone asks our assistant a question, we find the relevant pieces of company
documents and send them, along with the question, to an AI model (Gemini, OpenAI and
so on). Those pieces can contain people's names, emails, phone numbers, and sometimes
passwords or API keys that someone pasted into a page.

The idea we looked at: before sending anything to the model, swap each sensitive value
for a stand-in such as `PERSON_1` or `EMAIL_2`. The model writes its answer using the
stand-ins. We then swap the real values back in before showing the answer.

Is this the right approach, and how do real products handle it?

## Short answer

The idea is real and some tools do it, but it should not be our main protection.

- **Passwords and keys:** remove them when we first read the documents, and never put
them back. This is the most important change and has no downside.
- **Names:** most big products don't hide names from the model. They make sure each
person only sees documents they're allowed to see, and they sign contracts with the
AI provider saying the provider stores nothing and doesn't train on it. Some run the
model themselves.
- **Swapping in stand-ins:** worth offering as an optional setting for companies that
ask for it, with honest wording, not as the default.



## What we send to the AI model today

**The person asking:** their name, email and user ID are *not* sent. We use their email
only to decide which documents they can see, and that happens inside the database.

**Other people:** their names *are* sent, inside the document text. For example:

- Slack messages are stored as "09:14 Sana Asiwal: message text", and Slack's internal
user codes (like `<@U123>`) stay in the text.
- Linear issues say who they're assigned to and who commented.
- Notion pages include email and phone fields word for word.
- Google Drive documents include whatever names and emails are written in them.
- GitHub shows who made each change.
- Every piece of text we send has a "last edited by (name)" line added.
- Chart questions list up to 15 real names of people with activity.

**Passwords and keys:** if someone pasted a password or API key into a Notion page or a
Slack thread, we store it, search over it, and can send it to the model.

**What we filter today:** only text that tries to trick the model into following
instructions. We don't filter names, emails or secrets anywhere.

**Who receives this text:** not only the main AI model. With our current settings, text also goes to an outside service for search (Jina, which receives every stored piece of text, plus each question), and optionally to Groq (LLM gaurd) for safety checks. Local, in-house options exist for search but aren't switched on.

So the real problem isn't hiding the person asking. It's whether outside companies
should see the names and secrets inside our customers' documents.

## How others handle it



### Tools that swap and swap back

These find sensitive values, replace them with stand-ins, keep a private lookup table,
and restore the real values in the answer:

- Microsoft Presidio, free and runs on our own servers (LangChain adds the "swap back"
step).
- LLM Guard, free and runs on our own servers.
- Skyflow and Protecto, paid hosted services.
- Google's data-protection service, which can scramble values and unscramble them later.



### Tools that only hide or block

These replace a value with a generic label such as `{NAME}`, or refuse the request.
They can't put the value back: AWS Bedrock Guardrails, Cloudflare, Lakera, NVIDIA NeMo.

### What the big products do

None of these say they hide names from the model:

- **Microsoft 365 Copilot:** you only get answers from files you can already open, and
your data isn't used to train the model.
- **Slack AI:** runs the AI model inside Slack's own cloud, so the AI company never
sees the data, and respects who can see each channel.
- **Notion AI:** the AI providers keep nothing for Enterprise customers.
- **Glean and Atlassian Rovo:** providers keep nothing, plus strict permissions.

Their approach is: only fetch what the person is allowed to see, make the provider keep
nothing, run the model yourself when needed, and never store secrets. We already do the
first part well.

## Why swapping in stand-ins is harder than it sounds

1. **It misses things.** The tools that spot names are far from perfect. Presidio's own
  documentation says it can't promise to find everything. One test found it caught
   only a small share of the most sensitive details. It also does worse with Indian
   names, mixed Hindi and English, and names that are also ordinary words (Sana, Asha,
   Will). One missed name and the data has leaked anyway, while everyone believes it's
   protected.
2. **Answers get worse.** In one 2026 study, swapping names for stand-ins cut a model's
  score on document-based questions by more than half. Our product depends on linking
   facts about the same person across several documents, and stand-ins break that.
3. **One person becomes several people.** "Sana", "Sana Asiwal", her email, "@sana" and
  her Slack code would each get a different stand-in unless we first work out they're
   the same person.
4. **The model changes the stand-ins.** It may write "Person 1" instead of `PERSON_1`,
  or make up `PERSON_7`. Guessing what it meant can put the wrong name in the answer.
5. **Some questions are about people.** "What did Sana say?" or "Who owns this?" need
  the name. The question itself would have to be converted too, and if we converted  
   stored documents instead, searching by name would stop working.
6. **The lookup table becomes a target.** It has to be kept strictly separate per
  company. Otherwise one company's stand-ins could turn into another company's names.
7. **It affects other parts of the product.** Saved answers, link checks, chat history
  and the "Act" feature that writes into tools would all need changes so the right
   values show up in the right places.
8. **It costs time.** Scanning text for names adds a noticeable delay to every answer,
  and our prompts are long.



## What we recommend


| Kind of data                                                   | What to do                                                                                                                                                                                                                                                                                    | Swap back?                    |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------- |
| Passwords, API keys, tokens, private keys                      | Find them when we first read a document, replace with "[secret removed]" before storing, and tell an admin so the key can be changed. Add a final check on answers in case one slips through.                                                                                                 | Never                         |
| Emails, phone numbers, ID numbers (Aadhaar, PAN), card numbers | These follow fixed patterns and are easy to detect reliably. Hide them before sending to the model and restore them in the answer, or simply remove them.                                                                                                                                     | Optional                      |
| People's names                                                 | Don't hide them by default. Rely on our existing permission checks, a provider that stores nothing, and a self-hosted model for companies that need it. Offer name hiding as an optional company setting, clearly labelled "best effort; may miss names; answers about people may get worse". | Only if a company turns it on |
| The person asking                                              | Already not sent. Two small fixes: remove Slack's user codes from questions and stored messages, and keep an eye on remembered personal details.                                                                                                                                              | n/a                           |
| Which outside services see the data                            | Show each company, as a clear setting, which outside services receive their text (the AI model, search and safety checks). Offer in-house search and a self-hosted model, which is where the product is heading anyway.                                                                       | n/a                           |




### If we build name hiding later

- Do it just before sending to the model, not when we store documents, so search keeps
working.
- Give each value the same stand-in every time within a company.
- Use the list of known people we already keep, so one person gets one stand-in.
- Only swap back stand-ins we created, matched exactly, never guessed.
- We already use this kind of "stand-in, then swap back" step for citations (`[1]`,
`[2]`) and live document references. Answers are fully written before we show them,
so swapping back is simple.



## Limits and open questions

- **No detector is complete.** We must describe this as reducing risk, never as a
guarantee, the same way we already say Notion can't check per-page permissions.
- **We have no reliable test results for Indian names.** We'd need to test on our own
Slack and Linear data.
- **The Act feature and live GitHub reads** need real values to work, so they can't be
fully covered.
- **The main AI model isn't the only recipient.** The search service and the safety
checks also send text out. Covering only the main model does half the job.
- **Provider "store nothing" agreements** need approval from OpenAI or Anthropic. By  
default OpenAI keeps request logs for 30 days for abuse checks, and Microsoft's Azure  
version does the same unless that's switched off with their approval.

## Decisions needed

1. What worries us most: outside AI companies seeing the data, users of our own app
  seeing too much, or legal requirements (European GDPR, India's DPDP Act)?
2. Has any customer actually asked for names to be hidden from the AI model?
3. Should removing secrets when we first read documents be the first thing we build?
  We recommend yes.



## Sources

- Microsoft Presidio: [https://presidio.dataprivacystack.org/anonymizer/](https://presidio.dataprivacystack.org/anonymizer/) and FAQ [https://presidio.dataprivacystack.org/faq/](https://presidio.dataprivacystack.org/faq/)
- LLM Guard: [https://protectai.github.io/llm-guard/input_scanners/anonymize/](https://protectai.github.io/llm-guard/input_scanners/anonymize/)
- AWS Bedrock Guardrails: [https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html)
- Google data protection (scramble and unscramble): [https://docs.cloud.google.com/sensitive-data-protection/docs/transformations-reference](https://docs.cloud.google.com/sensitive-data-protection/docs/transformations-reference)
- Study on answer quality: [https://arxiv.org/abs/2609.11335](https://arxiv.org/abs/2609.11335)
- OWASP guidance on sensitive data in AI apps: [https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/](https://genai.owasp.org/llmrisk/llm022025-sensitive-information-disclosure/)
- Slack AI: [https://slack.engineering/how-we-built-slack-ai-to-be-secure-and-private/](https://slack.engineering/how-we-built-slack-ai-to-be-secure-and-private/)
- Microsoft 365 Copilot: [https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-privacy](https://learn.microsoft.com/microsoft-365-copilot/microsoft-365-copilot-privacy)
- Notion AI: [https://www.notion.com/help/notion-ai-security-practices](https://www.notion.com/help/notion-ai-security-practices)
- OpenAI data retention: [https://developers.openai.com/api/docs/guides/your-data](https://developers.openai.com/api/docs/guides/your-data)
- Azure OpenAI data privacy: [https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy](https://learn.microsoft.com/en-us/legal/cognitive-services/openai/data-privacy)
- GDPR Recital 26: [https://gdpr-text.com/en/read/recital-26/](https://gdpr-text.com/en/read/recital-26/)

