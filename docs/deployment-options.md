# Handbook Deployment Options

Handbook can be offered to a company in three ways. This document explains each one, how we look after it, and its trade-offs. It closes with the current limitations and how we plan to address them.

| | 1. Handbook Cloud | 2. Handbook Self-Hosted | 3. Handbook Dedicated |
| --- | --- | --- | --- |
| In short | The company uses the copy of Handbook we host | The company installs and runs Handbook itself | We run a separate copy just for that company |
| Who runs it | We do | The company does | We do |
| Where the data is kept | Our shared database | The company's own servers or cloud | Its own database, in our cloud or the company's |
| Best suited for | Startups and small companies | Companies with strict security or compliance rules | Companies that want their data kept separate, or kept in a particular region |

---

## 1. Handbook Cloud

**What it is.** We host a single copy of Handbook that many companies share. A company signs up, connects its tools and starts using it. Each company sees only its own data. This is how Handbook runs today.

**How we look after it**

- We run, update and monitor everything. New features reach every company at the same time.
- We back up the data regularly.
- If something goes wrong, we fix it and let companies know.
- Companies reach us by email or in the app.
- The company only connects its tools and manages its own users.

**Trade-offs**

| Advantages                                  | Disadvantages                                                                   |
| ------------------------------------------- | ------------------------------------------------------------------------------- |
| Lowest cost: one copy serves every company  | All companies' data is kept in one database, separated by our software and code |
| Updates reach everyone at once              | A problem also affects everyone at once                                         |
| We see issues directly and fix them quickly | Companies with strict compliance rules may not accept a shared database         |
| No setup for the company                    | One location for everyone, so companies far from it may see slower responses    |

---

## 2. Handbook Self-Hosted

**What it is.** We provide Handbook as a ready-to-run package (Docker images and a setup file) with an installation guide. The company installs it on its own servers or cloud, using its own database. It also sets up its own connections to Slack, GitHub, Google Drive, Notion and Linear (see section 5).

**How we look after it**

- We release new versions with clear upgrade instructions. The company chooses when to upgrade.
- We help with the current version and the one before it.
- We help over email and calls. We have no access to the company's system, so we work from the details the company shares with us.
- We let each company know promptly about any security update.
- Help is offered as a paid support plan, including help with the first installation.

| The company takes care of | We take care of |
| --- | --- |
| Servers, network and web address | Fixing problems in Handbook |
| Backups | New versions and upgrade instructions |
| Installing upgrades | Security updates |
| Setting up its connections to each tool | The installation guide |
| Its AI model account | Helping when the company needs it |

**Trade-offs**

| Advantages | Disadvantages |
| --- | --- |
| Strongest privacy: data stays with the company | Companies may upgrade late, so several versions are in use at once |
| No hosting cost for us | We cannot see the system, so solving problems takes longer |
| Opens up companies that cannot use a shared service | Setup takes more effort, especially connecting the five tools |
| | Each company's environment is different, which can cause unexpected setup issues |

---

## 3. Handbook Dedicated

**What it is.** We run a separate copy of Handbook for a single company, with its own database. It can run in either place:

- **(a) Our cloud,** in the region the company chooses. The company has nothing to set up.
- **(b) The company's own cloud account.** The company creates the account and gives us access, and we set up and run everything inside it.

In both cases the company uses Handbook just as it would on Cloud. It does not install or maintain anything.

**How we look after it**

- We run, update and monitor it just as we do Cloud.
- Updates are installed at a time agreed with the company.
- We back up the data regularly.
- In (b), the company can see every action we take in its account.
- The company has a direct contact on our team, and an agreed level of service.

**Trade-offs**

| Advantages | Disadvantages |
| --- | --- |
| The company's data is fully separate from other companies' | Every new company adds another copy for us to run and update |
| We run it, so problems are solved quickly | Costs more to run, so it is priced above Cloud |
| The company chooses where its data is kept | (b) Setup in the company's cloud can take longer, depending on its internal approvals |
| (b) Data never leaves the company's own account | (b) We rely on the company keeping our access in place |

---

## 4. What we do not offer

**The company runs Handbook while we keep its database.** The company's data would still sit with us, so it gains no privacy. Every request would also travel between two networks, which makes Handbook slower and problems harder to trace. A company that asks for this usually doesn't want to manage a database. Self-Hosted with its cloud provider's managed database, or Dedicated, meets that need better.

---

## 5. Current limitations and planned fixes

### Each self-hosted company must create its own app for every tool

**The limitation.** Handbook connects to Slack, GitHub, Google Drive, Notion and Linear through apps registered with each of those services. Our apps are registered to our web address and hold secret keys we cannot share. A self-hosted company must therefore create its own app on each service it uses: set the permissions, turn on the Slack bot, enter its own web address for sign-in and updates, and copy the keys into Handbook's settings. It only needs apps for the tools it actually uses.

**The fix.**

- A step-by-step guide for each tool, listing every permission and web address to enter.
- Ready-made app templates where the service supports them (Slack and GitHub), so the app is created in a few clicks instead of field by field.

### A self-hosted company has to approve its own sign-up

**The limitation.** On Cloud, every new company's sign-up is approved by us. A self-hosted copy keeps this step, so the company's admin signs up and then approves their own request by email. It works, but it is an unnecessary step.

**The fix.** A single-company mode for self-hosted copies: the company is created during installation and public sign-up is turned off.

### Dedicated copies can use our apps, but live updates reach only one copy

**The limitation.** A Dedicated company does not need to create its own apps. It can use the Slack, GitHub, Google Drive, Notion and Linear apps we already have.

Signing in through our apps works fine, because each app accepts sign-ins for many web addresses. The problem is live updates. Whenever something changes, such as a new Slack message or an edited Notion page, the tool sends a notice to the app's update address. Each app can have only **one** such address, and today it points to our Cloud copy.

With several Dedicated copies, each running at its own web address, these notices still go only to Cloud. The Dedicated copies never hear about changes. The Slack bot would not reply in a Dedicated company's workspace, and new content would show up only at the next scheduled sync.

**The fix.**

The company chooses one of two options:

- **The company creates its own apps.** It enters its own copy's address when it sets them up, so updates go straight to its copy and nothing else is needed. This adds the same setup work as Self-Hosted.
- **The company uses our apps.** It has nothing to set up. We add a small forwarding service that receives every update and passes it on to the right company's copy. For a copy in the company's own cloud, we create a separate set of apps for that company instead, so our shared app keys never sit in another company's account.

### Uploaded files can only be stored with one provider

**The limitation.** Files people upload in chat are stored with Cloudinary, a hosted service. Self-hosted and Dedicated (b) companies will expect files to stay in their own storage.

**The fix.** Add support for standard cloud storage (Amazon S3 and compatible services), which every major cloud and most companies already use.
