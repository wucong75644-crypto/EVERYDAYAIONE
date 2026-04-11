-- ============================================================
-- EverydayAI 本地数据库初始化脚本（合并全部迁移）
-- 适用于：从零开始在本地 PostgreSQL 建表
-- 用法：psql -h 127.0.0.1 -U everydayai -d everydayai -f init-database.sql
-- ============================================================

-- ============================================================
-- 0. 启用扩展（需要 superuser 预先执行，setup-database.sh 已处理）
-- ============================================================
-- CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
-- CREATE EXTENSION IF NOT EXISTS vector;
-- CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================
-- 1. 枚举类型
-- ============================================================
DO $$ BEGIN CREATE TYPE user_created_by AS ENUM ('wechat', 'phone', 'system', 'wecom'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE user_role AS ENUM ('user', 'admin', 'super_admin'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE account_status AS ENUM ('active', 'disabled'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE model_type AS ENUM ('text', 'image', 'multimodal'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE model_status AS ENUM ('active', 'maintenance', 'coming_soon'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE message_role AS ENUM ('user', 'assistant', 'system'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE credits_change_type AS ENUM ('register_gift', 'admin_adjust', 'conversation_cost', 'image_generation_cost', 'daily_checkin', 'purchase', 'video_generation_cost', 'merge', 'refund'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ============================================================
-- 2. 核心表
-- ============================================================

-- users
CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  nickname VARCHAR(50) NOT NULL,
  avatar_url VARCHAR(500),
  phone VARCHAR(20) UNIQUE,
  password_hash VARCHAR(255),
  wechat_openid VARCHAR(100) UNIQUE,
  wechat_unionid VARCHAR(100) UNIQUE,
  login_methods JSONB DEFAULT '["phone"]'::jsonb,
  created_by user_created_by DEFAULT 'phone',
  role user_role DEFAULT 'user',
  credits INTEGER DEFAULT 100,
  status account_status DEFAULT 'active',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  last_login_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_wechat_openid ON users(wechat_openid);
CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
CREATE INDEX IF NOT EXISTS idx_users_status ON users(status);
CREATE INDEX IF NOT EXISTS idx_users_created_at ON users(created_at);

-- models
CREATE TABLE IF NOT EXISTS models (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name VARCHAR(100) NOT NULL,
  provider VARCHAR(50) NOT NULL,
  model_key VARCHAR(100) NOT NULL UNIQUE,
  description TEXT,
  icon_url VARCHAR(500),
  type model_type NOT NULL,
  status model_status DEFAULT 'coming_soon',
  is_default BOOLEAN DEFAULT FALSE,
  credits_per_request INTEGER NOT NULL DEFAULT 10,
  total_calls BIGINT DEFAULT 0,
  total_subscribers INTEGER DEFAULT 0,
  api_key VARCHAR(500),
  api_endpoint VARCHAR(500),
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_models_status ON models(status);
CREATE INDEX IF NOT EXISTS idx_models_type ON models(type);
CREATE INDEX IF NOT EXISTS idx_models_model_key ON models(model_key);

-- user_subscriptions
CREATE TABLE IF NOT EXISTS user_subscriptions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  model_id VARCHAR(100) NOT NULL,
  subscribed_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(user_id, model_id)
);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_id ON user_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_subscriptions_model_id ON user_subscriptions(model_id);

-- conversations
CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(200) DEFAULT '新对话',
  model_id VARCHAR(100),
  message_count INTEGER DEFAULT 0,
  credits_consumed INTEGER DEFAULT 0,
  last_message_preview TEXT,
  context_summary TEXT,
  summary_message_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_model_id ON conversations(model_id);
CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);

-- messages
CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role message_role NOT NULL,
  content TEXT NOT NULL,
  image_url VARCHAR(500),
  video_url TEXT,
  credits_cost INTEGER DEFAULT 0,
  is_error BOOLEAN DEFAULT false,
  generation_params JSONB,
  client_request_id VARCHAR(100),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_client_request_id ON messages(client_request_id) WHERE client_request_id IS NOT NULL;
ALTER TABLE messages ADD CONSTRAINT generation_params_size_limit CHECK (pg_column_size(generation_params) < 10240);

-- image_generations
CREATE TABLE IF NOT EXISTS image_generations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  model_id UUID REFERENCES models(id) ON DELETE SET NULL,
  prompt TEXT NOT NULL,
  negative_prompt TEXT,
  image_size VARCHAR(20),
  image_url VARCHAR(500) NOT NULL,
  credits_cost INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_image_generations_user_id ON image_generations(user_id);
CREATE INDEX IF NOT EXISTS idx_image_generations_conversation_id ON image_generations(conversation_id);
CREATE INDEX IF NOT EXISTS idx_image_generations_model_id ON image_generations(model_id);
CREATE INDEX IF NOT EXISTS idx_image_generations_created_at ON image_generations(created_at);

-- credits_history
CREATE TABLE IF NOT EXISTS credits_history (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  change_amount INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  change_type credits_change_type NOT NULL,
  related_id UUID,
  description VARCHAR(500),
  operator_id UUID,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_credits_history_user_id ON credits_history(user_id);
CREATE INDEX IF NOT EXISTS idx_credits_history_change_type ON credits_history(change_type);
CREATE INDEX IF NOT EXISTS idx_credits_history_created_at ON credits_history(created_at);

-- admin_action_logs
CREATE TABLE IF NOT EXISTS admin_action_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  admin_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  admin_role user_role NOT NULL,
  action_type VARCHAR(50) NOT NULL,
  action_description TEXT,
  target_user_id UUID,
  target_resource_type VARCHAR(50),
  target_resource_id UUID,
  reason TEXT,
  changes_data JSONB,
  ip_address VARCHAR(50),
  user_agent VARCHAR(500),
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_admin_id ON admin_action_logs(admin_id);
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_action_type ON admin_action_logs(action_type);
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_target_user_id ON admin_action_logs(target_user_id);
CREATE INDEX IF NOT EXISTS idx_admin_action_logs_created_at ON admin_action_logs(created_at);

-- ============================================================
-- 3. 任务表
-- ============================================================

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    type VARCHAR(20) NOT NULL CHECK (type IN ('chat', 'image', 'video')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    credits_locked INTEGER DEFAULT 0,
    credits_used INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    -- 010: 持久化字段
    external_task_id VARCHAR(100),
    request_params JSONB,
    result JSONB,
    fail_code VARCHAR(50),
    placeholder_message_id VARCHAR(100),
    last_polled_at TIMESTAMPTZ,
    client_context JSONB,
    kie_url_expires_at TIMESTAMPTZ,
    version INTEGER DEFAULT 1,
    oss_retry_count INTEGER DEFAULT 0,
    -- 014:
    placeholder_created_at TIMESTAMPTZ,
    -- 015: 聊天任务
    accumulated_content TEXT,
    model_id VARCHAR(100),
    total_credits INTEGER DEFAULT 0,
    assistant_message_id UUID,
    -- 017: 前端乐观订阅
    client_task_id VARCHAR(100),
    -- 020: 多图批次
    image_index INTEGER,
    batch_id TEXT,
    result_data JSONB,
    -- 016: 积分事务关联
    credit_transaction_id UUID
);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_conversation ON tasks(conversation_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tasks_external_id ON tasks(external_task_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status_type ON tasks(status, type);
CREATE INDEX IF NOT EXISTS idx_tasks_user_pending ON tasks(user_id, status) WHERE status IN ('pending', 'running');
CREATE INDEX IF NOT EXISTS idx_tasks_assistant_message_id ON tasks(assistant_message_id) WHERE assistant_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_client_task_id ON tasks(client_task_id) WHERE client_task_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_batch_id ON tasks(batch_id) WHERE batch_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_credit_tx ON tasks(credit_transaction_id) WHERE credit_transaction_id IS NOT NULL;

-- credit_transactions
CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    task_id UUID NOT NULL,
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_credit_tx_task_unique ON credit_transactions(task_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_user ON credit_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_tx_status ON credit_transactions(status, expires_at);

-- tasks FK to credit_transactions（表都建好后再加）
ALTER TABLE tasks DROP CONSTRAINT IF EXISTS tasks_credit_transaction_id_fkey;
ALTER TABLE tasks ADD CONSTRAINT tasks_credit_transaction_id_fkey
    FOREIGN KEY (credit_transaction_id) REFERENCES credit_transactions(id);

-- ============================================================
-- 4. 记忆/知识库表
-- ============================================================

CREATE TABLE IF NOT EXISTS user_memory_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    memory_enabled BOOLEAN NOT NULL DEFAULT true,
    retention_days INTEGER NOT NULL DEFAULT 7,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_memory_settings_user_id ON user_memory_settings(user_id);

-- knowledge_metrics
CREATE TABLE IF NOT EXISTS knowledge_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_type TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL,
    error_code TEXT,
    cost_time_ms INT,
    prompt_tokens INT DEFAULT 0,
    completion_tokens INT DEFAULT 0,
    prompt_category TEXT,
    params JSONB DEFAULT '{}',
    retried BOOLEAN NOT NULL DEFAULT FALSE,
    retry_from_model TEXT,
    user_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_metrics_model_type ON knowledge_metrics(model_id, task_type);
CREATE INDEX IF NOT EXISTS idx_metrics_created ON knowledge_metrics(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_status ON knowledge_metrics(status);

-- knowledge_nodes
CREATE TABLE IF NOT EXISTS knowledge_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category TEXT NOT NULL CHECK (category IN ('model', 'tool', 'experience')),
    subcategory TEXT,
    node_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',
    embedding vector(1024),
    source TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('auto', 'seed', 'manual', 'aggregated')),
    confidence FLOAT NOT NULL DEFAULT 0.5,
    hit_count INT NOT NULL DEFAULT 0,
    scope TEXT NOT NULL DEFAULT 'global',
    content_hash TEXT UNIQUE,
    is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_nodes_category ON knowledge_nodes(category, subcategory);
CREATE INDEX IF NOT EXISTS idx_nodes_scope ON knowledge_nodes(scope);
CREATE INDEX IF NOT EXISTS idx_nodes_confidence ON knowledge_nodes(confidence DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_embedding ON knowledge_nodes USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10);

-- knowledge_edges
CREATE TABLE IF NOT EXISTS knowledge_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    target_id UUID NOT NULL REFERENCES knowledge_nodes(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    weight FLOAT NOT NULL DEFAULT 1.0,
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON knowledge_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON knowledge_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_type ON knowledge_edges(relation_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edges_unique ON knowledge_edges(source_id, target_id, relation_type);

-- scoring_audit_log
CREATE TABLE IF NOT EXISTS scoring_audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    old_score FLOAT,
    new_score FLOAT NOT NULL,
    score_change FLOAT NOT NULL,
    sample_count INT NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}',
    period_start TIMESTAMPTZ NOT NULL,
    period_end TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'auto_applied',
    knowledge_node_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_model_task ON scoring_audit_log (model_id, task_type);
CREATE INDEX IF NOT EXISTS idx_audit_status ON scoring_audit_log (status);
CREATE INDEX IF NOT EXISTS idx_audit_created ON scoring_audit_log (created_at DESC);

-- ============================================================
-- 5. 企微表
-- ============================================================

CREATE TABLE IF NOT EXISTS wecom_user_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wecom_userid VARCHAR(64) NOT NULL,
    corp_id VARCHAR(64) NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel VARCHAR(20) NOT NULL DEFAULT 'smart_robot',
    wecom_nickname VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_chatid VARCHAR(128),
    last_chat_type VARCHAR(20) DEFAULT 'single',
    bound_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_wecom_userid_corp ON wecom_user_mappings (wecom_userid, corp_id);
CREATE INDEX IF NOT EXISTS idx_wecom_user_id ON wecom_user_mappings (user_id);
CREATE INDEX IF NOT EXISTS idx_wecom_mappings_user_id ON wecom_user_mappings(user_id);

CREATE TABLE IF NOT EXISTS wecom_chat_targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    chatid VARCHAR(128) NOT NULL,
    chat_type VARCHAR(20) NOT NULL DEFAULT 'group',
    chat_name VARCHAR(256),
    corp_id VARCHAR(64) NOT NULL,
    first_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    message_count INT NOT NULL DEFAULT 1,
    UNIQUE(chatid, corp_id)
);
CREATE INDEX IF NOT EXISTS idx_chat_targets_corp_type ON wecom_chat_targets(corp_id, chat_type);

CREATE TABLE IF NOT EXISTS wecom_departments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    department_id INT NOT NULL,
    corp_id VARCHAR(64) NOT NULL,
    name VARCHAR(256) NOT NULL,
    parent_id INT DEFAULT 0,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(department_id, corp_id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_dept_parent ON wecom_departments(corp_id, parent_id);

CREATE TABLE IF NOT EXISTS wecom_employees (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wecom_userid VARCHAR(64) NOT NULL,
    corp_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    department_ids INT[] DEFAULT '{}',
    status INT NOT NULL DEFAULT 1,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(wecom_userid, corp_id)
);
CREATE INDEX IF NOT EXISTS idx_wecom_emp_status ON wecom_employees(corp_id, status);

-- ============================================================
-- 6. ERP 表（空表，数据通过同步任务拉取）
-- ============================================================

CREATE TABLE IF NOT EXISTS erp_document_items (
    id BIGSERIAL PRIMARY KEY,
    doc_type VARCHAR(20) NOT NULL,
    doc_id VARCHAR(64) NOT NULL,
    doc_code VARCHAR(64),
    doc_status VARCHAR(32),
    doc_created_at TIMESTAMP,
    doc_modified_at TIMESTAMP,
    item_index INTEGER NOT NULL DEFAULT 0,
    outer_id VARCHAR(128),
    sku_outer_id VARCHAR(128),
    item_name VARCHAR(256),
    quantity DECIMAL(12,2),
    quantity_received DECIMAL(12,2),
    price DECIMAL(12,2),
    amount DECIMAL(12,2),
    supplier_name VARCHAR(128),
    warehouse_name VARCHAR(128),
    shop_name VARCHAR(128),
    platform VARCHAR(20),
    order_no VARCHAR(64),
    order_status VARCHAR(32),
    express_no VARCHAR(64),
    express_company VARCHAR(64),
    cost DECIMAL(12,2),
    pay_time TIMESTAMP,
    consign_time TIMESTAMP,
    refund_status VARCHAR(32),
    discount_fee DECIMAL(12,2),
    post_fee DECIMAL(12,2),
    gross_profit DECIMAL(12,2),
    aftersale_type SMALLINT,
    refund_money DECIMAL(12,2),
    raw_refund_money DECIMAL(12,2),
    text_reason VARCHAR(256),
    finished_at TIMESTAMP,
    real_qty DECIMAL(12,2),
    delivery_date TIMESTAMP,
    actual_return_qty DECIMAL(12,2),
    purchase_order_code VARCHAR(64),
    remark TEXT,
    sys_memo TEXT,
    buyer_message TEXT,
    creator_name VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_doc_items_outer_id ON erp_document_items (outer_id);
CREATE INDEX IF NOT EXISTS idx_doc_items_sku_outer_id ON erp_document_items (sku_outer_id);
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_type_outer ON erp_document_items (doc_type, outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_type_sku ON erp_document_items (doc_type, sku_outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_id ON erp_document_items (doc_type, doc_id);
CREATE INDEX IF NOT EXISTS idx_doc_items_modified ON erp_document_items (doc_modified_at);
CREATE INDEX IF NOT EXISTS idx_doc_items_platform ON erp_document_items (platform) WHERE platform IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_shop ON erp_document_items (shop_name, doc_type);
CREATE INDEX IF NOT EXISTS idx_doc_items_order_no ON erp_document_items (order_no) WHERE order_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_refund ON erp_document_items (refund_status) WHERE refund_status IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_consign ON erp_document_items (consign_time) WHERE consign_time IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_aftersale_type ON erp_document_items (aftersale_type) WHERE aftersale_type IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_finished ON erp_document_items (finished_at) WHERE finished_at IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_doc_items ON erp_document_items (doc_type, doc_id, item_index);
CREATE INDEX IF NOT EXISTS idx_doc_items_express ON erp_document_items (express_no) WHERE express_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_doc_code ON erp_document_items (doc_code, doc_type) WHERE doc_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_supplier ON erp_document_items (supplier_name, doc_type) WHERE supplier_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_items_type_date ON erp_document_items (doc_type, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_items_outer_created ON erp_document_items (outer_id, doc_created_at);

CREATE TABLE IF NOT EXISTS erp_document_items_archive (
    id BIGSERIAL PRIMARY KEY,
    doc_type VARCHAR(20) NOT NULL,
    doc_id VARCHAR(64) NOT NULL,
    doc_code VARCHAR(64),
    doc_status VARCHAR(32),
    doc_created_at TIMESTAMP,
    doc_modified_at TIMESTAMP,
    item_index INTEGER NOT NULL DEFAULT 0,
    outer_id VARCHAR(128),
    sku_outer_id VARCHAR(128),
    item_name VARCHAR(256),
    quantity DECIMAL(12,2),
    quantity_received DECIMAL(12,2),
    price DECIMAL(12,2),
    amount DECIMAL(12,2),
    supplier_name VARCHAR(128),
    warehouse_name VARCHAR(128),
    shop_name VARCHAR(128),
    platform VARCHAR(20),
    order_no VARCHAR(64),
    order_status VARCHAR(32),
    express_no VARCHAR(64),
    express_company VARCHAR(64),
    cost DECIMAL(12,2),
    pay_time TIMESTAMP,
    consign_time TIMESTAMP,
    refund_status VARCHAR(32),
    discount_fee DECIMAL(12,2),
    post_fee DECIMAL(12,2),
    gross_profit DECIMAL(12,2),
    aftersale_type SMALLINT,
    refund_money DECIMAL(12,2),
    raw_refund_money DECIMAL(12,2),
    text_reason VARCHAR(256),
    finished_at TIMESTAMP,
    real_qty DECIMAL(12,2),
    delivery_date TIMESTAMP,
    actual_return_qty DECIMAL(12,2),
    purchase_order_code VARCHAR(64),
    remark TEXT,
    sys_memo TEXT,
    buyer_message TEXT,
    creator_name VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_archive_items_outer_id ON erp_document_items_archive (outer_id);
CREATE INDEX IF NOT EXISTS idx_archive_items_sku_outer_id ON erp_document_items_archive (sku_outer_id);
CREATE INDEX IF NOT EXISTS idx_archive_items_doc_type_outer ON erp_document_items_archive (doc_type, outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_items_doc_type_sku ON erp_document_items_archive (doc_type, sku_outer_id, doc_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_archive_items_doc_id ON erp_document_items_archive (doc_type, doc_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_archive_items ON erp_document_items_archive (doc_type, doc_id, item_index);
CREATE INDEX IF NOT EXISTS idx_archive_items_express ON erp_document_items_archive (express_no) WHERE express_no IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_archive_items_doc_code ON erp_document_items_archive (doc_code, doc_type) WHERE doc_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_archive_items_supplier ON erp_document_items_archive (supplier_name, doc_type) WHERE supplier_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_archive_items_type_date ON erp_document_items_archive (doc_type, doc_created_at DESC);

CREATE TABLE IF NOT EXISTS erp_product_daily_stats (
    id BIGSERIAL PRIMARY KEY,
    stat_date DATE NOT NULL,
    outer_id VARCHAR(128) NOT NULL,
    sku_outer_id VARCHAR(128),
    item_name VARCHAR(256),
    purchase_count INTEGER NOT NULL DEFAULT 0,
    purchase_qty DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_received_qty DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    receipt_count INTEGER NOT NULL DEFAULT 0,
    receipt_qty DECIMAL(12,2) NOT NULL DEFAULT 0,
    shelf_count INTEGER NOT NULL DEFAULT 0,
    shelf_qty DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_return_count INTEGER NOT NULL DEFAULT 0,
    purchase_return_qty DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_return_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    aftersale_count INTEGER NOT NULL DEFAULT 0,
    aftersale_refund_count INTEGER NOT NULL DEFAULT 0,
    aftersale_return_count INTEGER NOT NULL DEFAULT 0,
    aftersale_exchange_count INTEGER NOT NULL DEFAULT 0,
    aftersale_reissue_count INTEGER NOT NULL DEFAULT 0,
    aftersale_reject_count INTEGER NOT NULL DEFAULT 0,
    aftersale_repair_count INTEGER NOT NULL DEFAULT 0,
    aftersale_other_count INTEGER NOT NULL DEFAULT 0,
    aftersale_qty DECIMAL(12,2) NOT NULL DEFAULT 0,
    aftersale_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_count INTEGER NOT NULL DEFAULT 0,
    order_qty DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_amount DECIMAL(12,2) NOT NULL DEFAULT 0,
    order_shipped_count INTEGER NOT NULL DEFAULT 0,
    order_finished_count INTEGER NOT NULL DEFAULT 0,
    order_refund_count INTEGER NOT NULL DEFAULT 0,
    order_cancelled_count INTEGER NOT NULL DEFAULT 0,
    order_cost DECIMAL(12,2) NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_stats ON erp_product_daily_stats (stat_date, outer_id, COALESCE(sku_outer_id, ''));
CREATE INDEX IF NOT EXISTS idx_daily_stats_outer ON erp_product_daily_stats (outer_id, stat_date DESC);
CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON erp_product_daily_stats (stat_date);

CREATE TABLE IF NOT EXISTS erp_products (
    id BIGSERIAL PRIMARY KEY,
    outer_id VARCHAR(128) UNIQUE NOT NULL,
    title VARCHAR(256),
    item_type SMALLINT NOT NULL DEFAULT 0,
    is_virtual BOOLEAN NOT NULL DEFAULT false,
    active_status SMALLINT NOT NULL DEFAULT 1,
    barcode VARCHAR(64),
    purchase_price DECIMAL(12,2),
    selling_price DECIMAL(12,2),
    market_price DECIMAL(12,2),
    weight DECIMAL(10,3),
    unit VARCHAR(16),
    is_gift BOOLEAN NOT NULL DEFAULT false,
    sys_item_id VARCHAR(64),
    brand VARCHAR(64),
    shipper VARCHAR(128),
    remark TEXT,
    created_at TIMESTAMP,
    modified_at TIMESTAMP,
    pic_url VARCHAR(512),
    suit_singles JSONB,
    extra_json JSONB DEFAULT '{}',
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_products_barcode ON erp_products (barcode) WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_products_title ON erp_products USING GIN (title gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_products_type ON erp_products (item_type);
CREATE INDEX IF NOT EXISTS idx_products_shipper ON erp_products (shipper) WHERE shipper IS NOT NULL;

CREATE TABLE IF NOT EXISTS erp_product_skus (
    id BIGSERIAL PRIMARY KEY,
    outer_id VARCHAR(128) NOT NULL,
    sku_outer_id VARCHAR(128) UNIQUE NOT NULL,
    properties_name VARCHAR(256),
    barcode VARCHAR(64),
    purchase_price DECIMAL(12,2),
    selling_price DECIMAL(12,2),
    market_price DECIMAL(12,2),
    weight DECIMAL(10,3),
    unit VARCHAR(16),
    shipper VARCHAR(128),
    pic_url VARCHAR(512),
    sys_sku_id VARCHAR(64),
    active_status SMALLINT NOT NULL DEFAULT 1,
    extra_json JSONB DEFAULT '{}',
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_skus_outer_id ON erp_product_skus (outer_id);
CREATE INDEX IF NOT EXISTS idx_skus_barcode ON erp_product_skus (barcode) WHERE barcode IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_skus_properties_name ON erp_product_skus USING GIN (properties_name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS erp_stock_status (
    id BIGSERIAL PRIMARY KEY,
    outer_id VARCHAR(128) NOT NULL,
    sku_outer_id VARCHAR(128) NOT NULL DEFAULT '',
    item_name VARCHAR(256),
    properties_name VARCHAR(256),
    total_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    sellable_num DECIMAL(12,2) NOT NULL DEFAULT 0,
    available_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    lock_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_num DECIMAL(12,2) NOT NULL DEFAULT 0,
    on_the_way_num DECIMAL(12,2) NOT NULL DEFAULT 0,
    defective_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    virtual_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    stock_status SMALLINT NOT NULL DEFAULT 0,
    purchase_price DECIMAL(12,2),
    selling_price DECIMAL(12,2),
    market_price DECIMAL(12,2),
    allocate_num DECIMAL(12,2) NOT NULL DEFAULT 0,
    refund_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    purchase_stock DECIMAL(12,2) NOT NULL DEFAULT 0,
    supplier_codes VARCHAR(256),
    supplier_names VARCHAR(256),
    warehouse_id VARCHAR(64) NOT NULL DEFAULT '',
    stock_modified_time TIMESTAMP,
    extra_json JSONB DEFAULT '{}',
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_stock_outer_sku ON erp_stock_status (outer_id, sku_outer_id, warehouse_id);
CREATE INDEX IF NOT EXISTS idx_stock_outer_id ON erp_stock_status (outer_id);
CREATE INDEX IF NOT EXISTS idx_stock_sku_outer_id ON erp_stock_status (sku_outer_id) WHERE sku_outer_id != '';
CREATE INDEX IF NOT EXISTS idx_stock_status ON erp_stock_status (stock_status);
CREATE INDEX IF NOT EXISTS idx_stock_sellable ON erp_stock_status (sellable_num);
CREATE INDEX IF NOT EXISTS idx_stock_warehouse ON erp_stock_status (warehouse_id) WHERE warehouse_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS erp_suppliers (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(64) UNIQUE NOT NULL,
    name VARCHAR(128) NOT NULL,
    status SMALLINT NOT NULL DEFAULT 1,
    contact_name VARCHAR(64),
    mobile VARCHAR(32),
    phone VARCHAR(32),
    email VARCHAR(128),
    category_name VARCHAR(64),
    bill_type VARCHAR(32),
    plan_receive_day INTEGER,
    address TEXT,
    remark TEXT,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_suppliers_name ON erp_suppliers (name);
CREATE INDEX IF NOT EXISTS idx_suppliers_status ON erp_suppliers (status);

CREATE TABLE IF NOT EXISTS erp_product_platform_map (
    id BIGSERIAL PRIMARY KEY,
    outer_id VARCHAR(128) NOT NULL,
    num_iid VARCHAR(64) NOT NULL,
    user_id VARCHAR(64),
    title VARCHAR(256),
    sku_mappings JSONB DEFAULT '[]',
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_map ON erp_product_platform_map (outer_id, num_iid);
CREATE INDEX IF NOT EXISTS idx_platform_map_outer ON erp_product_platform_map (outer_id);
CREATE INDEX IF NOT EXISTS idx_platform_map_numiid ON erp_product_platform_map (num_iid);
CREATE INDEX IF NOT EXISTS idx_platform_map_user ON erp_product_platform_map (user_id);

CREATE TABLE IF NOT EXISTS erp_sync_state (
    id SERIAL PRIMARY KEY,
    sync_type VARCHAR(20) UNIQUE NOT NULL,
    last_sync_time TIMESTAMP,
    last_run_at TIMESTAMP,
    error_count SMALLINT NOT NULL DEFAULT 0,
    last_error TEXT,
    total_synced INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(16) NOT NULL DEFAULT 'idle',
    is_initial_done BOOLEAN NOT NULL DEFAULT false
);

-- erp_sync_dead_letter: 同步失败死信队列（detail API 失败时暂存，异步指数退避重试）
CREATE TABLE IF NOT EXISTS erp_sync_dead_letter (
    id BIGSERIAL PRIMARY KEY,
    doc_type VARCHAR(20) NOT NULL,
    doc_id VARCHAR(64) NOT NULL,
    detail_method VARCHAR(64) NOT NULL,
    doc_json JSONB NOT NULL,
    retry_count SMALLINT NOT NULL DEFAULT 0,
    max_retries SMALLINT NOT NULL DEFAULT 10,
    next_retry_at TIMESTAMP NOT NULL DEFAULT NOW(),
    status VARCHAR(10) NOT NULL DEFAULT 'pending',
    last_error TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dead_letter_pending ON erp_sync_dead_letter (next_retry_at) WHERE status = 'pending';
CREATE UNIQUE INDEX IF NOT EXISTS uq_dead_letter_doc ON erp_sync_dead_letter (doc_type, doc_id) WHERE status = 'pending';

-- erp_shops: 店铺列表（API 全量同步）
CREATE TABLE IF NOT EXISTS erp_shops (
    id BIGSERIAL PRIMARY KEY,
    shop_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    short_name VARCHAR(64),
    platform VARCHAR(32),
    nick VARCHAR(128),
    state SMALLINT NOT NULL DEFAULT 3,
    group_name VARCHAR(64),
    deadline VARCHAR(32),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_shops ON erp_shops (shop_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_shops_org ON erp_shops (org_id);
CREATE INDEX IF NOT EXISTS idx_erp_shops_platform ON erp_shops (platform);

-- erp_warehouses: 仓库列表（实体仓 + 虚拟仓，API 全量同步）
CREATE TABLE IF NOT EXISTS erp_warehouses (
    id BIGSERIAL PRIMARY KEY,
    warehouse_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    code VARCHAR(64),
    warehouse_type SMALLINT NOT NULL DEFAULT 0,
    status SMALLINT NOT NULL DEFAULT 1,
    contact VARCHAR(64),
    contact_phone VARCHAR(32),
    province VARCHAR(32),
    city VARCHAR(32),
    district VARCHAR(32),
    address TEXT,
    is_virtual BOOLEAN NOT NULL DEFAULT FALSE,
    external_code VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_warehouses ON erp_warehouses (warehouse_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_warehouses_org ON erp_warehouses (org_id);

-- erp_tags: 标签列表（订单标签 + 商品标签，API 全量同步）
CREATE TABLE IF NOT EXISTS erp_tags (
    id BIGSERIAL PRIMARY KEY,
    tag_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    tag_source VARCHAR(16) NOT NULL DEFAULT 'order',
    tag_type SMALLINT NOT NULL DEFAULT 0,
    color VARCHAR(16),
    remark TEXT,
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_tags ON erp_tags (tag_id, tag_source, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_tags_org ON erp_tags (org_id);

-- erp_categories: 分类列表（自定义分类 + 系统类目，API 全量同步）
CREATE TABLE IF NOT EXISTS erp_categories (
    id BIGSERIAL PRIMARY KEY,
    cat_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    parent_name VARCHAR(128),
    full_name VARCHAR(256),
    cat_source VARCHAR(16) NOT NULL DEFAULT 'seller_cat',
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_categories ON erp_categories (cat_id, cat_source, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_categories_org ON erp_categories (org_id);

-- erp_logistics_companies: 物流公司列表（API 全量同步）
CREATE TABLE IF NOT EXISTS erp_logistics_companies (
    id BIGSERIAL PRIMARY KEY,
    company_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL DEFAULT '',
    code VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_logistics_companies ON erp_logistics_companies (company_id, COALESCE(org_id, '00000000-0000-0000-0000-000000000000'));
CREATE INDEX IF NOT EXISTS idx_erp_logistics_companies_org ON erp_logistics_companies (org_id);

-- erp_order_logs: 订单操作日志（搭便车同步）
CREATE TABLE IF NOT EXISTS erp_order_logs (
    id BIGSERIAL PRIMARY KEY,
    system_id VARCHAR(32) NOT NULL,
    operator VARCHAR(64),
    action VARCHAR(64),
    content TEXT,
    operate_time TIMESTAMP,
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_order_logs_sid ON erp_order_logs (system_id);
CREATE INDEX IF NOT EXISTS idx_erp_order_logs_org ON erp_order_logs (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_order_logs ON erp_order_logs (
    system_id, COALESCE(operate_time, '1970-01-01'), COALESCE(action, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

-- erp_order_packages: 订单包裹/快递信息（搭便车同步）
CREATE TABLE IF NOT EXISTS erp_order_packages (
    id BIGSERIAL PRIMARY KEY,
    system_id VARCHAR(32) NOT NULL,
    package_id VARCHAR(64),
    express_no VARCHAR(64),
    express_company VARCHAR(64),
    express_company_code VARCHAR(32),
    items_json JSONB DEFAULT '[]',
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_order_packages_sid ON erp_order_packages (system_id);
CREATE INDEX IF NOT EXISTS idx_erp_order_packages_express ON erp_order_packages (express_no);
CREATE INDEX IF NOT EXISTS idx_erp_order_packages_org ON erp_order_packages (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_order_packages ON erp_order_packages (
    system_id, COALESCE(express_no, ''), COALESCE(package_id, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

-- erp_aftersale_logs: 售后操作日志（搭便车同步）
CREATE TABLE IF NOT EXISTS erp_aftersale_logs (
    id BIGSERIAL PRIMARY KEY,
    work_order_id VARCHAR(32) NOT NULL,
    operator VARCHAR(64),
    action VARCHAR(64),
    content TEXT,
    operate_time TIMESTAMP,
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_aftersale_logs_wid ON erp_aftersale_logs (work_order_id);
CREATE INDEX IF NOT EXISTS idx_erp_aftersale_logs_org ON erp_aftersale_logs (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_aftersale_logs ON erp_aftersale_logs (
    work_order_id, COALESCE(operate_time, '1970-01-01'), COALESCE(action, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

-- erp_batch_stock: 批次效期库存（遍历店铺全量同步）
CREATE TABLE IF NOT EXISTS erp_batch_stock (
    id BIGSERIAL PRIMARY KEY,
    outer_id VARCHAR(128) NOT NULL,
    sku_outer_id VARCHAR(128) NOT NULL DEFAULT '',
    item_name VARCHAR(256),
    batch_no VARCHAR(64),
    production_date VARCHAR(32),
    expiry_date VARCHAR(32),
    shelf_life_days INTEGER,
    stock_qty INTEGER NOT NULL DEFAULT 0,
    warehouse_name VARCHAR(128),
    shop_id VARCHAR(64),
    extra_json JSONB DEFAULT '{}',
    org_id UUID,
    synced_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_erp_batch_stock_outer ON erp_batch_stock (outer_id);
CREATE INDEX IF NOT EXISTS idx_erp_batch_stock_org ON erp_batch_stock (org_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_erp_batch_stock ON erp_batch_stock (
    outer_id, sku_outer_id, COALESCE(batch_no, ''),
    COALESCE(shop_id, ''), COALESCE(warehouse_name, ''),
    COALESCE(org_id, '00000000-0000-0000-0000-000000000000')
);

-- ============================================================
-- 7. 触发器
-- ============================================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS update_users_updated_at ON users;
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_models_updated_at ON models;
CREATE TRIGGER update_models_updated_at BEFORE UPDATE ON models FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_conversations_updated_at ON conversations;
CREATE TRIGGER update_conversations_updated_at BEFORE UPDATE ON conversations FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- 8. RPC 函数
-- ============================================================

-- 原子扣除积分
CREATE OR REPLACE FUNCTION deduct_credits_atomic(
    p_user_id UUID,
    p_amount INTEGER,
    p_reason TEXT,
    p_change_type TEXT,
    p_org_id UUID DEFAULT NULL
) RETURNS JSONB AS $$
DECLARE
    v_new_balance INTEGER;
BEGIN
    UPDATE users
    SET credits = credits - p_amount, updated_at = NOW()
    WHERE id = p_user_id AND credits >= p_amount
    RETURNING credits INTO v_new_balance;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('success', false, 'message', 'Insufficient credits');
    END IF;

    INSERT INTO credits_history (user_id, change_type, change_amount, balance_after, description, org_id)
    VALUES (p_user_id, p_change_type::credits_change_type, -p_amount, v_new_balance, p_reason, p_org_id);

    RETURN jsonb_build_object('success', true, 'new_balance', v_new_balance);
END;
$$ LANGUAGE plpgsql;

-- 原子退款（CAS检查pending + 退回余额 + 更新状态，单事务防双倍退款）
CREATE OR REPLACE FUNCTION atomic_refund_credits(
    p_transaction_id UUID,
    p_final_status TEXT DEFAULT 'refunded'
) RETURNS JSONB AS $$
DECLARE
    v_user_id UUID;
    v_amount INTEGER;
    v_org_id UUID;
    v_status TEXT;
BEGIN
    -- CAS: 只有 pending 状态才能退款，同时锁行防并发
    UPDATE credit_transactions
    SET status = p_final_status, confirmed_at = NOW()
    WHERE id = p_transaction_id AND status = 'pending'
    RETURNING user_id, amount, org_id INTO v_user_id, v_amount, v_org_id;

    IF v_user_id IS NULL THEN
        SELECT status INTO v_status
        FROM credit_transactions WHERE id = p_transaction_id;
        IF v_status IS NULL THEN
            RETURN jsonb_build_object('refunded', false, 'reason', 'not_found');
        ELSE
            RETURN jsonb_build_object('refunded', false, 'reason', 'status_' || v_status);
        END IF;
    END IF;

    UPDATE users SET credits = credits + v_amount, updated_at = NOW()
    WHERE id = v_user_id;

    INSERT INTO credits_history (user_id, change_amount, balance_after, change_type, description, org_id)
    SELECT v_user_id, v_amount,
           (SELECT credits FROM users WHERE id = v_user_id),
           'refund'::credits_change_type,
           'Refund for transaction ' || p_transaction_id,
           v_org_id;

    RETURN jsonb_build_object('refunded', true, 'user_id', v_user_id, 'amount', v_amount);
END;
$$ LANGUAGE plpgsql;

-- 清理过期积分锁定（使用原子退款函数）
CREATE OR REPLACE FUNCTION cleanup_expired_credit_locks() RETURNS INTEGER AS $$
DECLARE
    v_count INTEGER := 0;
    v_tx RECORD;
    v_result JSONB;
BEGIN
    FOR v_tx IN
        SELECT id FROM credit_transactions
        WHERE status = 'pending' AND expires_at < NOW()
    LOOP
        v_result := atomic_refund_credits(v_tx.id, 'expired');
        IF (v_result->>'refunded')::boolean THEN
            v_count := v_count + 1;
        END IF;
    END LOOP;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- 递增消息计数
CREATE OR REPLACE FUNCTION increment_message_count(
    conv_id UUID,
    p_org_id UUID DEFAULT NULL
)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE conversations
    SET message_count = message_count + 1, updated_at = NOW()
    WHERE id = conv_id
      AND (
          (p_org_id IS NULL AND org_id IS NULL)
          OR org_id = p_org_id
      );
END;
$$;

-- ERP 同步锁（多租户：按 org_id 隔离锁）
CREATE OR REPLACE FUNCTION erp_try_acquire_sync_lock(
    p_lock_ttl_seconds INT DEFAULT 300,
    p_org_id UUID DEFAULT NULL
)
RETURNS BOOLEAN LANGUAGE plpgsql AS $$
DECLARE v_acquired BOOLEAN;
BEGIN
    UPDATE erp_sync_state SET status = 'running', last_run_at = NOW()
    WHERE sync_type = 'purchase'
      AND (status != 'running' OR last_run_at < NOW() - (p_lock_ttl_seconds || ' seconds')::INTERVAL)
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);
    GET DIAGNOSTICS v_acquired = ROW_COUNT;
    RETURN v_acquired > 0;
END;
$$;

-- ERP 聚合（多租户：按 org_id 隔离）
-- ERP 聚合（多租户：DELETE + INSERT，advisory lock 串行化同 key 并发，058 迁移）
CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats(
    p_outer_id VARCHAR, p_stat_date DATE, p_org_id UUID DEFAULT NULL
)
RETURNS VOID LANGUAGE plpgsql AS $$
BEGIN
    -- 事务级 advisory lock：按 (outer_id, stat_date, org_id) 串行化同 key 并发
    -- 详见 backend/migrations/058_fix_aggregation_race.sql
    PERFORM pg_advisory_xact_lock(
        hashtextextended(
            p_outer_id || '|' || p_stat_date::text || '|' || COALESCE(p_org_id::text, ''),
            0
        )
    );

    -- 先删除旧记录（按 org_id 隔离）
    DELETE FROM erp_product_daily_stats
    WHERE stat_date = p_stat_date AND outer_id = p_outer_id
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);

    -- 再插入新聚合数据
    INSERT INTO erp_product_daily_stats (
        stat_date, outer_id, sku_outer_id, item_name,
        org_id,
        purchase_count, purchase_qty, purchase_received_qty, purchase_amount,
        receipt_count, receipt_qty, shelf_count, shelf_qty,
        purchase_return_count, purchase_return_qty, purchase_return_amount,
        aftersale_count, aftersale_refund_count, aftersale_return_count,
        aftersale_exchange_count, aftersale_reissue_count,
        aftersale_reject_count, aftersale_repair_count, aftersale_other_count,
        aftersale_qty, aftersale_amount,
        order_count, order_qty, order_amount,
        order_shipped_count, order_finished_count,
        order_refund_count, order_cancelled_count, order_cost, updated_at
    )
    SELECT
        p_stat_date, p_outer_id, NULL, MAX(item_name), p_org_id,
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(quantity_received) FILTER(WHERE doc_type = 'purchase'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'receipt'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'receipt'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'shelf'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'shelf'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'purchase_return'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'purchase_return'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale'),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type IN (1, 5)),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 2),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 4),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 3),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 7),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type = 9),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'aftersale' AND aftersale_type IN (0, 8)),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'aftersale'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'aftersale'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order'),
        COALESCE(SUM(quantity) FILTER(WHERE doc_type = 'order'), 0),
        COALESCE(SUM(amount) FILTER(WHERE doc_type = 'order'), 0),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND consign_time IS NOT NULL),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND order_status = 'FINISHED'),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND refund_status IS NOT NULL),
        COUNT(DISTINCT doc_id) FILTER(WHERE doc_type = 'order' AND (extra_json->>'isCancel')::int = 1),
        COALESCE(SUM(cost * quantity) FILTER(WHERE doc_type = 'order'), 0),
        NOW()
    FROM erp_document_items
    WHERE outer_id = p_outer_id
      AND doc_created_at >= p_stat_date
      AND doc_created_at < p_stat_date + INTERVAL '1 day'
      AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id);
END;
$$;

-- ERP 批量聚合（多租户：按 org_id 隔离）
CREATE OR REPLACE FUNCTION erp_aggregate_daily_stats_batch(
    p_since_date DATE, p_org_id UUID DEFAULT NULL
)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE v_count INTEGER := 0; v_rec RECORD;
BEGIN
    FOR v_rec IN
        SELECT DISTINCT outer_id, (doc_created_at::DATE)::TEXT AS stat_date
        FROM erp_document_items
        WHERE doc_created_at >= p_since_date AND outer_id IS NOT NULL
          AND ((p_org_id IS NULL AND org_id IS NULL) OR org_id = p_org_id)
    LOOP
        PERFORM erp_aggregate_daily_stats(v_rec.outer_id, v_rec.stat_date::DATE, p_org_id);
        v_count := v_count + 1;
    END LOOP;
    RETURN v_count;
END;
$$;

-- ERP 全局统计（较长，单独保留在 032 迁移中）
-- 注意：erp_global_stats_query 函数较复杂，请单独执行 backend/migrations/032_stock_warehouse_and_global_stats.sql 中的函数部分

-- ============================================================
-- 9. 多租户企业表（039 迁移）
-- ============================================================

-- organizations
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL UNIQUE,
    logo_url VARCHAR(500),
    owner_id UUID NOT NULL REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    max_members INTEGER NOT NULL DEFAULT 50,
    features JSONB NOT NULL DEFAULT '{"erp": false, "image_gen": true, "agent": true}',
    wecom_corp_id VARCHAR(100),
    wecom_agent_id VARCHAR(100),
    wecom_secret_encrypted TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_org_owner ON organizations(owner_id);
CREATE INDEX IF NOT EXISTS idx_org_status ON organizations(status);

CREATE TRIGGER update_organizations_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- org_members
CREATE TABLE IF NOT EXISTS org_members (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'admin', 'member')),
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    permissions JSONB NOT NULL DEFAULT '{}',
    invited_by UUID REFERENCES users(id),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_org_members_user ON org_members(user_id);

-- org_configs
CREATE TABLE IF NOT EXISTS org_configs (
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    config_key VARCHAR(100) NOT NULL,
    config_value_encrypted TEXT NOT NULL,
    updated_by UUID REFERENCES users(id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (org_id, config_key)
);

-- org_invitations
CREATE TABLE IF NOT EXISTS org_invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone VARCHAR(20) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member' CHECK (role IN ('admin', 'member')),
    invite_token VARCHAR(100) UNIQUE NOT NULL,
    invited_by UUID NOT NULL REFERENCES users(id),
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'expired')),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_org_invitations_phone ON org_invitations(phone);
CREATE INDEX IF NOT EXISTS idx_org_invitations_token ON org_invitations(invite_token);
CREATE INDEX IF NOT EXISTS idx_org_invitations_org ON org_invitations(org_id, status);

-- ============================================================
-- 10. 初始化默认模型数据
-- ============================================================

INSERT INTO models (name, provider, model_key, description, type, status, is_default, credits_per_request)
VALUES
  ('Gemini 2.5 Flash Preview', 'Google', 'gemini-2.5-flash-preview', 'Google最新的快速多模态模型', 'multimodal', 'active', TRUE, 5),
  ('Gemini 3 Flash Preview', 'Google', 'gemini-3-flash-preview', 'Google下一代Flash模型预览版', 'multimodal', 'active', TRUE, 8),
  ('GPT-4 Turbo', 'OpenAI', 'gpt-4-turbo', 'OpenAI最强大的文本生成模型', 'text', 'active', FALSE, 15),
  ('Claude 3.5 Sonnet', 'Anthropic', 'claude-3.5-sonnet', 'Anthropic高性能智能助手', 'text', 'active', FALSE, 12),
  ('DALL-E 3', 'OpenAI', 'dall-e-3', 'OpenAI最新的图片生成模型', 'image', 'active', FALSE, 20),
  ('Stable Diffusion XL', 'Stability AI', 'stable-diffusion-xl', '开源图片生成模型', 'image', 'active', FALSE, 15),
  ('Midjourney V6', 'Midjourney', 'midjourney-v6', '艺术级图片生成模型', 'image', 'coming_soon', FALSE, 25)
ON CONFLICT (model_key) DO NOTHING;

-- ============================================================
-- 完成！
-- ============================================================
