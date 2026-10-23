import io
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
import zipfile

from tools.verify_packages import binary_architecture, extract_verified


class ArchiveVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="skia-archive-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_native_binary_architectures_are_read_from_file_headers(self):
        for system, machines in [("windows", (0x8664, 0xAA64)), ("linux", (62, 183)),
                                 ("darwin", (0x1000007, 0x100000C))]:
            for cpu, machine in zip(("x64", "arm64"), machines):
                with self.subTest(system=system, cpu=cpu):
                    header = bytearray(128)
                    if system == "windows":
                        header[:2] = b"MZ"
                        struct.pack_into("<I", header, 60, 64)
                        header[64:68] = b"PE\0\0"
                        struct.pack_into("<H", header, 68, machine)
                    elif system == "linux":
                        header[:6] = b"\x7fELF\x02\x01"
                        struct.pack_into("<H", header, 18, machine)
                    else:
                        header[:4] = b"\xcf\xfa\xed\xfe"
                        struct.pack_into("<I", header, 4, machine)
                    path = self.root / "binary"
                    path.write_bytes(header)
                    self.assertEqual(binary_architecture(path, system), cpu)

    def test_invalid_native_binaries_are_rejected(self):
        path = self.root / "invalid"
        path.write_bytes(b"invalid" * 20)
        for system in ("windows", "linux", "darwin"):
            with self.subTest(system=system), self.assertRaises(ValueError):
                binary_architecture(path, system)

    def test_zip_cannot_escape_or_use_a_different_root(self):
        for name in ("../outside", "/absolute", "other/file", "package/../outside", "package\\file"):
            archive = self.root / "package.zip"
            with zipfile.ZipFile(archive, "w") as packed:
                packed.writestr(name, "data")
            if "\\" in name:
                archive.write_bytes(archive.read_bytes().replace(b"package/file", b"package\\file"))
            with self.subTest(name=name), self.assertRaises(ValueError):
                extract_verified(archive, self.root / "extract", "package")
        self.assertFalse((self.root / "outside").exists())

    def test_tar_rejects_symlinks_and_hardlinks(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            archive = self.root / "package.tar.gz"
            with tarfile.open(archive, "w:gz") as packed:
                item = tarfile.TarInfo("package/link")
                item.type = kind
                item.linkname = "../../outside"
                packed.addfile(item)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                extract_verified(archive, self.root / "extract", "package")

    def test_regular_archive_extracts(self):
        archive = self.root / "package.tar.gz"
        with tarfile.open(archive, "w:gz") as packed:
            item = tarfile.TarInfo("package/NOTICE.txt")
            item.size = 4
            packed.addfile(item, io.BytesIO(b"data"))
        extract_verified(archive, self.root / "extract", "package")
        self.assertEqual((self.root / "extract/package/NOTICE.txt").read_text(), "data")
