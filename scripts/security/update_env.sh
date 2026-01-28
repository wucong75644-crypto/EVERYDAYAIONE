#!/bin/bash

echo "=== Supabase 密钥更新助手 ==="
echo ""
echo "请按照以下步骤操作："
echo ""
echo "1. 访问: https://supabase.com/dashboard/project/qcaatwmlzqqnzfjdzlzm/settings/api"
echo "2. 点击 'service_role' 旁边的 [Reset] 按钮"
echo "3. 复制新生成的密钥"
echo "4. 粘贴到下方："
echo ""
read -p "请输入新的 Service Role Key: " NEW_SERVICE_KEY
echo ""

if [ -z "$NEW_SERVICE_KEY" ]; then
    echo "❌ 未输入密钥，退出"
    exit 1
fi

# 备份原文件
cp /Users/wucong/EVERYDAYAIONE/backend/.env /Users/wucong/EVERYDAYAIONE/backend/.env.backup.$(date +%Y%m%d_%H%M%S)
echo "✅ 已备份原 .env 文件"

# 更新 Service Role Key
sed -i.tmp "s|^SUPABASE_SERVICE_ROLE_KEY=.*|SUPABASE_SERVICE_ROLE_KEY=$NEW_SERVICE_KEY|" /Users/wucong/EVERYDAYAIONE/backend/.env
rm /Users/wucong/EVERYDAYAIONE/backend/.env.tmp

echo "✅ 已更新 SUPABASE_SERVICE_ROLE_KEY"
echo ""
echo "是否也要重置 Anon Key？(y/n)"
read -p "> " RESET_ANON

if [ "$RESET_ANON" = "y" ]; then
    echo ""
    echo "请在 Dashboard 中点击 'anon / public' 旁边的 [Reset] 按钮"
    read -p "请输入新的 Anon Key: " NEW_ANON_KEY
    
    if [ ! -z "$NEW_ANON_KEY" ]; then
        sed -i.tmp "s|^SUPABASE_ANON_KEY=.*|SUPABASE_ANON_KEY=$NEW_ANON_KEY|" /Users/wucong/EVERYDAYAIONE/backend/.env
        rm /Users/wucong/EVERYDAYAIONE/backend/.env.tmp
        echo "✅ 已更新 SUPABASE_ANON_KEY"
    fi
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ 密钥更新完成！"
echo ""
echo "⚠️  接下来需要："
echo "1. 重启后端服务"
echo "2. 测试数据库连接"
echo ""
echo "备份文件位置："
ls -lt /Users/wucong/EVERYDAYAIONE/backend/.env.backup.* | head -1
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

