from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext

from synergy_harbor.agent import Synergy
from synergy_harbor.installer import SYNERGY_VERSION


class FakeEnvironment:
    default_user: str | int | None = "agent"

    def __init__(self, run_output: str = "") -> None:
        self.run_output = run_output
        self.commands: list[dict[str, Any]] = []
        self.uploads: list[tuple[str, str, str]] = []

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        self.commands.append(
            {
                "command": command,
                "cwd": cwd,
                "env": env,
                "timeout_sec": timeout_sec,
                "user": user,
            }
        )
        if "uname -m" in command:
            return ExecResult(stdout="x86_64\nglibc\navx2\n", stderr="", return_code=0)
        if " send " in command:
            return ExecResult(stdout=self.run_output, stderr="", return_code=0)
        if "--version" in command:
            return ExecResult(stdout=SYNERGY_VERSION, stderr="", return_code=0)
        return ExecResult(stdout="", stderr="", return_code=0)

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        source = Path(source_path)
        content = await asyncio.to_thread(source.read_text)
        self.uploads.append((source.name, target_path, content))


@pytest.fixture
def agent(tmp_path: Path) -> Synergy:
    instance = Synergy(
        logs_dir=tmp_path,
        model_name="anthropic/claude-sonnet-4-5",
        variant="minimal",
        model_options={"thinking": {"type": "disabled"}},
    )
    instance.context_id = UUID("12345678-1234-5678-1234-567812345678")
    return instance


def test_identity_and_version(agent: Synergy) -> None:
    assert agent.name() == "synergy"
    assert agent.version() == SYNERGY_VERSION
    assert agent.import_path() == "synergy_harbor.agent:Synergy"
    version_command = agent.get_version_command()
    assert version_command is not None
    assert 'mktemp -d "${TMPDIR:-/tmp}/synergy-version-XXXXXX"' in version_command
    assert 'HOME="$synergy_home" SYNERGY_HOME="$synergy_home"' in version_command
    assert "trap 'rm -rf \"$synergy_home\"' EXIT" in version_command


def test_rejects_unpinned_version(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pins Synergy"):
        Synergy(logs_dir=tmp_path, version="latest")


def test_rejects_invalid_agent_environment_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="environment variable"):
        Synergy(
            logs_dir=tmp_path,
            model_name="deepseek/deepseek-v4-flash",
            extra_env={"INVALID-NAME": "value"},
        )


@pytest.mark.asyncio
async def test_install_selects_and_verifies_release(agent: Synergy) -> None:
    environment = FakeEnvironment()

    await agent.install(cast(BaseEnvironment, environment))

    commands = "\n".join(item["command"] for item in environment.commands)
    assert "sha256sum -c -" in commands
    assert "synergy-linux-x64.tar.gz" in commands
    assert "bun" not in commands


@pytest.mark.asyncio
async def test_run_uploads_instruction_without_shell_interpolation(agent: Synergy) -> None:
    output = (
        '{"type":"step_finish","sessionID":"ses_test","part":{"id":"finish",'
        '"type":"step-finish","reason":"stop","cost":0.1,"tokens":{"input":10,'
        '"output":4,"reasoning":1,"cache":{"read":2,"write":0}}}}\n'
    )
    environment = FakeEnvironment(run_output=output)
    context = AgentContext()
    instruction = "create a file named quote-'-$HOME.txt"

    await agent.run(instruction, cast(BaseEnvironment, environment), context)

    send_commands = [item for item in environment.commands if " send " in item["command"]]
    assert len(send_commands) == 1
    send = send_commands[0]
    assert instruction not in send["command"]
    assert "--variant minimal" in send["command"]
    assert (
        "< /installed-agent/synergy-instruction-12345678123456781234567812345678.txt"
        in send["command"]
    )
    expected_home = "/installed-agent/synergy-home-12345678123456781234567812345678"
    assert send["env"] == {"HOME": expected_home, "SYNERGY_HOME": expected_home}
    assert expected_home != "/root"
    assert expected_home != "/home/agent"

    uploaded_by_name = {name: (target, content) for name, target, content in environment.uploads}
    assert uploaded_by_name["instruction.txt"][1] == instruction
    assert '"controlProfile":"full_access"' in uploaded_by_name["80-permissions.jsonc"][1]
    assert '"anthropic"' in uploaded_by_name["20-providers.jsonc"][1]
    assert '"claude-sonnet-4-5"' in uploaded_by_name["20-providers.jsonc"][1]
    assert '"thinking":{"type":"disabled"}' in uploaded_by_name["20-providers.jsonc"][1]
    assert "10-models.jsonc" not in uploaded_by_name
    assert context.n_input_tokens == 10
    assert context.n_output_tokens == 5
    assert context.n_cache_tokens == 2
    assert context.cost_usd == 0.1
    assert context.metadata is not None
    assert context.metadata["synergy"]["session_id"] == "ses_test"


@pytest.mark.asyncio
async def test_run_uploads_agent_environment_without_exec_env(tmp_path: Path) -> None:
    secret = "sentinel-provider-secret"
    instance = Synergy(
        logs_dir=tmp_path,
        model_name="deepseek/deepseek-v4-flash",
        extra_env={"DEEPSEEK_API_KEY": secret},
    )
    instance.context_id = UUID("12345678-1234-5678-1234-567812345678")
    environment = FakeEnvironment()

    await instance.run(
        "complete the task",
        cast(BaseEnvironment, environment),
        AgentContext(),
    )

    assert instance.extra_env == {}
    uploaded_by_name = {name: (target, content) for name, target, content in environment.uploads}
    env_target, env_content = uploaded_by_name[".provider-env.sh"]
    assert env_target.endswith("/synergy-home-12345678123456781234567812345678/.provider-env.sh")
    assert f"export DEEPSEEK_API_KEY={secret}" in env_content

    serialized_commands = "\n".join(item["command"] for item in environment.commands)
    serialized_exec_env = "\n".join(
        value for item in environment.commands for value in (item["env"] or {}).values()
    )
    assert secret not in serialized_commands
    assert secret not in serialized_exec_env


