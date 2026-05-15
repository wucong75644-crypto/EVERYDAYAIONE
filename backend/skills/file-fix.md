# 文件修复规范

## 输入（系统自动提供）
- 原始文件路径
- L1 失败原因 + 错误详情
- 失败区域的原始数据样本

## 输出规范（必须严格遵守）
- Parquet 文件：输出到指定的 staging 路径
- .meta.json：必须包含 version / status / summary / schema / sample / stats / issues / cleaning
- status 必须如实填写 pass / warning / fail
- 不能修改原文件
- 不能跳过数据行（宁可标记异常也不能丢数据）
- 不能联网

## 约束
- 探索策略由你自己判断，系统不指定具体步骤
- 根据错误信息决定需要观察多少原始数据
- 修复后验证输出数据的完整性
