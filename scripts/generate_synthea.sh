#!/usr/bin/env bash
# Generate synthetic patient identities with Synthea into data/synthea/.
#
# The jar is pinned to a release and checked against the SHA-256 digest that
# GitHub publishes for the asset. The seed, clinician seed and reference date
# are fixed, so the same population comes out on every run. When the output
# directory already holds bundles for this configuration the script exits.
# Synthea needs Java 17; if the host java is older, the jar runs in an
# eclipse-temurin:17-jre container.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
synthea_version="v3.4.0"
synthea_sha256="38678aab1e667d26671163824aad60ee5e30fa366f3d59d5ea5765ebb5702432"
jar_url="https://github.com/synthetichealth/synthea/releases/download/$synthea_version/synthea-with-dependencies.jar"
population="${SYNTHEA_POPULATION:-500}"
seed="${SYNTHEA_SEED:-2019}"
reference_date="20240101"

cache_dir="$repo_root/data/cache"
output_dir="$repo_root/data/synthea"
jar_path="$cache_dir/synthea-$synthea_version.jar"
stamp="$output_dir/.generated-$synthea_version-seed$seed-p$population-r$reference_date"

if [[ -f "$stamp" ]]; then
  echo "generate_synthea: $output_dir already matches this configuration"
  exit 0
fi

mkdir -p "$cache_dir"
if [[ ! -f "$jar_path" ]]; then
  curl --fail --location --silent --show-error --retry 3 -o "$jar_path.part" "$jar_url"
  mv "$jar_path.part" "$jar_path"
fi
echo "$synthea_sha256  $jar_path" | shasum -a 256 -c -

# Clear only the bundles from a previous configuration, never the directory.
mkdir -p "$output_dir/fhir"
find "$output_dir/fhir" -maxdepth 1 -name '*.json' -type f -delete
rm -f "$output_dir"/.generated-*

synthea_args=(
  -s "$seed" -cs "$seed" -r "$reference_date" -p "$population"
  --exporter.baseDirectory=/synthea-output
  --exporter.fhir.export=true
  --exporter.hospital.fhir.export=false
  --exporter.practitioner.fhir.export=false
  --exporter.csv.export=false
  --generate.only_alive_patients=true
  Massachusetts
)

java_major() {
  java -version 2>&1 | sed -nE 's/.*version "([0-9]+).*/\1/p' | head -n 1
}

if command -v java >/dev/null 2>&1 && (( "$(java_major)" >= 17 )); then
  args=("${synthea_args[@]/\/synthea-output/$output_dir}")
  (cd "$cache_dir" && java -jar "$jar_path" "${args[@]}")
else
  docker run --rm --name wardwatch-synthea \
    -v "$cache_dir:/synthea-cache:ro" -v "$output_dir:/synthea-output" \
    -w /tmp eclipse-temurin:17-jre \
    java -jar "/synthea-cache/$(basename "$jar_path")" "${synthea_args[@]}"
fi

touch "$stamp"
echo "generate_synthea: wrote $(find "$output_dir/fhir" -name '*.json' | wc -l | tr -d ' ') bundles to $output_dir/fhir"
