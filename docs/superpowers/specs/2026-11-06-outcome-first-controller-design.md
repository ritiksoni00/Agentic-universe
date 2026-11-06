# Design: Outcome-first controller

Approved direction for the Formi assignment solution (implemented in `controller/`).

## Goal
Closed loop over the simulator HTTP API: ingest → detect → experiment → govern → apply → audit, with crash resume.

## Scope
Built: C1–C3, C5–C6, C8–C10, C12–C13; partial C4/C7/C14. Cut: C11 UI, C15 quotas.

## Core decisions
- SQLite WAL as sole durable store (SRE: no new infra).
- Statistical winners only; LLM only for prompt incident suggestions.
- Shared cohort list in workflow config.
- Supervisor prompt edits latch an incident watch (new prompts lack a self-baseline).
- One in-flight proposal per workflow; urgent prompt fixes bypass the 11:00 ops preference.
