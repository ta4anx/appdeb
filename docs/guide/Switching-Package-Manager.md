# Switching package manager

The same application often exists in more than one package manager. AppDeb can find the
equivalents and install one of them — then, if you want, remove the old copy.

**Why would you do that?** Because Flatpak and Snap versions update themselves, are sandboxed,
and show up properly in your desktop. Turning a loose AppImage into a Flatpak app, for
example, usually means fewer surprises later.

> This is optional. Nothing changes if you never open this section.

## Step by step

1. Open **Installed** and click the app you want to move.
2. Scroll to **Change package manager**.
3. AppDeb searches the *other* managers for the same app. The first time this can take a few
   seconds — you will see "Looking for alternative package sources…".
4. Each row shows one candidate:

   ```
   Flatpak · com.ultimaker.cura
   com.ultimaker.cura · 5.13.0 · flathub
   ```

   or

   ```
   Snap · cura-slicer
   cura-slicer · 4.13.2 · canonical
   Snap installs prompt for authentication
   ```

5. Click **Install** on the candidate you want.
   - A progress window installs it.
   - Snap installs ask for authentication first.
6. When it finishes, AppDeb asks: **"Remove the AppImage copy of Ultimaker Cura?"**
   - **Cancel** keeps both versions installed (useful to compare them once).
   - **Continue** removes only the old copy, using the normal uninstall path.
7. The details page refreshes. The app is now owned by the new manager.

## Things to know

- AppDeb only proposes **other** managers. A Flatpak app gets Snap/Homebrew candidates, not
  another Flatpak remote.
- Candidates are matched by app ID and name. A row can be a *similar* application rather than
  the identical one (for example, a different calculator); read the name before installing.
- The old copy is **not** removed automatically. You always get the question, and you can say
  no.
- Uninstalling an AppImage or tarball never deletes the file you downloaded; only AppDeb's
  managed copy and the menu entry go away.
- If the list stays empty ("No alternative package sources found"), the app simply is not
  packaged anywhere else. That is common for niche software.

## Example: Ultimaker Cura

Cura is installed here as an AppImage. On its details page AppDeb offers:

- **Flatpak · com.ultimaker.cura 5.13.0** (Flathub)
- **Snap · cura-slicer 4.13.2** (Canonical)

Installing the Flatpak version and removing the AppImage copy gives you a sandboxed Cura that
updates through Flatpak, while the menu name, icon and window class stay exactly as before.

Next: [Fixing icons](Fixing-Icons.md)
