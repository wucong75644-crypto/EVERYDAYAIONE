"""Opt-in NAS no-clobber probe. Run only with user approval for test writes.

Creates one unique hidden temporary directory under the supplied NAS root.
Never opens existing user paths; removes only its own temporary directory.
"""
import argparse
import os
from pathlib import Path
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("--nas-root", required=True)
parser.add_argument("--approved-test-writes", action="store_true")
args = parser.parse_args()
if not args.approved_test_writes:
    parser.error("explicit approval for temporary production test writes is required")

root = Path(args.nas_root).resolve(strict=True)
with tempfile.TemporaryDirectory(prefix=".tool04-publication-probe-", dir=root) as name:
    folder = Path(name)
    source, destination, replacement = [folder / p for p in ("source", "destination", "replacement")]
    with source.open("xb") as stream:
        stream.write(b"tool04-original")
    os.link(source, destination)
    assert destination.read_bytes() == b"tool04-original"
    with replacement.open("xb") as stream:
        stream.write(b"tool04-replacement")
    try:
        os.link(replacement, destination)
    except FileExistsError:
        pass
    else:
        raise AssertionError("existing destination was not protected")
    assert destination.read_bytes() == b"tool04-original"
    assert source.stat().st_ino == destination.stat().st_ino
    print("PASS: complete publication; existing destination preserved")
assert not folder.exists()
print("PASS: probe temporary directory removed")
