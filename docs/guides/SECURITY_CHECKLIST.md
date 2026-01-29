# 🔒 安全检查清单

**最后更新**: 2026-01-28

## 部署前必检项目

### 密钥管理
- [ ] 所有密钥已从 `.env` 文件轮换
- [ ] `.env` 文件在 `.gitignore` 中
- [ ] 生产密钥存储在环境变量中
- [ ] GitHub Token 不在 `.env` 中（使用 `gh auth login`）
- [ ] 检查 git 历史无密钥泄露: `git log --all --full-history -- "*.env"`

### 输入验证
- [ ] 所有用户输入使用 Pydantic schemas 验证
- [ ] `generation_params` 已添加验证和大小限制
- [ ] 文件上传限制大小和类型
- [ ] URL 验证使用白名单

### SQL 注入防护
- [ ] 所有数据库查询使用参数化查询
- [ ] 无字符串拼接 SQL
- [ ] Supabase ORM 正确使用

### 认证授权
- [ ] JWT Token 验证在所有受保护端点
- [ ] 所有操作验证用户所有权
- [ ] 密码使用 bcrypt 哈希
- [ ] Token 存储在 httpOnly cookies（前端）

### XSS 防护
- [ ] 无 `dangerouslySetInnerHTML` 使用
- [ ] 用户内容经过 sanitize
- [ ] CSP 头已配置

### 限流保护
- [ ] 所有 API 端点配置限流
- [ ] `create_message` 已添加限流: 60/分钟
- [ ] 图片生成: 10/分钟
- [ ] 视频生成: 5/分钟

### CORS 配置
- [ ] 开发环境仅允许 localhost
- [ ] 生产环境仅允许特定域名
- [ ] 禁用 `allow_origins=["*"]`

### 安全响应头
- [ ] X-Frame-Options: DENY
- [ ] X-Content-Type-Options: nosniff
- [ ] X-XSS-Protection: 1; mode=block
- [ ] Strict-Transport-Security (生产)
- [ ] Content-Security-Policy
- [ ] Referrer-Policy
- [ ] Permissions-Policy

### 依赖安全
- [ ] 前端: `npm audit` 无漏洞
- [ ] 后端: `pip check` 无冲突
- [ ] 定期更新依赖
- [ ] `package-lock.json` 已提交

### 数据库安全
- [ ] Row Level Security (RLS) 已启用
- [ ] `generation_params` 大小约束已添加
- [ ] 敏感字段加密存储

### 日志安全
- [ ] 无密码、Token、密钥记录
- [ ] 错误消息不暴露内部信息
- [ ] 生产环境禁用详细日志

## 定期检查（每月）

- [ ] 审查 Supabase 访问日志
- [ ] 检查异常登录活动
- [ ] 更新依赖版本
- [ ] 运行安全扫描工具
- [ ] 审查 API 限流阈值

## 应急响应

如发现安全问题：

1. **立即**暂停受影响的服务
2. 评估影响范围
3. 轮换相关密钥
4. 修复漏洞
5. 通知受影响用户
6. 记录事件并复盘

## 联系人

**安全负责人**: [填写]
**紧急联系**: [填写]
**Supabase 支持**: https://supabase.com/support

---

**版本**: 1.0
**下次审查日期**: 2026-02-28
