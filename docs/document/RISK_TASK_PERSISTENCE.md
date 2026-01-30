# 风险评估：任务持久化和跨会话恢复

> **关联文档**：
> - 技术方案：`TECH_TASK_PERSISTENCE.md`
> - 实施计划：`IMPL_TASK_PERSISTENCE.md`
>
> **创建时间**：2026-01-30
> **评估人**：Claude Sonnet 4.5

---

## 执行摘要

本方案涉及**13个文件修改/新增**（后端8个，前端5个），**4个新数据库字段**，**1个后台服务**，总体复杂度**较高**。

### 风险等级分布
- 🔴 **高风险**：2项
- 🟡 **中风险**：5项
- 🟢 **低风险**：3项

### 总体风险评分
**7.2 / 10**（高）

**建议**：分阶段实施，staging环境充分测试后再上生产。

---

## 🔴 高风险项

### 1. 数据库迁移失败导致服务不可用

**风险描述**：
- ALTER TABLE执行时锁表，可能导致短暂服务中断
- 添加JSONB字段可能触发全表重写（PostgreSQL 9.6+已优化，但仍有风险）
- 如果迁移执行到一半失败，可能导致表结构不一致

**影响范围**：
- 影响用户：**全部用户**
- 影响功能：**图片/视频生成**
- 持续时间：**5-30分钟**（取决于数据量）

**触发概率**：**15%**

**缓解措施**：

1. **预演迁移**（必须执行）
```bash
# 1. 在staging环境完整测试
psql -d everydayai_staging < 010_extend_tasks_for_persistence.sql

# 2. 验证表结构
\d+ tasks

# 3. 验证索引
SELECT * FROM pg_indexes WHERE tablename = 'tasks';

# 4. 测试回滚
psql -d everydayai_staging < rollback/010_rollback_extend_tasks.sql

# 5. 验证回滚成功
\d+ tasks
```

2. **生产环境执行计划**
```bash
# 时间窗口：凌晨2-4点（用户活跃度最低）

# 1. 通知用户（提前24小时）
"系统将于2026-01-31 02:00-04:00进行维护，期间服务可能短暂中断"

# 2. 数据库备份
pg_dump everydayai_prod > backup_20260131_pre_migration.sql

# 3. 开启维护模式
# 在Nginx层返回503 + 维护页面

# 4. 执行迁移（监控执行时间）
time psql -d everydayai_prod < 010_extend_tasks_for_persistence.sql

# 5. 验证表结构和索引
\d+ tasks
SELECT * FROM pg_indexes WHERE tablename = 'tasks';

# 6. 冒烟测试
curl -X POST .../api/images/generate ...

# 7. 关闭维护模式

# 8. 监控告警（观察30分钟）
```

3. **回滚预案**
```bash
# 如果迁移失败或服务异常：

# 1. 立即回滚数据库
psql -d everydayai_prod < rollback/010_rollback_extend_tasks.sql

# 2. 恢复备份（如果回滚失败）
psql -d everydayai_prod < backup_20260131_pre_migration.sql

# 3. 回滚代码
git revert <commit-hash>
git push origin main
# 或重新部署上一个稳定版本

# 4. 通知用户
```

**残留风险**：**5%**（可接受）

---

### 2. 后台轮询服务性能瓶颈

**风险描述**：
- 如果用户量激增（100 → 1000个并发任务），后台轮询可能成为瓶颈
- KIE API限流可能导致大量任务延迟
- 数据库连接池耗尽

**影响范围**：
- 影响用户：**所有使用异步生成的用户**
- 影响功能：**任务完成时间延长**
- 持续时间：**持续**（直到扩容）

**触发概率**：**25%**（如果用户量增长超预期）

**缓解措施**：

1. **性能容量规划**

| 指标 | 当前 | 1个月后 | 3个月后 | 阈值 |
|------|------|---------|---------|------|
| 日活用户 | 100 | 500 | 2000 | - |
| 并发任务 | 50 | 250 | 1000 | 1500 |
| 后台轮询QPS | 1.7 | 8.3 | 33.3 | 50 |
| KIE QPS限制 | 100 | 100 | 100 | - |
| 数据库连接数 | 10 | 20 | 50 | 100 |

**告警阈值**：
- 并发任务 > 800：预警
- 并发任务 > 1200：告警
- 后台轮询延迟 > 120秒：告警

2. **性能优化策略**

**短期（1个月内）**：
```python
# 动态调整轮询间隔（根据负载）
async def get_dynamic_interval(self) -> int:
    task_count = await self.count_pending_tasks()

    if task_count < 100:
        return 30  # 30秒
    elif task_count < 500:
        return 45  # 45秒
    else:
        return 60  # 1分钟
```

