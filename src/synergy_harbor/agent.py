from __future__ import annotations

import json
import re
import shlex
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, override

from harbor.agents.installed import base as harbor_installed
from harbor.agents.installed.base import BaseInstalledAgent, ErrorPattern, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from synergy_harbor.installer import (
    PLATFORM_PROBE_COMMAND,
    PREREQUISITE_COMMAND,
    SYNERGY_BINARY,
    SYNERGY_VERSION,
    build_install_command,
    parse_platform_probe,
)
from synergy_harbor.parser import UsageSummary, parse_synergy_jsonl

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class Synergy(BaseInstalledAgent):
    """Run Synergy inside a Harbor task environment through its headless CLI."""

    ERROR_PATTERNS = [
        *BaseInstalledAgent.ERROR_PATTERNS,
        ErrorPattern(
            r"ProviderAuthError|API key .*(missing|not configured)|invalid x-api-key|"
            r'"statusCode"\s*:\s*(?:401|403)',
            vars(harbor_installed)["AgentAuthenticationError"],
        ),
        ErrorPattern(
            r'"type"\s*:\s*"error"',
            vars(harbor_installed)["UnknownApiError"],
        ),
    ]

    def __init__(
        self,
        logs_dir: Path,
        prompt_template_path: Path | str | None = None,
        version: str | None = None,
        extra_env: dict[str, str] | None = None,
        variant: str | None = None,
        model_options: dict[str, Any] | None = None,
        workflow: str | None = None,
        allow_lightloop: bool = False,
        agent: str = "synergy",
        extra_allowed_hosts: list[str] | None = None,
        export_model_patch: bool = False,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if extra_allowed_hosts is not None and not all(
            isinstance(host, str) and host for host in extra_allowed_hosts
        ):
            raise ValueError("extra_allowed_hosts must be a list of non-empty strings")
        if version is not None and version != SYNERGY_VERSION:
            raise ValueError(
                f"This adapter pins Synergy {SYNERGY_VERSION}; unsupported version: {version}"
            )
        if workflow not in (None, "lightloop"):
            raise ValueError(
                f"Unsupported workflow mode: {workflow!r}; expected None or 'lightloop'"
            )
        if workflow == "lightloop" and not allow_lightloop:
            raise ValueError(
                "The pinned Synergy release does not expose --workflow lightloop; "
                "use the SynergyDev adapter with a source build that includes the CLI option"
            )
        if not agent or any(character.isspace() for character in agent):
            raise ValueError(f"Invalid agent name: {agent!r}")
        run_env = dict(extra_env or {})
        for key, value in run_env.items():
            if not isinstance(key, str) or _ENVIRONMENT_NAME.fullmatch(key) is None:
                raise ValueError(f"Invalid environment variable name: {key!r}")
            if not isinstance(value, str):
                raise ValueError(f"Invalid environment variable value for {key!r}")
        super().__init__(
            logs_dir,
            prompt_template_path,
            SYNERGY_VERSION,
            None,
            *args,
            **kwargs,
        )
        self._run_env = run_env
        self.variant = variant
        self.model_options = model_options
        self.workflow = workflow
        self.agent = agent
        self._extra_allowed_hosts = list(extra_allowed_hosts or [])
        self._export_model_patch = export_model_patch
        self._output = ""
        self._duration_seconds: float | None = None

    def install_spec(self) -> None:
        """Pier 0.3.0 compatibility: no declarative build-time install spec.

        The Synergy binary is installed imperatively in :meth:`install` from
        probe results, so no Dockerfile inlining is needed.
        """
        return None

    def network_allowlist(self):
        """Pier 0.3.0 compatibility: expose model API hosts for egress filtering.

        Pier's no-network tasks route filtered egress through a proxy that only
        admits these domains, so the model provider API must be listed here.
        Pier consumes only the ``domains`` attribute (duck-typed), so no pier
        import is needed and Harbor runs are unaffected.
        """

        class NetworkAllowlist:
            def __init__(self, domains: list[str]) -> None:
                self.domains = domains

        return NetworkAllowlist(domains=self._extra_allowed_hosts)

    def to_agent_info(self) -> Any:
        """Return agent identity compatible with both Harbor and Pier.

        Harbor reads ``agent_info.name`` / ``agent_info.model_info.name`` via
        attribute access, while Pier validates the value against its own
        pydantic ``AgentInfo`` (dict input). A dict subclass with attribute
        access satisfies both without importing either framework's runtime
        model.
        """

        class AgentInfoDict(dict[str, Any]):
            def __getattr__(self, name: str) -> Any:
                try:
                    return self[name]
                except KeyError as error:
                    raise AttributeError(name) from error

        model_info = None
        if self.model_name and "/" in self.model_name:
            provider, name = self.model_name.split("/", maxsplit=1)
            model_info = AgentInfoDict(name=name, provider=provider)
        return AgentInfoDict(
            name=self.name(),
            version=self.version() or "unknown",
            model_info=model_info,
        )

    @staticmethod
    @override
    def name() -> str:
        return "synergy"

    @override
    def get_version_command(self) -> str | None:
        binary = shlex.quote(SYNERGY_BINARY)
        return (
            'synergy_home=$(mktemp -d "${TMPDIR:-/tmp}/synergy-version-XXXXXX") && '
            "trap 'rm -rf \"$synergy_home\"' EXIT && "
            f'HOME="$synergy_home" SYNERGY_HOME="$synergy_home" {binary} --version'
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(environment, command=PREREQUISITE_COMMAND)
        probe = await self.exec_as_root(environment, command=PLATFORM_PROBE_COMMAND)
        asset = parse_platform_probe(probe.stdout or "")
        await self.exec_as_root(environment, command=build_install_command(asset))

    def _run_id(self) -> str:
        if self.context_id is not None:
            return self.context_id.hex
        if self.session_id:
            normalized = "".join(character for character in self.session_id if character.isalnum())
            if normalized:
                return normalized[:48]
        return uuid.uuid4().hex

    @staticmethod
    def _runtime_paths(run_id: str) -> tuple[PurePosixPath, PurePosixPath, PurePosixPath]:
        runtime_home = PurePosixPath("/installed-agent") / f"synergy-home-{run_id}"
        instruction_path = PurePosixPath("/installed-agent") / f"synergy-instruction-{run_id}.txt"
        config_path = runtime_home / ".synergy/config/synergy.d/80-permissions.jsonc"
        return runtime_home, instruction_path, config_path

    @staticmethod
    def _chown_command(environment: BaseEnvironment, *paths: PurePosixPath) -> str:
        quoted_paths = " ".join(shlex.quote(str(path)) for path in paths)
        if environment.default_user is None:
            return f"chmod -R u+rwX,go-rwx {quoted_paths}"
        owner = shlex.quote(str(environment.default_user))
        return f"chown -R -- {owner} {quoted_paths} && chmod -R u+rwX,go-rwx {quoted_paths}"

    async def _capture_git_base(self, environment: BaseEnvironment) -> str:
        """Best-effort capture of the pre-run git HEAD at /app.

        Returns an empty string when the environment has no git repository
        (e.g. Terminal-Bench tasks), in which case patch export is skipped.
        """

        try:
            result = await self.exec_as_root(
                environment,
                command="cd /app && git rev-parse HEAD",
                timeout_sec=30,
            )
        except Exception as error:
            self.logger.debug("Could not capture /app git HEAD: %s", error)
            return ""
        if result.return_code != 0:
            self.logger.debug("No git repository at /app (exit %s)", result.return_code)
            return ""
        return (result.stdout or "").strip()

    async def _export_patch(self, environment: BaseEnvironment, base_sha: str) -> None:
        """Export the agent's changes as /logs/artifacts/model.patch (best-effort).

        DeepSWE relies on a ``[[verifier.collect]]`` hook to export the agent's
        changes into the artifact directory, but Pier 0.3.0 never runs that
        hook. Without this export the verifier grades the pristine base state
        and every challenge test fails. The diff covers both committed and
        uncommitted agent changes against the pre-run HEAD.
        """

        # DeepSWE tasks instruct the agent to work on a new branch and commit;
        # the official [[verifier.collect]] hook diffs base..HEAD.  Stage every
        # change first so committed, staged, and unstaged agent work all land
        # in the index, then diff the index against base.  When the agent
        # committed, the index equals the new HEAD, so base..HEAD changes are
        # included; when the agent only staged or left the worktree dirty,
        # `git add -A` folds those in as well.
        command = (
            "cd /app && git config --global --add safe.directory /app && "
            "mkdir -p /logs/artifacts && git add -A && "
            f"git diff --cached --binary {shlex.quote(base_sha)} "
            "> /logs/artifacts/model.patch; "
            # Debug artifacts: trajectory and repo state snapshots so a
            # wrong-diff patch can be diagnosed without a separate log download.
            "cp /logs/agent/synergy.jsonl /logs/artifacts/synergy.jsonl 2>/dev/null || true; "
            "{ echo '=== git status --short ==='; git status --short; "
            "echo '=== git log --oneline -8 ==='; git log --oneline -8 2>/dev/null; "
            "echo '=== git branch -a ==='; git branch -a; "
            "echo '=== ls -la /app ==='; ls -la /app; "
            "echo '=== pwd ==='; pwd; } > /logs/artifacts/repo-state.txt 2>&1"
        )
        try:
            result = await self.exec_as_root(environment, command=command, timeout_sec=120)
            if result.return_code != 0:
                self.logger.warning(
                    "model.patch export failed with exit %s: %s",
                    result.return_code,
                    (result.stdout or "")[-500:],
                )
        except Exception as error:
            self.logger.warning("model.patch export failed: %s", error)

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name or "/" not in self.model_name:
            raise ValueError("Model name must use the provider/model format")

        run_id = self._run_id()
        runtime_home, instruction_path, config_path = self._runtime_paths(run_id)
        # Synergy loads global config from fixed ConfigDomain filenames under
        # synergy.d/ (10-models.jsonc, 20-providers.jsonc, 80-permissions.jsonc).
        # Other names are silently ignored, so model options must go into the
        # providers domain file to take effect.
        models_config_path = config_path.parent / "10-models.jsonc"
        providers_config_path = config_path.parent / "20-providers.jsonc"
        provider_env_path = runtime_home / ".provider-env.sh"
        log_path = PurePosixPath("/logs/agent/synergy.jsonl")
        started_at = time.monotonic()
        should_apply_context = False
        self._output = ""

        with tempfile.TemporaryDirectory(prefix="synergy-harbor-") as temporary_directory:
            temporary_root = Path(temporary_directory)
            host_instruction = temporary_root / "instruction.txt"
            host_config = temporary_root / "80-permissions.jsonc"
            host_models_config = temporary_root / "10-models.jsonc"
            host_providers_config = temporary_root / "20-providers.jsonc"
            host_provider_env = temporary_root / ".provider-env.sh"
            host_instruction.write_text(instruction, encoding="utf-8")
            host_config.write_text(
                json.dumps({"controlProfile": "full_access"}, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            if self._run_env:
                host_provider_env.write_text(
                    "".join(
                        f"export {key}={shlex.quote(value)}\n"
                        for key, value in self._run_env.items()
                    ),
                    encoding="utf-8",
                )
                host_provider_env.chmod(0o600)
            provider_id, model_id = self.model_name.split("/", maxsplit=1)
            if self.model_options is not None:
                host_providers_config.write_text(
                    json.dumps(
                        {
                            "provider": {
                                provider_id: {"models": {model_id: {"options": self.model_options}}}
                            }
                        },
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
            if self.workflow == "lightloop":
                # The Light Loop completion review runs as a Cortex subagent
                # (lightloop-reviewer) whose model resolves from the role
                # fallback chain thinking_model -> model. Without a configured
                # role model the review fails with "No model configured" and
                # the attempt ends failed, so pin the role models to the
                # benchmark model for lightloop runs.
                host_models_config.write_text(
                    json.dumps(
                        {
                            "model": self.model_name,
                            "thinking_model": self.model_name,
                        },
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )

            try:
                await self.exec_as_root(
                    environment,
                    command=(
                        f"rm -rf {shlex.quote(str(runtime_home))} "
                        f"{shlex.quote(str(instruction_path))} && "
                        f"mkdir -p {shlex.quote(str(config_path.parent))} && "
                        # Some task images (e.g. DeepSWE) do not pre-create
                        # /logs/agent; the send pipeline tees the JSONL log
                        # there, so create it before running the agent.
                        "mkdir -p /logs/agent && chmod 777 /logs/agent"
                    ),
                )
                await environment.upload_file(host_config, str(config_path))
                await environment.upload_file(host_instruction, str(instruction_path))
                if self._run_env:
                    await environment.upload_file(host_provider_env, str(provider_env_path))
                if self.model_options is not None:
                    await environment.upload_file(host_providers_config, str(providers_config_path))
                if self.workflow == "lightloop":
                    await environment.upload_file(host_models_config, str(models_config_path))
                await self.exec_as_root(
                    environment,
                    command=self._chown_command(environment, runtime_home, instruction_path),
                )

                base_sha = ""
                if self._export_model_patch:
                    base_sha = await self._capture_git_base(environment)

                model = shlex.quote(self.model_name)
                variant_flag = (
                    f" --variant {shlex.quote(self.variant)}" if self.variant is not None else ""
                )
                workflow_flag = " --workflow lightloop" if self.workflow == "lightloop" else ""
                env_prefix = f". {shlex.quote(str(provider_env_path))} && " if self._run_env else ""
                agent_flag = shlex.quote(self.agent)
                command = (
                    f"{env_prefix}{shlex.quote(SYNERGY_BINARY)} send "
                    f"--format json --agent {agent_flag} "
                    f"--model {model}{variant_flag}{workflow_flag} "
                    f"< {shlex.quote(str(instruction_path))} "
                    f"2>&1 | stdbuf -oL tee {shlex.quote(str(log_path))}"
                )
                should_apply_context = True
                run_env = {"HOME": str(runtime_home), "SYNERGY_HOME": str(runtime_home)}
                if self._export_model_patch:
                    # DeepSWE repositories live at /app (see task.toml
                    # verifier.collect).  Pin the CLI working directory so the
                    # agent's tools operate on the task repository instead of
                    # whatever directory the container happens to start in.
                    run_env["SYNERGY_CWD"] = "/app"
                result = await self.exec_as_agent(
                    environment,
                    command=command,
                    env=run_env,
                )
                self._output = result.stdout or ""
                if base_sha:
                    await self._export_patch(environment, base_sha)
            finally:
                self._duration_seconds = time.monotonic() - started_at
                try:
                    if should_apply_context:
                        self._apply_context(context)
                finally:
                    await self._cleanup(environment, runtime_home, instruction_path)

    @override
    def _classify_exec_error(self, command: str, result: Any):
        self._output = result.stdout or ""
        return super()._classify_exec_error(command, result)

    async def _cleanup(
        self,
        environment: BaseEnvironment,
        runtime_home: PurePosixPath,
        instruction_path: PurePosixPath,
    ) -> None:
        try:
            result = await environment.exec(
                command=(
                    f"rm -rf {shlex.quote(str(runtime_home))} {shlex.quote(str(instruction_path))}"
                ),
                user="root",
                timeout_sec=30,
            )
            if result.return_code != 0:
                self.logger.warning("Synergy trial cleanup failed with exit %s", result.return_code)
        except Exception as error:
            self.logger.warning("Synergy trial cleanup failed: %s", error)

    def _summary(self) -> UsageSummary:
        output = self._output
        downloaded_log = self.logs_dir / "synergy.jsonl"
        if downloaded_log.is_file():
            try:
                output = downloaded_log.read_text(encoding="utf-8")
            except OSError as error:
                self.logger.debug("Could not read downloaded Synergy log: %s", error)
        return parse_synergy_jsonl(output)

    def _apply_context(self, context: AgentContext) -> None:
        summary = self._summary()
        if summary.step_count:
            context.n_input_tokens = summary.input_tokens
            context.n_output_tokens = summary.output_tokens
            context.n_cache_tokens = summary.cache_tokens
            context.cost_usd = summary.cost_usd

        metadata = dict(context.metadata or {})
        metadata["synergy"] = {
            "version": SYNERGY_VERSION,
            "agent": self.agent,
            "workflow": self.workflow,
            "session_id": summary.session_id,
            "step_count": summary.step_count,
            "event_count": summary.event_count,
            "error_count": summary.error_count,
            "malformed_line_count": summary.malformed_line_count,
            "duration_seconds": self._duration_seconds,
        }
        context.metadata = metadata

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        self._apply_context(context)
