#!/usr/bin/env bash
# Brings the compose stack up, replays the committed fixtures (one stay
# deteriorates within its first hours), and passes once an alert can be read
# through the API. The stack is torn down afterwards, pass or fail.
#
#   scripts/smoke_stack.sh            # WARDWATCH_SMOKE_TIMEOUT seconds to wait (default 180)
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose=(docker compose -f "$repo_root/infra/docker-compose.yml")
timeout="${WARDWATCH_SMOKE_TIMEOUT:-180}"
api="http://127.0.0.1:8000"

teardown() {
  "${compose[@]}" --profile demo down --volumes --remove-orphans
}
trap teardown EXIT

mkdir -p "$repo_root/ml/artifacts"
"${compose[@]}" up --build --detach --wait ingest fhir-service scorer frontend
"${compose[@]}" --profile demo run --rm --detach -e WARDWATCH_DEMO_INPUT=fixtures simulator --seconds-per-hour 1

deadline=$((SECONDS + timeout))
while ((SECONDS < deadline)); do
  count="$(curl --silent --fail "$api/api/alerts" | python3 -c 'import json, sys; print(len(json.load(sys.stdin)))' 2>/dev/null)" || count=0
  if ((count > 0)); then
    echo "smoke: $count alerts readable through $api/api/alerts after $SECONDS s"
    curl --silent --fail --output /dev/null "http://127.0.0.1:3000/"
    echo "smoke: dashboard answers on http://127.0.0.1:3000"
    exit 0
  fi
  sleep 2
done
echo "smoke: no alert reached the API within $timeout s" >&2
"${compose[@]}" logs --tail 50 >&2
exit 1
