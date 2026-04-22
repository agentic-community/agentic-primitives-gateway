# Agent Teams

Agent teams allow multiple specialized agents to collaborate on complex tasks using a shared task board. A **planner** decomposes work, **workers** execute tasks concurrently, and a **synthesizer** combines the results into a final response. Between worker waves, a **continuous replanning** loop evaluates completed results and creates follow-up tasks.

## Architecture

```
Request
  |
  v
+-------------------+
| Phase 1: Planning |  Planner agent decomposes the request into tasks
|  (planner agent)  |  on the shared task board. Uses create_task tool.
+-------------------+
  |
  v
+-------------------------------+
| Phase 2: Execution + Replan   |  <-- continuous loop
|                               |
|  1. Launch all workers        |  Workers poll the board, claim tasks,
|  2. Workers claim & execute   |  execute them using their primitives,
|  3. Workers exit when idle    |  and mark tasks done/failed.
|  4. Re-planner evaluates      |  Re-planner reviews completed results
|  5. New tasks? Go to 1        |  and may create follow-up tasks.
|     No new tasks? Done        |
+-------------------------------+
  |
  v
+---------------------+
| Phase 3: Synthesis  |  Synthesizer agent reads all completed task
|  (synth agent)      |  results and produces a coherent final response.
+---------------------+
  |
  v
Final Response
```

## Team Spec

Defined in YAML config or via the API:

```yaml
teams:
  specs:
    research-team:
      description: "A team that researches topics and writes code"
      planner: "planner"           # Agent name for task decomposition
      synthesizer: "synthesizer"   # Agent name for result synthesis
      workers: ["researcher", "coder"]  # Agent names that do the work
      global_max_turns: 100        # Safety limit across all agents
      global_timeout_seconds: 300  # Wall-clock timeout
      shared_memory_namespace: "team:{team_name}:shared"  # Optional shared memory
```

### Fields

| Field | Description | Default |
|-------|-------------|---------|
| `name` | Unique identifier | required |
| `description` | Human-readable description | `""` |
| `planner` | Agent name for task decomposition | required |
| `synthesizer` | Agent name for result synthesis | required |
| `workers` | Agent names that execute tasks | required |
| `max_concurrent` | Max workers running simultaneously | `None` (unlimited) |
| `global_max_turns` | Safety limit across all agents | `100` |
| `global_timeout_seconds` | Wall-clock timeout for the entire run | `300` |
| `shared_memory_namespace` | Namespace for team-scoped shared memory | `None` (disabled) |
| `checkpointing_enabled` | Enable durable checkpoint persistence | `false` |

Each named agent (`planner`, `synthesizer`, `researcher`, `coder`) must exist in the agent store with its own model, system prompt, and primitives.

## Task Board

The task board is an in-memory (or provider-backed) shared state store scoped to a single team run (`team_run_id`). Every task has:

| Field | Description |
|-------|-------------|
| `id` | Unique ID (auto-generated) |
| `title` | Short description |
| `description` | Detailed instructions |
| `status` | `pending` -> `claimed` -> `in_progress` -> `done`/`failed` |
| `assigned_to` | Worker agent that claimed this task |
| `suggested_worker` | Worker the planner recommends (soft assignment) |
| `depends_on` | List of task IDs that must be `done` before this task is available |
| `result` | Output from the worker (stored on completion) |
| `notes` | Agent-to-agent communication (any agent can add notes) |
| `priority` | Higher = more important |

### Task Lifecycle

```
pending  -->  claimed  -->  in_progress  -->  done
                                          \-> failed
```

A task is **available** when:
- Status is `pending`
- All tasks in `depends_on` have status `done`
- If `suggested_worker` is set, only that worker can claim it

## Phase 1: Planning

The planner agent receives:
- The original user request
- A list of available workers with their descriptions and capabilities
- The `create_task` and `list_tasks` tools

