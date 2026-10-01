# Skill 快捷选择界面

2026-10-01，本地开发完成，用户随后明确授权“提交部署”，通过受控入口发布供生产测试；实际结果以本次 `RELEASE_RESULT` 为准，不合并 main、不清理工作树。按用户明确选定的 19:57 方案实现：快速选择、灰底描边标签、标签外的使用范围菜单。分支 `codex/task/20260928123557-skill-selector-ui`，基座 `34b7e11f8d945b641f27dc1fe9b10505d7429d47`。

## 行为

- Skill 工具栏入口及弹层锚点保持原位（top/start，8px 间距），列表限宽 380px，窄屏限制在视口内；沿用项目浅色、深色主题。移除顶部“仅本条／固定会话”页签。
- 列表只出现一份可见目录，每项显示名称、一行用途和选择入口；详情展示完整用途、版本、来源、推荐依据和不相关反馈。文件类型入口收在底部，仅使用用户明确选择。
- 选择后，在附件下方、文字输入上方单独显示灰底描边标签。× 仅移除对应 Skill；标签右侧独立提供“仅本条／会话固定”。选择或范围保存成功后焦点回到输入框。
- 默认仅本条，仍以原 `selected_skill` 字段提交，消费规则不变；不拼接消息正文、不更改工具授权或自动激活。会话固定沿用现有服务端 API 和固定版本规则，最多 4 项，不自动升级版本。
- 范围操作成功确认后才更新标签；网络失败结果不确定时保留选择并暂停发送，刷新绑定结果后再恢复。切回仅本条须确认原固定 revision 仍在当前目录中；不会静默换到新版本。不可用绑定仍可移除。
- 图片缩略图、文件卡片、预览、独立删除、正文和附件提交顺序沿用原实现。切换会话不显示上一会话的标签或迟到操作结果。
- 推荐只为当前目录中 ID 和 revision 均匹配的候选排序，最多 3 项；忽略错误、过期、禁用或无权限目录项。推荐关闭、空候选和失败不影响普通手动选择。

## 改动文件

| 文件（相对仓库根目录） | 改动 |
| --- | --- |
| `frontend/src/components/chat/input/SkillSelector.tsx` | 单列表弹层、原锚点、选中入口状态和主题 |
| `frontend/src/components/chat/input/SkillRecommendations.tsx` | 目录去重、推荐排序、详情/反馈、折叠文件类型 |
| `frontend/src/components/chat/input/SessionSkillBindings.tsx` | 灰底标签、独立范围菜单、移除与状态提示 |
| `frontend/src/components/chat/input/useSkillBindings.ts` | 共享绑定状态、确认后更新、失败核对、会话隔离 |
| `frontend/src/components/chat/input/InputControls.tsx` | 标签独立一行；范围写入未确认时保护发送 |
| `frontend/src/components/chat/input/__tests__/SkillSelector.test.tsx` | 选择入口、空目录、失败、新会话、迟到响应 |
| `frontend/src/components/chat/input/__tests__/SkillRecommendations.test.tsx` | 推荐隔离、目录权威性、开关、反馈、版本冲突 |
| `frontend/src/components/chat/input/__tests__/SessionSkillBindings.test.tsx` | 固定/取消、4 项上限、撤权、旧版本、网络结果不确定、切会话 |
| `frontend/src/components/chat/input/__tests__/InputControlsSkill.test.tsx` | 标签位置、焦点、附件共存、正文保留、发送保护 |
| `frontend/src/components/chat/input/__tests__/useInputSubmission.test.tsx` | 多种附件与 Skill 身份分别提交，附件顺序不变 |
| `docs/document/TECH_Skill聊天选择与反馈.md` | 同步标签与快捷选择行为 |
| `docs/document/TECH_Skill会话预绑定.md` | 同步范围入口与生产验收路径 |
| `docs/document/TECH_Skill可信上下文推荐.md` | 同步合并目录、详情原因与反馈入口 |
| 本文 | 当前实现、验证、生产步骤与回滚记录 |

## 验证结果

- 定向测试共 **87 项通过**：Skill 选择/推荐/会话标签/输入区/提交 5 个文件 61 项；原附件预览、上下文、适配、提交、上传状态及 binding 服务契约 6 个文件 26 项。最后的主题样式调整后再次执行输入区 6 项，全部通过。
- 改动的 5 个生产 TypeScript 文件定向 ESLint 通过；TypeScript 检查与两个 Skill UI 开关开启的生产构建通过。构建保留既有大 chunk 提示。
- 实际组件以独立本地模拟接口运行：核对浅色/深色、桌面和 368px 窄屏；图片、PDF、Excel 与标签同时显示；选择后焦点返回输入；固定到会话、切回仅本条成功。修复检查发现的深色弹层白底、边框及强调色对比问题。
- 未连接生产、未调用真实模型。浏览器检查使用模拟摘要与附件，不代替生产网络和模型激活验证。没有服务端、数据库、依赖或旧 Runtime 平台路径改动。

## 生产验证（用户再次明确“提交部署”后）

1. 通过 `deploy/release.sh` 发布当前任务候选；保持现有 Skill/推荐开关配置。本次无数据库迁移。
2. 打开 Skill：确认锚点不变、只有一份目录、名称及用途易读；详情可查看版本和建议原因，文件类型需明确选择。
3. 同时插入图片、PDF 或 Excel，选择 Skill：确认附件仍在上方，预览和独立移除正常；删除 Skill 不删除附件或文字。发送后确认附件顺序和内容无变化，仅本条标签清除。
4. 选择后从“仅本条”切到会话固定，确认刷新及后续消息保留；切回仅本条后下一次发送消费。换会话不串标签；不可用绑定可移除。断网保存时不得显示成功，刷新可确认最终结果。
5. 验证推荐关闭、无候选、禁用 Skill、异组织不可见及旧版本锁定；确认实际加载仍走原用户选择/显式 `activate_skill` 流程，不静默加载正文或扩大工具权限。核对既有 selected/not_relevant 审计反馈。

## 回滚

代码回滚参考 `34b7e11f8d945b641f27dc1fe9b10505d7429d47`。若发布后需要撤回，通过当前任务形成仅撤回上述前端改动的回退提交，再走受控发布；保留发布前同步的主线更新、原 Skill revision、会话绑定和审计，不回退数据库，不直接以旧基座覆盖后续已验收任务。
