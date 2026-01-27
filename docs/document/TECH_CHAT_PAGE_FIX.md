# 聊天页面问题修复开发执行清单

> **文档类型**：技术方案 + 执行清单  
> **创建日期**：2026-01-26  
> **状态**：待执行  
> **优先级**：高

---

## 一、问题概览

### 问题统计

| 优先级 | 数量 | 说明 |
|-------|-----|------|
| P0 严重 | 5 | 数据一致性/安全问题，必须立即修复 |
| P1 重要 | 4 | 功能缺失，影响用户体验 |
| P2 中等 | 6 | 功能不完整，可正常使用 |
| P3 轻微 | 3 | 体验优化，后续迭代 |

### 问题清单

| # | 问题 | 优先级 | 阶段 | 备注 |
|---|------|-------|-----|------|
| 1 | 后端无任务数量限制（全局15/单对话5） | P0 | 一 | |
| 2 | 无分布式锁保护（并发竞态） | P0 | 一 | |
| 3 | 积分扣除非原子性 + 失败无积分退回 | P0 | 一 | 合并处理 |
| 4 | 无 API 限流 | P0 | 一 | |
| 5 | video_generation_cost 枚举缺失 | P0 | 一 | 当前代码已在使用，必须优先修复 |
| 6 | 重新生成调用错误API端点 | P1 | 二 | 应调用 /regenerate 而非 /stream |
| 7 | 重新生成闭包竞态条件 | P1 | 二 | 使用函数式 setState 修复 |
| 8 | 无停止生成功能 | P1 | 三 | |
| 9 | 个人设置页面不存在 | P1 | 三 | |
| 10 | 前端无发送限制检查 | P2 | 三 | |
| 11 | 重新生成无任务限制检查 | P2 | 二 | |
| 12 | URL 无格式验证 | P2 | 二 | |
| 13 | 缺少 tasks 表 | P2 | 一 | 与积分服务一起创建 |
| 14 | 缺少 credit_transactions 表 | P2 | 一 | 与积分服务一起创建 |
| 15 | 通知队列无上限 | P2 | 三 | |
| 16 | 技能选择提示误导 | P2 | 三 | |
| 17 | 核心服务无测试 | P3 | 四 | |
| 18 | 对话切换时重新生成状态未处理 | P3 | 二 | |

> **注**：问题 #19（TODO 注释未清理）经验证已不存在，已从清单移除。

---

## 二、开发阶段规划

```
阶段一：基础设施 + 数据库（后端核心）
    ↓
阶段二：消息重新生成修复（前端核心）
    ↓
阶段三：前端功能完善
    ↓
阶段四：测试与收尾
```

---

## 三、阶段一：基础设施 + 数据库修复

### 目标
- 修复数据库枚举缺失（阻塞视频生成）
- 创建 Redis 连接管理
- 实现分布式锁
- 实现任务限制服务
- 实现积分服务（原子操作 + 锁定机制 + 失败退回）
- 添加 API 限流中间件
- 创建必要的数据库表

### 依赖关系
```
1.1 数据库迁移（枚举+表） ─── 无依赖，优先执行
         │
         ▼
1.2 Redis连接 ──┬──▶ 1.3 任务限制服务
                └──▶ 1.4 积分服务（含锁定/退回）
                          │
                          ▼
                     1.5 API限流
                          │
                          ▼
                     1.6 业务服务集成
```

---

### 步骤 1.1：数据库迁移（优先执行）

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 新建 | `docs/database/migrations/005_add_video_cost_enum.sql` |
| 新建 | `docs/database/migrations/006_add_tasks_table.sql` |
| 新建 | `docs/database/migrations/007_add_credit_transactions.sql` |

**SQL 内容**：

```sql
-- 005_add_video_cost_enum.sql
-- 添加视频生成积分类型枚举值（P0 阻塞问题）

ALTER TYPE credits_change_type ADD VALUE IF NOT EXISTS 'video_generation_cost';

-- 验证
SELECT enumlabel FROM pg_enum 
WHERE enumtypid = 'credits_change_type'::regtype;
```

```sql
-- 006_add_tasks_table.sql
-- 创建任务追踪表

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('chat', 'image', 'video')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' 
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    credits_locked INTEGER DEFAULT 0,
    credits_used INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX idx_tasks_conversation ON tasks(conversation_id);
CREATE INDEX idx_tasks_created ON tasks(created_at DESC);

ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own tasks" ON tasks FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Service role can manage all tasks" ON tasks FOR ALL
    USING (auth.role() = 'service_role');
```

```sql
-- 007_add_credit_transactions.sql
-- 创建积分事务表（用于锁定-确认-退回流程）

CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID UNIQUE NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL CHECK (amount > 0),
    type VARCHAR(20) NOT NULL CHECK (type IN ('lock', 'deduct', 'refund')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'refunded', 'expired')),
    reason VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '10 minutes')
);

CREATE INDEX idx_credit_tx_user ON credit_transactions(user_id);
CREATE INDEX idx_credit_tx_task ON credit_transactions(task_id);
CREATE INDEX idx_credit_tx_status ON credit_transactions(status, expires_at);

ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own transactions" ON credit_transactions FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Service role can manage all transactions" ON credit_transactions FOR ALL
    USING (auth.role() = 'service_role');
```

**执行方式**：
```bash
# 在 Supabase Dashboard -> SQL Editor 执行
# 或使用 supabase cli
supabase db push
```

