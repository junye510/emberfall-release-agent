# Emberfall release agent

A small OpenMinion 0.1.24 agent that prepares a ship decision for **Emberfall**, a twin-stick survival game. It reads a changelog and a playtest log, returns a structured release packet (highlights, fixes, known issues, prioritized bugs, hold / ship-with-caveats / ship), and writes `RELEASE_NOTES.md` plus `release-packet.json` only after a human approves the exact bytes.

The sample inputs under `examples/emberfall/` are fixtures for the 0.4.0 "Cinder Orchard" patch. They are shaped like a real closed-beta changelog and playtest log. They are not a dump of a private game repository.

This is a hackathon entry for [Cortensor Hackathon #5](https://docs.cortensor.network/community-and-ecosystem/hackathon/hackathon-5). There is no frontend and no multi-agent setup.

## Install

Python 3.11 or newer. This workspace was exercised on Python 3.12.3 with `openminion==0.1.24`.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Check the SDK import the quickstart names:

```bash
python -c "from openminion import Agent, ProviderError, TurnTimeoutError, tool; print('SDK 0.1.24 ready')"
```

## Run with Z.ai

Z.ai / Zhipu is the real-model provider for this project. The free model is `glm-4.7-flash` (function calling and structured output). The OpenMinion `zai` preset's own default is the paid `glm-5.2`, so the CLI always passes `glm-4.7-flash` unless `OPENMINION_MODEL` is set. The key is read only from `ZAI_API_KEY`.

International keys from z.ai use the preset endpoint `https://api.z.ai/api/paas/v4/`. Keys issued on [bigmodel.cn](https://open.bigmodel.cn/) need that site's OpenAI-compatible endpoint. Set `ZAI_BASE_URL` or `OPENMINION_BASE_URL` (the general variable wins when both are set). `--base-url` overrides both.

```bash
export OPENMINION_PROVIDER=zai
export OPENMINION_MODEL=glm-4.7-flash
export ZAI_API_KEY=...
# Keys from bigmodel.cn (China). Omit this line for a z.ai international key.
export ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/
python -m release_agent \
  --changelog examples/emberfall/CHANGELOG.md \
  --bugs examples/emberfall/playtest-log.md \
  --out dist/emberfall-0.4.0 \
  --approval ask
```

The same preset from the OpenMinion CLI:

```bash
# Use the same home the CLI uses. `openminion setup` writes
# $OPENMINION_HOME/.openminion/agents.json. OPENMINION_DATA_ROOT does not
# choose that path; point it at the same directory so state and config stay together.
export OPENMINION_HOME="$PWD/.openminion-home"
export OPENMINION_DATA_ROOT="$OPENMINION_HOME/.openminion"
export ZAI_API_KEY=...
openminion setup --provider zai --model glm-4.7-flash --no-chat
# bigmodel.cn keys:
# openminion setup --provider zai --model glm-4.7-flash \
#   --base-url https://open.bigmodel.cn/api/paas/v4/ --no-chat
openminion doctor --check-turn
```

The CLI `--home` flag is that `OPENMINION_HOME` (default `.openminion-home`). It prints `openminion_home=` and `openminion_config=` after a run. `$OPENMINION_HOME/.openminion/` is gitignored.

Hosted turns wait up to 300 seconds unless `--timeout` or `OPENMINION_TIMEOUT_SECONDS` is set. Echo stays at 120 seconds. On `glm-4.7-flash`, a turn that calls a tool has hit `TurnTimeoutError` at 180 seconds, so 300 leaves room for one provider retry cycle.

DeepSeek remains available (`OPENMINION_PROVIDER=deepseek`, model `deepseek-v4-flash`, key `DEEPSEEK_API_KEY`). An account with no balance fails that turn with `ProviderError` `402` `insufficient_balance_error`. That is why Z.ai is the recommended path, and it is recorded under [What We Learned](#what-we-learned).

## Run the offline demo

No API key is required. If `OPENMINION_PROVIDER` is unset, the CLI uses OpenMinion's echo provider. Pass `--provider echo` when a `.env` file already selects Z.ai. Echo does not call a model, so this project installs an in-process completer on `EchoProvider.complete` for the release turn only. The completer decides the next tool call. The SDK still executes the tools, keeps the session, validates the Pydantic packet, and asks the approval callback before the write.

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

Useful flags: `--draft-only`, `--session-id`, `--timeout` (hosted default 300 seconds, echo default 120), `--home` (this is `OPENMINION_HOME`; config is `<home>/.openminion/agents.json`, default `.openminion-home/`), `--provider`, `--model`, `--base-url`.

## Tests

```bash
pytest
```

The suite covers the changelog and bug parsers, the read and write sandbox, the offline planner, a full echo run that approves the write, a full echo run that denies it, `AgentOutputValidationError` on a non-JSON reply, `TurnTimeoutError` from `timeout_seconds`, `ProviderError` when `openai` is selected without `OPENAI_API_KEY`, the Z.ai preset (`glm-4.7-flash`, not the paid `glm-5.2`, including the bigmodel.cn base URL), the DeepSeek preset when its API key is missing, one retry for `RATE_LIMITED` and for a non-JSON final answer, the 300-second hosted timeout default, and `openminion setup` writing `agents.json` under `OPENMINION_HOME` rather than `OPENMINION_DATA_ROOT`.

Last run in this workspace: **29 passed** on `openminion==0.1.24`.

## Switch to a real model

Copy `.env.example` to `.env` or export the variables. The CLI loads `.env` from the working directory and does not override variables that are already set. Do not commit `.env`.

Echo stays the default until you change `OPENMINION_PROVIDER`. The offline completer is installed only when the provider is `echo`. Any other provider uses the same `Agent`, tools, schema, session, and approval callback, and the model has to choose the tool calls itself.

`OPENMINION_MODEL` is ignored for echo. OpenMinion raises `ConfigError: Runtime model overrides are not supported for the echo provider` if you pass a model override into an echo run. For every other provider the value is written into that provider's `model` field in the generated `agents.json`.

```bash
# Z.ai / Zhipu (recommended). glm-4.7-flash is the free model.
# The preset default glm-5.2 is paid and is not used unless you set it.
export OPENMINION_PROVIDER=zai
export OPENMINION_MODEL=glm-4.7-flash
export ZAI_API_KEY=...
# International keys use https://api.z.ai/api/paas/v4/ (the preset default).
# bigmodel.cn keys:
export ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/

# DeepSeek (alternative; needs account balance)
# export OPENMINION_PROVIDER=deepseek
# export OPENMINION_MODEL=deepseek-v4-flash
# export DEEPSEEK_API_KEY=sk-...

# OpenAI
# export OPENMINION_PROVIDER=openai
# export OPENMINION_MODEL=gpt-4.1-mini
# export OPENAI_API_KEY=sk-...

# Anthropic
# export OPENMINION_PROVIDER=anthropic
# export OPENMINION_MODEL=claude-sonnet-5
# export ANTHROPIC_API_KEY=...

# OpenRouter
# export OPENMINION_PROVIDER=openrouter
# export OPENMINION_MODEL=openai/gpt-4.1-mini
# export OPENROUTER_API_KEY=...

# Ollama on this machine
# export OPENMINION_PROVIDER=ollama
# export OPENMINION_MODEL=llama3.1
# export OPENMINION_BASE_URL=http://127.0.0.1:11434

# Cortensor router (experimental hosted path)
# export OPENMINION_PROVIDER=cortensor
# export OPENMINION_MODEL=gpt-oss-20b
# export OPENMINION_BASE_URL=http://127.0.0.1:8080/api/v2/completions
# export CORTENSOR_API_KEY=...
```

Then run the same command. Drop `--approval yes` the first time so you can see the approval prompt. The draft instructions tell the model to call `read_release_file` twice, call `compile_release_packet`, and return only that JSON object. The publish instructions tell it to call `write_release_files` once with the exact bytes. `forced_tools` on the draft turn limits the offered tools to the two read/compile tools.

`config init --provider` accepts `echo`, `openai`, `anthropic`, `claude`, `openrouter`, `cerebras`, `groq`, `ollama`, and `cortensor`. Z.ai and DeepSeek are not in that list. 0.1.24 configures them with `openminion setup`. Z.ai saves an OpenAI-compatible profile (`provider: openai`, default base URL `https://api.z.ai/api/paas/v4/`, `api_key_env: ZAI_API_KEY`) and this CLI passes model `glm-4.7-flash`. DeepSeek saves `base_url: https://api.deepseek.com`, `api_key_env: DEEPSEEK_API_KEY`, and model `deepseek-v4-flash` or `deepseek-v4-pro`. The release CLI runs that setup itself for `--provider zai` and `--provider deepseek`.

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

1. `prepare_runtime_home` writes an isolated config under `--home`. Echo and the other `config init` providers use `python -m openminion config init --provider <name> --force`, then patch `model` and `base_url` when those were set. Z.ai and DeepSeek use `python -m openminion setup --provider <preset> --model <model> --no-chat`. Z.ai's model is `glm-4.7-flash` and its base URL is `https://api.z.ai/api/paas/v4/` unless `ZAI_BASE_URL`, `OPENMINION_BASE_URL`, or `--base-url` points at `https://open.bigmodel.cn/api/paas/v4/` for a bigmodel.cn key. The generated profile uses `default_channel: console`, which the default authenticity policy trusts.
2. The draft `Agent` is constructed with `instructions`, `output_type=ReleasePacket`, `tools=[read_release_file, compile_release_packet]`, `forced_tools` for those two names, and `session_id`.
3. `agent.run(draft_message, timeout_seconds=...)` reads both files through the tools and returns `result.output` as a validated `ReleasePacket`. `result.text` is the raw reply, which echo prefixes with the profile name (`openminion: `). The SDK's object extractor still accepts the JSON.
4. The CLI prints the release notes. Nothing is on disk yet.
5. A second `Agent` shares the same runtime and `session_id`, exposes `write_release_files`, and calls `run(..., thread_id=draft.thread_id, approval_callback=...)`.
6. The write tool is a normal `@tool(idempotent=False)` with no `min_scope`. The default action policy treats that as a write of unknown reversibility and calls `approval_callback(tool_name, args, approval_id)`. The callback prints the tool, the approval id, the directory, and the sha256 of both files, then returns true or false. `--approval ask` reads stdin. `yes` and `no` are unattended.
7. On allow, the tool writes the two files. On deny, the tool function is not called and the turn body is `Confirmation denied. The action was not run.`
8. `ProviderError`, `TurnTimeoutError`, and `AgentOutputValidationError` are caught at the CLI and mapped to exit codes 3, 4, and 5. A `RATE_LIMITED` provider error and a non-JSON final answer each get one automatic retry. The JSON retry adds the instruction "Reply only with JSON." A second failure of the same kind exits with a message that names the Z.ai free-tier rate limit or the prose reply.

Read tools declare `@tool(min_scope="READ_ONLY", idempotent=True)`, so the default policy runs them without a prompt. Both tools refuse paths outside the changelog directory, the bug-log directory, or the requested output directory.

## Building Blocks Tested

Tested in this repo, on `openminion==0.1.24` (first brought up on 0.1.22, then re-run after the bump):

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
| `ProviderError` | OpenAI, Z.ai, or DeepSeek with the matching API key unset | `code=PROVIDER_ERROR`. The message names `OPENAI_API_KEY`, `ZAI_API_KEY`, or `DEEPSEEK_API_KEY`. Raised before a hosted call. |
| `TurnTimeoutError` | `timeout_seconds=0.05` on a warm runtime, and `--timeout 0.05` on the CLI | Raises `Turn execution timed out after 0.05 seconds.` |
| `AgentOutputValidationError` | Echo reply `this is not json` with `output_type=ReleasePacket` | `raw_text` keeps the reply. The CLI prints it and exits 5. |
| Echo provider and config | `openminion config init --provider echo` | Enough to boot the runtime with no secrets. Not enough to choose tools. |
| Z.ai preset | `openminion setup --provider zai --model glm-4.7-flash --no-chat` | Writes `provider: openai`, base URL `https://api.z.ai/api/paas/v4/` unless overridden, `api_key_env: ZAI_API_KEY`, and a `zai` connection whose model is `glm-4.7-flash` rather than the paid preset default `glm-5.2`. `ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4/` selects the China endpoint. |
| DeepSeek preset | `openminion setup --provider deepseek --model deepseek-v4-flash --no-chat` | Writes `provider: openai`, `base_url: https://api.deepseek.com`, `api_key_env: DEEPSEEK_API_KEY`, and a `deepseek` model connection. Kept as the documented alternative. |
| Run metadata | `result.metadata` | Reports `provider=echo` and `model=brain-orchestrator`, plus `llm_calls_count`. One one-tool turn made 8 provider calls. |

Not exercised here: handoffs, subagents, memory bundles, skills, schedules, `run_stream`, MCP, and a live Cortensor Portal or router call. Portal access was not available in this workspace.

## What We Learned

The public names in the 0.1.24 quickstart match the installed package: `Agent`, `@tool`, `session_id`, `thread_id`, `output_type`, `approval_callback`, `ProviderError`, `TurnTimeoutError`, and `AgentOutputValidationError` all import and behave as documented. The project was first run on 0.1.22. The same suite passes on 0.1.24 after one matcher change noted below. The gaps are from running that package, not from a different API.

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

On 0.1.24 the entry instruction changed from `include task_update` to `include exactly one task_update`. A check for the 0.1.22 phrase stopped attaching the sidecar, and the publish turn failed again with the same "could not safely update the task conditions" reply. Matching the field name `task_update` restored approve and deny. Repro on 0.1.24: run the release CLI with `--approval no` against echo while the completer only attaches `task_update` when the system prompt contains the exact 0.1.22 words.

**6. `timeout_seconds` does not cover runtime construction.**

A cold `Agent.run("hello", timeout_seconds=0.3)` completed in about 0.68s and did not raise. The gateway portion finished inside 0.3s; constructing the runtime sat outside the deadline. After that same agent was warm, `timeout_seconds=0.05` raised `TurnTimeoutError` in about 0.07s. `timeout_seconds=0.001` is reported as `Turn execution timed out after 0.00 seconds.` because the message formats with two decimal places.

`services/runtime/ingress/gateway_call.py` defines `_run_coro_sync(..., timeout)` and immediately does `del timeout`. The deadline that actually fires is the `asyncio.timeout` inside `await_with_cancel`. That inner deadline is what raised `TurnTimeoutError` for us. The deleted argument means the thread that runs the coroutine is joined with no outer timeout of its own.

**7. One user turn is many provider calls.**

A single `read_note` turn reported `llm_calls_count` 8 and `llm_call_counts_by_purpose` of entry 1, freshness_classify 2, act 3, judge 1, reflect 1. The offline completer has to no-op on phases whose only tool is `submit_output`, or those classifiers receive a release tool call instead of the JSON they asked for. Echo's non-JSON reply is tolerated there by the runtime's fallback. That fallback is why a scripted completer can ignore those phases, and it is also why echo cannot be used as evidence of model-selected tool use.

**8. The Agent callback and the turn-handle approval API are different.**

`Agent.run(approval_callback=)` takes `(tool_name, args, approval_id) -> bool`. The runtime invokes an internal wrapper with a fourth `policy_facts` argument, and `Agent` drops it before calling the application. `RuntimeTurnHandle.resolve_approval` instead wants the strings `allow_once` or `deny`. We used the boolean callback. It is the one that fires for a `@tool` write under the default policy.

**9. DeepSeek with no account balance returns ProviderError 402, so the recommended model is Z.ai's free glm-4.7-flash.**

DeepSeek is an OpenAI-compatible setup preset, not a `config init` provider name. A key that authenticates but has no balance does not look like a missing-key failure. The turn raises `ProviderError` with HTTP `402` and error code `insufficient_balance_error`. That was observed on another machine against `deepseek-v4-flash` at `https://api.deepseek.com` after `openminion setup --provider deepseek --model deepseek-v4-flash --no-chat`. This workspace has no `DEEPSEEK_API_KEY`, so the local check stops earlier: `ProviderError` `PROVIDER_ERROR` telling you to export `DEEPSEEK_API_KEY`, with no hosted request.

The recommended path is therefore Z.ai preset `zai` with the free model `glm-4.7-flash`. The preset's own recommended model is the paid `glm-5.2`, so leaving the model unset would select that paid id. The CLI passes `glm-4.7-flash` explicitly. Z.ai documents that model as supporting function calling and structured output. A key from z.ai uses `https://api.z.ai/api/paas/v4/`. A key from bigmodel.cn uses `https://open.bigmodel.cn/api/paas/v4/`, set with `ZAI_BASE_URL` or `OPENMINION_BASE_URL`. No `ZAI_API_KEY` is present here, so that hosted turn was not run. A missing key still raises `ProviderError` and names `ZAI_API_KEY`.

**10. `config init` and `setup` do not accept the same provider ids.**

`openminion config init --provider zai` and `--provider deepseek` are rejected. The working commands are `openminion setup --provider zai --model glm-4.7-flash --no-chat` and `openminion setup --provider deepseek --model deepseek-v4-flash --no-chat`. Non-interactive setup also refuses to write the profile unless the preset's key env var is set (`Setup failed: Missing credential for ...`). The CLI passes a placeholder into that subprocess only when the parent environment has no key, and the saved file keeps `api_key` empty with `api_key_env` set to `ZAI_API_KEY` or `DEEPSEEK_API_KEY`.

**11. glm-4.7-flash on a bigmodel.cn key: auth works on both endpoints, the free tier is one request at a time, and a turn can time out or skip the tool.**

Observed on our machine with `openminion==0.1.24`, `OPENMINION_PROVIDER=zai`, `OPENMINION_MODEL=glm-4.7-flash`, and a key issued on bigmodel.cn. This workspace has no key, so these calls were not repeated here.

Auth. Both endpoints accepted the key:

```bash
export OPENMINION_HOME="$PWD/.openminion-home"
export OPENMINION_DATA_ROOT="$OPENMINION_HOME/.openminion"
export ZAI_API_KEY=...
openminion setup --provider zai --model glm-4.7-flash \
  --base-url https://open.bigmodel.cn/api/paas/v4/ --no-chat
openminion doctor --check-turn
```

`https://api.z.ai/api/paas/v4/` also accepted that same key. Use the bigmodel.cn URL for a key from that site. `ZAI_BASE_URL` and `OPENMINION_BASE_URL` both override the preset.

Config path. `openminion setup` writes `agents.json` to `$OPENMINION_HOME/.openminion/agents.json`. It does not write that file under `OPENMINION_DATA_ROOT`. Repro: set `OPENMINION_HOME` and `OPENMINION_DATA_ROOT` to two different directories, run `openminion setup --provider zai --model glm-4.7-flash --no-chat`, and look for the file. It is under the home directory. The CLI sets `OPENMINION_HOME` to `--home` (default `.openminion-home`) and reads `<home>/.openminion/agents.json`. Export that same home before `doctor --check-turn`, or doctor checks a different config than the one the CLI just wrote.

Rate limit. The free tier behaves like concurrency 1. A second request while one is in flight returns Zhipu `1302` `您的账户已达到速率限制` (HTTP 429). The free model also returns `1305` `该模型当前访问量过大` for long stretches when it is overloaded. OpenMinion retries for about 30 seconds and then about 60 seconds, then raises `ProviderError` with `code=RATE_LIMITED`. `openminion doctor --check-turn` failed after 130 seconds on that path. Repro: start `doctor --check-turn`, and while it is running start a second turn against the same key. The release CLI retries a `RATE_LIMITED` turn once, then prints that the free tier allows one request at a time and that you should wait before launching again.

Timeout after a tool call. One draft turn did call the `@tool`, then hit `TurnTimeoutError` at 180 seconds. The log after that was `brain.tool_transcript.recovery_failed error_type=ProgrammingError`. The deadline is the caller's `timeout_seconds`; it does not include a recovery of the tool transcript. Hosted runs now default to 300 seconds. Repro: `python -m release_agent ... --provider zai --timeout 180` while glm-4.7-flash is slow. Raise `--timeout` if 300 is still too small. The `ProgrammingError` is in OpenMinion's transcript recovery after the deadline, not in the release tools.

Prose instead of JSON. Another turn skipped the tool and answered in prose. The body was prefixed `openminion: `, and `output_type` raised `AgentOutputValidationError` (`Invalid JSON` at line 1 column 1). Repro: the same CLI command when the model answers without calling `compile_release_packet`. The CLI retries that turn once with the user message ending in "Reply only with JSON. Do not write prose. Do not prefix the answer with a name." If the second reply is still prose, it exits 5 and prints the raw text. The `openminion: ` prefix is the profile name the runtime adds; a JSON object after that prefix still validates, but a sentence does not.

GLM also wraps a correct object in a markdown fence, and both the first turn and that JSON-only retry then failed validation:

```text
openminion: ```json
{ ...valid release JSON... }
```
```

`glm-4.7-flash` did this on an overnight Z.ai run: the packet itself was valid, and the CLI still exited 5. The draft turn now strips an optional `openminion:` prefix and ```json or bare ``` fences, takes the first balanced JSON object, and validates that object with `ReleasePacket` unchanged. Prose around the object is ignored. A fence does not skip schema checks.

### Pending

- **Real-model run in this workspace.** Item 11 records what glm-4.7-flash did on our box with a bigmodel.cn key. This agent environment still has no `ZAI_API_KEY`, so those calls were not repeated here. Expect the 1200-character tool-result clip to matter: the packet JSON does not fit in a tool message, so the model should build the final JSON from the file texts it saw, not from the clipped `compile_release_packet` return value. DeepSeek still returns `ProviderError` `402` `insufficient_balance_error` until that account has balance.
- **Demo video.** Not recorded. A 1–5 minute capture of `--approval ask` on the Cinder Orchard sample, including the deny path, is the missing submission piece.
- **Cortensor Portal / router.** The `cortensor` provider id is wired through the same env switch, with the default model `gpt-oss-20b` and base URL `http://127.0.0.1:8080/api/v2/completions`. Nothing was listening, and there was no `CORTENSOR_API_KEY`, so that path was not executed.

## Layout

```text
release_agent/    CLI, tools, parser, offline completer, workflow
examples/emberfall/   sample changelog and playtest log
tests/            parsers, tools, planner, approval, provider and timeout errors
```

## License

MIT. See [LICENSE](LICENSE).
