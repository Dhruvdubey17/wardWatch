#!/usr/bin/env bash
# The macOS xgboost wheel links @rpath/libomp.dylib and looks for it only in
# Homebrew's prefix. scikit-learn's wheel ships a libomp.dylib, so this adds
# an rpath from libxgboost.dylib to that copy and re-signs the library ad hoc.
# Everything it touches is inside python/.venv. Safe to run repeatedly; does
# nothing on other platforms.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  exit 0
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
site_packages="$(cd "$repo_root/python" && uv run python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
library="$site_packages/xgboost/lib/libxgboost.dylib"
openmp_dir="$site_packages/sklearn/.dylibs"
rpath="@loader_path/../../sklearn/.dylibs"

if [[ ! -f "$library" || ! -f "$openmp_dir/libomp.dylib" ]]; then
  echo "macos_openmp_rpath: xgboost or scikit-learn is not installed yet" >&2
  exit 0
fi
if otool -l "$library" | grep -q "path $rpath "; then
  exit 0
fi
install_name_tool -add_rpath "$rpath" "$library"
codesign --force --sign - "$library"
echo "macos_openmp_rpath: libxgboost.dylib now finds libomp in scikit-learn's wheel"
