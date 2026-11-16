# FAQ and glossary

## Frequently asked questions

**Is AppDeb a replacement for the Ubuntu Software app?**
Partly. It does much more for Flatpak, AppImage, Homebrew and portable `.tar.gz` apps, and it
lets you repair menu entries. For installing normal Ubuntu packages you can still use Ubuntu
Software; AppDeb installs local `.deb` files.

**Does AppDeb need the internet?**
No for listing and managing what is installed; yes for searching stores, installing from them,
and loading store screenshots/descriptions. Those features fall back to cached or offline data.

**Do I need to use the terminal?**
No. The terminal is optional and only described in [Command line](Command-Line.md).

**Will AppDeb break my system?**
It writes only to your home folder (`~/.local/share/applications`, `~/.local/share/appdeb`,
`~/.cache/appdeb`). System changes happen only through `apt`, `flatpak` and `snap`, which ask
for authentication and do their own safety checks. Removing essential `.deb` packages is
refused outright.

**Where does AppDeb store my changes?**
- Menu overrides: `~/.local/share/applications/*.desktop`
- AppDeb data and database: `~/.local/share/appdeb/`
- Backups: `~/.local/share/appdeb/backups/`
- Cache: `~/.cache/appdeb/`

**Can I undo a rename or an icon change?**
Yes. **Reset Icon** restores the previous icon. The original name is kept in the menu entry as
`X-Appdeb-OriginalName`, and deleting the override file in `~/.local/share/applications`
restores everything.

**Why does AppDeb show applications I did not install?**
It lists every desktop entry on the system, including ones shipped by your distribution.

**Does it work on Fedora/Arch?**
The application is written for Debian-based systems and uses `dpkg`/`apt`; the Flatpak,
AppImage, Homebrew and tarball parts would work elsewhere, but this is not supported.

**Is it available in my language?**
English, Türkçe, Deutsch, Français, 中文（简体）, Español, 한국어, Polski, Svenska, Italiano,
Azərbaycanca and Dansk. See [Languages](Languages.md).

---

## Glossary

**AppImage** — a single executable file containing an application. No installation needed, but
it is not sandboxed.

**APT / dpkg** — the classic Debian/Ubuntu package manager and its low-level tool. `.deb`
files belong here.

**Backup** — a copy of a file made before AppDeb changes it. Stored under
`~/.local/share/appdeb/backups/`.

**Desktop entry** — the small text file (`.desktop`) that makes a program appear in your
application menu with a name, icon and categories.

**Dock / task bar** — the bar with running and favourite apps. It needs the window class to
match a desktop entry to show the right icon.

**Exec line** — the command inside a desktop entry that actually starts the program.

**Flathub** — the main Flatpak app store.

**Flatpak** — a sandboxed application format with its own runtimes and permission model.

**Freedesktop / XDG** — the standards that define where Linux stores menu entries, icons and
settings. `~/.local/share` and `~/.config` come from these standards.

**Homebrew** — a package manager that installs command-line tools into your home folder.

**MIME type** — the label a desktop uses for a file type (for example
`application/vnd.debian.binary-package` for `.deb`). It decides which programs appear in
"Open with".

**NoDisplay / Hidden** — desktop-entry keys that keep an app out of the menu. AppDeb hides such
entries by default.

**Polkit / pkexec** — the mechanism that shows the password dialog when an action needs
administrator rights.

**Sandbox** — a restricted environment that limits what an application can read. Flatpak and
Snap apps are sandboxed; AppImages and tarballs are not.

**Snap** — Ubuntu's sandboxed packaging format, installed system-wide.

**StartupWMClass** — the desktop-entry key that tells your desktop which window belongs to the
entry. Missing it produces the grey gear.

**Store metadata** — the description, developer, license and screenshots an app store provides.

**Tarball** — a `.tar.gz` / `.tgz` archive. Portable, not sandboxed, no installer.

**User vs System scope** — user installations belong to your account and need no password;
system installations are for every account and need authentication.

**Window class** — the name a window reports to the desktop, used for icon matching.
