#!/usr/bin/env python3
"""Verify, relocate and compile/run the two packages for the native platform."""

import argparse
import json
from pathlib import Path, PurePosixPath
import platform
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import generate_skia_package as package
import release_support as release
import linux_support as linux


def binary_architecture(path, system):
    with open(path, "rb") as stream:
        header = stream.read(64)
        if system == "windows":
            if header[:2] != b"MZ":
                raise ValueError("Not a PE binary")
            stream.seek(struct.unpack_from("<I", header, 60)[0])
            pe = stream.read(6)
            if pe[:4] != b"PE\0\0":
                raise ValueError("Invalid PE signature")
            machine = struct.unpack_from("<H", pe, 4)[0]
            return {0x8664: "x64", 0xAA64: "arm64"}[machine]
        if system == "linux":
            if header[:6] != b"\x7fELF\x02\x01":
                raise ValueError("Expected a 64-bit little-endian ELF binary")
            return {62: "x64", 183: "arm64"}[struct.unpack_from("<H", header, 18)[0]]
        if header[:4] != b"\xcf\xfa\xed\xfe":
            raise ValueError("Expected a thin 64-bit Mach-O binary")
        return {0x1000007: "x64", 0x100000C: "arm64"}[struct.unpack_from("<I", header, 4)[0]]


def extract_verified(archive, destination, expected_root):
    def safe(name):
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or not path.parts or path.parts[0] != expected_root:
            raise ValueError(f"Unsafe or unexpected archive entry: {name}")

    allowed_links = {}
    import re
    match = re.fullmatch(r"skia-(m[1-9][0-9]*-[0-9a-f]{10}-r[1-9][0-9]*)-(cpu|gpu)-linux-(x64|arm64)", expected_root)
    if match:
        allowed_links = {expected_root + "/" + name: target for name, target in linux.library_symlinks(match[1]).items()}
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as packed:
            seen = set()
            for item in packed.infolist():
                safe(item.orig_filename)
                canonical = PurePosixPath(item.orig_filename).as_posix()
                if canonical in seen:
                    raise ValueError("Duplicate archive entry")
                seen.add(canonical)
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Symlinks are not permitted in release archives")
            packed.extractall(destination)
    else:
        with tarfile.open(archive) as packed:
            members = packed.getmembers()
            seen = {}
            for item in members:
                safe(item.name)
                canonical = PurePosixPath(item.name).as_posix()
                if canonical in seen:
                    raise ValueError("Duplicate archive entry")
                seen[canonical] = item
                if item.issym():
                    if allowed_links.get(canonical) != item.linkname:
                        raise ValueError("Unexpected or unsafe archive symlink")
                elif not item.isdir() and not item.isfile():
                    raise ValueError("Only regular files, directories and approved library symlinks are permitted")
            for name, item in seen.items():
                if item.issym():
                    target = str(PurePosixPath(name).parent / item.linkname)
                    if target not in seen or not (seen[target].isfile() or seen[target].issym()):
                        raise ValueError("Dangling archive symlink")
                for parent in PurePosixPath(name).parents:
                    if str(parent) in seen and not seen[str(parent)].isdir():
                        raise ValueError("Archive entry has a non-directory parent")
            packed.extractall(destination, filter="data")


def verify_contents(root, release_info, system, cpu, profile):
    manifest = json.loads((root / "build-manifest.json").read_text(encoding="utf-8"))
    expected = dict(release_tag=release_info.tag, skia_commit=release_info.skia_commit,
                    cmake_version=release_info.cmake_version, profile=profile,
                    platform=package.detect_package_platform(system, cpu),
                    sanitizer="none", header_abi="uninstrumented", schema_version=1)
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise ValueError(f"Package manifest mismatch: {key}")
    expected_binaries = {value[1] for value in package.packaged_binary_layout(system, profile, release_info.tag).values()}
    if set(manifest.get("binaries", {})) != expected_binaries:
        raise ValueError("Binary manifest is incomplete")
    for relative, checksum in manifest["binaries"].items():
        path = root / relative
        if release.file_digest(path) != checksum:
            raise ValueError(f"Binary checksum mismatch: {relative}")
        if path.suffix not in {".lib"} and binary_architecture(path, system) != cpu:
            raise ValueError(f"Binary architecture mismatch: {relative}")
    for required in ("NOTICE.txt", "licenses/Skia-LICENSE", "licenses/SkCMS-LICENSE",
                     "licenses/Packaging-MIT-LICENSE", "include/private/SkASAN.h"):
        if not (root / required).is_file():
            raise ValueError(f"Package is incomplete: {required}")
    if not manifest.get("recipe_sha256") or not manifest.get("toolchain") or not manifest.get("dependencies"):
        raise ValueError("Package provenance is incomplete")
    if system == "linux":
        if manifest.get("linux_abi") != linux.abi_contract(release_info.tag) or manifest.get("symlinks") != linux.library_symlinks(release_info.tag):
            raise ValueError("Incorrect Linux ABI or symlink manifest")
        for relative, target in manifest["symlinks"].items():
            path = root / relative
            if not path.is_symlink() or path.readlink().as_posix() != target or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Incorrect Linux library symlink")
        elf = linux.inspect_elf(root / manifest["linux_abi"]["library"], release_info.tag)
        if manifest.get("linux_elf") != elf:
            raise ValueError("ELF metadata does not match binary")
        if manifest["toolchain"].get("linux_target") != linux.target_identity():
            raise ValueError("Linux package uses a different target toolchain")
        for name in ("COPYING3", "COPYING.RUNTIME", "NOTICE.txt"):
            if not (root / "licenses/GCC-Runtime" / name).is_file():
                raise ValueError("Missing GCC runtime redistribution notices")
    return manifest


