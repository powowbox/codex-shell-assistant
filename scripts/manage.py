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


def uninstall(zshrc, data, zsh, restore_legacy=False, legacy_dir=None):
    original = zshrc.read_text() if zshrc.exists() else ""
    managed = block_span(original, START, END)
    legacy = block_span(original, LEGACY_START, LEGACY_END)
    manifest = load_manifest(data) if data.exists() else {}
    if manifest and manifest.get("zshrc") != str(zshrc):
        raise RuntimeError("This installation belongs to a different .zshrc.")
    if managed and original[managed[0]:managed[1]] != snippet(data):
        raise RuntimeError("The managed source block has been edited; refusing to remove it.")
    if legacy:
        block = original[legacy[0]:legacy[1]]
        if "_codex_fix_explain" not in block or "ask()" not in block:
            raise RuntimeError("Unrecognized legacy block; refusing to remove it.")
    if restore_legacy and managed and legacy:
        raise RuntimeError("Both shell blocks exist; cannot safely restore the legacy block.")

    reader = None
    if not restore_legacy and legacy_dir is not None:
        candidate = legacy_dir / "iterm2_last_output.py"
        if candidate.exists():
            if candidate.is_symlink() or legacy_dir.is_symlink():
                raise RuntimeError("The legacy reader is a symlink; refusing to remove it.")
            text = candidate.read_text()
            if not all(marker in text for marker in (
                    "def read_output(", "iterm2.async_list_prompts", "def is_fix(")):
                raise RuntimeError("Unrecognized legacy reader; refusing to remove it.")
            reader = candidate

    new_text = original
    for region, replacement in sorted(
            [(managed, manifest.get("legacy_block", "") if restore_legacy else ""),
             (legacy, original[legacy[0]:legacy[1]] if restore_legacy and legacy else "")],
            key=lambda item: item[0][0] if item[0] else -1, reverse=True):
        if region:
            start, end = region
            new_text = new_text[:start] + replacement + new_text[end:]
    validate_text(new_text, zsh)
    if new_text != original:
        backup = backup_zshrc(zshrc)
        atomic_text(zshrc, new_text)
        print(f"Shell backup: {backup}")
    if reader:
        # Move, rather than destroy, the previous standalone reader.
        backup = reader.with_name(reader.name + ".backup-uninstalled-" + uuid.uuid4().hex[:10])
        reader.rename(backup)
        print(f"Legacy reader disabled; backup: {backup}")
    if data.exists():
        shutil.rmtree(data)
    if not managed and not legacy and not manifest and not reader:
        print("Already uninstalled.")
    else:
        print("Uninstalled. Close this shell and open a new one to unload existing functions.")
    if restore_legacy and manifest.get("legacy_block"):
        print("Your previous legacy ask/fix block was restored by request.")
    print("Codex credentials, iTerm2 settings, unrelated functions, and backups were left in place.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--home", type=Path, default=Path.home(), help="Home directory (also useful for isolated testing)")
    parser.add_argument("--zshrc", type=Path, help="Target shell config; defaults to HOME/.zshrc")
    parser.add_argument("--data-dir", type=Path, help="Installation directory; defaults to HOME/.local/share/codex-shell-assistant")
    parser.add_argument("--python", default=sys.executable, help="Python 3.9+ interpreter for the isolated environment")
    parser.add_argument("--restore-legacy", action="store_true", help="On uninstall only: restore the previous inline setup instead of removing it")
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
            uninstall(zshrc, data, zsh, args.restore_legacy,
                      args.home.expanduser().absolute() / ".local/share/codex-shell")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