**测试验证**：
```sql
-- 测试枚举
INSERT INTO credits_history (user_id, change_type, change_amount, balance_after, description)
VALUES ('test-user-id', 'video_generation_cost', -10, 90, '测试视频生成');
-- 应该成功

-- 测试 tasks 表
SELECT * FROM tasks LIMIT 1;

-- 测试 credit_transactions 表
SELECT * FROM credit_transactions LIMIT 1;
```

**完成标准**：
- [ ] 005 迁移执行成功
- [ ] 006 迁移执行成功
- [ ] 007 迁移执行成功
- [ ] video_generation_cost 枚举可用
- [ ] tasks 表可正常读写
- [ ] credit_transactions 表可正常读写

---

### 步骤 1.2：创建 Redis 连接管理

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 新建 | `backend/core/redis.py` |
| 修改 | `backend/requirements.txt`（添加依赖） |

**函数路径**：
```python
# backend/core/redis.py
from typing import Optional
from redis.asyncio import Redis
from core.config import settings
from loguru import logger

class RedisClient:
    """Redis 连接管理（单例模式）"""
    _instance: Optional[Redis] = None
    
    @classmethod
    async def get_client(cls) -> Redis:
        """获取 Redis 客户端"""
        if cls._instance is None:
            cls._instance = Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
            )
            logger.info("Redis 连接已建立")
        return cls._instance
    
    @classmethod
    async def close(cls) -> None:
        """关闭 Redis 连接"""
        if cls._instance:
            await cls._instance.close()
            cls._instance = None
            logger.info("Redis 连接已关闭")
    
    @classmethod
    async def acquire_lock(
        cls, 
        key: str, 
        timeout: int = 10
    ) -> Optional[str]:
        """
        获取分布式锁
        
        Args:
            key: 锁的键名
            timeout: 锁超时时间（秒）
            
        Returns:
            成功返回锁 token，失败返回 None
        """
        import uuid
        client = await cls.get_client()
        token = str(uuid.uuid4())
        acquired = await client.set(
            f"lock:{key}", 
            token, 
            nx=True, 
            ex=timeout
        )
        return token if acquired else None
    
    @classmethod
    async def release_lock(cls, key: str, token: str) -> bool:
        """
        释放分布式锁（使用 Lua 脚本保证原子性）
        
        Args:
            key: 锁的键名
            token: 获取锁时返回的 token
            
        Returns:
            是否成功释放
        """
        client = await cls.get_client()
        lua_script = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """
        result = await client.eval(lua_script, 1, f"lock:{key}", token)
        return result == 1
```

**依赖添加**：
```
redis==5.0.1
```

**测试验证**：
```bash
cd backend && source venv/bin/activate
python3 -c "
import asyncio
from core.redis import RedisClient

async def test():
    # 测试连接
    client = await RedisClient.get_client()
    print('连接成功')
    
    # 测试锁
    token = await RedisClient.acquire_lock('test_key', 10)
    print(f'获取锁: {token}')
    
    # 测试重复获取（应失败）
    token2 = await RedisClient.acquire_lock('test_key', 10)
    print(f'重复获取锁: {token2}')  # 应为 None
    
    # 释放锁
    released = await RedisClient.release_lock('test_key', token)
    print(f'释放锁: {released}')
    
    await RedisClient.close()

asyncio.run(test())
"
```

**完成标准**：
- [ ] Redis 连接成功
- [ ] acquire_lock 获取锁成功
- [ ] 重复获取同一锁失败（返回 None）
- [ ] release_lock 释放锁成功

---

### 步骤 1.3：实现任务限制服务

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 新建 | `backend/services/task_limit_service.py` |
| 只读 | `backend/core/config.py`（读取限制配置） |
| 只读 | `backend/core/exceptions.py`（使用 TaskQueueFullError） |

**函数路径**：
```python
# backend/services/task_limit_service.py
from typing import Optional
from redis.asyncio import Redis
from core.config import settings
from core.exceptions import TaskQueueFullError
from loguru import logger


class TaskLimitService:
    """任务限制服务"""
    
    def __init__(self, redis_client: Redis):
        self.redis = redis_client
        self.global_limit = settings.rate_limit_global_tasks  # 15
        self.conversation_limit = settings.rate_limit_conversation_tasks  # 5
    
    def _global_key(self, user_id: str) -> str:
        return f"task:global:{user_id}"
    
    def _conversation_key(self, user_id: str, conversation_id: str) -> str:
        return f"task:conv:{user_id}:{conversation_id}"
    
    async def check_and_acquire(
        self, 
        user_id: str, 
        conversation_id: str
    ) -> bool:
        """
        检查限制并获取槽位
        
        Raises:
            TaskQueueFullError: 超过限制时抛出
            
        Returns:
            True 表示获取成功
        """
        global_key = self._global_key(user_id)
        conv_key = self._conversation_key(user_id, conversation_id)
        
        # 检查全局限制
        global_count = await self.redis.get(global_key)
        if global_count and int(global_count) >= self.global_limit:
            logger.warning(
                "任务队列已满（全局）",
                user_id=user_id,
                current=global_count,
                limit=self.global_limit
            )
            raise TaskQueueFullError(
                f"任务队列已满，最多同时执行 {self.global_limit} 个任务"
            )
        
        # 检查单对话限制
        conv_count = await self.redis.get(conv_key)
        if conv_count and int(conv_count) >= self.conversation_limit:
            logger.warning(
                "任务队列已满（单对话）",
                user_id=user_id,
                conversation_id=conversation_id,
                current=conv_count,
                limit=self.conversation_limit
            )
            raise TaskQueueFullError(
                f"当前对话任务队列已满，最多同时执行 {self.conversation_limit} 个任务"
            )
        
        # 原子递增（使用 pipeline 保证原子性）
        async with self.redis.pipeline() as pipe:
            await pipe.incr(global_key)
            await pipe.expire(global_key, 3600)  # 1小时过期
            await pipe.incr(conv_key)
            await pipe.expire(conv_key, 3600)
            await pipe.execute()
        
        logger.debug(
            "获取任务槽位成功",
            user_id=user_id,
            conversation_id=conversation_id
        )
        return True
    
    async def release(
        self, 
        user_id: str, 
        conversation_id: str
    ) -> None:
        """释放槽位"""
        global_key = self._global_key(user_id)
        conv_key = self._conversation_key(user_id, conversation_id)
        
        async with self.redis.pipeline() as pipe:
            await pipe.decr(global_key)
            await pipe.decr(conv_key)
            await pipe.execute()
        
        logger.debug(
            "释放任务槽位",
            user_id=user_id,
            conversation_id=conversation_id
        )
    
    async def get_active_count(
        self, 
        user_id: str, 
        conversation_id: Optional[str] = None
    ) -> dict:
        """获取活跃任务数量"""
        global_count = await self.redis.get(self._global_key(user_id)) or 0
        
        conv_count = 0
        if conversation_id:
            conv_count = await self.redis.get(
                self._conversation_key(user_id, conversation_id)
            ) or 0
        
        return {
            "global": int(global_count),
            "conversation": int(conv_count)
        }
```

