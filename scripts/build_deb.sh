#!/usr/bin/env bash
# Build appdeb_<version>_all.deb from the working tree.
set -eu

cd "$(dirname "$0")/.."
VERSION="$(PYTHONPATH=. python3 -c 'import appdeb; print(appdeb.__version__)')"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

install -d "$ROOT/usr/bin"
install -d "$ROOT/usr/lib/appdeb/appdeb"
install -d "$ROOT/usr/share/applications"
install -d "$ROOT/usr/share/icons/hicolor/scalable/apps"
install -d "$ROOT/usr/share/metainfo"
install -d "$ROOT/usr/share/appdeb"
install -d "$ROOT/usr/share/doc/appdeb"
install -d "$ROOT/DEBIAN"

install -m 0755 scripts/appdeb-launcher.sh "$ROOT/usr/bin/appdeb"
install -m 0644 data/appdeb.desktop "$ROOT/usr/share/applications/appdeb.desktop"
install -m 0644 data/io.github.ta4anx.AppDeb.svg \
  "$ROOT/usr/share/icons/hicolor/scalable/apps/io.github.ta4anx.AppDeb.svg"
install -m 0644 data/io.github.ta4anx.AppDeb.metainfo.xml \
  "$ROOT/usr/share/metainfo/io.github.ta4anx.AppDeb.metainfo.xml"
install -m 0644 appdeb/style.css "$ROOT/usr/share/appdeb/style.css"
install -m 0644 README.md "$ROOT/usr/share/doc/appdeb/README.md"

find appdeb -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
while IFS= read -r -d '' file; do
  target="$ROOT/usr/lib/appdeb/${file}"
  install -d "$(dirname "$target")"
  install -m 0644 "$file" "$target"
done < <(find appdeb -type f -name '*.py' -print0)

cat > "$ROOT/usr/share/doc/appdeb/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: appdeb
Source: https://github.com/ta4anx/appdeb

Files: *
Copyright: 2026 Kaan Çakmak <skaancakmak@gmail.com>
License: GPL-3.0-or-later
 On Debian systems the full text of the GNU General Public License version 3
 can be found in /usr/share/common-licenses/GPL-3.
EOF

cat > "$ROOT/DEBIAN/control" <<EOF
Package: appdeb
Version: ${VERSION}
Architecture: all
Maintainer: Kaan Çakmak <skaancakmak@gmail.com>
Section: utils
Priority: optional
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1 (>= 1.4), python3-psutil, python3-requests, python3-pil, desktop-file-utils, pkexec, flatpak, gtk-update-icon-cache
Recommends: squashfs-tools, xdg-utils, appstream
Description: Full-control app installer and manager for Debian-based Linux
 AppDeb installs and manages Flatpak apps, AppImages, local .deb packages,
 and portable .tar.gz applications.
 It edits menu names and icons, creates desktop shortcuts, shows per-app and
 system resource usage, app file locations, Flatpak updates and permissions,
 and autostart toggles.
EOF

cat > "$ROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  update-desktop-database /usr/share/applications || true
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
EOF

cat > "$ROOT/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
  update-desktop-database /usr/share/applications || true
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
EOF
chmod 0755 "$ROOT/DEBIAN/postinst" "$ROOT/DEBIAN/postrm"

mkdir -p build
dpkg-deb --build --root-owner-group "$ROOT" "build/appdeb_${VERSION}_all.deb"
(cd build && sha256sum "appdeb_${VERSION}_all.deb" > "appdeb_${VERSION}_all.deb.sha256")
echo "built build/appdeb_${VERSION}_all.deb"
echo "built build/appdeb_${VERSION}_all.deb.sha256"
