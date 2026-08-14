"""Shared command fixtures for deployment contract tests."""

from pathlib import Path


def fake_installer_commands(directory: Path) -> tuple[Path, Path]:
    fake_bin = directory / "bin"
    fake_bin.mkdir()
    calls = directory / "systemctl-calls"
    commands = {
        "sudo": '#!/bin/sh\nexec "$@"\n',
        "id": (
            '#!/bin/sh\nif [ "$1" = -u ] && '
            '[ "$2" = everydayai-agent-runtime ]; then echo 1201; exit 0; fi\n'
            'if [ "$1" = -nG ] && [ "$2" = everydayai-agent-runtime ]; then '
            'echo "everydayai-app everydayai-sandbox-io '
            'everydayai-runtime-model-secret"; exit 0; fi\n'
            'exec /usr/bin/id "$@"\n'
        ),
        "getent": (
            '#!/bin/sh\nif [ "$1" = group ] && '
            '[ "$2" = everydayai-runtime-model-secret ]; then '
            "echo 'everydayai-runtime-model-secret:x:987:everydayai-agent-runtime'; "
            "exit 0; fi\nexit 2\n"
        ),
        "stat": (
            '#!/bin/sh\ncase "${1:-}:${2:-}" in\n'
            '  -c:%u|-f:%u) echo "${FAKE_FILE_UID:-0}"; exit 0 ;;\n'
            '  -c:%g|-f:%g) echo "${FAKE_FILE_GID:-987}"; exit 0 ;;\n'
            'esac\nexec /usr/bin/stat "$@"\n'
        ),
        "systemctl": (
            f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> \'{calls}\'\n'
            'if [ "$2" = everydayai-agent-model-gateway ]; then '
            '[ "$1" = is-active ] && echo inactive || echo not-found; exit 0; fi\n'
        ),
    }
    for name, content in commands.items():
        command = fake_bin / name
        command.write_text(content, encoding="utf-8")
        command.chmod(0o755)
    return fake_bin, calls
