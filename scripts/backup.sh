#!/usr/bin/env bash
# MeshWiki data backup script.
# Tarballs data/pages and uploads to an S3-compatible bucket via rclone.
#
# Required environment variables:
#   BACKUP_BUCKET  — rclone remote path, e.g. "s3:my-bucket" or "b2:my-bucket"
#
# Optional environment variables:
#   BACKUP_DIR     — path to the pages directory (default: /opt/meshwiki/data/pages)
#   BACKUP_TMP     — directory for the local archive (default: /tmp)
#
# Usage:
#   BACKUP_BUCKET=s3:my-bucket ./scripts/backup.sh
#
# Crontab (run daily at 03:00):
#   0 3 * * * root BACKUP_BUCKET=s3:my-bucket /opt/meshwiki/scripts/backup.sh >> /var/log/meshwiki-backup.log 2>&1

set -euo pipefail

BACKUP_BUCKET="${BACKUP_BUCKET:?BACKUP_BUCKET env var is required}"
BACKUP_DIR="${BACKUP_DIR:-/opt/meshwiki/data/pages}"
BACKUP_TMP="${BACKUP_TMP:-/tmp}"
DATE=$(date +%Y-%m-%d)
ARCHIVE="${BACKUP_TMP}/meshwiki-pages-${DATE}.tar.gz"

echo "[$(date -u +%FT%TZ)] Starting backup of ${BACKUP_DIR} → ${BACKUP_BUCKET}/backups/"

if [[ ! -d "${BACKUP_DIR}" ]]; then
    echo "ERROR: BACKUP_DIR '${BACKUP_DIR}' does not exist" >&2
    exit 1
fi

tar -czf "${ARCHIVE}" -C "$(dirname "${BACKUP_DIR}")" "$(basename "${BACKUP_DIR}")"
echo "[$(date -u +%FT%TZ)] Archive created: ${ARCHIVE} ($(du -sh "${ARCHIVE}" | cut -f1))"

rclone copy "${ARCHIVE}" "${BACKUP_BUCKET}/backups/" --s3-no-check-bucket
echo "[$(date -u +%FT%TZ)] Uploaded to ${BACKUP_BUCKET}/backups/meshwiki-pages-${DATE}.tar.gz"

rm "${ARCHIVE}"
echo "[$(date -u +%FT%TZ)] Backup complete"
