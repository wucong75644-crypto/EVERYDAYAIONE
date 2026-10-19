---
name: production-diagnostics
description: 用户说“排查生产问题”“查生产日志”“检查线上服务状态”，或报告 EVERYDAYAIONE 线上故障时，使用 GitHub 连接器获取新采集的只读生产日志投影与服务状态并分析。不自动修复、部署或重启。
---

# 只读生产排障

用户上述请求授权查询和单次重跑固定的只读诊断 job；不用用户记作业编号，不再询问是否查询。
不授权修复代码、修改生产、部署、验收合并、回滚、重启、查数据库或获取原始敏感日志。

## 找到受控入口

读取仓库根目录 [诊断入口注册表](../../../.github/production-diagnostics.json)，
以及需要时的 [能力边界和运维说明](../../../docs/development/PRODUCTION_DIAGNOSTICS.md)。
本地文件不可用时，通过 GitHub 连接器 fetch_file 读取同一路径；使用云端启动规则提供的已审核规则 ref。
不要猜作业编号、要求用户提供编号，或用最新发布/验收 job 代替诊断 job。
注册表缺失或来源不明则报告缺口，不自行创建标签、增加权限或改网关。

## 获取新现场

优先使用本对话的 GitHub 连接器；按需发现 GitHub 工具。云端执行器/VPN 不可用时，连接器仍可能可用。
没有 GitHub 工具则明确报告连接器缺失，不用本机 SSH 代替云端能力，不读取生产凭据或要求上传私钥。

1. 用注册表 repository、seed_run_id 获取最新 attempt 的 job 列表，选择精确 job_name；
   核对 job 链接所属仓库/run、step_name，并读取 source_sha 对应的 workflow_path 确认是受控只读工作流。
   不运行通用发布、验收或旧凭据验证作业。
2. 记录请求时间、当前 attempt/job。若已有本次请求后启动的诊断在运行，等待该次；否则仅 rerun_workflow_job 一次。
   不因超时不确定再发起重复重跑；先查当前状态。等待总计至多 5 分钟，失败/超时/权限拒绝时报告具体阻塞。
3. 获取新 attempt 对应的新 job 日志，**只读这个诊断 job**，提取 result_marker 后的 JSON。
   核对 collected_at 更新、collection_complete、arbitrary_commands_denied、raw_messages_included=false、deployed=false、database_accessed=false。
   新 job 未完成、JSON 缺失或校验不符时不能报告查询成功，不能拿旧 attempt 冒充当前现场。
4. 同时检查每个 journal/nginx_files 的 query、parsing_complete、parse_errors、scan_truncated、important_records_omitted、oldest_record_at/newest_record_at。缺失备用文件 absent 可接受，但 access/error 各需至少一个实际文件成功；解析失败或采样截断必须说明影响，不能因 collection_complete=true 把有限样本称为全部日志。根据服务 ActiveState/SubState、MainPID/NRestarts/ExecMainStatus、日志级别/类别、source、异常类型、堆栈文件/行号、Nginx 状态码/固定 route_group/可得耗时分析。统计覆盖扫描范围，不等于最多100条返回样本；routine不是错误，WARNING不等于服务失败。
   有用户症状时结合相应生产版本代码验证原因；版本不明或证据不足时标明假设，不把 main 当作生产版本。
   用户没给具体现象时先完成上述查询并报告状态；仅在继续定位需要时询问复现现象/时间。

输出采集时间、关键服务/日志证据、可支持的结论和下一步，附本次新 job 链接。
不回显全部记录，不输出私钥、.env、Token、请求正文/参数或原始异常消息。

## 不可超出的边界

采集最近15分钟，每服务/文件最多扫描5000条或2MiB，优先返回错误、警告及最新上下文最多100条；范围是 backend、sync、wecom、conversation-actor、nginx journal，以及注册能力内固定 Nginx access/error 文件。
不读轮转历史或任意日志路径。safe 格式可有耗时，combined 没有时不推测。application_commit 用于查对应版本源码；没有时明确版本未知。unclassified、空日志或服务 active 都不证明没有错误；
没有异常详情时说明投影的定位限制，不能恢复或猜测被移除的内容。
排障完成后停止；不创建定时任务，不自动部署、重启或“清理工作树”。
用户随后明确要求修复时再进入故障修复技能，生产操作仍需对应授权。
