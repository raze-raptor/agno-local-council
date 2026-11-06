#!/usr/bin/env python3
"""Read-only diagnostic catalogue shared by the server1 broker and the server2 forced command.

An LLM never supplies a command: it supplies a CHECK ID and at most one validated argument. Every check maps to a fixed argv list
(no shell). Output is redacted for secret-looking text and capped. Nothing here can change system state.
"""
import re
import subprocess

MAX_OUT = 6000
TIMEOUT = 20
UNIT = re.compile(r"^[A-Za-z0-9@._:][A-Za-z0-9@._:-]{0,79}\.(service|timer|socket|mount|path)$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
# containers whose logs may hold credentials or personal data: refuse logs for these (status is still allowed)
SENSITIVE = re.compile(r"(?i)vault|authentik|secret|mail|postfix|dovecot|rspamd|mysql|maria|postgres|redis|couch|auth|keycloak|token")

REDACT = [
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|apikey|authorization|bearer|cookie|session[_-]?id)\b(\s*[=:]\s*|\s+)([^\s\"',;]{4,})"), r"\1=<redacted>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "<redacted private key>"),
    (re.compile(r"\b[A-Fa-f0-9]{32,}\b"), "<redacted-hex>"),
    (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "<redacted-token>"),
    (re.compile(r"(?i)\b(sk|pk|ghp|gho|xox[abp]|AKIA)[-_A-Za-z0-9]{12,}"), "<redacted-key>"),
]


def redact(text):
    for rx, rep in REDACT:
        text = rx.sub(rep, text)
    return text


# check id -> (description, argument kind, argv builder)
def _units(a): return ["systemctl", "status", a, "--no-pager", "-l", "-n", "12"]


CHECKS = {
    "uptime_load":       ("uptime and load average", None, lambda a: ["uptime"]),
    "memory":            ("memory and swap (free -h)", None, lambda a: ["free", "-h"]),
    "top_memory":        ("top 12 processes by memory", None, lambda a: ["sh", "-c", "ps -eo pid,user,pcpu,pmem,etime,comm --sort=-pmem | head -13"]),
    "top_cpu":           ("top 12 processes by CPU", None, lambda a: ["sh", "-c", "ps -eo pid,user,pcpu,pmem,etime,comm --sort=-pcpu | head -13"]),
    "disk":              ("filesystem usage (df -h, real filesystems only)", None, lambda a: ["df", "-h", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs", "-x", "overlay"]),
    "failed_units":      ("failed systemd units", None, lambda a: ["systemctl", "--failed", "--no-pager", "--plain"]),
    "timers":            ("systemd timers (next/last run)", None, lambda a: ["systemctl", "list-timers", "--no-pager", "--all"]),
    "unit_status":       ("status of one systemd unit (arg = unit name, e.g. cloudflared.service)", "unit", _units),
    "unit_journal":      ("last 40 journal lines of one unit (arg = unit name)", "unit", lambda a: ["journalctl", "-u", a, "-n", "40", "--no-pager", "-o", "short-iso"]),
    "listening_ports":   ("listening TCP sockets (ss -ltn)", None, lambda a: ["ss", "-ltn"]),
    "kernel_warnings":   ("recent kernel warnings", None, lambda a: ["journalctl", "-k", "-p", "warning", "-n", "30", "--no-pager"]),
    "time_sync":         ("clock / NTP status", None, lambda a: ["timedatectl"]),
    "logins":            ("current and recent logins", None, lambda a: ["sh", "-c", "who; echo; last -n 8"]),
    "upgradable":        ("packages with pending upgrades (no refresh)", None, lambda a: ["sh", "-c", "apt list --upgradable 2>/dev/null | head -25"]),
    "docker_ps":         ("all containers with status", None, lambda a: ["docker", "ps", "-a", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"]),
    "container_state":   ("state/exit code/restarts of one container (arg = container name)", "name",
                          lambda a: ["docker", "inspect", "-f", "state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} started={{.State.StartedAt}} finished={{.State.FinishedAt}} restarts={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}", a]),
    "container_logs":    ("last 40 log lines of one container (arg = name; refused for credential-bearing services)", "name",
                          lambda a: ["docker", "logs", "--tail", "40", "--timestamps", a]),
    "ollama_models":     ("models known to Ollama (server2)", None, lambda a: ["ollama", "list"]),
}


def catalogue():
    return {k: v[0] for k, v in CHECKS.items()}


def _run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
        return p.returncode, (p.stdout + (("\n[stderr] " + p.stderr) if p.stderr.strip() else "")).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"
    except FileNotFoundError:
        return 127, "command not available on this host"


def run_check(check, arg=""):
    """-> (rc, output). Never raises on bad input; returns rc 2 with a reason instead."""
    if check not in CHECKS:
        return 2, "unknown check '%s'. Available: %s" % (check[:40], ", ".join(sorted(CHECKS)))
    desc, kind, build = CHECKS[check]
    arg = (arg or "").strip()
    if kind is None and arg:
        return 2, "this check takes no argument"
    if kind == "unit":
        if not UNIT.match(arg):
            return 2, "argument must be a unit name like 'nginx.service'"
        rc, out = _run(["systemctl", "list-unit-files", "--no-pager", "--plain", "--no-legend", arg])
        rc2, out2 = _run(["systemctl", "list-units", "--all", "--no-pager", "--plain", "--no-legend", arg])
        if arg not in out and arg not in out2:
            return 2, "no such unit on this host"
    if kind == "name":
        if not NAME.match(arg):
            return 2, "argument must be a container name"
        rc, names = _run(["docker", "ps", "-a", "--format", "{{.Names}}"])
        if arg not in names.split():
            return 2, "no such container on this host"
        if check == "container_logs" and SENSITIVE.search(arg):
            return 2, "logs of '%s' are not available to the assistant (may contain credentials); use container_state instead" % arg
    rc, out = _run(build(arg))
    out = redact(out)
    if len(out) > MAX_OUT:
        out = out[:MAX_OUT] + "\n...[truncated]"
    return rc, out
