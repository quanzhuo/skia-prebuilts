import argparse
import contextlib
import io
import unittest
from unittest import mock

import generate_skia_package as package


class BuildJobsTests(unittest.TestCase):
    def test_explicit_jobs_override_detection(self):
        with mock.patch.object(package.os, "process_cpu_count", create=True) as count:
            self.assertEqual(package.resolve_jobs(7), 7)
            count.assert_not_called()

    def test_default_uses_process_cpu_count(self):
        with mock.patch.object(package.os, "process_cpu_count", return_value=6, create=True), \
             mock.patch.object(package.os, "cpu_count", return_value=64):
            self.assertEqual(package.resolve_jobs(), 6)

    def test_old_python_respects_affinity(self):
        with mock.patch.object(package.os, "process_cpu_count", None, create=True), \
             mock.patch.object(package.os, "sched_getaffinity", return_value={2, 3, 4}, create=True), \
             mock.patch.object(package.os, "cpu_count", return_value=64):
            self.assertEqual(package.resolve_jobs(), 3)

    def test_unknown_process_count_falls_back(self):
        with mock.patch.object(package.os, "process_cpu_count", return_value=None, create=True), \
             mock.patch.object(package.os, "sched_getaffinity", None, create=True), \
             mock.patch.object(package.os, "cpu_count", return_value=32):
            self.assertEqual(package.resolve_jobs(), 32)

    def test_unavailable_affinity_falls_back(self):
        for failure in (OSError, NotImplementedError):
            with mock.patch.object(package.os, "process_cpu_count", None, create=True), \
                 mock.patch.object(package.os, "sched_getaffinity", side_effect=failure, create=True), \
                 mock.patch.object(package.os, "cpu_count", return_value=16):
                self.assertEqual(package.resolve_jobs(), 16)

    def test_unknown_cpu_count_uses_one(self):
        with mock.patch.object(package.os, "process_cpu_count", None, create=True), \
             mock.patch.object(package.os, "sched_getaffinity", None, create=True), \
             mock.patch.object(package.os, "cpu_count", return_value=None):
            self.assertEqual(package.resolve_jobs(), 1)

    def test_invalid_explicit_jobs_are_rejected(self):
        for value in (0, -1, True, 1.5, "8"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                package.resolve_jobs(value)
        for value in ("0", "-2", "1.5", "auto", ""):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                package.positive_jobs(value)
        self.assertEqual(package.positive_jobs("24"), 24)

    def test_cli_forwards_explicit_jobs_and_preserves_auto_default(self):
        for arguments, expected in (([], None), (["--jobs", "20"], 20), (["-j", "3"], 3)):
            with mock.patch("sys.argv", ["generate_skia_package.py", ".", *arguments]), \
                 mock.patch.object(package, "create_packages", return_value=["package.zip"]) as create, \
                 contextlib.redirect_stdout(io.StringIO()):
                package.main()
                self.assertEqual(create.call_args.kwargs["jobs"], expected)

    def test_invalid_cli_jobs_fail_before_packaging(self):
        with mock.patch("sys.argv", ["generate_skia_package.py", ".", "--jobs", "0"]), \
             mock.patch.object(package, "create_packages") as create, \
             contextlib.redirect_stderr(io.StringIO()), \
             self.assertRaises(SystemExit) as error:
            package.main()
        self.assertEqual(error.exception.code, 2)
        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
