"""``ai-quotas login <provider> --name <name>``: make an owned login.

Runs the vendor's own CLI login with its home pointed at a fresh staging dir,
so the new grant never touches the CLI login you use day to day:

    claude  CLAUDE_CONFIG_DIR=<dir> claude auth login --claudeai
    codex   CODEX_HOME=<dir> codex login --device-auth -c cli_auth_credentials_store="file"
    grok    GROK_HOME=<dir>  grok login --device-auth --leader-socket <dir>/leader.sock

On macOS Claude keeps that login in the Keychain item
``Claude Code-credentials-<sha256(dir)[:8]>``; it is moved into
``<dir>/.credentials.json`` and the item is deleted, so the sampler is the
only holder. Then ``login.json`` marks the dir owned and it is renamed into
``<logins_dir>/<provider>-<name>``. Re-running replaces the credentials of
an existing owned login (the fix for "needs re-login").

``--move-to HOST[:DIR]`` streams the finished dir over ssh into DIR (default
``~/.local/share/ai-quotas/logins`` on that host) and deletes the local copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from ai_quotas import config, owned
from ai_quotas.accounts import normalize_account

KEYCHAIN_PREFIX = "Claude Code-credentials"
DEFAULT_REMOTE_DIR = ".local/share/ai-quotas/logins"


def _binary(provider: str) -> str | None:
    env = os.environ.get(f"AI_QUOTAS_{provider.upper()}_BIN")
    if env:
        return env
    found = shutil.which(provider)
    if found:
        return found
    for candidate in (Path.home() / ".local" / "bin" / provider, Path.home() / ".grok" / "bin" / provider):
        if candidate.is_file():
            return str(candidate)
    return None


def login_command(provider: str, staging: Path) -> tuple[list[str], dict[str, str]]:
    binary = _binary(provider)
    if not binary:
        raise owned.OwnedError(f"`{provider}` CLI not found (set AI_QUOTAS_{provider.upper()}_BIN)")
    env = dict(os.environ)
    for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "GROK_HOME"):
        env.pop(name, None)  # never inherit a caller's login home
    if provider == "claude":
        env["CLAUDE_CONFIG_DIR"] = str(staging)
        return [binary, "auth", "login", "--claudeai"], env
    if provider == "codex":
        env["CODEX_HOME"] = str(staging)
        return [binary, "login", "--device-auth", "-c", 'cli_auth_credentials_store="file"'], env
    env["GROK_HOME"] = str(staging)
    return [binary, "login", "--device-auth", "--leader-socket", str(staging / "leader.sock")], env


def _keychain_services(staging: Path) -> list[str]:
    names = {str(staging), str(staging.resolve())}
    return [f"{KEYCHAIN_PREFIX}-{hashlib.sha256(n.encode()).hexdigest()[:8]}" for n in sorted(names)]


def _move_claude_keychain(staging: Path) -> bool:
    """Keychain item for this config dir → ``.credentials.json``; item deleted."""
    if sys.platform != "darwin" or not shutil.which("security"):
        return False
    for service in _keychain_services(staging):
        try:
            got = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                                 capture_output=True, text=True, timeout=10, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if got.returncode != 0 or not got.stdout.strip():
            continue
        try:
            data = json.loads(got.stdout.strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not isinstance(data.get("claudeAiOauth"), dict):
            continue
        owned.atomic_write_json(staging / owned.CREDENTIALS["claude"], data)
        subprocess.run(["security", "delete-generic-password", "-s", service],
                       capture_output=True, timeout=10, check=False)
        return True
    return False


def _claude_account(staging: Path, creds: dict[str, Any]) -> str | None:
    for name, key in ((".claude.json", "oauthAccount"), ("oauth-account.json", None)):
        try:
            raw = json.loads((staging / name).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        block = raw.get(key) if key else raw
        if isinstance(block, dict):
            email = normalize_account(block.get("emailAddress") or block.get("email"))
            if email:
                return email
    from ai_quotas.adapters.claude import _profile_email

    oauth = creds.get("claudeAiOauth") if isinstance(creds.get("claudeAiOauth"), dict) else {}
    token = oauth.get("accessToken")
    return _profile_email(str(token)) if token else None


def account_of(provider: str, staging: Path) -> str | None:
    probe = owned.Login(provider=provider, name="staging", path=staging)
    try:
        creds = owned.read_json(probe.creds_path)
    except (OSError, ValueError, owned.OwnedError):
        return None
    if provider == "claude":
        return _claude_account(staging, creds)
    return owned.creds_account(probe, creds)


def _secure(path: Path) -> None:
    """Owner-only modes. Symlinks are skipped: chmod would follow them out of
    the login dir."""
    for item in [path, *path.rglob("*")]:
        if item.is_symlink():
            continue
        try:
            os.chmod(item, 0o700 if item.is_dir() else 0o600)
        except OSError:
            pass


def _parse_move_to(dest: str) -> tuple[str, str]:
    host, sep, remote = dest.partition(":")
    if not host:
        raise owned.OwnedError(f"--move-to needs HOST or HOST:DIR, got {dest!r}")
    return host, (remote if sep and remote else DEFAULT_REMOTE_DIR)


def move_to(login: owned.Login, dest: str) -> str:
    """tar | ssh into the remote logins dir; local copy removed on success."""
    host, remote_root = _parse_move_to(dest)
    root = shlex.quote(remote_root)
    name = shlex.quote(login.path.name)
    # New files take the owner of the remote logins dir (a container user).
    script = (
        f"set -e; umask 077; mkdir -p {root}; rm -rf {root}/.incoming-{name}; "
        f"mkdir {root}/.incoming-{name}; tar -C {root}/.incoming-{name} -xf - --no-same-owner; "
        f"rm -rf {root}/{name}; mv {root}/.incoming-{name}/{name} {root}/{name}; "
        f"rmdir {root}/.incoming-{name}; "
        f"chown -R \"$(stat -c %u:%g {root} 2>/dev/null || stat -f %u:%g {root})\" {root}/{name} 2>/dev/null || true; "
        f"test -f {root}/{name}/{owned.MARKER}"
    )
    tar = subprocess.Popen(
        ["tar", "-C", str(login.path.parent), "--exclude", owned.LOCK, "-cf", "-", login.path.name],
        stdout=subprocess.PIPE,
    )
    try:
        ssh = subprocess.run([config.ssh_binary(), "-o", "BatchMode=yes", host, script], stdin=tar.stdout,
                             capture_output=True, text=True, timeout=120, check=False)
    finally:
        if tar.stdout:
            tar.stdout.close()
        tar.wait(timeout=30)
    if tar.returncode != 0 or ssh.returncode != 0:
        tail = (ssh.stderr or "").strip().splitlines()
        raise owned.OwnedError(f"move to {host} failed" + (f": {tail[-1][:160]}" if tail else ""))
    shutil.rmtree(login.path)
    return f"{host}:{remote_root}/{login.path.name}"


def run(
    provider: str,
    name: str,
    *,
    move: str | None = None,
    default: bool = False,
    root: Path | None = None,
) -> int:
    if provider not in owned.PROVIDERS:
        print(f"unknown provider {provider!r}; owned logins exist for: {', '.join(owned.PROVIDERS)}",
              file=sys.stderr)
        return 2
    if not owned.valid_name(name):
        print("name: lower-case letters, digits, '-', '_' or '.', up to 40 chars", file=sys.stderr)
        return 2
    base = owned.root_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    os.chmod(base, 0o700)
    target = owned.login_path(provider, name, base)
    if target.exists():
        try:
            owned.gate(target, base)
        except owned.NotOwned as exc:
            print(f"{target} exists and is not an owned login ({exc}); pick another --name",
                  file=sys.stderr)
            return 2
    staging = base / f".staging-{provider}-{name}-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(mode=0o700)
    try:
        try:
            cmd, env = login_command(provider, staging)
        except owned.OwnedError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Signing a NEW {provider} login in for the sampler. Your everyday "
              f"`{provider}` login is not touched.", flush=True)
        print(f"$ {' '.join(shlex.quote(c) for c in cmd)}", flush=True)
        proc = subprocess.run(cmd, env=env, check=False)
        if proc.returncode != 0:
            print(f"{provider} login exited {proc.returncode}; nothing saved", file=sys.stderr)
            return 1
        creds = staging / owned.CREDENTIALS[provider]
        if provider == "claude" and not creds.is_file():
            _move_claude_keychain(staging)
        if not creds.is_file() or creds.is_symlink():
            print(f"{provider} login finished but wrote no {creds.name} into {staging}; nothing saved",
                  file=sys.stderr)
            return 1
        account = account_of(provider, staging)
        owned.write_marker(staging, provider=provider, name=name, account=account, default=default)
        for leftover in ("leader.sock",):
            try:
                (staging / leftover).unlink()
            except OSError:
                pass
        _secure(staging)
        if target.exists():
            old = base / f".replaced-{target.name}-{os.getpid()}"
            # A sampler may be refreshing this login: swap only under its lock.
            with owned.locked(owned.Login(provider=provider, name=name, path=target)):
                target.rename(old)
                staging.rename(target)
            shutil.rmtree(old, ignore_errors=True)
        else:
            staging.rename(target)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    login = owned.gate(target, base)
    print(f"owned login {login.id}: {login.account or 'account unknown'} → {login.path}")
    if move:
        try:
            where = move_to(login, move)
        except owned.OwnedError as exc:
            print(f"{exc}. The login stays at {login.path}", file=sys.stderr)
            return 1
        print(f"moved to {where} (local copy removed)")
        return 0
    print(f"next: ai-quotas sampler --login {login.id} --once")
    return 0
