# 04 根因修复：生产存储证据

日期：2026-09-10。此前用户允许只读生产排查。本次没有调用业务删除/恢复、没有修改生产配置、没有部署代码。

## 已执行：同机跨进程 flock

范围：此前问题会话所属 owner 的 staging 中已有、闲置的 `.lock` 文件；未遍历其他租户。原始带 owner 路径的探针仅在本地临时目录，不进入仓库。

步骤：主进程打开已有锁文件，非阻塞独占试探确认闲置，转共享锁；另起 Python 进程打开同一文件，共享获取成功后释放，尝试排他必须被阻塞。主进程释放后通过 stdin 通知子进程，子进程排他获取成功。IPC 屏障控制顺序，超时只用于防挂死。无新建文件、无文件内容读写，最后关闭文件描述符。

实际输出：

```text
filesystem: /mnt/nas-workspace, nfs, vers=3, nolock, local_lock=all
shared-overlap
exclusive-blocked
exclusive-after-release
PASS: existing NAS lock supports shared overlap and cross-process exclusion
```

服务元数据只读检查：everydayai-backend 和 everydayai-conversation-actor 都已加载，User=root / Group=root，同一生产主机。第一次查询了不存在的 everydayai-actor 服务名，返回 not-found；按现有部署脚本纠正名称后确认。第一次用 git 读取版本发现生产分发目录没有 .git；改读受控 `.release-provenance` 得到：

```text
commit=20636929f7b78346991b357630240d932ed5772d
mode=preview
recorded_at=2026-09-09T16:01:26Z
```

结论：**通过当前同机多个 worker 的锁机制验证**；未部署的新 helper 在本地双进程/真实文件调用中另有验证。本挂载的 local_lock=all 不能支持跨主机排他结论，未来横向扩容前必须重设计/验证分布式协调。不能把同机测试写成分布式 NAS 锁通过。

## 已执行：NAS 不覆盖发布

2026-09-10 用户明确“提交部署”后，作为本次发布必要的无业务副作用存储验证，执行已经审查的 [probe-04-nas-publication.py](probe-04-nas-publication.py)。检查仅在 TemporaryDirectory 新建的唯一 `.tool04-publication-probe-*` 目录内创建两份几十字节固定测试内容，无用户业务路径、数据库、OSS、业务 Handler 或配置变更。

实际命令：通过现有 deploy/config.env 指定的 SSH 主机，把该脚本传给 `/var/www/everydayai/backend/venv/bin/python - --nas-root /mnt/nas-workspace --approved-test-writes`。在成功发布后再次尝试同名 link，验证 FileExistsError、原字节及 inode 不变，最后清理自己的临时目录。

实际输出（退出 0）：

```text
PASS: complete publication; existing destination preserved
PASS: probe temporary directory removed
```

检查时间：2026-09-10，04:25:43 UTC 前完成；生产应用仍是发布前候选 20636929。P-02 存储证据已补齐；结合本地真实 restore/upload/helper 测试，当前同机部署的技术验收通过。后续确定发布 SHA 由受控 RELEASE_RESULT 及最终交付消息记录，不能以本探针替代新版本生产业务验收。真实业务删除/批准/恢复仍待用户在获准测试资源上验证。
