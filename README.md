# Emberfall release agent

A small OpenMinion 0.1.22 agent that prepares a ship decision for **Emberfall**, a twin-stick survival game. It reads a changelog and a playtest log, returns a structured release packet (highlights, fixes, known issues, prioritized bugs, hold / ship-with-caveats / ship), and writes `RELEASE_NOTES.md` plus `release-packet.json` only after a human approves the exact bytes.

The sample inputs under `examples/emberfall/` are fixtures for the 0.4.0 "Cinder Orchard" patch. They are shaped like a real closed-beta changelog and playtest log. They are not a dump of a private game repository.

This is a hackathon entry for [Cortensor Hackathon #5](https://docs.cortensor.network/community-and-ecosystem/hackathon/hackathon-5). There is no frontend and no multi-agent setup.

## Install

Python 3.11 or newer. This workspace was exercised on Python 3.12.3 with `openminion==0.1.22`.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Check the SDK import the quickstart names:

```bash
python -c "from openminion import Agent, ProviderError, TurnTimeoutError, tool; print('SDK 0.1.22 ready')"
```

## Run the offline demo

No API key is required. The default provider is OpenMinion's echo provider. Echo does not call a model, so this project installs an in-process completer on `EchoProvider.complete` for the release turn only. The completer decides the next tool call. The SDK still executes the tools, keeps the session, validates the Pydantic packet, and asks the approval callback before the write. Details and the reason for the completer are in [What We Learned](#what-we-learned).

Unattended write (the path the tests use):

```bash
python -m release_agent \
  --changelog examples/emberfall/CHANGELOG.md \
  --bugs examples/emberfall/playtest-log.md \
  --out dist/emberfall-0.4.0 \
  --approval yes
```

Prompt before writing:

```bash
python -m release_agent \
  --changelog examples/emberfall/CHANGELOG.md \
  --bugs examples/emberfall/playtest-log.md \
  --out dist/emberfall-0.4.0 \
  --approval ask
```

Refuse the write and leave the directory empty:

```bash
python -m release_agent \
  --changelog examples/emberfall/CHANGELOG.md \
  --bugs examples/emberfall/playtest-log.md \
  --out dist/emberfall-0.4.0 \
  --approval no
```

On the Cinder Orchard sample the packet recommends **hold**, because BUG-118 is an open P0 crash. An approved run writes:

- `dist/emberfall-0.4.0/RELEASE_NOTES.md`
- `dist/emberfall-0.4.0/release-packet.json`

Exit codes: `0` files written, `2` approval denied or the write did not happen, `3` `ProviderError`, `4` `TurnTimeoutError`, `5` structured output failed validation, `1` other usage or runtime errors.

Useful flags: `--draft-only`, `--session-id`, `--timeout`, `--home` (isolated OpenMinion config and sqlite state, default `.openminion-home/`), `--provider`, `--model`, `--base-url`.

## Tests

```bash
pytest
```

The suite covers the changelog and bug parsers, the read and write sandbox, the offline planner, a full echo run that approves the write, a full echo run that denies it, `AgentOutputValidationError` on a non-JSON reply, `TurnTimeoutError` from `timeout_seconds`, and `ProviderError` when `openai` is selected without `OPENAI_API_KEY`.

Last run in this workspace: **16 passed**.

## Switch to a real model

Copy `.env.example` to `.env` or export the variables. The CLI loads `.env` from the working directory and does not override variables that are already set. Do not commit `.env`.

Echo stays the default until you change `OPENMINION_PROVIDER`. The offline completer is installed only when the provider is `echo`. Any other provider uses the same `Agent`, tools, schema, session, and approval callback, and the model has to choose the tool calls itself.

`OPENMINION_MODEL` is ignored for echo. OpenMinion raises `ConfigError: Runtime model overrides are not supported for the echo provider` if you pass a model override into an echo run. For every other provider the value is written into that provider's `model` field in the generated `agents.json`.

```bash
# OpenAI
export OPENMINION_PROVIDER=openai
export OPENMINION_MODEL=gpt-4.1-mini
export OPENAI_API_KEY=sk-...

# Anthropic
export OPENMINION_PROVIDER=anthropic
export OPENMINION_MODEL=claude-sonnet-5
export ANTHROPIC_API_KEY=...

# OpenRouter
export OPENMINION_PROVIDER=openrouter
export OPENMINION_MODEL=openai/gpt-4.1-mini
export OPENROUTER_API_KEY=...

# Ollama on this machine
export OPENMINION_PROVIDER=ollama
export OPENMINION_MODEL=llama3.1
export OPENMINION_BASE_URL=http://127.0.0.1:11434

# Cortensor router (experimental in the 0.1.22 provider docs)
export OPENMINION_PROVIDER=cortensor
export OPENMINION_MODEL=gpt-oss-20b
export OPENMINION_BASE_URL=http://127.0.0.1:8080/api/v2/completions
export CORTENSOR_API_KEY=...
```

Then run the same command. Drop `--approval yes` the first time so you can see the approval prompt. The draft instructions tell the model to call `read_release_file` twice, call `compile_release_packet`, and return only that JSON object. The publish instructions tell it to call `write_release_files` once with the exact bytes. `forced_tools` on the draft turn limits the offered tools to the two read/compile tools.

Supported provider ids match `openminion config init --provider`: `echo`, `openai`, `anthropic`, `claude`, `openrouter`, `cerebras`, `groq`, `ollama`, `cortensor`.

## Input format

The changelog is a Keep-a-Changelog heading plus `### Added`, `### Changed`, `### Fixed`, and `### Known issues` bullets:

```markdown
# Emberfall

## [0.4.0] - 2026-10-18 — Cinder Orchard

### Added
- One highlight
```

The playtest log is one `## BUG-123` block per open bug, with `title`, `severity`, `area`, and `summary` fields. Severities map to priority like this: crash, blocker, and critical are P0; major and high are P1; minor and medium are P2; trivial, cosmetic, and low are P3. Anything else is P2. P0 recommends `hold`. P1 with no P0 recommends `ship-with-caveats`. Otherwise the recommendation is `ship`.

## SDK workflow

One process, two turns, one shared `APIRuntime`.

1. `prepare_runtime_home` runs `python -m openminion config init --provider <name> --force` inside `--home`, then patches `model` and `base_url` when those were set. The generated profile uses `default_channel: console`, which the default authenticity policy trusts.
2. The draft `Agent` is constructed with `instructions`, `output_type=ReleasePacket`, `tools=[read_release_file, compile_release_packet]`, `forced_tools` for those two names, and `session_id`.
3. `agent.run(draft_message, timeout_seconds=...)` reads both files through the tools and returns `result.output` as a validated `ReleasePacket`. `result.text` is the raw reply, which echo prefixes with the profile name (`openminion: `). The SDK's object extractor still accepts the JSON.
4. The CLI prints the release notes. Nothing is on disk yet.
5. A second `Agent` shares the same runtime and `session_id`, exposes `write_release_files`, and calls `run(..., thread_id=draft.thread_id, approval_callback=...)`.
6. The write tool is a normal `@tool(idempotent=False)` with no `min_scope`. The default action policy treats that as a write of unknown reversibility and calls `approval_callback(tool_name, args, approval_id)`. The callback prints the tool, the approval id, the directory, and the sha256 of both files, then returns true or false. `--approval ask` reads stdin. `yes` and `no` are unattended.
7. On allow, the tool writes the two files. On deny, the tool function is not called and the turn body is `Confirmation denied. The action was not run.`
8. `ProviderError`, `TurnTimeoutError`, and `AgentOutputValidationError` are caught at the CLI and mapped to exit codes 3, 4, and 5.

Read tools declare `@tool(min_scope="READ_ONLY", idempotent=True)`, so the default policy runs them without a prompt. Both tools refuse paths outside the changelog directory, the bug-log directory, or the requested output directory.

## Building Blocks Tested

Tested in this repo, on `openminion==0.1.22`:

| Block | What we did | Result |
| --- | --- | --- |
| `Agent` facade | Draft and publish agents, `instructions`, `with` cleanup, shared `APIRuntime` | Works. Closing an agent that was given `runtime=` does not close that runtime. |
| `@tool` | `read_release_file`, `compile_release_packet`, `write_release_files` | Works. Undecorated callables are rejected by `Agent`. |
| Scope and approval policy | `READ_ONLY` + `idempotent=True` versus a bare write tool | Reads run with no prompt. The write calls `approval_callback`. |
| `forced_tools` | Draft turn only | The draft offers the read and compile tools. Forcing the write tool on a continued thread made the entry phase reject the call. See below. |
| Sessions | `session_id="emberfall-0.4.0"` on both agents | Both results report that session id. |
| `thread_id` | Publish run passes `draft.thread_id` | Both turns reported the same id (`emberfall-0.4.0:<uuid>`). A completed turn still has a `thread_id`; it does not mean the turn is waiting. |
| `output_type` | `ReleasePacket` on the draft turn | `result.output` is the validated model. The sample packet matches the local parser byte for byte after approval. |
| `approval_callback` | Approve and deny | Allow writes the files. Deny returns `Confirmation denied. The action was not run.` and writes nothing. |
| `ProviderError` | `OPENMINION_PROVIDER=openai` with no `OPENAI_API_KEY` | `code=PROVIDER_ERROR`, message tells you to export `OPENAI_API_KEY`, `details={}`. Raised in well under a second. |
| `TurnTimeoutError` | `timeout_seconds=0.05` on a warm runtime, and `--timeout 0.05` on the CLI | Raises `Turn execution timed out after 0.05 seconds.` |
| `AgentOutputValidationError` | Echo reply `this is not json` with `output_type=ReleasePacket` | `raw_text` keeps the reply. The CLI prints it and exits 5. |
| Echo provider and config | `openminion config init --provider echo` | Enough to boot the runtime with no secrets. Not enough to choose tools. |
| Run metadata | `result.metadata` | Reports `provider=echo` and `model=brain-orchestrator`, plus `llm_calls_count`. One one-tool turn made 8 provider calls. |

Not exercised here: handoffs, subagents, memory bundles, skills, schedules, `run_stream`, MCP, and a live Cortensor Portal or router call. Portal access was not available in this workspace.

## What We Learned

The public names in the 0.1.22 quickstart match the installed package: `Agent`, `@tool`, `session_id`, `thread_id`, `output_type`, `approval_callback`, `ProviderError`, `TurnTimeoutError`, and `AgentOutputValidationError` all import and behave as documented. The gaps below are from running that package, not from a different API.

### What worked

- A read-only idempotent tool really does skip the prompt, and a write tool really does stop for `approval_callback`. Deny does not call the function. The body on deny, prefixed by the profile name, is `openminion: Confirmation denied. The action was not run.`
- `output_type` validates a JSON object even when the body is `openminion: {...}`. The prefix is applied to echo replies. The extractor pulls the first JSON object out of surrounding text.
- `session_id` and `thread_id` round-trip. The publish turn was given the draft's `thread_id` and both results carried `emberfall-0.4.0:94a286ac78134bb784598ec3efbe944b` on the demo run.
- `timeout_seconds` and a missing hosted key surface as the documented exception types, distinct from each other.
- `APIRuntime.from_config_path(..., home_root=, data_root=)` lets two agents share one runtime without passing both `runtime=` and `config_path=`.

### Friction and bugs

**1. Echo cannot drive this workflow, and the tool-capable test provider is not selectable.**

Echo repeats the last user text and returns no tool calls. That matches the docs. The package also contains `StubProvider`, whose `complete()` turns a user message of the form `tool:<name> {json}` into a tool call. Setting the agent provider to `stub` never reaches it:

```text
ProviderError PROVIDER_ERROR
Unknown provider 'stub'. Supported: anthropic, cerebras, claude, cortensor, echo, groq, ollama, openai, openrouter
```

Repro: `openminion config init --provider echo`, set `agents.openminion.provider` to `stub`, construct `Agent`, call `run`. Because of that, the offline path patches `EchoProvider.complete` for the duration of the release run and restores it afterwards. Phases that do not offer the release tools still use the original echo adapter. This patch is an in-process mock, not a public provider.

**2. A minimal `agents.json` without `default_channel` fails the first turn.**

```text
RuntimeError: inbound authenticity denied (reason=invalid_channel, mode=warn)
```

The quickstart `Agent(...)` example does not set a channel. An empty channel is denied even when authenticity `mode` is `warn`. `openminion config init` writes `default_channel: console`, and `console` is in the default trusted channel list, which passes. Repro: an `agents.json` whose only agent is `{ "name": "default", "provider": "echo" }` and no `default_channel`.

**3. Echo rejects `Agent(model=...)`.**

```text
ConfigError
Runtime model overrides are not supported for the echo provider.
```

Repro: `Agent(model="echo:echo").run("hi")` against an echo config. The CLI warns and drops `OPENMINION_MODEL` when the provider is echo. For other providers the model is stored on the provider block instead of sent as `override_model`.

**4. Tool results longer than 1200 characters are clipped before the next model call.**

`openminion.modules.brain.constants.TOOL_MESSAGE_STRING_LIMIT` is `1200`. A tool that returns a longer string comes back to the model as `text[:1200] + "...[truncated]"`. `compile_release_packet` returns about 2.4 KB of packet JSON. Using that clipped tool message as the draft's final answer raised `AgentOutputValidationError`. The visible cut was inside a bug title: `Orchard Heart root slam hard-crashes at empty ...[truncated]`.

The final assistant text is not clipped the same way. A probe that returned a 4000-character assistant string came back intact (`len(result.text) == 4042` including the `openminion: ` prefix). The offline completer therefore keeps the full packet the tool computed and emits that string as the assistant's final answer. A real model that trusts the tool message will see the clip. Repro: decorate a function that returns `"x" * 2000`, force a second provider call, and inspect the tool message content.

The playtest fixture is kept under that limit on purpose so a real model can see the whole log. The changelog fixture is about 1.1 KB.

**5. A tool call on a continued thread must carry `task_update` or the turn is rejected.**

Reusing `draft.thread_id` and calling `write_release_files` with only the file arguments produced:

```text
I could not safely update the task conditions. Please restate the current task and its required conditions.
```

A short text follow-up on the same thread does work. Probe: `run("Remember that the launch color is teal.")`, then `run("What is the launch color?", thread_id=first.thread_id)`. Echo answered by repeating the second sentence, and the thread id stayed the same. No task update was required because that reply had no tool call.

The failing path is an entry tool call on a thread that already has a task. `resolve_entry_task_update` raises `Missing task_update for a clarification or active request` when the task exists and the tool call has no `task_update`. The first draft turn's task was revision 1 with `objective_kind=verbatim`, so the follow-up has to be `operation: update` (not `start`), `expected_revision: 1`, a `user_quote` copied verbatim from the new user message, and a resolved `objective`. The runtime strips `task_update` before the tool handler runs. `forced_tools=["write_release_files"]` made that write the entry tool, which is why the publish agent offers the tool but does not force it.

**6. `timeout_seconds` does not cover runtime construction.**

A cold `Agent.run("hello", timeout_seconds=0.3)` completed in about 0.68s and did not raise. The gateway portion finished inside 0.3s; constructing the runtime sat outside the deadline. After that same agent was warm, `timeout_seconds=0.05` raised `TurnTimeoutError` in about 0.07s. `timeout_seconds=0.001` is reported as `Turn execution timed out after 0.00 seconds.` because the message formats with two decimal places.

`services/runtime/ingress/gateway_call.py` defines `_run_coro_sync(..., timeout)` and immediately does `del timeout`. The deadline that actually fires is the `asyncio.timeout` inside `await_with_cancel`. That inner deadline is what raised `TurnTimeoutError` for us. The deleted argument means the thread that runs the coroutine is joined with no outer timeout of its own.

**7. One user turn is many provider calls.**

A single `read_note` turn reported `llm_calls_count` 8 and `llm_call_counts_by_purpose` of entry 1, freshness_classify 2, act 3, judge 1, reflect 1. The offline completer has to no-op on phases whose only tool is `submit_output`, or those classifiers receive a release tool call instead of the JSON they asked for. Echo's non-JSON reply is tolerated there by the runtime's fallback. That fallback is why a scripted completer can ignore those phases, and it is also why echo cannot be used as evidence of model-selected tool use.

**8. The Agent callback and the turn-handle approval API are different.**

`Agent.run(approval_callback=)` takes `(tool_name, args, approval_id) -> bool`. The runtime invokes an internal wrapper with a fourth `policy_facts` argument, and `Agent` drops it before calling the application. `RuntimeTurnHandle.resolve_approval` instead wants the strings `allow_once` or `deny`. We used the boolean callback. It is the one that fires for a `@tool` write under the default policy.

### Pending

- **Real-model run.** No provider API key was available here. The switch steps above are what we would run next. Expect the 1200-character tool-result clip to matter: the packet JSON does not fit in a tool message, so a real model should build the final JSON from the file texts it saw, not from the clipped `compile_release_packet` return value.
- **Demo video.** Not recorded. A 1–5 minute capture of `--approval ask` on the Cinder Orchard sample, including the deny path, is the missing submission piece.
- **Cortensor Portal / router.** The `cortensor` provider id is wired through the same env switch, with the 0.1.22 default model `gpt-oss-20b` and base URL `http://127.0.0.1:8080/api/v2/completions`. Nothing was listening, and there was no `CORTENSOR_API_KEY`, so that path was not executed.

## Layout

```text
release_agent/    CLI, tools, parser, offline completer, workflow
examples/emberfall/   sample changelog and playtest log
tests/            parsers, tools, planner, approval, provider and timeout errors
```

## License

MIT. See [LICENSE](LICENSE).
