#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 Slinky Software

set -euo pipefail

APP_USER="${APP_USER:-yealink}"
APP_DIR="${APP_DIR:-/opt/yealinkService}"
REPO_URL="https://github.com/SlinkySoftware/YealinkServices"
STAGE2_SCRIPT="$APP_DIR/scripts/install-rhel-baremetal-stage2.sh"

log() {
  echo "[install-rhel] $*"
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must run as root (use sudo)."
    exit 1
  fi
}

ensure_git_available() {
  if command -v git >/dev/null 2>&1; then
    return
  fi

  log "Installing git for repository bootstrap"
  dnf -y install git
}

clone_repository() {
  if [[ -d "$APP_DIR/.git" ]]; then
    log "Existing git checkout detected at $APP_DIR"
    return
  fi

  if [[ -e "$APP_DIR" && -n "$(find "$APP_DIR" -mindepth 1 -maxdepth 1 2>/dev/null | head -n 1)" ]]; then
    echo "Target directory already exists and is not empty: $APP_DIR"
    echo "Set APP_DIR to an empty path or remove the existing contents."
    exit 1
  fi

  log "Cloning repository from $REPO_URL to $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"
  rm -rf "$APP_DIR"
  git clone "$REPO_URL" "$APP_DIR"
}

handoff_to_stage2() {
  if [[ ! -f "$STAGE2_SCRIPT" ]]; then
    echo "Stage 2 install script not found after clone: $STAGE2_SCRIPT"
    exit 1
  fi

  log "Handing off to stage 2 install script from cloned checkout"
  exec bash "$STAGE2_SCRIPT" "$@"
}

main() {
  require_root
  log "Starting stage 1 installation bootstrap"
  ensure_git_available
  clone_repository
  handoff_to_stage2 "$@"
}

main "$@"
