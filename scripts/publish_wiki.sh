#!/usr/bin/env bash
# Publish docs/guide/*.md to the GitHub wiki of ta4anx/appdeb.
#
# GitHub only creates a wiki repository after the first page exists, and that
# first page can only be created in the browser:
#   1. open https://github.com/ta4anx/appdeb/wiki
#   2. click "Create the first page", title "Home", save (content is replaced)
#   3. run this script again
set -eu

SOURCE="$(cd "$(dirname "$0")/.." && pwd)/docs/guide"
RAW_BASE="https://raw.githubusercontent.com/ta4anx/appdeb/main/docs/screenshots"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "${WIKI_REPO:-}" ]; then
  REPO="$WIKI_REPO"
elif [ -n "${GH_TOKEN:-}" ]; then
  REPO="https://x-access-token:${GH_TOKEN}@github.com/ta4anx/appdeb.wiki.git"
else
  REPO="https://github.com/ta4anx/appdeb.wiki.git"
fi

if ! git clone --quiet "$REPO" "$WORK/wiki" 2>/dev/null; then
  cat >&2 <<EOF
The wiki repository does not exist yet.

GitHub creates it only after the first page has been created in a browser:
  1. open https://github.com/ta4anx/appdeb/wiki
  2. click "Create the first page"
  3. use the title "Home" and save anything
  4. run: bash scripts/publish_wiki.sh

The same pages are already readable in the repository:
  https://github.com/ta4anx/appdeb/blob/main/docs/guide/index.md
EOF
  exit 1
fi

cd "$WORK/wiki"

for file in "$SOURCE"/*.md; do
  name="$(basename "$file")"
  [ "$name" = "index.md" ] && name="Home.md"
  # wiki URLs have no .md suffix; screenshots must use absolute URLs
  sed -e 's#](\.\./screenshots/\([A-Za-z0-9._-]*\))#]('"$RAW_BASE"'/\1)#g' \
      -e 's#](\([A-Za-z0-9-]*\)\.md)#](\1)#g' \
      "$file" > "$name"
done

cat > _Sidebar.md <<'EOF'
**AppDeb guide**

- [Start here](Home)
- [Installation](Installation)
- [First launch](First-Launch)
- [Installing apps](Installing-Apps)
- [Managing apps](Managing-Apps)
- [Package managers](Package-Managers)
- [Switching package manager](Switching-Package-Manager)
- [Fixing icons](Fixing-Icons)
- [System and resources](System-and-Resources)
- [Languages](Languages)
- [Command line](Command-Line)
- [Troubleshooting](Troubleshooting)
- [FAQ and glossary](FAQ-and-Glossary)
EOF

cat > _Footer.md <<'EOF'
AppDeb — [releases](https://github.com/ta4anx/appdeb/releases) · [issues](https://github.com/ta4anx/appdeb/issues) · [source](https://github.com/ta4anx/appdeb)
EOF

git add -A
if git -c user.name=AppDeb -c user.email=dev@localhost commit -qm "docs: publish user guide"; then
  branch="$(git rev-parse --abbrev-ref HEAD)"
  git push origin "$branch"
  echo "Wiki updated: https://github.com/ta4anx/appdeb/wiki"
else
  echo "Wiki already up to date"
fi
