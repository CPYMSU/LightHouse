# LightHouse OS 1.2.0

LightHouse 1.2 turns the autonomous 1.0 platform into an observable, professionally
specialized and massively scalable engineering environment.

## Fixed

- Auto Mode no longer asks at the start of greetings, explanations or other Runs
  that may never perform a side effect.
- Permission is requested only when the first immutable governed Operation actually
  needs it.
- A successful file, desktop, database or other Operation Receipt can no longer be
  erased by a later provider SSL EOF or disconnect.
- Provider transport errors receive one bounded retry.
- Multi-instance database migrations are serialized with a PostgreSQL advisory lock.

## Agent Observatory

- Live Agent role, task, state, progress and critical findings during active Runs.
- Agent Bus advice for wait-all, wait-critical, continue-now or parallel-review-later.
- `/agents` and Agent event APIs.
- Exact or explicitly estimated model Token receipts with `/tokens` and usage APIs.

## Professional Agents

- Research Agent with safe public-web search and bounded page reading.
- Taste Agent for context-sensitive frontend design judgment.
- Frontend and Backend implementation Agents.
- Wiring Verification Agent for real UI-to-database and Receipt evidence.
- Integration, Test Design and Contract Agents.

Specialist Agents can request registered tools and process their Receipts over
multiple rounds. Side effects remain governed by parent-Run scope and Massive Build
Write Leases.

## Massive Build

- Build Cells for independent domains and capabilities.
- Versioned shared contracts.
- Optional isolated Git Worktrees.
- Expiring non-overlapping Write Leases.
- Reviewable code batches and diff summaries.
- Incremental domain/project integrations.
- Full-stack wiring evidence and continuous regression support.
- Unbounded logical Work Orders with adaptive physical concurrency.

No fixed investigation, planning, build or test workflow is introduced. The main AI
remains free to wait, continue, parallelize, revise, roll back or integrate based on
current evidence.

## Validation target

The release requires Linux/PostgreSQL integration, Python compilation, macOS
installer syntax, PowerShell 5.1, generated Windows startup and Windows-native
contract tests before merge.
