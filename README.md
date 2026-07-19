# Synergy Harbor Adapter

A Harbor `BaseInstalledAgent` adapter that runs Synergy inside each benchmark task environment. The first milestone targets local Terminal-Bench 2.1 smoke runs; it does not yet produce ATIF trajectories or qualify for leaderboard submission.

## Architecture

Harbor creates the task container, installs the pinned Synergy release, and calls the adapter with the task instruction. The adapter uploads the instruction as a file and invokes:

```text
synergy send --format json --agent synergy --model provider/model < instruction.txt
```

`synergy send` owns the temporary Synergy server, session, tool loop, completion wait, and shutdown. The task container remains the outer isolation boundary, so Synergy's file and shell tools modify the environment that Harbor later verifies.

Each run receives a unique `SYNERGY_HOME` under `/installed-agent/` with `full_access` configured. The adapter removes the instruction and runtime state after execution; Harbor's container teardown is the final cleanup boundary.

## Current scope

Implemented:

- Harbor 0.20 `BaseInstalledAgent` import-path adapter
- Pinned Synergy 2.4.3 Linux release installation
- SHA-256 verification for x64/arm64, glibc/musl, and baseline x64 assets
- Instruction delivery through `BaseEnvironment.upload_file()` and stdin
- Per-trial Synergy state isolation
- Harbor wall-clock cancellation through the single `environment.exec()` call
- Synergy JSONL log capture and token/cost extraction
- Best-effort cleanup on success and failure

Deferred:

- ATIF trajectory generation (`SUPPORTS_ATIF` is false)
- Resume support
- Windows tasks
- Harbor registry publication
- Official Terminal-Bench leaderboard submission

## Development

Requirements: Python 3.12+, `uv`, and Docker for real smoke runs.

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run ty check
```

The package exposes this custom agent import path:

```text
synergy_harbor.agent:Synergy
```

## Verify the container install

This setup-only smoke downloads the pinned task image and Synergy release, verifies the
archive checksum, and records Synergy 2.4.3 without making a model request:

```bash
ANTHROPIC_API_KEY=unused \
  uv run harbor run -c smoke/terminal-bench-2-1.yaml --install-only --yes
```

## Run one Terminal-Bench 2.1 task

Set the provider credential used by the model in `smoke/terminal-bench-2-1.yaml`, then run:

```bash
export ANTHROPIC_API_KEY=...
uv run harbor run -c smoke/terminal-bench-2-1.yaml
```

The smoke config is intentionally limited to one task, one attempt, and one concurrent trial. It uses the pinned Terminal-Bench 2.1 dataset digest but is **not** a leaderboard configuration.

To select another provider/model, update both `model_name` and the provider environment variable. Harbor passes agent environment values into the task container; Synergy reads its normal provider variables such as `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.

## Direct CLI form

Harbor accepts a custom agent through `--agent`:

```bash
uv run harbor run \
  -d terminal-bench/terminal-bench-2-1 \
  --agent synergy_harbor.agent:Synergy \
  -m anthropic/claude-sonnet-4-5 \
  -e docker \
  -k 1 \
  -n 1
```

For restricted Harbor network policies, allow the selected provider API host during the agent phase. Synergy release installation follows the same setup-time download pattern as Harbor's other installed agents.

## Reproducibility and security

- The adapter downloads a named Synergy 2.4.3 release asset and verifies its hard-coded digest before extraction.
- Instructions never enter a shell argument or environment variable.
- Provider keys are never written by this repository; they flow through Harbor's `AgentConfig.env` / `extra_env` mechanism.
- Synergy executes inside the task container, never against the Harbor host.
- JSONL logs may contain task content and tool output. Treat Harbor trial artifacts as sensitive unless the benchmark is explicitly public.

## Next milestone

After an end-to-end Docker smoke run passes, add a Synergy JSONL-to-ATIF v1.7 converter, full trajectory tests, and leaderboard-compliant 89-task × 5-attempt configuration.