def test_classifies_provider_authentication_failure(agent: Synergy) -> None:
    result = ExecResult(
        stdout=(
            '{"type":"error","error":{"name":"APIError","data":'
            '{"message":"invalid x-api-key","statusCode":401}}}\n'
            "SessionTerminalError: invalid x-api-key\n"
        ),
        stderr="",
        return_code=1,
    )

    error = agent._classify_exec_error("synergy send", result)

    assert type(error).__name__ == "AgentAuthenticationError"


@pytest.mark.asyncio
async def test_cleanup_runs_after_agent_failure(agent: Synergy) -> None:
    class FailingEnvironment(FakeEnvironment):
        async def exec(
            self,
            command: str,
            cwd: str | None = None,
            env: dict[str, str] | None = None,
            timeout_sec: int | None = None,
            user: str | int | None = None,
        ) -> ExecResult:
            result = await super().exec(command, cwd, env, timeout_sec, user)
            if " send " in command:
                return ExecResult(
                    stdout='{"type":"error","sessionID":"s","error":{"name":"ApiError"}}\n',
                    stderr="",
                    return_code=1,
                )
            return result

    environment = FailingEnvironment()
    context = AgentContext()

    with pytest.raises(Exception, match="Command failed"):
        await agent.run("fail", cast(BaseEnvironment, environment), context)

    cleanup_commands = [
        item
        for item in environment.commands
        if item["command"].startswith("rm -rf /installed-agent/synergy-home")
    ]
    assert cleanup_commands
    assert context.metadata is not None
    assert context.metadata["synergy"]["error_count"] == 1


@pytest.mark.asyncio
async def test_cleanup_runs_after_setup_failure(agent: Synergy) -> None:
    class FailingUploadEnvironment(FakeEnvironment):
        async def upload_file(self, source_path: Path | str, target_path: str) -> None:
            await super().upload_file(source_path, target_path)
            if target_path.endswith("20-providers.jsonc"):
                raise OSError("injected upload failure")

    environment = FailingUploadEnvironment()

    with pytest.raises(OSError, match="injected upload failure"):
        await agent.run(
            "fail during setup",
            cast(BaseEnvironment, environment),
            AgentContext(),
        )

    cleanup_commands = [
        item
        for item in environment.commands
        if item["command"].startswith("rm -rf /installed-agent/synergy-home")
    ]
    assert cleanup_commands


def test_rejects_unsupported_workflow_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported workflow mode"):
        Synergy(logs_dir=tmp_path, model_name="anthropic/claude-sonnet-4-5", workflow="lattice")


def test_release_adapter_rejects_lightloop_without_dev_build(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="--workflow lightloop"):
        Synergy(logs_dir=tmp_path, model_name="anthropic/claude-sonnet-4-5", workflow="lightloop")


@pytest.mark.asyncio
async def test_run_lightloop_passes_workflow_flag(agent: Synergy) -> None:
    output = (
        '{"type":"step_finish","sessionID":"ses_loop","part":{"id":"finish",'
        '"type":"step-finish","reason":"stop","cost":0.2,"tokens":{"input":20,'
        '"output":8,"reasoning":2,"cache":{"read":4,"write":0}}}}\n'
        '{"type":"lightloop_finish","sessionID":"ses_loop","status":"completed",'
        '"elapsedMs":1234,"timedOut":false}\n'
    )
    environment = FakeEnvironment(run_output=output)
    context = AgentContext()
    instance = Synergy(
        logs_dir=agent.logs_dir,
        model_name="anthropic/claude-sonnet-4-5",
        workflow="lightloop",
        allow_lightloop=True,
    )
    instance.context_id = UUID("12345678-1234-5678-1234-567812345678")

    await instance.run("finish the loop task", cast(BaseEnvironment, environment), context)

    send_commands = [item for item in environment.commands if " send " in item["command"]]
    assert len(send_commands) == 1
    assert "--workflow lightloop" in send_commands[0]["command"]
    assert context.metadata is not None
    assert context.metadata["synergy"]["workflow"] == "lightloop"
    assert context.metadata["synergy"]["session_id"] == "ses_loop"

    uploaded_by_name = {name: (target, content) for name, target, content in environment.uploads}
    models_config = uploaded_by_name["10-models.jsonc"][1]
    assert '"model":"anthropic/claude-sonnet-4-5"' in models_config
    assert '"thinking_model":"anthropic/claude-sonnet-4-5"' in models_config


@pytest.mark.asyncio
async def test_run_default_mode_omits_workflow_flag(agent: Synergy) -> None:
    environment = FakeEnvironment(run_output="")
    context = AgentContext()

    await agent.run("plain task", cast(BaseEnvironment, environment), context)

    send_commands = [item for item in environment.commands if " send " in item["command"]]
    assert len(send_commands) == 1
    assert "--workflow" not in send_commands[0]["command"]
    assert context.metadata is not None
    assert context.metadata["synergy"]["workflow"] is None
