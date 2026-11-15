import io
import os
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from appdeb import config, db, desktop
from appdeb.backends import runner, tarball


def make_png(path: Path, size: int = 32) -> None:
    Image.new("RGBA", (size, size), (53, 132, 228, 255)).save(path)


def build_archive(
    target: Path,
    root_name: str = "Fixture-1.0",
    name: str = "Fixture",
    executable: str = "fixture",
    with_desktop: bool = True,
    with_icon: bool = True,
    extra_members: list[tuple[str, bytes]] | None = None,
) -> Path:
    with tempfile.TemporaryDirectory() as stage:
        root = Path(stage) / root_name
        root.mkdir()
        script = root / executable
        script.write_text("#!/bin/sh\nprintf 'ok\\n'\n")
        script.chmod(0o755)
        if with_icon:
            make_png(root / "fixture.png")
        if with_desktop:
            (root / "fixture.desktop").write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={name}\n"
                "Comment=Fixture app\n"
                f"Exec={executable}\n"
                "Icon=fixture\n"
                "Categories=Utility;\n",
                encoding="utf-8",
            )
        with tarfile.open(target, "w:gz") as archive:
            archive.add(root, arcname=root_name)
            for member_name, payload in extra_members or []:
                data = payload if isinstance(payload, bytes) else str(payload).encode()
                info = tarfile.TarInfo(member_name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return Path(target)


class TarballTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="appdeb-tar-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._env = {}
        for key, sub in (
            ("HOME", "home"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_DATA_DIRS", "system/share"),
        ):
            self._env[key] = os.environ.get(key)
            os.environ[key] = str(self.tmp / sub)
        config.ensure_dirs()
        db.init()
        (self.tmp / "home" / "Desktop").mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def archive(self, **kwargs) -> Path:
        return build_archive(self.tmp / "Fixture-1.0.tar.gz", **kwargs)


class DetectionTests(TarballTestCase):
    def test_is_tarball_accepts_gz_only(self):
        good = self.archive()
        self.assertTrue(tarball.is_tarball(good))

        plain = self.tmp / "plain.tar.gz"
        plain.write_text("not an archive")
        self.assertFalse(tarball.is_tarball(plain))

        for suffix in (".tar.xz", ".zip", ".tar"):
            other = self.tmp / f"thing{suffix}"
            other.write_bytes(b"\x00")
            self.assertFalse(tarball.is_tarball(other), suffix)

    def test_inspect_detects_name_executable_and_icon(self):
        meta = tarball.inspect(self.archive())
        self.assertEqual(meta.name, "Fixture")
        self.assertTrue(meta.executable_rel.endswith("fixture"))
        self.assertTrue(meta.icon_rel.endswith("fixture.png"))
        self.assertTrue(meta.desktop_rel.endswith("fixture.desktop"))
        self.assertIn("fixture", meta.executable_candidates)

    def test_inspect_recovers_without_desktop_file(self):
        meta = tarball.inspect(self.archive(with_desktop=False, with_icon=False))
        self.assertEqual(meta.name, "Fixture")
        self.assertTrue(meta.executable_rel.endswith("fixture"))


class InstallTests(TarballTestCase):
    def test_install_one_click_uses_managed_store(self):
        source = self.archive()
        app = tarball.install(source)

        bundle = config.tarball_store() / "fixture" / "fixture"
        self.assertTrue(bundle.is_file())
        self.assertTrue(os.access(bundle, os.X_OK))

        desktop_file = config.user_applications_dir() / "appdeb-fixture.desktop"
        self.assertEqual(app.entry.path, desktop_file)
        self.assertTrue(desktop_file.is_file())
        self.assertEqual(desktop.validate(desktop_file), [])

        kf = desktop.load_keyfile(desktop_file)
        exec_line = kf.get_string("Desktop Entry", "Exec")
        self.assertTrue(exec_line.startswith("/"))
        self.assertTrue(Path(exec_line.split()[0]).is_file())
        self.assertEqual(kf.get_string("Desktop Entry", "X-Appdeb-Kind"), "tarball")
        self.assertEqual(
            kf.get_string("Desktop Entry", "Path"), str(config.tarball_store() / "fixture")
        )
        self.assertTrue((config.user_icon_dir() / "256x256" / "apps").is_dir())

        row = db.get("appdeb-fixture.desktop")
        self.assertEqual(row["kind"], "tarball")
        self.assertEqual(Path(row["install_path"]), config.tarball_store() / "fixture")
        self.assertEqual(row["source_ref"], str(source))

    def test_install_to_custom_destination(self):
        source = self.archive()
        custom = self.tmp / "custom"
        custom.mkdir()
        app = tarball.install(source, dest=custom)

        self.assertTrue((custom / "fixture" / "fixture").is_file())
        kf = desktop.load_keyfile(app.entry.path)
        self.assertEqual(kf.get_string("Desktop Entry", "X-Appdeb-InstallPath"), str(custom / "fixture"))
        self.assertFalse((config.tarball_store() / "fixture").exists())

    def test_install_with_explicit_executable(self):
        with tempfile.TemporaryDirectory() as stage:
            root = Path(stage) / "Fixture-1.0"
            (root / "bin").mkdir(parents=True)
            (root / "bin" / "fixture-real").write_text("#!/bin/sh\nexit 0\n")
            (root / "bin" / "fixture-real").chmod(0o755)
            (root / "README").write_text("docs")
            nested = self.tmp / "nested.tar.gz"
            with tarfile.open(nested, "w:gz") as archive:
                archive.add(root, arcname="Fixture-1.0")
        app = tarball.install(nested, executable_rel="bin/fixture-real")
        kf = desktop.load_keyfile(app.entry.path)
        self.assertTrue(kf.get_string("Desktop Entry", "Exec").endswith("fixture-real"))

    def test_install_spec_custom_destination_and_name(self):
        source = self.archive(with_desktop=False)
        custom = self.tmp / "picked"
        custom.mkdir()
        app = tarball.install_spec(
            source,
            name="My Fixture",
            executable_rel="fixture",
            icon_source=None,
            dest=custom,
            comment="custom",
            categories="Development;",
            wm_class="fixture",
        )
        kf = desktop.load_keyfile(app.entry.path)
        self.assertEqual(kf.get_string("Desktop Entry", "Name"), "My Fixture")
        self.assertEqual(kf.get_string("Desktop Entry", "StartupWMClass"), "fixture")
        self.assertEqual(kf.get_string("Desktop Entry", "Categories"), "Development;")
        self.assertTrue((custom / "my-fixture" / "fixture").is_file())


class SafetyTests(TarballTestCase):
    def test_path_traversal_is_rejected(self):
        source = build_archive(
            self.tmp / "evil.tar.gz",
            extra_members=[("../escape", b"pwned")],
        )
        with self.assertRaises(runner.BackendError):
            tarball.install(source)
        self.assertFalse((self.tmp / "escape").exists())
        self.assertFalse((config.tarball_store() / "escape").exists())

    def test_absolute_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as stage:
            root = Path(stage) / "Evil-1.0"
            root.mkdir()
            (root / "run.sh").write_text("#!/bin/sh\n")
            (root / "run.sh").chmod(0o755)
            (root / "link").symlink_to("/etc/passwd")
            archive_path = self.tmp / "evil-link.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(root, arcname="Evil-1.0")
        with self.assertRaises(runner.BackendError):
            tarball.install(archive_path)

    def test_archive_without_executable_reports_reason(self):
        with tempfile.TemporaryDirectory() as stage:
            root = Path(stage) / "Docs-1.0"
            root.mkdir()
            (root / "README").write_text("nothing to run")
            archive_path = self.tmp / "Docs-1.0.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(root, arcname="Docs-1.0")
        with self.assertRaises(runner.BackendError) as ctx:
            tarball.install(archive_path)
        self.assertIn("no executable found", str(ctx.exception))


class LifecycleTests(TarballTestCase):
    def test_replace_keeps_desktop_id_and_backs_up(self):
        source = self.archive()
        app = tarball.install(source)
        desktop_id = app.entry.id

        with tempfile.TemporaryDirectory() as stage:
            root = Path(stage) / "Fixture-2.0"
            root.mkdir()
            (root / "fixture").write_text("#!/bin/sh\nprintf 'v2\\n'\n")
            (root / "fixture").chmod(0o755)
            make_png(root / "fixture.png")
            (root / "fixture.desktop").write_text(
                "[Desktop Entry]\nType=Application\nName=Fixture\nExec=fixture\nIcon=fixture\n",
                encoding="utf-8",
            )
            newer = self.tmp / "Fixture-2.0.tar.gz"
            with tarfile.open(newer, "w:gz") as archive:
                archive.add(root, arcname="Fixture-2.0")

        replaced = tarball.replace(app, newer)
        self.assertEqual(replaced.entry.id, desktop_id)
        backups = list(config.tarball_store().glob("fixture.bak-*"))
        self.assertTrue(backups, "expected a backup of the previous install directory")
        self.assertIn("v2", (config.tarball_store() / "fixture" / "fixture").read_text())

    def test_uninstall_removes_app_but_keeps_archive(self):
        source = self.archive()
        app = tarball.install(source)
        self.assertTrue(source.is_file())

        tarball.uninstall(app)
        self.assertFalse(app.entry.path.exists())
        self.assertFalse((config.tarball_store() / "fixture").exists())
        self.assertIsNone(db.get(app.entry.id))
        self.assertTrue(source.is_file(), "the user's downloaded archive must survive")


if __name__ == "__main__":
    unittest.main()
