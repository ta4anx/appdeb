# Languages

AppDeb speaks eleven languages plus the system default and English:

- English
- System default (follows your desktop's language)
- Türkçe
- Deutsch
- Français
- 中文（简体）
- Español
- 한국어
- Polski
- Svenska
- Italiano
- Azərbaycanca
- Dansk

## Change the language

1. Click **Settings** in the sidebar.
2. Find the **Language** group.
3. Open the dropdown and choose your language.
4. A toast appears: **Restart AppDeb to apply**. Click **Restart**.
   - If you closed the toast, simply close AppDeb and open it again.

The whole interface changes: menus, buttons, dialogs, tooltips, toasts, the welcome guide and
the command-line help.

## Going back to English

English is always in the list, right under **System**. Pick it and restart — you cannot get
stuck in a language you do not understand.

## System default vs English

- **System default** uses the language configured in your desktop's settings (for example
  German on a German desktop).
- **English** always shows English, even if the desktop is set to something else.

## What is not translated

App and package names (`AppDeb`, `Flatpak`, `Snap`, `Homebrew`, `Flathub`, `AppImage`,
`.deb`, `.tar.gz`), file paths, command names and anything a store or package manager prints
stay as they are — translating them would break commands.

## For translators

Catalogues live in `appdeb/locale/<code>/LC_MESSAGES/appdeb.po`; the source template is
`appdeb/locale/appdeb.pot`. After editing a `.po`:

```bash
python3 scripts/build_mo.py --in-place --check
LANGUAGE=de python3 -m appdeb        # preview
```

Pull requests with corrections are welcome.

Next: [Command line](Command-Line.md)
