import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("manage", Path(__file__).parents[1] / "scripts/manage.py")
manage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manage)


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="codex-shell-test-")
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.rc = self.home / ".zshrc"
        self.data = self.home / "data with spaces"
        self.zsh = shutil.which("zsh")
        self.real_run = subprocess.run

    def fake_run(self, args, **kwargs):
        if args[:2] == [self.zsh, "-n"]:
            return self.real_run(args, **kwargs)
        return subprocess.CompletedProcess(args, 0,
            stdout="--ignore-user-config --ephemeral --output-last-message", stderr="")

    def install(self):
        with patch.object(manage.shutil, "which", return_value="/mock/codex"), \
                patch.object(manage.subprocess, "run", side_effect=self.fake_run):
            manage.install(self.rc, self.data, "python3", self.zsh)

    def test_install_reinstall_uninstall_preserves_unrelated_edits(self):
        original = "# user config\nplugins=(git docker)\nalias rm=trash\n"
        self.rc.write_text(original)
        self.install()
        self.rc.write_text(self.rc.read_text() + "# added later\n")
        self.install()
        self.assertEqual(self.rc.read_text().count(manage.START), 1)
        manage.uninstall(self.rc, self.data, self.zsh)
        self.assertEqual(self.rc.read_text(), original + "# added later\n")
        self.assertFalse(self.data.exists())
        manage.uninstall(self.rc, self.data, self.zsh)

    def test_legacy_restored_only_when_requested_after_reinstall(self):
        original = "# before\n" + manage.LEGACY_START + "\n_codex_fix_explain() { :; }\nask() { :; }\n" + manage.LEGACY_END + "\n# after\n"
        self.rc.write_text(original)
        self.install()
        self.install()
        manage.uninstall(self.rc, self.data, self.zsh, restore_legacy=True)
        self.assertEqual(self.rc.read_text(), original)

    def test_original_inline_setup_removed_without_package_install(self):
        prefix = '# user configuration\nalias rm=trash\n'
        legacy = manage.LEGACY_START + '\n_codex_fix_explain() { :; }\nask() { :; }\n' + manage.LEGACY_END + '\n'
        self.rc.write_text(prefix + legacy + '# later edit\n')
        reader_dir = self.home / 'legacy-reader'
        reader_dir.mkdir()
        reader = reader_dir / 'iterm2_last_output.py'
        reader.write_text('def read_output():\n    iterm2.async_list_prompts()\ndef is_fix(): pass\n')
        (reader_dir / 'unrelated.txt').write_text('keep')
        manage.uninstall(self.rc, self.data, self.zsh, legacy_dir=reader_dir)
        self.assertEqual(self.rc.read_text(), prefix + '# later edit\n')
        self.assertFalse(reader.exists())
        self.assertEqual((reader_dir / 'unrelated.txt').read_text(), 'keep')
        self.assertEqual(len(list(reader_dir.glob('*.backup-uninstalled-*'))), 1)
        manage.uninstall(self.rc, self.data, self.zsh, legacy_dir=reader_dir)

    def test_migrated_setup_removed_by_default(self):
        legacy = manage.LEGACY_START + '\n_codex_fix_explain() { :; }\nask() { :; }\n' + manage.LEGACY_END + '\n'
        self.rc.write_text('# before\n' + legacy + '# after\n')
        self.install()
        manage.uninstall(self.rc, self.data, self.zsh)
        self.assertEqual(self.rc.read_text(), '# before\n# after\n')

    def test_missing_runtime_still_removes_owned_source_block(self):
        self.rc.write_text('# user\n' + manage.snippet(self.data))
        manage.uninstall(self.rc, self.data, self.zsh)
        self.assertEqual(self.rc.read_text(), '# user\n')

    def test_dependency_failure_rolls_back(self):
        self.rc.write_text("# user\n")
        self.install()
        before = self.rc.read_bytes()
        sentinel = self.data / "keep-me"
        sentinel.write_text("old installation")
        def fail(args, **kwargs):
            if "venv" in args:
                raise subprocess.CalledProcessError(1, args)
            return self.fake_run(args, **kwargs)
        with patch.object(manage.shutil, "which", return_value="/mock/codex"), \
                patch.object(manage.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                manage.install(self.rc, self.data, "python3", self.zsh)
        self.assertEqual(self.rc.read_bytes(), before)
        self.assertEqual(sentinel.read_text(), "old installation")

    def test_unowned_directory_and_malformed_block_refused(self):
        self.data.mkdir()
        (self.data / "personal").write_text("keep")
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertTrue((self.data / "personal").exists())
        for text in [manage.START + "\n", manage.START + "\n" + manage.END + "\n" + manage.START + "\n" + manage.END]:
            with self.assertRaises(RuntimeError):
                manage.install_text(text, self.data)


if __name__ == "__main__":
    unittest.main()
