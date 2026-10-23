"""Container entry point shared by x64 and ARM64 release jobs."""

import os
import argparse
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import generate_skia_package as package
import linux_support
from tools.verify_packages import verify

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--jobs', type=package.positive_jobs, default=12)
args = parser.parse_args()
target = linux_support.target_identity()
if target['architecture'] != package.normalize_cpu_name(package.platform.machine()):
    raise RuntimeError('The Linux target sysroot does not match the native runner')
os.environ['PATH'] = '/skia/third_party/ninja:' + os.environ['PATH']
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], check=True)
subprocess.run([sys.executable, 'generate_skia_package.py', '/skia', '--build-root', '/work/build/linux', '--jobs', str(args.jobs)], check=True)
verify('dist', args.jobs)
