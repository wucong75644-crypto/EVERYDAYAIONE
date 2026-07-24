"""Dedicated KEK environment file deployment contract."""

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/validate-kek-env.sh"


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_valid_kek_file_requires_exact_keys_and_mode_0600(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env.kek"
    path.write_text(
        "CONFIG_KEK_CURRENT_VERSION=v1\n"
        'CONFIG_KEK_KEYRING_JSON={"v1":"'
        'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="}\n',
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(path)

    assert result.returncode == 0
    assert "合同验证通过" in result.stdout


def test_kek_file_rejects_non_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / ".env.kek"
    path.write_text(
        "CONFIG_KEK_CURRENT_VERSION=v1\n"
        'CONFIG_KEK_KEYRING_JSON={"v1":"key"}\n',
        encoding="utf-8",
    )
    path.chmod(0o644)

    result = _run(path)

    assert result.returncode == 1
    assert "0600" in result.stderr


def test_kek_file_rejects_template_placeholder(tmp_path: Path) -> None:
    result = _run(ROOT / "deploy/env-templates/kek.env.template")

    assert result.returncode == 1
    assert "0600" in result.stderr or "占位符" in result.stderr


def test_kek_file_rejects_extra_configuration(tmp_path: Path) -> None:
    path = tmp_path / ".env.kek"
    path.write_text(
        "CONFIG_KEK_CURRENT_VERSION=v1\n"
        'CONFIG_KEK_KEYRING_JSON={"v1":"key"}\n'
        "UNEXPECTED=value\n",
        encoding="utf-8",
    )
    path.chmod(0o600)

    result = _run(path)

    assert result.returncode == 1
    assert "只能包含两项" in result.stderr
