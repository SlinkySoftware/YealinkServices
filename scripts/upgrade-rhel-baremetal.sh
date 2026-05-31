#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Slinky Software

set -euo pipefail

APP_USER="${APP_USER:-yealink}"
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STAGE2_SCRIPT="$APP_DIR/scripts/upgrade-rhel-baremetal-stage2.sh"

log() {
  echo "[upgrade-rhel] $*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)."
    exit 1
  fi
}

validate_paths() {
  if [[ ! -d "$APP_DIR/.git" ]]; then
    echo "Git repository not found at $APP_DIR"
    echo "Set APP_DIR to the project checkout path."
    exit 1
  fi
}

update_source_code() {
  log "Fetching latest source code"
  sudo -u "$APP_USER" -H bash -lc "cd '$APP_DIR' && git fetch --all --prune"

  log "Pulling latest changes"
  sudo -u "$APP_USER" -H bash -lc "cd '$APP_DIR' && git pull --ff-only"
}

main() {
  require_root
  validate_paths

  log "Starting stage 1 upgrade launcher"
  update_source_code

  if [[ ! -f "$STAGE2_SCRIPT" ]]; then
    echo "Stage 2 upgrade script not found after pull: $STAGE2_SCRIPT"
    exit 1
  fi

  log "Handing off to stage 2 upgrade script from refreshed checkout"
  exec bash "$STAGE2_SCRIPT" "$@"
}

main "$@"