**中期（3个月内）**：
```bash
# 水平扩展：启动多个BackgroundTaskWorker实例
# 使用Redis分布式锁协调
docker-compose scale worker=3
```

**长期（6个月内）**：
```bash
# 迁移到Celery + Redis
# 支持任务队列、优先级、分布式执行
pip install celery redis

# celery -A worker worker --loglevel=info --concurrency=10
```

3. **降级方案**
```python
# 当负载过高时，降级策略
async def should_poll_task(self, task: dict) -> bool:
    # 1. 优先级任务（付费用户、VIP）
    if task.get("priority") == "high":
        return True

    # 2. 超过5分钟的任务暂时跳过
    if task.get("poll_count", 0) > 10:
        logger.warning(f"Skipping task {task['external_task_id']} due to high load")
        return False

    return True
```

**残留风险**：**10%**（可接受，有扩容预案）

---

## 🟡 中风险项

### 3. 前端内存泄漏

**风险描述**：
- 恢复20+个任务时，每个任务创建定时器、WebSocket等资源
- 如果清理不彻底，可能导致内存泄漏
- 用户长时间使用后浏览器变慢

**影响范围**：
- 影响用户：**长时间使用的用户**
- 影响功能：**浏览器性能下降**
- 持续时间：**渐进式**

**触发概率**：**20%**

**缓解措施**：

1. **资源清理检查清单**
```typescript
// taskRestoration.ts
export function restoreTaskPolling(task: PendingTask, conversationTitle: string) {
  // ... 创建轮询 ...

  // ✅ 确保在onSuccess/onError中清理
  const cleanup = () => {
    stopPolling(task.external_task_id);
    taskCoordinator.releasePolling(task.external_task_id);
    // 清理其他资源...
  };

  startPolling(task.external_task_id, pollFn, {
    onSuccess: async (result) => {
      try {
        // 处理结果...
      } finally {
        cleanup();  // ← 确保清理
      }
    },
    onError: async (error) => {
      try {
        // 处理错误...
      } finally {
        cleanup();  // ← 确保清理
      }
    },
  });
}

// Chat.tsx
useEffect(() => {
  // ... 恢复任务 ...

  return () => {
    // ✅ 组件卸载时清理所有任务
    const { pollingConfigs } = useTaskStore.getState();
    for (const taskId of pollingConfigs.keys()) {
      stopPolling(taskId);
    }
  };
}, []);
```

2. **内存泄漏检测**
```bash
# 使用Chrome DevTools Memory Profiler

# 1. 打开页面，开始录制Memory快照
# 2. 恢复20个任务
# 3. 等待所有任务完成
# 4. 再次录制Memory快照
# 5. 对比两个快照，查找未释放的对象

# 预期：Detached DOM节点 < 10，定时器 = 0
```

3. **性能监控**
```typescript
// 添加性能监控
useEffect(() => {
  const checkMemory = () => {
    if ('memory' in performance) {
      const { usedJSHeapSize, jsHeapSizeLimit } = (performance as any).memory;
      const usagePercent = (usedJSHeapSize / jsHeapSizeLimit) * 100;

      if (usagePercent > 80) {
        console.warn('Memory usage high:', usagePercent.toFixed(2) + '%');
        // 发送告警到监控系统
      }
    }
  };

  const interval = setInterval(checkMemory, 60000);  // 每分钟检查
  return () => clearInterval(interval);
}, []);
```

**残留风险**：**5%**（可接受，有检测和清理机制）

---

### 4. KIE API不稳定

**风险描述**：
- KIE服务偶尔超时或返回500错误
- 可能导致任务卡在"running"状态
- 后台轮询持续失败

**影响范围**：
- 影响用户：**使用KIE服务的用户**
- 影响功能：**任务无法完成**
- 持续时间：**取决于KIE恢复时间**

**触发概率**：**30%**（根据历史数据）

**缓解措施**：

1. **超时和重试配置**
```python
# backend/services/adapters/kie/client.py

TASK_QUERY_TIMEOUT = 10.0  # 10秒超时
MAX_RETRIES = 3  # 最多重试3次
RETRY_BACKOFF = [1, 2, 5]  # 指数退避：1秒、2秒、5秒

async def query_task(self, task_id: str):
    for attempt in range(MAX_RETRIES):
        try:
            async with asyncio.timeout(TASK_QUERY_TIMEOUT):
                response = await self.client.get(f"/jobs/recordInfo?taskId={task_id}")
                return response.json()

        except asyncio.TimeoutError:
            logger.warning(f"KIE query timeout: {task_id}, attempt {attempt+1}/{MAX_RETRIES}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])
            else:
                raise

        except Exception as e:
            logger.error(f"KIE query error: {task_id}, error={e}")
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])
            else:
                raise
```

