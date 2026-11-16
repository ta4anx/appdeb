# Installation

You have three ways to get AppDeb. Pick the first one unless you have a reason not to.

---

## Option 1 — Install the `.deb` package (recommended)

You need: an internet connection, and your normal user password (for the installation step only).

1. Open the [releases page](https://github.com/ta4anx/appdeb/releases/latest) in your browser.
2. Under **Assets**, click **`appdeb_0.4.1_all.deb`** to download it.
3. Open your **Downloads** folder, **right-click** the file and choose **Open With →
   Software Install**, then click **Install**.
   - If nothing happens, open a terminal in the Downloads folder and run:
     ```bash
     sudo apt install ./appdeb_0.4.1_all.deb
     ```
     Type your password when asked (the cursor does not move while typing — that is normal).
4. Wait for it to finish. You will see no success dialog; that is normal.

**Check that it worked:** open the application menu and type `AppDeb`. You should see the
blue box icon. Click it — the AppDeb window opens.

> Tip: the `.sha256` file next to the `.deb` is a fingerprint of the download. Comparing it is
> optional and only useful if you want to be sure the file was not corrupted in transit.

---

## Option 2 — Run from the source code (for tinkerers)

Use this if you want the newest code or to change something.

1. Open a terminal and run:
   ```bash
   git clone https://github.com/ta4anx/appdeb.git
   cd appdeb
   python3 -m appdeb
   ```
2. That starts AppDeb without installing it. To build a `.deb` of your own:
   ```bash
   bash scripts/build_deb.sh
   ```
   The package appears in `build/`.

Requirements (usually already installed on Ubuntu): Python 3.10+, GTK 4.10+, libadwaita
1.4+, `python3-psutil`, `python3-requests`, `python3-pil`, `desktop-file-utils`, `pkexec`,
`flatpak`, `gtk-update-icon-cache`. Optional but recommended: `squashfs-tools` (needed to read
AppImage metadata), `xdg-utils`, `appstream`.

---

## Option 3 — Use the Flatpak/Snap build

There is no official Flatpak or Snap package for AppDeb itself yet. Use Option 1.

---

## What got installed?

- The **`appdeb`** command (used by the app itself and by the optional command line).
- The menu entry **AppDeb** with its real icon.
- Translations for the eleven supported languages.
- A file association so that `.AppImage`, `.deb`, Flatpak files and `.tar.gz` archives can be
  opened **with AppDeb** from your file manager.

Nothing else is changed. AppDeb keeps its own data in `~/.local/share/appdeb/` and its
settings in `~/.local/share/appdeb/appdeb.db`.

---

## Uninstalling AppDeb

Only if you really want to remove it. Your installed applications stay installed.

```bash
sudo apt remove appdeb
```

To also delete AppDeb's own data (backups of desktop entries, the managed AppImage/tarball
copies and the database):

```bash
rm -rf ~/.local/share/appdeb
```

Next: [First launch](First-Launch.md)
