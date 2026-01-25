-- ========================================
-- EVERYDAYAIONE Supabase 数据库初始化脚本
-- ========================================
-- 创建时间: 2026-01-21
-- 说明: PostgreSQL/Supabase 兼容版本
-- ========================================

-- 启用 UUID 扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ========================================
-- 1. 枚举类型定义
-- ========================================

-- 用户创建来源
CREATE TYPE user_created_by AS ENUM ('wechat', 'phone', 'system');

-- 用户角色
CREATE TYPE user_role AS ENUM ('user', 'admin', 'super_admin');

-- 账号状态
CREATE TYPE account_status AS ENUM ('active', 'disabled');

-- 模型类型
CREATE TYPE model_type AS ENUM ('text', 'image', 'multimodal');

-- 模型状态
CREATE TYPE model_status AS ENUM ('active', 'maintenance', 'coming_soon');

-- 消息角色
CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system');

-- 积分变动类型
CREATE TYPE credits_change_type AS ENUM (
  'register_gift',
  'admin_adjust',
  'conversation_cost',
  'image_generation_cost',
  'daily_checkin',
  'purchase'
);


-- ========================================
-- 2. 用户表 (users)
-- ========================================
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  -- 基本信息
  nickname VARCHAR(50) NOT NULL,
  avatar_url VARCHAR(500),

  -- 登录方式字段
  phone VARCHAR(20) UNIQUE,
  password_hash VARCHAR(255),
  wechat_openid VARCHAR(100) UNIQUE,
  wechat_unionid VARCHAR(100) UNIQUE,

  -- 账号元数据
  login_methods JSONB DEFAULT '["phone"]'::jsonb,
  created_by user_created_by DEFAULT 'phone',
  role user_role DEFAULT 'user',
  credits INTEGER DEFAULT 100,
  status account_status DEFAULT 'active',

  -- 时间戳
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  last_login_at TIMESTAMPTZ
);

-- 用户表索引
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_wechat_openid ON users(wechat_openid);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);


-- ========================================
-- 3. 模型表 (models)
-- ========================================
CREATE TABLE IF NOT EXISTS models (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  -- 模型基本信息
  name VARCHAR(100) NOT NULL,
  provider VARCHAR(50) NOT NULL,
  model_key VARCHAR(100) NOT NULL UNIQUE,
  description TEXT,
  icon_url VARCHAR(500),

  -- 模型分类
  type model_type NOT NULL,

  -- 状态控制
  status model_status DEFAULT 'coming_soon',
  is_default BOOLEAN DEFAULT FALSE,

  -- 定价配置
  credits_per_request INTEGER NOT NULL DEFAULT 10,

  -- 使用统计
  total_calls BIGINT DEFAULT 0,
  total_subscribers INTEGER DEFAULT 0,

  -- API配置（加密存储）
  api_key VARCHAR(500),
  api_endpoint VARCHAR(500),

  -- 时间戳
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 模型表索引
CREATE INDEX IF NOT EXISTS idx_models_status ON models(status);
CREATE INDEX IF NOT EXISTS idx_models_type ON models(type);
CREATE INDEX IF NOT EXISTS idx_models_model_key ON models(model_key);


-- ========================================
-- 4. 用户订阅模型表 (user_subscriptions)
-- ========================================
CREATE TABLE IF NOT EXISTS user_subscriptions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  model_id UUID NOT NULL REFERENCES models(id) ON DELETE CASCADE,

  subscribed_at TIMESTAMPTZ DEFAULT NOW(),

  -- 唯一约束
  UNIQUE(user_id, model_id)
);

-- 订阅表索引
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_id ON user_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_model_id ON user_subscriptions(model_id);


-- ========================================
-- 5. 对话记录表 (conversations)
-- ========================================
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(200) DEFAULT '新对话',
  model_id UUID REFERENCES models(id) ON DELETE SET NULL,

  -- 统计信息
  message_count INTEGER DEFAULT 0,
  credits_consumed INTEGER DEFAULT 0,

  -- 时间戳
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 对话表索引
CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_model_id ON conversations(model_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);


-- ========================================
-- 6. 消息记录表 (messages)
-- ========================================
CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role message_role NOT NULL,
  content TEXT NOT NULL,

  -- 图片 URL（用户上传或 AI 生成的图片）
  image_url VARCHAR(500),

  -- 成本统计
  credits_cost INTEGER DEFAULT 0,

  -- 时间戳
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 消息表索引
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);


-- ========================================
-- 7. 图片生成记录表 (image_generations)
-- ========================================
CREATE TABLE IF NOT EXISTS image_generations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  model_id UUID REFERENCES models(id) ON DELETE SET NULL,

  -- 生成参数
  prompt TEXT NOT NULL,
  negative_prompt TEXT,
  image_size VARCHAR(20),

  -- 结果信息
  image_url VARCHAR(500) NOT NULL,
  credits_cost INTEGER DEFAULT 0,

  -- 时间戳
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 图片生成表索引
CREATE INDEX IF NOT EXISTS idx_image_generations_user_id ON image_generations(user_id);
CREATE INDEX IF NOT EXISTS idx_image_generations_conversation_id ON image_generations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_image_generations_model_id ON image_generations(model_id);
CREATE INDEX IF NOT EXISTS idx_image_generations_created_at ON image_generations(created_at);