**测试验证**：
```bash
python3 -m pytest tests/test_task_limit_service.py -v
```

**测试场景**：
```python
# tests/test_task_limit_service.py
import pytest
from services.task_limit_service import TaskLimitService
from core.exceptions import TaskQueueFullError

@pytest.mark.asyncio
async def test_acquire_within_limit(redis_client):
    service = TaskLimitService(redis_client)
    result = await service.check_and_acquire("user1", "conv1")
    assert result == True

@pytest.mark.asyncio
async def test_acquire_exceed_global_limit(redis_client):
    service = TaskLimitService(redis_client)
    # 获取15个槽位
    for i in range(15):
        await service.check_and_acquire("user1", f"conv{i}")
    
    # 第16个应该失败
    with pytest.raises(TaskQueueFullError):
        await service.check_and_acquire("user1", "conv16")

@pytest.mark.asyncio
async def test_acquire_exceed_conversation_limit(redis_client):
    service = TaskLimitService(redis_client)
    # 同一对话获取5个槽位
    for i in range(5):
        await service.check_and_acquire("user1", "conv1")
    
    # 第6个应该失败
    with pytest.raises(TaskQueueFullError):
        await service.check_and_acquire("user1", "conv1")

@pytest.mark.asyncio
async def test_release(redis_client):
    service = TaskLimitService(redis_client)
    await service.check_and_acquire("user1", "conv1")
    await service.release("user1", "conv1")
    
    counts = await service.get_active_count("user1", "conv1")
    assert counts["global"] == 0
    assert counts["conversation"] == 0
```

**完成标准**：
- [ ] check_and_acquire 正常获取槽位
- [ ] 超过全局限制时抛出 TaskQueueFullError
- [ ] 超过单对话限制时抛出 TaskQueueFullError
- [ ] release 正确释放槽位
- [ ] 所有测试通过

---

### 步骤 1.4：实现积分服务（含锁定/退回）

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 新建 | `backend/services/credit_service.py` |
| 只读 | `backend/core/exceptions.py`（使用 InsufficientCreditsError） |

