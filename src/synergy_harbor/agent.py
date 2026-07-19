from __future__ import annotations

import json
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
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if version is not None and version != SYNERGY_VERSION:
            raise ValueError(
                f"This adapter pins Synergy {SYNERGY_VERSION}; unsupported version: {version}"
            )
        super().__init__(
            logs_dir,
            prompt_template_path,
            SYNERGY_VERSION,
            extra_env,
            *args,
            **kwargs,
        )
        self._output = ""
        self._duration_seconds: float | None = None

    @staticmethod
    @override
    def name() -> str:
        return "synergy"

    @override
    def get_version_command(self) -> str | None:
        return f"{shlex.quote(SYNERGY_BINARY)} --version"

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
        log_path = PurePosixPath("/logs/agent/synergy.jsonl")
        started_at = time.monotonic()

        with tempfile.TemporaryDirectory(prefix="synergy-harbor-") as temporary_directory:
            temporary_root = Path(temporary_directory)
            host_instruction = temporary_root / "instruction.txt"
            host_config = temporary_root / "80-permissions.jsonc"
            host_instruction.write_text(instruction, encoding="utf-8")
            host_config.write_text(
                json.dumps({"controlProfile": "full_access"}, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            await self.exec_as_root(
                environment,
                command=(
                    f"rm -rf {shlex.quote(str(runtime_home))} "
                    f"{shlex.quote(str(instruction_path))} && "
                    f"mkdir -p {shlex.quote(str(config_path.parent))}"
                ),
            )
            await environment.upload_file(host_config, str(config_path))
            await environment.upload_file(host_instruction, str(instruction_path))
            await self.exec_as_root(
                environment,
                command=self._chown_command(environment, runtime_home, instruction_path),
            )

            model = shlex.quote(self.model_name)
            command = (
                f"{shlex.quote(SYNERGY_BINARY)} send "
                f"--format json --agent synergy --model {model} "
                f"< {shlex.quote(str(instruction_path))} "
                f"2>&1 | stdbuf -oL tee {shlex.quote(str(log_path))}"
            )
            try:
                result = await self.exec_as_agent(
                    environment,
                    command=command,
                    env={"SYNERGY_HOME": str(runtime_home)},
                )
                self._output = result.stdout or ""
            finally:
                self._duration_seconds = time.monotonic() - started_at
                self._apply_context(context)
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
