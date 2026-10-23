"""Configure-only checks of generated packages; fixture binaries are never linked."""

from pathlib import Path
import platform
import subprocess
import tempfile
import unittest

import generate_skia_package as package


class CMakePackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skia-cmake-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sequence = 0
        self.packages = {profile: self.make_package(profile) for profile in package.PROFILES}

    def make_package(self, profile, version="m152-0873ec164a-r1"):
        system = platform.system().lower()
        root = self.root / f"{version}-{profile}"
        for _, destination in package.binary_layout(system, profile).values():
            path = root / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture only")
        package.write_cmake_package_files(str(root), version, system, None, profile, source_commit=package.parse_package_version(version)["commit"].ljust(40, "0"))
        return root / "lib" / "cmake" / "Skia"

    def configure(self, contents):
        self.sequence += 1
        source = self.root / f"source-{self.sequence}"
        source.mkdir()
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.21)\n"
            "project(SkiaPackageContract LANGUAGES NONE)\n"
            f"set(CMAKE_SYSTEM_PROCESSOR {platform.machine()})\n" + contents,
            encoding="utf-8",
        )
        result = subprocess.run(
            ["cmake", "-S", str(source), "-B", str(self.root / f"build-{self.sequence}"), "-G", "Ninja"],
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout + result.stderr

    @staticmethod
    def find(config, version="", required=True):
        return (f'set(Skia_DIR "{config.as_posix()}")\n'
                f'find_package(Skia {version} CONFIG {"REQUIRED" if required else "QUIET"} NO_DEFAULT_PATH)\n')

    def test_repeated_import_of_same_package_is_supported(self):
        for profile, config in self.packages.items():
            aliases = [config / ".." / "Skia"]
            if platform.system() == "Windows":
                aliases.append(Path(str(config).upper()))
            for alias in aliases:
                with self.subTest(profile=profile, alias=alias):
                    code = self.find(config) + self.find(alias)
                    code += f'if(NOT Skia_BUILD_PROFILE STREQUAL "{profile}")\n'
                    code += '  message(FATAL_ERROR "Metadata changed on repeated import")\nendif()\n'
                    status, output = self.configure(code)
                    self.assertEqual(status, 0, output)

    def test_conflicting_profiles_are_rejected_in_both_orders(self):
        for first, second in (("gpu", "cpu"), ("cpu", "gpu")):
            with self.subTest(first=first):
                status, output = self.configure(self.find(self.packages[first]) + self.find(self.packages[second]))
                self.assertNotEqual(status, 0, output)
                self.assertIn("Skia::Skia already exists", output)

    def test_conflicting_revisions_are_rejected(self):
        second = self.make_package("gpu", "m152-1234567890-r2")
        status, output = self.configure(self.find(self.packages["gpu"]) + self.find(second))
        self.assertNotEqual(status, 0, output)
        self.assertIn("Skia::Skia already exists", output)

    def test_foreign_target_is_not_silently_reused(self):
        status, output = self.configure('add_library(Skia::Skia INTERFACE IMPORTED)\n' + self.find(self.packages["gpu"]))
        self.assertNotEqual(status, 0, output)
        self.assertIn("Skia::Skia already exists", output)

    def test_version_constraints_match_exact_release_revision(self):
        cases = {
            "152": False,
            "152.1.0": True,
            "152.1.0 EXACT": True,
            "152.0.0 EXACT": False,
            "152.2.0": False,
            "151": False,
            "153": False,
            "152...152.1.0": True,
            "152...<152.1.0": False,
            "152.1.0...<153": True,
            "152.2...152.3": False,
        }
        for version, accepted in cases.items():
            with self.subTest(version=version):
                code = self.find(self.packages["cpu"], version, required=False)
                code += f'if({"NOT " if accepted else ""}Skia_FOUND)\n'
                code += f'  message(FATAL_ERROR "Unexpected match for {version}: ${{Skia_FOUND}}")\nendif()\n'
                status, output = self.configure(code)
                self.assertEqual(status, 0, output)

    def test_range_upper_bound_is_enforced(self):
        # Exercise the version template with a later packaging revision so the upper-bound
        # cases are valid, nonempty CMake ranges within the same major.
        version_file = self.packages["gpu"] / "SkiaConfigVersion.cmake"
        contents = version_file.read_text(encoding="utf-8").replace('"152.1.0"', '"152.2.0"')
        version_file.write_text(contents, encoding="utf-8")
        for version, accepted in (("152...152.1", False), ("152...<152.2", False),
                                  ("152...152.2", True), ("152...<152.3", True)):
            with self.subTest(version=version):
                code = self.find(self.packages["gpu"], version, required=False)
                code += f'if({"NOT " if accepted else ""}Skia_FOUND)\n'
                code += f'  message(FATAL_ERROR "Unexpected match for {version}")\nendif()\n'
                status, output = self.configure(code)
                self.assertEqual(status, 0, output)

    def test_version_file_clears_previous_exact_result(self):
        version_file = self.packages["gpu"] / "SkiaConfigVersion.cmake"
        code = f'''
set(PACKAGE_FIND_VERSION 152.1.0)
set(PACKAGE_FIND_VERSION_MAJOR 152)
include("{version_file.as_posix()}")
if(NOT PACKAGE_VERSION_EXACT)
    message(FATAL_ERROR "Expected an exact match")
endif()
set(PACKAGE_FIND_VERSION 152.2.0)
include("{version_file.as_posix()}")
if(PACKAGE_VERSION_EXACT OR PACKAGE_VERSION_COMPATIBLE)
    message(FATAL_ERROR "Stale version result escaped from an earlier check")
endif()
'''
        status, output = self.configure(code)
        self.assertEqual(status, 0, output)

    def test_missing_binary_is_rejected(self):
        system = platform.system().lower()
        _, destination = next(iter(package.binary_layout(system, "cpu").values()))
        (self.packages["cpu"].parents[2] / destination).unlink()
        status, output = self.configure(self.find(self.packages["cpu"]))
        self.assertNotEqual(status, 0, output)
        self.assertIn("Skia prebuilt package is incomplete", output)

    def test_wrong_architecture_is_rejected_before_importing_targets(self):
        other = "arm64" if package.normalize_cpu_name(platform.machine()) == "x64" else "x64"
        code = f'set(CMAKE_SYSTEM_PROCESSOR {other})\n' + self.find(self.packages["cpu"], required=False)
        code += 'if(Skia_FOUND OR TARGET Skia::Skia)\n  message(FATAL_ERROR "Wrong architecture accepted")\nendif()\n'
        status, output = self.configure(code)
        self.assertEqual(status, 0, output)

    def test_32_bit_consumers_are_rejected(self):
        code = 'set(CMAKE_SIZEOF_VOID_P 4)\n' + self.find(self.packages["cpu"], required=False)
        code += 'if(Skia_FOUND OR TARGET Skia::Skia)\n  message(FATAL_ERROR "32-bit consumer accepted")\nendif()\n'
        status, output = self.configure(code)
        self.assertEqual(status, 0, output)

    def test_target_os_is_checked(self):
        code = 'set(CMAKE_SYSTEM_NAME FreeBSD)\n' + self.find(self.packages["cpu"], required=False)
        code += 'if(Skia_FOUND OR TARGET Skia::Skia)\n  message(FATAL_ERROR "Unsupported OS accepted")\nendif()\n'
        status, output = self.configure(code)
        self.assertEqual(status, 0, output)

    def test_macos_thin_package_rejects_universal_consumer(self):
        root = self.root / "macos"
        for _, destination in package.binary_layout("darwin", "cpu").values():
            path = root / destination
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fixture")
        package.write_cmake_package_files(str(root), "m152-0873ec164a-r1", "darwin", "arm64", "cpu", source_commit="0873ec164a" + "0" * 30)
        config = root / "lib/cmake/Skia"
        code = 'set(CMAKE_SYSTEM_NAME Darwin)\nset(APPLE TRUE)\nset(CMAKE_OSX_ARCHITECTURES "arm64;x86_64")\n'
        code += self.find(config, required=False)
        code += 'if(Skia_FOUND OR TARGET Skia::Skia)\n  message(FATAL_ERROR "Thin package accepted as universal")\nendif()\n'
        status, output = self.configure(code)
        self.assertEqual(status, 0, output)


if __name__ == "__main__":
    unittest.main()
