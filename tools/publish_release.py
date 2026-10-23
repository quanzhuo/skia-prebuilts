#!/usr/bin/env python3
"""Assemble a complete verified release; publish only when explicitly requested."""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_support as release


PLATFORMS = ("windows-x64", "windows-arm64", "linux-x64", "linux-arm64", "macos-x64", "macos-arm64")


def prepare(dist):
    dist = Path(dist)
    info = release.load_release()
    records = []
    recipe = None
    assets = []
    for platform in PLATFORMS:
        report = json.loads((dist / f"verification-{platform}.json").read_text(encoding="utf-8"))
        if len(report) != 2 or {item["profile"] for item in report} != {"cpu", "gpu"}:
            raise ValueError(f"Incomplete verification report: {platform}")
        for profile in ("cpu", "gpu"):
            stem = f"skia-{info.tag}-{profile}-{platform}"
            archive = dist / (stem + (".zip" if platform.startswith("windows-") else ".tar.gz"))
            digest = release.file_digest(archive)
            item = next(item for item in report if item["profile"] == profile)
            modes = {"normal", "asan-layout"}
            if platform != "windows-arm64":
                modes.add("asan")
            if (item.get("archive") != archive.name or item.get("sha256") != digest
                    or item.get("platform") != platform or set(item.get("tests", [])) != modes):
                raise ValueError(f"Verification does not match archive: {archive.name}")
            if archive.with_name(archive.name + ".sha256").read_text(encoding="utf-8") != f"{digest}  {archive.name}\n":
                raise ValueError(f"Archive checksum mismatch: {archive.name}")
            member = stem + "/build-manifest.json"
            if archive.suffix == ".zip":
                with zipfile.ZipFile(archive) as packed:
                    manifest = json.loads(packed.read(member))
            else:
                with tarfile.open(archive) as packed:
                    manifest = json.load(packed.extractfile(member))
            if (manifest.get("release_tag") != info.tag or manifest.get("skia_commit") != info.skia_commit
                    or manifest.get("cmake_version") != info.cmake_version
                    or manifest.get("profile") != profile or manifest.get("platform") != platform):
                raise ValueError(f"Release identity mismatch: {archive.name}")
            if not re.fullmatch(r"[0-9a-f]{64}", manifest.get("recipe_sha256", "")):
                raise ValueError("Missing recipe digest")
            if recipe is None:
                recipe = manifest["recipe_sha256"]
            elif recipe != manifest["recipe_sha256"]:
                raise ValueError("Packages were built from different packaging recipes")
            records.append(dict(archive=archive.name, sha256=digest, verification=item, build=manifest))
            assets.append(archive)
    expected = {asset.name for asset in assets}
    found = {path.name for path in dist.iterdir() if path.name.endswith((".zip", ".tar.gz"))}
    if found != expected:
        raise ValueError("Release directory must contain exactly the twelve expected archives")
    sums = dist / "SHA256SUMS"
    sums.write_text("".join(f"{item['sha256']}  {item['archive']}\n" for item in sorted(records, key=lambda item: item["archive"])),
                    encoding="utf-8", newline="\n")
    manifest = dist / "release-manifest.json"
    release.write_json(manifest, dict(schema_version=1, release_tag=info.tag, skia_commit=info.skia_commit,
                                     cmake_version=info.cmake_version, packages=records))
    return info, [*assets, sums, manifest]


def api(repository, endpoint):
    result = subprocess.run(["gh", "api", f"repos/{repository}/{endpoint}"], capture_output=True, text=True)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout)


def publish(dist, repository, commit):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("An explicit owner/repository and full packaging commit are required")
    info, assets = prepare(dist)
    existing = api(repository, f"releases/tags/{info.tag}")
    if existing is not None and not existing["draft"]:
        raise RuntimeError("Release is already published; increment revision instead of replacing it")
    if existing is not None and existing.get("target_commitish") != commit:
        raise RuntimeError("Existing draft belongs to a different packaging commit")
    # Revisions remain unique even when the upstream hash changes.
    history = subprocess.check_output(["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"], text=True)
    for page in json.loads(history):
        for previous in page:
            match = re.fullmatch(rf"m{info.milestone}-[0-9a-f]{{10}}-r([1-9][0-9]*)", previous["tag_name"])
            if match and previous["tag_name"] != info.tag and int(match.group(1)) >= info.revision:
                raise RuntimeError("Release revision must increase monotonically within its milestone")
    ref = api(repository, f"git/ref/tags/{info.tag}")
    if ref is not None:
        target = ref["object"]
        if target["type"] == "tag":
            target = api(repository, f"git/tags/{target['sha']}")["object"]
        if target["type"] != "commit" or target["sha"] != commit:
            raise RuntimeError("Release tag belongs to a different packaging commit")
    notes = Path(dist) / "release-notes.md"
    notes.write_text(
        f"Skia {info.milestone}, packaging revision {info.revision}.\n\n"
        f"Upstream commit: `{info.skia_commit}`.\n\n"
        "Twelve native CPU/GPU packages for Windows, Linux and macOS, each on x64 and ARM64.\n"
        "All packages passed native consumer, relocation and ABI checks. See release-manifest.json\n"
        "for exact toolchains and sanitizer tests. Verify downloads against SHA256SUMS.\n\n"
        "GPU linkage tests use a Ganesh mock context; hardware GPU behavior is not certified by hosted CI.\n",
        encoding="utf-8", newline="\n")
    if existing is None:
        subprocess.run(["gh", "release", "create", info.tag, "--repo", repository, "--target", commit,
                        "--draft", "--title", info.tag, "--notes-file", str(notes)], check=True)
    subprocess.run(["gh", "release", "upload", info.tag, "--repo", repository, "--clobber", *map(str, assets)], check=True)
    uploaded = api(repository, f"releases/tags/{info.tag}")
    if uploaded is None or {asset["name"] for asset in uploaded["assets"]} != {path.name for path in assets}:
        raise RuntimeError("Draft does not contain exactly the complete approved asset set")
    subprocess.run(["gh", "release", "edit", info.tag, "--repo", repository, "--draft=false", "--latest"], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--repository")
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.publish:
        publish(args.dist, args.repository or "", args.commit or "")
    else:
        prepare(args.dist)
