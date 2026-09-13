#!/usr/bin/env python3
"""Read one completed command's display text from its originating iTerm2 pane.

No daemon, terminal writes, clipboard access, or persistent output log.
"""
import argparse
import asyncio
import contextlib
import os
import re
import shlex
import signal
import subprocess
import sys

import iterm2

MAX_LINES = 200
MAX_CHARS = 16000


def is_fix(command):
    try:
        words = shlex.split(command)
        return bool(words) and words[0] == "fix"
    except ValueError:
        return False


def clean_text(value):
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)


def crop_line(line, start, end, width):
    if start == 0 and end is None:
        return line.string
    pieces = []
    for column in range(start, width if end is None else end):
        try:
            pieces.append(line.string_at(column))
        except IndexError:
            break
    return "".join(pieces)


async def read_output(connection, session_id, expected):
    app = await iterm2.async_get_app(connection)
    session = app.get_session_by_id(session_id)
    if session is None:
        raise RuntimeError("The original iTerm2 pane is no longer available.")

    async with iterm2.Transaction(connection):
        ids = list(await iterm2.async_list_prompts(connection, session_id))
        prompt = None
        for prompt_id in reversed(ids[-32:]):
            # The installed SDK maps FINISHED to 3, but its wire protocol
            # correctly defines FINISHED=2. Read the protocol enum directly.
            response = await iterm2.rpc.async_get_prompt(connection, session_id, prompt_id)
            candidate = response.get_prompt_response
            if candidate.status == iterm2.api_pb2.GetPromptResponse.PROMPT_UNAVAILABLE:
                continue
            if candidate.status != iterm2.api_pb2.GetPromptResponse.OK:
                raise RuntimeError("Unable to read iTerm2 command information.")
            if not candidate.command.strip():
                continue
            if is_fix(candidate.command):
                continue
            if candidate.command.strip() != expected.strip():
                raise RuntimeError("The last iTerm2 command does not match the command recorded by fix.")
            if (not candidate.HasField("prompt_state") or
                    candidate.prompt_state != iterm2.api_pb2.GetPromptResponse.FINISHED):
                raise RuntimeError("iTerm2 has not marked this command as finished yet.")
            prompt = candidate
            break
        if prompt is None:
            raise RuntimeError("Command missing from iTerm2 history; check Shell Integration.")

        area = iterm2.util.CoordRange.from_proto(prompt.output_range)
        info = await session.async_get_line_info()
        end = area.end.y + (1 if area.end.x > 0 else 0)
        if area.start.y < 0 or end < area.start.y:
            raise RuntimeError("The output range is unavailable.")
        if end <= info.overflow and end > area.start.y:
            raise RuntimeError("The output is no longer in iTerm2 scrollback.")
        start = max(area.start.y, info.overflow, end - MAX_LINES)
        lines = await session.async_get_contents(start, max(0, end - start)) if end > start else []
        pieces = []
        for offset, line in enumerate(lines):
            y = start + offset
            left = area.start.x if y == area.start.y else 0
            right = area.end.x if y == area.end.y else None
            pieces.append(crop_line(line, left, right, session.grid_size.width))
            if line.hard_eol and right is None:
                pieces.append("\n")
        text = clean_text("".join(pieces))
        truncated = start > area.start.y or len(text) > MAX_CHARS
        text = text[-MAX_CHARS:]
        if truncated:
            text = "[Beginning truncated: last lines of output]\n" + text
        return text if text.strip() else "[Command finished without displayed output]"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", default=os.environ.get("ITERM_SESSION_ID", ""))
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    session_id = args.session.rsplit(":", 1)[-1]
    if not session_id:
        print("Automatic output capture is available only in iTerm2.", file=sys.stderr)
        return 1

    preference = subprocess.run(
        ["/usr/bin/defaults", "read", "com.googlecode.iterm2", "EnableAPIServer"],
        capture_output=True, text=True, timeout=3)
    if preference.returncode == 0 and preference.stdout.strip().lower() in ("0", "false", "no"):
        print("The iTerm2 Python API is disabled. Enable Settings → General → Magic → Enable Python API.", file=sys.stderr)
        return 1

    result = []
    failures = []

    def timeout(_signum, _frame):
        raise TimeoutError("iTerm2 is not responding; check Enable Python API and script authorization.")

    async def fetch(connection):
        # Catch inside the callback: the SDK otherwise prints a traceback
        # and calls sys.exit before our outer error handler can run.
        try:
            result.append(await asyncio.wait_for(read_output(connection, session_id, args.command), 10))
        except Exception as exc:
            failures.append(exc)

    signal.signal(signal.SIGALRM, timeout)
    signal.alarm(30)
    try:
        # Authentication diagnostics must never become model context.
        with contextlib.redirect_stdout(sys.stderr):
            iterm2.run_until_complete(fetch)
        if failures:
            raise failures[0]
        if not result:
            raise RuntimeError("No output received from iTerm2.")
        sys.stdout.write(result[0])
        return 0
    except Exception as exc:
        print("Unable to read iTerm2 output: " + str(exc), file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    sys.exit(main())
