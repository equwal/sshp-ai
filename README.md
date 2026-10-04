# sshp-ai

A parallel SSH runner for AI coding agents: run one command on many hosts at once and get per-host output and exit codes.

sshp-ai wraps [sshp](https://github.com/bahamas10/sshp) by Dave Eddy (bahamas10), a fast parallel SSH executor written in C. It adds two things:

- **`skills/sshp/SKILL.md`**: an agent skill that teaches when and how to use sshp: `-n` dry runs before risky commands, `BatchMode` and timeouts so nothing hangs, output grouping, and per-host exit codes. It works in the skills folders of Claude Code, Codex, Copilot CLI and Antigravity.
- **`sshp_mcp.py`**: a stdio MCP server (Python 3 stdlib only) with two tools:
  - `sshp_run(command, hosts?, max_jobs?, timeout?, user?, dry_run?)` returns JSON with each host's output and exit code.
  - `sshp_hosts()` lists the allowlisted hosts and whether writes are enabled.

sshp-ai does not include sshp's code. Install sshp yourself.

## Safety model

The operator sets the limits in a config file. The model cannot change them.

- **Host allowlist.** Only hosts in the hosts file can be targeted.
- **Conservative default.** Out of the box, a command must start with an allowed verb (`uptime`, `df`, `systemctl status`, `journalctl`, `docker ps`, ...) and must not contain shell metacharacters (`; | & > < $ \` ( )`). Write-like forms such as `find -delete`, `systemctl restart` or `date -s` are refused.
- **`"allow_write": true` makes it a general-purpose runner.** Set it in the config to run any command (restarts, installs, edits). Only the operator can set it. The host allowlist still applies.
- ssh runs with `BatchMode=yes` and `ConnectTimeout=10`, so a prompt fails fast. The whole run has a timeout (default 60 s, max 600 s).

## Install

1. Build and install sshp (see its README). It must be on `PATH`, or set `"sshp"` in the config.
2. Copy the skill into your agent's skills folder:

   ```sh
   for d in ~/.claude/skills ~/.codex/skills ~/.copilot/skills ~/.gemini/skills ~/.gemini/antigravity/skills; do
     mkdir -p "$d" && cp -r skills/sshp "$d/"
   done
   ```

3. Create the hosts file and (optionally) the config:

   ```sh
   mkdir -p ~/.config/sshp-ai
   printf 'web1.example.com\nroot@db1.example.com\n' > ~/.config/sshp-ai/hosts
   ```

   `~/.config/sshp-ai/config.json` (all keys optional):

   ```json
   {
     "hosts_file": "~/.config/sshp-ai/hosts",
     "allow_write": false,
     "sshp": "sshp",
     "ssh_options": ["BatchMode=yes", "ConnectTimeout=10"]
   }
   ```

   Set `SSHP_AI_CONFIG` to use another config path.

4. Register the MCP server, for example in Claude Code:

   ```sh
   claude mcp add -s user sshp -- python3 /path/to/sshp_mcp.py
   ```

   Other clients take the same command (`python3 /path/to/sshp_mcp.py`) in their MCP config.

## Example result

```json
{
 "sshp_exit_code": 1,
 "dry_run": false,
 "hosts": {
  "web1.example.com": {"output": " 10:00:01 up 3 days, load average: 0.10", "exit_code": 0},
  "db1.example.com": {"output": "ssh: connect to host db1.example.com port 22: Connection refused", "exit_code": 255}
 },
 "sshp_stderr": ""
}
```

sshp merges each host's stdout and stderr into one stream, so `output` holds both.

## Tests

```sh
python3 -m unittest -v test_sshp_mcp
```

The policy tests run anywhere. The integration tests need `sshp` on `PATH` and a POSIX system; a mock `ssh` script plays the remote hosts, so no network is used.

## License

MIT, the same license as sshp. sshp is Copyright 2021 Dave Eddy.