-- ========================================
-- 8. 积分历史表 (credits_history)
-- ========================================
CREATE TABLE IF NOT EXISTS credits_history (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  -- 变动信息
  change_amount INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,

  -- 变动类型
  change_type credits_change_type NOT NULL,

  -- 关联信息
  related_id UUID,
  description VARCHAR(500),
  operator_id UUID,

  -- 时间戳
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 积分历史表索引
CREATE INDEX IF NOT EXISTS idx_credits_history_user_id ON credits_history(user_id);
CREATE INDEX IF NOT EXISTS idx_credits_history_change_type ON credits_history(change_type);
CREATE INDEX IF NOT EXISTS idx_credits_history_created_at ON credits_history(created_at);


-- ========================================
-- 9. 管理员操作日志表 (admin_action_logs)
-- ========================================
CREATE TABLE IF NOT EXISTS admin_action_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

  -- 操作者信息
  admin_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  admin_role user_role NOT NULL,

  -- 操作信息
  action_type VARCHAR(50) NOT NULL,
  action_description TEXT,

  -- 目标信息
  target_user_id UUID,
  target_resource_type VARCHAR(50),
  target_resource_id UUID,

  -- 操作详情
  reason TEXT,
  changes_data JSONB,

  -- 请求信息
  ip_address VARCHAR(50),
  user_agent VARCHAR(500),

  -- 时间戳
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 管理员日志表索引
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_admin_id ON admin_action_logs(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_action_type ON admin_action_logs(action_type);
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_target_user_id ON admin_action_logs(target_user_id);
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_created_at ON admin_action_logs(created_at);


-- ========================================
-- 10. 触发器：自动更新 updated_at
-- ========================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ language 'plpgsql';

-- 为需要的表添加触发器
CREATE TRIGGER update_users_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_models_updated_at
  BEFORE UPDATE ON models
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_conversations_updated_at
  BEFORE UPDATE ON conversations
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();


-- ========================================
-- 11. RLS (Row Level Security) 策略
-- ========================================

-- 启用 RLS
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE image_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE credits_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_subscriptions ENABLE ROW LEVEL SECURITY;

-- 用户只能访问自己的数据（通过 JWT 中的 sub 字段）
-- 注意：这些策略假设使用 Supabase Auth 或自定义 JWT

-- 用户表策略：用户可以读取和更新自己的信息
CREATE POLICY "Users can view own data" ON users
  FOR SELECT USING (auth.uid()::text = id::text);

CREATE POLICY "Users can update own data" ON users
  FOR UPDATE USING (auth.uid()::text = id::text);

-- 对话表策略
CREATE POLICY "Users can view own conversations" ON conversations
  FOR SELECT USING (auth.uid()::text = user_id::text);

CREATE POLICY "Users can insert own conversations" ON conversations
  FOR INSERT WITH CHECK (auth.uid()::text = user_id::text);

CREATE POLICY "Users can update own conversations" ON conversations
  FOR UPDATE USING (auth.uid()::text = user_id::text);

CREATE POLICY "Users can delete own conversations" ON conversations
  FOR DELETE USING (auth.uid()::text = user_id::text);

-- 消息表策略（通过对话关联）
CREATE POLICY "Users can view own messages" ON messages
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM conversations
      WHERE conversations.id = messages.conversation_id
      AND auth.uid()::text = conversations.user_id::text
    )
  );

CREATE POLICY "Users can insert own messages" ON messages
  FOR INSERT WITH CHECK (
    EXISTS (
      SELECT 1 FROM conversations
      WHERE conversations.id = messages.conversation_id
      AND auth.uid()::text = conversations.user_id::text
    )
  );

-- 图片生成表策略
CREATE POLICY "Users can view own image generations" ON image_generations
  FOR SELECT USING (auth.uid()::text = user_id::text);

CREATE POLICY "Users can insert own image generations" ON image_generations
  FOR INSERT WITH CHECK (auth.uid()::text = user_id::text);

-- 积分历史表策略
CREATE POLICY "Users can view own credits history" ON credits_history
  FOR SELECT USING (auth.uid()::text = user_id::text);

-- 订阅表策略
CREATE POLICY "Users can view own subscriptions" ON user_subscriptions
  FOR SELECT USING (auth.uid()::text = user_id::text);

CREATE POLICY "Users can manage own subscriptions" ON user_subscriptions
  FOR ALL USING (auth.uid()::text = user_id::text);

-- 模型表：所有认证用户可读
ALTER TABLE models ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Authenticated users can view models" ON models
  FOR SELECT TO authenticated USING (true);


-- ========================================
-- 12. 初始化数据
-- ========================================

-- 插入默认模型
INSERT INTO models (name, provider, model_key, description, type, status, is_default, credits_per_request)
VALUES
  ('Gemini 2.5 Flash Preview', 'Google', 'gemini-2.5-flash-preview', 'Google最新的快速多模态模型，支持文本和图片理解', 'multimodal', 'active', TRUE, 5),
  ('Gemini 3 Flash Preview', 'Google', 'gemini-3-flash-preview', 'Google下一代Flash模型预览版', 'multimodal', 'active', TRUE, 8),
  ('GPT-4 Turbo', 'OpenAI', 'gpt-4-turbo', 'OpenAI最强大的文本生成模型', 'text', 'active', FALSE, 15),
  ('Claude 3.5 Sonnet', 'Anthropic', 'claude-3.5-sonnet', 'Anthropic高性能智能助手', 'text', 'active', FALSE, 12),
  ('DALL-E 3', 'OpenAI', 'dall-e-3', 'OpenAI最新的图片生成模型', 'image', 'active', FALSE, 20),
  ('Stable Diffusion XL', 'Stability AI', 'stable-diffusion-xl', '开源图片生成模型', 'image', 'active', FALSE, 15),
  ('Midjourney V6', 'Midjourney', 'midjourney-v6', '艺术级图片生成模型', 'image', 'coming_soon', FALSE, 25)
ON CONFLICT (model_key) DO NOTHING;


-- ========================================
-- 完成提示
-- ========================================
-- 数据库初始化完成！
-- 注意：超级管理员账号需要通过应用注册流程或手动插入