**函数路径**：
```python
# backend/services/credit_service.py
from typing import Optional
from contextlib import asynccontextmanager
from uuid import uuid4
from datetime import datetime, timezone
from supabase import AsyncClient as SupabaseClient
from redis.asyncio import Redis
from core.exceptions import InsufficientCreditsError
from loguru import logger


class CreditService:
    """
    积分服务
    
    支持两种模式：
    1. 原子扣除（deduct_atomic）：简单场景，直接扣除
    2. 锁定模式（credit_lock）：复杂场景，先锁定再确认/退回
    """
    
    def __init__(self, db: SupabaseClient, redis: Optional[Redis] = None):
        self.db = db
        self.redis = redis
    
    async def deduct_atomic(
        self, 
        user_id: str, 
        amount: int, 
        reason: str,
        change_type: str
    ) -> int:
        """
        原子扣除积分
        
        使用 SQL 条件更新保证原子性：
        UPDATE users SET credits = credits - amount 
        WHERE id = user_id AND credits >= amount
        
        Args:
            user_id: 用户ID
            amount: 扣除数量
            reason: 扣除原因
            change_type: 变更类型（枚举值）
            
        Returns:
            新余额
            
        Raises:
            InsufficientCreditsError: 余额不足
        """
        # 使用 RPC 调用原子扣除
        result = await self.db.rpc(
            'deduct_credits_atomic',
            {
                'p_user_id': user_id,
                'p_amount': amount,
                'p_reason': reason,
                'p_change_type': change_type
            }
        ).execute()
        
        if not result.data or result.data.get('success') == False:
            logger.warning(
                "积分扣除失败：余额不足",
                user_id=user_id,
                amount=amount,
                reason=reason
            )
            raise InsufficientCreditsError("积分不足")
        
        new_balance = result.data.get('new_balance', 0)
        logger.info(
            "积分扣除成功",
            user_id=user_id,
            amount=amount,
            new_balance=new_balance,
            reason=reason
        )
        return new_balance
    
    async def lock_credits(
        self, 
        task_id: str, 
        user_id: str, 
        amount: int,
        reason: str = ""
    ) -> str:
        """
        预扣积分（锁定）
        
        Args:
            task_id: 任务ID（幂等键）
            user_id: 用户ID
            amount: 锁定数量
            reason: 锁定原因
            
        Returns:
            transaction_id
            
        Raises:
            InsufficientCreditsError: 余额不足
        """
        transaction_id = str(uuid4())
        
        # 1. 检查余额
        user_result = await self.db.table("users").select("credits").eq("id", user_id).single().execute()
        if not user_result.data:
            raise InsufficientCreditsError("用户不存在")
        
        current_credits = user_result.data.get("credits", 0)
        if current_credits < amount:
            logger.warning(
                "积分锁定失败：余额不足",
                user_id=user_id,
                amount=amount,
                current=current_credits
            )
            raise InsufficientCreditsError(f"积分不足，当前余额 {current_credits}，需要 {amount}")
        
        # 2. 原子扣除并记录事务
        # 使用事务保证一致性
        new_balance = current_credits - amount
        
        await self.db.table("users").update({
            "credits": new_balance,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", user_id).eq("credits", current_credits).execute()
        
        # 3. 记录事务
        await self.db.table("credit_transactions").insert({
            "id": transaction_id,
            "task_id": task_id,
            "user_id": user_id,
            "amount": amount,
            "type": "lock",
            "status": "pending",
            "reason": reason
        }).execute()
        
        logger.info(
            "积分锁定成功",
            transaction_id=transaction_id,
            task_id=task_id,
            user_id=user_id,
            amount=amount
        )
        
        return transaction_id
    
    async def confirm_deduct(self, transaction_id: str) -> None:
        """确认扣除（任务成功时调用）"""
        await self.db.table("credit_transactions").update({
            "status": "confirmed",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()
        
        logger.info("积分扣除确认", transaction_id=transaction_id)
    
    async def refund_credits(self, transaction_id: str) -> None:
        """退回积分（任务失败时调用）"""
        # 1. 获取事务信息
        tx_result = await self.db.table("credit_transactions").select("*").eq("id", transaction_id).single().execute()
        if not tx_result.data:
            logger.warning("退回失败：事务不存在", transaction_id=transaction_id)
            return
        
        tx = tx_result.data
        if tx["status"] != "pending":
            logger.warning("退回失败：事务状态不是 pending", transaction_id=transaction_id, status=tx["status"])
            return
        
        # 2. 退回积分
        await self.db.rpc(
            'refund_credits',
            {
                'p_user_id': tx["user_id"],
                'p_amount': tx["amount"]
            }
        ).execute()
        
        # 3. 更新事务状态
        await self.db.table("credit_transactions").update({
            "status": "refunded",
            "confirmed_at": datetime.now(timezone.utc).isoformat()
        }).eq("id", transaction_id).execute()
        
        logger.info(
            "积分退回成功",
            transaction_id=transaction_id,
            user_id=tx["user_id"],
            amount=tx["amount"]
        )
    
    @asynccontextmanager
    async def credit_lock(
        self, 
        task_id: str, 
        user_id: str, 
        amount: int,
        reason: str = ""
    ):
        """
        积分锁定上下文管理器
        
        正常退出：自动确认扣除
        异常退出：自动退回积分
        
        Usage:
            async with credit_service.credit_lock(task_id, user_id, 10) as tx_id:
                result = await do_something()
                # 成功则自动确认
            # 异常则自动退回
        """
        transaction_id = await self.lock_credits(task_id, user_id, amount, reason)
        try:
            yield transaction_id
            # 正常退出，确认扣除
            await self.confirm_deduct(transaction_id)
        except Exception as e:
            # 异常退出，退回积分
            logger.error(
                "任务失败，退回积分",
                transaction_id=transaction_id,
                error=str(e)
            )
            await self.refund_credits(transaction_id)
            raise
```

**数据库 RPC 函数**（需要在 Supabase 创建）：
```sql
-- 原子扣除函数
CREATE OR REPLACE FUNCTION deduct_credits_atomic(
    p_user_id UUID,
    p_amount INTEGER,
    p_reason TEXT,
    p_change_type TEXT
) RETURNS JSONB AS $$
DECLARE
    v_new_balance INTEGER;
BEGIN
    -- 原子扣除
    UPDATE users 
    SET credits = credits - p_amount,
        updated_at = NOW()
    WHERE id = p_user_id 
      AND credits >= p_amount
    RETURNING credits INTO v_new_balance;
    
    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'message', 'Insufficient credits');
    END IF;
    
    -- 记录历史
    INSERT INTO credits_history (user_id, change_type, change_amount, balance_after, description)
    VALUES (p_user_id, p_change_type::credits_change_type, -p_amount, v_new_balance, p_reason);
    
    RETURN jsonb_build_object('success', true, 'new_balance', v_new_balance);
END;
$$ LANGUAGE plpgsql;

-- 退回函数
CREATE OR REPLACE FUNCTION refund_credits(
    p_user_id UUID,
    p_amount INTEGER
) RETURNS VOID AS $$
BEGIN
    UPDATE users 
    SET credits = credits + p_amount,
        updated_at = NOW()
    WHERE id = p_user_id;
END;
$$ LANGUAGE plpgsql;
```

