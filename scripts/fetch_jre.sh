#!/usr/bin/env bash
# Downloads the Eclipse Temurin 17 JRE into .tools/jre17 for Synthea, on hosts
# whose own Java is older. The release is pinned and each platform's archive is
# checked against the SHA-256 Adoptium publishes for it. Prints the java path.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release="jdk-17.0.20.1+1"
file_version="17.0.20.1_1"
target="$repo_root/.tools/jre17"
downloads="$repo_root/.tools/downloads"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) platform=aarch64_mac sha256=190480874ccceb358cbc840393207f77ac3e63a4c5f8129d0e23e9518b96ad05 ;;
  Darwin-x86_64) platform=x64_mac sha256=333cb81123c36568586646c73c8fa2326dab8badc43f5ea388a90fff59c9df27 ;;
  Linux-aarch64) platform=aarch64_linux sha256=b8efcd5acc9109fe8d35bed132499643048a257b4f6042906ece37d03c839d77 ;;
  Linux-x86_64) platform=x64_linux sha256=0b2b640e3046b64c8ec504de0ab9d91bb5610182bda21fad454681ce54d45a62 ;;
  *)
    echo "fetch_jre: no pinned JRE for $(uname -s) $(uname -m)" >&2
    exit 1
    ;;
esac

java_bin() {
  find "$target" -path '*/bin/java' -type f -perm -u+x | head -n 1
}

if [[ -d "$target" && -n "$(java_bin)" ]]; then
  java_bin
  exit 0
fi

archive="OpenJDK17U-jre_${platform}_hotspot_${file_version}.tar.gz"
mkdir -p "$downloads"
if [[ ! -f "$downloads/$archive" ]]; then
  curl --fail --location --silent --show-error --retry 3 -o "$downloads/$archive.part" \
    "https://github.com/adoptium/temurin17-binaries/releases/download/${release/+/%2B}/$archive"
  mv "$downloads/$archive.part" "$downloads/$archive"
fi
echo "$sha256  $downloads/$archive" | shasum -a 256 -c - >&2
mkdir -p "$target"
tar -xzf "$downloads/$archive" -C "$target"
java_bin
