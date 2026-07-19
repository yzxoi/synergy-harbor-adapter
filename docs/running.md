# Running and debugging

This guide describes how to run the Synergy Harbor adapter locally with Harbor 0.20.0. All commands assume the repository root as the working directory.

## Runtime model

A Harbor trial has two isolation layers:

1. Harbor starts a task environment, normally a Docker container.
2. The adapter starts Synergy inside that environment with a unique trial home.

The adapter sets both variables for the `synergy send` process:

```text
HOME=/installed-agent/synergy-home-<trial-id>
SYNERGY_HOME=/installed-agent/synergy-home-<trial-id>
```

Synergy resolves its actual state root as:

```text
$SYNERGY_HOME/.synergy
```

This means a trial does not read or write any of these locations:

- the Harbor host's `~/.synergy`
- the container root user's `/root/.synergy`
- the task agent user's default `~/.synergy`
- another trial's Synergy home

Installation and Harbor's version probe also use temporary isolated homes. The adapter removes trial instructions and runtime state best-effort after each run.

### Permissions

The adapter configures every trial's Synergy instance with `controlProfile: full_access`. This grants unrestricted file, shell, and tool access inside the isolated benchmark container, which is required for tasks that expect the agent to inspect and modify the environment. It does not grant access to the Harbor host. Do not reuse this profile outside a disposable, trusted isolation boundary.

The adapter writes two config fragments under the trial root:

```text
$SYNERGY_HOME/.synergy/config/synergy.d/80-permissions.jsonc
$SYNERGY_HOME/.synergy/config/synergy.d/90-model-options.jsonc
```

`80-permissions.jsonc` selects `full_access`. `90-model-options.jsonc` is created only when `model_options` are supplied and applies those options to the provider/model selected by `model_name`.

Provider variables are not written to either config fragment. The adapter removes them from Harbor's generic exec environment, uploads them as `$SYNERGY_HOME/.provider-env.sh` with access limited to the trial user, and sources that file only for `synergy send`. Cleanup removes it with the rest of the trial home best-effort.

## Install dependencies

Requirements:

- Python 3.12 or newer
- `uv`
- Docker with enough storage for the task image and Synergy release

```bash
uv sync
```

Run the local validation suite without Docker or provider access:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

## Configure `.env`

Copy the tracked template, then fill only the providers you intend to use:

```bash
cp .env.example .env
```

`.env` is ignored by git. Do not paste real keys into YAML, shell history, process arguments, job names, logs, issues, or commits.

Harbor loads the file with `--env-file .env`, resolves `${VARIABLE}` references when it constructs the agent, and passes the selected values to the adapter. The adapter keeps them out of Harbor's Docker exec environment and does not persist them in Synergy config. Do not pass a real key through `--agent-env KEY=value`; the Harbor CLI invocation itself can expose it through process inspection and shell history.

## Included job configs

### Anthropic

`smoke/terminal-bench-2-1.yaml` uses:

```text
anthropic/claude-sonnet-4-5
```

Required variable:

```text
ANTHROPIC_API_KEY
```

### DeepSeek

`smoke/terminal-bench-2-1-deepseek.yaml` uses:

```text
deepseek/deepseek-v4-flash
```

Required variable:

```text
DEEPSEEK_API_KEY
```

The config passes this model option to the isolated Synergy model configuration:

```yaml
model_options:
  thinking:
    type: disabled
```

DeepSeek V4 thinking defaults to enabled. Disabling it is useful for benchmark tasks where a long reasoning stream can consume the response budget before the model completes tool work. This is a model option, not a Synergy `variant`.

Both configs select one Terminal-Bench 2.1 task, one attempt, and one concurrent trial. They are smoke configurations, not leaderboard configurations.

## Mode 1: inspect the job config

Use this before any real run when changing agent kwargs, task filters, or concurrency. `--print-config` validates the JobConfig and applies CLI overrides, but Harbor leaves `${VARIABLE}` agent environment references unresolved and does not verify that the referenced provider key exists:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --print-config
```

This prints the validated job JSON without starting a task container or calling a model. Confirm that `agents[].env` contains a `${VARIABLE}` template rather than a literal credential.

Review at least:

- `agents[].import_path`
- `agents[].model_name`
- `agents[].kwargs`
- `datasets[].ref`
- task and attempt counts
- concurrency

Do not publish preview output from configs that contain literal sensitive values.

## Mode 2: install-only compatibility check

Use install-only mode to validate:

- task image resolution
- Docker startup
- CPU architecture and libc detection
- Synergy release selection
- release download and SHA-256 verification
- isolated Synergy version startup

DeepSeek config:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --install-only \
  --yes
```

