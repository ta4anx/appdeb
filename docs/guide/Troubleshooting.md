# Troubleshooting

Find your symptom below. Each entry says what usually causes it and what to do.

---

## "The app I installed has no menu entry"

- Give the desktop a moment, then log out and back in.
- Refresh the menu database:
  ```bash
  update-desktop-database ~/.local/share/applications
  ```
- Check that the app is listed: **Installed** page, or `appdeb list | grep -i name`.

## "The menu entry shows a gear / no icon"

Open **Tools → Scan desktop entries** and click **Fix** on the `no-wm-class` or
`missing-icon` row. Details and the manual fix are in [Fixing icons](Fixing-Icons.md).

## "My AppImage does not start, or asks for FUSE"

Some AppImages need FUSE 2:

```bash
sudo apt install libfuse2t64    # Ubuntu 24.04 and newer
```

AppDeb cannot install that for you because it is a system library.

## "`unsupported file type` when I open a file"

AppDeb accepts `.AppImage`, `.deb`, `.flatpak`, `.flatpakref`, `.tar.gz` and `.tgz`.
`.zip`, `.tar.xz` and `.rpm` are not supported. Unpack those yourself or ask the app's author
for an AppImage or Flatpak build.

## "A `.tar.gz` install says `no executable found`"

The archive does not contain an obvious program. Re-run the install and choose the executable
manually in the dialog, or from the command line:

```bash
appdeb install ./program.tar.gz --exec path/inside/archive/program
```

## "Installation failed: not authorized" (Flatpak, system scope)

System-wide installs need authentication. Either:

- switch **Settings → Installation scope** to **User** and try again, or
- enter your password in the polkit dialog. If no dialog appears, AppDeb retries through
  `pkexec`; make sure you are in a graphical session.

## "The window is blank / AppDeb does not start"

Run it from a terminal to see the error:

```bash
python3 -m appdeb      # from a source checkout
appdeb                 # installed
```

If there is no text at all, check that GTK 4 and libadwaita are installed:

```bash
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 python3-gi
```

## "My language is gone / everything is in a language I do not read"

Open the last item in the sidebar (**Settings**), then the group with the dropdown listing
language names. Choose **English** (second entry) and click **Restart**.

If you cannot find it: quit AppDeb, then run

```bash
sudo apt install sqlite3       # only if the command below is missing
sqlite3 ~/.local/share/appdeb/appdeb.db \
  "insert into settings(key,value) values('language','en') on conflict(key) do update set value='en'"
```

Start AppDeb again; the interface is English and you can pick another language properly.

## "Removing a package manager is greyed out"

That is the safety rule: Flatpak and Snap can only be removed when they own zero packages.
Hover the button to read the exact reason. Remove the apps first
([Package managers](Package-Managers.md)).

## "The dock still shows the old icon"

The desktop caches icon associations. Close the app completely (check the details page's
process count is 0), then start it again. If it persists, log out and back in.

## "Flatpak updates are not shown"

Press refresh on the **Installed** page. Updates only appear for applications installed through
Flatpak and only when your remotes have newer versions.

## "Snap installation asks for a password every time"

That is normal: snaps are installed system-wide. Flatpak and Homebrew do not need it in user
scope.

## "Where are my backups?"

`~/.local/share/appdeb/backups/` — one folder per repair run, containing the desktop entries
and a copy of the AppDeb database.

## "I want to reset AppDeb completely"

```bash
rm -rf ~/.local/share/appdeb ~/.cache/appdeb
```

This deletes AppDeb's database, managed AppImage/tarball copies and backups. Installed
applications stay installed. On the next start the welcome guide appears again.

## Nothing here helped

Open an issue with:

- what you clicked,
- the exact error text (a screenshot helps),
- the output of `appdeb --version` and `flatpak --version`.

<https://github.com/ta4anx/appdeb/issues>

Next: [FAQ and glossary](FAQ-and-Glossary.md)
