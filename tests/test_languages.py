import unittest

from appdeb import i18n


class LanguageListTests(unittest.TestCase):
    def test_languages_include_english_and_system(self):
        codes = [code for code, _label in i18n.LANGUAGES]
        self.assertEqual(codes[0], "system")
        self.assertIn("en", codes)
        for code in ("tr", "de", "fr", "zh_CN", "es", "ko", "pl", "sv", "it", "az", "da"):
            self.assertIn(code, codes)

    def test_every_language_has_a_label(self):
        for code, label in i18n.LANGUAGES:
            self.assertTrue(label.strip(), code)
            self.assertEqual(i18n.language_name(code), label)

    def test_english_selection_returns_source_strings(self):
        self.assertEqual(i18n.setup("en"), "en")
        self.assertEqual(i18n._("Install"), "Install")
        self.assertEqual(i18n._("Uninstall"), "Uninstall")
        self.assertEqual(i18n.ngettext("%d application", "%d applications", 2), "%d applications")

    def test_switching_back_and_forth(self):
        i18n.setup("tr")
        self.assertEqual(i18n._("Install"), "Kur")
        i18n.setup("en")
        self.assertEqual(i18n._("Install"), "Install")
        i18n.setup("system")
        self.assertIn(i18n._("Install"), ("Install", "Kur", "Installieren"))

    def test_unknown_language_is_ignored(self):
        code = i18n.setup("xx_YY")
        self.assertEqual(code, "xx_YY")
        self.assertIn(i18n._("Install"), ("Install",))


if __name__ == "__main__":
    unittest.main()
