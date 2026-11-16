# Fixing icons (the grey gear)

You know the problem: an app is running, but the dock (or task bar) shows a **generic grey
gear** instead of the app's real logo. Sometimes the application menu shows the wrong icon
too, or the entry does not start anything at all.

AppDeb's **Tools** page finds these problems and repairs them for you.

## Why it happens (short version)

A running window does not tell your desktop which menu entry belongs to it. Your desktop
matches them by a property called **window class**. When the menu entry does not declare that
class, the desktop cannot find the icon and falls back to the gear.

AppDeb writes the missing property — and only that — into a **personal copy** of the menu
entry. The original file from the package manager is never touched.

## Repair an icon in two clicks

1. Click **Tools** in the sidebar.
2. Click **Scan desktop entries**.
3. Read the results. Each row is one finding, for example:

   - **invalid-desktop** — the file has a syntax error (usually a duplicated
     `[Desktop Entry]` group).
   - **duplicate-groups** — the same group appears twice; the second copy is the real one.
   - **missing-icon** — the icon named in the file does not exist.
   - **absolute-icon** — the icon is a full path outside the icon theme (works, but fragile).
   - **missing-exec** — the program that the entry starts does not exist.
   - **no-wm-class** — a window with a matching class is running but the entry does not declare
     it. This is the gear-icon case.

4. Click **Fix** on a row. AppDeb backs up the affected files first, applies the change and
   rescans.
5. Restart the app if it is running. The dock now shows the real icon.

Backups are stored under `~/.local/share/appdeb/backups/` (one folder per run, containing the
desktop entries and the AppDeb database).

## When the fix is not offered

- **missing-exec** cannot be fixed automatically: the program is gone. Reinstall the app or
  remove the entry.
- On a **Wayland-only** session, AppDeb may not see X11 windows, so the `no-wm-class` check can
  find nothing. In that case use the details page instead:

### Manual window-class fix

1. Open **Installed** and click the app.
2. Find **Dock icon association** (the **Window class** field).
3. Type the app's window class and click **Save**.
   - For an Electron app called *Antigravity*, that is often `antigravity`.
   - For *Ultimaker Cura*, it is `UltiMaker-Cura` (capital letters matter).
4. Close and reopen the app.

Not sure about the value? Run the app, open a terminal and type `xprop WM_CLASS`, then click
the app's window. The second string in the output is the value to use.

## Fix everything at once

The command line equivalent (`appdeb fix-icons`) applies every fixable finding and prints a
summary; see [Command line](Command-Line.md).

## Other Tools buttons

- **Rebuild icon cache** — makes newly imported icons appear immediately.
- **Refresh application database** — recomputes the menu database after manual changes.
- **Back up desktop entries** — copies your menu entries to a timestamped folder, without
  fixing anything.

Next: [System and resources](System-and-Resources.md)
