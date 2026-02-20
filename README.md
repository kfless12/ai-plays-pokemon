# Pokemon Red AI Navigator

Run Pokemon Red in an emulator and control it with a local LLM. This project currently connects a small local LLM to a control loop that reads state from the emulator, asks the LLM what to do, and issues inputs. This README defines the next iteration plan and design so we can implement improvements incrementally.

## Current State (Baseline)

- Emulator: Pokemon Red (ROM and save present).
- Lua scripts in `scripts/` probe and expose game state (HUD, battle menu, movement, etc.).
- Python orchestrates:
  - Reads state (e.g., `state.json`, logs in text files like `event_logs.txt`).
  - Sends prompts to a local LLM (see `llm_client.py`, `setup_llm.sh`).
  - Writes actions to `action.txt` for Lua to consume.
- Known files:
  - `agent.py`: Main AI agent loop (LLM interaction, action decisions).
  - `navigator.py`: Navigation helpers and/or high-level movement logic.
  - Lua scripts (under `scripts/`): hooks and probes to interact with emulator.
  - Logs: `agent_log.txt`, `control_probe_log.txt`, `event_logs.txt`.
- Limitation: Single-step decisions with minimal context make complex objectives (e.g., “Explore city”) inefficient and brittle.

## Objectives for Next Iteration

1. Provide richer context to the local AI.
2. Implement a Task/Subtask system (hierarchical planning) with persistent objectives.
3. Expand and formalize the action interface ("move_to(x,y)", "swap_pokemon", "finish_text_box", "save_game", etc.).
4. Reduce polling rate and decouple frame updates from decision points.
5. Strengthen structured logging (including LLM calls/responses) with clean separation.
6. Replace file-based IPC between Python and Lua with socket/API communication.

Each objective below includes rationale, design, actionable steps, milestones, and testing.

---

## 1) Richer Context to the Local AI

Rationale: Small LLMs benefit from well-structured, compressed, and relevant context. Context should include: current location/area, party summary, inventory highlights, objectives, and recent history of actions/outcomes.

Design:
- Introduce a ContextBuilder that compiles a compact, schema-driven snapshot:
  - Player: map, coordinates, facing, money, badges, time played.
  - Party: species, levels, HP %, status conditions, moves (abridged), lead.
  - Encounter/Battle: opponent, phase, selectable options.
  - World: interactables in proximity (NPC, sign, door), points-of-interest (if known).
  - Objectives: current task tree (see Task System) + immediate subtask goal.
  - Recent history: last N actions and outcomes (bounded, summarized).
- Output format: JSON for internal use, templated prompt for LLM with compact bullet-style summary and a strict schema for the LLM’s response.
- All the information should be limited to what a human player would be able to see or interpret so things like exact enemy health shouldnt be shown

Implementation Steps:
- Create `context/` package with `context_builder.py` and `prompt_templates.py`.
- Refactor `agent.py` to request context snapshots before each decision point.
- Add compression/limits (token-aware formatting; max lengths per section).

Milestones:
- v1: Static snapshot assembled from `state.json` + memories in `agent.py`.
- v2: Include short-term memory buffer and rolling summaries.

Testing:
- Unit test context assembly with synthetic `state.json`.
- Validate prompt token counts against model context window.

---

## 2) Task/Subtask System (Hierarchical Planning)

Rationale: Enable large objectives like “Explore city” or “Beat Gym” while delegating execution to smaller, reusable subtasks (navigate blocks, talk-to-NPC, clear dialog, heal at center, etc.). Persist task state across multiple LLM calls.

Design:
- Core concepts:
  - Task: High-level goal with success criteria and decomposition strategy.
  - Subtask: Concrete, executable unit (state machine) with clear preconditions and completion checks.
  - Blackboard/TaskManager: Stores active task tree, progress, and next required actions.
- Flow:
  1) Planner (LLM-assisted) creates/updates a task tree periodically or on major events.
  2) Executor runs the active leaf subtask, emitting low-level actions until done or blocked.
  3) Monitor checks success/timeout; escalates or replans when needed.
