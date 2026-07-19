from __future__ import annotations

import shlex
from dataclasses import dataclass

SYNERGY_VERSION = "2.4.3"
INSTALL_ROOT = "/installed-agent/synergy-dist"
SYNERGY_BINARY = f"{INSTALL_ROOT}/bin/synergy"
RELEASE_BASE_URL = f"https://github.com/SII-Holos/synergy/releases/download/v{SYNERGY_VERSION}"


@dataclass(frozen=True)
class ReleaseAsset:
    filename: str
    sha256: str

    @property
    def url(self) -> str:
        return f"{RELEASE_BASE_URL}/{self.filename}"


_ASSETS = {
    ("arm64", False, True): ReleaseAsset(
        "synergy-linux-arm64.tar.gz",
        "258f720fd839e7c1c0f7551a806da33935515a776b857802e8087748d1208d95",
    ),
    ("arm64", True, True): ReleaseAsset(
        "synergy-linux-arm64-musl.tar.gz",
        "5615376b9c3b77ff571c85a95dcb969d66e87b79c54637853547a5328921b6ce",
    ),
    ("x64", False, True): ReleaseAsset(
        "synergy-linux-x64.tar.gz",
        "23610148f05e3ee93191de822b155d49de1c9b6fe7838a44028d9cf5dea4eb3f",
    ),
    ("x64", True, True): ReleaseAsset(
        "synergy-linux-x64-musl.tar.gz",
        "c3f42383f7767cf2dc8f0043a033624f6bbd8266caf27887b5c12b6d583e8b57",
    ),
    ("x64", False, False): ReleaseAsset(
        "synergy-linux-x64-baseline.tar.gz",
        "467f49899928329912975edb3fe5c1c607fbcd2fb3968d395c57857048a7d80d",
    ),
    ("x64", True, False): ReleaseAsset(
        "synergy-linux-x64-baseline-musl.tar.gz",
        "201734845de31648daf90da1b18779b5d8ceeed6af819a2b55f6ef9d4c212098",
    ),
}

PLATFORM_PROBE_COMMAND = """set -euo pipefail
uname -m
if [ -f /etc/alpine-release ] || \
  (command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | grep -qi musl); then
  echo musl
else
  echo glibc
fi
if [ "$(uname -m)" = "x86_64" ] || [ "$(uname -m)" = "amd64" ]; then
  if grep -qi avx2 /proc/cpuinfo 2>/dev/null; then echo avx2; else echo baseline; fi
else
  echo avx2
fi
"""

PREREQUISITE_COMMAND = """set -euo pipefail
if command -v apt-get >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y bash ca-certificates coreutils curl tar
elif command -v apk >/dev/null 2>&1; then
  apk add --no-cache bash ca-certificates coreutils curl tar
elif command -v dnf >/dev/null 2>&1; then
  dnf install -y bash ca-certificates coreutils curl tar
elif command -v yum >/dev/null 2>&1; then
  yum install -y bash ca-certificates coreutils curl tar
fi
command -v bash >/dev/null
command -v curl >/dev/null
command -v sha256sum >/dev/null
command -v tar >/dev/null
command -v stdbuf >/dev/null
command -v tee >/dev/null
"""


def select_release_asset(machine: str, *, musl: bool, avx2: bool) -> ReleaseAsset:
    normalized = machine.strip().lower()
    if normalized in {"aarch64", "arm64"}:
        architecture = "arm64"
        avx2 = True
    elif normalized in {"amd64", "x86_64", "x64"}:
        architecture = "x64"
    else:
        raise ValueError(f"Unsupported Linux architecture: {machine!r}")

    return _ASSETS[(architecture, musl, avx2)]


def parse_platform_probe(stdout: str) -> ReleaseAsset:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if len(lines) != 3:
        raise ValueError(f"Unexpected platform probe output: {stdout!r}")
    machine, libc, cpu = lines
    if libc not in {"glibc", "musl"} or cpu not in {"avx2", "baseline"}:
        raise ValueError(f"Unexpected platform probe output: {stdout!r}")
    return select_release_asset(machine, musl=libc == "musl", avx2=cpu == "avx2")


def build_install_command(asset: ReleaseAsset) -> str:
    url = shlex.quote(asset.url)
    digest = shlex.quote(asset.sha256)
    install_root = shlex.quote(INSTALL_ROOT)
    binary = shlex.quote(SYNERGY_BINARY)
    return f"""set -euo pipefail
tmpdir=$(mktemp -d)
synergy_home=$(mktemp -d /installed-agent/synergy-install-XXXXXX)
trap 'rm -rf "$tmpdir" "$synergy_home"' EXIT
curl -fsSL {url} -o "$tmpdir/synergy.tar.gz"
printf '%s  %s\\n' {digest} "$tmpdir/synergy.tar.gz" | sha256sum -c -
rm -rf {install_root}
mkdir -p {install_root}
tar -xzf "$tmpdir/synergy.tar.gz" -C {install_root}
chmod -R a+rX {install_root}
HOME="$synergy_home" SYNERGY_HOME="$synergy_home" {binary} --version
"""
