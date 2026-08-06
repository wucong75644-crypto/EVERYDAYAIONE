#!/bin/bash
set -euo pipefail
test "$(id -u)" -eq 0
getent group everydayai-app >/dev/null || groupadd --system everydayai-app
getent group everydayai-sandbox-io >/dev/null ||
  groupadd --system everydayai-sandbox-io
getent group everydayai-sandbox >/dev/null ||
  groupadd --system everydayai-sandbox
getent group everydayai-model-gateway >/dev/null ||
  groupadd --system everydayai-model-gateway
for name in api actor wecom sync agent-runtime projection authorization; do
  id "everydayai-$name" >/dev/null 2>&1 || useradd --system \
    --no-create-home --shell /usr/sbin/nologin --gid everydayai-app \
    "everydayai-$name"
done
for name in sandbox; do
  id "everydayai-$name" >/dev/null 2>&1 || useradd --system \
    --no-create-home --shell /usr/sbin/nologin --gid everydayai-sandbox \
    "everydayai-$name"
done
id everydayai-agent-model-gateway >/dev/null 2>&1 || useradd --system \
  --no-create-home --shell /usr/sbin/nologin --gid everydayai-model-gateway \
  everydayai-agent-model-gateway
usermod -a -G everydayai-sandbox-io everydayai-agent-runtime
usermod -a -G everydayai-model-gateway everydayai-agent-runtime
usermod -a -G everydayai-sandbox-io everydayai-sandbox
install -d -o root -g everydayai-sandbox-io -m 2770 \
  /var/lib/everydayai/sandbox-jobs
install -d -o root -g everydayai-sandbox-io -m 0750 \
  /var/lib/everydayai/sandbox-rootfs
install -d -o root -g everydayai-app -m 2770 /var/log/everydayai
for name in agent-runtime projection authorization; do
  install -d -o "everydayai-$name" -g everydayai-app -m 0750 \
    "/var/log/everydayai/$name"
done
install -d -o everydayai-agent-model-gateway -g everydayai-model-gateway -m 0750 \
  /var/log/everydayai/model-gateway
install -d -o everydayai-sandbox -g everydayai-sandbox-io -m 0750 \
  /var/log/everydayai/sandbox
install -d -o root -g root -m 0751 /etc/everydayai
find /etc/everydayai -maxdepth 1 -type f -name '*.env' \
  -exec chown root:everydayai-app {} + -exec chmod 0640 {} +
if [ -f /etc/everydayai/sandbox-worker.env ]; then
  chown root:everydayai-sandbox-io /etc/everydayai/sandbox-worker.env
  chmod 0640 /etc/everydayai/sandbox-worker.env
fi
for name in agent-model-gateway.env agent-model-gateway-kek.env; do
  if [ -f "/etc/everydayai/$name" ]; then
    chown root:everydayai-model-gateway "/etc/everydayai/$name"
    chmod 0640 "/etc/everydayai/$name"
  fi
done
if [ -f /etc/everydayai/sandbox-job.policy ]; then
  chown root:everydayai-sandbox-io /etc/everydayai/sandbox-job.policy
  chmod 0640 /etc/everydayai/sandbox-job.policy
fi
find /var/www/everydayai/backend -maxdepth 1 -type f -name '.env*' \
  -exec chown root:everydayai-app {} + -exec chmod 0640 {} +
for name in .env.runtime .env.wecom-runtime .env.worker .env.worker-client \
  .env.migrator .env.sync .env.kek; do
  if [ -f "/var/www/everydayai/backend/$name" ]; then
    chmod 0600 "/var/www/everydayai/backend/$name"
  fi
done
if [ -f /var/www/everydayai/backend/.env ]; then
  chmod 0640 /var/www/everydayai/backend/.env
fi
install -d -o root -g everydayai-app -m 2770 \
  /var/www/everydayai/backend/logs
find /var/www/everydayai/backend/logs -maxdepth 1 -type f \
  -exec chown root:everydayai-app {} + -exec chmod 0660 {} +
for name in api actor wecom sync agent-runtime projection authorization; do
  runuser -u "everydayai-$name" -- test -r /var/www/everydayai/backend
  runuser -u "everydayai-$name" -- test -x /var/www/everydayai/backend
done
runuser -u everydayai-agent-model-gateway -- \
  test -r /var/www/everydayai/backend
if [ -f /etc/everydayai/agent-model-gateway.env ]; then
  runuser -u everydayai-agent-model-gateway -- \
    test -r /etc/everydayai/agent-model-gateway.env
fi
if [ -f /etc/everydayai/agent-model-gateway-kek.env ]; then
  runuser -u everydayai-agent-model-gateway -- \
    test -r /etc/everydayai/agent-model-gateway-kek.env
fi
if [ -f /etc/everydayai/sandbox-worker.env ]; then
  runuser -u everydayai-sandbox -- \
    test -r /etc/everydayai/sandbox-worker.env
fi
runuser -u everydayai-sandbox -- \
  test ! -r /var/www/everydayai/backend/.env
