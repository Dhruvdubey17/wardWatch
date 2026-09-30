#!/usr/bin/env bash
# Runs the whole WardWatch pipeline as local processes, for machines without
# Docker: ingest, FHIR service, scorer and the dashboard, on top of the Postgres
# and Kafka from scripts/local_services.sh. Every port binds to 127.0.0.1.
#
#   scripts/local_stack.sh start   # fresh database and topics, then every service
#   scripts/local_stack.sh stop    # stops only the processes this script started
#
# The compose stack in infra/ is the supported way to run WardWatch; this
# script exists so the end to end tests can run where Docker cannot.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stack="$repo_root/.tools/stack"
kafka_home="$repo_root/.tools/kafka_2.13-3.9.1"
pnpm="$repo_root/.tools/node/node_modules/.bin/pnpm"
pg_port="${WARDWATCH_LOCAL_PG_PORT:-55432}"
kafka="127.0.0.1:${WARDWATCH_LOCAL_KAFKA_PORT:-59092}"
database="wardwatch_stack"
topics=(hl7.validated hl7.deadletter fhir.observations ward.alerts ward.alert-events ward.scores)
services=(frontend scorer fhir ingest)

running() {
  [[ -f "$stack/$1.pid" ]] && kill -0 "$(cat "$stack/$1.pid")" 2>/dev/null
}

launch() {
  local name="$1"
  shift
  nohup "$@" >"$stack/$name.log" 2>&1 &
  echo $! >"$stack/$name.pid"
}

wait_for() {
  local name="$1" url="$2"
  for _ in $(seq 1 90); do
    if curl --silent --fail --output /dev/null "$url"; then
      return
    fi
    if ! running "$name"; then
      echo "local_stack: $name exited; see $stack/$name.log" >&2
      exit 1
    fi
    sleep 1
  done
  echo "local_stack: $name did not answer at $url; see $stack/$name.log" >&2
  exit 1
}

reset_topics() {
  local existing
  existing="$("$kafka_home/bin/kafka-topics.sh" --bootstrap-server "$kafka" --list)"
  for topic in "${topics[@]}"; do
    if grep -qx "$topic" <<<"$existing"; then
      "$kafka_home/bin/kafka-topics.sh" --bootstrap-server "$kafka" --delete --topic "$topic"
    fi
  done
  for topic in "${topics[@]}"; do
    for _ in $(seq 1 30); do
      if "$kafka_home/bin/kafka-topics.sh" --bootstrap-server "$kafka" --create --if-not-exists \
        --topic "$topic" --partitions 3 --replication-factor 1 >/dev/null 2>&1; then
        break
      fi
      sleep 1 # a deleted topic takes a moment to disappear
    done
  done
}

start() {
  for name in "${services[@]}"; do
    if running "$name"; then
      echo "local_stack: $name is already running; run $0 stop first" >&2
      exit 1
    fi
  done
  mkdir -p "$stack"
  "$repo_root/scripts/local_services.sh" start >/dev/null
  dropdb --host 127.0.0.1 --port "$pg_port" --username wardwatch --if-exists --force "$database"
  createdb --host 127.0.0.1 --port "$pg_port" --username wardwatch "$database"
  reset_topics

  (cd "$repo_root/ingest" && cmake --preset release >/dev/null && cmake --build --preset release --target wardwatch-ingest >/dev/null)
  "$repo_root/scripts/macos_openmp_rpath.sh"

  export WARDWATCH_FHIR_DATABASE_URL="postgresql+asyncpg://wardwatch@127.0.0.1:$pg_port/$database"
  export WARDWATCH_FHIR_KAFKA_BOOTSTRAP="$kafka"
  export WARDWATCH_SCORER_KAFKA_BOOTSTRAP="$kafka"
  export WARDWATCH_SCORER_FHIR_BASE_URL="http://127.0.0.1:8000"
  if [[ -f "$repo_root/ml/artifacts/current/bundle.json" ]]; then
    export WARDWATCH_SCORER_BUNDLE_DIR="$repo_root/ml/artifacts/current"
  fi

  (cd "$repo_root/python" && uv run --quiet wardwatch-fhir migrate)
  launch fhir bash -c "cd '$repo_root/python' && exec uv run --quiet wardwatch-fhir serve --host 127.0.0.1 --port 8000"
  wait_for fhir http://127.0.0.1:8000/readyz

  launch ingest "$repo_root/ingest/build/release/apps/wardwatch-ingest" --bind 127.0.0.1 --port 2575 \
    --metrics-bind 127.0.0.1 --metrics-port 9464 --sink kafka --kafka-brokers "$kafka"
  wait_for ingest http://127.0.0.1:9464/metrics

  launch scorer bash -c "cd '$repo_root/python' && exec uv run --quiet wardwatch-scorer"
  wait_for scorer http://127.0.0.1:9465/metrics

  # Rewrites are fixed when the dashboard is built, so the build names the API.
  WARDWATCH_API_URL=http://127.0.0.1:8000 "$pnpm" --dir "$repo_root/frontend" build >"$stack/frontend-build.log" 2>&1
  launch frontend "$pnpm" --dir "$repo_root/frontend" exec next start --hostname 127.0.0.1 --port 3000
  wait_for frontend http://127.0.0.1:3000/

  echo "local_stack: dashboard http://127.0.0.1:3000, FHIR API http://127.0.0.1:8000, MLLP 127.0.0.1:2575"
}

# The process and everything it started: uv and pnpm run services as children
# or grandchildren.
tree() {
  local child
  for child in $(pgrep -P "$1"); do
    tree "$child"
  done
  echo "$1"
}

signal_all() {
  local signal="$1" pid
  shift
  for pid in "$@"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "-$signal" "$pid"
    fi
  done
}

stop() {
  local pids=()
  for name in "${services[@]}"; do
    if running "$name"; then
      read -r -a found <<<"$(tree "$(cat "$stack/$name.pid")" | tr '\n' ' ')"
      pids+=("${found[@]}")
    fi
    rm -f "$stack/$name.pid"
  done
  if [[ ${#pids[@]} -eq 0 ]]; then
    return
  fi
  signal_all TERM "${pids[@]}"
  # Open event streams can hold a server in graceful shutdown, so after ten
  # seconds whatever is left is killed, as docker stop does.
  for _ in $(seq 1 10); do
    local alive=0 pid
    for pid in "${pids[@]}"; do
      if kill -0 "$pid" 2>/dev/null; then
        alive=1
      fi
    done
    if [[ $alive -eq 0 ]]; then
      return
    fi
    sleep 1
  done
  signal_all KILL "${pids[@]}"
}

case "${1:-}" in
  start) start ;;
  stop) stop ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
