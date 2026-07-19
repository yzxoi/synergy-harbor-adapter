import pytest

from synergy_harbor.installer import (
    PREREQUISITE_COMMAND,
    SYNERGY_BINARY,
    build_install_command,
    parse_platform_probe,
    select_release_asset,
)


def test_selects_x64_glibc_asset() -> None:
    asset = select_release_asset("x86_64", musl=False, avx2=True)

    assert asset.filename == "synergy-linux-x64.tar.gz"
    assert asset.sha256 == "23610148f05e3ee93191de822b155d49de1c9b6fe7838a44028d9cf5dea4eb3f"


def test_selects_baseline_musl_asset() -> None:
    asset = select_release_asset("amd64", musl=True, avx2=False)

    assert asset.filename == "synergy-linux-x64-baseline-musl.tar.gz"


def test_arm64_ignores_avx2_probe() -> None:
    regular = select_release_asset("aarch64", musl=False, avx2=False)
    explicit = select_release_asset("arm64", musl=False, avx2=True)

    assert regular == explicit


def test_rejects_unsupported_architecture() -> None:
    with pytest.raises(ValueError, match="Unsupported Linux architecture"):
        select_release_asset("riscv64", musl=False, avx2=True)


def test_parses_platform_probe() -> None:
    asset = parse_platform_probe("x86_64\nmusl\nbaseline\n")

    assert asset.filename == "synergy-linux-x64-baseline-musl.tar.gz"


def test_prerequisites_include_log_capture_tools() -> None:
    assert "command -v stdbuf" in PREREQUISITE_COMMAND
    assert "command -v tee" in PREREQUISITE_COMMAND


def test_install_command_verifies_archive_before_extraction() -> None:
    asset = select_release_asset("x86_64", musl=False, avx2=True)
    command = build_install_command(asset)

    checksum_position = command.index("sha256sum -c -")
    extraction_position = command.index("tar -xzf")
    assert checksum_position < extraction_position
    assert asset.sha256 in command
    assert asset.url in command
    assert SYNERGY_BINARY in command
