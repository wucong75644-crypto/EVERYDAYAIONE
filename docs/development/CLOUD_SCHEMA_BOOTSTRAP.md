# 原生空白开发库 bootstrap

仅用于 Codex VM 内独立开发和测试，不是生产初始化或迁移路径。无需也禁止生产 dump、备份、连接或凭据。生产 `backend/scripts/migration_runner.py` 与已发布 SQL 的内容/checksum 完全保留。

## 运行与隔离

Python 3.12、Node.js 22.12+、npm、Git 须可用。完整开发环境显式执行：

```bash
CLOUD_PYTHON=python3.12 bash scripts/cloud/setup.sh --with-services
.venv/bin/python scripts/cloud/dev.py check
.venv/bin/python scripts/cloud/dev.py start
```

不带 `--with-services` 的 setup 仍只装依赖。原生包精确版本、下载地址和来自签名 Debian Packages 索引的 SHA256 在 `scripts/cloud/native-packages.json`，每次使用前校验。24 个原生依赖包在工作区解包，不需要 root、systemd、Docker 或外部付费资源，不改系统安装。维护 baseline 所需的唯一额外 Python 依赖是 `bootstrap/requirements.txt` 中精确版本的 pglast；日常 bootstrap/启动不需要该维护依赖。后端依赖版本仍由原 `backend/requirements.txt` 管理。

服务、密码、日志、数据位于私有 `/workspace/.everydayaione-dev/instances/<checkout真实绝对路径的SHA256前16位>/`。每个任务拥有不同 PostgreSQL cluster、Redis 进程与动态端口，只监听 `127.0.0.1`。Redis 开启密码、protected-mode、AOF，使用 DB 15；逻辑 DB 15 不替代实例隔离。`services.py` 验证 PostgreSQL 数据目录、Redis dir，拒绝指向其他实例。`.env` 的已有非本实例数据库配置不会被覆盖。密码在 VM 内生成，以 0600 文件保存，不打印、不写入 Git 或环境草稿。

进程不会随发布保留。恢复任务时先运行 `services.py prepare "$PWD"`，再 check/start。停止 API 使用本次 supervisor 的 Ctrl-C/SIGTERM；停止数据服务用 `services.py stop "$PWD"`。只停止本任务管理的实例，不按进程名批量杀服务。数据不会随 stop/start 删除。新任务先按 AGENTS 创建独立 `--development-only` 工作树；适配未合入 main 前追加已核验 `--base`。

## 新开发 baseline 的身份与审计

`bootstrap/schema.sql` 是从仓库结构来源在全新本机开发构建库生成、再独立恢复验证的当前原生结构。它不是生产 dump，也不包含用户数据。其 `seed.sql` 仅有仓库 SQL 自带的 12 项配置 bundle 定义、16 项配置定义、23 项权限目录；没有账号、组织、真实配置、Token 或业务记录。

`manifest.json` 记录源提交、229 份原始来源完整 checksum、逐段来源/执行文本 checksum、生成器 checksum、独立 baseline 身份、资源 checksum 与完整 catalog fingerprint；`catalog.json` 记录实际恢复后的结构与 ACL 契约。所有源码函数体和当前 RLS/session_user/membership 校验保留。数据库中只写独立 `cloud_development.bootstrap_state` receipt，绝不创建/填充旧 `schema_migration_ledger` 或伪造历史 applied/baseline 行。

初始化必须满足当前工作树私有 cluster 的文件系统身份、`127.0.0.1`、管理账号和 `cloud_dev_*` 数据库名。只有真正空库才可创建结构；整个 schema、注册表元数据与 receipt 在一个事务内提交。任意失败回滚为空库，可安全重试。重复执行仅核验完整 catalog 与 receipt，不 truncate、重建、重置数据或自动升级；未知非空库、资源漂移、列/约束/函数/RLS/ACL 漂移都会明确拒绝。

## 相对原始迁移链的明确适配