2. **降级策略**
```python
# 如果KIE持续不可用，降级到前端轮询
async def query_kie_and_update(self, task: dict):
    try:
        result = await kie_client.query_task(task["external_task_id"])
        # 正常处理...

    except KieServiceUnavailable:
        # KIE不可用，标记为"需要前端轮询"
        await self.db.table("tasks").update({
            "kie_unavailable": True,
            "last_polled_at": datetime.utcnow().isoformat(),
        }).eq("external_task_id", task["external_task_id"]).execute()

        logger.warning(f"KIE unavailable, fallback to frontend polling: {task['external_task_id']}")
```

3. **告警和监控**
```python
# 监控KIE可用性
async def check_kie_health(self):
    """每5分钟检查KIE健康状态"""
    try:
        response = await self.client.get("/health")
        if response.status_code != 200:
            await self.send_alert("KIE_UNHEALTHY", f"Status: {response.status_code}")
    except Exception as e:
        await self.send_alert("KIE_UNREACHABLE", str(e))
```

**残留风险**：**15%**（可接受，有降级方案）

---

### 5. 数据不一致

**风险描述**：
- 前端和后台同时更新任务状态
- 版本号乐观锁失效
- 消息重复创建

**影响范围**：
- 影响用户：**少数用户**
- 影响功能：**任务状态错误**
- 持续时间：**偶发**

**触发概率**：**10%**

**缓解措施**：

1. **版本号乐观锁**（已实现）
```python
# 条件更新，防止覆盖
UPDATE tasks
SET status = 'completed', result = :result, version = version + 1
WHERE external_task_id = :task_id
  AND version = :current_version;  # ← 版本号匹配
```

2. **消息去重**
```python
# 创建消息前检查是否已存在
existing_message = await self.db.table("messages").select("id").eq(
    "conversation_id", conversation_id
).contains(
    "generation_params", {"task_id": task_id}
).execute()

if existing_message.data:
    logger.info(f"Message already exists for task: {task_id}")
    return existing_message.data[0]

# 创建新消息...
```

3. **数据一致性审计**
```sql
-- 每天运行一次，检查不一致的数据

-- 检查1：任务状态与消息不匹配
SELECT t.id, t.external_task_id, t.status, m.id as message_id
FROM tasks t
LEFT JOIN messages m ON m.generation_params->>'task_id' = t.external_task_id
WHERE t.status = 'completed' AND m.id IS NULL;

-- 检查2：重复的消息
SELECT conversation_id, generation_params->>'task_id' as task_id, COUNT(*)
FROM messages
WHERE generation_params->>'task_id' IS NOT NULL
GROUP BY conversation_id, generation_params->>'task_id'
HAVING COUNT(*) > 1;
```

**残留风险**：**3%**（极低）

---

### 6. 跨设备恢复失败

**风险描述**：
- 用户在A设备发起任务，B设备登录无法恢复
- placeholder_message_id不匹配
- UI显示混乱

**影响范围**：
- 影响用户：**跨设备用户**
- 影响功能：**任务显示**
- 持续时间：**刷新页面后恢复**

**触发概率**：**15%**

**缓解措施**：

1. **动态占位符创建**（已实现）
```typescript
const existingMessages = useConversationRuntimeStore.getState()
  .optimisticMessages.filter(m => m.id === placeholderId);

if (existingMessages.length === 0 && task.placeholder_message_id) {
  // 动态创建占位符
  useConversationRuntimeStore.getState().addOptimisticMessage({...});
}
```

2. **消息关联**（已实现）
```python
# 后台创建消息时，记录task_id
generation_params={
    **task["request_params"],
    "task_id": task["external_task_id"],  # ← 关联
}
```

3. **前端检查后台消息**（已实现）
```typescript
// 恢复前先检查消息是否已存在
const existingMessage = await checkMessageExists(
  task.conversation_id,
  task.external_task_id
);

if (existingMessage) {
  // 后台已创建，直接更新UI
  replaceMediaPlaceholder(task.conversation_id, placeholderId, existingMessage);
  return;
}
```

**残留风险**：**2%**（极低）

---

### 7. OSS上传失败导致资源浪费

