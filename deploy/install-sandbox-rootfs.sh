#!/bin/bash
set -euo pipefail

test "$(id -u)" -eq 0
revision=${1:?usage: install-sandbox-rootfs.sh REVISION RELEASE_ASSET_DIR}
asset_dir=${2:?usage: install-sandbox-rootfs.sh REVISION RELEASE_ASSET_DIR}
[[ "$revision" =~ ^[a-z0-9][a-z0-9._-]{0,127}$ ]]

archive=$(realpath "$asset_dir/rootfs.tar.gz")
manifest=$(realpath "$asset_dir/rootfs.manifest")
checksums=$(realpath "$asset_dir/SHA256SUMS")
test -f "$archive"
test -f "$manifest"
test -f "$checksums"
(
  cd "$asset_dir"
  sha256sum -c SHA256SUMS
)

install_root=/var/lib/everydayai/sandbox-rootfs
target=$install_root/$revision
test ! -e "$target"
install -d -o root -g everydayai-sandbox-io -m 0750 "$install_root"
temporary=$(mktemp -d "$install_root/.install-$revision.XXXXXX")
cleanup() {
  if [ -n "${temporary:-}" ] && [ -d "$temporary" ]; then
    rm -rf -- "$temporary"
  fi
}
trap cleanup EXIT
install -d -o root -g everydayai-sandbox-io -m 0750 "$temporary/rootfs"
tar --extract --gzip --file "$archive" --directory "$temporary/rootfs" \
  --same-owner --numeric-owner
install -o root -g everydayai-sandbox-io -m 0640 \
  "$manifest" "$temporary/rootfs.manifest"
/var/www/everydayai/backend/venv/bin/python \
  /var/www/everydayai/backend/services/agent/runtime/sandbox/rootfs_manifest.py \
  verify \
  "$temporary/rootfs" "$temporary/rootfs.manifest"
chown root:everydayai-sandbox-io "$temporary"
chmod 0750 "$temporary"
mv "$temporary" "$target"
temporary=
trap - EXIT
echo "sandbox-rootfs-installed=$target"
