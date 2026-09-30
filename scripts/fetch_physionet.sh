#!/usr/bin/env bash
# Download the PhysioNet 2019 Challenge training sets into data/physionet/.
#
# PhysioNet publishes SHA-256 sums only for the top-level files of this
# project, not for the 40,336 .psv files, so the script verifies LICENSE.txt by
# checksum and every .psv by its header and the published file counts. Files
# already present are skipped, so reruns resume an interrupted download.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data_dir="${WARDWATCH_PHYSIONET_DIR:-$repo_root/data/physionet}"
base_url="https://physionet.org/files/challenge-2019/1.0.0"
parallel_downloads="${PARALLEL_DOWNLOADS:-8}"
expected_header="HR|O2Sat|Temp|SBP|MAP|DBP|Resp|EtCO2|BaseExcess|HCO3|FiO2|pH|PaCO2|SaO2|AST|BUN|Alkalinephos|Calcium|Chloride|Creatinine|Bilirubin_direct|Glucose|Lactate|Magnesium|Phosphate|Potassium|Bilirubin_total|TroponinI|Hct|Hgb|PTT|WBC|Fibrinogen|Platelets|Age|Gender|Unit1|Unit2|HospAdmTime|ICULOS|SepsisLabel"

# Counts from the PhysioNet 2019 project page. Written as a function because
# the bash 3.2 that ships with macOS has no associative arrays.
expected_count() {
  case "$1" in
    training_setA) echo 20336 ;;
    training_setB) echo 20000 ;;
  esac
}

mkdir -p "$data_dir"

curl --fail --silent --show-error --retry 3 -o "$data_dir/SHA256SUMS.txt" "$base_url/SHA256SUMS.txt"
curl --fail --silent --show-error --retry 3 -o "$data_dir/LICENSE.txt" "$base_url/LICENSE.txt"
# SHA256SUMS.txt separates hash and name with one space; shasum wants two.
(cd "$data_dir" && grep ' LICENSE.txt$' SHA256SUMS.txt | awk '{print $1 "  " $2}' | shasum -a 256 -c -)

for set_name in training_setA training_setB; do
  set_dir="$data_dir/$set_name"
  mkdir -p "$set_dir"
  listing="$set_dir/.listing"
  curl --fail --silent --show-error --retry 3 "$base_url/training/$set_name/" \
    | grep -oE 'href="p[0-9]+\.psv"' | sed -E 's/href="(.*)"/\1/' | sort -u >"$listing"

  listed="$(wc -l <"$listing" | tr -d ' ')"
  if [[ "$listed" != "$(expected_count "$set_name")" ]]; then
    echo "fetch_physionet: $set_name lists $listed files, expected $(expected_count "$set_name")" >&2
    exit 1
  fi

  missing="$set_dir/.missing"
  : >"$missing"
  while read -r file_name; do
    [[ -s "$set_dir/$file_name" ]] || echo "$file_name" >>"$missing"
  done <"$listing"

  echo "fetch_physionet: $set_name has $(wc -l <"$missing" | tr -d ' ') of $listed files left to download"
  # Download to a temporary name and rename, so an interrupted run never
  # leaves a partial file that the skip check above would accept.
  xargs -P "$parallel_downloads" -I {} sh -c \
    'curl --fail --silent --show-error --retry 3 -o "$1/$2.part" "$0/training/$3/$2" && mv "$1/$2.part" "$1/$2"' \
    "$base_url" "$set_dir" {} "$set_name" <"$missing"

  bad_headers=0
  while read -r file_name; do
    if [[ "$(head -n 1 "$set_dir/$file_name")" != "$expected_header" ]]; then
      echo "fetch_physionet: unexpected header in $set_name/$file_name" >&2
      bad_headers=$((bad_headers + 1))
    fi
  done <"$listing"
  if (( bad_headers > 0 )); then
    exit 1
  fi
  rm -f "$missing"
done

echo "fetch_physionet: done, data in $data_dir"
