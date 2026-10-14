#!/usr/bin/env bash
# Wipes all data and re-applies db/schema.sql.
# Needed after editing the schema: init scripts only run on an empty volume.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose down -v
docker compose up -d --wait db
