from __future__ import annotations

import shlex
from typing import Any, override

from harbor.environments.base import BaseEnvironment

from synergy_harbor.agent import Synergy
from synergy_harbor.installer import (
    INSTALL_ROOT,
    PLATFORM_PROBE_COMMAND,
    PREREQUISITE_COMMAND,
    SYNERGY_BINARY,
    parse_platform_probe,
)

DEV_VERSION_PREFIX = "0.0.0-"
DEV_DIST_ROOT = "/opt/synergy-dev-dist"
DEV_NODE_PATH = "/opt/synergy-dev-node_modules"


class SynergyDev(Synergy):
    """Run a locally built Synergy dev binary mounted read-only by Harbor."""

    def __init__(
        self,
        *args: Any,
        version: str | None = None,
        **kwargs: Any,
    ) -> None:
        if version is not None:
            raise ValueError("The local Synergy dev version is detected from the mounted binary")
        # Pop extra_env from kwargs so it cannot be passed twice to Synergy.
        extra_env = kwargs.pop("extra_env", None)
        if extra_env is not None and not isinstance(extra_env, dict):
            raise TypeError("extra_env must be a dict")
        run_env = dict(extra_env or {})
        run_env["NODE_PATH"] = DEV_NODE_PATH
        # The dev build is compiled from a source checkout that includes the
        # --workflow lightloop CLI option, so lightloop mode is allowed here.
        kwargs.setdefault("allow_lightloop", True)
        kwargs["extra_env"] = run_env
        super().__init__(*args, **kwargs)
        self._version = None

    @override
    def get_version_command(self) -> str | None:
        command = super().get_version_command()
        if command is None:
            return None
        return f"export NODE_PATH={shlex.quote(DEV_NODE_PATH)}; {command}"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(environment, command=PREREQUISITE_COMMAND)
        probe = await self.exec_as_root(environment, command=PLATFORM_PROBE_COMMAND)
        asset = parse_platform_probe(probe.stdout or "")
        target = asset.filename.removesuffix(".tar.gz")
        source_binary = f"{DEV_DIST_ROOT}/{target}/bin/synergy"
        expected_prefix = shlex.quote(DEV_VERSION_PREFIX)
        node_path = shlex.quote(DEV_NODE_PATH)
        binary = shlex.quote(SYNERGY_BINARY)
        await self.exec_as_root(
            environment,
            command=f"""set -euo pipefail
source_binary={shlex.quote(source_binary)}
test -x "$source_binary"
rm -rf {shlex.quote(INSTALL_ROOT)}
mkdir -p {shlex.quote(INSTALL_ROOT)}/bin
ln -s "$source_binary" {shlex.quote(SYNERGY_BINARY)}
synergy_home=$(mktemp -d /installed-agent/synergy-install-XXXXXX)
trap 'rm -rf "$synergy_home"' EXIT
version_output=$(
  HOME="$synergy_home" SYNERGY_HOME="$synergy_home" NODE_PATH={node_path} {binary} --version
)
case "$version_output" in
  {expected_prefix}*) ;;
  *) exit 1 ;;
esac
""",
        )
