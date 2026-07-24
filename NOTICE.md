# Design and source notice

LightHouse OS is licensed under the MIT License.

The 0.2 agent runtime was implemented natively for this repository. No source
code from Claude Code, OpenAI Codex, Aider, OpenHands or other external coding
agents is vendored or copied.

Design references:

- OpenAI Codex (`openai/codex`, Apache-2.0): project instruction files,
  bounded context discovery and terminal-agent lifecycle concepts.
- `CPYMSU/warehouse`: capability registry, operation confirmation, deterministic
  executor, append-only event and durable receipt patterns.
- `CPYMSU/MSU/neural-bus.js`: standardized event/signal naming and durable
  experience-stream concept.

The local/OpenSSH process execution implementation is original LightHouse code;
its bounded output and receipt shape follow the operational lessons of
`CPYMSU/warehouse/scripts/shieldctl.py`.
