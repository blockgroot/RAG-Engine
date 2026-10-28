# Handbook Deployment Options

Handbook can be offered to a company in two ways. This document explains each one, how we look after it, and its trade-offs. It closes with the current limitations and how we plan to address them.

| | 1. Handbook Cloud | 2. Handbook Self-Hosted |
| --- | --- | --- |
| In short | The company uses the copy of Handbook we host | Handbook runs on the company's own servers or cloud |
| Who sets it up | Nobody: the company signs up | The company, or we do it for them |
| Who runs it | We do | The company does (we can help, on a support plan) |
| Where the data is kept | Our shared database | The company's own servers or cloud |
| Best suited for | Startups and small companies | Companies with strict security or compliance rules, or that want their data kept separate or in a particular region |

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

**What it is.** We provide Handbook as a ready-to-run package (Docker images, a setup file and scripts) with an installation guide. Handbook runs on the company's own servers or cloud account, with its own database, and its data never leaves the company. The company uses its own apps to connect Slack, GitHub, Google Drive, Notion and Linear (see section 4).

**Two ways to set it up**

| | **The company installs it** | **We install it for the company** |
| --- | --- | --- |
| Who installs Handbook | The company's IT team, following our guide | We do, on the company's server or cloud account, with access the company gives us |
| Who creates the tool apps | The company's IT team, following our guide | We do, in the company's Slack, GitHub, Google and other accounts, with its admins |
| After setup | The company upgrades and backs up, using our scripts | The same, or we keep doing it on a support plan |
| Best for | Companies with their own IT team | Companies that want their data on their own servers but not the setup work |

When we install it for a company, the company can see every action we take in its account. When setup is done we hand the access back, unless the company keeps us on a support plan.

**How we look after it**

- We release new versions with clear upgrade instructions. The company chooses when to upgrade.
- We help with the current version and the one before it.
- We help over email and calls. Unless we are on a support plan with access, we work from the details the company shares with us.
- We let each company know promptly about any security update.
- Help is offered as a paid support plan, which can include installing Handbook and running upgrades and backups for the company.

| The company takes care of | We take care of |
| --- | --- |
| Servers, network and web address | Fixing problems in Handbook |
| Backups and upgrades (or we do, on a support plan) | New versions and upgrade instructions |
| Its apps for each tool (or we create them, during setup) | Security updates |
| Its AI model account | The installation guide and scripts |
| Giving us access, if we install it | Helping when the company needs it |

**Trade-offs**

| Advantages | Disadvantages |
| --- | --- |
| Strongest privacy: data stays with the company | Companies may upgrade late, so several versions are in use at once |
| The company chooses where its data is kept | Without access, we cannot see the system, so solving problems takes longer |
| No hosting cost for us | Setup takes more effort, especially connecting the five tools |
| Opens up companies that cannot use a shared service | Each company's environment is different, which can cause unexpected setup issues |
| One package for every company, whoever installs it | When we install it, setup depends on the company's internal approvals and access |

---

## 3. What we do not offer

**The company runs Handbook while we keep its database.** The company's data would still sit with us, so it gains no privacy. Every request would also travel between two networks, which makes Handbook slower and problems harder to trace. A company that asks for this usually doesn't want to manage a database. Self-Hosted with its cloud provider's managed database, installed by us if it prefers, meets that need better.

**A separate copy of Handbook that we host for one company.** Companies that want their data kept apart are served by Self-Hosted in their own cloud, installed by us if they prefer. If a company needs this and has no cloud account of its own, we can run the same Self-Hosted package on a server we rent for it, as a one-off arrangement.

---

## 4. Current limitations and planned fixes

### Each self-hosted company needs its own app for every tool

**The limitation.** Handbook connects to Slack, GitHub, Google Drive, Notion and Linear through apps registered with each of those services. Our apps are registered to our web address and hold secret keys we cannot share. A self-hosted copy therefore needs its own app on each service it uses: set the permissions, turn on the Slack bot, enter its own web address for sign-in and updates, and copy the keys into Handbook's settings. It only needs apps for the tools it actually uses. When we install Handbook for a company, we do this work.

**The fix.**

- A step-by-step guide for each tool, listing every permission and web address to enter.
- Ready-made app templates where the service supports them (Slack and GitHub), so the app is created in a few clicks instead of field by field.

### Uploaded files keep only their text

**The limitation.** On Cloud, files people upload in chat are stored with Cloudinary, a hosted service. A self-hosted copy keeps them in its own database instead, so they stay on the company's server and are included in its backups. Only the text read from each file is kept, not the original file, so the original cannot be downloaded again later.

**The fix, if a company needs the originals.** Add support for standard cloud storage (Amazon S3 and compatible services), which every major cloud and most companies already use.