**测试验证**：
```bash
python3 -m pytest tests/test_credit_service.py -v
```

**测试场景**：
```python
@pytest.mark.asyncio
async def test_deduct_atomic_success():
    # 余额充足，扣除成功
    
@pytest.mark.asyncio
async def test_deduct_atomic_insufficient():
    # 余额不足，抛出 InsufficientCreditsError
    
@pytest.mark.asyncio
async def test_credit_lock_success():
    # 正常完成，积分扣除
    async with service.credit_lock(task_id, user_id, 10):
        pass  # 成功
    # 验证积分已扣除，事务状态为 confirmed
    
@pytest.mark.asyncio
async def test_credit_lock_exception():
    # 异常退出，积分退回
    try:
        async with service.credit_lock(task_id, user_id, 10):
            raise ValueError("模拟失败")
    except ValueError:
        pass
    # 验证积分已退回，事务状态为 refunded
```

**完成标准**：
- [ ] deduct_atomic 原子扣除成功
- [ ] 余额不足时正确抛出异常
- [ ] credit_lock 正常完成时确认扣除
- [ ] credit_lock 异常时自动退回
- [ ] 所有测试通过

---

### 步骤 1.5：添加 API 限流中间件

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `backend/main.py` |
| 修改 | `backend/requirements.txt` |

**修改范围**：
```python
# backend/main.py
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
```

**依赖添加**：
```
slowapi==0.1.9
```

**限流配置**：
| 端点 | 限制 |
|-----|------|
| POST /messages/stream | 30/minute |
| POST /messages/{id}/regenerate | 20/minute |
| POST /images/generate | 10/minute |
| POST /videos/generate | 5/minute |

**完成标准**：
- [ ] slowapi 中间件正确加载
- [ ] 超过限制返回 429 状态码
- [ ] 正常请求不受影响

---

### 步骤 1.6：业务服务集成

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `backend/services/image_service.py` |
| 修改 | `backend/services/video_service.py` |
| 修改 | `backend/api/routes/message.py` |
| 修改 | `backend/services/message_stream_service.py` |
| 修改 | `backend/schemas/message.py` |

**修改 1：图片服务集成积分服务**
```python
# backend/services/image_service.py
# 原代码：
await self._deduct_credits(user_id, cost)
result = await adapter.generate(prompt)

# 改为：
task_id = str(uuid.uuid4())
async with self.credit_service.credit_lock(task_id, user_id, cost, "图片生成"):
    result = await adapter.generate(prompt)
    # 成功则自动确认扣除
# 异常则自动退回
```

**修改 2：视频服务集成积分服务**
```python
# backend/services/video_service.py
# 同上，确保 change_type 使用 'video_generation_cost'
```

**修改 3：消息 API 添加任务限制**
```python
# backend/api/routes/message.py
@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: str,
    request: SendMessageRequest,
    user: CurrentUser,
    task_limit_service: TaskLimitService = Depends(get_task_limit_service)
):
    await task_limit_service.check_and_acquire(user.id, conversation_id)
    try:
        # 原有逻辑
        ...
    finally:
        await task_limit_service.release(user.id, conversation_id)
```

**修改 4：URL 格式验证**
```python
# backend/schemas/message.py
from pydantic import HttpUrl

class SendMessageRequest(BaseModel):
    content: str
    image_url: Optional[HttpUrl] = None
    video_url: Optional[HttpUrl] = None
    audio_url: Optional[HttpUrl] = None
```

**完成标准**：
- [ ] 图片服务使用 CreditService
- [ ] 视频服务使用 CreditService
- [ ] 生成失败时积分自动退回
- [ ] 发送/重新生成前检查任务限制
- [ ] URL 验证生效

---

### 阶段一完成检查清单

- [ ] 数据库迁移全部执行成功
- [ ] Redis 连接单例可用
- [ ] 分布式锁获取/释放正常
- [ ] 任务限制服务测试通过
- [ ] 积分服务测试通过（含锁定/退回）
- [ ] API 限流中间件生效
- [ ] 图片/视频服务集成完成
- [ ] 消息 API 任务限制集成完成
- [ ] requirements.txt 更新

---

## 四、阶段二：消息重新生成修复

### 目标
- 修复重新生成调用错误 API 端点问题
- 修复闭包竞态条件问题
- 添加对话切换时的状态保护

### 问题分析

**核心问题**：重新生成消息时调用了错误的 API 端点

| 当前行为 | 正确行为 |
|---------|---------|
| 调用 `/messages/stream`（创建新消息） | 应调用 `/messages/{id}/regenerate`（更新原消息） |

**调用链对比**：
```
当前（错误）：
用户点击重试 → sendMessageStream() → POST /stream → 创建新消息对

应该（正确）：
用户点击重试 → regenerateMessageStream() → POST /regenerate → 更新原消息
```

---

### 步骤 2.1：添加 regenerateMessageStream API 函数

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `frontend/src/services/message.ts` |

