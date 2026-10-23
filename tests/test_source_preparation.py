from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tools.prepare_source import host_tool_command


class HostToolsTests(unittest.TestCase):
    def test_native_hosts_use_upstream_fetch_scripts_directly(self):
        for system, machine in (("Windows", "AMD64"), ("Linux", "aarch64"), ("Darwin", "arm64")):
            with mock.patch.object(platform, "system", return_value=system), \
                 mock.patch.object(platform, "machine", return_value=machine):
                self.assertEqual(host_tool_command("fetch-gn"), [sys.executable, "fetch-gn"])

    def test_windows_arm64_host_tool_override_is_confined_to_the_fetch_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            for tool in ("fetch-gn", "fetch-ninja"):
                script = Path(temporary) / tool
                script.write_text('import platform\nprint(platform.machine())\n')
                with self.subTest(tool=tool), \
                     mock.patch.object(platform, "system", return_value="Windows"), \
                     mock.patch.object(platform, "machine", return_value="ARM64"):
                    command = host_tool_command(script)
                    result = subprocess.check_output(command, text=True).strip()
                    self.assertEqual(result, "AMD64")
                    self.assertEqual(platform.machine(), "ARM64")
