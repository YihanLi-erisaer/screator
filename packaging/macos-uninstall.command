#!/bin/sh
set -eu
# Launch directly from this DMG, before the normal installer/single-instance
# path. Never execute an older installed worker to clean the new data layout.
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$script_dir/screator.app/Contents/MacOS/screator" --uninstall
