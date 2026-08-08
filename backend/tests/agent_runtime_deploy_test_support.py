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
            'if [ "$1" = -nG ] && [ "$2" = everydayai-agent-model-gateway ]; '
            'then echo "everydayai-model-gateway everydayai-model-gateway-secret"; exit 0; fi\n'
            'if [ "$1" = -nG ] && [ "$2" = everydayai-agent-runtime ]; then '
            'echo "everydayai-app everydayai-model-gateway'
            '${FAKE_RUNTIME_SECRET_GROUP:+ everydayai-model-gateway-secret}"; exit 0; fi\n'
            'exec /usr/bin/id "$@"\n'
        ),
        "getent": (
            '#!/bin/sh\nif [ "$1" = group ] && '
            '[ "$2" = everydayai-model-gateway-secret ]; then '
            "echo 'everydayai-model-gateway-secret:x:987:'; exit 0; fi\nexit 2\n"
        ),
        "stat": (
            '#!/bin/sh\ncase "${1:-}:${2:-}" in\n'
            '  -c:%u|-f:%u) echo "${FAKE_FILE_UID:-0}"; exit 0 ;;\n'
            '  -c:%g|-f:%g) echo "${FAKE_FILE_GID:-987}"; exit 0 ;;\n'
            'esac\nexec /usr/bin/stat "$@"\n'
        ),
        "systemctl": f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> \'{calls}\'\n',
    }
    for name, content in commands.items():
        command = fake_bin / name
        command.write_text(content, encoding="utf-8")
        command.chmod(0o755)
    return fake_bin, calls
