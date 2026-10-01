# Onyx vs. Handbook: Feature Parity & Architectural Analysis

*Last updated: 1 October 2026.* Current Handbook status of every feature: `PRODUCT_STATUS.md`.

## 1. Overview

This document compares Onyx (formerly Danswer) and Handbook across architecture, feature parity, permissions, and retrieval capabilities. It outlines core differences, identifies features from Onyx that can be integrated into Handbook, and defines capabilities that are out of scope.

### System Comparison


| Attribute                | Onyx                                                                 | Handbook                                                                                   |
| ------------------------ | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Primary Objective        | Horizontal enterprise search across SaaS tools                       | Multi-tenant company Q&A, engineering intelligence, and scheduled reporting                |
| Core Architecture        | Distributed: Vespa (vectors, BM25, ACLs) + PostgreSQL + Celery/Redis | Unified: PostgreSQL + pgvector (HNSW, fulltext, relational data, job queues)               |
| Access Control           | Document-level ACL mirroring from external sources                   | Org and space isolation, plus document-level ACLs captured at sync (Drive, Slack, Linear) and per-repo checks (GitHub) |
| Grounding Strategy       | Prompt-guided refusal with cross-encoder reranking                   | Hard mathematical cosine gate (0.35 threshold) + prompt guardrails + post-generation audit |
| Source Data Freshness    | Static text indexing for code, tickets, and docs                     | Live REST APIs for engineering tools (GitHub) + indexed docs for knowledge bases           |
| Intelligence Model       | Single LLM with persona system prompts                               | Pinned domain agents, live GitHub tool agent, and natural-language-to-SQL agent            |
| Proactivity              | Reactive only (user-prompted search)                                 | Reactive Q&A plus proactive scheduled digests (daily, weekly, monthly)                     |
| Infrastructure Footprint | Heavy (16 GB+ RAM, multiple distributed services)                    | Minimal (2-4 GB RAM, single database engine, self-hostable)                                |


---



## 2. Feature Parity Matrix


| Capability                            | Onyx         | Handbook       | Comparison & Notes                                                           |
| ------------------------------------- | ------------ | -------------- | ---------------------------------------------------------------------------- |
| **Search & Retrieval**                |              |                |                                                                              |
| Hybrid Search (Vector + Fulltext)     | Yes          | Yes            | Onyx uses Vespa; Handbook uses pgvector HNSW and tsvector with RRF fusion.   |
| Cross-Encoder Reranking               | Yes          | Yes            | Supported in both systems over candidate retrieval pools.                    |
| Hard Mathematical Confidence Gate     | No           | Yes            | Handbook rejects irrelevant queries at score 0.35 before calling LLM.        |
| Post-Generation Grounding Audit       | No           | Yes            | Handbook validates generated claims against source citations.                |
| Author & Source Provenance in Context | Partial      | Yes            | Handbook injects author, update timestamp, and provider into chunk context.  |
| In-Chat File Attachments              | Yes          | Yes            | Handbook stores uploads in Cloudinary and blends them with retrieval.        |
| Conversation Memory & Query Rewrite   | Yes          | Yes            | Both rewrite follow-up questions to standalone search queries.               |
| **Connectors & Ingestion**            |              |                |                                                                              |
| Connector Breadth                     | 50+ sources  | 6 core sources | Onyx supports a wider range of enterprise platforms.                         |
| Google Drive                          | Yes          | Yes            | Both index folders and documents.                                            |
| Notion                                | Yes          | Yes            | Both support workspaces and pages.                                           |
| Slack                                 | Yes          | Yes            | Both support public and private channel indexing.                            |
| GitHub                                | Yes (Static) | Yes (Live API) | Onyx embeds code chunks; Handbook queries live PRs, reviews, and commits.    |
| Linear                                | Community    | Yes            | Handbook has first-class native Linear issue indexing.                       |
| Google Forms                          | No           | Yes            | Handbook includes dedicated form response ingestion.                         |
| **Permissions & Isolation**           |              |                |                                                                              |
| Multi-Tenant Isolation                | Yes          | Yes            | Handbook enforces org_id checks at database query level.                     |
| Sub-Workspaces (Spaces)               | No           | Yes            | Handbook supports nested private workspaces inside organizations.            |
| Document-Level ACL Mirroring          | Yes          | Yes            | Drive per file, Slack private channels, Linear private teams, GitHub per repo. Neither syncs Notion page permissions (no API). |
| **Agents & Automation**               |              |                |                                                                              |
| Domain-Specific Specialized Agents    | No           | Yes            | Handbook routes deterministically to Notion, Drive, Slack, or GitHub agents. |
| Live REST Tool Agent                  | No           | Yes            | Handbook GitHubAgent queries live repository states without vector delay.    |
| SQL Metric Generation (Insights)      | No           | Yes            | Handbook InsightsAgent translates text to SQL for verifiable data charts.    |
| Multi-Hop Deep Research               | Yes          | No             | Onyx decomposes complex prompts into multi-step research loops.              |
| External Write Actions                | Yes          | No             | Onyx can execute write tasks (create tickets, send messages).                |
| Scheduled Activity Reports            | No           | Yes            | Handbook sends automated background digests on defined schedules.            |
| Slackbot Integration                  | Yes          | Yes            | Both support channel mentions and 1-on-1 direct messages.                    |
| End-User Feedback Tracking            | Yes          | Yes            | Thumbs with reasons, plus automatic documentation-gap logging.               |
| Webhook / Push Sync                   | Yes          | Yes            | Handbook: Slack, Linear, Notion webhooks and Drive push channels.            |
| Knowledge Graph Across Tools          | Partial      | Yes            | Handbook's Second Brain links people, documents, issues and PRs.             |
| Personal Memory                       | Yes          | Yes            | Handbook keeps a few user-stated facts, never as evidence.                   |
| Prompt-Injection Defense              | Partial      | Yes            | Policy file, scrubbing, link provenance, canary, safety-model scoring.       |