**函数路径**：
```typescript
// frontend/src/services/message.ts

/**
 * 重新生成失败的消息（流式）
 * @param conversationId 对话ID
 * @param messageId 消息ID
 * @param callbacks 流式回调
 */
export async function regenerateMessageStream(
  conversationId: string,
  messageId: string,
  callbacks: SendMessageStreamCallbacks
): Promise<void> {
  const token = localStorage.getItem('access_token');
  const url = `${API_BASE_URL}/api/conversations/${conversationId}/messages/${messageId}/regenerate`;

  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.message || '重新生成失败');
    }

    if (!response.body) {
      throw new Error('响应体为空');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.trim() || !line.startsWith('data: ')) continue;

        const jsonData = line.slice(6);
        if (jsonData === '[DONE]') break;

        try {
          const event = JSON.parse(jsonData);
          switch (event.type) {
            case 'start':
              callbacks.onStart?.(event.data.model);
              break;
            case 'content':
              callbacks.onContent?.(event.data.text);
              break;
            case 'done':
              callbacks.onDone?.(
                event.data.assistant_message,
                event.data.credits_consumed
              );
              break;
            case 'error':
              callbacks.onError?.(event.data.message);
              break;
          }
        } catch (e) {
          console.error('解析 SSE 事件失败:', e);
        }
      }
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : '未知错误';
    callbacks.onError?.(message);
    throw error;
  }
}
```

**完成标准**：
- [ ] 函数创建成功
- [ ] 调用正确的 `/regenerate` 端点
- [ ] SSE 解析正确

---

### 步骤 2.2：重构策略A，使用函数式 setState

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `frontend/src/components/chat/MessageArea.tsx` |

**修改范围**（第124-181行）：

**关键改动**：
1. 调用新的 `regenerateMessageStream` API
2. 使用函数式 `setMessages((prev) => ...)` 避免闭包问题
3. 移除 `messages` 从依赖数组
4. 使用局部 `contentRef` 累积内容
5. 添加对话ID验证，防止对话切换时写入错误对话

```typescript
const regenerateFailedMessage = useCallback(async (
  messageId: string,
  targetMessage: Message,
  userMessage: Message
) => {
  if (!conversationId) return;

  // 保存当前对话ID，用于后续验证
  const regeneratingConversationId = conversationId;

  setRegeneratingId(messageId);
  setIsRegeneratingAI(true);

  // 使用局部ref存储累积内容，避免闭包问题
  const contentRef = { current: '' };

  try {
    await regenerateMessageStream(
      conversationId,
      messageId,
      {
        onContent: (content: string) => {
          contentRef.current += content;

          // 使用函数式setState，避免闭包
          setMessages((prevMessages) => {
            // 验证对话是否仍未改变
            if (conversationId !== regeneratingConversationId) {
              console.warn('[重新生成] 对话已切换，忽略更新');
              return prevMessages;
            }

            return prevMessages.map((m) =>
              m.id === messageId
                ? { ...m, content: contentRef.current, is_error: false }
                : m
            );
          });

          if (!userScrolledAway) scrollToBottom();
        },
        onDone: (finalMessage: Message | null) => {
          if (!finalMessage) return;

          setMessages((prevMessages) => {
            if (conversationId !== regeneratingConversationId) {
              return prevMessages;
            }

            const finalMessages = prevMessages.map((m) =>
              m.id === messageId ? finalMessage : m
            );

            // 同步更新缓存
            const cached = getCachedMessages(conversationId);
            if (cached) {
              updateCachedMessages(
                conversationId,
                finalMessages.map(toStoreMessage),
                cached.hasMore
              );
            }

            return finalMessages;
          });

          resetRegeneratingState();
          if (onMessageUpdate) onMessageUpdate(finalMessage.content);
        },
        onError: (error: string) => {
          console.error('重试失败:', error);

          // 恢复原消息
          setMessages((prevMessages) =>
            prevMessages.map((m) =>
              m.id === messageId ? targetMessage : m
            )
          );

          resetRegeneratingState();
          toast.error(`重试失败: ${error}`);
        },
      }
    );
  } catch (error) {
    console.error('重新生成异常:', error);

    setMessages((prevMessages) =>
      prevMessages.map((m) =>
        m.id === messageId ? targetMessage : m
      )
    );

    resetRegeneratingState();
    toast.error('重新生成失败，请重试');
  }
}, [
  conversationId,
  modelId,
  userScrolledAway,
  scrollToBottom,
  getCachedMessages,
  updateCachedMessages,
  toStoreMessage,
  onMessageUpdate,
  resetRegeneratingState
]);
// 注意：移除了 messages 依赖
```

**完成标准**：
- [ ] 调用 regenerateMessageStream 而非 sendMessageStream
- [ ] 使用函数式 setState
- [ ] 移除 messages 依赖
- [ ] 添加对话ID验证

---

### 步骤 2.3：增强错误处理

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `frontend/src/components/chat/MessageArea.tsx` |

**修改范围**（handleRegenerate 函数）：

```typescript
const handleRegenerate = useCallback(async (messageId: string) => {
  if (!conversationId || regeneratingId) return;

  const targetMessage = messages.find((m) => m.id === messageId);
  if (!targetMessage || targetMessage.role !== 'assistant') return;

  // 查找对应的用户消息
  const aiIndex = messages.findIndex((m) => m.id === messageId);
  let userMessage: Message | null = null;
  for (let i = aiIndex - 1; i >= 0; i--) {
    if (messages[i].role === 'user') {
      userMessage = messages[i];
      break;
    }
  }

  if (!userMessage) {
    toast.error('未找到对应的用户消息');
    return;
  }

  try {
    if (targetMessage.is_error === true) {
      await regenerateFailedMessage(messageId, targetMessage, userMessage);
    } else {
      await regenerateAsNewMessage(userMessage);
    }
  } catch (error) {
    console.error('重新生成失败:', error);

    // 增强错误恢复
    if (targetMessage.is_error === true) {
      setMessages((prev) =>
        prev.map((m) => (m.id === messageId ? targetMessage : m))
      );
    }

    resetRegeneratingState();

    const errorMsg = error instanceof Error ? error.message : '未知错误';
    toast.error(`重新生成失败: ${errorMsg}`);

    // 记录详细日志
    console.error('重新生成失败详情:', {
      messageId,
      conversationId,
      isError: targetMessage.is_error,
      error
    });
  }
}, [
  conversationId,
  messages,
  setMessages,
  regeneratingId,
  regenerateFailedMessage,
  regenerateAsNewMessage,
  resetRegeneratingState
]);
```

