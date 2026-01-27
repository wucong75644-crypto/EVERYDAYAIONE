# 阿里云 CDN 配置指南

> 目标：为 AI 生成的图片配置 CDN 加速，解决 CORS 跨域问题

## 一、前置准备

### 1.1 你需要准备
- [x] 已备案域名：`everydayai.com.cn`
- [ ] 阿里云账号（已实名认证）
- [ ] 开通 OSS 服务
- [ ] 开通 CDN 服务

### 1.2 规划的域名
| 用途 | 域名 | 说明 |
|------|------|------|
| 网站主域名 | everydayai.com.cn | 已备案 |
| CDN 加速域名 | cdn.everydayai.com.cn | 本次配置 |
| API 域名 | api.everydayai.com.cn | 可选 |

---

## 二、创建 OSS Bucket

### 步骤 2.1：进入 OSS 控制台
1. 登录阿里云控制台：https://console.aliyun.com
2. 搜索「对象存储 OSS」，点击进入
3. 如果没开通，点击「立即开通」

### 步骤 2.2：创建 Bucket
1. 点击「创建 Bucket」
2. 填写配置：

| 配置项 | 推荐值 | 说明 |
|--------|--------|------|
| Bucket 名称 | `everydayai-images` | 全局唯一，建议用项目名 |
| 地域 | `华东1（杭州）` | 选离用户近的，备案在浙江选杭州 |
| 存储类型 | `标准存储` | 频繁访问选标准 |
| 读写权限 | `公共读` | ⚠️ 重要：必须选公共读，CDN 才能访问 |
| 版本控制 | `不开通` | 图片不需要版本控制 |
| 服务端加密 | `无` | 公开图片不需要加密 |

3. 点击「确定」创建

### 步骤 2.3：配置跨域规则（CORS）
1. 进入刚创建的 Bucket
2. 左侧菜单 → 「数据安全」→「跨域设置」
3. 点击「创建规则」
4. 填写：

| 配置项 | 值 |
|--------|-----|
| 来源 | `*` |
| 允许 Methods | 勾选 `GET`, `POST`, `PUT`, `DELETE`, `HEAD` |
| 允许 Headers | `*` |
| 暴露 Headers | `ETag`, `Content-Length`, `x-oss-request-id` |
| 缓存时间 | `3600` |

> **说明**：来源设为 `*` 是因为开发环境（localhost）也需要访问。生产环境如需严格限制，可改为 `https://everydayai.com.cn,http://localhost:*`

5. 点击「确定」

### 步骤 2.4：记录 OSS 信息
创建完成后，记录以下信息（后面要用）：

```
Bucket 名称：everydayai-images
地域：oss-cn-hangzhou
外网访问域名：everydayai-images.oss-cn-hangzhou.aliyuncs.com
```

---

## 三、配置 CDN 加速

### 步骤 3.1：进入 CDN 控制台
1. 阿里云控制台搜索「CDN」
2. 如果没开通，点击「立即开通」

### 步骤 3.2：添加加速域名
1. 左侧菜单 →「域名管理」
2. 点击「添加域名」
3. 填写配置：

**基本信息**
| 配置项 | 值 | 说明 |
|--------|-----|------|
| 加速域名 | `cdn.everydayai.com.cn` | 你要用的 CDN 域名 |
| 业务类型 | `图片小文件` | 选这个，针对图片优化 |
| 加速区域 | `仅中国内地` | 已备案只能选这个 |

**源站信息**
| 配置项 | 值 |
|--------|-----|
| 源站类型 | `OSS域名` |
| 域名 | `everydayai-images.oss-cn-hangzhou.aliyuncs.com` |
| 端口 | `443` |

4. 点击「下一步」→「确定」

### 步骤 3.3：等待审核
- 添加后状态是「配置中」
- 等待 5-10 分钟变成「正常运行」
- 此时会显示一个 CNAME 值，类似：`cdn.everydayai.com.cn.w.cdngslb.com`

**记录这个 CNAME 值！**

---

## 四、配置 DNS 解析

### 步骤 4.1：进入云解析 DNS
1. 阿里云控制台搜索「云解析 DNS」
2. 找到 `everydayai.com.cn` 域名，点击「解析设置」

### 步骤 4.2：添加 CNAME 记录
点击「添加记录」，填写：

| 配置项 | 值 |
|--------|-----|
| 记录类型 | `CNAME` |
| 主机记录 | `cdn` |
| 记录值 | `cdn.everydayai.com.cn.w.cdngslb.com`（你的 CNAME 值） |
| TTL | `10分钟` |

点击「确认」

### 步骤 4.3：验证解析
等待 5 分钟后，打开终端验证：

