import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from appdeb import apps, config
from appdeb.backends import appimage

REAL_BUNDLES = [
    Path("/home/kaan/Apps/filmcraft-0.2.1-linux-x86_64.AppImage"),
    Path("/home/kaan/Apps/Cura.AppImage"),
]


def fake_header(appimage_type: bytes = b"AI\x02") -> bytes:
    return b"\x7fELF" + b"\x00" * 4 + appimage_type + b"\x00" * 32


class AppImageDetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-appimage-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_is_appimage_accepts_magic(self):
        for appimage_type in (b"AI\x01", b"AI\x02"):
            path = self.tmp / f"type{appimage_type[2]}.AppImage"
            path.write_bytes(fake_header(appimage_type))
            self.assertTrue(appimage.is_appimage(path), appimage_type)

    def test_is_appimage_rejects_plain_elf_and_empty(self):
        plain = self.tmp / "plain.bin"
        plain.write_bytes(b"\x7fELF" + b"\x00" * 40)
        self.assertFalse(appimage.is_appimage(plain))

        empty = self.tmp / "empty.bin"
        empty.write_bytes(b"")
        self.assertFalse(appimage.is_appimage(empty))

        missing = self.tmp / "missing.bin"
        self.assertFalse(appimage.is_appimage(missing))

    def test_find_squashfs_offset_ignores_false_hits(self):
        path = self.tmp / "fake.AppImage"
        path.write_bytes(fake_header() + b"hsqs" + b"\x00" * 64 + b"hsqs" + b"\x00" * 64)

        fake_run = mock.Mock()
        fake_run.return_value = mock.Mock(returncode=1, output="", lines=[], ok=False)
        with mock.patch.object(appimage, "runner", mock.Mock(run=fake_run, capture=fake_run)):
            started = time.monotonic()
            self.assertIsNone(appimage.find_squashfs_offset(path))
            self.assertLess(time.monotonic() - started, 10)
        self.assertGreaterEqual(fake_run.call_count, 2)


class ParseSizeTests(unittest.TestCase):
    def test_human_sizes(self):
        self.assertEqual(apps.parse_size("1.3 GB"), 1300000000)
        self.assertEqual(apps.parse_size("29.6 MB"), 29600000)
        self.assertEqual(apps.parse_size("340.3 MB"), 340300000)
        self.assertEqual(apps.parse_size("2.0 MB"), 2000000)
        self.assertEqual(apps.parse_size("12 kB"), 12000)
        self.assertEqual(apps.parse_size("1.3\u00a0GB"), 1300000000)
        self.assertIsNone(apps.parse_size(""))
        self.assertIsNone(apps.parse_size("unknown"))

    def test_human_size_formatting(self):
        self.assertEqual(apps.human_size(0), "")
        self.assertEqual(apps.human_size(1200), "1.2 KB")
        self.assertEqual(apps.human_size(1300000000), "1.3 GB")


@unittest.skipUnless(REAL_BUNDLES[0].exists(), "no real AppImage available")
class RealBundleTests(unittest.TestCase):
    def test_metadata_extraction_does_not_raise(self):
        meta = appimage.extract_metadata(REAL_BUNDLES[0])
        if meta is not None:
            self.assertTrue(meta.name)
        self.assertTrue(appimage.is_appimage(REAL_BUNDLES[0]))

    def test_cura_superblock_offset(self):
        cura = REAL_BUNDLES[1]
        if not cura.exists() or not shutil.which("unsquashfs"):
            self.skipTest("Cura bundle or unsquashfs unavailable")
        self.assertEqual(appimage.find_squashfs_offset(cura), 944632)


if __name__ == "__main__":
    unittest.main()
