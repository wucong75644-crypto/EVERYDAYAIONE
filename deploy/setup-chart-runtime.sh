#!/bin/bash
set -e

BACKEND_DIR="${1:-/var/www/everydayai/backend}"
cd "$BACKEND_DIR"

npm ci --omit=dev --ignore-scripts --prefix chart_runtime
source venv/bin/activate
python -m playwright install chromium

node -e "require('./chart_runtime/node_modules/echarts')"
python -c "from playwright.async_api import async_playwright"
python scripts/smoke_chart_renderer.py
echo "✅ 图表渲染运行时检查通过"