The planner's system prompt instructs it to:
- Decompose the request into specific, actionable tasks
- Assign each task to the appropriate worker (`assigned_to`)
- Set dependencies between tasks when ordering matters
- Only create tasks that can be fully described right now (defer vague tasks to replanning)

Example: For "Research Python web frameworks and benchmark them", the planner might create:
- Task 1: "Research top 3 Python web frameworks" (assigned: researcher)
- Task 2: "Write benchmark script" (assigned: coder, depends_on: [task-1])

## Phase 2: Execution with Continuous Replanning

This is the core loop in `_run_with_replanning`:

```python
reviewed_tasks: set[str] = set()  # Track which completions have been evaluated

while True:
    # 1. Launch ALL workers concurrently
    #    Each worker polls the board, claims available tasks, executes them
    await asyncio.gather(*[worker_loop(w) for w in workers])

    # 2. Check for newly completed tasks since last review
    newly_completed = [t for t in all_tasks
                       if t.status == "done" and t.id not in reviewed_tasks]
    if not newly_completed:
        break  # Nothing new to evaluate

    # 3. Mark these as reviewed so we don't re-evaluate them
    for t in newly_completed:
        reviewed_tasks.add(t.id)

    # 4. Run the re-planner
    #    It sees: original request + completed results + pending tasks
    #    It decides: create follow-up tasks or not
    new_task_count = await run_replanner(...)
    if new_task_count == 0:
        break  # Planner is satisfied, no more work needed

    # 5. New tasks exist -> restart workers to pick them up
```

### Worker Loop

Each worker runs independently:

```python
while True:
    available = get_available_tasks(worker_name)  # Pending + deps met + assigned to me
    if not available:
        if no_incomplete_tasks():
            break  # All work is done
        sleep(1)   # Wait for other workers to finish dependencies
        continue

    claimed = claim_batch(available)  # Atomic claim to prevent races
    await gather(*[execute(task) for task in claimed])  # Parallel execution
```

Workers have access to:
- Their own primitive tools (memory, browser, code_interpreter, etc.)
- Task board tools (`complete_task`, `fail_task`, `add_task_note`, `get_available_tasks`, `create_task`)
- Upstream context: results from tasks in `depends_on` are injected into the worker's prompt

### Re-planning Prompt

The re-planner receives:
- The original user request
- All completed task results (title + result preview)
- All pending/active tasks
- Worker descriptions

It's asked: "Based on the completed results, do any NEW follow-up tasks need to be created?"

Key guidelines in the prompt:
- Review results for specific details that enable new concrete tasks
- Don't recreate tasks that already exist
- If no new tasks are needed, respond with text only (no tool calls)

### Why Continuous Replanning?

Without replanning, the planner must decompose everything upfront. But often early tasks reveal information needed to plan later tasks:

```
Wave 1 Planning:
  -> "Research frameworks" (researcher)
  -> "Write benchmarks" (coder) -- but for WHICH frameworks? Unknown yet.

Wave 1 Execution:
  -> researcher finds: FastAPI, Django, Flask

Replanning (after wave 1):
  -> Replanner sees the research results, NOW knows which frameworks
  -> Creates: "Benchmark FastAPI", "Benchmark Django", "Benchmark Flask"

Wave 2 Execution:
  -> coder runs all three benchmarks in parallel

Replanning (after wave 2):
  -> All tasks complete, nothing new needed
  -> Loop ends
```

## Phase 3: Synthesis

The synthesizer agent receives:
- The original request
- All task results (completed and failed)
- Read-only task board access (`list_tasks`, `get_task`)

It produces a single coherent response combining all results.

## Streaming

The streaming endpoint (`POST /api/v1/teams/{name}/run/stream`) yields SSE events:

