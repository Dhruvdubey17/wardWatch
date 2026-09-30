#!/usr/bin/env bash
# Runs Postgres and a single-node Kafka (KRaft) for integration tests on a
# machine without Docker. Everything lives under .tools/services and binds to
# 127.0.0.1. Postgres comes from the host's PostgreSQL install; Kafka is the
# Apache 3.9.1 release, downloaded into .tools and checked against its SHA-512.
#
#   scripts/local_services.sh start   # prints the environment to export
#   scripts/local_services.sh stop
#
# The test fixtures use these services when WARDWATCH_TEST_POSTGRES_URL and
# WARDWATCH_TEST_KAFKA_BOOTSTRAP are set, and testcontainers otherwise.
set -euo pipefail
# Postgres on macOS refuses to start without a valid locale in LC_ALL.
export LC_ALL=C

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
services="$repo_root/.tools/services"
downloads="$repo_root/.tools/downloads"
kafka_version="3.9.1"
kafka_archive="kafka_2.13-$kafka_version.tgz"
kafka_url="https://archive.apache.org/dist/kafka/$kafka_version/$kafka_archive"
kafka_home="$repo_root/.tools/kafka_2.13-$kafka_version"
pg_port="${WARDWATCH_LOCAL_PG_PORT:-55432}"
kafka_port="${WARDWATCH_LOCAL_KAFKA_PORT:-59092}"
controller_port=$((kafka_port + 1))
env_file="$services/env"

ensure_kafka() {
  if [[ -x "$kafka_home/bin/kafka-server-start.sh" ]]; then
    return
  fi
  mkdir -p "$downloads"
  if [[ ! -f "$downloads/$kafka_archive" ]]; then
    curl --fail --location --silent --show-error -o "$downloads/$kafka_archive.part" "$kafka_url"
    mv "$downloads/$kafka_archive.part" "$downloads/$kafka_archive"
  fi
  curl --fail --location --silent --show-error -o "$downloads/$kafka_archive.sha512" "$kafka_url.sha512"
  # Apache publishes the digest as "name: HEX HEX ..." split over lines.
  expected="$(tr -d ' \n' <"$downloads/$kafka_archive.sha512" | cut -d: -f2 | tr 'A-F' 'a-f')"
  actual="$(shasum -a 512 "$downloads/$kafka_archive" | cut -d' ' -f1)"
  if [[ "$expected" != "$actual" ]]; then
    echo "local_services: $kafka_archive failed its SHA-512 check" >&2
    exit 1
  fi
  tar -xzf "$downloads/$kafka_archive" -C "$repo_root/.tools"
}

start_postgres() {
  local data="$services/postgres"
  if [[ ! -f "$data/PG_VERSION" ]]; then
    mkdir -p "$data"
    initdb --pgdata "$data" --username wardwatch --auth trust --encoding UTF8 --no-locale >/dev/null
  fi
  if ! pg_ctl --pgdata "$data" status >/dev/null 2>&1; then
    pg_ctl --pgdata "$data" --log "$services/postgres.log" --wait \
      -o "-c listen_addresses=127.0.0.1 -p $pg_port -k $services" start >/dev/null
  fi
  local exists
  exists="$(psql --host 127.0.0.1 --port "$pg_port" --username wardwatch --dbname postgres --tuples-only \
    --command "SELECT 1 FROM pg_database WHERE datname = 'wardwatch_test'" | tr -d ' ')"
  if [[ "$exists" != "1" ]]; then
    createdb --host 127.0.0.1 --port "$pg_port" --username wardwatch wardwatch_test
  fi
}

start_kafka() {
  ensure_kafka
  local data="$services/kafka"
  local config="$services/kraft.properties"
  cat >"$config" <<PROPERTIES
process.roles=broker,controller
node.id=1
controller.quorum.voters=1@127.0.0.1:$controller_port
listeners=PLAINTEXT://127.0.0.1:$kafka_port,CONTROLLER://127.0.0.1:$controller_port
advertised.listeners=PLAINTEXT://127.0.0.1:$kafka_port
controller.listener.names=CONTROLLER
listener.security.protocol.map=PLAINTEXT:PLAINTEXT,CONTROLLER:PLAINTEXT
log.dirs=$data
num.partitions=3
offsets.topic.replication.factor=1
transaction.state.log.replication.factor=1
transaction.state.log.min.isr=1
group.initial.rebalance.delay.ms=0
auto.create.topics.enable=true
PROPERTIES
  if [[ ! -f "$data/meta.properties" ]]; then
    mkdir -p "$data"
    "$kafka_home/bin/kafka-storage.sh" format --config "$config" \
      --cluster-id "$("$kafka_home/bin/kafka-storage.sh" random-uuid)" >/dev/null
  fi
  if [[ -f "$services/kafka.pid" ]] && kill -0 "$(cat "$services/kafka.pid")" 2>/dev/null; then
    return
  fi
  LOG_DIR="$services/kafka-logs" nohup "$kafka_home/bin/kafka-server-start.sh" "$config" \
    >"$services/kafka.log" 2>&1 &
  echo $! >"$services/kafka.pid"
  for _ in $(seq 1 60); do
    if "$kafka_home/bin/kafka-topics.sh" --bootstrap-server "127.0.0.1:$kafka_port" --list >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  echo "local_services: Kafka did not start; see $services/kafka.log" >&2
  exit 1
}

case "${1:-}" in
  start)
    mkdir -p "$services"
    start_postgres
    start_kafka
    cat >"$env_file" <<ENV
export WARDWATCH_TEST_POSTGRES_URL=postgresql+asyncpg://wardwatch@127.0.0.1:$pg_port/wardwatch_test
export WARDWATCH_TEST_KAFKA_BOOTSTRAP=127.0.0.1:$kafka_port
export WARDWATCH_KAFKA_BROKERS=127.0.0.1:$kafka_port
ENV
    cat "$env_file"
    ;;
  stop)
    if [[ -f "$services/kafka.pid" ]]; then
      pid="$(cat "$services/kafka.pid")"
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
      fi
      rm -f "$services/kafka.pid"
    fi
    if [[ -f "$services/postgres/PG_VERSION" ]] && pg_ctl --pgdata "$services/postgres" status >/dev/null 2>&1; then
      pg_ctl --pgdata "$services/postgres" stop >/dev/null
    fi
    ;;
  *)
    echo "usage: $0 start|stop" >&2
    exit 2
    ;;
esac
