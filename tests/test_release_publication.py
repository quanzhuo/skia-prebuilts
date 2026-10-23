import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

import release_support as release
from tools import publish_release as publication


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skia-publication-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.info = release.load_release()
        self.archives = []
        for platform in publication.PLATFORMS:
            reports = []
            for profile in ("cpu", "gpu"):
                stem = f"skia-{self.info.tag}-{profile}-{platform}"
                archive = self.root / (stem + (".zip" if platform.startswith("windows-") else ".tar.gz"))
                manifest = dict(release_tag=self.info.tag, skia_commit=self.info.skia_commit,
                                cmake_version=self.info.cmake_version, profile=profile, platform=platform,
                                recipe_sha256="a" * 64)
                payload = json.dumps(manifest).encode()
                if archive.suffix == ".zip":
                    with zipfile.ZipFile(archive, "w") as packed:
                        packed.writestr(stem + "/build-manifest.json", payload)
                else:
                    with tarfile.open(archive, "w:gz") as packed:
                        member = tarfile.TarInfo(stem + "/build-manifest.json")
                        member.size = len(payload)
                        packed.addfile(member, io.BytesIO(payload))
                digest = release.file_digest(archive)
                archive.with_name(archive.name + ".sha256").write_text(f"{digest}  {archive.name}\n")
                modes = ["normal", "asan-layout"] + ([] if platform == "windows-arm64" else ["asan"])
                reports.append(dict(profile=profile, platform=platform, archive=archive.name, sha256=digest, tests=modes))
                self.archives.append(archive)
            release.write_json(self.root / f"verification-{platform}.json", reports)

    def test_complete_release_has_twelve_archives_and_two_metadata_assets(self):
        info, assets = publication.prepare(self.root)
        self.assertEqual(info, self.info)
        self.assertEqual(len(assets), 14)
        manifest = json.loads((self.root / "release-manifest.json").read_text())
        self.assertEqual(len(manifest["packages"]), 12)
        self.assertEqual(len((self.root / "SHA256SUMS").read_text().splitlines()), 12)

    def test_missing_platform_blocks_assembly(self):
        (self.root / "verification-macos-arm64.json").unlink()
        with self.assertRaises(FileNotFoundError):
            publication.prepare(self.root)

    def test_modified_archive_blocks_assembly(self):
        self.archives[0].write_bytes(self.archives[0].read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "Verification"):
            publication.prepare(self.root)

    def test_missing_native_test_blocks_assembly(self):
        path = self.root / "verification-linux-arm64.json"
        data = json.loads(path.read_text())
        data[0]["tests"].remove("asan")
        release.write_json(path, data)
        with self.assertRaisesRegex(ValueError, "Verification"):
            publication.prepare(self.root)

    def test_unexpected_archive_blocks_assembly(self):
        (self.root / "unexpected.zip").write_bytes(b"extra")
        with self.assertRaisesRegex(ValueError, "exactly"):
            publication.prepare(self.root)

    def test_published_release_is_never_overwritten(self):
        with mock.patch.object(publication, "api", return_value={"draft": False}), \
             mock.patch.object(publication.subprocess, "run") as mutation:
            with self.assertRaisesRegex(RuntimeError, "already published"):
                publication.publish(self.root, "owner/repo", "b" * 40)
            mutation.assert_not_called()

    def test_revision_cannot_be_reused_for_another_upstream_commit(self):
        history = [[dict(tag_name="m152-abcdef0123-r1")]]
        with mock.patch.object(publication, "api", return_value=None), \
             mock.patch.object(publication.subprocess, "check_output", return_value=json.dumps(history)), \
             mock.patch.object(publication.subprocess, "run") as mutation:
            with self.assertRaisesRegex(RuntimeError, "monotonically"):
                publication.publish(self.root, "owner/repo", "b" * 40)
            mutation.assert_not_called()

    def test_existing_draft_with_different_commit_is_rejected(self):
        with mock.patch.object(publication, "api", return_value=dict(draft=True, target_commitish="c" * 40)), \
             mock.patch.object(publication.subprocess, "run") as mutation:
            with self.assertRaisesRegex(RuntimeError, "different packaging commit"):
                publication.publish(self.root, "owner/repo", "b" * 40)
            mutation.assert_not_called()
