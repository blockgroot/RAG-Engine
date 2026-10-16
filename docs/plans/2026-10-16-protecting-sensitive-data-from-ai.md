# Protecting Sensitive Data from Outside AI Services

*Draft for discussion. Nothing has been built yet. 16 Oct 2026.*

## What we want

When someone asks our assistant a question, we send parts of company documents to an outside AI company to write the answer. Those documents can contain people's names, emails, phone numbers, ID numbers, bank details and sometimes passwords.

**Goal:** the outside AI should never see these values, but the person asking should still get an answer with the real values in it.

## How it works

The approach is called **pseudonymization**. Before any text is sent to the AI, each sensitive value is replaced with a neutral placeholder. The AI writes its answer using those placeholders. Once the answer is back on our side, the original values are put back in before it's shown to the user.

| Step | Text |
|---|---|
| Original text | "Ask sana@syvora.com, she owns the billing project." |
| Sent to the AI | "Ask EMAIL_1, she owns the billing project." |
| AI's reply | "You should contact EMAIL_1." |
| Shown to the user | "You should contact sana@syvora.com." |

The mapping between placeholders and real values is kept only for the duration of a single question. It is deleted afterwards and is never shared between companies.

## What gets hidden

| Type of data | Can we hide it? | Why |
|---|---|---|
| Emails, phone numbers | Yes | They follow a fixed format, so they're easy to spot |
| Slack, Linear and GitHub user IDs | Yes | Fixed format |
| Employees' names | Yes | We already keep a list of everyone in the company from the connected tools |
| Aadhaar, PAN, passport, card and bank numbers | Yes | Fixed format, with built-in check digits |
| Passwords and API keys | Yes, **deleted for good** | No answer ever needs a password, so we never put it back |
| Names of customers or other outsiders | Only partly | They aren't on our list, and name-spotting tools miss many of them, especially Indian names |
| Salary, health details, reviews, deal values | No | These are the content of the answer. If we hide them, the AI can't answer |

## How much of the app changes

The change is smaller than it sounds. **Every message to the AI already passes through one gateway in our code**, so most of the work happens in one place.

1. **A new privacy layer at the gateway.** It replaces sensitive values on the way to the AI and restores them on the way back. The rest of the app (chat, charts, live reads, Act, Slack, saved history) keeps receiving normal answers and doesn't need rewriting.
2. **Password removal when we first save documents,** so passwords are never stored or searchable.
3. **Keeping the search step on our own servers.** Before the AI answers, the app searches all the company's documents to find the most relevant passages. Today Jina does this search, so every document and every question is sent to them in full, with real names and emails. Placeholders can't be used here, because search needs the real words: if someone asks "What did Sana say?", the documents mentioning Sana must be found by that name. To keep this step private, we would need to run the search models on our own servers for companies that turn privacy on. We don't do this anywhere today. The code has an early option for it, so this means setting it up, testing it and adding server capacity.
4. **An on/off switch per company** in the admin settings.
5. **Tests** that fail if any known email, ID or password ever reaches the AI.

We use existing free tools (Microsoft Presidio and a few smaller ones) to *find* the sensitive values. The replace-and-restore step we build ourselves, because no maintained ready-made tool does both.

## Effect on live features

Our app also reads Notion, Drive, Slack, Linear and GitHub live while answering, and can create issues or post messages (Act).

- **Live reads:** covered automatically, because that text goes through the same gateway. Passwords are removed there too, since live text skips the "save" step.
- **GitHub questions:** when the AI asks to "look up commits by PERSON_3", we restore the real name before contacting GitHub.
- **Creating issues or posting messages:** the draft comes back with real names before the person sees it, so posts and @-mentions work normally.
- **Speed:** the basic checks take a fraction of a second. A more advanced name detector would add noticeable delay, so it stays optional.

Salesforce's AI product works the same way, which is a good sign the approach holds up in practice.

## Limitations (what this can't do)

- **It can't hide the subject of the question.** Salary, health and business details stay visible, because they are what the AI needs to read. Protecting those needs either a contract where the AI company keeps nothing, or running the AI on our own servers. Running it ourselves is the only way to guarantee nothing leaves.
- **It won't catch every name.** Anyone not on our company list can slip through.
- **It doesn't control what our own users can see.** That's handled by permissions, which we already have.
- **Running search on our own servers is new work and costs more.** It has to be set up and tested, needs extra server capacity, may be slightly slower than the outside service, and each company that switches must have its documents processed again once. Until it's done, the outside search company still sees the real text.

## Challenges

1. **Answers may get worse.** One study found AI answers built from documents got much worse when names were replaced. Another found almost no difference. We need to test on our own questions before switching it on.
2. **One person, many forms.** "Sana", "Sana Asiwal", her email and her Slack handle must all map to the same placeholder, or the AI treats them as different people. Our company list helps, but two people with the same first name are tricky.
3. **Names that are ordinary words.** Names like Sana, Asha or Will look like normal words to detection tools.
4. **The AI altering placeholders.** If it writes "Person 3" instead of "PERSON_3", we can't safely restore it, so that name is left out of the answer.
5. **Hints still identify people.** "The backend lead in Pune who joined in March" points to one person without naming them.
6. **Every exit must be covered.** If one feature talks to the AI without going through the gateway, data leaks there. Tests must guard against this.
7. **False alarms.** Order numbers or random-looking text may be mistaken for ID numbers or passwords and hidden when they shouldn't be.
8. **Bigger app.** The detection tools add roughly 700 MB, which matters for our servers and the self-hosted version.

## Suggested order

1. **Remove passwords and keys.** Quick, no downside.
2. **Hide emails, phones, IDs and employee names**, behind a per-company switch.
3. **Move search to our own servers** for companies that turn it on.
4. **Maybe later:** detect outsiders' names, once we've measured speed and accuracy.

## Decisions needed

1. Should this be a per-company setting (recommended) or always on?
2. Is it acceptable to run search on our own servers for those companies, given the extra server cost?
3. Is catching outsiders' names in scope, knowing it's best effort?
4. Start with password removal? We recommend yes.