- Persistence: Serialize task tree to `tasks_state.json` to survive restarts.

Initial Task Library (examples):
- ExploreArea(map_region, budget_steps)
- MoveTo(tile|landmark)
- InteractAt(target: NPC|Sign|Door)
- FinishTextBoxes()
- HealAtPokemonCenter()
- ManageParty(swap: lead_to_index)
- SaveGame(note)

Implementation Steps:
- Create `tasks/` package:
  - `base.py` (Task, Subtask, status enums, serialization)
  - `executor.py` (runs active leaf, translates to actions)
  - `planner.py` (LLM-assisted high-level planning with guardrails)
  - `library/` (concrete subtasks as above)
- Integrate with `agent.py` main loop to choose between plan/update vs execute.

Milestones:
- v1: Deterministic subtasks (FinishTextBoxes, MoveTo by simple path, SaveGame, SwapLead).
- v2: LLM-assisted high-level Task creation (ExploreArea -> sequence of MoveTo+Interact).

Testing:
- Simulate states to verify subtask termination conditions.
- Dry-run planner prompts with mocked LLM responses.

---

## 3) Action Interface Expansion and Formalization

Rationale: Increase the work accomplished per decision by supporting parameterized, semantically rich actions instead of primitive button presses.

Design:
- Define an Action schema (JSON) with type, params, and optional metadata.
- Action types (initial set):
  - navigate: { to: { x,y | landmark }, path_hint?: [...], allow_encounters?: bool }
    - navigation should be allowed via path sequence, so go to A first then B etc...
  - interact: { target: npc|sign|door|object }
  - finish_text: {}
  - battle_decision: { move_index|run|switch_index|use_item }
  - party_swap: { from_index, to_index }
  - save_game: { note?: string }
  - wait_until: { condition, timeout_ms }
  - talk to npc
  - enter house/go to stairs/go to warp
  - search grass for pokemon
  - noop: {}
- Translator layer maps Action -> concrete control intents for Lua.
- Validation: strict schema check prior to dispatch; reject malformed/unsafe actions.
- remove all actions that are pre programmed like "go to oaks lab" and instead adjust things to allow the system to determine its objective then explore areas/use context to navigate itself.

Implementation Steps:
- Create `actions/schema.py` (pydantic/dataclasses) + `actions/translator.py`.
- Replace direct writes to `action.txt` with structured dispatch (file or socket until IPC revamp is ready).
- Update Lua side to parse and execute structured actions.

Milestones:
- v1: JSON-based file exchange using new schema.
- v2: Socket-based transport (see IPC section) with same schema.

Testing:
- Golden tests for Action -> control intents.
- Round-trip tests between Python and Lua using sample actions.

---

## 4) Reduce Polling and Decouple From Frame Rate

Rationale: The AI only needs fresh state at decision boundaries, not per frame.

Design:
- Event/trigger-based decision loop:
  - Triggers: text box opened/closed, player idle after movement, battle state change, map transition, timeout.
  - Debounce and coalesce rapid events.
- Decision cadence:
  - Executor runs subtasks continuously using local heuristics.
  - LLM planner invoked only on: task completion, blocked state, or scheduled replans.

Implementation Steps:
- In Lua, raise structured events when meaningful state changes occur.
- In Python, maintain an event queue; process to update internal state and decide if LLM call is warranted.
- Introduce a simple rate-limiter for LLM calls and a minimal cooldown.

Milestones:
- v1: File-based event queue with timestamps.
- v2: IPC events over socket with backpressure and batching. This may be done in objective 6

Testing:
- Simulate bursty events and ensure the rate-limiter blocks redundant LLM calls.

---

## 5) Structured Logging and Telemetry

Rationale: Improve observability and debugging; separate logs by concern.

Design:
- Log streams (rotating files, JSON lines for machine parsing):
  - agent.log: high-level decisions, task transitions, errors.
  - llm.log: prompts, responses, timings, tokens, model, exit status.
  - actions.log: emitted actions with parameters and outcomes.
  - events.log: incoming events/state deltas.
  - ipc.log: transport-level details (socket connect/send/recv/errors).
