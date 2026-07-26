# LightHouse OS 1.5.0

## Agent Bus 2.0

LightHouse now operates professional Agents as a shared-cognition engineering team while the main AI remains the only Project Director.

- Structured Work Orders carry intent, code scope, symbols, deliverables, preservation constraints and execution profiles.
- Every professional model Agent can inspect real project context and search/read code; implementation roles can patch and test within inherited Run authority.
- Similar active Work Orders are semantically deduplicated and receive merged context instead of creating duplicate Agent work.
- A durable Finding Board shares verified evidence across Agents and surfaces contradictory positions for main-AI review.
- Dynamic dependency graphs, bounded collaboration requests and advisory Write Intent conflicts coordinate parallel work without imposing a fixed workflow.
- Specialist work uses elastic, evidence-driven tool loops with local Cognitive State, duplicate-call suppression and no-progress stopping.
- Structured implementation, verification, integration and release results are fused into the main AI Cognitive State.
- Agent quality profiles are derived from real completed work, evidence and tool Receipts; they remain advisory rather than hard routing.

## Work Intensity

Users can select `quick`, `balanced`, `advanced` or `extreme` work intensity.

Work Intensity adjusts reasoning effort, context depth, main-AI and Agent budgets, collaboration depth, verification expectations and independent review. It is independent from Observe Mode, Run-wide Auto and Kernel Mode. Simple work stays efficient even at a high setting, while high-risk work can strengthen only the relevant verification dimensions.

Supported OpenAI-compatible providers receive the matching reasoning-effort hint. Providers that reject the optional field are retried automatically without it, so compatibility never blocks a Run.

## Live Auto Execution

Run-wide Auto now removes repeated permission prompts without hiding execution.

After the first Auto confirmation, the server continues the Run in the background and the terminal resumes polling durable state. Balanced mode displays real `READ`, `SEARCH`, `EDIT`, `WRITE`, `TEST`, `DIFF`, `EXEC`, `DATABASE`, `SERVICE`, `AGENT` and related activity with `STARTED`, `SUCCEEDED`, `FAILED` or permission status. Specialist Agent tool starts and Receipt-backed results are also durable, recoverable and injected into the main AI's next-turn context.

Sensitive credentials and authorization values are redacted before execution summaries are persisted or displayed.

## Compatibility and Data

- No new PostgreSQL migration is required.
- Existing Work Orders, Work Events, Agent Steps, Memory, Mega Projects, Massive Build data and Receipts remain authoritative.
- Legacy `design`, `coding` and `verification` role requests are mapped to professional Agent Bus 2.0 roles; the old tool-less generic Agents are no longer used for routing.
- Existing installed instances retain all data and configuration when upgraded.
