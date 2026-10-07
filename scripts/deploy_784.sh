#!/usr/bin/env bash
# ============================================================
# sso.pdhc — #784 reform deploy, 4 files.
#
# Rule 12 / Rule 19: the operator runs this on the macmini. It compares
# before it writes, and the default mode writes NOTHING.
#
#   ./deploy_784.sh            # compare only (Rule 12). Writes nothing.
#   ./deploy_784.sh --apply    # back up, copy, compile-check, rebuild, probe
#
# Run it from the directory holding the four new files, which must sit in the
# same relative layout the repo uses:
#     app/src/templates/base.html
#     app/src/templates/landing.html
#     app/src/templates/su_admin.html
#     app/src/routes/frontend.py
#
# Why compare first, every time: the served copy of a PDHC service has been
# found AHEAD of git more than once -- someone fixes a template on the server
# and it is never committed. Copying over that silently destroys the work. The
# check below prints a line per file and refuses to continue on a surprise.
# ============================================================
set -euo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
docker context use colima >/dev/null 2>&1 || true

MODE="${1:---check}"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP="$HOME/backups/predeploy/sso.pdhc/$TS"

FILES=(
  "app/src/templates/base.html"
  "app/src/templates/landing.html"
  "app/src/templates/su_admin.html"
  "app/src/routes/frontend.py"
)

# --- Locate the live tree from the container itself, not from a guess. ---
# A hardcoded /usr/local/www/<service> has been wrong before (ips lives at
# /usr/local/www/pdhcips/gateway). The compose label is authoritative.
LIVE="$(docker inspect sso_app \
        --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' \
        2>/dev/null || true)"
if [ -z "$LIVE" ] || [ ! -d "$LIVE" ]; then
  echo "ERROR: could not resolve sso_app's working_dir (got '${LIVE:-empty}')."
  echo "       Find it with:  docker inspect sso_app --format '{{json .Config.Labels}}'"
  exit 2
fi
echo "live tree : $LIVE"
echo "new files : $SRC"
echo "mode      : $MODE"
echo

# --- Rule 12: compare before anything else. ---
DIFFER=0
MISSING=0
for f in "${FILES[@]}"; do
  new="$SRC/$f"
  old="$LIVE/$f"
  if [ ! -f "$new" ]; then
    echo "MISSING-NEW   $f   (not in this bundle)"; MISSING=1; continue
  fi
  if [ ! -f "$old" ]; then
    echo "NEW-FILE      $f   (does not exist on the server yet)"; DIFFER=1; continue
  fi
  h_new="$(shasum -a 256 "$new" | cut -d' ' -f1)"
  h_old="$(shasum -a 256 "$old" | cut -d' ' -f1)"
  if [ "$h_new" = "$h_old" ]; then
    echo "IDENTICAL     $f"
  else
    echo "WILL-REPLACE  $f"
    echo "              server $(wc -c <"$old" | tr -d ' ') bytes, $(stat -f '%Sm' "$old")"
    echo "              new    $(wc -c <"$new" | tr -d ' ') bytes"
    DIFFER=1
  fi
done
echo

if [ "$MISSING" = "1" ]; then
  echo "ABORT: the bundle is incomplete. Nothing was changed."
  exit 3
fi
if [ "$DIFFER" = "0" ]; then
  echo "Nothing to do — the server already matches this bundle."
  exit 0
fi

if [ "$MODE" != "--apply" ]; then
  cat <<'MSG'
Compare only. NOTHING was written.

Read the WILL-REPLACE lines above. If any server file is NEWER than you expect,
stop: someone edited it on the server and it was never committed. Save that
copy first.

  ./deploy_784.sh --apply     # when the comparison looks right
MSG
  exit 0
fi

# --- Apply ---
echo "Backing up the live copies to $BACKUP"
mkdir -p "$BACKUP"
for f in "${FILES[@]}"; do
  if [ -f "$LIVE/$f" ]; then
    mkdir -p "$BACKUP/$(dirname "$f")"
    cp -p "$LIVE/$f" "$BACKUP/$f"
  fi
done
echo "Backed up $(find "$BACKUP" -type f | wc -l | tr -d ' ') file(s)."
echo

for f in "${FILES[@]}"; do
  mkdir -p "$LIVE/$(dirname "$f")"
  cp "$SRC/$f" "$LIVE/$f"
  echo "copied  $f"
done
echo

# Syntax-check the one Python file before anything restarts. A broken
# frontend.py takes the whole auth service down, and every other PDHC service
# revalidates its tokens here on every request.
python3 -m py_compile "$LIVE/app/src/routes/frontend.py" \
  && echo "py_compile OK" \
  || { echo "ERROR: frontend.py does not compile. Restoring the backup."
       for f in "${FILES[@]}"; do
         [ -f "$BACKUP/$f" ] && cp -p "$BACKUP/$f" "$LIVE/$f"
       done
       echo "Restored. Service untouched."; exit 4; }
echo

# `COPY . .` in the Dockerfile bakes the source into the image, so a plain
# restart would run the OLD code. --build is not optional here.
DC="docker compose"; command -v docker-compose >/dev/null 2>&1 && DC="docker-compose"
echo "Rebuilding sso_app ($DC up -d --build app) ..."
( cd "$LIVE" && $DC up -d --build app )
echo

echo "Waiting for health ..."
for i in $(seq 1 30); do
  if curl -fsS https://sso.pdhc.se/api/health >/dev/null 2>&1; then
    echo "HEALTHY: $(curl -fsS https://sso.pdhc.se/api/health)"
    break
  fi
  sleep 2
  [ "$i" = "30" ] && { echo "WARNING: /api/health did not come back in 60s."
                       echo "Last 40 log lines:"; docker logs --tail 40 sso_app
                       echo "Roll back with: cp -rp $BACKUP/app $LIVE/app && (cd $LIVE && $DC up -d --build app)"
                       exit 5; }
done
echo

# Prove the reform is actually live rather than assuming the rebuild took.
echo "Verifying the new code is in the container:"
docker exec sso_app grep -c "admin/export-users" /app/src/templates/su_admin.html \
  2>/dev/null | sed 's/^/  export-users link occurrences: /' || \
  echo "  (could not grep in-container; check the path inside sso_app)"
docker exec sso_app grep -c "USER_CSV_COLUMNS" /app/src/routes/frontend.py \
  2>/dev/null | sed 's/^/  USER_CSV_COLUMNS occurrences : /' || true
echo
echo "Done. Backup: $BACKUP"
echo "Roll back:  cp -rp $BACKUP/app $LIVE/app && (cd $LIVE && $DC up -d --build app)"
