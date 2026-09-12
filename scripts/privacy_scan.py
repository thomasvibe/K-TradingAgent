"""Pre-publish scan: flag personal identifiers, local paths and secret-like strings in tracked files.

    python scripts/privacy_scan.py [--extra PATTERN ...]

Exit code 1 when anything is found. Add your own identifiers with --extra.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

DEFAULT_PATTERNS = [
    r"/home/[a-z0-9_-]+/",                       # local home paths
    r"/Users/[A-Za-z0-9_-]+/",
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}",  # e-mail addresses
    r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b",           # Telegram bot token
    r"crtfc_key=[A-Za-z0-9]{40}",                # DART key in a URL
    r"X-NCP-APIGW-API-KEY(?:-ID)?:\s*[A-Za-z0-9]{8,}",
    r"(?i)(api[_-]?key|secret|password|token)\s*[=:]\s*['\"]?[A-Za-z0-9/+_-]{16,}",
    r"@[A-Za-z0-9_]{4,}_bot\b",                  # Telegram bot handles
]
ALLOW = [
    r"noreply@anthropic\.com", r"users\.noreply\.github\.com", r"example\.com", r"\.env\.example",
    r"placeholder", r"YOUR_", r"<hidden", r"/home/appuser/",  # upstream Docker user, not a person
]
BINARY = (".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".gguf", ".db", ".zip")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--extra", nargs="*", default=[], help="additional regexes (e.g. your name or handle)")
    args = ap.parse_args()
    patterns = [re.compile(p) for p in DEFAULT_PATTERNS + args.extra]
    allow = [re.compile(a) for a in ALLOW]
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    hits = 0
    for f in files:
        if f == "scripts/privacy_scan.py" or f.lower().endswith(BINARY):
            continue
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for pat in patterns:
                m = pat.search(line)
                if m and not any(a.search(line) for a in allow):
                    hits += 1
                    print(f"{f}:{n}: {m.group(0)[:60]}  | {line.strip()[:100]}")
                    break
    print(f"\n{hits} finding(s) in {len(files)} tracked files")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