def verify(dist, jobs, use_asan=True):
    info = release.load_release()
    system = platform.system().lower()
    cpu = package.normalize_cpu_name(platform.machine())
    target = linux.target_identity() if system == "linux" else None
    if target and target["architecture"] != cpu:
        raise ValueError("Linux target architecture does not match native host")
    results = []
    # Verify the extracted package outside its source/build trees. Runtime
    # discovery must work after relocation, without hidden checkout paths.
    with tempfile.TemporaryDirectory(prefix="skia-package-consumer-") as temporary:
        temporary = Path(temporary)
        for profile in package.PROFILES:
            name = package.package_name(info.tag, system, cpu, profile)
            archive = Path(dist).resolve() / (name + (".zip" if system == "windows" else ".tar.gz"))
            checksum = archive.with_name(archive.name + ".sha256").read_text(encoding="utf-8")
            if checksum != f"{release.file_digest(archive)}  {archive.name}\n":
                raise ValueError(f"Archive checksum mismatch: {archive.name}")
            extract_verified(archive, temporary, name)
            root = temporary / name
            manifest = verify_contents(root, info, system, cpu, profile)
            if system == "windows" and cpu == "arm64":
                imports = release.command_version(["dumpbin.exe", "/nologo", "/imports", str(root / "bin/skia.dll")]).lower()
                if "wglget" in imports or "opengl32.dll" in imports:
                    raise RuntimeError("Windows ARM64 package imports unsupported WGL")
            elif system == "linux":
                output = release.command_version(["ldd", str(root / "lib/libskia.so")])
                if "not found" in output:
                    raise RuntimeError(output)
            elif system == "darwin":
                output = release.command_version(["otool", "-L", str(root / "lib/libskia.dylib")])
                for line in output.splitlines()[1:]:
                    dependency = line.strip().split(" ", 1)[0]
                    if not dependency.startswith(("@rpath/", "/usr/lib/", "/System/Library/")):
                        raise RuntimeError(f"Nonportable macOS dependency: {dependency}")
            modes = ["normal", "asan-layout"]
            # MSVC currently supports ASan on x64, not Windows ARM64.
            if use_asan and not (system == "windows" and cpu == "arm64"):
                modes.append("asan")
            for mode in modes:
                build = temporary / "consumers" / profile / mode
                compiler = []
                if target:
                    compiler = ["-DCMAKE_CXX_COMPILER=" + ("/usr/bin/g++" if mode == "asan" else "/opt/skia-toolchain/bin/clang++")]
                subprocess.run(["cmake", "-S", str(release.ROOT / "tests/pathops"), "-B", str(build), "-G", "Ninja",
                                "-DCMAKE_BUILD_TYPE=RelWithDebInfo", f"-DCMAKE_PREFIX_PATH={root}",
                                f"-DSKIA_EXPECT_PROFILE={profile}", f"-DSKIA_TEST_ASAN={'ON' if mode == 'asan' else 'OFF'}",
                                f"-DSKIA_TEST_ASAN_LAYOUT={'ON' if mode == 'asan-layout' else 'OFF'}", *compiler], check=True)
                subprocess.run(["cmake", "--build", str(build), "-j", str(jobs)], check=True)
                subprocess.run(["ctest", "--test-dir", str(build), "--output-on-failure"], check=True)
                if target and mode != "asan":
                    command = linux.runtime_command(build / "skia_pathops_smoke", root, target)
                    # --list must resolve every dependency inside the package or
                    # old sysroot, so a modern host cannot hide a missing runtime.
                    linked = subprocess.check_output([*command[:-1], "--list", command[-1]], text=True)
                    if "not found" in linked:
                        raise RuntimeError(linked)
                    for line in linked.splitlines():
                        if " => /" in line:
                            resolved = Path(line.split(" => ", 1)[1].split()[0]).resolve()
                            if not any(resolved.is_relative_to(Path(parent).resolve()) for parent in (root, target["sysroot"])):
                                raise RuntimeError(f"Baseline test leaked a host library: {line}")
                    subprocess.run(command, check=True)
                    # Prove runtime deployment using the imported target's real
                    # file and SONAME alias; no development symlink is required.
                    bundle = temporary / "deployment" / profile / mode
                    bundle.mkdir(parents=True)
                    real, soname = linux.library_names(info.tag)
                    shutil.copy2(root / "lib" / real, bundle / real)
                    (bundle / soname).symlink_to(real)
                    shutil.copy2(build / "skia_pathops_smoke", bundle / "consumer")
                    deployment = linux.runtime_command(bundle / "consumer", root, target)
                    deployment[2] = str(bundle) + ":" + deployment[2].split(":", 1)[1]
                    resolved = subprocess.check_output([*deployment[:-1], "--list", deployment[-1]], text=True)
                    if f"{soname} => {bundle / soname} " not in resolved:
                        raise RuntimeError("Runtime deployment did not load its own versioned Skia library: " + resolved)
                    subprocess.run(deployment, check=True)
            if target:
                modes.extend(["baseline-runtime", "elf-abi", "runtime-deployment"])
            results.append(dict(archive=archive.name, sha256=release.file_digest(archive), tests=modes,
                                platform=manifest["platform"], profile=profile))
    release.write_json(Path(dist) / f"verification-{package.detect_package_platform(system, cpu)}.json", results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", nargs="?", default="dist")
    parser.add_argument("--jobs", type=package.positive_jobs, default=12)
    args = parser.parse_args()
    verify(args.dist, args.jobs)