---



## 3. Core Architectural Differences



### Storage Engine

- **Onyx**: Employs Vespa as a dedicated search engine alongside PostgreSQL and Redis/Celery. This supports distributed tensor computation and in-engine permission filtering, but requires dedicated cluster management and higher resource consumption.
- **Handbook**: Uses a single PostgreSQL database with pgvector for vector storage, tsvector for keyword search, standard relational tables for metadata, and skip-locked queues for background workers. This allows the system to run on standard hardware with low operational maintenance.



### Grounding and Hallucination Control

- **Onyx**: Relies primarily on prompt instructions to avoid answering when context is insufficient. Hallucinations can occur when retrieved documents are tangentially related.
- **Handbook**: Uses a two-layer defense. First, a pre-LLM cosine threshold gate (0.35) rejects questions with insufficient similarity without invoking the LLM, reducing latency and cost. Second, context is fenced, and a secondary audit step validates citations.



### Permission Models

- **Onyx**: Pulls permission metadata (user emails and group IDs) during source crawling and indexes them alongside document chunks in Vespa. Queries are filtered against the user's identity. This handles granular file-sharing permissions but requires constant synchronization to reflect permission changes.
- **Handbook**: Organization and space membership first, then document-level ACLs: each document stores its viewers (emails, `domain:`, `group:`, `channel:` entries) captured from the source's own sharing at sync time, and every retrieval query adds one predicate in the same SQL that pins the tenant. Google Groups are expanded on the read side (off until an admin connection exists); GitHub is checked per repository against the asker's linked login. Notion stays space-level: its API exposes no page sharing.

---



## 4. Strengths of Handbook Over Onyx

1. **Operational Simplicity**: Unified on PostgreSQL with no distributed search cluster dependencies.
2. **Deterministic Grounding**: The pre-LLM 0.35 cosine gate prevents hallucinations and unnecessary LLM API calls on irrelevant queries.
3. **Live Operational Data**: The GitHub agent interacts directly with current repository data, avoiding the latency and staleness of vector-embedded pull requests and reviews.
4. **Verified Analytics**: The Insights agent generates executable SQL queries for structured metric questions, avoiding approximate LLM text answers.
5. **Proactive Automation**: Background schedulers deliver recurring summary digests without requiring manual user queries.

---



## 5. Features from Onyx Adaptable to Handbook

**Already adopted:** in-chat file attachments, document-level access filtering and feedback
with documentation-gap tracking, each now live (see `PRODUCT_STATUS.md`).

**Still open:**
- **Deep Research multi-agent workflow:** break a complex question into sub-queries across the
  domain agents and compile a report.
- **Bi-directional action tools:** write actions (create issues, post updates) behind an
  explicit confirmation step.
- **Additional enterprise connectors:** Jira and Confluence first, using the existing adapter
  interface.

---

## 6. Implementation Prioritization (remaining)

1. **Jira and Confluence connectors:** extend coverage for engineering and product docs.
2. **Deep research workflow:** combine existing agents for comprehensive analysis.
3. **Bi-directional action tools:** write functionality with required approval workflows.
