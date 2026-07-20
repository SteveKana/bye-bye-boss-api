#!/usr/bin/env bash
#
# Install deploy.sh to the path the CI wrappers and sudoers rule expect.
# Run as root on the server, from a checkout:
#   sudo /var/www/bye-bye-boss-api/api.byebyeboss.fr/repo/deploy/install.sh
#
# The installed copy is deliberately not refreshed automatically on every
# deploy: a bad commit would otherwise break the mechanism that deploys the
# fix. deploy.sh reports drift so the update stays a conscious step.

set -euo pipefail

readonly TARGET=/var/www/bye-bye-boss-api/deploy-byebyeboss-api
readonly SRC="$(cd "$(dirname "$0")" && pwd)/deploy.sh"

[ "$(id -u)" -eq 0 ] || { echo "must run as root" >&2; exit 1; }
[ -f "$SRC" ] || { echo "missing $SRC" >&2; exit 1; }
bash -n "$SRC" || { echo "$SRC has a syntax error, refusing to install" >&2; exit 1; }

install -o root -g root -m 755 "$SRC" "$TARGET"
echo "installed $TARGET from $SRC"
