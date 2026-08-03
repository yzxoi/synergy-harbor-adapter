# Synergy Harbor Adapter

A Harbor `BaseInstalledAgent` adapter that runs Synergy inside each benchmark task container. The current milestone targets local Terminal-Bench 2.1 validation; it does not yet emit ATIF trajectories or qualify as a leaderboard submission.

## What it does

Harbor creates a task container, installs the pinned Synergy release, uploads the task instruction, and invokes:

```text
synergy send --format json --agent synergy --model provider/model < instruction.txt
```

Synergy owns the temporary server, session, tool loop, completion wait, and shutdown. Its file and shell tools operate inside the Harbor task container, whose final state is evaluated by Harbor's verifier.

Implemented capabilities:

- Harbor 0.20.0 custom installed-agent adapter
- Pinned Synergy 2.4.3 Linux installation
- SHA-256 verification for arm64/x64, glibc/musl, and baseline x64 assets
- Instructions uploaded as files and delivered through stdin
- Per-trial `HOME` and `SYNERGY_HOME` isolation
- Optional Synergy model options and CLI variants
- JSONL log capture with token and cost extraction
- Best-effort runtime cleanup after success or failure

Deferred capabilities:

- ATIF trajectory generation
- Native session resume
- Windows task environments
- Harbor registry publication
- Official Terminal-Bench leaderboard submission

## Isolation guarantee

The adapter never starts Synergy against the container user's default `~/.synergy`, and it never uses the Harbor host's Synergy state.

Each trial receives a unique parent home:

```text
/installed-agent/synergy-home-<trial-id>/
├── .provider-env.sh
└── .synergy/
    ├── config/
    ├── data/
    ├── log/
    └── state/
```

Both `HOME` and `SYNERGY_HOME` point to that trial-specific parent directory. Synergy therefore resolves its runtime root as `$SYNERGY_HOME/.synergy`. Provider variables are uploaded to the private `.provider-env.sh` file and sourced only by `synergy send`, keeping their values out of Harbor's Docker exec arguments. Installation and version probes use separate temporary homes, and trial state is removed best-effort after the run.

Each trial also receives `controlProfile: full_access` inside its isolated config. This is intentional for benchmark containers: Synergy may freely read, write, and execute inside the disposable task environment, but it receives no access to the Harbor host.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- Docker

Install dependencies and run local checks:

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

The custom Harbor agent import path is:

```text
synergy_harbor.agent:Synergy
```

## Configure credentials

Copy the template and fill only the provider keys you use:

```bash
cp .env.example .env
```

`.env` is ignored by git and must never be committed. Harbor resolves the YAML references and gives the values to the adapter constructor. The adapter removes them from Harbor's generic exec environment, uploads a trial-private file, and sources that file only for `synergy send`; it does not persist credentials in Synergy config. Do not pass real keys through `--agent-env`, because the Harbor CLI invocation itself can expose them in process arguments and shell history.

## Run modes

| Mode                       | Calls a model | Runs verifier | Purpose                                                             |
| -------------------------- | ------------: | ------------: | ------------------------------------------------------------------- |
| Local quality checks       |            No |            No | Validate Python code and tests                                      |
| Job config preview         |            No |            No | Validate Harbor config, overrides, and environment templates        |
| Install-only               |            No |            No | Verify image, architecture, download, checksum, and Synergy startup |
| Single-task smoke          |           Yes |           Yes | End-to-end adapter and benchmark validation                         |
| Agent-only smoke           |           Yes |            No | Debug model/tool execution without scoring                          |
| Retained debug environment |      Optional |      Optional | Preserve the task container after a failure                         |
| Scaled evaluation          |           Yes |           Yes | Run multiple tasks, attempts, and concurrent trials                 |

### 0. Run local quality checks

These checks do not start Harbor, Docker, or a model:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

### 1. Preview the job config

This validates the Harbor job config and CLI overrides without starting a task container. Harbor intentionally leaves `${VARIABLE}` agent environment references unresolved in this output, so this mode does not verify that a provider key exists or can authenticate:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --print-config
```

Confirm that the output retains the template reference rather than a literal credential. Do not publish preview output from configs that contain literal sensitive values.

### 2. Verify installation only

This downloads the task image and pinned Synergy release, verifies the archive checksum, starts Synergy only for isolated version detection, and skips the model and verifier:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1.yaml \
  --env-file .env \
  --install-only \
  --yes
```

### 3. Run an Anthropic single-task smoke

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1.yaml \
  --env-file .env \
  --yes
```

### 4. Run a DeepSeek V4 Flash single-task smoke

The DeepSeek smoke config injects `thinking: {type: disabled}` into the isolated trial config. This avoids the V4 default thinking mode consuming the benchmark response budget.

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --yes
```

### 5. Run without the verifier

Use this when debugging provider access, tool execution, or agent logs without scoring the task result:

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --disable-verification \
  --yes
```

### 6. Keep the task environment for debugging

```bash
uv run harbor run \
  -c smoke/terminal-bench-2-1-deepseek.yaml \
  --env-file .env \
  --no-delete \
  --debug \
  --yes
```

`--no-delete` preserves Harbor's task environment. The adapter still removes its trial-specific Synergy home best-effort, so use downloaded JSONL logs and Harbor artifacts for post-run diagnosis. Do not expect the isolated Synergy runtime directory to remain after a completed agent phase.

### 7. Scale beyond one task

Start with a small batch before increasing attempts or concurrency:

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

Do not treat this smoke configuration as leaderboard-compliant. ATIF support and the official attempt matrix remain deferred.

## Agent options

Structured options are easiest to express in YAML:

```yaml
agents:
  - import_path: synergy_harbor.agent:Synergy
    model_name: deepseek/deepseek-v4-flash
    kwargs:
      model_options:
        thinking:
          type: disabled
      variant: high
      workflow: lightloop
```

- `model_options` are written to the selected provider/model inside the isolated trial's `synergy.d` config.
- `variant` is passed to `synergy send --variant`.
- `workflow: lightloop` runs the instruction as a Light Loop workflow task (`synergy send --workflow lightloop`); omit it for a plain single-shot session. The pinned Synergy release does not expose the option yet, so lightloop requires the `SynergyDev` adapter with a source build; the release adapter rejects the mode at construction.
- The three mechanisms are independent. Do not add a variant unless the selected model exposes that variant.

See [Running and debugging](docs/running.md) for the complete configuration reference, direct CLI usage, output locations, troubleshooting, and Apple Silicon notes.

## Reproducibility and security

- Release assets are pinned by version and hard-coded SHA-256 digest.
- Instructions never enter a shell argument or environment variable.
- Provider keys are written only to a private per-trial environment file, never to Synergy config or host process arguments, and are removed with the trial home best-effort.
- Every Synergy process uses an explicit isolated home.
- Synergy executes inside the task container, never against the Harbor host.
- JSONL logs may contain benchmark instructions and tool output; treat job artifacts as sensitive unless the benchmark is public.

## Next milestone

After stable end-to-end smoke results, add a Synergy JSONL-to-ATIF v1.7 converter, trajectory tests, and a leaderboard-compliant Terminal-Bench evaluation configuration.
