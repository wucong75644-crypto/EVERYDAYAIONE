# Everything-Claude-Code 技能详细说明文档

> 完整版 - 所有技能和代理的详细功能说明

---

## 目录

- [一、开发规范与模式类](#一开发规范与模式类)
- [二、测试与质量保证类](#二测试与质量保证类)
- [三、代码审查与安全类](#三代码审查与安全类)
- [四、数据库类](#四数据库类)
- [五、构建与错误修复类](#五构建与错误修复类)
- [六、架构与规划类](#六架构与规划类)
- [七、文档类](#七文档类)
- [八、智能学习类](#八智能学习类)
- [九、其他工具类](#九其他工具类)

---

## 一、开发规范与模式类

### 1. coding-standards (通用编码标准)

**简介**：TypeScript/JavaScript/React/Node.js 的通用编码标准和最佳实践。

**核心功能**：
- ✅ **代码质量原则**：KISS、DRY、YAGNI
- ✅ **命名规范**：变量用 camelCase，组件用 PascalCase
- ✅ **不可变性模式**：强制使用 spread operator，禁止直接修改对象/数组
- ✅ **错误处理**：所有异步函数必须 try-catch
- ✅ **类型安全**：禁止使用 `any`，必须明确类型定义
- ✅ **React 最佳实践**：函数组件、自定义 hooks、状态管理
- ✅ **API 设计**：RESTful 规范、统一响应格式、Zod 验证

**实际例子**：

```typescript
// ❌ 我不会写成这样
const data: any = await fetch(url)
user.name = "New Name"  // 直接修改

// ✅ 我会自动写成这样
interface User {
  id: string
  name: string
}

const updatedUser = {
  ...user,
  name: "New Name"
}

try {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data: User = await response.json()
} catch (error) {
  console.error('Fetch failed:', error)
  throw new Error('Failed to fetch data')
}
```

**何时生效**：
- ✅ 你让我写任何代码时自动应用
- ✅ 我会主动遵循这些标准，无需你明确要求

**输出效果**：
- 代码干净、可读、可维护
- 没有直接修改对象（immutability）
- 完善的错误处理
- 类型安全

---

### 2. backend-patterns (后端架构模式)

**简介**：Node.js、Express、Next.js API routes 的后端架构模式。

**核心功能**：
- ✅ **API 设计模式**：RESTful 架构、GraphQL 集成
- ✅ **数据库优化**：连接池、查询优化、索引设计
- ✅ **认证授权**：JWT、OAuth、会话管理
- ✅ **缓存策略**：Redis 缓存、CDN 配置
- ✅ **错误处理**：统一错误中间件、日志记录
- ✅ **性能优化**：并发控制、限流、负载均衡

**实际例子**：

```typescript
// ✅ 标准 API 响应格式
interface ApiResponse<T> {
  success: boolean
  data?: T
  error?: string
  meta?: {
    total: number
    page: number
    limit: number
  }
}

// ✅ Next.js API Route 标准实现
export async function GET(request: Request) {
  try {
    const { searchParams } = new URL(request.url)
    const page = parseInt(searchParams.get('page') || '1')
    const limit = parseInt(searchParams.get('limit') || '10')

    // 数据库查询（带分页）
    const { data, error } = await supabase
      .from('markets')
      .select('*', { count: 'exact' })
      .range((page - 1) * limit, page * limit - 1)

    if (error) throw error

    return NextResponse.json({
      success: true,
      data,
      meta: { total: data.length, page, limit }
    })
  } catch (error) {
    return NextResponse.json({
      success: false,
      error: error.message
    }, { status: 500 })
  }
}
```

**何时生效**：
- 你让我创建 API 端点时
- 设计后端架构时
- 优化数据库查询时

---

### 3. frontend-patterns (前端开发模式)

**简介**：React、Next.js、状态管理、性能优化的前端模式。

**核心功能**：
- ✅ **React 模式**：自定义 hooks、组件组合、渲染优化
- ✅ **状态管理**：Context API、Zustand、React Query
- ✅ **性能优化**：懒加载、代码分割、memoization
- ✅ **UI 模式**：条件渲染、列表渲染、表单处理
- ✅ **CSS 模式**：CSS Modules、Tailwind、BEM

**实际例子**：

```typescript
// ✅ 自定义 Hook
export function useDebounce<T>(value: T, delay: number): T {
  const [debouncedValue, setDebouncedValue] = useState<T>(value)

  useEffect(() => {
    const handler = setTimeout(() => setDebouncedValue(value), delay)
    return () => clearTimeout(handler)  // 清理副作用
  }, [value, delay])

  return debouncedValue
}

// ✅ 性能优化：懒加载
const HeavyChart = lazy(() => import('./HeavyChart'))

export function Dashboard() {
  return (
    <Suspense fallback={<Spinner />}>
      <HeavyChart />
    </Suspense>
  )
}

// ✅ 防止不必要的渲染
const sortedMarkets = useMemo(() => {
  return markets.sort((a, b) => b.volume - a.volume)
}, [markets])
```

**何时生效**：
- 创建 React 组件时
- 优化前端性能时
- 实现状态管理时

---

### 4. golang-patterns (Go 语言模式)

**简介**：Go 语言的惯用模式、最佳实践和约定。

**核心功能**：
- ✅ **惯用 Go 风格**：简洁、明确、高效
- ✅ **错误处理**：显式错误返回、错误包装
- ✅ **并发模式**：goroutines、channels、select
- ✅ **接口设计**：小接口、组合优于继承
- ✅ **内存管理**：指针使用、零分配优化

**实际例子**：

```go
// ✅ 惯用的错误处理
func FetchUser(id string) (*User, error) {
    user, err := db.Query("SELECT * FROM users WHERE id = ?", id)
    if err != nil {
        return nil, fmt.Errorf("failed to fetch user %s: %w", id, err)
    }
    return user, nil
}

// ✅ 并发模式：使用 context 和 goroutines
func ProcessBatch(ctx context.Context, items []Item) error {
    errCh := make(chan error, len(items))

    for _, item := range items {
        go func(i Item) {
            select {
            case <-ctx.Done():
                errCh <- ctx.Err()
            default:
                errCh <- processItem(i)
            }
        }(item)
    }

    // 等待所有任务完成
    for range items {
        if err := <-errCh; err != nil {
            return err
        }
    }
    return nil
}
```

**何时生效**：
- 写 Go 代码时自动应用

---

## 二、测试与质量保证类

### 5. tdd-workflow (测试驱动开发工作流)

**简介**：强制执行测试驱动开发（TDD），确保 80%+ 测试覆盖率。

**核心功能**：
- ✅ **TDD 流程**：先写测试 → 运行失败 → 实现代码 → 测试通过 → 重构
- ✅ **三种测试**：单元测试、集成测试、E2E 测试
- ✅ **覆盖率要求**：最低 80%，包含边界条件和错误场景
- ✅ **测试模式**：AAA 模式（Arrange-Act-Assert）
- ✅ **Mock 策略**：外部依赖 mock（Supabase、Redis、OpenAI）

**TDD 工作流程**：

```
1. 写测试（RED）  ──→  2. 运行测试（失败）
                            ↓
6. 验证覆盖率  ←──  5. 重构  ←──  4. 运行测试（通过）
                                        ↑
                            3. 实现最小代码
```

**实际例子**：

```typescript
// ✅ 第1步：先写测试
describe('searchMarkets', () => {
  it('returns relevant markets for query', async () => {
    // Arrange
    const query = 'election'

    // Act
    const results = await searchMarkets(query)

    // Assert
    expect(results).toHaveLength(5)
    expect(results[0].name).toContain('election')
  })

  it('handles empty query gracefully', async () => {
    const results = await searchMarkets('')
    expect(results).toEqual([])
  })

  it('falls back to substring search when Redis unavailable', async () => {
    // Mock Redis failure
    jest.mock('@/lib/redis', () => ({
      checkRedisHealth: jest.fn(() => Promise.resolve({ connected: false }))
    }))

    const results = await searchMarkets('test')
    expect(results.length).toBeGreaterThan(0)
  })
})

// ✅ 第2步：运行测试 → 失败（因为还没实现）
// ✅ 第3步：实现代码
export async function searchMarkets(query: string) {
  if (!query) return []

  try {
    // 尝试 Redis 语义搜索
    const redisHealth = await checkRedisHealth()
    if (redisHealth.connected) {
      return await searchByVector(query)
    }
  } catch (error) {
    console.error('Redis search failed:', error)
  }

  // 降级到子串搜索
  return await substringSearch(query)
}

// ✅ 第4步：运行测试 → 通过
// ✅ 第5步：重构代码（保持测试通过）
// ✅ 第6步：验证覆盖率
// npm run test:coverage → 85% ✓
```

**何时生效**：
- 你说"写新功能"、"修bug"、"重构代码"时
- 我会自动先写测试，再实现代码

**输出效果**：
- 测试文件先于实现文件创建
- 每个功能都有完整测试覆盖
- 测试覆盖率报告 ≥80%

---

### 6. golang-testing (Go 测试模式)

**简介**：Go 语言的测试模式，包括表驱动测试、子测试、基准测试。

**核心功能**：
- ✅ **表驱动测试**：一次测试多个用例
- ✅ **子测试**：t.Run() 组织测试
- ✅ **基准测试**：性能测试
- ✅ **模糊测试**：自动生成测试输入
- ✅ **测试覆盖率**：go test -cover

**实际例子**：

```go
// ✅ 表驱动测试
func TestCalculateSimilarity(t *testing.T) {
    tests := []struct {
        name     string
        vector1  []float64
        vector2  []float64
        expected float64
    }{
        {"identical vectors", []float64{1, 0, 0}, []float64{1, 0, 0}, 1.0},
        {"orthogonal vectors", []float64{1, 0, 0}, []float64{0, 1, 0}, 0.0},
        {"opposite vectors", []float64{1, 0, 0}, []float64{-1, 0, 0}, -1.0},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := calculateCosineSimilarity(tt.vector1, tt.vector2)
            if math.Abs(got-tt.expected) > 0.001 {
                t.Errorf("got %v, want %v", got, tt.expected)
            }
        })
    }
}

// ✅ 基准测试
func BenchmarkVectorSearch(b *testing.B) {
    vectors := generateTestVectors(10000)
    query := []float64{0.5, 0.3, 0.2}

    b.ResetTimer()
    for i := 0; i < b.N; i++ {
        _ = searchSimilarVectors(query, vectors)
    }
}
```

**何时生效**：
- 写 Go 项目的测试时

---

### 7. tdd-guide (Agent - TDD 指导专家)

**简介**：专门的 TDD 指导代理，强制执行"先写测试再实现"的工作流。

**核心功能**：
- ✅ **强制 TDD 流程**：绝不允许先写实现
- ✅ **测试生成**：根据需求生成完整测试用例
- ✅ **覆盖率验证**：确保 80%+ 覆盖率
- ✅ **Mock 指导**：教你如何 mock 外部依赖

**何时调用**：
- 你明确说"用 TDD 方式开发"
- 需要严格执行测试驱动时

**与 tdd-workflow 的区别**：
- `tdd-workflow` (skill) = 我自动遵循的原则
- `tdd-guide` (agent) = 你明确调用的专家，更严格、更详细的指导

---

### 8. e2e-runner (Agent - E2E 测试专家)

**简介**：端到端测试专家，使用 Playwright 或 Vercel Agent Browser。

**核心功能**：
- ✅ **测试生成**：根据用户流程生成 E2E 测试
- ✅ **测试运行**：执行测试并捕获结果
- ✅ **Artifact 管理**：截图、视频、trace 文件
- ✅ **Flaky 测试处理**：隔离不稳定的测试

**实际例子**：

```typescript
// ✅ E2E 测试示例
import { test, expect } from '@playwright/test'

test('user can search and filter markets', async ({ page }) => {
  // 导航到市场页面
  await page.goto('/')
  await page.click('a[href="/markets"]')

  // 验证页面加载
  await expect(page.locator('h1')).toContainText('Markets')

  // 搜索市场
  await page.fill('input[placeholder="Search markets"]', 'election')
  await page.waitForTimeout(600)  // 等待防抖

  // 验证搜索结果
  const results = page.locator('[data-testid="market-card"]')
  await expect(results).toHaveCount(5, { timeout: 5000 })

  // 验证结果包含搜索词
  await expect(results.first()).toContainText('election', { ignoreCase: true })
})
```

**何时调用**：
- 你说"写 E2E 测试"
- 需要测试关键用户流程时

---

## 三、代码审查与安全类

### 9. code-reviewer (Agent - 代码审查专家)

**简介**：高级代码审查专家，写完代码后立即自动审查。

**核心功能**：
- ✅ **代码质量检查**：函数长度、文件长度、嵌套深度
- ✅ **安全检查**：硬编码密钥、SQL 注入、XSS
- ✅ **性能检查**：算法复杂度、不必要的重渲染
- ✅ **最佳实践**：命名规范、错误处理、测试覆盖

**审查清单**：

```markdown
## 安全检查（CRITICAL）
- [ ] 无硬编码凭证（API keys、密码、token）
- [ ] 无 SQL 注入风险（参数化查询）
- [ ] 无 XSS 漏洞（转义用户输入）
- [ ] 输入验证完整
- [ ] 依赖项无漏洞

## 代码质量（HIGH）
- [ ] 函数 <50 行
- [ ] 文件 <800 行
- [ ] 嵌套 <4 层
- [ ] 完善的错误处理
- [ ] 无 console.log
- [ ] 无直接修改（immutability）
- [ ] 新代码有测试

## 性能（MEDIUM）
- [ ] 算法效率（避免 O(n²)）
- [ ] React 渲染优化（useMemo、useCallback）
- [ ] 无 N+1 查询
- [ ] 图片优化
```

**审查报告格式**：

```markdown
## Code Review Report

### Critical Issues (Must Fix) 🔴
**[CRITICAL] Hardcoded API key**
File: src/api/client.ts:42
Issue: API key exposed in source code
Fix: Move to environment variable

const apiKey = "sk-abc123";  // ❌ Bad
const apiKey = process.env.API_KEY;  // ✓ Good

### Warnings (Should Fix) 🟡
**[HIGH] Large function**
File: src/utils/process.ts:156
Issue: Function is 85 lines, exceeds 50-line limit
Fix: Extract into smaller functions

### Suggestions (Consider) 🟢
**[MEDIUM] Missing JSDoc**
File: src/lib/search.ts:23
Consider: Add JSDoc for public API
```

**何时生效**：
- ✅ **我写完代码后会自动调用**
- 你明确说"审查代码"时

**输出效果**：
- 详细的审查报告
- 按优先级分类的问题列表
- 具体修复建议和代码示例

---

### 10. security-reviewer (Agent - 安全审查专家)

**简介**：安全漏洞检测和修复专家，专注 OWASP Top 10。

**核心功能**：
- ✅ **OWASP Top 10 检查**：注入、认证、XSS、CSRF 等
- ✅ **秘密检测**：扫描硬编码的 API key、密码
- ✅ **依赖扫描**：npm audit、已知 CVE
- ✅ **认证授权**：验证访问控制
- ✅ **金融安全**（针对支付系统）：原子事务、竞态条件

**安全检查项**：

```markdown
## 1. 注入攻击（CRITICAL）

### SQL 注入
❌ const query = `SELECT * FROM users WHERE id = ${userId}`
✅ const { data } = await supabase.from('users').select('*').eq('id', userId)

### 命令注入
❌ exec(`ping ${userInput}`)
✅ dns.lookup(userInput)  // 使用库而非 shell

## 2. 认证问题（CRITICAL）

### 明文密码
❌ if (password === storedPassword) { }
✅ const isValid = await bcrypt.compare(password, hashedPassword)

### JWT 验证
❌ const payload = jwt.decode(token)  // 不验证签名
✅ const payload = jwt.verify(token, SECRET_KEY)

## 3. XSS 攻击（HIGH）

❌ element.innerHTML = userInput
✅ element.textContent = userInput
✅ element.innerHTML = DOMPurify.sanitize(userInput)

## 4. SSRF 攻击（HIGH）

❌ const response = await fetch(userProvidedUrl)
✅ const allowedDomains = ['api.example.com']
   if (!allowedDomains.includes(url.hostname)) throw new Error()

## 5. 金融安全（CRITICAL - 针对支付平台）

### 竞态条件
❌ const balance = await getBalance(userId)
   if (balance >= amount) {
     await withdraw(userId, amount)  // 另一个请求可能并发执行！
   }

✅ await db.transaction(async (trx) => {
     const balance = await trx('balances')
       .where({ user_id: userId })
       .forUpdate()  // 锁定行
       .first()

     if (balance.amount < amount) throw new Error()
     await trx('balances').decrement('amount', amount)
   })
```

**何时生效**：
- ✅ **写认证、输入处理、API、支付相关代码后自动调用**
- 你说"安全审查"时

**输出效果**：
- 详细的安全漏洞报告
- 漏洞利用示例（POC）
- 安全修复代码

---

### 11. security-review (Skill - 安全检查清单)

**简介**：安全检查清单和模式库。

**与 security-reviewer 的区别**：
- `security-review` (skill) = 安全知识库，我自动遵循
- `security-reviewer` (agent) = 主动扫描和检测，生成报告

---

### 12. refactor-cleaner (Agent - 死代码清理专家)

**简介**：识别并移除死代码、重复代码、未使用的导出。

**核心功能**：
- ✅ **死代码检测**：运行 knip、depcheck、ts-prune
- ✅ **重复代码检测**：识别可提取的重复逻辑
- ✅ **未使用导出**：移除未引用的函数和变量
- ✅ **依赖清理**：移除未使用的 npm 包

**使用工具**：

```bash
# 死代码检测工具
npx knip                 # 检测未使用的文件和导出
npx depcheck            # 检测未使用的依赖
npx ts-prune            # 检测未使用的 TypeScript 导出

# 重复代码检测
npx jscpd src/         # 检测重复代码
```

**何时调用**：
- 你说"清理死代码"、"重构代码"时
- 代码库变大需要清理时

---

## 四、数据库类

### 13. postgres-patterns (Skill - PostgreSQL 最佳实践)

**简介**：PostgreSQL 的查询优化、schema 设计、索引、安全。基于 Supabase 最佳实践。

**核心功能**：
- ✅ **查询优化**：避免 N+1、使用 EXPLAIN ANALYZE
- ✅ **索引设计**：复合索引、部分索引、函数索引
- ✅ **Schema 设计**：规范化、外键、约束
- ✅ **RLS（行级安全）**：Supabase 必须配置 RLS
- ✅ **性能监控**：慢查询日志、连接池

**实际例子**：

```sql
-- ❌ N+1 查询问题
SELECT * FROM markets;
-- 然后对每个 market 执行：
SELECT * FROM trades WHERE market_id = ?;

-- ✅ 使用 JOIN 一次查询
SELECT m.*, t.*
FROM markets m
LEFT JOIN trades t ON m.id = t.market_id;

-- ✅ 创建索引加速查询
CREATE INDEX idx_trades_market_id ON trades(market_id);
CREATE INDEX idx_markets_status_created ON markets(status, created_at DESC);

-- ✅ 启用 RLS（Supabase 必须）
ALTER TABLE markets ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view active markets"
ON markets FOR SELECT
USING (status = 'active' OR auth.uid() = creator_id);
```

**何时生效**：
- 写 SQL 查询时
- 设计数据库 schema 时
- 创建迁移文件时

---

### 14. clickhouse-io (Skill - ClickHouse 分析模式)

**简介**：ClickHouse 分析型数据库的查询优化、数据工程最佳实践。

**核心功能**：
- ✅ **列式存储优化**：只查询需要的列
- ✅ **分布式查询**：跨节点聚合
- ✅ **物化视图**：预计算聚合结果
- ✅ **大数据处理**：处理 TB 级数据

**何时生效**：
- 处理大数据分析时
- 使用 ClickHouse 数据库时

---

### 15. database-reviewer (Agent - 数据库审查专家)

**简介**：数据库代码审查专家，确保高性能和安全。

**核心功能**：
- ✅ **查询性能审查**：检测慢查询、缺失索引
- ✅ **Schema 审查**：表设计、约束、外键
- ✅ **安全审查**：RLS 配置、SQL 注入风险
- ✅ **迁移审查**：检查破坏性变更

**审查项**：

```markdown
## 性能问题
- [ ] 无 SELECT *（只查询需要的列）
- [ ] JOIN 使用正确的索引
- [ ] 无 N+1 查询
- [ ] 大表有分页

## 安全问题
- [ ] RLS 已启用（Supabase）
- [ ] 无 SQL 注入（参数化查询）
- [ ] 敏感字段加密

## Schema 设计
- [ ] 外键约束正确
- [ ] 索引覆盖查询
- [ ] 数据类型合适
```

**何时生效**：
- ✅ **写 SQL、迁移、schema 时自动调用**

---

## 五、构建与错误修复类

### 16. build-error-resolver (Agent - 构建错误修复专家)

**简介**：快速修复 TypeScript 和构建错误，最小化改动。

**核心功能**：
- ✅ **TypeScript 错误修复**：类型错误、导入错误
- ✅ **构建错误修复**：Webpack、Vite、Next.js 构建问题
- ✅ **最小化改动**：只修复错误，不做架构改动
- ✅ **增量修复**：一次修复一个错误

**工作流程**：

```
1. 运行构建 → 收集错误
2. 按优先级排序错误（阻塞性 > 警告）
3. 修复第一个错误
4. 再次运行构建
5. 重复直到构建成功
```

**何时调用**：
- ✅ **构建失败时自动调用**
- 出现 TypeScript 错误时

**输出效果**：
- 快速修复错误
- 构建变绿✅

---

### 17. go-build-resolver (Agent - Go 构建错误修复专家)

**简介**：修复 Go 构建错误、go vet 警告、linter 问题。

**核心功能**：
- ✅ **编译错误修复**：类型错误、未定义引用
- ✅ **go vet 问题**：常见错误模式
- ✅ **Linter 警告**：golangci-lint 问题
- ✅ **最小改动**：外科手术式修复

**何时调用**：
- ✅ **Go 项目构建失败时自动调用**

---

### 18. go-reviewer (Agent - Go 代码审查专家)

**简介**：Go 代码审查专家，专注惯用模式、并发安全、错误处理。

**核心功能**：
- ✅ **惯用 Go 审查**：是否符合 Go 习惯
- ✅ **并发安全**：goroutine 泄漏、竞态条件
- ✅ **错误处理**：错误包装、错误检查
- ✅ **性能审查**：内存分配、算法效率

**何时调用**：
- ✅ **写 Go 代码后自动调用**

---

## 六、架构与规划类

### 19. architect (Agent - 软件架构专家)

**简介**：系统设计、可扩展性、技术决策专家。

**核心功能**：
- ✅ **架构设计**：微服务、单体、Serverless 选型
- ✅ **可扩展性**：水平扩展、垂直扩展、缓存策略
- ✅ **技术选型**：数据库、框架、基础设施
- ✅ **权衡分析**：性能 vs 成本 vs 复杂度

**何时调用**：
- ✅ **规划新功能或架构决策时自动调用**
- 重大重构前

---

### 20. planner (Agent - 实现计划专家)

**简介**：为复杂功能创建详细、可执行的实现计划。

**核心功能**：
- ✅ **需求分析**：理解功能需求
- ✅ **架构审查**：分析现有代码结构
- ✅ **步骤分解**：详细的实现步骤（带文件路径）
- ✅ **风险识别**：潜在问题和缓解策略

**计划格式**：

```markdown
# Implementation Plan: 语义搜索功能

## Overview
为市场平台添加基于 OpenAI embeddings 和 Redis 向量搜索的语义搜索。

## Requirements
- 用户输入自然语言查询
- 返回相关市场（按相似度排序）
- 降级到子串搜索（Redis 不可用时）

## Architecture Changes
- lib/openai.ts: 生成 embeddings
- lib/redis.ts: 向量搜索
- app/api/search/route.ts: 搜索 API
- components/SearchBar.tsx: 搜索 UI

## Implementation Steps

### Phase 1: Backend Setup (2-3 hours)

1. **设置 OpenAI 集成** (File: lib/openai.ts)
   - Action: 创建 generateEmbedding 函数
   - Why: 将文本转为向量
   - Dependencies: None
   - Risk: Low

2. **设置 Redis 向量搜索** (File: lib/redis.ts)
   - Action: 创建 searchByVector 函数
   - Why: 在 Redis 中搜索相似向量
   - Dependencies: Step 1（需要 embeddings）
   - Risk: Medium（Redis 配置复杂）

3. **创建搜索 API** (File: app/api/search/route.ts)
   - Action: 实现 GET /api/search
   - Why: 暴露搜索端点
   - Dependencies: Step 1 & 2
   - Risk: Low

### Phase 2: Frontend Integration (1-2 hours)

4. **更新搜索组件** (File: components/SearchBar.tsx)
   - Action: 添加 debounce、调用 API
   - Why: 实时搜索体验
   - Dependencies: Step 3
   - Risk: Low

## Testing Strategy
- Unit tests: openai.ts, redis.ts（mock 外部调用）
- Integration tests: search API route
- E2E tests: 用户搜索流程

## Risks & Mitigations
- **Risk**: OpenAI API 费用过高
  - Mitigation: 缓存 embeddings，限流
- **Risk**: Redis 不可用
  - Mitigation: 降级到 PostgreSQL 子串搜索

## Success Criteria
- [ ] 搜索返回相关结果
- [ ] 响应时间 <500ms
- [ ] Redis 降级工作正常
- [ ] 测试覆盖率 80%+
```

**何时调用**：
- ✅ **复杂功能开发前自动调用**
- 你说"规划实现"时

**输出效果**：
- 详细的分步计划
- 清晰的文件路径和依赖关系
- 风险识别和缓解策略

---

## 七、文档类

### 21. doc-updater (Agent - 文档维护专家)

**简介**：保持文档和代码地图与代码同步。

**核心功能**：
- ✅ **代码地图更新**：生成 docs/CODEMAPS/*
- ✅ **README 更新**：同步 API 变更
- ✅ **文档生成**：运行 /update-docs 命令
- ✅ **文档验证**：检查文档是否过期

**何时调用**：
- 你说"/update-docs"或"更新文档"时

---

## 八、智能学习类

### 22. continuous-learning (Skill - 持续学习 v1)

**简介**：从会话中自动提取可复用模式，保存为技能。

**核心功能**：
- ✅ **模式提取**：识别重复的工作流程
- ✅ **技能生成**：自动生成 SKILL.md
- ✅ **会话分析**：在会话结束时分析

**工作原理**：

```
会话结束 → Stop Hook 触发 → 分析会话历史 → 提取模式 →
生成技能 → 保存到 ~/.claude/skills/learned/
```

**何时生效**：
- ✅ **后台自动运行**（会话结束时）

---

### 23. continuous-learning-v2 (Skill - 持续学习 v2)

**简介**：基于"直觉"的学习系统，通过 hooks 观察、置信度评分、进化为技能。

**核心功能**：
- ✅ **实时观察**：PreToolUse/PostToolUse hooks（100%可靠）
- ✅ **原子直觉**：小的学习单元，带置信度评分
- ✅ **置信度系统**：0.3-0.9，随时间演化
- ✅ **直觉进化**：聚合成 skills/commands/agents
- ✅ **导入导出**：分享学到的模式

**架构**：

```
会话活动
  │
  │ Hooks 捕获（100%可靠）
  ▼
observations.jsonl
  │
  │ Observer agent 分析（Haiku，后台）
  ▼
instincts/personal/
  ├── prefer-functional.md (置信度: 0.7)
  ├── always-test-first.md (置信度: 0.9)
  └── use-zod-validation.md (置信度: 0.6)
  │
  │ /evolve 聚合
  ▼
evolved/
  ├── skills/testing-workflow.md
  ├── commands/new-feature.md
  └── agents/refactor-specialist.md
```

**置信度评分**：

| 分数 | 含义 | 行为 |
|------|------|------|
| 0.3 | 试探性 | 建议但不强制 |
| 0.5 | 中等 | 相关时应用 |
| 0.7 | 强 | 自动批准 |
| 0.9 | 接近确定 | 核心行为 |

**可用命令**：

```bash
/instinct-status        # 查看所有学到的直觉
/evolve                # 将相关直觉聚合成技能
/instinct-export       # 导出直觉分享给他人
/instinct-import <file> # 导入他人的直觉
```

**何时生效**：
- ✅ **后台持续运行**
- 每次工具调用都被观察
- 自动学习你的偏好和模式

**输出效果**：
- 自动学习你的编码风格
- 生成个性化的技能
- 可以分享学到的模式

---

## 九、其他工具类

### 24. eval-harness (Skill - 评估框架)

**简介**：正式的评估框架，实现评估驱动开发（EDD）原则。

**核心功能**：
- ✅ **质量评估**：代码质量、测试覆盖、性能
- ✅ **合规检查**：是否符合项目规范
- ✅ **自动化评分**：量化开发质量

**何时使用**：
- 需要评估开发质量时
- CI/CD 集成质量门禁

---

### 25. iterative-retrieval (Skill - 渐进式上下文检索)

**简介**：渐进式优化上下文检索，解决子代理上下文问题。

**核心功能**：
- ✅ **多轮检索**：逐步细化搜索
- ✅ **上下文优化**：只加载相关代码
- ✅ **子代理协作**：多个 agent 协同工作

**何时使用**：
- 处理复杂的多步骤任务时
- 需要多个 agent 协作时

---

### 26. strategic-compact (Skill - 智能上下文压缩)

**简介**：在逻辑节点建议手动压缩上下文，而非随意自动压缩。

**核心功能**：
- ✅ **压缩时机建议**：任务阶段完成时提醒
- ✅ **保留关键上下文**：避免丢失重要信息
- ✅ **手动控制**：用户决定何时压缩

**何时生效**：
- ✅ **上下文快满时自动提醒**
- 任务阶段切换时建议压缩

---

## 总结：如何选择使用哪个技能/代理？

### 自动激活（我会主动使用，无需你要求）

| 场景 | 技能/代理 |
|------|----------|
| 写任何代码 | `coding-standards`, `backend-patterns`, `frontend-patterns` |
| 写完代码 | `code-reviewer` |
| 涉及安全功能 | `security-reviewer` |
| 写数据库代码 | `postgres-patterns`, `database-reviewer` |
| 构建失败 | `build-error-resolver`, `go-build-resolver` |
| 复杂功能前 | `planner`, `architect` |
| 上下文快满 | `strategic-compact` |
| 后台持续 | `continuous-learning-v2` |

### 手动调用（你明确要求时）

| 你说 | 调用 |
|------|------|
| "用 TDD 方式" | `tdd-guide` |
| "写 E2E 测试" | `e2e-runner` |
| "清理死代码" | `refactor-cleaner` |
| "/update-docs" | `doc-updater` |
| "/evolve" | `continuous-learning-v2` |
| "审查代码" | `code-reviewer` |
| "安全审查" | `security-reviewer` |

---

## 最佳实践建议

1. **信任自动化**：我会在合适的时机自动调用相关技能/代理，无需你担心
2. **看审查报告**：写完代码后，查看 code-reviewer 的报告，关注 CRITICAL 和 HIGH 问题
3. **TDD 优先**：新功能使用 TDD 方式开发，确保测试覆盖
4. **定期清理**：使用 refactor-cleaner 清理死代码
5. **学习共享**：使用 continuous-learning-v2 积累个人经验，并导出分享给团队

---

**文档版本**：v1.0
**生成时间**：2026-01-29
**适用项目**：所有安装了 everything-claude-code 的项目