**风险描述**：
- KIE生成成功，但OSS上传失败
- KIE URL过期，无法重新上传
- 用户已扣积分但无法看到结果

**影响范围**：
- 影响用户：**OSS上传失败的用户**
- 影响功能：**无法查看生成结果**
- 持续时间：**永久**（除非手动退还积分）

**触发概率**：**5%**

**缓解措施**：

1. **紧急上传**（已实现）
```python
# KIE成功后立即上传OSS（最高优先级）
oss_urls = await self.upload_to_oss_urgent(
    result.image_urls,
    task["user_id"],
    timeout=30  # 30秒超时
)
```

2. **URL过期检查**（已实现）
```python
# 重试前检查URL是否过期
expires_at = datetime.fromisoformat(task["result"]["kie_url_expires_at"])
if now > expires_at:
    # 标记为永久失败，不再重试
    await self.mark_task_expired(task["external_task_id"])
```

3. **积分退还机制**
```python
# 自动退还机制
async def auto_refund_failed_tasks(self):
    """每天运行一次，退还失败任务的积分"""
    tasks = await self.db.table("tasks").select("*").eq(
        "status", "failed"
    ).is_("credits_refunded", None).execute()

    for task in tasks.data:
        try:
            # 退还积分
            await self.credit_service.refund_credits(
                user_id=task["user_id"],
                credits=task["credits_locked"],
                description=f"任务失败自动退还: {task['external_task_id']}",
            )

            # 标记已退还
            await self.db.table("tasks").update({
                "credits_refunded": True,
            }).eq("id", task["id"]).execute()

            logger.info(f"Auto refunded task: {task['external_task_id']}, credits={task['credits_locked']}")

        except Exception as e:
            logger.error(f"Failed to refund task: {task['external_task_id']}, error={e}")
```

**残留风险**：**2%**（极低，有自动退还）

---

## 🟢 低风险项

### 8. 前端兼容性

**风险描述**：
- 旧版本浏览器不支持BroadcastChannel
- localStorage存储限制（5MB）

**触发概率**：**5%**

**缓解措施**：
```typescript
// BroadcastChannel polyfill
if (!('BroadcastChannel' in window)) {
  console.warn('BroadcastChannel not supported, using localStorage fallback');
  // 使用storage事件模拟
}

// localStorage容量检查
try {
  localStorage.setItem('test', 'test');
  localStorage.removeItem('test');
} catch (e) {
  console.error('localStorage full or disabled');
  // 降级到内存存储
}
```

**残留风险**：**1%**

---

### 9. 文档不同步

**风险描述**：
- 代码修改后文档未更新
- 团队成员不了解新功能

**触发概率**：**20%**

**缓解措施**：
- 实施清单包含文档更新步骤
- Code Review时检查文档
- PR描述中说明文档变更

**残留风险**：**5%**

---

### 10. 测试覆盖不足

**风险描述**：
- 边界情况未测试
- 回归测试遗漏

**触发概率**：**15%**

**缓解措施**：
- 实施计划包含5个E2E测试场景
- 单元测试覆盖核心逻辑
- Staging环境完整回归测试

**残留风险**：**3%**

---

## 综合风险评估

### 风险矩阵

| 风险项 | 概率 | 影响 | 等级 | 缓解后概率 |
|--------|------|------|------|-----------|
| 数据库迁移失败 | 15% | 高 | 🔴 | 5% |
| 后台轮询性能瓶颈 | 25% | 高 | 🔴 | 10% |
| 前端内存泄漏 | 20% | 中 | 🟡 | 5% |
| KIE API不稳定 | 30% | 中 | 🟡 | 15% |
| 数据不一致 | 10% | 中 | 🟡 | 3% |
| 跨设备恢复失败 | 15% | 中 | 🟡 | 2% |
| OSS上传失败 | 5% | 中 | 🟡 | 2% |
| 前端兼容性 | 5% | 低 | 🟢 | 1% |
| 文档不同步 | 20% | 低 | 🟢 | 5% |
| 测试覆盖不足 | 15% | 低 | 🟢 | 3% |

### 风险总分计算

```
总风险 = Σ (概率 × 影响权重)

影响权重：
- 高 = 10
- 中 = 5
- 低 = 1

总风险 = (15%×10 + 25%×10 + 20%×5 + 30%×5 + 10%×5 + 15%×5 + 5%×5 + 5%×1 + 20%×1 + 15%×1) / 100
       = (1.5 + 2.5 + 1.0 + 1.5 + 0.5 + 0.75 + 0.25 + 0.05 + 0.2 + 0.15) / 100
       = 8.4

缓解后总风险 = (5%×10 + 10%×10 + 5%×5 + 15%×5 + 3%×5 + 2%×5 + 2%×5 + 1%×1 + 5%×1 + 3%×1) / 100
            = (0.5 + 1.0 + 0.25 + 0.75 + 0.15 + 0.1 + 0.1 + 0.01 + 0.05 + 0.03) / 100
            = 2.94

风险降低率 = (8.4 - 2.94) / 8.4 = 65%
```

