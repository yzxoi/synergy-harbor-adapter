# Synergy Harbor Adapter Rules

- This repository owns the Harbor integration boundary. Do not add Terminal-Bench-specific behavior to the Synergy product repository.
- Run Synergy inside the Harbor task environment. Never let Synergy's shell tools target the Harbor host.
- Keep Synergy and Harbor versions pinned. Update release asset checksums and compatibility tests together.
- Deliver task instructions through uploaded files and stdin, not shell arguments or environment variables.
- Never log provider credentials. Keep per-trial runtime state isolated under `/installed-agent/` and remove it best-effort after each run.
- ATIF support is intentionally deferred until the installed-agent POC is validated end to end.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run ty check` before delivery.
