import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import unittest
from unittest import mock

import generate_skia_package as package
import linux_support as linux
import release_support as release
from tools import build_linux


TAG = 'm152-0873ec164a-r2'


class LinuxAbiTests(unittest.TestCase):
    def test_library_identity_includes_packaging_revision(self):
        self.assertEqual(linux.library_names(TAG), ('libskia.so.152.2.0', 'libskia.so.152.2'))
        self.assertNotEqual(linux.library_names(TAG)[1], linux.library_names(TAG.replace('-r2', '-r3'))[1])
        self.assertEqual(linux.library_symlinks(TAG), {
            'lib/libskia.so': 'libskia.so.152.2', 'lib/libskia.so.152.2': 'libskia.so.152.2.0'})
        for tag in ('../r2', TAG + '\n', 'm152-0873ec164a-r0'):
            with self.assertRaises(ValueError): linux.library_names(tag)

    def dynamic(self, soname='libskia.so.152.2'):
        return f'(SONAME) Library soname: [{soname}]\n(NEEDED) Shared library: [libc.so.6]\n'

    def needs(self, *versions):
        return 'Version needs section: .gnu.version_r\n' + '\n'.join('Name: ' + value for value in versions)

    def test_both_c_and_cpp_runtime_ceilings_are_enforced(self):
        result = linux.parse_elf(self.dynamic(), self.needs('GLIBC_2.28', 'GLIBC_2.17', 'GLIBCXX_3.4.25', 'CXXABI_1.3.11', 'GCC_7.0.0'), TAG)
        self.assertEqual(result['symbol_versions']['GLIBC'], '2.28')
        for version in ('GLIBC_2.29', 'GLIBCXX_3.4.26', 'CXXABI_1.3.12', 'GCC_8.0.0', 'GLIBC_PRIVATE'):
            with self.subTest(version=version), self.assertRaises(ValueError):
                linux.parse_elf(self.dynamic(), self.needs('GLIBC_2.17', version), TAG)

    def test_version_definitions_do_not_hide_new_requirements(self):
        versions = 'Version definition section\nName: GLIBC_2.2.5\n' + self.needs('GLIBC_2.34')
        with self.assertRaisesRegex(ValueError, 'exceeds'): linux.parse_elf(self.dynamic(), versions, TAG)

    def test_wrong_soname_and_nonportable_paths_are_rejected(self):
        for dynamic in (self.dynamic('libskia.so'), self.dynamic('libskia.so.152.1'),
                        self.dynamic() + '(RUNPATH) Library runpath: [/build]\n',
                        self.dynamic().replace('libc.so.6', '/usr/lib/libc.so.6')):
            with self.subTest(dynamic=dynamic), self.assertRaises(ValueError):
                linux.parse_elf(dynamic, self.needs('GLIBC_2.17'), TAG)
        with self.assertRaises(ValueError): linux.parse_elf(self.dynamic(), '', TAG)

    def test_uncontrolled_linux_toolchain_is_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, 'build_linux.py'):
            linux.target_identity()

    def test_target_identity_records_image_sysroot_and_native_architecture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = dict(schema_version=1, policy=linux.POLICY, architecture='arm64', sysroot_sha256='a' * 64,
                            sysroot=directory, gcc_install_dir=directory, runtime_licenses=directory)
            path = root / 'manifest.json'
            path.write_text(json.dumps(manifest))
            with mock.patch.dict(os.environ, SKIA_LINUX_TARGET=str(path), SKIA_LINUX_IMAGE_ID='sha256:' + 'b' * 64):
                self.assertEqual(linux.target_identity()['image_id'], 'sha256:' + 'b' * 64)
                manifest['policy'] = dict(linux.POLICY, glibc='2.35')
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'target.json'): linux.target_identity()

    def test_object_cache_changes_with_release_soname(self):
        with mock.patch.object(package.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='a' * 40)):
            first = package.source_build_fingerprint('.', 'linux', 'cpu', TAG)
            self.assertNotEqual(first, package.source_build_fingerprint('.', 'linux', 'cpu', TAG.replace('-r2', '-r3')))

    def test_target_and_verification_changes_invalidate_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('generate_skia_package.py', 'release_support.py', 'linux_support.py',
                         'tools/build_linux.py', 'tools/verify_packages.py', '.dockerignore',
                         'tools/linux/target.json', 'tools/linux/clang-wrapper', 'tools/linux/Dockerfile',
                         'tests/pathops/main.cpp'):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('initial\n')
            with mock.patch.object(release, 'ROOT', root):
                original = release.recipe_digest()
                with mock.patch.object(type(root), '__lt__', lambda left, right: str(left) > str(right)):
                    self.assertEqual(original, release.recipe_digest())
                for name in ('tools/linux/target.json', 'tools/linux/clang-wrapper', 'tools/linux/Dockerfile',
                             'tools/verify_packages.py', 'tests/pathops/main.cpp'):
                    original = release.recipe_digest()
                    (root / name).write_text('changed\n')
                    self.assertNotEqual(original, release.recipe_digest(), name)

    def test_native_linux_ci_architectures_use_one_baseline_driver(self):
        workflow = (release.ROOT / '.github/workflows/release.yml').read_text()
        for runner in ('ubuntu-22.04', 'ubuntu-22.04-arm'):
            self.assertIn('runner: ' + runner, workflow)
        self.assertIn('python tools/build_linux.py build/skia --jobs', workflow)
        self.assertNotIn('llvm-toolchain-jammy', workflow)
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(build_linux.platform, 'system', return_value='Linux'), \
             mock.patch.object(build_linux.os, 'getuid', return_value=1000, create=True), \
             mock.patch.object(build_linux.os, 'getgid', return_value=1000, create=True), \
             mock.patch.object(build_linux.subprocess, 'check_output', return_value='sha256:' + 'a' * 64), \
             mock.patch.object(build_linux.subprocess, 'run') as run:
            build_linux.build(directory)
            build, execute = [call.args[0] for call in run.call_args_list]
            self.assertIn('SYSROOT_IMAGE=' + linux.POLICY['sysroot_image'], build)
            self.assertIn('--provenance=false', build)
            self.assertIn('--read-only', execute)
            self.assertIn('SKIA_LINUX_IMAGE_ID=sha256:' + 'a' * 64, execute)

    @unittest.skipUnless(platform.system() == 'Linux', 'Requires a native ELF linker')
    def test_native_linker_and_readelf_verify_the_versioned_soname(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'fixture.c'
            source.write_text('#include <stdio.h>\nvoid exported(void) { puts("ELF fixture"); }\n')
            library = root / 'libskia.so'
            subprocess.run(['cc', '-shared', '-fPIC', str(source), '-Wl,-soname,libskia.so.152.2', '-o', str(library)], check=True)
            self.assertEqual(linux.inspect_elf(library, TAG)['soname'], 'libskia.so.152.2')

    def test_cmake_import_uses_real_file_and_declares_soname(self):
        with tempfile.TemporaryDirectory() as directory:
            package.write_cmake_package_files(directory, TAG, 'linux', package.normalize_cpu_name(platform.machine()), 'cpu', '0873ec164a' + '0' * 30)
            targets = (Path(directory) / 'lib/cmake/Skia/SkiaTargets.cmake').read_text()
            self.assertIn('IMPORTED_LOCATION "${_skia_package_prefix}/lib/libskia.so.152.2.0"', targets)
            self.assertIn('IMPORTED_SONAME "libskia.so.152.2"', targets)
            for name in ('libskia.so', 'libskia.so.152.2', 'libskia.so.152.2.0'):
                self.assertIn('/lib/' + name + '"', targets)

    @unittest.skipUnless(platform.system() == 'Linux', 'Requires a POSIX compiler wrapper')
    def test_wrapper_resolves_indirect_sysroot_dependencies_only_when_linking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = root / 'backend'
            backend.mkdir()
            compiler = backend / 'clang'
            compiler.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps(sys.argv))\n')
            compiler.chmod(0o755)
            (backend / 'clang++').symlink_to('clang')
            gcc = root / 'gcc-install-dir'
            gcc.write_text('/fixture/gcc\n')
            script = (release.ROOT / 'tools/linux/clang-wrapper').read_text().replace('/usr/lib/llvm-21/bin/clang', str(compiler)).replace('/opt/skia-target/gcc-install-dir', str(gcc))
            wrapper = root / 'clang++'
            wrapper.write_text(script)
            for args, linking in [(['-shared', 'input.o'], True), (['input.cpp', '-o', 'app'], True),
                                  (['-c', 'input.cpp'], False), (['-E', 'input.cpp'], False), (['--version'], False)]:
                output = subprocess.check_output(['/bin/sh', str(wrapper), *args], text=True)
                argv = json.loads(output)
                self.assertEqual(any(value.startswith('-Wl,-rpath-link,') for value in argv), linking, args)
                self.assertFalse(any(value.startswith('-Wl,-rpath,') for value in argv))
                self.assertIn('--gcc-install-dir=/fixture/gcc', argv)
