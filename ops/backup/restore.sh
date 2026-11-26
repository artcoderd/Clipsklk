#!/bin/sh
# Restores a backup into the database (REPLACES its current contents).
#   docker compose run --rm backup restore.sh /backups/clipeo-20261126-030000.dump[.enc]
set -eu
: "${PGHOST:=postgres}" "${PGUSER:=clipeo}" "${PGDATABASE:=clipeo}"
export PGHOST PGUSER PGDATABASE
f="$1"
case "$f" in
  *.enc) openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$f" -out /tmp/restore.dump; f=/tmp/restore.dump ;;
esac
pg_restore --clean --if-exists --no-owner --dbname="$PGDATABASE" "$f"
echo "restored $1"
