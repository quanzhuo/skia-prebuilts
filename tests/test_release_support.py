import contextlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import release_support as release


class ReleaseIdentityTests(unittest.TestCase):
    def test_release_identity_has_one_canonical_spelling(self):
        info = release.Release(152, 1, release.load_release().skia_commit)
        self.assertEqual(info.tag, "m152-0873ec164a-r1")
        self.assertEqual(info.cmake_version, "152.1.0")
        self.assertEqual(len(info.skia_commit), 40)
        self.assertNotEqual(info.tag, release.Release(152, 2, info.skia_commit).tag)
        self.assertNotEqual(info.cmake_version, release.Release(152, 2, info.skia_commit).cmake_version)

    def test_invalid_identifiers_are_rejected(self):
        for milestone, revision, commit in [(0, 1, "a" * 40), (152, 0, "a" * 40),
                                             (True, 1, "a" * 40), (152, 1, "A" * 40),
                                             (152, 1, "abc1234"), (152, "1", "a" * 40)]:
            with self.subTest(values=(milestone, revision, commit)), self.assertRaises(ValueError):
                release.Release(milestone, revision, commit)

    def test_unknown_release_fields_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "release.json"
            path.write_text(json.dumps(dict(milestone=152, revision=1, skia_commit="a" * 40, extra=True)))
            with self.assertRaises(ValueError):
                release.load_release(path)

    def test_staged_header_patch_preserves_original_and_fixes_asan_detection(self):
        source = Path(__file__).parent / "fixtures/SkASAN.h"
        original = source.read_bytes()
        with tempfile.TemporaryDirectory() as temporary:
            header = Path(temporary) / "include/private/SkASAN.h"
            header.parent.mkdir(parents=True)
            shutil.copy2(source, header)
            release.stabilize_asan_headers(temporary)
            contents = header.read_text()
            self.assertNotIn("#define SK_SANITIZE_ADDRESS", contents)
            self.assertIn("#error", contents)
            self.assertEqual(source.read_bytes(), original)

    def test_header_patch_is_not_silently_skipped_inside_a_parent_git_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            subprocess.run(["git", "init", "--quiet", temporary], check=True)
            staging = Path(temporary) / "dist/staging/package"
            header = staging / "include/private/SkASAN.h"
            header.parent.mkdir(parents=True)
            shutil.copy2(Path(__file__).parent / "fixtures/SkASAN.h", header)
            release.stabilize_asan_headers(staging)
            self.assertNotIn("#define SK_SANITIZE_ADDRESS", header.read_text())


class VerifiedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skia-verified-source-")
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "skia"
        self.repo.mkdir()
        (self.repo / "include/core").mkdir(parents=True)
        (self.repo / "include/core/SkMilestone.h").write_text("#define SK_MILESTONE 152\n")
        (self.repo / ".gitignore").write_text("third_party/externals/\nout/\n")
        (self.repo / "tools").mkdir()
        (self.repo / "tools/git-sync-deps").write_text(
            'import json\nfrom pathlib import Path\ndef parse_file_to_dict(path):\n    return json.loads(Path(path).read_text())\n')
        definitions = {}
        for relative in release.required_dependencies("darwin", ("cpu",)):
            path = self.repo / relative
            path.mkdir(parents=True)
            (path / "LICENSE").write_text("license fixture")
            self.init(path)
            definitions[relative] = "https://example.invalid/source.git@" + release.git_output(path, "rev-parse", "HEAD")
        (self.repo / "DEPS").write_text(json.dumps({"deps": definitions}))
        self.init(self.repo)
        self.info = release.Release(152, 1, release.git_output(self.repo, "rev-parse", "HEAD"))

    @staticmethod
    def init(path):
        subprocess.run(["git", "init", "--quiet", str(path)], check=True)
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "--quiet", "-m", "fixture"], cwd=path, check=True)

    def verify(self):
        return release.verify_sources(self.repo, self.info, "darwin", ("cpu",))

    def test_clean_pinned_source_and_dependencies_are_accepted(self):
        revisions = self.verify()
        self.assertEqual(set(revisions), set(release.required_dependencies("darwin", ("cpu",))))
        (self.repo / "out").mkdir()
        (self.repo / "out/build.log").write_text("ignored build output")
        self.verify()

    def test_dirty_header_is_rejected(self):
        (self.repo / "include/core/SkMilestone.h").write_text("#define SK_MILESTONE 152\n// changed\n")
        with self.assertRaisesRegex(RuntimeError, "dirty"):
            self.verify()

    def test_untracked_source_is_rejected(self):
        (self.repo / "include/core/untracked.h").write_text("untracked")
        with self.assertRaisesRegex(RuntimeError, "dirty"):
            self.verify()

    def test_wrong_full_source_commit_is_rejected(self):
        info = release.Release(152, 1, "0" * 40)
        with self.assertRaisesRegex(RuntimeError, "full commit"):
            release.verify_sources(self.repo, info, "darwin", ("cpu",))

    def test_dirty_dependency_is_rejected(self):
        path = self.repo / release.required_dependencies("darwin", ("cpu",))[0]
        (path / "LICENSE").write_text("changed")
        with self.assertRaisesRegex(RuntimeError, "dirty"):
            self.verify()

    def test_wrong_dependency_revision_is_rejected(self):
        path = self.repo / release.required_dependencies("darwin", ("cpu",))[0]
        (path / "LICENSE").write_text("changed")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                        "commit", "--quiet", "-m", "different revision"], cwd=path, check=True)
        with self.assertRaisesRegex(RuntimeError, "revision mismatch"):
            self.verify()

    def test_missing_dependency_is_rejected(self):
        relative = release.required_dependencies("darwin", ("cpu",))[0]
        path = self.repo / relative
        moved = self.repo / "out/missing"
        moved.parent.mkdir()
        path.rename(moved)
        with self.assertRaisesRegex(RuntimeError, "not synced"):
            self.verify()
