import contextlib
import io
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import generate_skia_package as package


class WindowsWglTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skia-wgl-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.relative = Path("src/gpu/ganesh/gl/win/GrGLMakeWinInterface.cpp")
        self.source = self.root / self.relative
        self.source.parent.mkdir(parents=True)
        self.fixture = Path(__file__).parent / "fixtures/GrGLMakeWinInterface.cpp"
        shutil.copy2(self.fixture, self.source)
        subprocess.run(["git", "init", "--quiet", str(self.root)], check=True)
        subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=self.root, check=True)
        patch = Path(package.get_script_dir()) / "patches/0004-disable-unavailable-arm64-wgl.patch"
        self.enterContext(mock.patch.object(package, "get_source_patch_paths", return_value=[str(patch)]))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_architecture_patch_restores_source_after_packaging(self):
        with package.applied_source_patches(str(self.root), "windows", "gpu"):
            self.assertIn("#if defined(_M_ARM64)", self.source.read_text())
        self.assertEqual(self.source.read_bytes(), self.fixture.read_bytes())

    @unittest.skipUnless(platform.system() == "Windows", "Requires the Windows SDK")
    def test_arm64_factory_has_no_wgl_imports_and_x64_retains_them(self):
        headers = Path(package.get_script_dir()) / "build/skia"
        compiler = Path("C:/Program Files/LLVM/bin/clang-cl.exe")
        if not (headers / "include/core/SkTypes.h").is_file() or not compiler.is_file():
            self.skipTest("Prepare Skia sources and install LLVM to run the native object test")
        with package.applied_source_patches(str(self.root), "windows", "gpu"):
            for cpu, triple in (("arm64", "aarch64-pc-windows-msvc"), ("x64", "x86_64-pc-windows-msvc")):
                target = self.root / (cpu + ".obj")
                defines = ["/D" + definition for definition in package.get_interface_compile_definitions("windows", "gpu").split(";")]
                result = subprocess.run([str(compiler), "--target=" + triple, "/nologo", "/c", "/std:c++20",
                                         "/DSKIA_IMPLEMENTATION=1", *defines, "/I" + str(headers), str(self.source),
                                         "/Fo" + str(target)], capture_output=True, text=True, errors="replace")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                symbols = subprocess.check_output(["dumpbin.exe", "/nologo", "/symbols", str(target)],
                                                  text=True, errors="replace").lower()
                if cpu == "arm64":
                    self.assertNotIn("wglgetcurrentcontext", symbols)
                    self.assertNotIn("wglgetprocaddress", symbols)
                else:
                    self.assertIn("wglgetcurrentcontext", symbols)
                    self.assertIn("wglgetprocaddress", symbols)
