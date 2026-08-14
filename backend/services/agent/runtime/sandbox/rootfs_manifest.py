"""Deterministic complete rootfs manifest creation and verification."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


def create_manifest(root: str | Path) -> list[dict[str, object]]:
    base = Path(root).resolve(strict=True)
    entries: list[dict[str, object]] = []
    for path in sorted(base.rglob("*"), key=lambda item: item.as_posix()):
        metadata = path.lstat()
        relative = path.relative_to(base).as_posix()
        kind = (
            "file" if stat.S_ISREG(metadata.st_mode)
            else "directory" if stat.S_ISDIR(metadata.st_mode)
            else "symlink" if stat.S_ISLNK(metadata.st_mode)
            else "other"
        )
        entry: dict[str, object] = {
            "path": relative,
            "kind": kind,
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "size": metadata.st_size,
        }
        if kind == "file":
            entry["sha256"] = _sha256(path)
        elif kind == "symlink":
            entry["target"] = os.readlink(path)
        entries.append(entry)
    return entries


def write_manifest(root: str | Path, output: str | Path) -> None:
    Path(output).write_text(
        json.dumps(create_manifest(root), sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )


def verify_manifest(root: str | Path, manifest: str | Path) -> bool:
    try:
        expected = json.loads(Path(manifest).read_text(encoding="utf-8"))
        return isinstance(expected, list) and create_manifest(root) == expected
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) not in {3, 4}:
        raise SystemExit("usage: rootfs_manifest.py create ROOT OUTPUT | verify ROOT MANIFEST")
    if argv[1] == "create" and len(argv) == 4:
        write_manifest(argv[2], argv[3])
        return 0
    if argv[1] == "verify" and len(argv) == 4:
        return 0 if verify_manifest(argv[2], argv[3]) else 1
    raise SystemExit("invalid rootfs manifest command")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
