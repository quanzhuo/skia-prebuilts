import contextlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import generate_skia_package as package


class SourcePatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skia-package-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.build = self.root / "build"
        self.build.mkdir()
        self.stamp = self.build / package.BUILD_INPUTS_STAMP
        # Keep expected build/patch progress out of unittest output.
        output = contextlib.redirect_stdout(io.StringIO())
        errors = contextlib.redirect_stderr(io.StringIO())
        output.__enter__()
        errors.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        self.addCleanup(errors.__exit__, None, None, None)

    def test_pathops_is_included_on_every_platform(self):
        for system in ("windows", "linux", "darwin"):
            paths = package.get_source_patch_paths(system)
            self.assertIn("0002-include-pathops-in-skia.patch", [Path(p).name for p in paths])
            self.assertEqual(len(paths), 3 if system == "windows" else 1)
            self.assertTrue(all(Path(p).is_file() for p in paths))

    def fingerprint(self, patch_paths, revision="abc123"):
        with mock.patch.object(package, "get_source_patch_paths", return_value=patch_paths), \
             mock.patch.object(package.subprocess, "run", return_value=subprocess.CompletedProcess(
                 [], 0, stdout=revision + "\n")):
            return package.source_build_fingerprint(str(self.root), "linux")

    def test_fingerprint_detects_source_and_patch_changes(self):
        patch = self.root / "test.patch"
        patch.write_text("original\n", encoding="utf-8")
        original = self.fingerprint([str(patch)])
        self.assertNotEqual(original, self.fingerprint([str(patch)], "def456"))
        patch.write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(original, self.fingerprint([str(patch)]))
        self.assertNotEqual(original, self.fingerprint([]))

    def test_fingerprint_ignores_checkout_line_endings(self):
        patch = self.root / "test.patch"
        patch.write_bytes(b"line1\nline2\n")
        original = self.fingerprint([str(patch)])
        patch.write_bytes(b"line1\r\nline2\r\n")
        self.assertEqual(original, self.fingerprint([str(patch)]))

    def test_missing_patch_cannot_certify_old_binaries(self):
        with self.assertRaises(FileNotFoundError):
            self.fingerprint([str(self.root / "missing.patch")])

    def test_only_matching_success_stamp_is_current(self):
        self.assertFalse(package.build_source_is_current(str(self.build), "fingerprint"))
        self.stamp.write_text("", encoding="utf-8")
        self.assertFalse(package.build_source_is_current(str(self.build), "fingerprint"))
        self.stamp.write_text("fingerprint\n", encoding="utf-8")
        self.assertTrue(package.build_source_is_current(str(self.build), "fingerprint"))
        self.assertFalse(package.build_source_is_current(str(self.build), "changed"))

    @contextlib.contextmanager
    def mock_build(self, runner=None, lifecycle=None):
        with mock.patch.object(package.platform, "system", return_value="Linux"), \
             mock.patch.object(package, "source_build_fingerprint", return_value="fingerprint") as fingerprint, \
             mock.patch.object(package, "applied_source_patches",
                               side_effect=lifecycle or (lambda *_: contextlib.nullcontext())), \
             mock.patch.object(package, "prepare_args_gn_file"), \
             mock.patch.object(package.subprocess, "run", side_effect=runner) as run:
            yield fingerprint, run

    def test_success_stamp_written_only_after_patch_restoration(self):
        @contextlib.contextmanager
        def lifecycle(*_):
            self.assertEqual(self.stamp.read_text(), "")
            yield
            self.assertEqual(self.stamp.read_text(), "")

        with self.mock_build(lifecycle=lifecycle) as (_, run):
            package.build_skia(str(self.root), str(self.build), jobs=7)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0], ["ninja", "-C", str(self.build), "-j", "7", "skia"])
        self.assertEqual(self.stamp.read_text(), "fingerprint\n")

    def test_failed_gn_or_ninja_invalidates_previous_success(self):
        for failed_program in ("gn", "ninja"):
            self.stamp.write_text("fingerprint\n", encoding="utf-8")

            def runner(command, **_):
                if Path(command[0]).name == failed_program:
                    raise subprocess.CalledProcessError(1, command)

            with self.mock_build(runner=runner), self.assertRaises(RuntimeError):
                package.build_skia(str(self.root), str(self.build))
            self.assertEqual(self.stamp.read_text(), "")

    def test_automatic_jobs_are_forwarded_to_ninja(self):
        with self.mock_build() as (_, run), \
             mock.patch.object(package.os, "process_cpu_count", return_value=18, create=True):
            package.build_skia(str(self.root), str(self.build))
            self.assertEqual(run.call_args.args[0], ["ninja", "-C", str(self.build), "-j", "18", "skia"])

    def test_failed_patch_restoration_does_not_certify_build(self):
        @contextlib.contextmanager
        def lifecycle(*_):
            yield
            raise RuntimeError("restoration failed")

        with self.mock_build(lifecycle=lifecycle), self.assertRaisesRegex(RuntimeError, "restoration"):
            package.build_skia(str(self.root), str(self.build))
        self.assertEqual(self.stamp.read_text(), "")

    def test_changed_source_during_build_does_not_certify_build(self):
        with self.mock_build() as (fingerprint, _):
            fingerprint.side_effect = ["before", "after"]
            with self.assertRaisesRegex(RuntimeError, "发生变化"):
                package.build_skia(str(self.root), str(self.build))
        self.assertEqual(self.stamp.read_text(), "")

    def test_dirty_source_or_changed_toolchain_cannot_certify_build(self):
        with self.mock_build(), self.assertRaisesRegex(RuntimeError, "input changed"):
            package.build_skia(str(self.root), str(self.build),
                               source_validator=mock.Mock(side_effect=RuntimeError("input changed")))
        self.assertEqual(self.stamp.read_text(), "")

    def test_package_rebuilds_uncertified_binary_even_when_args_match(self):
        # Stop just before packaging: these are orchestration tests, not fake binaries to ship.
        for recorded in (None, "stale", "fingerprint"):
            if recorded is not None:
                self.stamp.write_text(recorded, encoding="utf-8")
            with mock.patch.object(package.platform, "system", return_value="Linux"), \
                 mock.patch.object(package, "find_binary_files", return_value={"so": "libskia.so"}), \
                 mock.patch.object(package, "build_args_gn_is_current", return_value=True), \
                 mock.patch.object(package, "source_build_fingerprint", return_value="fingerprint"), \
                 mock.patch.object(package, "validate_source_version"), \
                 mock.patch.object(package, "build_skia") as build, \
                 mock.patch.object(package.tempfile, "TemporaryDirectory", side_effect=RuntimeError("stop packaging")):
                with self.assertRaisesRegex(RuntimeError, "stop packaging"):
                    package.create_package(str(self.root), str(self.build), "m152-abcdef0123-r1", jobs=9, output_dir=str(self.root / "dist"))
                self.assertEqual(build.call_count, 0 if recorded == "fingerprint" else 1)
                if recorded != "fingerprint":
                    build.assert_called_once_with(str(self.root), str(self.build), None, jobs=9, profile="gpu", source_validator=None)


class PatchLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skia-patch-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        self.build_file = self.root / "BUILD.gn"
        self.original = '''skia_component("skia") {
  public_configs = [ ":skia_public" ]
  configs = skia_library_configs

  public_deps = [
    ":core",
    ":fontmgr_FontConfigInterface",
    ":fontmgr_android",
  ]
}
'''
        self.build_file.write_text(self.original, encoding="utf-8")
        self.patch = package.get_source_patch_paths("linux")[0]

    def test_patch_is_applied_and_restored_on_success_and_exception(self):
        for should_fail in (False, True):
            try:
                with package.applied_source_patches(str(self.root), "linux"):
                    self.assertIn('":pathops"', self.build_file.read_text())
                    if should_fail:
                        raise RuntimeError("build failed")
            except RuntimeError:
                if not should_fail:
                    raise
            self.assertEqual(self.build_file.read_text(), self.original)

    def test_preexisting_patch_is_not_reverted(self):
        subprocess.run(["git", "apply", self.patch], cwd=self.root, check=True)
        patched = self.build_file.read_text()
        with package.applied_source_patches(str(self.root), "linux"):
            self.assertEqual(self.build_file.read_text(), patched)
        self.assertEqual(self.build_file.read_text(), patched)

    def test_later_patch_failure_restores_only_our_earlier_patch(self):
        bad_patch = self.root / "bad.patch"
        bad_patch.write_text("not a patch\n", encoding="utf-8")
        with mock.patch.object(package, "get_source_patch_paths", return_value=[self.patch, str(bad_patch)]):
            with self.assertRaisesRegex(RuntimeError, "无法应用"):
                with package.applied_source_patches(str(self.root), "linux"):
                    self.fail("should not enter build")
        self.assertEqual(self.build_file.read_text(), self.original)

    def test_failed_restore_still_attempts_remaining_patches(self):
        other_file = self.root / "other.txt"
        other_file.write_text("original\n", encoding="utf-8", newline="\n")
        other_patch = self.root / "other.patch"
        other_patch.write_text(
            "diff --git a/other.txt b/other.txt\n"
            "--- a/other.txt\n+++ b/other.txt\n"
            "@@ -1 +1 @@\n-original\n+patched\n", encoding="utf-8", newline="\n")
        with mock.patch.object(package, "get_source_patch_paths", return_value=[self.patch, str(other_patch)]):
            with self.assertRaises(Exception) as error:
                with package.applied_source_patches(str(self.root), "linux"):
                    # Simulate an external edit which prevents this patch's reversal.
                    other_file.write_text("external edit\n", encoding="utf-8")
        self.assertEqual(self.build_file.read_text(), self.original)
        self.assertEqual(other_file.read_text(), "external edit\n")
        self.assertIsInstance(error.exception, RuntimeError)
        self.assertIn(str(other_patch), str(error.exception))


if __name__ == "__main__":
    unittest.main()