- Add correlation IDs per decision cycle and propagate through logs.
- Optional lightweight UI: tail dashboard or web viewer later.

Implementation Steps:
- Create `logging_config.py` with a structlog or standard logging JSON formatter.
- Replace ad-hoc file writes with structured loggers.
- Add timing and counters (prometheus-like metrics optional).

Milestones:
- v1: JSON logs with correlation IDs and rotation.
- v2: Metrics and a minimal viewer.

Testing:
- Validate logs contain expected fields and are easy to grep/aggregate.

---

## 6) Python-Lua IPC via Socket/API

Rationale: Move from ad-hoc text files to reliable, bidirectional communication.

Design Options:
- TCP/UDP sockets (local): simple, minimal deps, stream JSON messages.
- Unix domain sockets: faster on Linux, local-only.
- HTTP on localhost: easy debugging, slightly heavier.

Recommended: Unix domain sockets for local dev; fall back to TCP if needed.

Message Protocol:
- JSON messages with envelope: { type, id, ts, payload }
- Types: state_update, event, action_request, action_ack, error, heartbeat.
- Keep messages small; chunk large payloads (e.g., screenshots) if later needed.

Implementation Steps:
- Create `ipc/` package:
  - `server.py` (Python) to accept Lua clients.
  - `client_lua.lua` that connects and exchanges JSON.
  - `protocol.py` for message schemas and validation.
- Migrate existing file-based pathways incrementally (feature flag).

Milestones:
- v1: Action dispatch over socket; state still via file.
- v2: Events and state over socket; deprecate files entirely.

Testing:
- Integration test: echo server/client with backpressure and reconnect logic.

---

## Roadmap and Work Plan

Order of implementation (minimizes risk):
1) Logging foundations (Objective 5 v1).
2) Action schema + translator (Objective 3 v1, file-based).
3) ContextBuilder and prompt templates (Objective 1 v1).
4) Deterministic subtasks + executor (Objective 2 v1) using the new actions.
5) Event-driven decision cadence with reduced polling (Objective 4 v1).
6) IPC migration to sockets (Objective 6 v1) reusing the action schema.
7) LLM-assisted high-level planner (Objective 2 v2) with guardrails.
8) Replace remaining file paths with socket messages (Objective 3/4/6 v2).

Each step should produce a runnable system and include smoke tests.

---

## File/Module Plan (to be created or refactored)

- actions/
  - schema.py
  - translator.py
- context/
  - context_builder.py
  - prompt_templates.py
- tasks/
  - base.py
  - executor.py
  - planner.py
  - library/
    - finish_text.py
    - move_to.py
    - save_game.py
    - party_swap.py
- ipc/
  - protocol.py
  - server.py
  - client_lua.lua
- logging_config.py

Refactors:
- agent.py: integrate logging, context, tasks, and action dispatch
- navigator.py: expose utilities to support MoveTo and exploration subtasks
- scripts/*.lua: emit events, consume structured actions; or wrap with client_lua.lua

---

## LLM Prompt and Response Contract (Draft)

- Planner prompt: receives compact context, task backlog, constraints; outputs a task plan JSON adhering to a provided schema. Include strict functions/tools format or JSON schema in prompt to keep it parseable.
- Executor prompt (if needed by small models): discouraged; prefer deterministic subtasks. If used, restrict to selecting between whitelisted actions with bounded params.
- Response validation: must pass schema check; otherwise retry with a short correction prompt.

---

## Development and Testing Notes

- Keep deterministic fallbacks; do not block gameplay on LLM success.
- Rate-limit LLM usage; use cached answers for repeated patterns when safe.
- Seed reproducible tests using saved states and mock Lua.
- Ensure all long-running loops are interruptible and support graceful shutdown.

---

## Setup

- Local LLM via Ollama: see `setup_llm.sh` and `llm_client.py` for model config.
- Emulator + Lua hooks: place Lua scripts from `scripts/` into the emulator’s script dir; ensure they can read/write to this repo path during file-based phase.

---

## License

Personal project; no license specified yet.
