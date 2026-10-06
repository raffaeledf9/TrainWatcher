"""Refuse to publish secrets or personal data.

Scans every tracked file at HEAD and, with --history, every blob in every commit reachable from
the given refs (default: main). Fails on token-shaped strings and, when a local .env exists, on
any of its actual values; with --history also on commit emails that aren't GitHub noreply addresses.
Prints only file:line (or the commit hash) and the rule name, never the matched text.

    python scripts/secret_scan.py            # tracked files
    python scripts/secret_scan.py --history  # whole history of main
"""
import re
import subprocess
import sys
from pathlib import Path

RULES = {
    "telegram-bot-token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "github-token": re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{30,})\b"),
    "cloudflare-oauth": re.compile(r"oauth_token\s*=\s*\"[^\"]{20,}\""),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "generic-secret-assign": re.compile(r"(?i)\b(secret|token|password|api_key)\s*[:=]\s*['\"][A-Za-z0-9/+_=-]{24,}['\"]"),
}


def env_values():
    env = Path(__file__).resolve().parent.parent / ".env"
    if not env.exists():
        return {}
    vals = {}
    for line in env.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            if len(v.strip()) >= 6:
                vals[k.strip()] = v.strip()
    return vals


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def scan_text(name, text, secrets, hits):
    for n, line in enumerate(text.splitlines(), 1):
        for rule, rx in RULES.items():
            if rx.search(line):
                hits.append(f"{name}:{n}: {rule}")
        for k, v in secrets.items():
            if v in line:
                hits.append(f"{name}:{n}: value of .env {k}")


def main(argv):
    secrets, hits = env_values(), []
    if "--history" in argv:
        refs = [a for a in argv if not a.startswith("--")] or ["main"]
        seen = set()
        for line in git("rev-list", "--objects", *refs).decode().splitlines():
            parts = line.split(" ", 1)
            if len(parts) != 2 or parts[0] in seen:
                continue
            seen.add(parts[0])
            if git("cat-file", "-t", parts[0]).strip() != b"blob":
                continue
            scan_text(parts[1], git("cat-file", "-p", parts[0]).decode("utf-8", "replace"), secrets, hits)
        for line in git("log", "--format=%h %ae %ce", *refs).decode().splitlines():
            h, *emails = line.split()
            if any(not e.endswith("@users.noreply.github.com") for e in emails):
                hits.append(f"commit {h}: personal email (use the noreply address)")
    else:
        for name in git("ls-files").decode().splitlines():
            p = Path(name)
            if p.is_file():
                scan_text(name, p.read_text(encoding="utf-8", errors="replace"), secrets, hits)
    for h in hits:
        print("SECRET-SCAN:", h)
    print(f"secret scan: {'FAILED' if hits else 'clean'} ({len(secrets)} .env values checked)")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
