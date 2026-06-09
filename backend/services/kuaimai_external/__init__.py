"""
快麦 Web 外部数据接入模块

职责：
  - 通过 cookie 鉴权抓取快麦 Web 后台（智库 + viperp）数据
  - 多租户隔离（按 org_id）
  - 字段变化审计 + 企微告警
  - 店铺-运营自动映射

不要跟 services/kuaimai/ 混淆——那个是快麦官方 API（signature 鉴权）的客户端。
本模块是独立的 Web 后端抓取层，两套鉴权机制完全独立。

设计文档：见 migrations/114_kuaimai_external_data.sql 头部注释
"""
