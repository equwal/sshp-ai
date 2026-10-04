---
name: sshp
description: Run one command on many SSH hosts in parallel with sshp, and read per-host output and exit codes. Use for fleet-wide checks such as "is the service up on every server", "disk space on all hosts", "which hosts run kernel X", or when the user names several hosts or a hosts file. Read-only checks first; dry run before any write.
---

# sshp: parallel SSH for fleet checks

`sshp` (https://github.com/bahamas10/sshp) runs one command on many hosts at once and prefixes each output line with `[host]`. Prefer it over a shell loop of `ssh` calls.

If the `sshp_run` MCP tool is available, use it: it enforces the host allowlist and read-only mode, and returns JSON per host. Otherwise call the `sshp` binary as below.

## Rules

1. **Read-only first.** Start with commands that change nothing: `uptime`, `df -h`, `systemctl is-active X`, `journalctl -u X -n 50`, `cat /etc/os-release`.
2. **Dry run before a write.** Run with `-n` to list what would run. Show the user the host list and command. Get an explicit yes before any command that changes state (restart, install, edit, delete, reboot).
3. **Never let it hang.** Always pass `-o BatchMode=yes -o ConnectTimeout=10`. A password prompt or host-key prompt then fails fast instead of blocking.
4. **Limit concurrency for heavy work.** Default `-m 50`. Use `-m 1` for rolling changes (one host at a time), `-m 5` for anything that loads the hosts.
5. **Read exit codes per host.** Always pass `-e`. sshp prints `[host] exited: N (T ms)`. Report the hosts that failed, not only the overall status. sshp itself exits non-zero if any host failed.
6. **Use a hosts file.** `-f hosts` reads one host per line (`user@host` works). Do not invent hosts; use the ones the user gives or the configured file (`~/.config/sshp-ai/hosts`).

## Commands

```sh
# Check: one line per host, exit codes, no prompts
sshp -e -c off -o BatchMode=yes -o ConnectTimeout=10 -f hosts uptime

# Group output by host (multi-line output stays together)
sshp -g -e -f hosts -- df -h /

# Join mode: group hosts that produced identical output (spot the odd one out)
sshp -j -f hosts -- uname -r

# Dry run: show what would run, start nothing
sshp -n -f hosts -- systemctl restart nginx

# Rolling change after the user said yes: one host at a time, login as root
sshp -m 1 -g -e -l root -f hosts -- systemctl restart nginx
```

Put `--` before the remote command so sshp does not parse its flags. Quote a remote pipeline as one argument: `sshp -f hosts -- 'ps aux | grep nginx'`.

## Exit codes

| sshp exit | Meaning |
|---|---|
| 0 | Every host exited 0 |
| 1 | At least one host exited non-zero (see `[host] exited:` lines) |
| 2 | Bad sshp arguments |
| 4 | Interrupted (SIGINT or SIGTERM) |

Per host, `255` means ssh itself failed (DNS, refused, auth, host key). Any other code is the remote command's own exit code.