**完成标准**：
- [ ] 错误处理完整
- [ ] 失败时消息不会消失
- [ ] 日志记录详细

---

### 阶段二验证方案

**测试场景1：失败消息重新生成**
```
1. 发送一条消息导致AI服务错误（显示错误消息）
2. 点击"重试"按钮
3. 验证：
   - 消息内容被清空显示为空
   - 流式内容逐步累积
   - 最终显示完整AI回复
   - 错误标志被清除（is_error=false）
```

**测试场景2：网络中断恢复**
```
1. 发送一条消息导致错误
2. 点击"重试"
3. 在流式传输中断开网络
4. 验证：
   - 显示错误提示
   - 消息恢复到错误状态（不消失）
   - 可以再次点击重试
```

**测试场景3：对话切换**
```
1. 在对话A中点击重新生成消息
2. 在流式传输进行到50%时，切换到对话B
3. 等待完成后切回对话A
4. 验证：
   - 对话A的消息正确显示
   - 对话B没有来自对话A的消息
```

---

### 阶段二完成检查清单

- [ ] regenerateMessageStream 函数创建
- [ ] regenerateFailedMessage 使用新 API
- [ ] 使用函数式 setState
- [ ] 对话ID验证生效
- [ ] 错误处理完整
- [ ] 所有测试场景通过

---

## 五、阶段三：前端功能完善

### 目标
- 停止生成功能
- 前端任务限制检查
- 通知队列上限
- 技能选择提示处理
- 个人设置页面

---

### 步骤 3.1：停止生成功能

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `frontend/src/services/message.ts` |
| 修改 | `frontend/src/components/chat/MessageArea.tsx` |
| 修改 | `frontend/src/components/chat/InputArea.tsx` |
| 修改 | `backend/api/routes/message.py` |

**前端修改1：添加 AbortController 支持**
```typescript
// message.ts
export async function sendMessageStream(
  conversationId: string,
  content: string,
  callbacks: StreamCallbacks,
  options?: {
    signal?: AbortSignal;
    imageUrl?: string;
  }
) {
  const response = await fetch(url, {
    ...options,
    signal: options?.signal,
  });
  // ...
}
```

**前端修改2：添加停止按钮**
```typescript
// MessageArea.tsx 或 InputArea.tsx
const [abortController, setAbortController] = useState<AbortController | null>(null);

const handleStop = () => {
  abortController?.abort();
  setAbortController(null);
};

// UI
{isStreaming && (
  <button onClick={handleStop}>停止生成</button>
)}
```

**后端修改：添加中断 API**
```python
# backend/api/routes/message.py
@router.post("/{task_id}/abort")
async def abort_task(task_id: str, user: CurrentUser):
    # 标记任务为 cancelled
    # 返回结果
```

**完成标准**：
- [ ] 停止按钮在流式输出时显示
- [ ] 点击后流立即中断
- [ ] UI 正确恢复

---

### 步骤 3.2：前端任务限制检查

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `frontend/src/stores/useTaskStore.ts` |
| 修改 | `frontend/src/components/chat/InputArea.tsx` |

**修改范围**：
```typescript
// useTaskStore.ts
const GLOBAL_LIMIT = 15;
const CONVERSATION_LIMIT = 5;

canStartTask: (conversationId: string) => {
  const state = get();
  const globalCount = state.activeTasks.size;
  const convCount = Array.from(state.activeTasks.values())
    .filter(t => t.conversationId === conversationId).length;
  
  return globalCount < GLOBAL_LIMIT && convCount < CONVERSATION_LIMIT;
}

// InputArea.tsx handleSubmit
const handleSubmit = async () => {
  if (!useTaskStore.getState().canStartTask(conversationId)) {
    toast.error('任务队列已满，请等待当前任务完成');
    return;
  }
  // ...
};
```

**完成标准**：
- [ ] canStartTask 方法正确判断
- [ ] 超限时显示友好提示

---

### 步骤 3.3：通知队列上限

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `frontend/src/stores/useTaskStore.ts` |

**修改范围**：
```typescript
const MAX_NOTIFICATIONS = 50;

completeTask: (conversationId: string) => {
  set((state) => {
    let newNotifications = [
      ...state.pendingNotifications,
      { conversationId, completedAt: Date.now() }
    ];
    
    if (newNotifications.length > MAX_NOTIFICATIONS) {
      newNotifications = newNotifications.slice(-MAX_NOTIFICATIONS);
    }
    
    return { pendingNotifications: newNotifications };
  });
}
```

**完成标准**：
- [ ] 通知队列不超过 50 条

---

### 步骤 3.4：技能选择提示处理

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `frontend/src/components/chat/InputControls.tsx` |

**修改范围**：
```typescript
// 修改 placeholder
placeholder='发消息...'  // 移除 "或输入'/'选择技能" 误导提示
```