- 原始基础来自 `docs/database/supabase_init.sql` 的结构段、`docs/database/migrations/001–017`、当前 `backend/migrations/*.sql`。Supabase 的 auth policies 不适用于原生本机；按已有原生部署基线的架构选择原生结构，不创建假 `auth.uid()`，不声称 Supabase policy 已运行。
- 033、076 的真实 `CREATE INDEX CONCURRENTLY` 在来源构建时按独立 autocommit 执行。固化后的 baseline 是最终索引定义，空库原子恢复无需并发建索引。生产迁移执行器事务语义未改变。
- 039 缺失的真实 `erp_sync_dead_letter` CREATE TABLE 来自当前原生 `deploy/init-database.sql`，不是占位表。构建时把该迁移自己的 DROP CONSTRAINT 放到 DROP INDEX 前；无签名函数 COMMENT 指定刚创建的准确签名，函数体不变。
- 094 假定的 `shop_user_id`、`supplier_code` 两个真实列，在文档/当前目标 ALTER TYPE 明确声明的最终 TEXT 类型下补齐；两张 ERP 表均按同样来源处理。
- `messages` 的当前 JSONB/content、status VARCHAR(20) NOT NULL DEFAULT completed、task_id、error、updated_at 来自 `docs/document/TECH_统一多模态消息系统重构方案.md` §2.2 和当前消费者。旧 024 是需 Supabase 的数据转换程序；空库没有旧数据，不执行外部程序，也不伪造其迁移记录。
- 当前 156–160 的事务身份读取，与 `core/db_scope.py` 写入的 `app.actor_user_id` / `app.org_id` 一致；读取真实 UUID，缺失为空，不提供虚假 actor 或绕过授权。数据库中保留当前 SQL 引用的数据库角色作为 NOLOGIN principal（应用 everydayai 为非超级用户登录），不恢复旧平台服务/路径/表。现行会话角色、组织成员、RLS 检查保持完整。
- 266、271、272 的表/函数 ownership 按各文件头注释与现行 migration executor owner 合同对齐；281 在 279 创建对象的同一受限 owner 下构建。没有靠 SUPERUSER/BYPASSRLS 的应用账号跳过权限。
- 已被 112 删除的 pending_interaction 不重建；lifespan 的失效清理语句删除。沙盒、外部自动化、知识种子加载、独立 worker 保持关闭。模型/短信/OSS 等外部能力不在本地验证范围。

维护生成器使用 pglast 的原文 statement 切片，避免重印 AST 改写函数体或错误移动 NULLS NOT DISTINCT。PG17 dump/reparse 对 varchar 数组转 text 数组的反序列化写法不同，生成器只接受该等价 cast 归一化；其余结构和所有表/函数 ACL 必须完全相同。

## 维护与测试

重建候选资源只在本任务私有实例中新建构建库，不覆盖已有库，也不直接覆盖已固化 baseline：

```bash
.venv/bin/python -m pip install -r scripts/cloud/bootstrap/requirements.txt
.venv/bin/python scripts/cloud/bootstrap/build.py cloud_build_<unique>
.venv/bin/python scripts/cloud/bootstrap/export.py cloud_build_<unique> .cloud-dev/candidate-<unique>
```

export 仅允许三张上述注册表元数据表非空，随后创建另一个空库独立恢复，比较所有 catalog/ACL。SQL 中角色列表顺序与元数据 created_at 可随构建不同；独立恢复后的 catalog 契约必须一致。新 schema 版本需独立评审及新空库，不会自动对用户已有库做迁移。

```bash
CLOUD_NATIVE_INTEGRATION=1 .venv/bin/python -m unittest scripts/testing/test_cloud_bootstrap.py -v
.venv/bin/python -m unittest scripts/testing/test_cloud_development.py scripts/testing/test_migration_executor.py -v
.venv/bin/python scripts/cloud/smoke.py
```

集成测试覆盖两个独立空库、重复执行保留业务数据、未知非空库拒绝、SQL 失败全事务回滚及重试、checksum/结构/ACL 漂移拒绝、系统库拒绝、非超级用户与 receipt 越权拒绝、真实 pgvector 和事务 UUID scope。smoke 使用真实 loopback API、合成账号/组织/对话，验证认证、组织权限初始化、对话、非成员 403、事务回滚、Redis，并在至少 20 秒后台周期中拒绝任何 ERROR/缺表列函数/权限错误。健康 200 不代替这些检查。

## 修复交付与快照兼容

修复现已同步到本地独立任务分支，提交推送由明确授权的受控 Git 入口完成。后续环境优先直接检出包含修复的提交并运行 setup，不再依赖未提交补丁。此前发布的快照携带完整 patch、逐文件 SHA256 manifest 与 `reuse_patch.py` 的校验副本，仅用于旧基座过渡：Install/Start 配置必须固定 patch 和 manifest 的 SHA256，调用该工具：只在 `codex/task/*` 的干净、完全相同源基座应用，先 git apply --check，随后逐文件校验；不暂存、不提交、不推送。已逐文件匹配则幂等返回，其他基座、已有修改、未知路径或私有文件一律拒绝。继续已有任务时不覆盖开发者修改。

新任务应用经页面评审发布的 patch 后，再在新工作树运行 setup --with-services / prepare / check / start。因此依赖的是可核验文件与可重建步骤，而不是本次 VM 的存活进程或任何诊断库。主工作树、生产迁移和发布流程不被该工具更改。
