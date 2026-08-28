#!/usr/bin/env bash
# Publish the staging copy of the documentation.
#
#     tools/docs/learn/publish-dev.sh
#
# Builds the site with --target dev and pushes it to simoncoombes/pretium-dev,
# which GitHub Pages serves at https://simoncoombes.github.io/pretium-dev/.
#
# The staging site differs from the published one in four ways, all of them
# decided in TARGETS in build.py rather than here: it points at its own
# address, every page carries noindex, there is no sitemap, and it does not
# report to analytics. Nothing about it is a hand edit, so nothing about it
# can be left behind on the way to production.
#
# The repository holds only built output. It is not a fork and it has no
# history worth keeping, so each publish replaces the tree wholesale — which
# is also what stops a file deleted from the build lingering on the site.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
OUT="$ROOT/dist/dev-site"
REMOTE="${PRETIUM_DEV_REMOTE:-https://github.com/simoncoombes/pretium-dev.git}"

cd "$ROOT"
python3 tools/docs/learn/build.py --target dev --out "$OUT"

cat > "$OUT/README.md" <<'MD'
# pretium documentation — staging

Built output only. The source is in
[simoncoombes/pretium](https://github.com/simoncoombes/pretium) under
`tools/docs/learn/`; this repository is written by
`tools/docs/learn/publish-dev.sh` and every push replaces it wholesale.

Served at <https://simoncoombes.github.io/pretium-dev/>.

**It is not indexed and must not be.** Every page carries
`<meta name="robots" content="noindex, nofollow">`, `robots.txt` disallows
everything, and no sitemap is written. A staging copy of a documentation
site competing with the real one in search results is worse than having no
staging copy at all: the same words at two addresses, and no way to tell a
search engine afterwards which one was real.

The published site is <https://simoncoombes.github.io/pretium/>.
MD

# A fresh history each time: this is a mirror of a directory, not a branch of
# anything, and keeping commits would only accumulate copies of the site.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -R "$OUT/." "$WORK/"

cd "$WORK"
git init -q -b main
git add -A
git -c user.name="${GIT_AUTHOR_NAME:-pretium docs}" \
    -c user.email="${GIT_AUTHOR_EMAIL:-noreply@simoncoombes.github.io}" \
    commit -q -m "Staging build from pretium@$(git -C "$ROOT" rev-parse --short HEAD)"
git push -q --force "$REMOTE" main

echo "pushed $(ls "$OUT"/*.html | wc -l | tr -d ' ') pages to $REMOTE"
echo "https://simoncoombes.github.io/pretium-dev/"
