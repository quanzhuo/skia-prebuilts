#!/usr/bin/env python3
"""Build and verify Linux packages using the native glibc 2.28 target toolchain."""

import argparse
import os
from pathlib import Path
import platform
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import linux_support
import release_support as release


def build(source, jobs=12, docker='docker'):
    if platform.system() != 'Linux':
        raise RuntimeError('Run this command on a native Linux host (or inside WSL)')
    source = Path(source).resolve(strict=True)
    root = release.ROOT
    policy = linux_support.POLICY
    image = 'skia-prebuilts-linux:' + release.recipe_digest()[:16]
    # An invocation-specific attestation index must not invalidate object caches
    # when the concrete compiler/sysroot image has not changed.
    subprocess.run([docker, 'build', '--provenance=false', '--file', str(root / 'tools/linux/Dockerfile'),
                    '--build-arg', f'SYSROOT_IMAGE={policy["sysroot_image"]}',
                    '--build-arg', f'BUILD_IMAGE={policy["build_image"]}', '-t', image, str(root)], check=True)
    image_id = subprocess.check_output([docker, 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()
    subprocess.run([docker, 'run', '--rm', '--read-only', '--tmpfs', '/tmp:exec',
                    '--user', f'{os.getuid()}:{os.getgid()}', '--env', 'HOME=/tmp',
                    '--env', f'SKIA_LINUX_IMAGE_ID={image_id}',
                    '--volume', f'{root}:/work', '--volume', f'{source}:/skia',
                    image, 'python3', 'tools/linux/build.py', '--jobs', str(jobs)], check=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', nargs='?', default='build/skia')
    parser.add_argument('--jobs', type=int, default=12)
    parser.add_argument('--docker', default='docker')
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    build(args.source, args.jobs, args.docker)