```bash
# 检查 CNAME 是否生效
dig cdn.everydayai.com.cn

# 或者用 nslookup
nslookup cdn.everydayai.com.cn
```

看到返回的是 `*.cdngslb.com` 结尾的地址就成功了。

---

## 五、配置 HTTPS（重要）

### 步骤 5.1：申请免费 SSL 证书
1. 阿里云控制台搜索「数字证书管理服务」
2. 左侧「SSL 证书」→「免费证书」
3. 点击「创建证书」
4. 填写：
   - 证书绑定域名：`cdn.everydayai.com.cn`
   - 其他默认
5. 点击「提交审核」
6. 按提示完成 DNS 验证（添加一条 TXT 记录）
7. 等待签发（通常几分钟）

### 步骤 5.2：在 CDN 配置 HTTPS
1. 回到 CDN 控制台 →「域名管理」
2. 点击 `cdn.everydayai.com.cn` 进入配置
3. 左侧「HTTPS配置」
4. 点击「修改配置」
5. 配置：

| 配置项 | 值 |
|--------|-----|
| HTTPS 安全加速 | `开启` |
| 证书来源 | `云盾证书` |
| 证书名称 | 选择刚申请的证书 |
| HTTP/2 | `开启` |
| 强制跳转 | `HTTP -> HTTPS` |

6. 点击「确定」

---

## 六、CDN 优化配置（推荐）

### 6.1 缓存配置
CDN 控制台 → 域名管理 → 选择域名 → 缓存配置

添加规则：
| 类型 | 地址 | 过期时间 |
|------|------|----------|
| 文件后缀 | `jpg,jpeg,png,gif,webp` | 30 天 |

### 6.2 性能优化
CDN 控制台 → 域名管理 → 选择域名 → 性能优化

开启：
- [x] 智能压缩（Gzip/Brotli）
- [x] 页面优化（去除注释空格）

### 6.3 访问控制（可选）
如果担心盗链，可以配置 Referer 防盗链：
- 白名单：`everydayai.com.cn`
- 允许空 Referer：是（允许直接访问）

---

## 七、验证测试

### 7.1 测试 CDN 访问
先手动上传一张测试图片到 OSS：
1. OSS 控制台 → 进入 Bucket
2. 点击「上传文件」，上传一张图片（如 `test.png`）

然后测试访问：
```bash
# 测试 CDN 地址
curl -I https://cdn.everydayai.com.cn/test.png

# 应该返回 200，且有 X-Cache: HIT 表示命中缓存
```

### 7.2 测试 CORS
```bash
curl -I -H "Origin: https://everydayai.com.cn" https://cdn.everydayai.com.cn/test.png

# 应该看到：
# Access-Control-Allow-Origin: https://everydayai.com.cn
```

---

## 八、费用说明

### OSS 费用
- 存储：约 0.12 元/GB/月（标准存储）
- 流量：通过 CDN 回源，费用较低

### CDN 费用
- 流量计费：约 0.24 元/GB（按量付费）
- 建议：月流量 >500GB 时购买流量包更划算

### 预估月费用
| 场景 | 存储 | CDN流量 | 月费用 |
|------|------|---------|--------|
| 初期（1GB 存储，10GB 流量） | 0.12 元 | 2.4 元 | ~3 元 |
| 中期（10GB 存储，100GB 流量） | 1.2 元 | 24 元 | ~25 元 |

---

## 九、配置完成后

把以下信息告诉我，我来更新后端配置：

```
OSS Bucket 名称：
OSS 地域（如 oss-cn-hangzhou）：
CDN 域名：cdn.everydayai.com.cn
OSS AccessKey ID：（在 RAM 控制台创建）
OSS AccessKey Secret：（在 RAM 控制台创建）
```

**AccessKey 创建方法**：
1. 阿里云控制台 → 右上角头像 →「AccessKey 管理」
2. 推荐「使用子用户 AccessKey」（更安全）
3. 创建用户，授权「AliyunOSSFullAccess」权限
4. 创建 AccessKey 并保存

---

## 常见问题

### Q1：CDN 域名访问返回 403
- 检查 OSS Bucket 是否设为「公共读」
- 检查 CDN 源站域名是否正确

### Q2：HTTPS 证书申请失败
- 检查 DNS 验证记录是否添加正确
- 等待 DNS 生效（最多 10 分钟）

### Q3：图片加载慢
- 检查是否开启了智能压缩
- 检查缓存时间配置

### Q4：CORS 错误
- 检查 OSS 跨域规则
- CDN 配置中开启「回源跟随」

---

有任何步骤不清楚，截图给我，我帮你看。
