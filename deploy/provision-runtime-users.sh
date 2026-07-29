#!/bin/bash
set -euo pipefail
test "$(id -u)" -eq 0
getent group everydayai-app >/dev/null || groupadd --system everydayai-app
getent group everydayai-sandbox-io >/dev/null ||
  groupadd --system everydayai-sandbox-io
getent group everydayai-sandbox >/dev/null ||
  groupadd --system everydayai-sandbox
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
usermod -a -G everydayai-sandbox-io everydayai-agent-runtime
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
install -d -o everydayai-sandbox -g everydayai-sandbox-io -m 0750 \
  /var/log/everydayai/sandbox
install -d -o root -g root -m 0751 /etc/everydayai
find /etc/everydayai -maxdepth 1 -type f -name '*.env' \
  -exec chown root:everydayai-app {} + -exec chmod 0640 {} +
if [ -f /etc/everydayai/sandbox-worker.env ]; then
  chown root:everydayai-sandbox-io /etc/everydayai/sandbox-worker.env
  chmod 0640 /etc/everydayai/sandbox-worker.env
fi
if [ -f /etc/everydayai/sandbox-job.policy ]; then
  chown root:everydayai-sandbox-io /etc/everydayai/sandbox-job.policy
  chmod 0640 /etc/everydayai/sandbox-job.policy
fi
find /var/www/everydayai/backend -maxdepth 1 -type f -name '.env*' \
  -exec chown root:everydayai-app {} + -exec chmod 0640 {} +
for name in api actor wecom sync agent-runtime projection authorization; do
  runuser -u "everydayai-$name" -- test -r /var/www/everydayai/backend
  runuser -u "everydayai-$name" -- test -x /var/www/everydayai/backend
done
if [ -f /etc/everydayai/sandbox-worker.env ]; then
  runuser -u everydayai-sandbox -- \
    test -r /etc/everydayai/sandbox-worker.env
fi
runuser -u everydayai-sandbox -- \
  test ! -r /var/www/everydayai/backend/.env
