#!/usr/bin/env bash
# Builds the ingest coverage preset, runs the unit and integration suites,
# and fails when line coverage of the library (src/ and include/) is below
# the gate. The server binary spawned by the integration tests writes its own
# profile, so it is included in the report.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ingest_dir="$repo_root/ingest"
build_dir="$ingest_dir/build/coverage"
profile_dir="$build_dir/profiles"
gate="${INGEST_COVERAGE_GATE:-90}"

if command -v xcrun >/dev/null 2>&1; then
  llvm_profdata=(xcrun llvm-profdata)
  llvm_cov=(xcrun llvm-cov)
else
  llvm_profdata=("$(command -v llvm-profdata || command -v llvm-profdata-18)")
  llvm_cov=("$(command -v llvm-cov || command -v llvm-cov-18)")
fi

cd "$ingest_dir"
cmake --preset coverage >/dev/null
cmake --build --preset coverage
grep -q '^WARDWATCH_COVERAGE:BOOL=ON$' "$build_dir/CMakeCache.txt" || {
  echo "ingest_coverage: build/coverage lost WARDWATCH_COVERAGE; delete its CMakeCache.txt" >&2
  exit 1
}

rm -rf "$profile_dir"
mkdir -p "$profile_dir"
# The Kafka round trip runs only when a broker is configured (as in CI).
if [[ -n "${WARDWATCH_KAFKA_BROKERS:-}" ]]; then
  ctest --preset coverage
else
  ctest --preset coverage --label-exclude kafka
fi

"${llvm_profdata[@]}" merge -sparse "$profile_dir"/*.profraw -o "$build_dir/merged.profdata"

objects=(
  "$build_dir/tests/wardwatch_unit_tests"
  -object "$build_dir/tests/wardwatch_integration_tests"
  -object "$build_dir/apps/wardwatch-ingest"
)
if [[ -x "$build_dir/tests/wardwatch_kafka_tests" ]]; then
  objects+=(-object "$build_dir/tests/wardwatch_kafka_tests")
fi
sources=("$ingest_dir/src" "$ingest_dir/include")

"${llvm_cov[@]}" report "${objects[@]}" -instr-profile="$build_dir/merged.profdata" "${sources[@]}" \
  | tee "$build_dir/report.txt"
"${llvm_cov[@]}" export "${objects[@]}" -instr-profile="$build_dir/merged.profdata" \
  -summary-only "${sources[@]}" >"$build_dir/summary.json"

line_percent="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['data'][0]['totals']['lines']['percent'])" "$build_dir/summary.json")"
echo "ingest_coverage: library line coverage ${line_percent}% (gate ${gate}%)"
python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) >= float(sys.argv[2]) else 1)" "$line_percent" "$gate" || {
  echo "ingest_coverage: below the ${gate}% gate" >&2
  exit 1
}