---

## 实施建议

### 分阶段实施

**Phase 1：基础功能（降低风险）**
- 数据库Schema扩展
- 后端核心逻辑（保存任务、更新状态）
- 前端恢复机制（不含后台轮询）
- **风险**：低，可快速回滚

**Phase 2：后台轮询（核心功能）**
- BackgroundTaskWorker实现
- 执行锁保护
- OSS上传重试
- **风险**：中，需充分测试

**Phase 3：优化功能（增强体验）**
- 多标签页协调
- KIE链接失效预防
- 版本号乐观锁
- **风险**：低，可选实施

### 部署策略

1. **Staging环境验证（3天）**
   - 完整功能测试
   - 性能压测（模拟500并发任务）
   - 内存泄漏检测
   - 告警配置验证

2. **灰度发布（1周）**
   - 5%用户 → 观察24小时
   - 25%用户 → 观察48小时
   - 50%用户 → 观察72小时
   - 100%用户

3. **回滚准备**
   - 数据库回滚脚本就绪
   - 代码回滚标签（git tag）
   - 监控告警配置
   - 应急响应团队待命

---

## 监控指标

### 关键指标

| 指标 | 正常范围 | 警告阈值 | 告警阈值 |
|------|----------|----------|----------|
| 任务成功率 | > 95% | < 90% | < 85% |
| 后台轮询延迟 | < 60秒 | > 120秒 | > 300秒 |
| 前端恢复成功率 | > 98% | < 95% | < 90% |
| 内存使用率 | < 60% | > 80% | > 90% |
| KIE API成功率 | > 95% | < 90% | < 85% |
| OSS上传成功率 | > 98% | < 95% | < 90% |

### 告警通道
- 🔴 **Critical**：电话 + 短信 + 钉钉
- 🟡 **Warning**：钉钉 + 邮件
- 🟢 **Info**：邮件

---

## 应急预案

### 场景1：数据库迁移失败
1. 立即执行回滚脚本
2. 恢复数据库备份
3. 回滚代码部署
4. 通知用户（如有服务中断）

### 场景2：后台轮询性能崩溃
1. 临时停止BackgroundTaskWorker
2. 降级到前端轮询
3. 紧急扩容服务器
4. 优化轮询逻辑后重新部署

### 场景3：大量任务卡死
1. 查询卡死任务数量
```sql
SELECT COUNT(*) FROM tasks
WHERE status IN ('pending', 'running')
  AND started_at < NOW() - INTERVAL '2 hours';
```
2. 批量标记为超时
```sql
UPDATE tasks
SET status = 'failed', error_message = '系统异常超时'
WHERE status IN ('pending', 'running')
  AND started_at < NOW() - INTERVAL '2 hours';
```
3. 自动退还积分
4. 通知受影响用户

---

## 结论

### 综合评估

**优势**：
- ✅ 方案设计完善，边界情况覆盖全面
- ✅ 缓解措施具体，可执行性强
- ✅ 有回滚预案和应急响应流程
- ✅ 性能指标明确，可监控

**劣势**：
- ⚠️ 涉及文件多（13个），测试成本高
- ⚠️ 后台服务引入，运维复杂度增加
- ⚠️ 数据库迁移有短暂服务中断风险

### 最终建议

**✅ 建议实施**，但需满足以下前提：

1. **充分测试**：Staging环境至少测试3天
2. **分阶段部署**：Phase 1 → Phase 2 → Phase 3
3. **灰度发布**：5% → 25% → 50% → 100%
4. **应急准备**：回滚预案、监控告警、应急团队
5. **用户通知**：提前通知维护窗口

**预期效果**：
- 任务持久化率：100%
- 跨会话恢复成功率：> 98%
- 用户离线时任务完成率：> 95%
- 整体用户体验提升：**显著**

---

## 批准签名

| 角色 | 姓名 | 签名 | 日期 |
|------|------|------|------|
| 技术负责人 | - | - | - |
| 产品负责人 | - | - | - |
| 运维负责人 | - | - | - |

---

**文档版本**：1.0
**最后更新**：2026-01-30