Anthropic config:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1.yaml \
  --env-file .env \
  --install-only \
  --yes
```

`--install-only` skips `agent.run()` and implies `--disable-verification`. It does not make a provider request, but the selected config must still resolve its referenced environment variable.

If no real key is available and only install compatibility is being checked, supply a process-local placeholder for that config:

```bash
DEEPSEEK_API_KEY=unused \
  uv run harbor run \
    -c smoke/terminal-bench-2-1-deepseek.yaml \
    --install-only \
    --yes
```

## Mode 3: full single-task smoke

A full smoke installs Synergy, runs the agent, downloads logs, executes the task verifier, and records a reward.

DeepSeek:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --job-name synergy-tbench-deepseek-smoke \
  --yes
```

Anthropic:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1.yaml \
  --env-file .env \
  --job-name synergy-tbench-anthropic-smoke \
  --yes
```

A successful adapter run is not the same as a successful benchmark result. Check both:

- whether the agent phase completed without an adapter/provider error
- whether the verifier produced a non-zero reward

## Mode 4: agent-only debugging

Skip the verifier when debugging API authentication, model configuration, Synergy startup, or tool execution:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --disable-verification \
  --debug \
  --job-name synergy-deepseek-agent-debug \
  --yes
```

This still runs the real model and can incur provider cost.

Use this mode when the pass/fail signal is in `synergy.jsonl`, not in the task's scoring logic.

## Mode 5: retain the Harbor environment

Harbor deletes task environments by default. Preserve one when container-level diagnosis is necessary:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --no-delete \
  --debug \
  --job-name synergy-deepseek-retained \
  --yes
```

Important behavior:

- `--no-delete` preserves Harbor's environment.
- The adapter still removes its trial-specific Synergy home best-effort after the agent phase.
- Inspect downloaded logs and benchmark artifacts first.
- Do not depend on the isolated Synergy home remaining after a completed run.
- Delete retained environments manually after diagnosis so they do not consume Docker storage.

## Mode 6: direct CLI install check

Harbor can load the custom adapter directly when no nested model configuration or real provider credential is needed. This example is intentionally install-only:

```bash
uv run harbor run \
  -d terminal-bench/terminal-bench-2-1 \
  --agent synergy_harbor.agent:Synergy \
  --model anthropic/claude-sonnet-4-5 \
  --env docker \
  --n-tasks 1 \
  --n-attempts 1 \
  --n-concurrent 1 \
  --install-only \
  --yes
```

Direct CLI mode is useful for installation checks and scalar adapter experiments. Use a YAML config plus `--env-file .env` for any real model call, nested `model_options`, reproducible dataset digests, and provider allowlists.

Harbor passes non-secret adapter constructor arguments with `--agent-kwarg` / `--ak`:

```bash
--agent-kwarg variant=high
```

Do not pass provider keys through `--agent-env`, and do not encode nested model options as an ad hoc shell string.

## Mode 7: run a selected task

Filter the dataset when a known task provides a faster debugging loop:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --include-task-name 'write-compressor' \
  --n-attempts 1 \
  --n-concurrent 1 \
  --job-name synergy-write-compressor-debug \
  --yes
```

Use `--include-task-name` or `--exclude-task-name` for name filters and `--n-tasks` to cap the resulting set.

## Mode 8: small batch and scaled evaluation

Scale in stages. First run a small batch:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --n-tasks 10 \
  --n-attempts 1 \
  --n-concurrent 2 \
  --job-name synergy-tbench-deepseek-10 \
  --yes
```

Increase concurrency only after checking:

- provider rate and concurrency limits
- Docker CPU, memory, and storage use
- per-trial setup time for the Synergy release
- failure rate and retry behavior
- token and cost totals

For repeated trials, choose `--n-attempts`, `--max-retries`, and retry include/exclude rules deliberately. Retries are operational recovery; attempts are benchmark samples. They should not be treated as interchangeable.

The current adapter is not ready for an official 89-task × 5-attempt leaderboard run because ATIF trajectory support remains deferred.

## Mode 9: local task or dataset

Reuse a checked-in provider config and override only the task path:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --path /absolute/path/to/task-or-dataset \
  --n-concurrent 1 \
  --yes
```

This keeps real credentials in `.env` and preserves the checked-in adapter/model configuration. Keep task paths and private dataset contents out of shared logs and commit messages.

## Adapter configuration reference

Options documented in this section belong to the adapter constructor. Other command-line flags in this guide, such as `--install-only`, `--print-config`, `--no-delete`, `--path`, and `--extra-docker-compose`, are provided by Harbor 0.20.0.

### `version`

The adapter pins Synergy 2.4.3. Passing another value raises an error instead of silently downloading an unverified release.

### `variant`

Optional Synergy CLI variant:

