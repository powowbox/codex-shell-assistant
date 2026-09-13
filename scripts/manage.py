#!/usr/bin/env python3
"""Install/uninstall without modifying shell plugins or Codex configuration."""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
START = "# >>> codex-shell-assistant >>>"
END = "# <<< codex-shell-assistant <<<"
LEGACY_START = "# --- Codex shell assistant ---"
LEGACY_END = "# end codex block"
MANIFEST = ".codex-shell-assistant.json"


def block_span(text, start, end):
    lines = text.splitlines(keepends=True)
    starts, ends, position = [], [], 0
    for line in lines:
        if line.rstrip("\r\n") == start:
            starts.append(position)
        if line.rstrip("\r\n") == end:
            ends.append(position + len(line))
        position += len(line)
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise RuntimeError("Duplicate or incomplete shell block; refusing to edit .zshrc.")
    return starts[0], ends[0]


def load_manifest(data):
    path = data / MANIFEST
    if not path.is_file():
        raise RuntimeError(f"Not a managed installation: {data}")
    manifest = json.loads(path.read_text())
    if manifest.get("package") != "codex-shell-assistant":
        raise RuntimeError("Installation ownership check failed.")
    return manifest


def snippet(data):
    quoted = shlex.quote(str(data))
    return (f"{START}\n"
            f"typeset -g CODEX_SHELL_DIR={quoted}\n"
            '[[ -r "$CODEX_SHELL_DIR/codex-shell.zsh" ]] && source "$CODEX_SHELL_DIR/codex-shell.zsh"\n'
            f"{END}\n")


def install_text(original, data):
    managed = block_span(original, START, END)
    legacy = block_span(original, LEGACY_START, LEGACY_END)
    if managed and legacy:
        raise RuntimeError("Both legacy and managed shell blocks exist; resolve the duplicate first.")
    region = managed or legacy
    if legacy:
        old_block = original[legacy[0]:legacy[1]]
        if "_codex_fix_explain" not in old_block or "ask()" not in old_block:
            raise RuntimeError("Unrecognized legacy block; refusing to replace it.")
    else:
        old_block = ""
    if region:
        a, b = region
        return original[:a] + snippet(data) + original[b:], old_block
    separator = "" if not original or original.endswith("\n") else "\n"
    return original + separator + snippet(data), ""


