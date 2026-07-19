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
    instance = Synergy(logs_dir=tmp_path, model_name="anthropic/claude-sonnet-4-5")
    instance.context_id = UUID("12345678-1234-5678-1234-567812345678")
    return instance


def test_identity_and_version(agent: Synergy) -> None:
    assert agent.name() == "synergy"
    assert agent.version() == SYNERGY_VERSION
    assert agent.import_path() == "synergy_harbor.agent:Synergy"


def test_rejects_unpinned_version(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="pins Synergy"):
        Synergy(logs_dir=tmp_path, version="latest")


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
    assert (
        "< /installed-agent/synergy-instruction-12345678123456781234567812345678.txt"
        in send["command"]
    )
    assert send["env"] == {
        "SYNERGY_HOME": "/installed-agent/synergy-home-12345678123456781234567812345678"
    }

    uploaded_by_name = {name: (target, content) for name, target, content in environment.uploads}
    assert uploaded_by_name["instruction.txt"][1] == instruction
    assert '"controlProfile":"full_access"' in uploaded_by_name["80-permissions.jsonc"][1]
    assert context.n_input_tokens == 10
    assert context.n_output_tokens == 5
    assert context.n_cache_tokens == 2
    assert context.cost_usd == 0.1
    assert context.metadata is not None
    assert context.metadata["synergy"]["session_id"] == "ses_test"


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
