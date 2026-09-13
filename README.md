# Codex Shell Assistant

Two small zsh helpers for macOS:

- **`ask`** generates a shell command and places it in your next input line. Edit it or press Return to execute it. It is never executed automatically.
- **`fix`** explains the previous command's failure using its command text, working directory, exit status, and output read from the originating iTerm2 pane.

Both helpers use the **Codex CLI and its existing sign-in**, display a spinner while waiting, and respond in **English**. The iTerm2 Python API is local; the iTerm2 AI plugin and an OpenAI API key are not needed for this setup.

## Requirements

- macOS with zsh (Oh My Zsh is optional).
- Python **3.9+** with `venv` and pip.
- A signed-in Codex CLI on `PATH`. Run `codex login` if needed.
- A recent Codex CLI with `--ignore-user-config`, `--ephemeral`, `--output-last-message`, and the feature flags used in `src/codex-shell.zsh`.
- For automatic output capture: **iTerm2**, Shell Integration, and its Python API enabled.

The original setup used iTerm2 **3.7.1**, Python **3.9**, the `iterm2` Python package **2.23**, and Codex CLI **0.154.0**. Tests use real protocol messages to cover the SDK's incorrect `PromptState.FINISHED` mapping.

## Install

```sh
git clone git@github.com:powowbox/codex-shell-assistant.git
cd codex-shell-assistant
./install.sh
source ~/.zshrc
```

The clone command assumes you have published the repository at that address. Installing from an existing local checkout works with just `./install.sh`.

The installer:

1. Checks shell syntax and required Codex options.
2. Copies the runtime into `~/.local/share/codex-shell-assistant`.
3. Creates an isolated Python environment and installs `requirements.txt` from PyPI.
4. Backs up `.zshrc` and adds a short, marked `source` block.

It preserves aliases, completion setup, plugins, and other hooks. Re-running it updates the same installation without adding duplicate source blocks. It does not edit Codex authentication/configuration, iTerm2 preferences, or SSH keys.

**Migrating the original inline setup:** the installer recognizes the `# --- Codex shell assistant ---` / `# end codex block` block created during development. It replaces only that block and records it for restoration on uninstall. The older `~/.local/share/codex-shell` reader is left in place so that restoration remains possible. Unrecognized or duplicate blocks are refused.

To select a Python interpreter or alternative paths:

```sh
PYTHON=/opt/homebrew/bin/python3 ./install.sh
./install.sh --zshrc /path/to/actual/zshrc --data-dir /path/to/runtime
```

The wrapper's `PYTHON` selects the installer interpreter; `--python /path/to/python3` selects the runtime interpreter. For symlinked dotfiles, pass their real target with `--zshrc`. Use the same custom paths when uninstalling.

## Enable automatic output capture

In iTerm2, enable **Settings → General → Magic → Enable Python API**. This is separate from iTerm2's AI features. Ensure **Shell Integration** is installed/loaded in your shell.

On the first `fix`, approve the Python reader if iTerm2 requests access. Reading is performed only when you call `fix`; no daemon or continuous output log is installed.

## Usage

```zsh
ask 'Show the 10 largest Docker images'
# A command appears in the next input line. Review it, then press Return.

docker compose up
fix

# Supply context manually, including outside iTerm2:
fix 'no configuration file provided: not found'
```

Type `fix` on its **own line**, immediately after the command to diagnose. Repeated calls to `fix` preserve the original command and status. Running another ordinary command changes the target. The exit code is the shell's overall command status, not every individual pipeline status.

`ask` prints the suggestion normally when used non-interactively or with redirected output. It rejects empty, multiline, and control-character-containing responses. This is formatting validation, not a guarantee that a proposed command is correct or safe: review it before Return.

### Configuration

Set these before the installed source block in `.zshrc`, or in your current shell:

```zsh
CODEX_SHELL_MODEL='gpt-6-astra'
CODEX_SHELL_BIN='codex'  # executable name or absolute path, not arguments
```

Choose a model available to your Codex account. The configured default preserves the original working setup; the helper requests low reasoning effort.

`CODEX_SHELL_DIR` points to the installed runtime. `CODEX_SHELL_PYTHON` defaults to its virtual environment and may be overridden with an interpreter that has the required `iterm2` module installed.

## What reaches Codex

For `fix`, the helper sends the command text, directory, exit status, and either manually supplied context or the displayed output of the matching completed command. The terminal output combines stdout and stderr. Capture is limited to the last **200 terminal rows** and **16,000 characters**, with an explicit truncation label.

The reader checks the originating pane and command identity, skips `fix` invocations, and refuses unavailable, mismatched, or still-running commands. Output that has left scrollback cannot be recovered. Full-screen applications and complex prompt customizations may not produce useful command ranges. Literal paths, command arguments, and quoted output retain their original language.

Each Codex invocation uses a temporary neutral directory, ignores user configuration, suppresses project instructions, and disables shell tools and several integrations. These switches reduce unwanted activity; they are not a general sandbox guarantee for every future CLI version. Only the final response is shown on success; failures show a short diagnostic. Temporary response files are removed on exit or interruption. `--ephemeral` is a CLI session-storage option, not a provider data-retention promise.

## Uninstall

```sh
./uninstall.sh
```

This backs up `.zshrc`, removes the managed source block, and deletes only the runtime directory bearing this package's ownership manifest. If installation migrated a legacy block, that exact block is restored instead. Later edits elsewhere in `.zshrc` are preserved.

Open a **new shell** afterward; functions already loaded into a running shell remain in memory. Shell backups, Codex credentials, iTerm2 settings, and any pre-existing legacy reader remain untouched. An unowned installation directory will never be deleted.

## Tests

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
zsh -n src/codex-shell.zsh
bash -n install.sh uninstall.sh
```

Tests cover fresh installation, reinstall, legacy restoration, preserving unrelated edits, dependency-failure rollback, installation ownership, protocol state handling, pane/command matching, expired scrollback, wrapping, truncation, and concise errors. Installer tests mock dependency downloads and Codex; reader tests mock the live API. They do not run generated commands or use a model. GitHub Actions runs these checks on macOS.

For a live check, reload the shell, run a harmless failing command such as `ls /a-path-that-does-not-exist`, then `fix`. Confirm that the response refers to that actual error. Try `ask 'Print hello'` and verify the suggestion waits for Return.

## Publish over SSH

Create an **empty** GitHub repository named `codex-shell-assistant` under your account (do not initialize it with a README). Then, if needed:

```sh
git remote add origin git@github.com:YOUR_USERNAME/codex-shell-assistant.git
git push -u origin main
```

If `origin` already exists, inspect it with `git remote -v` instead of adding it again. SSH authentication can be checked with `ssh -T git@github.com`; GitHub's successful greeting normally exits with status 1 because shell access is not provided.

Private keys, authentication files, local terminal output, and the user's `.zshrc` do not belong in this repository. No license has been selected; add one before offering a license grant to others.

## References

- [iTerm2 Python API](https://iterm2.com/python-api/)
- [iTerm2 Shell Integration](https://iterm2.com/documentation-shell-integration.html)
- [Codex CLI reference](https://developers.openai.com/codex/cli/reference)
