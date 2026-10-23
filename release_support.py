"""Release identity, verified source inputs and redistribution material.

No dependency is downloaded or source checkout modified by this module.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import platform
import re
import runpy
import shutil
import subprocess


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Release:
    milestone: int
    revision: int
    skia_commit: str

    def __post_init__(self):
        for value in (self.milestone, self.revision):
            if type(value) is not int or value < 1:
                raise ValueError("milestone and revision must be positive integers")
        if not re.fullmatch(r"[0-9a-f]{40}", self.skia_commit):
            raise ValueError("skia_commit must be a full lowercase SHA-1")

    @property
    def tag(self):
        return f"m{self.milestone}-{self.skia_commit[:10]}-r{self.revision}"

    @property
    def cmake_version(self):
        return f"{self.milestone}.{self.revision}.0"


def load_release(path=ROOT / "release.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"milestone", "revision", "skia_commit"}:
        raise ValueError("Release file requires exactly milestone, revision and skia_commit")
    return Release(**data)


def git_output(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


def check_clean(repo):
    # Ignored build outputs are allowed; tracked and non-ignored untracked
    # source changes are not. Never reset or clean somebody else's checkout.
    if git_output(repo, "status", "--porcelain", "--untracked-files=normal"):
        raise RuntimeError(f"Source checkout is dirty: {repo}")


def dependency_definitions(repo):
    # Use the pinned upstream parser, including its Var() handling. This is
    # the same trusted DEPS file executed by tools/git-sync-deps itself.
    sync = runpy.run_path(str(Path(repo) / "tools" / "git-sync-deps"))
    return sync["parse_file_to_dict"](str(Path(repo) / "DEPS"))["deps"]


def required_dependencies(system, profiles=("gpu", "cpu")):
    names = {"libjpeg-turbo", "libpng", "zlib"}
    if system == "linux":
        names.add("freetype")
    if "gpu" in profiles:
        # Existing GPU configurations retain upstream Wuffs defaults.
        names.add("wuffs")
        if system == "windows":
            names.update({"angle2", "spirv-headers", "spirv-tools"})
        if system == "linux":
            names.add("vulkanmemoryallocator")
    paths = {"third_party/externals/" + name for name in names}
    if system == "windows" and "gpu" in profiles:
        paths.add("buildtools")
    return sorted(paths)


def verify_sources(repo, release, system, profiles=("gpu", "cpu")):
    repo = Path(repo)
    if git_output(repo, "rev-parse", "--show-toplevel").casefold() != str(repo.resolve()).replace("\\", "/").casefold():
        # Git on Windows normally prints forward slashes.
        top = Path(git_output(repo, "rev-parse", "--show-toplevel")).resolve()
        if top != repo.resolve():
            raise RuntimeError("Skia source must be its Git worktree root")
    if git_output(repo, "rev-parse", "HEAD") != release.skia_commit:
        raise RuntimeError("Skia HEAD does not match the full commit in release.json")
    check_clean(repo)
    header = (repo / "include/core/SkMilestone.h").read_text(encoding="utf-8")
    if not re.search(rf"^\s*#define\s+SK_MILESTONE\s+{release.milestone}\s*$", header, re.M):
        raise RuntimeError("Skia milestone does not match release.json")
    definitions = dependency_definitions(repo)
    revisions = {}
    for relative in required_dependencies(system, profiles):
        definition = definitions.get(relative)
        if not isinstance(definition, str) or "@" not in definition:
            raise RuntimeError(f"Missing pinned upstream dependency: {relative}")
        expected = definition.rsplit("@", 1)[1]
        path = repo / relative
        if not (path / ".git").exists():
            raise RuntimeError(f"Dependency is not synced: {relative}")
        actual = git_output(path, "rev-parse", "HEAD")
        if actual != expected:
            raise RuntimeError(f"Dependency revision mismatch: {relative}")
        check_clean(path)
        revisions[relative] = actual
    return revisions


def recipe_digest():
    digest = hashlib.sha256()
    paths = [ROOT / "generate_skia_package.py", ROOT / "release_support.py"]
    for directory in ("configs", "patches", "cmake"):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file())
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode() + b"\0")
        digest.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode() + b"\0")
    return digest.hexdigest()


def command_version(command):
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    return (result.stdout + result.stderr).strip()


def toolchain_identity(repo, system):
    suffix = ".exe" if system == "windows" else ""
    compiler = "C:/Program Files/LLVM/bin/clang-cl.exe" if system == "windows" else "clang++"
    commands = {
        "compiler": [compiler, "--version"],
        "gn": [str(Path(repo) / "bin" / ("gn" + suffix)), "--version"],
        "ninja": ["ninja", "--version"],
    }
    if system == "windows":
        commands["linker"] = ["C:/Program Files/LLVM/bin/lld-link.exe", "--version"]
    elif system == "linux":
        commands.update(linker=["ld", "--version"], libc=["ldd", "--version"])
    if system == "darwin":
        commands.update(xcode=["xcodebuild", "-version"], sdk=["xcrun", "--show-sdk-version"],
                        linker_path=["xcrun", "--find", "ld"])
    identity = {name: command_version(command) for name, command in commands.items()}
    if system == "windows":
        import os
        identity.update({key: os.environ.get(key, "") for key in ("VCToolsVersion", "WindowsSDKVersion")})
        if not identity["VCToolsVersion"] or not identity["WindowsSDKVersion"]:
            raise RuntimeError("Run inside a Visual Studio Developer environment")
    return identity


def write_json(path, data):
    Path(path).write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stage_licenses(repo, package_dir, system, profile):
    repo, package_dir = Path(repo), Path(package_dir)
    destination = package_dir / "licenses"
    destination.mkdir()
    shutil.copy2(repo / "LICENSE", destination / "Skia-LICENSE")
    shutil.copy2(ROOT / "LICENSE", destination / "Packaging-MIT-LICENSE")
    # Copy all legal files from dependencies actually linked by this profile,
    # preserving relative paths. This includes nested ANGLE third-party notices
    # and FreeType's FTL/GPL alternatives, not just one top-level LICENSE.
    externals = required_dependencies(system, (profile,))
    for relative in externals:
        if relative == "buildtools":
            continue
        source = repo / relative
        copied = 0
        for path in sorted(source.rglob("*")):
            if not path.is_file() or ".git" in path.relative_to(source).parts:
                continue
            name = path.name.lower()
            legal = (name.startswith(("license", "licence", "copying", "copyright", "notice"))
                     or name in {"ftl.txt", "gplv2.txt", "bsd.txt", "gpl.txt", "authors"})
            if legal:
                target = destination / source.name / path.relative_to(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                copied += 1
        if not copied:
            raise RuntimeError(f"No license/notice files found for {relative}")
    # SkCMS is embedded directly in the Skia tree and carries its own license.
    shutil.copy2(repo / "LICENSE", destination / "SkCMS-LICENSE")
    (package_dir / "NOTICE.txt").write_text(
        "Skia Prebuilts\n\n"
        "Skia and bundled third-party code retain their original licenses.\n"
        "See licenses/ and include/third_party/ for license texts and notices.\n"
        "The MIT license applies only to this project's packaging code.\n",
        encoding="utf-8", newline="\n")


def stabilize_asan_headers(package_dir):
    # The binary is uninstrumented. Stop Skia's header-only ASan detection from
    # adding fPoisoned to TArray; compiler instrumentation in consumer code is
    # still enabled. Apply an audited, versioned patch to the STAGED copy only.
    patch = ROOT / "patches/0003-prebuilt-asan-abi.patch"
    lines = patch.read_text(encoding="utf-8").splitlines(keepends=True)
    hunks = [index for index, line in enumerate(lines) if line.startswith("@@ ")]
    if len(hunks) != 1:
        raise RuntimeError("The staged ABI patch must contain exactly one audited hunk")
    hunk = lines[hunks[0] + 1:]
    old = "".join(line[1:] for line in hunk if line.startswith((" ", "-")))
    new = "".join(line[1:] for line in hunk if line.startswith((" ", "+")))
    header = Path(package_dir) / "include/private/SkASAN.h"
    contents = header.read_text(encoding="utf-8")
    if not old or contents.count(old) != 1:
        raise RuntimeError("The staged ABI header does not match the audited patch preimage")
    header.write_text(contents.replace(old, new, 1), encoding="utf-8", newline="\n")
