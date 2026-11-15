import unittest

from appdeb import managerops


class ManagerPolicyTests(unittest.TestCase):
    def test_apt_is_never_removable(self):
        allowed, reason = managerops.can_remove("apt", 0)
        self.assertFalse(allowed)
        self.assertIn("base system", reason)

    def test_manager_with_packages_cannot_be_removed(self):
        allowed, reason = managerops.can_remove("flatpak", 8)
        self.assertFalse(allowed)
        self.assertIn("remove it first", reason)
        allowed, reason = managerops.can_remove("snap", 1)
        self.assertFalse(allowed)
        self.assertIn("package", reason)

    def test_empty_manager_can_be_removed(self):
        self.assertTrue(managerops.can_remove("flatpak", 0)[0])
        self.assertTrue(managerops.can_remove("snap", 0)[0])

    def test_brew_install_and_remove_are_instructions_only(self):
        allowed, reason = managerops.can_install("brew")
        self.assertFalse(allowed)
        self.assertIn("install.sh", reason)
        allowed, reason = managerops.can_remove("brew", 0)
        self.assertFalse(allowed)
        self.assertIn("uninstall.sh", reason)

    def test_apt_cannot_be_installed(self):
        self.assertFalse(managerops.can_install("apt")[0])

    def test_flatpak_and_snap_are_installable(self):
        self.assertTrue(managerops.can_install("flatpak")[0])
        self.assertTrue(managerops.can_install("snap")[0])


if __name__ == "__main__":
    unittest.main()
