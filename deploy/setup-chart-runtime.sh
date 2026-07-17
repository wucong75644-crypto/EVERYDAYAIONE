#!/bin/bash
set -e

BACKEND_DIR="${1:-/var/www/everydayai/backend}"
cd "$BACKEND_DIR"

npm ci --omit=dev --ignore-scripts --prefix chart_runtime
source venv/bin/activate
if command -v apt-get >/dev/null 2>&1; then
    python -m playwright install --with-deps chromium
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y atk at-spi2-atk at-spi2-core libXcomposite libXdamage
    python -m playwright install chromium
else
    python -m playwright install chromium
fi

node -e "require('./chart_runtime/node_modules/echarts')"
python -c "from playwright.async_api import async_playwright"
PYTHONPATH=. python scripts/smoke_chart_renderer.py
echo "✅ 图表渲染运行时检查通过"
