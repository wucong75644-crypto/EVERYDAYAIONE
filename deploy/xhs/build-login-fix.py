#!/usr/bin/env python3
"""Build the pinned XHS login fix into an explicit output; never install or restart."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

SOURCE_HASHES = {'service.go': '7946b0d2571328e4a80ef73a783263181e95471c3241331336c4748181e15c7c', 'login_session.go': 'e84206bfdbd8c4952e2120de473d3295adab23b90693591e9c4b0b6a2dc50828', 'xiaohongshu/search.go': '81ab35ccd60b4a7202786182603ba7a2476139c2875c7d04fa27d94d85f6ac79'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--go', required=True, type=Path)
    parser.add_argument('--browser', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    for name, expected in SOURCE_HASHES.items():
        if hashlib.sha256((source/name).read_bytes()).hexdigest() != expected:
            raise SystemExit('XHS source does not match pinned version: '+name)
    assets = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='reach-xhs-build-') as tmp:
        tree = Path(tmp)/'source'
        shutil.copytree(source, tree, ignore=shutil.ignore_patterns('.git', '._*'))
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(assets/'login-browser.patch')], cwd=tree, check=True)
        shutil.copyfile(assets/'login_browser_test.go', tree/'everydayai_login_browser_test.go')
        subprocess.run([str(args.go.parent/'gofmt'), '-w', 'service.go', 'login_session.go', 'everydayai_login_browser_test.go'], cwd=tree, check=True)
        for name in ('search_readiness.go', 'search_readiness_test.go'):
            shutil.copyfile(assets/name, tree/'xiaohongshu'/('everydayai_'+name))
        env = {**os.environ, 'XHS_TEST_BROWSER': str(args.browser.resolve())}
        subprocess.run([str(args.go.parent/'gofmt'), '-w', 'xiaohongshu/search.go', 'xiaohongshu/everydayai_search_readiness.go', 'xiaohongshu/everydayai_search_readiness_test.go'], cwd=tree, check=True)
        subprocess.run([str(args.go), 'test', '-p=2', '-race', '-run', 'TestEverydayAISearchReadiness', './xiaohongshu'], cwd=tree, env=env, check=True)
        subprocess.run([str(args.go), 'test', '-p=2', '-race', '-run', 'TestEverydayAIPendingScanStatus', '.'], cwd=tree, check=True)
        binary = Path(tmp)/'xiaohongshu-mcp'
        subprocess.run([str(args.go), 'build', '-p=2', '-o', str(binary), '.'], cwd=tree, check=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(binary, args.output)
        args.output.chmod(0o755)
    print('Built XHS login fix sha256='+hashlib.sha256(args.output.read_bytes()).hexdigest())

if __name__ == '__main__':
    main()
