#!/bin/bash
# nsjail 编译安装脚本（Alibaba Cloud Linux 3 / RHEL 8 系）
#
# 用法：sudo bash install_nsjail.sh
# 前提：root 权限、可联网
# 产出：/usr/local/bin/nsjail

set -euo pipefail

NSJAIL_VERSION="3.4"
NSJAIL_SRC="/opt/nsjail-src"

echo "=== 1. 安装编译依赖 ==="
dnf install -y epel-release
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

# 使用稳定版本
git checkout "$NSJAIL_VERSION" 2>/dev/null || echo "使用 HEAD（$NSJAIL_VERSION 标签不存在）"

echo "=== 3. 编译 ==="
make clean 2>/dev/null || true
make -j"$(nproc)"

echo "=== 4. 安装 ==="
cp nsjail /usr/local/bin/nsjail
chmod +x /usr/local/bin/nsjail

echo "=== 5. 验证 ==="
nsjail --version || nsjail --help 2>&1 | head -3

echo "=== 6. 清理编译产物（保留源码以备重新编译） ==="
make clean

echo ""
echo "nsjail 安装完成: $(which nsjail)"
echo "版本: $(nsjail --version 2>&1 || echo 'see --help')"
