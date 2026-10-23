"""Record the target sysroot and its runtime redistribution material at image build time."""

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

root = Path('/opt/skia-target')
sysroot = Path('/opt/skia-sysroot')
policy = json.loads((root / 'target.json').read_text())
gcc_paths = list((sysroot / 'opt/rh/gcc-toolset-11/root/usr/lib/gcc').glob('*/*'))
if len(gcc_paths) != 1:
    raise RuntimeError(f'Expected exactly one native GCC installation: {gcc_paths}')
gcc = gcc_paths[0]
(root / 'gcc-install-dir').write_text(str(gcc) + '\n')
compiler = subprocess.check_output(['/usr/lib/llvm-21/bin/clang++', '--version'], text=True)
if f'version {policy["clang"]} ' not in compiler:
    raise RuntimeError('Unexpected Clang version')

# Includes GCC's C++ headers, linker scripts and nonshared compatibility archive,
# plus libc, headers and system dependencies. Symlinks are hashed without following
# them; /etc/mtab and similar absolute links must not read the build host.
digest = hashlib.sha256()
for path in sorted(sysroot.rglob('*')):
    digest.update(path.relative_to(sysroot).as_posix().encode() + b'\0')
    if path.is_symlink():
        digest.update(b'link\0' + os.readlink(path).encode() + b'\0')
    elif path.is_file():
        digest.update(b'file\0')
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        digest.update(b'\0')

# GCC Toolset's linker script uses a small nonshared compatibility archive.
# Preserve its GPLv3 + GCC Runtime Library Exception texts with the packages.
licenses = root / 'runtime-licenses'
licenses.mkdir()
gcc_docs = sysroot / 'opt/rh/gcc-toolset-11/root/usr/share/doc/gcc-toolset-11-gcc'
for name in ('COPYING3', 'COPYING.RUNTIME'):
    shutil.copy2(gcc_docs / name, licenses / name)
(licenses / 'NOTICE.txt').write_text(
    'GCC Toolset 11 runtime compatibility code is licensed under GPLv3 with the\n'
    'GCC Runtime Library Exception 3.1. See COPYING3 and COPYING.RUNTIME.\n'
    'Exact RPM versions and the sysroot identity are in build-manifest.json.\n'
    'Source packages: https://git.almalinux.org/rpms/gcc-toolset-11-gcc\n')
architecture = {'x86_64': 'x64', 'aarch64': 'arm64'}[platform.machine()]
loader_name = 'ld-linux-x86-64.so.2' if architecture == 'x64' else 'ld-linux-aarch64.so.1'
loader = next(sysroot / directory / loader_name for directory in ('lib64', 'lib', 'usr/lib64') if (sysroot / directory / loader_name).exists())
runtime = subprocess.check_output([str(loader), '--library-path', f'{sysroot}/usr/lib64:{sysroot}/lib64', str(sysroot / 'lib64/libc.so.6')], text=True)
if not re.search(r'release version ' + re.escape(policy['glibc']) + r'\.', runtime):
    raise RuntimeError('The target runtime is not the required glibc baseline')
manifest = dict(schema_version=1, policy=policy, architecture=architecture,
                sysroot=str(sysroot), gcc_install_dir=str(gcc),
                runtime_licenses=str(licenses), sysroot_sha256=digest.hexdigest(),
                target_libc=runtime.splitlines()[0], packages=(sysroot / 'skia-target-rpms.txt').read_text().splitlines())
(root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
