"""Linux target ABI, shared-library identity and runtime verification."""

import json
import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent
POLICY = json.loads((ROOT / "tools/linux/target.json").read_text(encoding="utf-8"))


def library_names(tag):
    match = re.fullmatch(r"m([1-9][0-9]*)-[0-9a-f]{10}-r([1-9][0-9]*)", tag)
    if not match:
        raise ValueError("Invalid release tag for Linux library identity")
    soversion = ".".join(match.groups())
    return f"libskia.so.{soversion}.0", f"libskia.so.{soversion}"


def library_symlinks(tag):
    real, soname = library_names(tag)
    return {"lib/libskia.so": soname, f"lib/{soname}": real}


def abi_contract(tag):
    real, soname = library_names(tag)
    return dict(glibc=POLICY["glibc"], glibcxx=POLICY["glibcxx"], cxxabi=POLICY["cxxabi"],
                library=f"lib/{real}", soname=soname)


def version_tuple(value):
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", value):
        raise ValueError(f"Invalid symbol version: {value}")
    return tuple(map(int, value.split(".")))


def parse_elf(dynamic, versions, tag):
    expected = abi_contract(tag)
    sonames = re.findall(r"\(SONAME\).*?\[([^\]]+)\]", dynamic)
    if sonames != [expected["soname"]]:
        raise ValueError(f"Incorrect ELF SONAME: {sonames}; expected {expected['soname']}")
    if re.search(r"\((?:RPATH|RUNPATH)\)", dynamic):
        raise ValueError("The Linux library must not embed RPATH/RUNPATH")
    dependencies = re.findall(r"\(NEEDED\).*?\[([^\]]+)\]", dynamic)
    if not dependencies or any("/" in name or "\\" in name for name in dependencies):
        raise ValueError("Invalid Linux shared-library dependency")
    needs = versions.partition("Version needs section")[2]
    if not needs:
        raise ValueError("Missing ELF version requirements")
    requirements = {}
    for family, value in re.findall(r"Name: (GLIBCXX|GLIBC|CXXABI|GCC)_([^\s]+)", needs):
        version_tuple(value)  # Reject GLIBC_PRIVATE and other nonnumeric requirements.
        if version_tuple(value) > version_tuple(requirements.get(family, "0")):
            requirements[family] = value
    for family, limit in (("GLIBC", POLICY["glibc"]), ("GLIBCXX", POLICY["glibcxx"]),
                          ("CXXABI", POLICY["cxxabi"]), ("GCC", POLICY["gcc_abi"])):
        if version_tuple(requirements.get(family, "0")) > version_tuple(limit):
            raise ValueError(f"{family}_{requirements[family]} exceeds Linux baseline {limit}")
    if "GLIBC" not in requirements:
        raise ValueError("Missing glibc symbol requirements")
    return dict(soname=sonames[0], needed=dependencies, symbol_versions=requirements)


def inspect_elf(path, tag):
    env = dict(os.environ, LC_ALL="C")
    def read(*args):
        return subprocess.check_output(["readelf", *args, str(path)], text=True, env=env)
    return parse_elf(read("--wide", "--dynamic"), read("--wide", "--version-info"), tag)


def target_identity():
    path = os.environ.get("SKIA_LINUX_TARGET")
    image = os.environ.get("SKIA_LINUX_IMAGE_ID", "")
    if not path or not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
        raise RuntimeError("Linux builds require the baseline toolchain: python tools/build_linux.py")
    identity = json.loads(Path(path).read_text(encoding="utf-8"))
    if (identity.get("schema_version") != 1 or identity.get("policy") != POLICY
            or identity.get("architecture") not in {"x64", "arm64"}
            or not re.fullmatch(r"[0-9a-f]{64}", identity.get("sysroot_sha256", ""))):
        raise ValueError("Linux target toolchain does not match target.json")
    for key in ("sysroot", "gcc_install_dir", "runtime_licenses"):
        if not Path(identity.get(key, "")).is_dir():
            raise ValueError(f"Missing Linux target {key}")
    return dict(identity, image_id=image)


def runtime_command(executable, package_root, target):
    sysroot = Path(target["sysroot"])
    loader = sysroot / ("lib64/ld-linux-x86-64.so.2" if target["architecture"] == "x64"
                        else "lib/ld-linux-aarch64.so.1")
    # AlmaLinux puts its AArch64 loader in /lib (with /lib -> usr/lib).
    if not loader.exists():
        loader = sysroot / "lib64/ld-linux-aarch64.so.1"
    paths = [Path(package_root) / "lib", sysroot / "usr/lib64", sysroot / "lib64"]
    return [str(loader), "--library-path", ":".join(map(str, paths)), str(executable)]
