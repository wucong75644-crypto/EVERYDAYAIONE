#!/bin/bash
# nsjail 编译安装脚本（Alibaba Cloud Linux 3 / RHEL 8 系）
#
# 用法：sudo bash install_nsjail.sh
# 前提：root 权限、可联网
# 产出：/usr/local/bin/nsjail

set -euo pipefail

NSJAIL_VERSION="3.4"
NSJAIL_COMMIT="079d70dda4aa1edd9512cfd25ff1e47e316dc355"
NSJAIL_SRC="/opt/nsjail-src"
NSJAIL_DEST="/usr/local/lib/everydayai/nsjail/$NSJAIL_VERSION/nsjail"

echo "=== 1. 安装编译依赖 ==="
if rpm -q --quiet epel-aliyuncs-release || rpm -q --quiet epel-release; then
    echo "EPEL release 已由系统提供，跳过重复安装"
else
    dnf install -y epel-release
fi
dnf install -y gcc-c++ make git protobuf-compiler protobuf-devel libnl3-devel libcap-devel

echo "=== 2. 克隆 nsjail 源码 ==="
if [ -d "$NSJAIL_SRC" ]; then
    echo "源码目录已存在，跳过克隆"
    cd "$NSJAIL_SRC"
    git fetch --tags
else
    git clone https://github.com/google/nsjail.git "$NSJAIL_SRC"
    cd "$NSJAIL_SRC"
fi

git fetch --depth=1 origin "$NSJAIL_COMMIT"
git checkout --detach "$NSJAIL_COMMIT"
test "$(git rev-parse HEAD)" = "$NSJAIL_COMMIT"
git submodule update --init --recursive --depth=1

echo "=== 3. 编译 ==="
make clean 2>/dev/null || true
make -j"$(nproc)"

echo "=== 4. 安装 ==="
install -D -o root -g root -m 0755 nsjail "$NSJAIL_DEST"

echo "=== 5. 验证 ==="
if ! "$NSJAIL_DEST" --version >/dev/null 2>&1; then
    "$NSJAIL_DEST" --help >/dev/null 2>&1
fi
sha256sum "$NSJAIL_DEST"

echo "=== 6. 清理编译产物（保留源码以备重新编译） ==="
make clean

echo ""
echo "nsjail 安装完成: $NSJAIL_DEST"
echo "版本: $("$NSJAIL_DEST" --version 2>&1 || echo 'see --help')"
