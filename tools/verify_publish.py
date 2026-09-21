"""Verify the published GitHub repo matches this working tree, blob for blob.

Compares git blob SHAs (authoritative) instead of fetched bytes, because
raw.githubusercontent.com serves a cached copy for minutes after a push and
would report false differences.

Run: python tools/verify_publish.py
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.request

OWNER = "shojaee76-cmyk"
NAME = "divar-mcp"
ROOT = pathlib.Path(__file__).resolve().parent.parent


def token() -> str:
    envp = pathlib.Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env"))
    if envp.exists():
        for line in envp.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r'\s*(?:export\s+)?GITHUB_TOKEN\s*=\s*"?([^"\s]+)"?', line)
            if m:
                return m.group(1)
    return os.environ.get("GITHUB_TOKEN", "")


def api(path: str, tok: str):
    req = urllib.request.Request(
        "https://api.github.com" + path,
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "user-agent": "divar-mcp-verify"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def local_blob(rel: str) -> str:
    out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"HEAD:{rel}"],
                         capture_output=True, text=True)
    return out.stdout.strip()


def main() -> int:
    tok = token()
    if not tok:
        print("no GITHUB_TOKEN found", file=sys.stderr)
        return 2
    tree = api(f"/repos/{OWNER}/{NAME}/git/trees/main?recursive=1", tok)
    remote = {t["path"]: t["sha"] for t in tree["tree"] if t["type"] == "blob"}
    # -z: paths may contain spaces ("launchers/DIVAR search.bat")
    listed = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z"],
                            capture_output=True, text=True).stdout
    files = [f for f in listed.split("\0") if f]
    missing, mismatched, ok = [], [], 0
    for rel in files:
        sha = local_blob(rel)
        if rel not in remote:
            missing.append(rel)
        elif remote[rel] != sha:
            mismatched.append((rel, sha[:10], remote[rel][:10]))
        else:
            ok += 1
    print(f"repo: https://github.com/{OWNER}/{NAME} | files in git: {len(files)} | remote blobs: {len(remote)}")
    print(f"identical blobs: {ok}")
    for rel in missing:
        print(f"  MISSING ON REMOTE: {rel}")
    for rel, l, r in mismatched:
        print(f"  MISMATCH {rel}: local {l} vs remote {r}")
    extra = set(remote) - set(files)
    for rel in sorted(extra):
        print(f"  EXTRA ON REMOTE: {rel}")
    ok_all = not missing and not mismatched and not extra
    print("VERDICT:", "published repo matches the working tree exactly" if ok_all else "DIFFERENCES FOUND")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
