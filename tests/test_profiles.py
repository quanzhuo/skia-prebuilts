import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

import generate_skia_package as package


VERSION = "m152-0873ec164a"


class ProfileTests(unittest.TestCase):
    def test_names_are_derived_for_each_platform_and_profile(self):
        for system, prefix in (("Windows", "win32"), ("Linux", "linux"), ("Darwin", "macos")):
            for machine, cpu in (("AMD64", "x64"), ("aarch64", "arm64")):
                with mock.patch.object(package.platform, "machine", return_value=machine):
                    self.assertEqual(package.package_name(VERSION, system.lower(), None, "full"),
                                     f"skia-{VERSION}-{prefix}-{cpu}")
                    self.assertEqual(package.package_name(VERSION, system.lower(), None, "cpu-only"),
                                     f"skia-{VERSION}-cpu-only-{prefix}-{cpu}")

    def test_versions_cannot_inject_paths_or_profile_suffixes(self):
        for value in ("../m152-0873ec164a", "m152-0873ec164a/cpu", "m152-0873ec164a-cpu-only",
                      "skia-m152-0873ec164a-win32-x64", "m152-123", "m0-abcdef0", "m152-abcdefg",
                      "m152-" + "a" * 41, "m152-0873ec164a\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                package.parse_package_version(value)
        self.assertEqual(package.parse_package_version("m152-ABCDEF0")["commit"], "abcdef0")

    def test_native_architecture_is_explicit_and_cross_architecture_is_not_silently_ignored(self):
        with mock.patch.object(package.platform, "machine", return_value="AMD64"):
            for system in ("windows", "linux"):
                with self.assertRaises(ValueError):
                    package.detect_package_platform(system, "arm64")
            self.assertEqual(package.detect_package_platform("darwin", "arm64"), "macos-arm64")

    def test_six_configs_preserve_profile_semantics_and_pathops(self):
        for system in ("windows", "linux", "darwin"):
            for profile in package.PROFILES:
                with self.subTest(system=system, profile=profile):
                    contents = Path(package.get_args_gn_file_path(system, profile)).read_text(encoding="utf-8")
                    expected = "true" if profile == "full" else "false"
                    self.assertIn(f"skia_enable_ganesh = {expected}", contents)
                    self.assertIn("skia_use_libpng_encode = true", contents)
                    patches = [Path(p).name for p in package.get_source_patch_paths(system, profile)]
                    self.assertIn("0002-include-pathops-in-skia.patch", patches)
                    self.assertEqual("0001-expose-angle-runtime-target.patch" in patches,
                                     system == "windows" and profile == "full")
                    rendered = package.render_args_gn_contents(package.get_args_gn_file_path(system, profile), system, None)
                    self.assertIn("target_cpu =", rendered)
                    self.assertNotIn("min_macos_version =", rendered)

    def test_cpu_only_public_definitions_do_not_expose_gpu(self):
        for system, font_macro in (("windows", "SK_FONTMGR_DIRECTWRITE_AVAILABLE"),
                                   ("linux", "SK_FONTMGR_FONTCONFIG_AVAILABLE"),
                                   ("darwin", "SK_ENABLE_API_AVAILABLE")):
            cpu = package.get_interface_compile_definitions(system, "cpu-only").split(";")
            self.assertIn(font_macro, cpu)
            self.assertIn("SK_CODEC_ENCODES_PNG", cpu)
            self.assertFalse(any(value.startswith(("SK_GANESH", "SK_GL", "SK_VULKAN", "SK_METAL", "SK_ASSUME_GL")) for value in cpu))
            self.assertEqual(package.get_gpu_backends(system, "cpu-only"), "")
            self.assertNotEqual(package.get_gpu_backends(system, "full"), "")

    def test_invalid_profile_is_rejected(self):
        for function, args in ((package.package_name, (VERSION, "windows", None)),
                               (package.binary_layout, ("windows",)),
                               (package.get_args_gn_file_path, ("windows",))):
            with self.assertRaises(ValueError):
                function(*args, "typo")


class PackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="skia-profiles-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "skia"
        self.build = self.root / "build"
        self.output = self.root / "dist"
        self.build.mkdir()
        for relative in ("include/core/SkMilestone.h", "include/pathops/SkPathOps.h",
                         "modules/skcms/skcms.h", "modules/skcms/src/skcms_public.h",
                         "third_party/externals/angle2/include/EGL/egl.h"):
            path = self.repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#define SK_MILESTONE 152\n", encoding="utf-8")
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))

    def test_version_matches_actual_commit_and_milestone(self):
        with mock.patch.object(package.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, stdout="0873ec164a" + "0" * 30 + "\n")):
            package.validate_source_version(str(self.repo), VERSION)
            for version in ("m151-0873ec164a", "m152-1234567"):
                with self.assertRaisesRegex(ValueError, "不一致"):
                    package.validate_source_version(str(self.repo), version)

    def test_profile_and_config_change_invalidate_fingerprint(self):
        config = self.root / "args.gn"
        config.write_text("a = true\n", encoding="utf-8")
        with mock.patch.object(package, "get_args_gn_file_path", return_value=str(config)), \
             mock.patch.object(package, "get_source_patch_paths", return_value=[]), \
             mock.patch.object(package.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout="abc1234\n")):
            full = package.source_build_fingerprint(str(self.repo), "linux", "full")
            self.assertNotEqual(full, package.source_build_fingerprint(str(self.repo), "linux", "cpu-only"))
            config.write_text("a = false\n", encoding="utf-8")
            self.assertNotEqual(full, package.source_build_fingerprint(str(self.repo), "linux", "full"))

    def test_cached_args_require_both_matching_profile_and_architecture(self):
        with mock.patch.object(package.platform, "machine", return_value="AMD64"):
            config = package.get_args_gn_file_path("darwin", "cpu-only")
            package.prepare_args_gn_file(config, str(self.build / "args.gn"), "darwin", "arm64")
            self.assertTrue(package.build_args_gn_is_current(str(self.build), "darwin", "arm64", "cpu-only"))
            self.assertFalse(package.build_args_gn_is_current(str(self.build), "darwin", "arm64", "full"))
            self.assertFalse(package.build_args_gn_is_current(str(self.build), "darwin", "x64", "cpu-only"))

    def test_copy_directory_never_deletes_an_existing_destination(self):
        (self.build / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            package.copy_directory(str(self.repo / "include"), str(self.build))
        self.assertEqual((self.build / "keep.txt").read_text(), "keep")

    @contextlib.contextmanager
    def cached_package(self, system):
        # Deliberately leave full outputs present even when packaging CPU-only.
        for source, _ in package.binary_layout(system, "full").values():
            (self.build / source).write_bytes(b"fixture binary: " + source.encode())
        with mock.patch.object(package.platform, "system", return_value=system.capitalize()), \
             mock.patch.object(package.platform, "machine", return_value="AMD64"), \
             mock.patch.object(package, "validate_source_version"), \
             mock.patch.object(package, "source_build_fingerprint", return_value="fingerprint"), \
             mock.patch.object(package, "build_args_gn_is_current", return_value=True), \
             mock.patch.object(package, "build_source_is_current", return_value=True), \
             mock.patch.object(package, "build_skia") as build:
            yield build

    def create(self, profile="full", force=False):
        return package.create_package(str(self.repo), str(self.build), VERSION,
                                      force_build=force, profile=profile, output_dir=str(self.output))

    def test_archives_contain_matching_profile_metadata_and_only_required_binaries(self):
        for system in ("windows", "linux", "darwin"):
            for profile in package.PROFILES:
                with self.subTest(system=system, profile=profile), self.cached_package(system) as build:
                    archive = self.create(profile)
                    build.assert_not_called()
                    name = package.package_name(VERSION, system, None, profile)
                    if system == "windows":
                        with zipfile.ZipFile(archive) as packed:
                            names = packed.namelist()
                            contents = packed.read(f"{name}/lib/cmake/Skia/SkiaTargets.cmake").decode()
                            config = packed.read(f"{name}/lib/cmake/Skia/SkiaConfig.cmake").decode()
                    else:
                        with tarfile.open(archive) as packed:
                            names = packed.getnames()
                            contents = packed.extractfile(f"{name}/lib/cmake/Skia/SkiaTargets.cmake").read().decode()
                            config = packed.extractfile(f"{name}/lib/cmake/Skia/SkiaConfig.cmake").read().decode()
                    self.assertTrue(all(entry == name or entry.startswith(name + "/") for entry in names))
                    self.assertIn(f"{name}/include/pathops/SkPathOps.h", names)
                    self.assertIn(f'set(Skia_BUILD_PROFILE "{profile}")', config)
                    self.assertIn("set(Skia_HAS_PATHOPS TRUE)", config)
                    self.assertIn("INTERFACE_COMPILE_FEATURES cxx_std_20", contents)
                    self.assertNotIn("@SKIA_", contents + config)
                    angle = system == "windows" and profile == "full"
                    self.assertEqual("Skia::Angle" in contents, angle)
                    self.assertEqual(any("libGLESv2" in entry for entry in names), angle)
                    self.assertEqual(any("/angle/" in entry for entry in names), angle)
                    for _, destination in package.binary_layout(system, profile).values():
                        self.assertIn(f"{name}/{destination}", names)

    def test_force_build_propagates_profile(self):
        with self.cached_package("windows") as build:
            self.create("cpu-only", force=True)
            self.assertEqual(build.call_args.kwargs["profile"], "cpu-only")

    def test_incomplete_cpu_binary_is_not_packaged(self):
        with self.cached_package("windows") as build:
            (self.build / "skia.dll").unlink()
            with self.assertRaises(FileNotFoundError):
                self.create("cpu-only")
            build.assert_called_once()
            self.assertFalse(self.output.exists())

    def test_failed_compression_preserves_previous_archive_and_cleans_staging(self):
        with self.cached_package("windows"):
            archive = Path(self.create())
            previous = archive.read_bytes()
            with mock.patch.object(package.shutil, "make_archive", side_effect=OSError("compression failed")):
                with self.assertRaises(OSError):
                    self.create()
            self.assertEqual(archive.read_bytes(), previous)
            self.assertEqual(list(self.output.iterdir()), [archive])

    def test_default_batch_separates_platform_and_profile_build_directories(self):
        with mock.patch.object(package.platform, "system", return_value="Windows"), \
             mock.patch.object(package.platform, "machine", return_value="AMD64"), \
             mock.patch.object(package, "checkout_build_lock", return_value=contextlib.nullcontext()), \
             mock.patch.object(package, "validate_source_version"), \
             mock.patch.object(package, "create_package", side_effect=["full.zip", "cpu.zip"]) as create:
            results = package.create_packages(str(self.repo), VERSION, jobs=3)
            self.assertEqual(results, ["full.zip", "cpu.zip"])
            calls = create.call_args_list
            self.assertEqual([call.kwargs["profile"] for call in calls], ["full", "cpu-only"])
            for call, profile in zip(calls, package.PROFILES):
                self.assertEqual(Path(call.args[1]), self.repo / "out" / "prebuilts" / "win32-x64" / profile)
                self.assertEqual(call.kwargs["jobs"], 3)

    def test_single_profile_and_failure_stop_batch(self):
        with mock.patch.object(package, "checkout_build_lock", return_value=contextlib.nullcontext()), \
             mock.patch.object(package, "validate_source_version"), \
             mock.patch.object(package, "create_package", return_value="cpu.zip") as create:
            package.create_packages(str(self.repo), VERSION, profile="cpu-only")
            create.assert_called_once()
            self.assertEqual(create.call_args.kwargs["profile"], "cpu-only")
            create.reset_mock()
            create.side_effect = RuntimeError("build failed")
            with self.assertRaisesRegex(RuntimeError, "build failed"):
                package.create_packages(str(self.repo), VERSION)
            create.assert_called_once()

    def test_windows_ninja_does_not_build_angle_for_cpu_only(self):
        with mock.patch.object(package.platform, "system", return_value="Windows"), \
             mock.patch.object(package, "source_build_fingerprint", return_value="fingerprint"), \
             mock.patch.object(package, "applied_source_patches", return_value=contextlib.nullcontext()), \
             mock.patch.object(package, "prepare_args_gn_file"), \
             mock.patch.object(package.subprocess, "run") as run:
            for profile in package.PROFILES:
                package.build_skia(str(self.repo), str(self.build), jobs=5, profile=profile)
                self.assertEqual("skia_angle_runtime" in run.call_args.args[0], profile == "full")
                self.assertIn("--fail-on-unused-args", run.call_args_list[-2].args[0])

    def test_checkout_lock_blocks_another_process_and_releases_after_exception(self):
        subprocess.run(["git", "init", "--quiet", str(self.repo)], check=True)
        with self.assertRaisesRegex(RuntimeError, "intentional"):
            with package.checkout_build_lock(str(self.repo)):
                code = "import generate_skia_package as p, sys\nwith p.checkout_build_lock(sys.argv[1]): pass"
                child = subprocess.run([sys.executable, "-c", code, str(self.repo)],
                                       cwd=package.get_script_dir(), capture_output=True)
                self.assertNotEqual(child.returncode, 0)
                raise RuntimeError("intentional")
        with package.checkout_build_lock(str(self.repo)):
            pass


if __name__ == "__main__":
    unittest.main()
