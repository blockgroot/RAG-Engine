# Onyx vs. Handbook: Feature Parity & Architectural Analysis

## 1. Overview

This document compares Onyx (formerly Danswer) and Handbook across architecture, feature parity, permissions, and retrieval capabilities. It outlines core differences, identifies features from Onyx that can be integrated into Handbook, and defines capabilities that are out of scope.

### System Comparison


| Attribute                | Onyx                                                                 | Handbook                                                                                   |
| ------------------------ | -------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| Primary Objective        | Horizontal enterprise search across SaaS tools                       | Multi-tenant company Q&A, engineering intelligence, and scheduled reporting                |
| Core Architecture        | Distributed: Vespa (vectors, BM25, ACLs) + PostgreSQL + Celery/Redis | Unified: PostgreSQL + pgvector (HNSW, fulltext, relational data, job queues)               |
| Access Control           | Document-level ACL mirroring from external sources                   | Workspace-level isolation with org_id and workspace_id                                     |
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
| In-Chat File Attachments              | Yes          | Planned        | Onyx supports drag-and-drop file queries in active chats.                    |
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
| Document-Level ACL Mirroring          | Yes          | No             | Onyx mirrors file permissions from Drive and Notion into Vespa.              |
| **Agents & Automation**               |              |                |                                                                              |
| Domain-Specific Specialized Agents    | No           | Yes            | Handbook routes deterministically to Notion, Drive, Slack, or GitHub agents. |
| Live REST Tool Agent                  | No           | Yes            | Handbook GitHubAgent queries live repository states without vector delay.    |
| SQL Metric Generation (Insights)      | No           | Yes            | Handbook InsightsAgent translates text to SQL for verifiable data charts.    |
| Multi-Hop Deep Research               | Yes          | No             | Onyx decomposes complex prompts into multi-step research loops.              |
| External Write Actions                | Yes          | No             | Onyx can execute write tasks (create tickets, send messages).                |
| Scheduled Activity Reports            | No           | Yes            | Handbook sends automated background digests on defined schedules.            |
| Slackbot Integration                  | Yes          | Yes            | Both support channel mentions and 1-on-1 direct messages.                    |
| End-User Feedback Tracking            | Yes          | No             | Onyx tracks thumbs up/down and unresolved query metrics.                     |


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
- **Handbook**: Enforces permissions at the container and workspace level. Access is determined by organization and workspace membership. This eliminates the risk of cross-workspace leaks and avoids synchronization lag, but does not distinguish file-level sharing permissions within the same workspace.

---



## 4. Strengths of Handbook Over Onyx

1. **Operational Simplicity**: Unified on PostgreSQL with no distributed search cluster dependencies.
2. **Deterministic Grounding**: The pre-LLM 0.35 cosine gate prevents hallucinations and unnecessary LLM API calls on irrelevant queries.
3. **Live Operational Data**: The GitHub agent interacts directly with current repository data, avoiding the latency and staleness of vector-embedded pull requests and reviews.
4. **Verified Analytics**: The Insights agent generates executable SQL queries for structured metric questions, avoiding approximate LLM text answers.
5. **Proactive Automation**: Background schedulers deliver recurring summary digests without requiring manual user queries.

---



## 5. Features from Onyx Adaptable to Handbook



### Feature 1: In-Chat Ad-Hoc File Attachments

- **Functionality**: Users upload individual files (PDF, DOCX, CSV, TXT) within an active chat thread for localized querying.
- **Handbook Fit**: Associate uploaded documents with the specific conversation identifier. When the conversation is deleted, associated files and embeddings are automatically cleared via database cascade rules. Retrieval queries search conversation-specific files alongside or in place of workspace documents.



### Feature 2: Document-Level Access Filtering

- **Functionality**: Prevent users from viewing documents they cannot access in the source system (e.g., restricted Google Drive files).
- **Handbook Fit**: Capture allowed user emails during connector synchronization and store them in an array column on the document table. Query retrieval can then include a filter matching the authenticated user's email against this array, providing document-level security within PostgreSQL without external search engines.



### Feature 3: Deep Research Multi-Agent Workflow

- **Functionality**: Answer complex, multi-part questions by performing multi-step retrieval across different sources and compiling a comprehensive summary.
- **Handbook Fit**: Add an orchestrator agent that breaks complex questions into focused sub-queries, routes them to existing domain agents (Notion, Drive, Slack, GitHub), and synthesizes the returned findings into a structured report.



### Feature 4: Bi-Directional Action Tools

- **Functionality**: Enable the assistant to take action in connected services (e.g., creating issues or posting updates).
- **Handbook Fit**: Add write capabilities to existing connectors (such as Linear and GitHub) with an explicit confirmation step in the user interface before executing external changes.



### Feature 5: Additional Enterprise Connectors

- **Functionality**: Broaden ingestion coverage to platforms commonly used alongside current integrations.
- **Handbook Fit**: Implement Atlassian Jira and Confluence adapters using the existing source connector interface.



### Feature 6: Feedback and Documentation Gap Tracking

- **Functionality**: Collect user ratings on answers and identify knowledge gaps where documentation is missing.
- **Handbook Fit**: Record user ratings and ungrounded queries (those rejected by the confidence gate) in a dedicated table, giving administrators a summary of topics that require documentation.

---



## 7. Implementation Prioritization

1. **In-Chat File Attachments**: Immediate user-facing benefit for analyzing one-off documents within existing conversations.
2. **Feedback and Gap Tracking**: Low complexity; provides data on retrieval quality and missing information.
3. **Document-Level Access Filtering**: Enhances security for shared Google Drive folders without infrastructure changes.
4. **Jira and Confluence Connectors**: Extends coverage for engineering and product documentation.
5. **Deep Research Workflow**: Combines outputs from existing agents for comprehensive analysis.
6. **Bi-Directional Action Tools**: Introduces write functionality with required approval workflows.

