#!/usr/bin/env sh
# Encrypted local backup of database + evidence/keys volume (brief p.28, p.63).
# Usage: BACKUP_PASSPHRASE_FILE=/root/kantam-backup.key ./backup.sh /secure/backups
set -eu
DEST=${1:?destination directory}
KEY=${BACKUP_PASSPHRASE_FILE:?path to passphrase file}
STAMP=$(date +%Y%m%d-%H%M%S)
cd "$(dirname "$0")"
docker compose exec -T db pg_dump -U kantam -Fc kantam \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass file:"$KEY" > "$DEST/kantam-db-$STAMP.dump.enc"
docker run --rm -v kantam-erp_appdata:/data:ro alpine:3.20 tar -C /data -czf - . \
  | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass file:"$KEY" > "$DEST/kantam-files-$STAMP.tgz.enc"
sha256sum "$DEST"/kantam-*-"$STAMP".* > "$DEST/kantam-$STAMP.sha256"
echo "Backup written: $DEST (stamp $STAMP). A backup is only proven by a restore drill - see README."
