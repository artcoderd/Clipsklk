#!/bin/sh
# Daily PostgreSQL backups.
#   backup.sh daemon  -> one backup every day at BACKUP_HOUR (UTC), forever
#   backup.sh once    -> one backup now
# Files: /backups/clipeo-YYYYmmdd-HHMMSS.dump[.enc], kept BACKUP_KEEP_DAYS days.
# With BACKUP_PASSPHRASE they are encrypted (AES-256). With BACKUP_REMOTE (an rclone
# remote configured through RCLONE_CONFIG_* variables, e.g. "offsite:clipeo-backups")
# each file is also copied off the server.
set -eu
: "${PGHOST:=postgres}" "${PGUSER:=clipeo}" "${PGDATABASE:=clipeo}"
: "${BACKUP_HOUR:=3}" "${BACKUP_KEEP_DAYS:=14}" "${BACKUP_DIR:=/backups}"
export PGHOST PGUSER PGDATABASE

backup() {
  mkdir -p "$BACKUP_DIR"
  name="clipeo-$(date -u +%Y%m%d-%H%M%S).dump"
  tmp="$BACKUP_DIR/.$name.partial"
  trap 'rm -f "$tmp" "$tmp.enc"' EXIT
  pg_dump --format=custom --compress=6 --no-owner --file="$tmp"
  if [ -n "${BACKUP_PASSPHRASE:-}" ]; then
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE -in "$tmp" -out "$tmp.enc"
    rm -f "$tmp"; tmp="$tmp.enc"; name="$name.enc"
  fi
  mv "$tmp" "$BACKUP_DIR/$name"
  trap - EXIT
  echo "$(date -u +%FT%TZ) backup ok: $name ($(du -h "$BACKUP_DIR/$name" | cut -f1))"
  if [ -n "${BACKUP_REMOTE:-}" ]; then
    rclone copyto "$BACKUP_DIR/$name" "$BACKUP_REMOTE/$name" && echo "uploaded to $BACKUP_REMOTE"
    rclone delete --min-age "${BACKUP_KEEP_DAYS}d" "$BACKUP_REMOTE" || true
  fi
  find "$BACKUP_DIR" -name 'clipeo-*.dump*' -mtime +"$BACKUP_KEEP_DAYS" -delete
}

case "${1:-daemon}" in
  once) backup ;;
  daemon)
    echo "daily backups at ${BACKUP_HOUR}:00 UTC, keeping ${BACKUP_KEEP_DAYS} days"
    while true; do
      now=$(date -u +%s)
      next=$(date -u -d "@$(( now - now % 86400 + BACKUP_HOUR * 3600 ))" +%s)
      [ "$next" -le "$now" ] && next=$(( next + 86400 ))
      sleep $(( next - now ))
      backup || echo "$(date -u +%FT%TZ) BACKUP FAILED" >&2
    done ;;
  *) echo "usage: backup.sh daemon|once" >&2; exit 2 ;;
esac
