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

# Paths RELATIVE TO THE SOURCE ROOT (the directory containing src/).
FILES=(
  "src/templates/base.html"
  "src/templates/landing.html"
  "src/templates/su_admin.html"
  "src/routes/frontend.py"
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
# The compose working_dir may BE the source root or its parent, depending on
# where docker-compose.yml sits. On sso.pdhc it is /usr/local/www/sso.pdhc/app,
# which already contains src/ -- the first version of this script appended
# "app/" again and reported all four files as missing. That refusal is why
# nothing was written. Resolve it explicitly and fail loudly if neither
# candidate holds src/, rather than inventing a path and creating a phantom
# tree that the health probe would happily pass.
if   [ -d "$LIVE/src" ];     then ROOT="$LIVE"
elif [ -d "$LIVE/app/src" ]; then ROOT="$LIVE/app"
else
  echo "ERROR: no src/ under '$LIVE' or '$LIVE/app'. Refusing to guess."
  exit 2
fi

# Same question for the bundle.
if   [ -d "$SRC/src" ];     then NEWROOT="$SRC"
elif [ -d "$SRC/app/src" ]; then NEWROOT="$SRC/app"
else
  echo "ERROR: no src/ in the bundle at '$SRC'."
  exit 2
fi

echo "live root : $ROOT"
echo "new files : $NEWROOT"
echo "baseline  : $SRC/baseline"
echo "mode      : $MODE"
echo

# --- Rule 12: compare before anything else. ---
# The question is NOT "does the server differ from my new file" -- of course it
# does, that is the point. It is "does the server still match what I started
# from". `baseline/` holds the pre-change copies straight out of git. If the
# server matches the baseline, my edits are the only change and the copy is
# provably safe. If it does not, somebody edited the server and never committed
# it, and copying would destroy that work.
DIFFER=0
DRIFT=0
MISSING=0
for f in "${FILES[@]}"; do
  new="$NEWROOT/$f"
  old="$ROOT/$f"
  base="$SRC/baseline/$f"
  if [ ! -f "$new" ]; then
    echo "MISSING-NEW   $f   (not in this bundle)"; MISSING=1; continue
  fi
  if [ ! -f "$old" ]; then
    echo "NEW-FILE      $f   (not on the server yet)"; DIFFER=1; continue
  fi
  h_new="$(shasum -a 256 "$new"  | cut -d' ' -f1)"
  h_old="$(shasum -a 256 "$old"  | cut -d' ' -f1)"
  h_base=""
  [ -f "$base" ] && h_base="$(shasum -a 256 "$base" | cut -d' ' -f1)"

  if [ "$h_new" = "$h_old" ]; then
    echo "IDENTICAL     $f   (already deployed)"
    continue
  fi
  if [ -z "$h_base" ]; then
    echo "NO-BASELINE   $f   — cannot prove the server is unmodified"; DRIFT=1
  elif [ "$h_old" = "$h_base" ]; then
    echo "SAFE-REPLACE  $f"
    echo "              server matches my pre-change baseline exactly"
    echo "              $(wc -c <"$old" | tr -d ' ') -> $(wc -c <"$new" | tr -d ' ') bytes"
    DIFFER=1
  else
    echo "*** DRIFT ***  $f"
    echo "              the server copy is NOT my baseline — it was edited"
    echo "              on the server and never committed."
    echo "              server   $(wc -c <"$old" | tr -d ' ') bytes, $(stat -f '%Sm' "$old")"
    echo "              baseline $(wc -c <"$base" | tr -d ' ') bytes"
    echo "              diff --- server vs baseline (first 40 lines) ---"
    diff "$base" "$old" | head -40 | sed 's/^/              /' || true
    DRIFT=1
  fi
done
echo

if [ "$MISSING" = "1" ]; then
  echo "ABORT: the bundle is incomplete. Nothing was changed."
  exit 3
fi
if [ "$DRIFT" = "1" ]; then
  echo "ABORT: at least one server file is not what I started from."
  echo "Nothing was changed. Save the server copy, reconcile it into git,"
  echo "then rebuild the bundle. Do NOT pass --apply to get past this."
  exit 6
fi
if [ "$DIFFER" = "0" ]; then
  echo "Nothing to do — the server already matches this bundle."
  exit 0
fi

if [ "$MODE" != "--apply" ]; then
  cat <<'MSG'
Compare only. NOTHING was written.

Every file above should say SAFE-REPLACE, meaning the server copy is byte-for-byte
my pre-change baseline, so my edits are the only change. Any *** DRIFT *** line
aborts on its own -- do not try to get past it with --apply.

  ./deploy_784.sh --apply     # when every line says SAFE-REPLACE
MSG
  exit 0
fi

# --- Apply ---
echo "Backing up the live copies to $BACKUP"
mkdir -p "$BACKUP"
for f in "${FILES[@]}"; do
  if [ -f "$ROOT/$f" ]; then
    mkdir -p "$BACKUP/$(dirname "$f")"
    cp -p "$ROOT/$f" "$BACKUP/$f"
  fi
done
echo "Backed up $(find "$BACKUP" -type f | wc -l | tr -d ' ') file(s)."
echo

for f in "${FILES[@]}"; do
  mkdir -p "$ROOT/$(dirname "$f")"
  cp "$NEWROOT/$f" "$ROOT/$f"
  echo "copied  $f"
done
echo

# Syntax-check the one Python file before anything restarts. A broken
# frontend.py takes the whole auth service down, and every other PDHC service
# revalidates its tokens here on every request.
python3 -m py_compile "$ROOT/src/routes/frontend.py" \
  && echo "py_compile OK" \
  || { echo "ERROR: frontend.py does not compile. Restoring the backup."
       for f in "${FILES[@]}"; do
         [ -f "$BACKUP/$f" ] && cp -p "$BACKUP/$f" "$ROOT/$f"
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
                       echo "Roll back with: cp -rp $BACKUP/src $ROOT/src && (cd $LIVE && $DC up -d --build app)"
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
echo "Roll back:  cp -rp $BACKUP/src $ROOT/src && (cd $LIVE && $DC up -d --build app)"
