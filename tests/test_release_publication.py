import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import subprocess
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
        self.recipe = release.recipe_digest()
        self.archives = []
        for platform in publication.PLATFORMS:
            reports = []
            for profile in ("cpu", "gpu"):
                stem = f"skia-{self.info.tag}-{profile}-{platform}"
                archive = self.root / (stem + (".zip" if platform.startswith("windows-") else ".tar.gz"))
                manifest = dict(release_tag=self.info.tag, skia_commit=self.info.skia_commit,
                                cmake_version=self.info.cmake_version, profile=profile, platform=platform,
                                recipe_sha256=self.recipe)
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

    def test_artifacts_from_another_build_recipe_are_rejected(self):
        with mock.patch.object(publication.release, "recipe_digest", return_value="b" * 64):
            with self.assertRaisesRegex(ValueError, "current build recipe"):
                publication.prepare(self.root)

    def test_draft_lookup_uses_authenticated_listing_when_tag_endpoint_returns_404(self):
        draft = dict(id=123, tag_name=self.info.tag, draft=True)
        with mock.patch.object(publication, "api", return_value=None), \
             mock.patch.object(publication, "release_history", return_value=[draft]):
            self.assertEqual(publication.release_by_tag("owner/repo", self.info.tag), draft)

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

    def test_draft_assets_are_verified_by_release_id_before_publication(self):
        _, assets = publication.prepare(self.root)
        draft = dict(id=123, draft=True, target_commitish="b" * 40)
        def response(_, endpoint):
            if endpoint.startswith("git/ref/"):
                return None
            self.assertEqual(endpoint, "releases/123")
            return dict(assets=[dict(name=path.name) for path in assets])
        with mock.patch.object(publication, "release_by_tag", return_value=draft), \
             mock.patch.object(publication, "release_history", return_value=[]), \
             mock.patch.object(publication, "api", side_effect=response), \
             mock.patch.object(publication.subprocess, "run") as mutation:
            publication.publish(self.root, "owner/repo", "b" * 40)
            self.assertIn("--draft=false", mutation.call_args.args[0])

    def test_incomplete_draft_is_never_published(self):
        draft = dict(id=123, draft=True, target_commitish="b" * 40)
        with mock.patch.object(publication, "release_by_tag", return_value=draft), \
             mock.patch.object(publication, "release_history", return_value=[]), \
             mock.patch.object(publication, "api", side_effect=[None, dict(assets=[])]), \
             mock.patch.object(publication.subprocess, "run") as mutation:
            with self.assertRaisesRegex(RuntimeError, "complete approved asset set"):
                publication.publish(self.root, "owner/repo", "b" * 40)
            self.assertFalse(any("--draft=false" in call.args[0] for call in mutation.call_args_list))

    def test_new_draft_id_comes_directly_from_creation_response(self):
        _, assets = publication.prepare(self.root)
        draft = dict(id=456, draft=True, target_commitish="b" * 40)
        def response(_, endpoint):
            if endpoint.startswith("git/ref/"):
                return None
            self.assertEqual(endpoint, "releases/456")
            return dict(assets=[dict(name=path.name) for path in assets])
        with mock.patch.object(publication, "release_by_tag", return_value=None), \
             mock.patch.object(publication, "release_history", return_value=[]), \
             mock.patch.object(publication, "api", side_effect=response), \
             mock.patch.object(publication.subprocess, "run", return_value=subprocess.CompletedProcess(
                 [], 0, stdout=json.dumps(draft))) as mutation:
            publication.publish(self.root, "owner/repo", "b" * 40)
            self.assertTrue(json.loads(mutation.call_args_list[0].kwargs["input"])["draft"])
            self.assertIn("--draft=false", mutation.call_args.args[0])


class VerifiedBuildContextTests(unittest.TestCase):
    def setUp(self):
        self.run = dict(status="completed", head_branch="main", path=".github/workflows/release.yml",
                        head_repository=dict(full_name="owner/repo"), head_sha="c" * 40, event="workflow_dispatch")
        self.jobs = [dict(name=name, conclusion="success") for name in publication.PLATFORMS]

    def verify(self):
        with mock.patch.object(publication, "api", return_value=self.run), \
             mock.patch.object(publication.subprocess, "check_output", return_value=json.dumps([dict(jobs=self.jobs)])):
            return publication.verified_build_commit("owner/repo", "123")

    def test_complete_native_matrix_can_resume_publication(self):
        self.assertEqual(self.verify(), "c" * 40)

    def test_missing_or_failed_platform_prevents_reuse(self):
        self.jobs[0]["conclusion"] = "failure"
        with self.assertRaisesRegex(RuntimeError, "did not succeed"):
            self.verify()
        self.jobs.pop(0)
        with self.assertRaisesRegex(RuntimeError, "did not succeed"):
            self.verify()

    def test_fork_or_untrusted_branch_cannot_supply_release_artifacts(self):
        self.run["head_repository"]["full_name"] = "fork/repo"
        with self.assertRaisesRegex(RuntimeError, "main-branch"):
            self.verify()
        self.run["head_repository"]["full_name"] = "owner/repo"
        self.run["head_branch"] = "feature"
        with self.assertRaisesRegex(RuntimeError, "main-branch"):
            self.verify()

    def test_invalid_run_id_is_rejected_before_network_access(self):
        with mock.patch.object(publication, "api") as network:
            with self.assertRaises(ValueError):
                publication.verified_build_commit("owner/repo", "../123")
            network.assert_not_called()
