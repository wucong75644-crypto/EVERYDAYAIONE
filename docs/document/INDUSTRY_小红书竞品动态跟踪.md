# 小红书竞争博主与店铺动态跟踪调研

调研日期：2026-10-14（北京时间）。项目基线：504382b2。状态：研究建议，未开发、未部署、未采购、未创建监控或通知任务。对象为未授权的第三方博主与竞争店铺。

## 结论与证据层级

公开博主笔记、已知商品快照及可见买家评价有可用的技术接入候选；指定店铺完整商品枚举仍缺真实目标验证。竞争店铺真实订单、净销量、退款和成交额不能用当前证据承诺。以下外部能力来自公开文档或源代码，不代表已购买、已实调或已验证长期完整性。

官方电商开放平台面向授权店铺；授权流程要求店铺主账号授权。该路线不能作为未授权竞争店铺的经营数据来源。[官方开放平台](https://open.xiaohongshu.com/)、[授权流程](https://open.xiaohongshu.com/document/developer/file/38)。

| 目标 | 可尝试获取 | 必须保留的边界 |
|---|---|---|
| 博主发帖 | 作品ID、标题、链接、发布时间（如返回）、正文、公开互动 | 需要作者作品列表；搜索替代不能保证完整发现；不保证私密、删除或全部历史 |
| 店铺上新 | 指定店铺商品列表快照与新增商品ID | 需要真正的按店铺枚举/分页能力；关键词商品搜索加shop_id过滤仅能提供候选；首次发现时间不是实际上架时间 |
| 商品销售变化 | 平台展示销量文本/数量、明确标注的第三方估算 | 展示值可能为下限或四舍五入；不等于期间真实销量，不据此计算精确GMV或净成交 |
| 商品评价 | 可见买家评价ID、正文、商品/物流/服务分、规格、时间文本 | 与笔记评论不同；分页覆盖与排序可能变化；缺失ID降低去重可靠性；不以样本计算全店准确差评率 |

## 数据接入候选

### 现有xiaohongshu-mcp

上游提供user_profile及用户笔记列表读取。核对当前main源码，主页读取主要从已加载的window.__INITIAL_STATE__.user.notes提取，没有在该函数发现全量滚动分页实现；README所称公开作品能力不能替代完整性验证。需要评估固定生产版本、主页令牌刷新和有界等待，不应直接升级全部上游。

来源：[官方仓库说明](https://github.com/xpzouying/xiaohongshu-mcp/blob/main/README.md)、[主页读取源码](https://raw.githubusercontent.com/xpzouying/xiaohongshu-mcp/main/xiaohongshu/user_profile.go)。当前项目公共工具仅接入search/read/read_comments，尚未接入主页发现和商品/买家评价。

### SocialDataX

核验公共OpenAPI（info.version=2026-09-02）：提供用户笔记列表、商品搜索、商品详情和商品评价列表；商品以sku_id调用，商品结果含shop_id，可见销量保留sales_text与sold_count。文档将带“+”数量定义为下限，缺失为null。商品评价支持最新优先与分页，含review_id、正文、评分及规格；时间可能只有相对文本/月日，不能强行补全年份。

核对公开路径目录未发现独立店铺全量商品列表接口，不能以商品搜索替代。分页令牌为空可能是无更多结果，也可能无法继续，不能自动宣布全量。接口可能HTTP200仍返回业务错误码，需按业务结果判定。

来源：[API文档入口](https://socialdatax.52choujiang.com/api-docs/)、[公开OpenAPI](https://mcp.socialdatax.com/socialdatax/api/v1/docs/openapi.json)。本轮用HTTP成功读取公共JSON，未调用需Key的业务接口。

### TikHub、DataXAPI与千瓜

TikHub维护者SDK列出用户作品游标、商品详情、商品搜索、评价总览及评价列表，证明存在集成候选；仍需真实数据验证，不把其他平台的seller/shop接口误认作小红书接口。[维护者SDK](https://github.com/TikHub/TikHub-API-Python-SDK/blob/main/docs/reference.md)。

DataXAPI公开文档提供用户作品分页、商品快照与买家评价；作品页明确不保证完整历史。商品详情、评价页各标价0.19元/调用，空结果也可能收费，评价覆盖可变。商品详情当前字段与其他厂商不同，不能因为都叫detail就推断包含销量。[作品列表](https://dataxapi.com/en/apis/xiaohongshu/users/notes/)、[商品详情](https://dataxapi.com/en/apis/xiaohongshu/products/detail/)、[商品评价](https://dataxapi.com/en/apis/xiaohongshu/products/reviews/)。

千瓜公开功能目录包括店铺搜索、商品销售分析及监控；其协议将部分销量/销售额归为预估，并限制披露范围。网页会员功能不能直接推断存在可接入API或获准将数据经本系统转发，须核验授权方式。[功能目录](https://www.qian-gua.com/Home/AllPrice?typeId=1)、[预估数据说明](https://wap.qian-gua.com/Home/IndexAnalysisTool)。

## 推荐自动跟踪机制（尚未确认的实施建议）

流程：目标名单 → 后台限频采集 → 保存带来源和时间的快照 → 与基线比较 → 去重事件 → 指定渠道提醒/日报。

1. 目标以稳定user_id/shop_id/product_id/sku_id识别，不以昵称或商品标题去重；博主与店铺的对应关系需显式核验。
2. 第一轮建立基线，历史内容不批量当作“新增”。新帖、首次发现商品、新增SKU、重新上架分别记录；分页不完整时标注覆盖缺口。
3. 读取作品列表使用重叠窗口和ID去重，置顶变化不当新帖；商品搜索候选必须校验shop_id。历史已知商品持续监控与全店新商品发现分开。
4. 保存销量原文、精度/下限、来源和采样时间。下限从1.2万+到1.3万+只能说展示档位变化，不能说期间精确售出1000件；显示不变也不能证明无成交。厂商估算与页面观察分栏。
5. 评价按最新排序取有限页，与已见ID重叠；只有文本时使用降级去重并标记不确定。新评价/低分评价可提醒；LLM识别问题主题需引用原文，不能自动推断所有用户都有同类问题。
6. 调度复用现有后台Worker、逐项队列、原子预算准入和恢复时间。周期等于预期检查频率，不是发现延迟上限；账户登录、分页、冷却或上游更新延迟均可能延后。
7. 登录失效/验证码/明确限频时暂停并提醒负责人，不反复撞平台；失败时保留上次成功快照与过期标记，不制造“无更新/商品下架”事件。
8. 提醒仅在新事件、重要变化、首次故障及恢复时产生，平时安静；普通评价可日报合并。站内、企业微信/飞书等需明确收件人、渠道授权及是否允许第三方数据转发。
9. 事件和投递分开去重，重试/重启不重复制造事件；渠道确认不明确时无法保证外部消息绝不重复。采集与提醒内容保留对象、变化前后值、观察时间、来源链接与覆盖状态。

## 容量与成本

项目目前有后台周期研究、不可变来源快照、账号额度/间隔、任务恢复和消息投递基础；竞品监控目标、商品/评价采集、差异事件和专用提醒规则仍需新增。现有自发内容的周期复盘不能直接等同竞争店铺监控。

按现有默认每日100估计单位及身份+动作每次2单位作规划示例：5位博主每6小时检查一次，单页发现已约40单位/日；详情、翻页和评论继续消耗，实际成本可能更高。不能直接承诺几十个博主分钟级自建监控，也不默认提高额度。

付费示例仅用于量级：按DataXAPI页面0.19元/次，10个SKU每天2轮、每轮详情+一页评价，共40次，约7.60元/日、30天228元；未含博主、发现、额外评价页和模型摘要，不代表销量接口全包或最终报价。供应商收费与我们本地账号估计单位是不同预算，不能互换。

## 进入实施前最小验证

建议取得3–5位博主主页、2个竞争店铺、5–10个商品样本。先验证真实作者/店铺身份、列表可达性、分页和稳定ID、商品规格映射、可见销量口径、评价排序/覆盖及时间精度。连续观察3–7天与用户人工页面抽样对照，记录发现延迟、漏采、重复、平台限制、调用量和费用。

需用户后续决定：样本对象、重点商品范围、发现延迟目标、提醒收件人/渠道、付费数据预算。店铺全量枚举不通过时明确交付“已知商品监控+候选发现”，不得称全店上新监控。只有真实样本验证后才能决定具体供应商与承诺覆盖范围。

## 2026-10-15 现有浏览器后端实测（未接付费API）

用户授权任取合适公开博主作测试。使用生产已连接的小红书专用服务、其同版本源码/browser.NewBrowser工厂、既有Chromium148和固定会话指纹。原生读取通过现有REST，页面观察通过临时Go探针，不修改生产源码/二进制、不创建持续任务、不登录/导出Cookie、不接付费接口。平台访问沿用账号锁和AccessBudgetService预留/结算，保持原限频及额度。未点击关注、购买、评论等写入按钮。探针后续固定使用-count=1；Go缓存回放未纳入实测结论。

### 样本和成功证据

1. 搜索hapikoko彩色文具屋：返回20条结果，7条作者匹配；选中“谁还没拥有这么可爱的笔？！【新品设计首曝】”。浏览器正常显示正文、作者与笔记评论，并从真实作者链接取得主页访问参数。不是商品评价。
2. 原生POST /api/v1/user/profile成功返回该作者资料与24条笔记，核验24个不同ID。字段含id、xsecToken、标题、作者、interactInfo和封面；native输出不带逐帖发布时间，后续需读详情。页面包含置顶和新品预告，不能仅按第一张卡片判断最新发帖。主页两次有界滚动未增加该样本已加载24条，不能据此宣称完整历史。
3. 补充搜索“得力官方旗舰店”，选昵称完全匹配账号的“这白板里加了什么？！好能写啊”笔记；读取实际笔记和作者主页，不能仅凭昵称证明其店铺身份或认证主体。页面简介提到订单、售后与店铺客服，但没有可点击的店铺入口。
4. 第二个作者主页滚动实测：页面状态中的作品数组依次61、91、121条；跨3帧DOM合计提取70个不同笔记链接。DOM每帧只有约30张可见卡片，存在虚拟化，不能将可见卡片数当全部作品数。证明同一浏览器能够继续加载作品；尚未完整导出/去重121条状态记录或验证完整历史。

样本主页标识：hapikoko为67137519000000001d032a33；第二样本为6162c426000000001f035c83。调研记录不保存xsec_token或认证Token；有效访问参数只在服务器受权限限制的临时探针文件中使用。

### 电商部分的真实限制

在这两个样本的笔记页与桌面主页上，未发现可进入的店铺/橱窗/商品链接或真正的购买入口。第二个样本简介里的“店铺客服”只是文本，不是商品入口。现有专用服务路由只有user/profile等内容操作，没有独立shop/goods/product/review读取路由。

因此本轮没有取得竞争店铺商品列表、商品展示销量或买家评价，尚未完成真实商品H5页的读取验证。这些是“当前已测桌面路径未打通/缺少真实商品页样本”，不能扩大为“小红书所有商品页都不可读”，也不能推出“必须购买API”。关键词和笔记可用于观察新品宣传，但不能代替店铺正式上架事件。商品销量、商品评价也不能拿笔记互动数、笔记评论替代。

目前可以支持进入实施评估的部分：扩展agent_reach主页发现、持续刷新作品列表、按稳定ID去重并读取新笔记详情，结合现有后台任务实现发帖/新品预告提醒。电商部分仍需要拿到真实小红书店铺或商品分享链接，再用同一浏览器验证H5访问、商品字段、评价入口和分页；验证前不承诺全店上新、销量或评价监控。

截图（只含公开页面，可用于复核，不是全量数据导出）：

- [第二样本主页滚动后](../../outputs/xhs-browser-validation/deli-profile.png)
- [第二样本笔记页](../../outputs/xhs-browser-validation/deli-note.png)

本轮只完成读取可行性验证，没有新增监控产品功能、推送或部署。

## 2026-10-15 真实商品/店铺链接补充实测与GitHub参考

本节更新上一节电商尚未打通的结论：通过真实商品链接，现有浏览器已读到商品详情、展示销量和店铺预览；全店枚举与买家评价正文仍未确认。没有接入付费API，没有开发监控或线上查询适配器。

### 真实链接及观察值

- 商品：[【黑贝果】手工 孤鹰之爪 戒指](https://www.xiaohongshu.com/goods-detail/6967abf98f0f9c0001c2ab3d)。商品链接取自下述GitHub仓库的已保存商品数据，随后以我们的浏览器真实打开，商品名称相符，不把文档示例当有效商品。
- 店铺：[黑贝果](https://www.xiaohongshu.com/shop/65263c56796c030001e298d4)。店铺链接来自该真实商品H5数据的sellerH5.link，未根据ID猜测拼接。
- 00:18–00:23实际页面：商品展示到手价16.8元、划线价46.8元、已售7299；商品页展示店铺名“黑贝果的店”、卖家口碑4.52、店铺已售6.6万。店铺页展示昵称“黑贝果”、好评率97.9%、店铺已售6.6万。
- 店铺首屏6张商品卡片，含商品名、价格和展示销量；例如“乌洛波洛斯 戒指”16.8元/已售7099、“星迹 耳钉”15.8元/已售4464、“绿野 耳骨夹”15.8元/已售1.1万+。
- 页面价格是观察到的展示到手价，不承诺实际结算价；已售文本保留原样，6.6万和1.1万+不转换成精确订单数。

### 读取路径和边界

使用同一个既有Chromium/browser.NewBrowser工厂与已连接会话，先打开goods-detail页面取DOM；再参考GitHub公开文档，用浏览器直接打开小红书自己的H5读取地址：

`https://mall.xiaohongshu.com/api/store/jpd/edith/detail/h5/toc?version=0.0.5&item_id=6967abf98f0f9c0001c2ab3d`

它是小红书自身网页的数据返回，不是新增付费第三方服务，也没有生成签名、读取/导出Cookie或绕过验证码。实际success=true，字段与DOM交叉一致：descriptionMain.name、descriptionH5.skuId、priceH5.dealPrice.price、priceH5.itemAnalysisDataText、sellerH5.name/id/link/salesVolume/sellerScore。该非开放平台内部路径可能变化；可行性证据不等于稳定公共API承诺。

店铺列表覆盖测试：初始6张，滚动后仍6张；点击“新品”后跳至/oia，页面要求下载/打开小红书App。因此只能确认网页预览商品可读，不能声称拿到全部商品或可靠地按新品排序。测试脚本随后尝试定位“进店查看更多商品”时已在App引导页，未实际独立点击该入口，不把该帧当作独立成功导航证据。

买家评价：商品页面未找到评价/晒单入口，reviews/comments选择器无结果；同商品H5的template_data中没有comment/review/evaluation模块。读到店铺好评率与卖家口碑，不等于取得买家评价正文、日期、规格或评价列表。当前样本这条H5路径未完成评价正文读取，不推断所有商品均无评价。

### 开源参考核验

1. [Alex-supersolo/rednote_monitor](https://github.com/Alex-supersolo/rednote_monitor)，核验提交51f39504387621a062a0495519d0ee0cfebb2441。仓库有实际Node/Puppeteer代码，server_simple.js从商品页面DOM及文本提取名称、价格、已售和店铺信息；data/products.json提供本次有效商品链接。未发现该代码提供买家评价列表或完整店铺商品枚举。仅阅读，没有安装、运行或复制该项目代码；仓库API未返回明确开源许可证，不能据公开仓库就认定可直接复制复用。
2. [dong-liudong/hongshuleida/dq.md](https://github.com/dong-liudong/hongshuleida/blob/main/dq.md)，核验提交3b1dd38db4c27928393be740e1d229b39e46144a。仓库树只有dq.md，是开发规格/提示词，不是交付实现。提供了上述第一方H5路径与字段线索，本轮已独立实测；其中空销量按0、累计高水位及日GMV等处理不能直接照搬为数据真实性保证。
3. [cv-cat/Spider_XHS](https://github.com/cv-cat/Spider_XHS)：核对PC与千帆接口文件，PC评论主要是笔记评论；千帆存在店铺/达人相关接口但需要对应认证上下文，本轮未执行或接入。不能据名字“comment/shop”就认定能读竞争商品买家评价。

### 证据及当前结论

- [真实商品页截图](../../outputs/xhs-browser-validation/heibeiguo-product.png)
- [真实店铺页截图](../../outputs/xhs-browser-validation/heibeiguo-shop.png)
- [新品排序进入App引导页](../../outputs/xhs-browser-validation/heibeiguo-app-boundary.png)
- [去除令牌的结构化验证摘要](../../outputs/xhs-browser-validation/commerce-evidence.json)

可确认：底层浏览器可以读取真实商品、商品展示销量、店铺展示销量、店铺汇总口碑及6个预览商品。尚未确认：全店分页/新品覆盖、买家评价列表和正文。现有公开agent_reach接口仍未封装这些电商链接与数据字段，本次只验证底层，不宣称线上查询工具已新增该功能。

## 2026-10-15 全店分页与评价正文继续核验

本轮继续只读探针，沿用原账号锁、访问预算及同一浏览器工厂，没有修改生产实现、接入付费API或开发监控。

### 全店列表

独立打开真实店铺后点击“进店查看更多商品”，确认元素存在且click成功，跳转到https://oia.xiaohongshu.com/oia，正文为下载App/直接在App内打开。修正此前先点新品导致后续查看更多没有实际执行的证据缺口。

检查该页面实际加载的第一方main.feb6789.js：普通店铺页组件total>6时显示查看更多；普通模式内容被唤起App组件包裹。分页/排序逻辑的另一分支是preview=true，走hawkeye.devops域名的商家预览接口。这不是已验证可用于公开竞品查询的普通分页入口，本轮未访问该商家预览服务。

同脚本普通页面使用mall.xiaohongshu.com/api/store/guide/components/general/h5，shopH5Page/shop/shop_h5。使用现有浏览器在同源下只读POST验证：camelCase顶层请求返回500 BizIdentity为空，属于请求序列化错误，不是权限结论；snake_case请求返回200、success=true，但请求page=2/pageSize=20后仍返回page=1/pageSize=6。返回DefaultMixCardComponent.total=555、hasMore=true，仍为同样6个预览商品，没有第二页新商品。混合字段命名变体外层success=true，内层data.result.success=false、error_code=INVALID_PARAM；属于参数无效，未取得商品组件。不能将hasMore=true或total=555当成全量枚举成功；total仅是本接口报告值，尚未验证其统计口径。

可额外读取店铺分类、排序选项、卡片itemId/skuId及已售标签；例如绿野耳骨夹标签“已售1.1万+”，tag_ext_info.sold为字符串11771，而baseInfo.itemSaleNum为0。存在字段语义差异，不应默认任取数字或将0当真实销量。此轮没有读取完整555个商品。

### 买家评价与开源代码真实性

既有商品H5样本仍无买家评价列表或正文，未获得新评价数据。补查以下仓库实际源码：

- yaoyunchou/hsld，提交647ebb9461f5b7e26d34d33fd73adcc56cafc2c9，red-potato-radar/parser.py：提供真实商品H5接口、价格/已售解析；没有全店枚举或评价正文实现。其“完整响应中销量空值=>0”不能作为本项目真实性规则直接复用。
- Aritz-China/MediaCrawleR，提交eb027a712b126af3e445bea2f5d93ba53d5c9bd0，media_platform/xhs/mall.py：存在get_product_reviews函数与mall/product/reviews路径，但同文件分类/商品列表含明确模拟数据，异常转空列表；没有找到足以证明该路径能返回真实小红书买家评价的样本。因此仅作为未验证线索，不安装执行、不宣布支持。文件声明禁止商业用途，不直接复制进产品。
- TonyWang-hub/mcp-cn-commerce，提交5ee226b9cb1d45cf820cb6bc669c7fdb583035de，servers/xiaohongshu/server.py：依赖CLIENT_ID/CLIENT_SECRET/ACCESS_TOKEN商家认证；get_review_list注释明确“Unavailable: no verified official review API mapping”。不适合作为竞争店铺公开评价浏览器实现的成功证据。

结论：本轮增加了“接口报告555件、实际仅返回6件且分页参数未生效”的证据，仍未打通全店商品枚举和买家评价正文。不能承诺通过现有浏览器后端稳定查询这两部分，也不能据此断言必须购买API或所有店铺都不可读。可复用现有浏览器读取已知商品信息和店铺预览；完整目录和真实评价需要新的、经实际内容验收的访问路径。

临时原始探针：/tmp/xhs-monitor-shop-assets.json、/tmp/xhs-monitor-shop-more.json、/tmp/xhs-monitor-shop-h5-pagination.json、/tmp/xhs-monitor-shop-h5-snake.json、/tmp/xhs-monitor-shop-h5-mixed.json（生产服务器临时目录）。本地研究输出仅保存公开店铺数据，不包含Cookie/Token。

## 2026-10-15 继续追查分页参数与真实评价入口

### 已核实的分页失效原因与限制

从真实商品页main.10fe36d.js中的9935数据转换模块，以及HTTP请求拦截器核实：transform=true会递归把请求data/params的camelCase字段转成snake_case（包括嵌套对象/数组）。此前全snake_case请求格式与该转换一致；camelCase顶层导致BizIdentity为空和混合格式INVALID_PARAM属于无效探针，不能作为分页能力证据。

真实店铺barley页面MainContent和index组件显示：普通分享页调用shop_h5通用组件入口，本身没有传pageParam；滚动分页、排序刷新函数f仅服务preview分支，普通页面点击内容由唤起App组件处理。preview分支使用shop_pc场景的hawkeye.devops商家预览服务，本轮不访问或借用该权限。此前将PC预览分页参数加到普通H5通用入口，实际仍得到page=1/pageSize=6，是混用两条访问路径，不能认为支持分页的同一接口发生普通参数bug。可确认普通H5入口未按本次分页参数提供新商品；无法仅凭前端代码断言服务端内部为何忽略参数或所有请求形式均不支持分页。

额外验证混合字段命名（仅componentParam的动态键/子对象使用camelCase、commonParam保持seller_id）仍是内层INVALID_PARAM。没有以外层success替代内层业务成功。

### 从实际商品页脚本取得的新接口线索及实测

真实加载的商品main脚本包含生成式服务函数：

- GET /api/store/review/{itemId}/product_review（获取评价数据）
- GET /api/store/review/{itemId}/product_detail/product_review
- GET /api/store/review/{itemId}/shop_review
- GET /api/store/vs/{storeId}/items/v2（双列接口）

这些是脚本内存在的读取定义，不能当成当前H5实际渲染/调用或稳定公开API承诺。已读当前商详动态chunks中未发现评价渲染调用；未执行任何评价点赞、关注、购买、最近买家身份列表等无关路径。

核实HTTP生产默认baseURL为www.xiaohongshu.com，mall域名仅是商详H5数据的显式覆盖。先验证mall上的product_review和vs/items均为nginx404，属于域名/路由不匹配，不能推出评价接口不存在。

换到www域名，用商品真实itemId=6967abf98f0f9c0001c2ab3c（分享URL是skuId=6967abf98f0f9c0001c2ab3d），product_review返回HTTP200但code=-100、success=false、msg=登录已过期。进一步在真实商品页面内调用其已初始化的launcher HTTP客户端（正常请求拦截器、transform及页面原有认证逻辑；未读取或输出Cookie/Token），同一读取仍抛HTTPBizError：code=-100/status=200/登录已过期。排除“只因直接Navigate没有走页面正常客户端”这一个解释；本连接未获得该评价接口接受的认证上下文。不能将此错误扩大为小红书笔记会话全部失效，也未判断具体缺少哪种凭证、是否还有其他权限限制。未自动重新登录、生成或伪造凭证。

www上的vs/{storeId}/items/v2以当前sellerId调用，返回success=true/data=[]。商品H5明确给出sellerId=65263c56796c030001e298d4及sellerUserId=614fe0bf000000000201810d，未给出这个旧vs接口另需的storeId映射。空数组既不能证明全店为空，也不能证明该接口对现代店铺可用；不得当作全店读取成功。

新增证据文件（服务器/tmp）：xhs-monitor-shop-h5-map.json、xhs-monitor-goods-assets.json、xhs-monitor-review-native.json、xhs-monitor-store-items-native.json、xhs-monitor-review-web.json、xhs-monitor-store-items-web.json、xhs-monitor-review-page-client.json。所有平台请求保持账号锁及AccessBudgetService，本轮未修改查询实现、部署或监控。

补充：旧vs/items/v2再显式传page=1&page_size=20，仍返回success=true/data=[]。这组参数仅为候选请求，并无该旧接口参数契约的独立验证，不能据它排除所有旧接口参数形式。当前可靠结论仍为没有取得新的店铺商品，未打通全店分页。

本轮收束：已定位普通H5与商家预览分页分支混用，找到第一方商品评价路径并经页面正常客户端验证当前认证失败，旧商品双列入口未取得有效商品。尚无真正验收通过的全店分页或买家评价正文读取方案。不接付费API、不开发监控的范围保持不变。

## 2026-10-15 评价认证机制与旧storeId来源验证

### 评价接口认证：当前会话缺少页面要求的sid

真实商品页main.10fe36d.js的authConfig明确scheme.type="sid"。sid_createServices先从App bridge getSession获取会话，失败时回退当前URL的sid参数。getSession内部调用getUserInfo，读取value.sessionId；正常请求拦截器将有效sid放入Authorization，并默认同时附加sid查询参数。该页面未配置用笔记web_session Cookie转换成sid的逻辑。此处仅追查既有前端认证流程，不提取、生成、复用或输出任何凭证值，不触发登录。

通过同一浏览器实际商品页做只读存在性验证：

- appBridgePresent=false（该特定全局桥接对象未存在；主要证据仍是实际getSession失败）
- querySidPresent=false
- 页面提供的getSession返回错误“not support getUserInfo”，sessionAvailable=false
- 页面正常HTTP客户端准备发出评价请求时，authorizationPresent=false、sidParamPresent=false
- 评价响应仍为HTTP200/code=-100/登录已过期

因此，这个样本的评价失败已有具体原因：现有桌面浏览器会话没有取得此电商读取路径要求的sid，实际请求也未携带Authorization。不能将其解释为已携带有效sid但被判过期，更不能用HTTP200判成功。现有笔记网页Cookie不等价于这条App/H5电商会话。仍未证明获得有效sid后必定能取评价、评价分页参数契约或是否有其他权限限制。

### 旧storeId：sellerId已被旧详情接口接受

实际请求https://www.xiaohongshu.com/api/store/vs/65263c56796c030001e298d4/details，返回success=true、popup_shop_info.shop_name=黑贝果的店、brand.name=黑贝果、brand.id及user_info.user_id=614fe0bf000000000201810d，与现代商品H5一致。说明在本样本可从真实商品H5的sellerH5.link或bottomBarMainH5.seller.sellerId取得店铺ID，并用于旧vs详情路径；此前“可能另需storeId映射”在本样本已被这个正向匹配验证取代。博主userId不是本样本旧接口的storeId，不需猜测或换成账号ID。

旧详情同时返回popup_shop_info.shop_type=NO_SHOP_INFO、status=CLOSE_SHOP；同店现代H5仍返回商品预览。只能认定两套数据视图/访问结果存在差异，不能对外报告该店实际关闭。旧商品items/v2此前含/不含候选分页参数均返回空列表；既然详情ID已匹配，不能再归咎于店铺ID完全错误。尚未取得该旧列表正式参数契约，也不能仅凭旧状态字段断定空列表的服务端唯一原因。

结论：旧店铺详情读取已打通，ID来源已确认；旧全店列表仍未打通。评价路径的当前认证缺口已验证，现有会话不能直接提供所需sid，尚未取得买家评价正文。没有接付费API、修改生产实现、自动重新登录或开发监控。

本地公开/脱敏证据：outputs/xhs-browser-validation/commerce-auth-presence.json（仅存在性布尔值与错误）、legacy-store-details.json（公开店铺信息）。服务器探针路径为/tmp/xhs-monitor-commerce-auth-presence.json及/tmp/xhs-monitor-store-details.json。

## 2026-10-15 sid正常获取入口与现代商品列表继续核验

真实商品页login_gotoLoginPage在普通浏览器分支跳转https://www.xiaohongshu.com/web-login?redirectPath=<完整商品URL>。正常App分支使用App本身的登录/会话机制；未调用该分支，因为代码含logout行为。本轮只导航网页入口，不调用登录/登出函数，不发送验证码、不填写手机号。

页面代码保留短信发送、验证码校验及phone_without_password_in_web登录路径，说明有过正常网页登录实现；不能仅凭代码存在断言当前可签发电商sid。首次探针误用相对redirectPath，得到手机号/验证码表单和“回调地址错误”，不作为正常获取成功/失败证据。修正为源码同形式完整绝对商品URL后，实际最终跳转https://oia.xiaohongshu.com/oia，提示下载/在App打开，没有sid参数。本次仅记录最终origin/path与查询参数名，没有记录凭证值。现有桌面会话未通过该入口获得可用电商sid，尚未证明另行合法网页登录一定可取得sid或有效sid必能读取评价。

现代店铺网页实际入口保持：mall.xiaohongshu.com/api/store/guide/components/general/h5，biz_identity={template_id:shopH5Page,biz_type:shop,scene:shop_h5}，common_param.seller_id。这是6件分享预览；完整商品列表仍未取得。页面PC预览分页分支走hawkeye.devops的商家服务，未访问或借用商家权限。

同一现代页面主脚本还提供第一方新品日历GET https://edith.xiaohongshu.com/api/store/personal/new_arrival_goods_list，前端参数sellerId、hostId、page、pageSize、id，转换为snake_case；该路径属于新品日历，不能直接当全店商品目录。现有浏览器实际传真实seller_id=65263c56796c030001e298d4、host_id=614fe0bf000000000201810d、page=1/page_size=20，未提供未知的id，返回HTTP正常、外层success=true/code=0，但data.result.success=false，无商品列表。未取得失败原因，可能仍缺上下文；不能报告该入口已打通或将失败扩大为所有新品日历都不可读。

GitHub精确搜索new_arrival_goods_list/shop_h5及sid登录实现没有取得新的可信公开实现。本轮继续使用现有浏览器工厂、账号锁和预算，未接付费API、开发监控或修改线上功能。

结论：合法获取路径已追溯至App会话和网页登录入口，但现有桌面会话仍缺少sid，网页入口实测导向App；现代H5预览读取可用，新品日历候选接口尚未返回商品。没有验收通过的全店分页/评价正文方案。证据保存outputs/xhs-browser-validation/commerce-login-absolute.json及modern-arrival.json。

## 2026-10-15 新品日历id来源和当前网页登录代码澄清

实际barley路由为/barley/ssr/next/wakanda，pageInstance=shop_newgoods_page。DSLRenderer配置NewProductPageTop（内部包含NewProductCalendar）及FloatActionButton。主页面和日历接口均直接读取route.query中的sellerId、hostId、id；activityId另用于跟踪。这证明id从入站页面链接传入，客户端未从sellerId计算或补全id。源码没有在调用前校验id必填；因此必须修正“失败就是缺id”的潜在推断：缺少真实页面实例/上下文只是待验证假设，尚未识别id究竟是何种后端实体，也不能断言所有店铺都有新品日历页面。

同一浏览器调用new_arrival_page，传真实seller_id/host_id但不传未知id，仍为外层success=true、内层data.result.success=false，未返回components/shareData，不能由此自动发现完整分享链接。首次直接页面探针立即读取DOM时body尚未生成导致探针TypeError，不是平台拒绝；另行以4秒等待的页面快照验证，不把该异常当业务失败。

进一步读取官方fe-login的真实登录表单异步包705.2f19b64a.js，核实mounted在合法redirectPath下无条件调用jumpToApp，不检查已有登录状态。这解释此前完整回调导航到oia，不能理解成自动登录成功。当前表单POST LOGIN实际配置为/fe_api/burdock/web/v2/login/by_phone，短信发送配置为/fe_api/burdock/v2/login/identifyCode/send；表单成功处理仅导航回redirectPath，未读取响应sid或将sid附加到回调URL。页面配置withCredentials=true。这里也修正仅从商品通用SDK保留的旧phone_without_password_in_web方法推断当前网页登录路径的做法；实际登录页使用另一组接口。未执行短信发送或登录POST，无法验证服务器是否设置可被电商接受的认证Cookie、是否签发sid或是否另需App桥接会话。

用户已表示可配合必要登录。本轮尚未找到可保证在同一现有远程浏览器中完成并取得sid的稳定网页流程，不让用户先做无效的重复笔记网页登录。已请求用户提供任意真实“新品日历/上新预约/新品预告”页面的完整分享链接，以获取真实id及页面上下文；这是内容样本缺口，不是付费API或新增监控需求。

等待页面渲染后的实际结果：wakanda路径保留sellerId/hostId查询参数，正文仅“App 内打开”，无可提取分享链接。没有取得id或日历商品。证据为outputs/xhs-browser-validation/arrival-rendered.json。

## 2026-10-15 用户提供主页样本核验

用户提供user/profile/64e6e565000000000100e53d，现有浏览器正常读取昵称“盘盘（贴贴版）”，主页状态39条笔记。桌面主页未展示店铺/商品入口。用户分享参数仅用于导航，没有将访问参数保存到研究文档。

通过真实主页卡片点击读取笔记/explore/6acafde0000000001c02e1eb：“10月12日造景贴纸新品上新日历”。实际正文为米糯米3.0（四款特油亮膜，刀模+激光）、知意3.0（江南系列四款）、沐瑶24.0（两款），均10月12日20:00，并有3张图及2条作者评论。可以读到笔记式新品预告信息。此处“日历”是普通笔记标题，未取得官方wakanda日历页面id或分享链接。评论中的商品名在DOM中是普通note-text span，没有可提取商品/店铺链接；不将其当商品卡片或买家评价。

该样本正向验证现有浏览器可以查询这个博主的发帖与笔记式上新信息，但没有提供打通官方新品日历接口、店铺全量列表、商品销量或买家评价的新上下文。证据为outputs/xhs-browser-validation/user-calendar-note.json与user-calendar-note.png。未开发监控或变更线上功能。

## 2026-10-15 自主寻找预约店铺：样本排除及日额度阻塞

用户要求自行找真实商品预约店铺，不再让用户先找样本。本轮现有浏览器完成站内“上新预约”“小红书 店铺 新品预约 入口”“新品日历 店铺”搜索，并读取以下候选笔记：

- 花与珍珠匣品牌笔记“花与珍珠匣三丽鸥家族合作系列小物合集”，称10月15日开启预约，但桌面note state未返回商品/店铺/预约链接，不能据宣传文字认定小红书原生商品预约已验证。
- 古苔GOODTIME“古苔双11新品，AW*18套look＊（抽送衣服）”，实际正文明确“点击下面预约上新直播”，是直播预约，不作为商品预约或新品日历样本。
- 薯店长官方“这次真的不一样！双11超全好品味攻略公开”，仅指引App市集活动主会场，没有在桌面数据中取得具体预约店铺/商品分享链接。
- “不要再给我发延迟通知了好吗”是短剧预约，排除。
- “新品日历 店铺”多数是实体日历商品、笔记式排期，不能把名称匹配当wakanda页面。

后续古苔主页信息探针及“小红书预约开售商品”搜索均在账号访问准入阶段返回ACCESS_BUDGET_WAIT，未执行平台读取，不纳入内容证据。后者shell复制得到的/tmp/xhs-reservation-presale-search.json可能是旧搜索输出，明确无效，不用于结论。没有取得新的真实预约商品链接、日历id或买家评价正文，用户本轮目标尚未完成。

只读查询本地预算元数据确认：2026-10-15，Asia/Shanghai，daily_limit=100、consumed=100、allowed_start_hour=0、allowed_end_hour=24、breaker为空。准入next_eligible_at=2026-10-15T16:00:00+00:00，即北京时间2026-10-16 00:00。是账号共享日额度到限，不是平台登录或风控失败；100是账号当日累计预算消耗，不等于本轮执行了100次查询。未提高额度、换账号/绕过限额或创建自动续跑任务。

保留公开候选证据于outputs/xhs-browser-validation/reservation-brand-note.json、reservation-gutai-note.json、reservation-official-note.json。后续需日额度恢复或用户明确决定调整预算后，再继续原后端实测；没有理由要求用户为此重新登录。仍未接付费API或开发监控。

## 2026-10-15 开源实现专项核验：全店商品列表与买家评价正文

本轮遵循用户纠偏，仅检索GitHub公开代码、README、目录、测试和提交，不研究预约，不调用额度已满的小红书账号，不安装/执行第三方项目，不接付费API或开发监控。筛查11个新增候选，以下列出与目标直接相关的代码证据；源码存在不等于本机/线上实测成功。

### 1. GitBinS/xhs-product-search：真实公开商品采集，但仅首页可见商品

MIT，核验提交34990cb5d736e2b763f1b1ec93459229c6f5ed45。关键文件skill/xhs-product-collect/scripts/collect.py。

- fetch_shop_items（约586行）只GET一次www.xiaohongshu.com/shop/<sellerId>，分别解析product-item DOM和内嵌baseInfo JSON，按标题合并；没有翻页请求或完整枚举循环。
- collect_shop（约1038行）文档明确“首页可见商品列表”，输出标题也是“首页可见商品”。约754行注明首页前6件以外的销量口径问题。
- 没有买家评价列表或正文采集。好评率是店铺汇总，不是评论内容。
- dev/smoke.js和Python自检主要验证解析/格式，不发真实业务请求，不能证明全店采集成功。
- 部分商品候选ID通过末位加1猜测，部分日期逻辑从ID取时间戳，不能不加核验地采用；本项目应优先使用页面明确的skuId/itemId及字段口径。

结论：可作已知商品和首页预览解析参考，不能解决全店分页/评价正文。

### 2. wu83255-sketch/-：Android店铺采样有实际实现，不是全店采集

MIT，核验提交b60ddcd805edcee2d1e9de599c0e2499c0a1c11e。关键backend/app/services/xhs_mobile_crawler.py。

- 使用Android ADB输入、UIAutomator XML读取App店铺节点。
- collect_store_page（617行起）默认limit=50，实际只for range(2)滚动两次，然后按ID去重截取；没有读到末页/总数核对的完整性机制。
- _build_store_id/_build_product_id（1855行起）由店铺名/商品标题做SHA1合成本地ID，并构造xhsdiscover://goods/<合成ID>，不能直接作为平台真实商品分享链接或稳定商品ID。
- 两处review_count来自repurchase_text（回购人数）而非评价列表；rating使用店铺评分。不能将这些字段解释为买家评价数/单品评分。
- 网页分支xhs_live_crawler.py仅一次/两次可见候选提取，并允许笔记作者主页作为目标；不证明抓到店铺完整商品。还含sales_count*0.12/0.36生成增长字段，不能作为真实销量变化直接采用。
- 测试声称真机样本KANS韩束5商品，依赖backend/mobile_debug/mobile_smoke_shop5.json；该路径未出现在公开仓库树中，本轮无法复核原始样本。
- 未找到进入商品评价列表、提取买家评价正文/日期/规格及翻页的代码。

结论：是App UI采样参考，依赖新增Android运行环境，不能直接移植进现有浏览器，也不能宣称全店或评价正文已经可用。

### 3. zenvor/xhs-review-image-saver：商品评价区图片操作，不是正文抓取

MIT，提交5bed32836cd0c03396c1d4757891ddc911873d3c。读取README及app/src/main/java/com/example/xhsimagesaver/XhsAutomationService.kt。实现Android无障碍长按图片、保存、左右切换；需用户先打开评论图片。另有手动复制当前UI节点用于调试，但没有结构化买家评价正文、评价ID、日期、SKU或评价列表分页采集。不能将名称带review当评价正文实现。

### 4. 其他候选的边界

- weshty/xiaohongshugoodsspilder（Apache-2.0，提交2136d22c18dbceeaf4325f09e5d9df386f0b3ffe）：fetcher.py实际Playwright采集单商品HTML，parser.py仅名称/主图/价格/销量，无全店枚举和评价正文。
- burn374/xhs-sales-monitor（MIT，提交d0c3307d21286c533b684d1829014b4c525071b9）：README明确“不支持导入一家店铺中的全部商品”，店铺功能仅累计已售，排除为全店方案。
- AndersonBY/xiaohongshu-ecommerce-python-sdk（MIT，提交e25347c01fcd70cec91e1c05c51b34cd467d4a9d）：开放平台app_id/app_secret/商家授权access_token，只能在对应授权商家范围使用，不能代替竞品公开店铺读取。
- ItellYouTool/XHS-Vendor：公开树只有Readme和截图，无可审计采集源码，且无明确开源许可证，不视作可复用开源实现。
- zhi168168/shuju、Baiqun1011/xiaohongshu-monitor2：以已知商品销量采集为主，未取得能支持两项目标的全店分页或评价正文证据；README营销能力不能当验收结果。
- ningyan1228/xhs-product-monitor：README限公开笔记商品卡片/已给笔记列表；不覆盖全店和商品评价。
- xsye666888/xiaohongshu-automation：专业版自有运营数据/GMV/订单抓取，依赖相应后台登录，不是竞品查询。

此前已核验Aritz-China/MediaCrawleR的mall.py（模拟数据与未验证reviews函数）、TonyWang-hub/mcp-cn-commerce（评价映射明确Unavailable）、cv-cat/Spider_XHS（笔记评论/千帆认证上下文）结论不变，本轮未重复执行。

### 专项结论

找到真实的单商品读取、店铺首页预览、Android店铺节点采样和评价图片操作代码，但未找到经证据验收、可直接接入现有浏览器后端的“全店枚举+买家评价正文”实现。该结论仅覆盖本轮检查的候选，不声称开源生态绝无其他方案。没有新的可执行浏览器全店分页或评价正文路径，因此本轮不把源码存在、模拟测试或字段名匹配包装成线上功能已经打通。Android UI路线只能作为后续独立可行性验证候选，尚需实际商品ID、完整性和评价正文验证；未擅自切换后端或开展开发。

## 2026-10-16 网页端继续验证（进行中，暂未全量验收）

用户明确暂不安装模拟器，集中网页全店商品列表与买家评价正文。本轮未安装Android环境、接付费API、开发监控或修改线上实现。账号额度已按服务端Asia/Shanghai自然日恢复，所有平台读取继续使用既有浏览器工厂、同一账号锁、AccessBudgetService和30秒限频。

### 新发现：公开网页入口支持shop_pc场景分页

在同一已验证公开端点mall.xiaohongshu.com/api/store/guide/components/general/h5中，使用前端脚本已出现的scene=shop_pc，而不是scene=shop_h5，即可返回20条真正新的商品与有效pageParam。未访问hawkeye.devops商家服务，没有附加新凭证或借用商家权限。请求格式：

```json
{"biz_identity":{"template_id":"shopH5Page","biz_type":"shop","scene":"shop_pc"},"component_param":{"mix_card_component_param":[{"sort_type":"sales_qty","page_param":{"page":0,"page_size":20}}]},"common_param":{"seller_id":"65263c56796c030001e298d4"}}
```

page从0开始；返回pageParam.page为请求page+1。外层success=true且data.result.success=true，DefaultMixCardComponent包含真实itemId/skuId、priceInfo、已售标签、上架时间和店铺ID。本轮与此前shop_h5固定6条的区别已实际验证：此前并非网页端完全没有分页能力，而是分享预览scene限制。旧结论“没有可用网页分页路径”在此被新证据更新。

正在依次读取到末页，以实际去重数量、末页hasMore及reported total核验完整性，尚未在本段记录完成。临时每页响应与进度为服务器/tmp/xhs-full-shop-validation/page-XX.json、summary.json。

新增商品抽查：第3页“【黑贝果】手工 红海 耳骨夹”，itemId=695e81c8bd92f500012d29c7、skuId=695e81c8bd92f500012d29c8。由实际skuId构建标准商品路径并在同一浏览器读取第一方toc，返回success=true、相同名称、黑贝果的店、到手价16.8、已售164。没有按ID末位猜测或本地哈希生成平台ID。

### 评价正文仍未通过

额外验证脚本已有定义的/api/store/review/<真实itemId>/product_detail/product_review，www域名返回HTTP200/code=-100/success=false/登录已过期。新增红海耳骨夹toc没有review/comment/evaluation模块。以上没有取得正文，也不能用商品ID已经有效推导认证问题已解决。仍未验证合法网页登录可提供该评价接口所需会话，不重复要求用户为未验证流程登录。

### 2026-10-16 全店分页完成验收，更新以上进行中状态

本轮“黑贝果的店”公开商品列表已经通过以下实测核验：

- 请求page=0..27，返回pageParam.page=1..28；前27页每页20件，第28页15件。
- 28个有商品的响应均报告total=555；累计555条，itemId去重555、skuId去重555，重复itemId为0；每条sellerId均与目标店铺一致。
- 第28页15件时hasMore仍为true，因此继续读取request page=28。第29个响应为空、empty=true、hasMore=false，pageParam.page=29。
- 空页的total重置为0，不能用它覆盖有商品页面的555，也不能据此说店铺为空。读取终止应结合当前页为空/hasMore以及此前有效总数，不能仅以某个非满页或末页hasMore推断。
- 第3页红海耳骨夹实际SKU详情匹配名称、店铺、到手价16.8和已售164。
- 第28页最后商品“【黑贝果】手工 血滴 戒指”，itemId=685ae50e7ec78a00017f445c、skuId=685ae50e7ec78a00017f445d。实际toc返回同名、黑贝果的店、到手价16.8。该详情已售字段为空，保留null，不转换为0，也不补造精确销量。

根因修正：此前shop_h5为分享预览场景，分页参数没有取得新商品；同一公开H5组件端点实际接受shop_pc场景并提供分页，不必访问内部hawkeye商家预览服务。此前将PC场景过早等同为只能使用内部服务，导致未及时验证这个组合，本轮已经用实际结果纠正。此结论仅对当前样本及接口返回目录成立，不保证所有店铺、后台商品、历史下架档案或未来接口版本均同样可用。

现有浏览器底层“全店公开商品发现→实际ID→商品详情”可行性已打通；尚未封装进线上agent_reach公开工具，也未修改部署。用户未要求本轮开发监控，因此没有实现监控或定时任务。

商品评价仍未通过：本轮新增product_detail/product_review与jpd/main/<itemId>/primary读取均返回-100登录已过期；两个商品的公开toc均未返回评价正文模块。真实ID已得到验证，当前认证上下文仍未被这两条读取路径接受。未将笔记评论/店铺评分冒充买家评价，未要求用户重复走尚未证明有效的网页登录流程。

可复核本地证据：

- outputs/xhs-browser-validation/shop-full-pagination.json：555个商品记录、实际itemId/skuId、标准商品URL、原始价格/标签、来源页码、逐页数量和详情抽样结果。
- outputs/xhs-browser-validation/shop-pagination-verification.json：完整性核验摘要。
- outputs/xhs-browser-validation/shop-terminal-page.json：空末页原始响应。

服务器临时目录/tmp/xhs-full-shop-validation保留各页原始公开响应与两条详情抽样响应；未保存或输出Cookie/Token。全部实测沿用原账号锁、额度与限频，没有安装模拟器、接入付费API或开发监控。


### 2026-10-16 买家评价认证专项：本机正常网页会话交叉验证

本轮继续只读核对官方登录前端和现成 Chrome 会话，没有退出账号、提交短信、读取或导出浏览器 Cookie，也没有新增凭证或修改生产工具。

1. 本机 Chrome 打开官方 /explore，侧栏显示“我”并指向已登录个人主页，页面正常加载推荐。此证据为既有正常网页登录会话，不是本轮重新扫码或短信登录的验收。
2. 同一 Chrome 地址栏读取 /api/store/review/6967abf98f0f9c0001c2ab3c/product_review?page=1&pageSize=10，实际可见原文为 {"code":-100,"success":false,"msg":"登录已过期","data":{}}。直接导航未附加商品页客户端生成的请求头；服务端已有页面客户端探针也同样失败，两者证据不能混为完全相同请求。
3. 同一会话打开商品 /goods-detail/6967abf98f0f9c0001c2ab3d，显示正确名称、到手价16.8、已售7304；完整可访问页面包括保障、店铺、图文详情、参数和购买按钮，没有买家评价正文或评价列表入口。
4. 访问商品前端提供的 /web-login?redirectPath=<完整官方商品URL>，最终跳转 oia.xiaohongshu.com/oia，页面显示“下载小红书 App”和“直接在 App 内打开”。未得到可操作的独立电商网页登录表单，未安装 App。

官方 fe-login 前端资源 index.223835ba.js、异步表单705.2f19b64a.js及44.42c31514.js提供进一步来源证据：短信登录逻辑使用 /fe_api/burdock/web/v2/login/by_phone、withCredentials=true；表单mounted会对合法回跳地址调用App启动器，启动器桌面分支也进入oia/下载流程。这解释了跳转，不能将其当作“自动登录成功”。表单成功处理只回到redirectPath，本轮没有找到将登录响应sid附加到回跳URL的逻辑。

结合之前商品页scheme.type=sid、App桥getUserInfo().sessionId/URL sid来源及请求Authorization和sid参数均缺失的探针：当前已验证路径的具体障碍是评价接口不接受现有网页认证上下文，客户端依赖的sid来源在普通浏览器中不可用。仍不能据此断言平台所有网页入口永远无法访问评价，也未验证全新短信登录后的服务端Set-Cookie内容；不要声称已证明网页登录绝对无法获得sid。

本轮没有取得任何真实买家评价正文。暂不要求用户重复扫码，因为现成登录态已经存在，且未发现可让用户完成的、已证明会补齐电商认证的网页登录入口。后续只有找到官方可用的电商网页登录/评价页面、或用户在普通浏览器确能看到该商品评价的具体入口，才值得继续验证会话差异。


### 2026-10-16 补查其他网页入口与新开源候选

用户明确排除跳App入口。本轮没有访问web-login/oia，也没有请求用户扫码、安装环境、接入第三方付费服务或开发监控。先执行Agent Reach doctor；check-update为v1.5.0最新。

新增候选源码核对：

- Amazinghorseli/RedNote-Insight，HEAD 522ed71ee4023cecbf408ce2d3195569053d0853。README英文称product reviews，但src/real_crawler.py:635起实际请求edith的/api/sns/web/v2/comment/page，参数note_id；后续DOM选择器也是笔记评论。它不是电商买家评价实现，不应因README措辞而误判。
- 0xmariowu/Autosearch，HEAD 00ad54c022a3effb9e848bb70b158b79584f6014。免费网页实现via_signsrv.py请求/api/sns/web/v1/search/notes；via_tikhub.py请求第三方/api/v1/xiaohongshu/web_v3/fetch_search_notes。未找到免费买家评价正文读取实现，不运行其凭证提取或登录代码。
- TikHub/TikHub-API-Python-SDK，src/tikhub/resources/xiaohongshu_app_v2.py:309-329确有get_product_reviews，接sku_id/page/sort_strategy_type/share_pics_only/from_page；其实现只是self._client._request调用第三方/api/v1/xiaohongshu/app_v2/get_product_reviews。没有开源小红书上游请求、网页登录态或sid获取方法，不能把SDK开源等同于可自建免费采集实现，未调用服务。

网页检索组合覆盖product-review/product_review/goods-review/goods-comment、中文买家评价/商品评论/评价列表和GitHub相关实现。没有获得有来源、可供现有浏览器实测的新评价页面URL。已保存的官方goods-detail主包与已取得动态chunk中，未发现独立买家评价路由；出现的评价读取方法仍为先前已测-100的store/review接口。本轮没有通过猜路径批量访问平台，也没有重复调用认证条件未变的失败接口。

结论：新增研究排除了两个容易因product reviews措辞误判的开源候选，并确认一个SDK只是服务封装；没有新增真实买家评价正文样本，不能宣布打通，也不能据检索无结果证明平台不存在其他网页入口。保留已验证555商品全店列表结论。当前具体缺口仍是有效评价页面/上游实现和被接受的电商会话，用户无需为这些候选配合登录。

来源：https://github.com/Amazinghorseli/RedNote-Insight/blob/522ed71ee4023cecbf408ce2d3195569053d0853/src/real_crawler.py；https://github.com/0xmariowu/Autosearch/tree/00ad54c022a3effb9e848bb70b158b79584f6014/autosearch/skills/channels/xiaohongshu/methods；https://github.com/TikHub/TikHub-API-Python-SDK/blob/main/src/tikhub/resources/xiaohongshu_app_v2.py。