def validate_text(text, zsh):
    result = subprocess.run([zsh, "-n"], input=text, text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Shell syntax check failed:\n" + result.stderr)


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        temporary.chmod((path.stat().st_mode & 0o777) if path.exists() else 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def backup_zshrc(path):
    if not path.exists():
        return None
    backup = path.with_name(path.name + ".backup-codex-shell-" + uuid.uuid4().hex[:10])
    shutil.copy2(path, backup)
    return backup


def install(zshrc, data, python, zsh):
    original = zshrc.read_text() if zshrc.exists() else ""
    old_manifest = load_manifest(data) if data.exists() else None
    if old_manifest and old_manifest.get("zshrc") != str(zshrc):
        raise RuntimeError("This installation belongs to a different .zshrc.")
    new_text, legacy = install_text(original, data)
    validate_text(new_text, zsh)
    codex = shutil.which(os.environ.get("CODEX_SHELL_BIN", "codex"))
    if not codex:
        raise RuntimeError("Codex CLI was not found. Install it and run codex login first.")
    help_result = subprocess.run([codex, "exec", "--help"], capture_output=True, text=True)
    for option in ("--ignore-user-config", "--ephemeral", "--output-last-message"):
        if help_result.returncode or option not in help_result.stdout:
            raise RuntimeError("Codex CLI is missing required options; update it before installing.")

    data.parent.mkdir(parents=True, exist_ok=True)
    previous = data.with_name(data.name + ".previous-" + uuid.uuid4().hex[:10])
    if old_manifest:
        data.rename(previous)
    backup = None
    try:
        data.mkdir(mode=0o700)
        for name in ("codex-shell.zsh", "iterm2_last_output.py"):
            shutil.copy2(ROOT / "src" / name, data / name)
        subprocess.run([python, "-m", "venv", str(data / "venv")], check=True)
        executable = str(data / "venv" / "bin" / "python3")
        subprocess.run([executable, "-m", "pip", "install", "--disable-pip-version-check",
                        "-r", str(ROOT / "requirements.txt")], check=True)
        subprocess.run([executable, "-c", "import iterm2; assert hasattr(iterm2, 'async_list_prompts')"], check=True)
        # Never overwrite edits made while dependencies were installing.
        current = zshrc.read_text() if zshrc.exists() else ""
        if current != original:
            raise RuntimeError(".zshrc changed during installation; please retry.")
        manifest = {
            "package": "codex-shell-assistant", "version": 1,
            "zshrc": str(zshrc),
            "legacy_block": legacy or (old_manifest or {}).get("legacy_block", ""),
        }
        atomic_text(data / MANIFEST, json.dumps(manifest, indent=2) + "\n")
        backup = backup_zshrc(zshrc)
        atomic_text(zshrc, new_text)
    except BaseException:
        if data.exists():
            shutil.rmtree(data)
        if previous.exists():
            previous.rename(data)
        raise
    if previous.exists():
        shutil.rmtree(previous)
    print(f"Installed in {data}")
    if backup:
        print(f"Shell backup: {backup}")
    print(f"Reload with: source {shlex.quote(str(zshrc))}")
    print("For automatic fix output, enable iTerm2 Settings > General > Magic > Enable Python API.")
    print("Approve the reader script in iTerm2 when prompted. No OpenAI API key is needed.")


def uninstall(zshrc, data, zsh):
    original = zshrc.read_text() if zshrc.exists() else ""
    region = block_span(original, START, END)
    if not data.exists() and not region:
        print("Already uninstalled.")
        return
    manifest = load_manifest(data)
    if manifest.get("zshrc") != str(zshrc):
        raise RuntimeError("This installation belongs to a different .zshrc.")
    if region:
        a, b = region
        new_text = original[:a] + manifest.get("legacy_block", "") + original[b:]
        validate_text(new_text, zsh)
        backup = backup_zshrc(zshrc)
        atomic_text(zshrc, new_text)
        print(f"Shell backup: {backup}")
    shutil.rmtree(data)
    print("Uninstalled. Close this shell and open a new one to unload existing functions.")
    if manifest.get("legacy_block"):
        print("Your previous legacy ask/fix block was restored.")
    print("Codex credentials, iTerm2 settings, and shell backups were left in place.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--home", type=Path, default=Path.home(), help="Home directory (also useful for isolated testing)")
    parser.add_argument("--zshrc", type=Path, help="Target shell config; defaults to HOME/.zshrc")
    parser.add_argument("--data-dir", type=Path, help="Installation directory; defaults to HOME/.local/share/codex-shell-assistant")
    parser.add_argument("--python", default=sys.executable, help="Python 3.9+ interpreter for the isolated environment")
    args = parser.parse_args()
    if sys.version_info < (3, 9):
        parser.error("Python 3.9 or newer is required.")
    zshrc = (args.zshrc or args.home / ".zshrc").expanduser().absolute()
    data = (args.data_dir or args.home / ".local/share/codex-shell-assistant").expanduser().absolute()
    if zshrc.is_symlink() or data.is_symlink():
        parser.error("Pass the real target path explicitly instead of a symlink.")
    if data == args.home.absolute() or data == Path("/") or data in zshrc.parents or zshrc == data:
        parser.error("The installation directory must be separate from the shell configuration.")
    zsh = shutil.which("zsh")
    if not zsh:
        parser.error("zsh is required.")
    try:
        if args.action == "install":
            install(zshrc, data, args.python, zsh)
        else:
            uninstall(zshrc, data, zsh)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
