#!/usr/bin/env sh
# Restore drill / disaster recovery. Run against a TEST stack first.
# Usage: BACKUP_PASSPHRASE_FILE=... ./restore.sh /secure/backups/kantam-db-STAMP.dump.enc /secure/backups/kantam-files-STAMP.tgz.enc
set -eu
DB=${1:?db dump}; FILES=${2:?files archive}; KEY=${BACKUP_PASSPHRASE_FILE:?passphrase file}
cd "$(dirname "$0")"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass file:"$KEY" < "$DB" | docker compose exec -T db pg_restore -U kantam -d kantam --clean --if-exists
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass file:"$KEY" < "$FILES" | docker run --rm -i -v kantam-erp_appdata:/data alpine:3.20 tar -C /data -xzf -
docker compose exec backend python manage.py shell -c "from apps.core.audit import verify_chain; print('audit chain', verify_chain())"
