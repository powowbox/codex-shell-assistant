import asyncio
import contextlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, patch

SPEC = importlib.util.spec_from_file_location("reader", Path(__file__).parents[1] / "src/iterm2_last_output.py")
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)


class Transaction:
    def __init__(self, *_args): pass
    async def __aenter__(self): pass
    async def __aexit__(self, *_args): pass


class Line:
    def __init__(self, text, hard=True):
        self.string, self.hard_eol = text, hard
    def string_at(self, column):
        return self.string[column]


class ReaderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        api = reader.iterm2.api_pb2
        self.response = api.ServerOriginatedMessage()
        p = self.response.get_prompt_response
        p.status = api.GetPromptResponse.OK
        p.command = "docker compose up"
        p.prompt_state = api.GetPromptResponse.FINISHED
        p.output_range.start.y = 10
        p.output_range.end.y = 11
        self.current = api.ServerOriginatedMessage()
        self.current.get_prompt_response.status = api.GetPromptResponse.OK
        self.current.get_prompt_response.command = "fix"
        self.current.get_prompt_response.prompt_state = api.GetPromptResponse.RUNNING
        self.session = NS(grid_size=NS(width=80),
            async_get_line_info=AsyncMock(return_value=NS(overflow=0)),
            async_get_contents=AsyncMock(return_value=[Line("no configuration file provided: not found")]))
        app = NS(get_session_by_id=lambda sid: self.session if sid == "original-pane" else None)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(reader.iterm2, "Transaction", Transaction))
        self.stack.enter_context(patch.object(reader.iterm2, "async_get_app", AsyncMock(return_value=app)))
        self.stack.enter_context(patch.object(reader.iterm2, "async_list_prompts", AsyncMock(return_value=["previous", "fix"])))
        async def get_prompt(_c, _sid, pid):
            return self.current if pid == "fix" else self.response
        self.stack.enter_context(patch.object(reader.iterm2.rpc, "async_get_prompt", get_prompt))

    async def test_real_protocol_finished_enum_and_ignore_fix(self):
        self.assertEqual(self.response.get_prompt_response.prompt_state, 2)
        text = await reader.read_output(None, "original-pane", "docker compose up")
        self.assertEqual(text, "no configuration file provided: not found\n")

    async def test_wrong_pane_command_and_running_rejected(self):
        for pane, command in [("other-pane", "docker compose up"), ("original-pane", "something else")]:
            with self.assertRaises(RuntimeError):
                await reader.read_output(None, pane, command)
        self.response.get_prompt_response.prompt_state = reader.iterm2.api_pb2.GetPromptResponse.RUNNING
        with self.assertRaises(RuntimeError):
            await reader.read_output(None, "original-pane", "docker compose up")

    async def test_expired_output_rejected(self):
        self.session.async_get_line_info.return_value = NS(overflow=12)
        with self.assertRaises(RuntimeError):
            await reader.read_output(None, "original-pane", "docker compose up")

    async def test_wrapping_and_truncation(self):
        self.response.get_prompt_response.output_range.end.y = 12
        self.session.async_get_contents.return_value = [Line("wrapped ", False), Line("line")]
        self.assertEqual(await reader.read_output(None, "original-pane", "docker compose up"), "wrapped line\n")
        self.response.get_prompt_response.output_range.end.y = 400
        text = await reader.read_output(None, "original-pane", "docker compose up")
        self.assertTrue(text.startswith("[Beginning truncated:"))
        self.session.async_get_contents.assert_awaited_with(200, 200)


class ErrorTests(unittest.TestCase):
    def test_callback_errors_are_english_without_traceback(self):
        err, out = io.StringIO(), io.StringIO()
        with patch.object(reader.sys, "argv", ["reader", "--session", "test", "--command", "test"]), \
                patch.object(reader.subprocess, "run", return_value=NS(returncode=0, stdout="1")), \
                patch.object(reader.iterm2, "run_until_complete", lambda cb: asyncio.run(cb(None))), \
                patch.object(reader, "read_output", AsyncMock(side_effect=RuntimeError("output missing"))), \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            self.assertEqual(reader.main(), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("Unable to read iTerm2 output: output missing", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


if __name__ == "__main__":
    unittest.main()