**完成标准**：
- [ ] placeholder 不再包含技能选择提示

---

### 步骤 3.5：个人设置页面

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 新建 | `frontend/src/pages/Settings.tsx` |
| 修改 | `frontend/src/App.tsx` |
| 修改 | `frontend/src/components/chat/Sidebar.tsx` |

**页面功能**：
- 显示用户信息（昵称、手机号）
- 修改昵称
- 积分余额显示
- 退出登录

**完成标准**：
- [ ] /settings 路由可访问
- [ ] 显示用户基本信息
- [ ] 可修改昵称

---

### 阶段三完成检查清单

- [ ] 停止按钮显示且可用
- [ ] 前端发送限制检查生效
- [ ] 通知队列有上限
- [ ] placeholder 无误导
- [ ] 个人设置页面可访问

---

## 六、阶段四：测试与收尾

### 步骤 4.1-4.3：核心服务单元测试

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 新建 | `tests/test_image_service.py` |
| 新建 | `tests/test_video_service.py` |
| 新建 | `tests/test_credit_service.py` |
| 新建 | `tests/test_task_limit_service.py` |

**测试覆盖目标**：
- 正常流程
- 边界条件（积分刚好/不足）
- 异常处理（API 失败）
- 并发场景

**完成标准**：
- [ ] 核心服务测试覆盖率 ≥ 80%
- [ ] 所有测试通过

---

### 步骤 4.4：更新文档

**文件清单**：
| 操作 | 文件路径 |
|-----|---------|
| 修改 | `docs/CURRENT_ISSUES.md` |
| 修改 | `docs/FUNCTION_INDEX.md` |

**更新内容**：
- 记录已修复的问题
- 更新函数索引（新增的服务）
- 更新项目结构（新增的文件）

**完成标准**：
- [ ] CURRENT_ISSUES.md 更新
- [ ] FUNCTION_INDEX.md 更新

---

## 七、风险与回滚方案

### 高风险操作

| 操作 | 风险 | 回滚方案 |
|-----|------|---------|
| 数据库迁移 | 生产数据影响 | 先在测试环境验证 |
| 积分服务切换 | 扣费不一致 | 保留旧逻辑，双写期 |
| API 限流 | 正常用户被限制 | 先配置宽松限制 |
| 重新生成API切换 | 功能不可用 | 保留旧函数可回退 |

### 降级策略

| 服务 | 降级方案 |
|-----|---------|
| Redis 不可用 | 内存计数 + 告警 |
| 任务限制服务异常 | 跳过检查 + 告警 |
| 积分服务异常 | 使用旧逻辑 + 告警 |

---

## 八、进度跟踪

### 阶段一进度
- [ ] 1.1 数据库迁移（枚举 + 表）
- [ ] 1.2 Redis 连接管理
- [ ] 1.3 任务限制服务
- [ ] 1.4 积分服务（含锁定/退回）
- [ ] 1.5 API 限流
- [ ] 1.6 业务服务集成

### 阶段二进度
- [ ] 2.1 regenerateMessageStream 函数
- [ ] 2.2 重构策略A（函数式 setState）
- [ ] 2.3 增强错误处理

### 阶段三进度
- [ ] 3.1 停止生成功能
- [ ] 3.2 前端任务限制
- [ ] 3.3 通知队列上限
- [ ] 3.4 技能选择提示
- [ ] 3.5 个人设置页面

### 阶段四进度
- [ ] 4.1-4.3 单元测试
- [ ] 4.4 文档更新

---

## 九、关键文件修改清单

### 新建文件
| 文件路径 | 用途 | 阶段 |
|---------|-----|-----|
| `backend/core/redis.py` | Redis 连接管理 | 一 |
| `backend/services/task_limit_service.py` | 任务限制服务 | 一 |
| `backend/services/credit_service.py` | 积分服务 | 一 |
| `docs/database/migrations/005_*.sql` | 枚举迁移 | 一 |
| `docs/database/migrations/006_*.sql` | 任务表迁移 | 一 |
| `docs/database/migrations/007_*.sql` | 积分事务表 | 一 |
| `frontend/src/pages/Settings.tsx` | 个人设置页面 | 三 |
| `tests/test_*.py` | 单元测试 | 四 |

### 修改文件
| 文件路径 | 修改内容 | 阶段 |
|---------|---------|-----|
| `backend/main.py` | 添加限流中间件 | 一 |
| `backend/requirements.txt` | 添加 redis, slowapi | 一 |
| `backend/services/image_service.py` | 使用 CreditService | 一 |
| `backend/services/video_service.py` | 使用 CreditService | 一 |
| `backend/api/routes/message.py` | 任务限制 + 中断API | 一/三 |
| `backend/schemas/message.py` | URL 验证 | 一 |
| `frontend/src/services/message.ts` | regenerateMessageStream | 二 |
| `frontend/src/components/chat/MessageArea.tsx` | 重构重新生成逻辑 | 二 |
| `frontend/src/stores/useTaskStore.ts` | 任务限制检查 | 三 |
| `frontend/src/components/chat/InputArea.tsx` | 发送限制 + 停止按钮 | 三 |
| `frontend/src/components/chat/InputControls.tsx` | 移除误导提示 | 三 |
| `frontend/src/App.tsx` | 添加 /settings 路由 | 三 |

---

**文档版本**：v2.0  
**最后更新**：2026-01-26  
**更新说明**：合并 TECH_MESSAGE_REGENERATION_FIX.md，根据反馈调整问题优先级
