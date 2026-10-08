#!/usr/bin/env bash
# PreToolUse guard. Exit 2 = block the action and show stderr to Claude.
# Blocks: (1) writes to secret files, (2) git push to main/master, force pushes, hard resets.
input="$(cat)"
python3 - "$input" <<'PY'
import json, re, sys
try:
    data = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)  # never block on malformed input
tool = data.get("tool_name", "")
ti = data.get("tool_input", {}) or {}

def block(msg):
    print("BLOCKED by .claude/hooks/guard.sh: " + msg, file=sys.stderr)
    sys.exit(2)

if tool in ("Write", "Edit", "MultiEdit"):
    path = ti.get("file_path", "")
    name = path.rsplit("/", 1)[-1]
    if re.match(r"^\.env(\..+)?$", name) and not name.endswith((".example", ".sample")):
        block(f"{name} may hold secrets. Edit it manually, or change .env.example instead.")
    if re.search(r"\.(pem|key|p12)$", name) or "/.ssh/" in path:
        block(f"{path} is a credential file.")

if tool == "Bash":
    cmd = ti.get("command", "")
    if re.search(r"\bgit\s+push\b.*(--force|--force-with-lease|\s-f\b)", cmd):
        block("force push is not allowed.")
    if re.search(r"\bgit\s+push\b[^;&|]*\b(origin\s+)?(main|master)\b", cmd) or re.search(r"\bgit\s+push\b[^;&|]*HEAD:(main|master)\b", cmd):
        block("do not push to main/master. Push a branch and open a pull request.")
    if re.search(r"\bgit\s+reset\s+--hard\b", cmd):
        block("git reset --hard discards work. Ask the user first.")
    if re.search(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|\brm\s+-[a-zA-Z]*f[a-zA-Z]*r", cmd) and re.search(r"(\s/\s|\s~|\s\$HOME|\s\.\s*$|\s\*)", cmd):
        block("recursive force delete on a broad target. Ask the user first.")
sys.exit(0)
PY
