#!/usr/bin/env bash
# Runs unattended Claude Code passes over this repo until PROGRESS.md says
# STATUS: COMPLETE. It passes --dangerously-skip-permissions, so run it only
# inside a disposable VM or dev container, never on a workstation.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

max_passes=40
max_idle_passes=3
log_file="logs/autopilot.log"
prompt="Continue building WardWatch. Follow CLAUDE.md exactly and resume from PROGRESS.md."

if ! command -v claude >/dev/null 2>&1; then
  echo "autopilot: the claude CLI is not on PATH, stopping." >&2
  exit 1
fi

mkdir -p logs

log() {
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$log_file"
}

head_commit() {
  git rev-parse --verify --quiet HEAD || echo none
}

idle_passes=0
for pass in $(seq 1 "$max_passes"); do
  if [[ "$(head -n 1 PROGRESS.md)" == "STATUS: COMPLETE" ]]; then
    log "PROGRESS.md reports STATUS: COMPLETE, stopping."
    exit 0
  fi

  before="$(head_commit)"
  log "pass $pass of $max_passes starting at commit $before"
  status=0
  claude -p "$prompt" --dangerously-skip-permissions >>"$log_file" 2>&1 || status=$?
  after="$(head_commit)"
  log "pass $pass finished with exit code $status at commit $after"

  if [[ "$before" == "$after" ]]; then
    idle_passes=$((idle_passes + 1))
    if (( idle_passes >= max_idle_passes )); then
      log "no new commit in $max_idle_passes passes in a row, stopping."
      exit 1
    fi
  else
    idle_passes=0
  fi
done

log "reached $max_passes passes without STATUS: COMPLETE, stopping."
exit 1
