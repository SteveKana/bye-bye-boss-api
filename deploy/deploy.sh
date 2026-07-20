#!/usr/bin/env bash
#
# Deploy a Bye Bye Boss API environment.
#   deploy-byebyeboss-api api.byebyeboss.fr [ref]
#   deploy-byebyeboss-api api-dev.byebyeboss.fr --rollback
#
# Layout per site:
#   <site>/repo   git checkout (root-owned) + .env
#   <site>/venv   virtualenv
#
# Unlike the frontend there is no build artifact to swap: the checkout is the
# artifact. A failed deploy rolls the code back, but NEVER auto-downgrades the
# database -- alembic downgrades are lossy, so a human decides. The pre-migration
# revision and a pg_dump are recorded so that decision is possible.

set -euo pipefail

readonly ROOT=/var/www/bye-bye-boss-api
readonly BACKUPS=/var/backups/byebyeboss-api
readonly RETAIN=10

SITE=${1:-}
ARG=${2:-}

case "$SITE" in
  api.byebyeboss.fr)     BRANCH=main    ; PORT=8000 ; DB=byebyeboss_api ;;
  api-dev.byebyeboss.fr) BRANCH=develop ; PORT=8001 ; DB=byebyeboss_api_dev ;;
  *)
    echo "usage: $(basename "$0") {api.byebyeboss.fr|api-dev.byebyeboss.fr} [ref|--rollback]" >&2
    exit 64 ;;
esac

readonly DIR=$ROOT/$SITE
readonly REPO_DIR=$DIR/repo
readonly VENV=$DIR/venv
readonly LOG=/var/log/deploy-$SITE.log
readonly LOCK=/var/lock/deploy-$SITE.lock

exec {lockfd}>"$LOCK"
flock -n "$lockfd" || { echo "a deployment for $SITE is already running" >&2; exit 69; }

log()  { printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*" | tee -a "$LOG"; }
fail() { log "FAILED: $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root (needs systemctl)"
[ -d "$REPO_DIR/.git" ] || fail "$REPO_DIR is not a git checkout"
[ -x "$VENV/bin/python" ] || fail "$VENV is not a virtualenv"

health() {
  local body=
  for i in $(seq 1 30); do
    sleep 1
    body=$(curl -s --max-time 5 "http://127.0.0.1:$PORT/health" || true)
    case "$body" in
      *'"status":"ok"'*) log "healthy after ${i}s"; return 0 ;;
    esac
  done
  log "health check failed (last response: ${body:-none})"
  return 1
}

# Read the revision from the table, not from `alembic current`: the app's
# structlog output puts a timestamp line first, which parses as hex.
revision() { sudo -u postgres psql -tAc "select version_num from alembic_version" -d "$DB" 2>/dev/null | tr -d ' ' | head -1; }

restore_code() {
  git -C "$REPO_DIR" reset --hard "$1" >/dev/null
  "$VENV/bin/pip" install -q -e "$REPO_DIR" || true
  systemctl restart "$SITE"
}

PREVIOUS_SHA=$(git -C "$REPO_DIR" rev-parse HEAD)

if [ "$ARG" = "--rollback" ]; then
  target=$(git -C "$REPO_DIR" rev-parse 'HEAD@{1}' 2>/dev/null || true)
  [ -n "$target" ] || fail "no previous revision in reflog to roll back to"
  log "=== rollback $SITE -> ${target:0:8} ==="
  restore_code "$target"
  health || fail "rollback is unhealthy, manual intervention required"
  log "rolled back to ${target:0:8} (database left at its current revision)"
  exit 0
fi

TARGET=${ARG:-origin/$BRANCH}

log "=== deploy $SITE ($BRANCH) ==="
log "current  ${PREVIOUS_SHA:0:8}  db revision $(revision)"

mkdir -p "$BACKUPS"
DUMP=$BACKUPS/$DB-$(date -u +%Y%m%d%H%M%S).sql.gz
sudo -u postgres pg_dump "$DB" 2>/dev/null | gzip > "$DUMP" || fail "pg_dump failed, refusing to migrate"
log "database dumped to $DUMP ($(du -h "$DUMP" | cut -f1))"

git -C "$REPO_DIR" fetch --prune --tags origin "$BRANCH"
git -C "$REPO_DIR" reset --hard "$TARGET" >/dev/null
INCOMING=$(git -C "$REPO_DIR" rev-parse HEAD)
log "incoming ${INCOMING:0:8}  $(git -C "$REPO_DIR" log -1 --format=%s)"

"$VENV/bin/pip" install -q -e "$REPO_DIR" >>"$LOG" 2>&1 || {
  log "dependency install failed, restoring ${PREVIOUS_SHA:0:8}"
  restore_code "$PREVIOUS_SHA"; exit 1
}

BEFORE_REV=$(revision)
cd "$REPO_DIR"
if ! "$VENV/bin/alembic" upgrade head >>"$LOG" 2>&1; then
  log "migration failed, restoring code to ${PREVIOUS_SHA:0:8}"
  log "database left at revision $BEFORE_REV -- restore from $DUMP if needed"
  restore_code "$PREVIOUS_SHA"; exit 1
fi
AFTER_REV=$(revision)
[ "$BEFORE_REV" = "$AFTER_REV" ] && log "db revision unchanged ($AFTER_REV)" || log "db migrated $BEFORE_REV -> $AFTER_REV"

systemctl restart "$SITE"

if ! health; then
  log "restoring code to ${PREVIOUS_SHA:0:8}"
  if [ "$BEFORE_REV" != "$AFTER_REV" ]; then
    log "WARNING: database was migrated to $AFTER_REV and is NOT being downgraded."
    log "         if the old code cannot run against it, restore $DUMP manually."
  fi
  restore_code "$PREVIOUS_SHA"
  sleep 2
  log "rolled back ($(systemctl is-active "$SITE"))"
  exit 1
fi

ls -1t "$BACKUPS"/$DB-*.sql.gz 2>/dev/null | tail -n +$((RETAIN + 1)) | xargs -r rm -f

public=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "https://$SITE/health" || echo "?")
log "done: ${INCOMING:0:8} live, https://$SITE/health -> $public"

if ! cmp -s "$REPO_DIR/deploy/deploy.sh" "$0"; then
  log "note: the checkout ships a different deploy.sh than the installed one"
  log "      run 'sudo $REPO_DIR/deploy/install.sh' to update it"
fi