| Event | When |
|-------|------|
| `team_start` | Run begins, includes `team_run_id` |
| `phase_change` | Transitioning between planning/execution/replanning/synthesis |
| `tasks_created` | Planner/replanner created new tasks (includes task list) |
| `worker_start` | A worker agent began its loop |
| `task_claimed` | A worker claimed a specific task |
| `agent_token` | Token streamed from a worker/planner/synthesizer |
| `agent_tool` | An agent called a tool |
| `task_completed` | A worker finished a task (includes result) |
| `task_failed` | A worker's task failed |
| `worker_done` | A worker exited its loop |
| `worker_error` | A worker encountered an error |
| `done` | Final response with summary stats |

## File Structure

```
agents/
  team_runner.py      # TeamRunner: orchestrates planning/execution/synthesis
  team_agent_loop.py  # Generic LLM tool-call loops (shared by planner/worker/synth)
  team_prompts.py     # Prompt builders for each phase
  team_store.py       # FileTeamStore (JSON persistence for team specs)
models/
  teams.py            # TeamSpec, TeamRunResponse, TeamRunPhase
  tasks.py            # Task, TaskStatus, TaskNote
primitives/
  tasks/              # Task board provider (in_memory, noop)
routes/
  teams.py            # /api/v1/teams/* endpoints (CRUD + run + stream)
```

## Configuration Example

```yaml
agents:
  specs:
    planner:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Decomposes requests into tasks for team execution"
      system_prompt: |
        You are a task planner. Decompose requests into concrete tasks
        and assign each to the right team member.
      primitives: {}

    synthesizer:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Synthesizes team results into coherent responses"
      system_prompt: |
        You are a synthesizer. Combine multiple task results into a
        clear, comprehensive response.
      primitives: {}

    researcher:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Researches topics using memory and web browsing"
      primitives:
        memory: { enabled: true }
        browser: { enabled: true }

    coder:
      model: "us.anthropic.claude-sonnet-4-20250514-v1:0"
      description: "Writes and executes code"
      primitives:
        code_interpreter: { enabled: true }

teams:
  specs:
    research-team:
      description: "Researches and codes collaboratively"
      planner: "planner"
      synthesizer: "synthesizer"
      workers: ["researcher", "coder"]
      global_max_turns: 100
      global_timeout_seconds: 300
```

## Shared Memory

Teams support shared memory for inter-agent communication during a run. When `shared_memory_namespace` is set on the team spec, all workers receive additional tools:

| Tool | Description |
|------|-------------|
| `share_finding(key, content)` | Store a finding in the team's shared namespace |
| `read_shared(key)` | Read a specific shared finding by key |
| `search_shared(query)` | Search shared findings by semantic similarity |
| `list_shared()` | List all findings in the shared namespace |

The `{team_name}` placeholder in the namespace is expanded at runtime. Team shared memory is **cross-user by design** — the whole point is that workers (and the humans who run the team) collaborate on the same findings. If you need per-user isolation, use each worker's private memory (`remember`/`recall`/`search_memory`) instead of the shared pool.

