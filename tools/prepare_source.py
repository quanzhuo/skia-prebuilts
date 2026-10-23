#!/usr/bin/env python3
"""Fetch the exact Skia revision and sync its pinned build dependencies."""

import argparse
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_support as release


def host_tool_command(script):
    # The pinned upstream GN revision has no windows-arm64 CIPD package.
    # Windows ARM64 can execute the matching x64 GN/Ninja host tools; LLVM,
    # MSVC libraries and the target CPU remain native ARM64 throughout.
    if platform.system() == "Windows" and platform.machine().lower() in {"arm64", "aarch64"}:
        return [sys.executable, "-c",
                "import platform,runpy,sys; platform.machine=lambda:'AMD64'; "
                "runpy.run_path(sys.argv[1], run_name='__main__')", str(script)]
    return [sys.executable, str(script)]


def prepare(destination, release_info):
    destination = Path(destination).resolve()
    if not destination.exists():
        destination.mkdir(parents=True)
        subprocess.run(["git", "init", "--quiet", str(destination)], check=True)
        subprocess.run(["git", "remote", "add", "origin", "https://skia.googlesource.com/skia.git"],
                       cwd=destination, check=True)
        subprocess.run(["git", "fetch", "--depth=1", "origin", release_info.skia_commit],
                       cwd=destination, check=True)
        subprocess.run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=destination, check=True)
    elif release.git_output(destination, "rev-parse", "HEAD") != release_info.skia_commit:
        raise RuntimeError("Existing source has a different HEAD; use a new destination")
    release.check_clean(destination)
    definitions = release.dependency_definitions(destination)
    system = platform.system().lower()
    selected = {path: definitions[path] for path in release.required_dependencies(system)}
    # Upstream's supported override retains its exact dependency URLs and
    # revisions while avoiding unrelated Dawn, Android, WASM and test assets.
    # The temporary DEPS must be next to upstream DEPS for relative paths.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".prebuilts-DEPS",
                                     dir=destination, delete=False) as stream:
        filtered = Path(stream.name)
        stream.write("deps = " + repr(selected) + "\n")
    try:
        code = ("import runpy,sys; sync=runpy.run_path(sys.argv[1]); "
                "sync['git_sync_deps'](sys.argv[2], [], True, True)")
        subprocess.run([sys.executable, "-c", code, str(destination / "tools/git-sync-deps"), str(filtered)],
                       cwd=destination, check=True)
    finally:
        filtered.unlink()
    for script in ("fetch-gn", "fetch-ninja"):
        subprocess.run(host_tool_command(destination / "bin" / script), cwd=destination, check=True)
    release.verify_sources(destination, release_info, system)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination")
    parser.add_argument("--release-file", default=release.ROOT / "release.json")
    args = parser.parse_args()
    prepare(args.destination, release.load_release(args.release_file))
