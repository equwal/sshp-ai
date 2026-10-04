#!/usr/bin/env python3
"""Stdio MCP server that runs commands on many hosts through sshp.

Safety comes from the server config, never from the model:
- Only hosts listed in the hosts file can be targeted.
- Read-only mode is the default. A command must start with an allowed verb
  and must not contain shell metacharacters, unless the config sets
  "allow_write": true.

Config: $SSHP_AI_CONFIG, else ~/.config/sshp-ai/config.json. Keys:
  hosts_file    path to a newline-separated hosts file (default ~/.config/sshp-ai/hosts)
  allow_write   bool, default false
  read_only_verbs  list of first words allowed in read-only mode (optional override)
  sshp          sshp binary (default "sshp")
  ssh_options   list of "key=value" ssh options (default BatchMode=yes, ConnectTimeout=10)
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile

VERSION = "0.1.0"
CONFIG_DIR = os.path.expanduser("~/.config/sshp-ai")

READ_ONLY_VERBS = [
    "uptime", "uname", "hostname", "whoami", "id", "date", "df", "du", "free",
    "cat", "head", "tail", "ls", "stat", "wc", "grep", "ps", "pgrep", "who",
    "w", "last", "lsblk", "ip", "ss", "netstat", "systemctl", "journalctl",
    "docker", "nproc", "lscpu", "env", "printenv", "which", "test", "echo",
    "sha256sum", "md5sum", "find", "dmesg", "uptime", "mount",
]
# Subcommands of multi-purpose verbs that are safe to read.
READ_ONLY_SUBCOMMANDS = {
    "systemctl": {"status", "is-active", "is-enabled", "is-failed", "list-units",
                  "list-timers", "show", "cat", "--failed"},
    "docker": {"ps", "images", "inspect", "logs", "stats", "version", "info"},
    "ip": {"a", "addr", "address", "r", "route", "link", "-br", "-4", "-6"},
}
FORBIDDEN = re.compile(r"[;&|<>`$\n\\]|\(|\)")
FIND_WRITE = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"}
EXIT_RE = re.compile(r"^\[(?P<host>[^\]]+)\] exited: (?P<code>-?\d+)")
LINE_RE = re.compile(r"^\[(?P<host>[^\]]+)\] ?(?P<text>.*)$")


def load_config():
    path = os.environ.get("SSHP_AI_CONFIG", os.path.join(CONFIG_DIR, "config.json"))
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.setdefault("hosts_file", os.path.join(CONFIG_DIR, "hosts"))
    cfg["hosts_file"] = os.path.expanduser(cfg["hosts_file"])
    cfg.setdefault("allow_write", False)
    cfg.setdefault("read_only_verbs", READ_ONLY_VERBS)
    cfg.setdefault("sshp", "sshp")
    cfg.setdefault("ssh_options", ["BatchMode=yes", "ConnectTimeout=10"])
    return cfg


def read_hosts(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]


def check_command(command, cfg):
    """Return an error string, or None if the command is allowed."""
    if not command.strip():
        return "empty command"
    if cfg["allow_write"]:
        return None
    if FORBIDDEN.search(command):
        return "read-only mode: shell metacharacters are not allowed"
    try:
        words = shlex.split(command)
    except ValueError as e:
        return f"cannot parse command: {e}"
    verb = words[0]
    if verb not in cfg["read_only_verbs"]:
        return f"read-only mode: '{verb}' is not an allowed verb"
    subs = READ_ONLY_SUBCOMMANDS.get(verb)
    if subs is not None and len(words) > 1 and words[1] not in subs:
        return f"read-only mode: '{verb} {words[1]}' is not allowed"
    if verb == "find" and FIND_WRITE.intersection(words):
        return "read-only mode: find actions that write or execute are not allowed"
    if verb == "dmesg" and any(w.startswith("-C") or w in ("-c", "--clear", "--read-clear") for w in words):
        return "read-only mode: dmesg clear is not allowed"
    if verb == "date" and any(w.startswith("-s") or w == "--set" for w in words):
        return "read-only mode: date --set is not allowed"
    if verb == "hostname" and len(words) > 1 and not words[1].startswith("-"):
        return "read-only mode: setting the hostname is not allowed"
    return None


def sshp_run(args, cfg):
    command = args.get("command", "")
    allowed = read_hosts(cfg["hosts_file"])
    hosts = args.get("hosts") or allowed
    bad = [h for h in hosts if h not in allowed]
    if bad:
        return {"error": f"hosts not in allowlist {cfg['hosts_file']}: {bad}"}
    if not hosts:
        return {"error": f"no hosts: add them to {cfg['hosts_file']}"}
    err = check_command(command, cfg)
    if err:
        return {"error": err}

    max_jobs = max(1, min(int(args.get("max_jobs", 10)), 50))
    timeout = max(1, min(int(args.get("timeout", 60)), 600))
    argv = [cfg["sshp"], "-e", "-c", "off", "-m", str(max_jobs)]
    if args.get("dry_run"):
        argv.append("-n")
    user = args.get("user")
    if user:
        if not re.fullmatch(r"[A-Za-z0-9._-]+", user):
            return {"error": "invalid user"}
        argv += ["-l", user]
    for opt in cfg["ssh_options"]:
        argv += ["-o", opt]

    with tempfile.NamedTemporaryFile("w", suffix=".hosts", delete=False) as f:
        f.write("\n".join(hosts) + "\n")
        hosts_path = f.name
    try:
        argv += ["-f", hosts_path, "--", command]
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"error": f"sshp did not finish within {timeout}s"}
        except FileNotFoundError:
            return {"error": f"sshp binary not found: {cfg['sshp']}"}
    finally:
        os.unlink(hosts_path)

    results = {h: {"output": [], "exit_code": None} for h in hosts}
    for line in p.stdout.splitlines():
        m = EXIT_RE.match(line)
        if m and m["host"] in results:
            results[m["host"]]["exit_code"] = int(m["code"])
            continue
        m = LINE_RE.match(line)
        if m and m["host"] in results:
            results[m["host"]]["output"].append(m["text"])
    return {
        "sshp_exit_code": p.returncode,
        "dry_run": bool(args.get("dry_run")),
        "hosts": {h: {"output": "\n".join(r["output"]), "exit_code": r["exit_code"]}
                  for h, r in results.items()},
        "sshp_stderr": p.stderr.strip(),
    }


TOOLS = [
    {
        "name": "sshp_run",
        "description": "Run one command on many SSH hosts in parallel with sshp. "
                       "Returns per-host output (stdout+stderr merged) and exit code. "
                       "Hosts must be in the server allowlist. Read-only commands only "
                       "unless the operator enabled writes in the server config.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Remote command, e.g. 'uptime' or 'df -h /'."},
                "hosts": {"type": "array", "items": {"type": "string"},
                          "description": "Subset of allowlisted hosts. Default: all."},
                "max_jobs": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 60,
                            "description": "Seconds for the whole run."},
                "user": {"type": "string", "description": "SSH login name (-l)."},
                "dry_run": {"type": "boolean", "default": False,
                            "description": "Pass -n: show what would run, start nothing."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "sshp_hosts",
        "description": "List allowlisted hosts and whether writes are enabled.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def handle(req):
    method, rid = req.get("method"), req.get("id")
    if rid is None:
        return None  # notification
    if method == "initialize":
        result = {"protocolVersion": req.get("params", {}).get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "sshp-ai", "version": VERSION}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = req.get("params", {})
        cfg = load_config()
        if params.get("name") == "sshp_run":
            out = sshp_run(params.get("arguments", {}), cfg)
        elif params.get("name") == "sshp_hosts":
            out = {"hosts": read_hosts(cfg["hosts_file"]), "allow_write": cfg["allow_write"]}
        else:
            return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "unknown tool"}}
        result = {"content": [{"type": "text", "text": json.dumps(out, indent=1)}],
                  "isError": "error" in out}
    else:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:  # keep the server alive on bad input
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