This is **Level 1** shared memory (team-scoped, single namespace). For **Level 2** (agent-level pools via `shared_namespaces`), see [Agents](agents.md#shared-memory-pools).

### Example

```yaml
teams:
  specs:
    research-team:
      shared_memory_namespace: "team:{team_name}:shared"
      workers: ["researcher", "coder"]
```

The researcher can call `share_finding(key="framework-list", content="FastAPI, Django, Flask")`, and the coder can then call `read_shared(key="framework-list")` or `search_shared(query="frameworks")` to access the shared findings.

## Dependency-Aware Execution

Tasks can declare dependencies on other tasks via the `depends_on` field. A task is only available for a worker to claim when all its dependencies have status `done`. This enables multi-wave execution:

```
Wave 1: Research frameworks     (no dependencies)
Wave 2: Benchmark FastAPI       (depends on: research)
         Benchmark Django        (depends on: research)
         Benchmark Flask         (depends on: research)
Wave 3: Compare results         (depends on: all benchmarks)
```

Tasks within the same wave run in parallel. The worker loop polls the task board and only sees tasks whose dependencies are satisfied.

## Export

Teams can be exported as standalone Python scripts via `GET /api/v1/teams/{name}/export`. The generated script includes the planner, all worker agents with their primitive tools, and the synthesizer. It handles dependency-aware wave execution, per-task browser/code_interpreter session isolation, shared memory, and includes a live-updating terminal task board (via `rich` if available).

See the [Teams API Reference](../api/teams.md#export) for details.

## Task Retry

Individual failed tasks within a completed team run can be retried without re-running the entire team. `POST /api/v1/teams/{name}/runs/{id}/tasks/{task_id}/retry` resets the task to `in_progress`, recovers partial tokens from the event store, and re-executes the assigned worker. Returns an SSE stream.

See the [Teams API Reference](../api/teams.md#task-retry) for details.

## Background Runs & Persistence

**Background execution:** Streaming team runs execute in a background `asyncio.Task`. If the client disconnects, the run completes independently (workers finish their tasks, synthesizer produces the response). All events are recorded for later replay.

**Event replay:** On reconnect, the UI fetches all recorded events from `/{name}/runs/{id}/events` and replays them through the same event handler to reconstruct the full UI state: task board, activity log, streaming content, and synthesized response.

**Task board persistence:** With `RedisTasksProvider`, the task board survives across requests and is visible from any replica. With `InMemoryTasksProvider` (default), tasks exist only in the process that created them.

**Multiple runs:** Each team can have many runs. The UI stores run IDs and provides a run picker to switch between them.

## Checkpointing

Team runs can be made durable similarly to agent runs. The checkpoint stores the current phase (planning, execution, or synthesis). Task board state is already durable when using `RedisTasksProvider`. On resume, any in-progress tasks are reset to pending, and the current phase restarts with partial token recovery from the event store.

See [Configuration](../getting-started/configuration.md) for the `checkpointing` config block.

## Run Cancellation

An active team run can be cancelled via `DELETE /api/v1/teams/{name}/runs/{run_id}/cancel`. Cancellation is cooperative: the runner checks an `asyncio.Event` at every worker checkpoint. When triggered, all in-progress tasks are marked as failed and the run terminates. This works for both local runs and runs recovered from a checkpoint on another replica.

## SSE Reconnection

If a stream drops, clients can reconnect to `GET /api/v1/teams/{name}/runs/{run_id}/stream`. This replays all stored events from the event store and then polls for new events if the run is still active. Token events are throttled during replay for smooth delivery.

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/teams` | Create a team |
| `GET` | `/api/v1/teams` | List teams |
| `GET` | `/api/v1/teams/{name}` | Get team spec |
| `PUT` | `/api/v1/teams/{name}` | Update team |
| `DELETE` | `/api/v1/teams/{name}` | Delete team |
| `GET` | `/api/v1/teams/{name}/export` | Export as standalone Python script |
| `POST` | `/api/v1/teams/{name}/run` | Run team (non-streaming) |
| `POST` | `/api/v1/teams/{name}/run/stream` | Run team (SSE streaming, background task) |
| `GET` | `/api/v1/teams/{name}/runs` | List all runs |
| `GET` | `/api/v1/teams/{name}/runs/{id}` | Get task board state |
| `GET` | `/api/v1/teams/{name}/runs/{id}/status` | Check run status |
| `GET` | `/api/v1/teams/{name}/runs/{id}/events` | Get recorded events for replay |
| `GET` | `/api/v1/teams/{name}/runs/{id}/stream` | SSE reconnect stream |
| `DELETE` | `/api/v1/teams/{name}/runs/{id}/cancel` | Cancel active run |
| `DELETE` | `/api/v1/teams/{name}/runs/{id}` | Delete run data |
| `POST` | `/api/v1/teams/{name}/runs/{id}/tasks/{task_id}/retry` | Retry a failed task (SSE) |
