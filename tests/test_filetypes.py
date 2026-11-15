import io
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from appdeb import filetypes


def appimage_header() -> bytes:
    return b"\x7fELF" + b"\x00" * 4 + b"AI\x02" + b"\x00" * 32


class FileTypeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-files-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, name: str, data: bytes = b"x") -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def make_tarball(self, name: str = "app.tar.gz") -> Path:
        path = self.tmp / name
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo("App/run.sh")
            info.size = 2
            archive.addfile(info, io.BytesIO(b"#\n"))
        return path

    def test_appimage_magic_and_suffix(self):
        good = self.write("Good.AppImage", appimage_header())
        self.assertEqual(filetypes.kind(good), "appimage")
        self.assertEqual(filetypes.kind(self.write("bad.AppImage", b"nope")), None)
        self.assertEqual(filetypes.kind(self.write("plain.bin", appimage_header())), None)

    def test_tarball_requires_real_archive(self):
        good = self.make_tarball()
        self.assertEqual(filetypes.kind(good), "tarball")
        self.assertEqual(filetypes.kind(self.make_tarball("App.tgz")), "tarball")
        self.assertIsNone(filetypes.kind(self.write("fake.tar.gz", b"not a tar")))

    def test_deb_and_flatpak_route_by_suffix(self):
        self.assertEqual(filetypes.kind(self.write("pkg.deb")), "deb")
        self.assertEqual(filetypes.kind(self.write("app.flatpak")), "flatpak")
        self.assertEqual(filetypes.kind(self.write("app.flatpakref")), "flatpak")

    def test_unsupported_formats(self):
        for name in ("archive.zip", "archive.tar.xz", "image.png", "notes.txt"):
            self.assertIsNone(filetypes.kind(self.write(name)), name)

    def test_supported_suffixes_cover_every_kind(self):
        suffixes = filetypes.supported_suffixes()
        for expected in (".AppImage", ".deb", ".flatpak", ".flatpakref", ".tar.gz", ".tgz"):
            self.assertIn(expected, suffixes)

    def test_missing_file_is_unsupported(self):
        self.assertIsNone(filetypes.kind(self.tmp / "nope.AppImage"))


if __name__ == "__main__":
    unittest.main()