```yaml
kwargs:
  variant: high
```

This becomes:

```text
synergy send ... --variant high
```

Only use variants exposed by the selected model.

### `model_options`

Optional provider/model options:

```yaml
kwargs:
  model_options:
    thinking:
      type: disabled
```

The adapter writes an isolated config fragment equivalent to:

```json
{
  "provider": {
    "deepseek": {
      "models": {
        "deepseek-v4-flash": {
          "options": {
            "thinking": {
              "type": "disabled"
            }
          }
        }
      }
    }
  }
}
```

The provider and model keys are derived from `model_name`. No provider-specific behavior is hard-coded into the adapter.

### `extra_allowed_hosts`

Use the Harbor config field for restricted network policies:

```yaml
extra_allowed_hosts:
  - api.deepseek.com
```

A warning that the allowlist is ignored under a public network policy is informational: public policy already permits the host. Under a restricted policy, configure both provider access and any setup-time download requirements appropriately.

## Output and logs

Harbor writes job data under:

```text
jobs/<job-name>/
```

The exact nested layout is Harbor-owned and may vary by release. Search within the selected job directory rather than assuming a fixed trial identifier.

The adapter captures Synergy's JSONL stream as:

```text
synergy.jsonl
```

The parser records:

- session ID
- event count
- step count
- input tokens, mapped to Harbor `n_input_tokens`
- output plus reasoning tokens, mapped to Harbor `n_output_tokens`
- cache tokens (read + write), mapped to Harbor `n_cache_tokens`
- model-reported cost
- error count
- malformed JSONL line count
- wall-clock duration

Logs can contain the task instruction, source files read by tools, model output, and error details. Treat them as sensitive.

Useful Harbor options:

```text
--jobs-dir <path>
--job-name <name>
--agent-include-logs <glob>
--agent-exclude-logs <glob>
--artifact <environment-path>
--debug
```

## Apple Silicon and architecture notes

The adapter selects a Synergy release that matches the task container architecture, not the Harbor host architecture.

On Apple Silicon, an x86-64 task image normally runs through Docker/QEMU. Some benchmark verifier toolchains may crash under emulation even when the Synergy agent itself works. A verifier crash is not proof of an adapter failure.

For local diagnosis only, an operator may build or obtain a native arm64 version of a specific task image and supply a Compose overlay:

```yaml
services:
  main:
    image: local/task-image:arm64
    platform: linux/arm64
```

Run it with:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --extra-docker-compose /absolute/path/to/arm64.compose.yaml \
  --yes
```

This is a local diagnostic mode, not a portable benchmark configuration. Record the substituted image and verifier architecture when comparing scores.

## Troubleshooting

### Provider authentication failure

Symptoms include HTTP 401/403, `invalid x-api-key`, or a Harbor `AgentAuthenticationError`.

Check:

1. The expected key exists in `.env`.
2. `--env-file .env` is present.
3. The YAML references the same variable name.
4. `model_name` uses the intended provider prefix.
5. The provider API host is allowed by the effective network policy.

Never print the key while debugging.

### Synergy writes under the wrong home

Every real `synergy send` execution must have identical explicit `HOME` and `SYNERGY_HOME` values under `/installed-agent/synergy-home-<trial-id>`.

The expected state directory is:

```text
/installed-agent/synergy-home-<trial-id>/.synergy
```

If logs show `/root/.synergy`, `/home/<user>/.synergy`, or a host path, stop the run. That is an isolation regression.

### Installation is slow

The pinned Synergy archive is downloaded during agent setup for each fresh task environment. Check whether the process is still downloading before diagnosing an agent hang. Use install-only mode to isolate setup time from model time.

### Agent finishes but reward is zero

Separate the two signals:

- Agent signal: JSONL has normal step completion, tool calls, and no terminal error.
- Benchmark signal: the expected task artifact exists and the verifier completed successfully.

Inspect verifier logs for task assertion failures, missing artifacts, architecture crashes, or timeout errors.

### Model reaches a length limit

Inspect `step_finish` events and token fields. For DeepSeek V4, confirm the isolated model config includes:

```yaml
thinking:
  type: disabled
```

A CLI `variant` alone does not disable DeepSeek thinking mode.

### Cleanup warning

Cleanup is best-effort. A warning means Harbor should still tear down the task container, but a retained environment may contain trial state. Inspect and remove it deliberately.

## Recommended progression

1. Run local tests and static checks.
2. Preview the resolved config.
3. Run install-only mode.
4. Run one agent-only task if provider/tool behavior is uncertain.
5. Run one full task with the verifier.
6. Run a 10-task batch with low concurrency.
7. Increase attempts or concurrency only after reviewing reliability and cost.
