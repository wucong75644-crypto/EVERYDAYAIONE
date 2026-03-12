
## basic

### 仓库查询
method: erp.warehouse.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
code
string
仓库编码
name
string
仓库名称
id
long
仓库ID

### 店铺查询
method: erp.shop.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
name
string
店铺名称
id
long
店铺编号
shortName
string
店铺简称
pageNo
int
页码（从1开始）
pageSize
int
分页大小

### 获取标签列表
method: erp.trade.query.tag.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
tagType
string
标签类型 0:自定义标签/系统标签 1:自定义异常/系统异常

### 客户基础资料查询
method: erp.query.customers.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
enableStatus
integer
客户状态 0.停用 1.正常
level
integer
客户等级 1.一级 2.二级 3.三级 4.四级 5.五级
nick
string
客户昵称
code
string
客户编码
name
string
客户名称
pageSize
integer
每页多少条【默认20】
pageNo
integer
第几页【默认1】

### 新增修改客户基本信息
method: erp.customer.create
params:
请求参数​
全部展开
参数名
类型
描述
必填
customerId
Long
客户id(修改的时候必传)
cmCode
string
客户编码 唯一
cmNick
string
客户昵称 唯一
cmName
string
客户公司全名 唯一
type
integer
客户类型
typeCode
integer
客户类型
typeMsg
String
客户类型
invoiceTitle
String
发票抬头
qqNumber
String
qq号
email
String
邮箱地址
fax
String
传真号
url
String
网址
taxNumber
String
税号
province
String
省
city
String
市
area
String
区
address
String
详细地址（街道）
bankName
String
开户行
bankAccount
String
开户账号
remark
String
备注
zipCode
String
邮编
importRowNum
Integer
导入的行号
paymentMethod
Integer
结算方式 0 现

### 分销商查询
method: erp.distributor.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
state
string
状态:1-查询所有状态；2-查询有效状态;
distributorCompanyIds
string
分销商编号(可批量查询，使用逗号分割，例如"5579,5579")
distributorName
string
分销商名称
pageNo
Integer
默认1
pageSize
Integer
默认20

## product

### 新增商品标签
method: erp.item.tag.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
name
string
标签名称
必填
styleType
int
标签样式 1-灰底黑字(默认), 2-绿底黑字 3-粉底黑字 4-紫底白字 5-蓝底白字 6-红底白字

### 查询商品标签
method: erp.item.tag.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
name
string
标签名称

### 设置商品标签
method: erp.item.tag.batch.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
tagNames
string
标签名称，多个以英文逗号隔开（与tagIds二选一）
tagIds
string
标签ID，多个以英文逗号隔开（与tagNames二选一）
operateType
int
1 增量修改 2覆盖修改 3删除标签
必填
itemInfoList
list
必填

### 新增商品分类
method: erp.item.seller.cat.add
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
新增商品标签
查询商品标签
设置商品标签
新增商品分类
查询商品分类信息
新增商品类目
查询商品类目信息
新增/修改商品品牌
查询商品品牌信息
商品对应关系查询
商品对应关系查询（按商品ID）
查询商品关联供应商信息V2
商品关联供应商信息更新V2
添加商品供应商关系V2
修改商品供应商关系V2
删除商品供应商关系V2
查询商品列表
查询单个商品明细
查询商品SKU信息
查询商品SKU列表V2
查询多个商品信息V2
商品多码查询
修改/新增商品V2
修改/新增普通商品
导入平台商品信息
更新商品历史成本价
查询商品出入库记录
查询库存状态
查询仓库及商品库存信息
修改实际库存
修改虚拟库存
批量修改虚拟库存
查询虚拟仓
商品类型转换（款维度）
商品历史成本价的查询
商品V1.0版本
交易
售后
仓储
采购
快麦通
API场景说明
API文档商品新增商品分类
新增商品分类
POST请求地址/router
系统相关界面​

"界面路径：【商品】----【商品分类】"

请求

### 查询商品分类信息
method: erp.item.seller.cat.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填

### 新增商品类目
method: erp.item.classify.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
classifyList
list
类目列表
必填

### 查询商品类目信息
method: item.classify.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填

### 新增修改商品品牌
method: item.brand.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
brandName
string
品牌名称
必填
id
long
品牌ID（修改时必填）

### 查询商品品牌信息
method: brand.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
pageNo
integer
页码 取值范围:大于零的整数。
1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；
40

### 商品对应关系查询
method: erp.item.outerid.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerIds
string
系统商家编码（最小粒度）,多个用逗号隔开（与numIidList二选一必填）
numIidList
string
平台商品id（最小粒度）,多个用逗号隔开（与outerIds二选一必填）
userId
long
店铺编码（若选择平台商品id，则与平台店铺id二选一必填）
taobaoId
long
平台店铺id（若选择平台商品id，则与店铺编码二选一必填）

### 商品对应关系查询按商品ID
method: erp.item.outerid.list.byitem.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
numIidList
string
平台商品id，多个用逗号隔开
必填
userId
long
店铺编码（与平台店铺id二选一必填）
必填
taobaoId
long
平台店铺id（与店铺编码二选一必填）
必填

### 查询商品关联供应商信息V2
method: erp.item.supplier.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
sysItemIds
string
系统主商品ID,多个用逗号隔开(最多支持20个)（参数必须四选一）
sysSkuIds
string
系统商品skuId,多个用逗号隔开(最多支持20个)
outerIds
string
平台商家编码,多个用逗号隔开(最多支持20个)
skuOuterIds
string
平台规格商家编码,多个用逗号隔开(最多支持20个)

### 商品关联供应商信息更新V2
method: erp.item.supplier.relevance
params:
请求参数​
全部展开
参数名
类型
描述
必填
itemId
long
平台主商品ID（与平台商家编码二选一）
outerId
string
平台商家编码（与平台主商品ID二选一）
skuOuterId
string
平台规格商家编码
skuId
long
平台skuID
suppliers
array
供应商信息列表

### 添加商品供应商关系V2
method: erp.item.supplier.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
itemId
long
平台主商品ID（与平台商家编码二选一）
outerId
string
平台商家编码（与平台主商品ID二选一）
skuOuterId
string
平台规格商家编码
skuId
long
规格商品ID
suppliers
array
供应商信息列表

### 修改商品供应商关系V2
method: erp.item.supplier.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
itemId
long
平台主商品ID（与平台商家编码二选一）
outerId
string
平台商家编码（与平台主商品ID二选一）
skuOuterId
string
平台规格商家编码
skuId
long
平台skuID
suppliers
array
供应商信息列表

### 删除商品供应商关系V2
method: erp.item.supplier.delete
params:
请求参数​
全部展开
参数名
类型
描述
必填
itemId
long
平台主商品的ID（与平台商家编码二选一）
outerId
string
商家编码，企业数据全局唯一 （和商品ID、规格ID，二选一
supplierIds
string
供应商ID（与供应商编码 二选一，ID优先），举例：[long,long]
supplierCodes
string
供应商编码 （与供应商ID 二选一，ID优先），举例：[string,string]
skuOuterId
string
规格商家编码
skuId
long
规格商品ID

### 查询商品列表
method: item.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
activeStatus
integer
数据的可用状态，0表示停用，1表示启用
startModified
string
起始的修改时间 , 示例：2000-01-01 00:00:00
endModified
string
结束的修改时间, 示例：2000-01-01 00:00:00
pageNo
integer
页码 取值范围:大于零的整数
1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；
40
type
integer
商品类型 0-普通商品（含组合、加工商品）且不是虚拟商品 1-套件（含纯套件、sku套件） 3-普通商品（含组合、加工商品）且是虚拟商品 6-仅加工商品 7-普通商品（不含组合、加工商品）且不是虚拟商品 8-非包材商品 9-仅组合商品 10-普通商品（含组合、加工、虚拟商品）
orderBy
string
排序方式 示例 modified:desc ， 选值：modified；
created:desc
whetherReturnPurchase
integer
是

### 查询单个商品明细
method: item.single.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerId
string
平台商家编码（同系统主商品ID二选一，优先取系统主商品ID）
sysItemId
long
系统主商品ID（同平台商家编码二选一，优先取系统主商品ID）
whetherReturnPurchase
integer
是否返回采购链接，0.否 1.是，默认不返回

### 查询商品SKU信息
method: erp.item.single.sku.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
sysSkuId
long
系统商品skuID（规格商家编码二选一，优先取系统商品skuID）
skuOuterId
string
规格商家编码（系统商品skuID二选一，优先取系统商品skuID）
whetherReturnPurchase
integer
是否返回采购链接，0.否 1.是，默认不返回
请求参数的逻辑​

当sysSkuId与skuOuterId都作为入参去查询： 1.只会走sysSkuId作为条件去查询返回结果,不会管skuOuterId的参数值

### 查询商品SKU列表V2
method: erp.item.sku.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerId
string
商家编码 (同系统主商品ID二选一，优先取系统主商品ID )
sysItemId
long
系统主商品ID (同商家编码二选一，优先取系统主商品ID )
whetherReturnPurchase
integer
是否返回采购链接，0.否 1.是，默认不返回

### 查询多个商品信息V2
method: erp.item.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
sysItemIds
string
系统主商品ID，多个用逗号隔开，最多支持20个(同平台商家编码二选一，优先取系统主商品ID)
outerIds
string
平台商家编码，多个用逗号隔开，最多支持20个(同系统主商品ID二选一，优先取系统主商品ID)
returnSkus
int
是否返回SKU信息 0:否 1:是 默认 1
必填

### 商品多码查询
method: erp.item.multicode.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
code
string
主商家编码/规格商家编码/商品条码

### 修改新增商品V2
method: erp.item.general.addorupdate
params:
请求参数​
全部展开
参数名
类型
描述
必填
itemRequestType
integer
商品类型（0-普通商品，1-加工 2-套件 3-组合），除了普通商品类型，其他类型的商品都需要填写“单品”字段的值。注意：新增时为必填，修改时为非必填
outerId
string
平台商家编码（主商家编码）
必填
title
string
商品名称
必填
skus
array
sku 列表（含规格时必填）
shortTitle
string
商品简称
barcode
string
商品条形码（当用户开启一品双码时，企业数据全局唯一，不能和商家编码重复）
id
long
系统主商品ID （有值表示修改已存在的商品）
catId
sting
商品类目属性ID
itemCatName
string
商品类目名称，各类目层级中如果没有重复可以只传商品类目名称； 支持指定多级类目，类目层级格式：["根类目","一级类目","二级类目","三级类目"]
sellerCids
string
商品分类ID
itemCategoryNames
string
商品分类名称，各分类层级中如果没有重复可以只传商品

### 修改新增普通商品
method: item.general.addorupdate
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
outerId
string
平台商家编码（主商家编码）
必填
id
long
系统主商品ID（有值表示修改已存在的商品）
title
string
商品名称
必填
skus
array
sku 列表（含规格时必填）
shortTitle
string
商品简称
barcode
string
条形码（当用户开启一品双码时，企业数据全局唯一，不能和商家编码重复）
catId
string
商品类目属性ID
sellerCids
string
商品分类
picPath
string
预览图片URL
brandId
long
品牌ID (通过品牌接口去查询)
brand
string
品牌名称
purchasePrice
double
成本价（单位：元），示例：100.99
sellingPrice
double
销售价（单位：元），示例：100.99
weight
double
商品重量（单位：Kg）， 示例：0.05
remark
string
商品备注
washLabels
arrayString
水洗唛列表
productFea

### 导入平台商品信息
method: erp.tb.item.import
params:
请求参数​
全部展开
参数名
类型
描述
必填
requestId
string
每次请求访问的唯一标识
必填
userId
long
店铺id
必填
tbItems
string
需要导入的平台商品
必填

### 更新商品历史成本价
method: erp.item.history.price.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerId
string
商家编码/规格商家编码，（同主商品的ID二选一，优先商家编码）
必填
sysItemId
long
主商品的ID，（同商家编码二选一，优先商家编码）
必填
sysSkuId
long
规格ID（同商家编码二选一，优先商家编码）
必填
price
string
成本价（单位：元）
必填
date
date
成本价更新时间，如未指定则会默认为当前时间 格式yyyy-MM-dd HH:mm:ss

### 查询商品出入库记录
method: erp.item.stock.in.out.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
orderType
integer
单据类型 0-库存调整
系统调整库存
outerId
string
平台商家编码
warehouseId
long
仓库ID
operateTimeBegin
string
操作开始时间 格式为：yyyy-MM-dd HH:mm:ss
operateTimeEnd
string
操作结束时间 格式为：yyyy-MM-dd HH:mm:ss
pageSize
integer
分页数量
必填
pageNo
integer
当前页
必填
orderNumber
string
单据编号

### 查询库存状态
method: stock.api.status.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
itemType
integer
商品类型；0-组合商品（仅单品），1-套件，2-组合装，3-加工
0
mainOuterId
string
主商家编码（与规格商家编码二选一，多个逗号隔开）
skuOuterId
string
规格商家编码（与主商家编码二选一，多个逗号隔开）
stockStatuses
integer
库存状态(1-正常，2-警戒，3-无货，4-超卖，6-有货)
warehouseId
long
仓库ID
pageSize
integer
分页数量，最大100条
pageNo
integer
当前页
created
string
商品创建时间，格式:yyyy-MM-dd HH:mm:ss
modified
string
商品更新时间，格式:yyyy-MM-dd HH:mm:ss
startStockModified
string
库存修改开始时间，格式:yyyy-MM-dd HH:mm:ss
endStockModified
string
库存修改结束时间，格式:yyyy-MM-dd HH:mm:ss
brand

### 查询仓库及商品库存信息
method: erp.item.warehouse.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerId
string
平台商家编码（与平台规格商家编码二选一，二者都有值，则按and条件匹配）
skuOuterId
string
平台规格商家编码（与平台商家编码二选一，二者都有值，则按and条件匹配）
pageNo
integer
页码
必填
pageSize
integer
每页数量，默认每页pageSize为20
请求参数的逻辑​

当outerId与skuOuterId都作为入参，则按两个都完全相等匹配的条件去查询： 1.若outerId存在，skuOuterId也存在，则后台返回结果 2.若outerId存在，而skuOuterId不存在，则后台不返回结果 3.若outerId不存在，而skuOuterId存在，则后台不返回结果

### 修改实际库存
method: erp.item.available.stock.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
warehouseId
long
仓库ID
必填
stockNum
long
实际库存数量
实际库存数量, 盘盈库存, 盘亏库存 三个参数中选择一个
overStockNum
long
盘盈库存，在实际库存数量基础上增加
实际库存数量, 盘盈库存, 盘亏库存 三个参数中选择一个
underStockNum
long
盘亏库存，在实际库存数量基础上减少
实际库存数量, 盘盈库存, 盘亏库存 三个参数中选择一个
skuOuterIds
string
平台规格商家编码，多个逗号隔开(同平台商家编码二选一，最多只能传20个)
outerIds
string
平台商家编码，多个逗号隔开(同平台规格商家编码二选一,最多只能传20个)

### 修改虚拟库存
method: erp.item.virtual.stock.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
itemIds
array
商品信息，纯商品sysSkuId传0，(同平台规格商家编码二选一) 格式[{"sysItemId":295711729489408,"sysSkuId":123},{"sysItemId":282263597731328,"sysSkuId":0}]
warehouseId
long
仓库ID
必填
stockNum
long
虚拟库存数量
必填
skuOuterIds
string
平台规格商家编码，多个逗号隔开(同商品信息二选一，最多只能传20个)
outerIds
string
平台商家编码，多个逗号隔开(最多只能传20个)

### 批量修改虚拟库存
method: erp.item.virtual.stock.updateV2
params:
请求参数​
全部展开
参数名
类型
描述
必填
stockInfo
arrayString
库存信息

### 查询虚拟仓
method: erp.virtual.warehouse.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
name
string
虚拟仓名称
pageNo
int
页码（从1开始）
pageSize
int
分页大小（最大500）
必填

### 商品类型转换
method: erp.item.type.change
params:
请求参数​
全部展开
参数名
类型
描述
必填
changeType
integer
0 默认值 不转 1 普通/组合/加工转套件 （款维度）2 普通/组合/加工转套件(含sku套件) 3. 普通/套件转组合（款维度） 5.普通/套件转加工（款维度） 20.其他类型转普通（款维度）
必填
itemRequestType
integer
商品类型（0-普通商品，1-加工 2-套件 3-组合），除了普通商品类型，其他类型的商品都需要填写“单品”字段的值。注意：新增时为必填，修改时为非必填
outerId
string
平台商家编码（主商家编码）
必填
title
string
商品名称
必填
skus
array
sku 列表（含规格时必填）
shortTitle
string
商品简称
barcode
string
商品条形码（当用户开启一品双码时，企业数据全局唯一，不能和商家编码重复）
id
long
系统主商品ID （有值表示修改已存在的商品）
catId
sting
商品类目属性ID
itemCatName
string
商品类目名称，各类目层级中如果没有重复可以只传商品类目名称；

### 商品历史成本价的查询
method: erp.item.history.cost.price.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
sysItemId
long
系统商品ID
必填
sysSkuId
long
系统规格ID 如果是纯商品 规格id为0
必填
startTime
string
开始时间 格式 yyyy-MM-dd HH:mm:ss
endTime
string
结束时间 格式 yyyy-MM-dd HH:mm:ss
warehouseIdList
array
仓库ID列表 示例：[1，2]

## trade

### 订单查询
method: erp.trade.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sid
string
系统订单号，多个逗号隔开
tid
string
平台订单号，多个逗号隔开
timeType
string
查询的时间类型：[created:下单时间]--[pay_time:付款时间]--[consign_time:发货时间]--[audit_time:审核时间]--[upd_time:修改时间]；与startTime、endTime同时使用，时间跨度建议不超过一天。为空时，默认为修改时间 ，归档订单(queryType参数传1时)不支持audit_time, upd_time
startTime
string
起始时间:格式yyyy-MM-dd HH:mm:ss，与timeType、endTime同时使用，时间跨度建议不超过一天
endTime
string
截止时间:格式yyyy-MM-dd HH:mm:ss，与timeType、startTime同时使用，时间跨度建议不超过一天
userIds
string
店铺ID，多个逗号隔开
pageSize
integer
每页多少条，最大支持200，不能小于2

### 创建系统手工单
method: erp.trade.create
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
userId
long
店铺编号
必填
warehouseId
long
仓库ID
必填
orders
array
订单商品明细集合
必填
postFee
string
运费（单位：元）
payment
string
实付金额（例如1.00）（单位：元）
必填
tid
string
平台订单号(相同tid只能推送一次)
receiverName
string
收件人姓名
必填
receiverState
string
收件省份
必填
receiverCity
string
收件市
必填
receiverDistrict
string
收件区县
必填
receiverStreet
string
收件街道
必填
receiverAddress
string
收件详细地址
必填
receiverZip
string
收件邮编
receiverMobile
string
收件人手机号（收件人手机号和收件人固话至少需要设置一个）
receiverPhone
string
收件人固话（收件人手机号和收件人固话至少需要设置一个）
mobileT

### 创建自建平台订单
method: erp.trade.create.new
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
tid
string
平台订单号
必填
userId
long
店铺编号
必填
status
string
订单平台状态 WAIT_BUYER_PAY(等待买家付款)，WAIT_SELLER_SEND_GOODS(等待卖家发货,即:买家已付款) ，WAIT_BUYER_CONFIRM_GOODS(等待买家确认收货,即:卖家已发货) ，TRADE_FINISHED(交易成功) ，TRADE_CLOSED(付款以后用户退款成功，交易自动关闭)
必填
receiverName
string
收件人姓名
必填
receiverState
string
收件省份
必填
receiverCity
string
收件市
必填
receiverDistrict
string
收件区县
必填
receiverStreet
string
收件街道
必填
receiverAddress
string
收件详细地址
必填
receiverZip
string
收件邮编
receiverMobile
string
收件人手机号
必填
receiverPho

### 销售出库查询
method: erp.trade.outstock.simple.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sid
string
系统订单号，多个逗号隔开
tid
string
平台订单号，多个逗号隔开
timeType
string
查询的时间类型：[created:下单时间]--[pay_time:付款时间]--[consign_time:发货时间]--[audit_time:审核时间]--[upd_time:修改时间]；与startTime、endTime同时使用，时间跨度建议不超过一天。为空时，默认为修改时间 ，归档订单(queryType参数传1时)不支持audit_time, upd_time
startTime
string
起始时间:格式yyyy-MM-dd HH:mm:ss，与timeType、endTime同时使用，时间跨度建议不超过一天
endTime
string
截止时间:格式yyyy-MM-dd HH:mm:ss，与timeType、startTime同时使用，时间跨度建议不超过一天
userIds
string
店铺ID
pageSize
integer
每页多少条，最大支持200，不能小于20
20
pa

### 订单操作日志
method: erp.trade.trace.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
sids
string
系统订单号,多个逗号隔开,最多支持200组
operators
string
操作人,多个逗号隔开,最多支持20组
action
string
操作类型(自定义文本)
content
string
操作备注
operateTimeStart
long
操作起始时间（时间戳）
operateTimeEnd
long
操作截止时间（时间戳）
pageNo
integer
页码
pageSize
integer
每页条数,最大支持200条,默认50条

### 修改订单收货地址
method: erp.trade.receiver.info.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
sid
long
系统编号(平台单号二选一)
tid
string
平台单号(系统编号二选一)
receiverName
string
收货人姓名
必填
receiverMobile
string
收货人手机号码(收货人电话二选一)
receiverPhone
string
收货人电话(收货人手机号码二选一)
receiverZip
string
收件邮编
receiverState
string
收件省份
必填
receiverCity
string
收件市
必填
receiverDistrict
string
收件区县
必填
receiverAddress
string
收货详细地址
必填

### 修改订单卖家备注与旗帜
method: erp.trade.seller.memo.upload
params:
请求参数​
全部展开
参数名
类型
描述
必填
sid
long
系统编号(平台订单号二选一)
tid
string
平台订单号(系统编号二选一)
sellerMemo
string
卖家备注(旗帜二选一必填)
flag
long
旗帜(卖家备注二选一必填) 系统旗帜 0:灰, 1:红, 2:黄, 3:绿, 4:蓝, 5:紫, 6:橙, 7:浅蓝, 8:浅粉, 9:深绿, 10:桃红

### 批量修改订单标签
method: erp.trade.tag.batch.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
tids
string
平台id,多个用逗号分隔，最多支持20组！(与sids 二选一必填)
必填
tagIds
string
标签id,多个用逗号分隔(通过erp.trade.query.tag.list获取)
必填
type
integer
类型 1=新增，2=删除
必填
sids
string
系统订单id,多个用逗号分隔，最多支持20组！(与tids 二选一必填)
必填

### 订单作废接口
method: erp.trade.cancel
params:
请求参数​
全部展开
参数名
类型
描述
必填
sids
string
系统订单号,多个逗号隔开，选填:平台订单号二选一，优先系统订单号
tids
string
平台订单号,多个逗号隔开，选填:系统订单号二选一，优先系统订单号

### 订单发货拦截接口
method: erp.trade.send.goods.intercept
params:
请求参数​
全部展开
参数名
类型
描述
必填
sids
string
系统订单号,多个逗号隔开
必填

### 多快递单号查询
method: erp.trade.multi.packs.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
sid
long
系统单号(系统单号或运单号二选一)
outSid
string
快递单号(系统单号或运单号二选一)

### 上传发货
method: erp.trade.consign
params:
请求参数​
全部展开
参数名
类型
描述
必填
sids
string
系统订单号,多个逗号隔开，选填:平台订单号二选一，优先系统订单号
必填
consignType
string
发货类型 DUMMY："无需物流发货",OFFLINE: "自己联系物流发货",ONLINE:"在线订单发货",ONLINE_CONFIRM:"确认发货通知",RESEND:"修改物流公司和运单号并重新发货" 不填默认为OFFLINE
tid
string
平台订单号，选填:系统订单号二选一，优先系统订单号
expressCode
string
快递公司编码
outSid
string
运单号
operateType
string
操作类型，不填operateType但是填了运单号和快递公司编码会仅上传平台，如果consignType传RESEND则不用传operateType； 1:仅系统发货 ；2:系统发货并上传平台 ,6 系统发货 并上传 且会返回上传失败的信息
非必填

### 上传备注与旗帜
method: erp.trade.upload.memo.flag
params:
请求参数​
全部展开
参数名
类型
描述
必填
memo
string
备注
必填
flag
long
旗帜 系统旗帜 0:灰, 1:红, 2:黄, 3:绿, 4:蓝, 5:紫, 6:橙, 7:浅蓝, 8:浅粉, 9:深绿, 10:桃红
必填
userId
long
店铺编号
必填
tid
string
平台订单号
必填

### 包装验货
method: erp.trade.pack
params:
请求参数​
全部展开
参数名
类型
描述
必填
uniqueCodes
string
唯一码信息,逗号隔开
sid
string
系统订单号
orderIdIdentCodes
string
子订单与识别码的对应参数
orderScanInfos
string
包装验货扫描信息(json字符串)例如:[{"scanCode":"abc", "num":1},{"scanCode":"def", "num":3}]
canForce
boolean
是否支持强制 支持true/不支持false
必填
outSid
string
运单号

### 波次手动拣选
method: erp.trade.wave.pick.hand
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
波次ID，可多个，逗号拼接，最大100个

### 订单唯一码收货
method: erp.trade.unique.code.receive
params:
请求参数​
全部展开
参数名
类型
描述
必填
uniqueCode
string
唯一码号
必填

### 查询唯一码
method: erp.item.unique.code.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sids
string
系统订单号,多个逗号隔开，最多支持20组
shortIds
string
内部单号,多个逗号隔开，最多支持20组
afterSaleOrderCodes
string
售后单号,多个逗号隔开，最多支持20组
uniqueCodes
string
唯一码号,多个逗号隔开，最多支持20组
updateStart
string
波次唯一码更新时间-起始 格式:yyyy-MM-dd HH:mm:ss
updateEnd
string
波次唯一码更新时间-截止 格式:yyyy-MM-dd HH:mm:ss
consignStart
string
订单发货时间-起始 格式:yyyy-MM-dd HH:mm:ss
consignEnd
string
订单发货时间-截止 格式:yyyy-MM-dd HH:mm:ss
receiveTimeStart
string
波次唯一码更新时间-起始 格式:yyyy-MM-dd HH:mm:ss
receiveTimeEnd
string
波次唯一码更新时间-截止 格式:yyyy-MM

### 波次信息查询
method: erp.trade.waves.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
int
页码
pageSize
int
页数, 默认20
start
string
创建开始时间 格式:yyyy-MM-dd HH:mm:ss
end
string
创建结束时间 格式:yyyy-MM-dd HH:mm:ss
pickStartTime
string
拣选完成开始时间 格式:yyyy-MM-dd HH:mm:ss
pickEndTime
string
拣选完成结束时间 格式:yyyy-MM-dd HH:mm:ss
status
int
波次状态 1 未完成 3已完成 4已取消

### 波次分拣信息查询
method: erp.trade.wave.sorting.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
waveId
long
波次号
必填

### 波次播种回传
method: erp.trade.wave.seed
params:
请求参数​
全部展开
参数名
类型
描述
必填
waveId
long
波次号
必填
list
string
播种详情json字符串，注意字符串里面双引号要加反斜杠转义符 见示例
必填
jobNum
string
员工工号

### 新增商品唯一码
method: erp.item.unique.code.generate
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
generateParams
string
入参json字符串
必填

### 校验波次唯一码
method: erp.wave.unique.code.validate
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
uniqueCodes
string
唯一码号,多个逗号隔开，最多支持20组（时间或者唯一码 其中一项必填）
startTime
string
起始时间:格式yyyy-MM-dd HH:mm:ss，与endTime同时使用，时间跨度建议小时数不超过24
endTime
string
截止时间:格式yyyy-MM-dd HH:mm:ss，与startTime同时使用，时间跨度建议小时数不超过24
pageNo
Integer
页码
pageSize
Integer
每页多少条，最大支持500，不能小于50

### 商品唯一码更新
method: erp.item.unique.code.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
generateParams
string
入参json字符串
必填

### 修改订单仓库
method: erp.trade.change.warehouse
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sids
string
系统订单号,多个逗号隔开
warehouseId
long
仓库ID
force
boolean
库存不足时是否强制换仓库

### 更新订单物流模板
method: erp.trade.logistics.template.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sids
string
系统订单号,多个逗号隔开
logisticsCompanyId
long
用户物流公司ID

### 获取物流单号
method: erp.trade.waybill.code.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sids
string
系统订单号,多个逗号隔开

### 用户物流公司列表
method: erp.trade.logistics.company.user.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
warehouseId
long
仓库ID

### 用户物流模板列表
method: erp.trade.logistics.template.user.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
warehouseId
long
仓库ID
必填

### 即入即出匹配
method: erp.fast.stock.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
outerId
string
商家编码(支持售后唯一码)
num
Integer
商品数量
type
Integer
null或0-售后入仓 1-采购入仓
needGoodsSection
boolean
是否需要商品关联货位
warehouseCode
string
仓库编码

### 修改订单商品备注
method: erp.trade.order.remark.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
orderId
long
订单商品行ID
remark
string
备注

### 批量修改订单商品备注
method: erp.trade.order.remark.batchUpdate
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sid
long
平台单号和系统订单号二选一
tid
long
平台单号和系统订单号二选一
orders
array
订单行信息

### 订单挂起
method: erp.trade.halt
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sids
string
系统订单号,多个用逗号分隔，最多支持20组
系统订单号和平台订单号二选一
tids
string
平台订单号,多个用逗号分隔，最多支持20组
系统订单号和平台订单号二选一
autoUnHalt
int
是否开启自动解挂 0不开启 1开启 不开启则仅挂起
必填
unHaltType
int
解挂依据 0按订单挂起时间 1按承诺发货与揽收时间，如果是否开启自动解挂时间传值为1，那么该字段必填
haltTimeUnit
int
挂起时长单位 时间单位 1：分 2：小时 3：天 4：月 5：年，如果是否开启自动解挂时间传值为1，那么该字段必填
haltTimeVal
int
挂起时长 具体的时长，最大不超过999，如果是否开启自动解挂时间传值为1，那么该字段必填
isUrgent
int
自动解除后更新为加急订单 0不更新 1更新，如果是否开启自动解挂时间传值为1，那么该字段必填

### 订单解挂
method: erp.trade.unhalt
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
sids
string
系统订单号,多个用逗号分隔，最多支持20组
系统订单号和平台订单号二选一
tids
string
平台订单号,多个用逗号分隔，最多支持20组
系统订单号和平台订单号二选一

### 销售出库单查询
method: erp.wave.logistics.order.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
默认值
userIds
string
店铺ID列表，多个用逗号分隔
否
statusList
string
销售出库单状态列表，多个用逗号分隔，10-待处理，20-预处理完成，30-发货中，50-已发货，70-已关闭，90-已作废
否
timeType
integer
时间类型，1-创建时间，2-发货时间，3-付款时间，4-下单时间，5-承诺时间，6-打印时间 系统单号、内部单号、平台单号、物流单号、波次号为空时，必须设置时间范围查询
否
timeBegin
long
开始时间（时间戳，单位：毫秒）系统单号、内部单号、平台单号、物流单号、波次号为空时，必须设置时间范围查询
否
timeEnd
long
结束时间（时间戳，单位：毫秒）系统单号、内部单号、平台单号、物流单号、波次号为空时，必须设置时间范围查询
否
sids
string
系统单号列表，多个用逗号分隔，最多50个
否
shortIds
string
内部单号列表，多个用逗号分隔，最多50个
否
tids
string
平台单号列表，多个用逗号分隔，最多50个
否
outSids

## aftersales

### 创建售后工单
method: erp.aftersale.workorder.create
params:
请求参数​
全部展开
参数名
类型
描述
必填
refundWarehouseId
long
退货仓库id
必填
reason
string
售后原因 ，可自定义（例如:退运费、收到商品破损、商品错发/漏发、商品需要维修、发票问题、收到商品与描述不符、我不想要了、商品质量问题、未按约定时间发货)
必填
hasOwner
int
是否有主 1 有主 2 无主 默认为2
sid
long
系统订单编号(系统销售订单号 1个订单号对应多个工单号),hasOwner为1时必填
必填
tid
string
平台订单号
userId
long
店铺编码(填写了sid或者tid时店铺编码必传)
afterSaleType
int
售后类型 1:已发货仅退款；2:退货；3:补发；4:换货；5:未发货仅退款；7:拒收退货；8:档口退货；9:维修
必填
reissueOrRefundList
array
补发或退货列表（类型为换货/补发需要同时传入退货商品和换货商品信息）
必填
platformId
string
平台工单号
refusePicAddress
string
拒绝退货时的上传图片路径
rem

### 售后工单查询
method: erp.aftersale.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
userIds
string
店铺ID
warehouseIds
string
仓库ID
tid
string
平台订单号
id
long
售后工单号
pageSize
integer
pageSize（不传默认为1，范围是1-200条）
pageNo
integer
页码
asVersion
integer
售后版本 1：旧版， 2：新版（默认）
startModified
string
起始修改时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
结束修改时间 格式:yyyy-MM-dd HH:mm:ss
startApplyTime
string
起始申请时间 格式:yyyy-MM-dd HH:mm:ss
endApplyTime
string
结束申请时间 格式:yyyy-MM-dd HH:mm:ss
afterSaleType
string
售后类型 0:其他；1:已发货仅退款；2:退货；3:补发；4:换货；5:未发货仅退款；7:拒收退货；8:档口退货；9:维修
suiteSingle
int

### 作废售后工单
method: erp.aftersale.workorder.cancel
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后作废售后工单
作废售后工单
POST请求地址/router
请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
appKe

### 解决售后工单
method: erp.aftersale.workorder.resolve
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后解决售后工单
解决售后工单
POST请求地址/router
系统关联页面​

【售后】--- 【解决工单】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
meth

### 售后工单退货入仓
method: erp.aftersale.workorder.goods.received
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后售后工单退货入仓
售后工单退货入仓
POST请求地址/router
系统关联页面​

【售后】--- 【退货登记】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述


### 更新售后工单标记
method: erp.aftersale.workorder.tag.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
type
string
类型 1=新增，2=删除 ,3=覆盖
必填
workOrderId
long
售后工单ID
必填
tagNames
string
售后工单标记名称 ,多个以“,”隔开( 与tagIds 二选一 必填)
必填
tagIds
string
售后工单标记ID ,多个以“,”隔开( 与tagNames 二选一 必填)
必填

### 更新售后单售后说明
method: erp.aftersale.workorder.explains.update
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后更新售后单售后说明
更新售后单售后说明
POST请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
string
multipart/form-data
必填
API接口地址​
全部展开
参数名
类型
描述
必填
method
string
erp.aftersale.workorder.explains.update
必填

### 批量修改售后类型
method: erp.aftersale.workorder.batchChangeType
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后批量修改售后类型
批量修改售后类型
POST请求地址/router
请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
a

### 销退入库单查询
method: erp.aftersale.refund.warehouse.query
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后销退入库单查询
销退入库单查询
POST请求地址/router
系统相关界面​

【售后】---【销退入库】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
met

### 登记补款查询
method: erp.aftersale.replenish.list.query
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后登记补款查询
登记补款查询
POST请求地址/router
系统相关界面​

【售后】---【登记补款】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
metho

### 维修单列表查询
method: erp.aftersale.repair.list.query
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后维修单列表查询
维修单列表查询
POST请求地址/router
系统相关界面​

【售后】---【维修单管理】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
me

### 维修单详情查询
method: erp.aftersale.repair.detail.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
repairOrderNum
维修单号
维修单号

### 维修单处理
method: erp.aftersale.repair.order.process
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后维修单处理
维修单处理
POST请求地址/router
系统相关界面​

【售后】---【维修单管理】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method

### 维修单修改费用
method: erp.aftersale.repair.order.edit.repairMoney
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后维修单修改费用
维修单修改费用
POST请求地址/router
系统相关界面​

【售后】---【维修单管理】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
me

### 维修单付款
method: erp.aftersale.repair.order.pay
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后维修单付款
维修单付款
POST请求地址/router
系统相关界面​

【售后】---【维修单管理】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method

### 修改售后工单备注
method: erp.aftersale.workorder.remark.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
remark
string
备注信息
必填
workOrderId
long
工单ID不能为空
必填

### 售后日志查询
method: erp.aftersale.operate.log.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
workOrderId
long
售后工单号

### 更新工单平台实退金额
method: erp.aftersale.update.platformRefundMoney
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后更新工单平台实退金额
更新工单平台实退金额
POST请求地址/router
请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口

### 更新工单退货快递信息
method: erp.aftersale.update.express
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
创建售后工单
售后工单查询
作废售后工单
解决售后工单
售后工单退货入仓
更新售后工单标记
更新售后单售后说明
批量修改售后类型
销退入库单查询
登记补款查询
维修单列表查询
维修单详情查询
维修单处理
维修单修改费用
维修单付款
修改售后工单备注
售后日志查询
更新工单平台实退金额
更新工单退货快递信息
仓储
采购
快麦通
API场景说明
API文档售后更新工单退货快递信息
更新工单退货快递信息
POST请求地址/router
请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口

## warehouse

### 查询调拨单列表
method: erp.allocate.task.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
status
String
状态 REATED：待审核 OUTING：出库中 AUDITED：已审核 ALLOCATE：调拨中 ALLOCATE_IN：调入中 FINISHED: 已完成 CANCELED：作废 MIXED：(待审核和出库中并且已拣选完成)
code
String
业务单据号
startModified
String
修改时间 开始 code为空时必填
必填
endModified
String
修改时间 结束
labelName
String
标签
pageNo
int
pageSize
int
不能为空或者小于20

### 新增完成的调拨单
method: allocate.task.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
outWarehouseCode
long
调出仓库外部编码
必填
inWarehouseCode
long
调入仓库外部编码
必填
details
array
调拨单明细
必填
remark
string
备注

### 新增调拨单
method: allocate.task.status.create
params:
请求参数​
全部展开
参数名
类型
描述
必填
outWarehouseCode
long
调出仓库外部编码
必填
inWarehouseCode
long
调入仓库外部编码
必填
outLocation
int
出库位置 货位/入库暂存区/销退暂存区/次品暂存区/通用暂存区/拣选暂存区/补货暂存区 1/2/3/4/5/6/8
必填
status
string
调拨状态 CREATED：待审核,AUDITED：待调拨
必填
platformOrderNumber
string
外部单号
details
array
调拨单明细
必填
remark
string
备注

### 查询调拨单明细
method: erp.allocate.task.detail.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
code
String
业务单据号
startModified
String
修改时间 开始 code为空时必填
必填
endModified
String
修改时间 结束
labelName
String
标签
pageNo
int
pageSize
int
不能为空或者小于20

### 查询调拨入库单列表
method: allocate.in.task.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss（单据修改时间）
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss（单据修改时间）
status
string
入库状态 单据状态 CREATED :已创建，ALLOCATE_OUT:收货完成，SHELVED：已上架，FINISHED :已完成，CANCELED :作废
code
string
单据号(收货单号) 如:DR2022432423423
customType
string
单据类型（调拨入库拒收类型：ALLOCATE_REFUND）

### 查询调拨入库单明细
method: allocate.in.task.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
示例：调拨入库单id
必填

### 调拨入库单收货
method: allocate.in.task.receive
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
示例：调拨入库单id
必填
weSourceId
String
外部单号
非必填
receiveDetails
String
示例：传入JSON数组 示例： [ { "outerId": "U", // 商家编码，必填 "goodNum": "1", // 良品数，良品数次品数二选一 "badNum": 0, // 次品数 "batchNo": "" // 批次号 ，非必填 } ]
必填

### 查询调拨出库单列表
method: allocate.out.task.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss
status
string
出库状态 CREATED,OUTING未出库，FINISHED已出库，CANCELED已完成
code
string
单据号 如:DC3911498093140480
timeType
string
查询时间类型 如: "create" 创建时间查询 "out" 出库时间查询 “gm_modified” 修改时间查询 不传默认根据修改时间查询

### 查询调拨出库单明细
method: allocate.out.task.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
调拨出库单id
必填

### 调拨出库单直接出库
method: erp.allocate.out.task.out
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
调拨出库id
必填
prSourceId
String
外部单号
非必填
details
jsonString
出库明细
必填

### 查询其他入库单
method: other.in.order.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部编号
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss（修改时间为准）
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss（修改时间为准）
customTypeStr
string
入库单类型，支持多个拼接
status
string
单据状态 NOT_FINISH :未完成，FINISHED :已完成，SHELVED :「已废弃」，CLOSED :已关闭
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
code
string
单据号

### 查询其他入库单明细
method: other.in.order.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
string
id
必填

### 新增其他入库单
method: other.in.order.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
weSourceId
string
示例：外部单号
customType
string
示例：出入库类型
remark
string
示例：备注
inLocation
string
示例：入库位置,货位/入库暂存区/销退暂存区/次品暂存区/通用暂存区/采退暂存区/补货暂存区/拣货暂存区, 1/2/3/4/5/6/7/8
items
string
示例：商品列表json [{"outerId":"springday","quantity":1,"price":1}],（price 单位：元）
必填
warehouseCode
string
示例：仓库外部编码
必填
status
integer
示例：状态 0 草稿， 1 审核并入库，-1 待入库（已审核）
uniqueCodes
string
唯一码 当type为1时传入 json [{"uniqueCode":123, "outerId":"商家编码1","supplierName":"张三供应商"}] 其中 uniqueCode字段必填。 当传入的唯一码在系统中不存在时，outerId

### 作废其他入库单
method: erp.other.in.order.cancel
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
示例：单据id，支持多个，逗号拼接
必填

### 查询其他出库单
method: other.out.order.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部编号
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss（修改时间为准）
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss（修改时间为准）
customTypeStr
string
出库类型，支持多个拼接
status
integer
单据状态 0 :已作废，1 :待出库，2 :已审核 「已废弃」，3 :已出库，4 :出库中，5：草稿
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
code
string
单据号:如:"QC3917302237225607"

### 查询其他出库单明细
method: other.out.order.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
id
必填

### 新增其他出库单
method: other.out.order.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
customType
string
示例：出入库类型
prSourceId
string
示例：外部单号(不允许重复)
outLocation
string
示例：出库位置,货位/入库暂存区/销退暂存区/次品暂存区/通用暂存区, 1/2/3/4/5 可以参考：采退的位置选择，字段的选择是一样, 不填 默认货位
remark
string
示例：备注
items
string
示例：商品列表 json [{"outerId":"springday","quantity":1,"price":1}],（price 单位：元）
必填
warehouseCode
string
示例：出库仓库(编码) - 仓库外部编码
必填
status
integer
示例：状态 0 草稿， 1 待出库，2 已出库
uniqueCodes
string
唯一码，逗号拼接。当type为1时传入
type
integer
类型 0或不传 普通， 1 唯一码

### 作废其他出库单
method: erp.other.out.order.cancel
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
示例：单据id，支持多个，逗号拼接
必填

### 查询盘点单列表
method: inventory.sheet.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
integer
示例：页码 取值范围:大于零的整数。默认值为1
startModified
string
示例：起始时间 格式:yyyy-MM-dd HH:mm:ss（修改时间）
pageSize
integer
示例：每页条数 取值范围:大于零的整数;最大值：200；默认值：40
endModified
string
示例：结束时间 格式:yyyy-MM-dd HH:mm:ss（修改时间）
status
string
示例：盘点状态
1-草稿状态,2-盘点中,3-盘点完成,4-已作废; 支持查询多个状态逗号拼接
code
string
单据号

### 查询盘点单明细
method: inventory.sheet.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
code
string
示例：盘点单号
必填
pageNo
integer
示例：页码 取值范围:大于零的整数。默认值为1
pageSize
integer
示例：每页条数 取值范围:大于零的整数;最大值：200；默认值：40
startModified
string
示例：起始时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
示例：结束时间 格式:yyyy-MM-dd HH:mm:ss

### 盘点单库存盘点
method: inventory.sheet.batch.update
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
查询调拨单列表
新增完成的调拨单
新增调拨单
查询调拨单明细列表
查询调拨入库单列表
查询调拨入库单明细
调拨入库单收货
查询调拨出库单列表
查询调拨出库单明细
调拨出库单直接出库
查询其他入库单
查询其他入库单明细
新增其他入库单
作废其他入库单
审核其他入库单(未发布)
查询其他出库单
查询其他出库单明细
新增其他出库单
作废其他出库单
审核其他出库单(未发布)
查询盘点单列表
查询盘点单明细
盘点单库存盘点
新建/修改下架单
查询下架单列表
查询下架单明细
下架单下架
货位库存查询列表
货位库存删除数据列表
平台商品批次效期库存查询列表
查询加工单列表
查询加工单明细
暂存区批量上架
货位进出记录查询
采购
快麦通
API场景说明
API文档仓储盘点单库存盘点
盘点单库存盘点
POST请求地址/router
系统相关界面​

"界面路径：【仓储】----【盘点单管理】"

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境	http

### 新建修改下架单
method: erp.wms.unshelve.order.save
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
string
下架单id (修改时传入)
targetLocation
int
下架位置 （默认通用暂存区）货位/入库暂存区/销退暂存区/次品暂存区/通用暂存区/拣选暂存区 1/2/3/4/5/6
items
string
下架的商品列表 json字符串 [{"outerId":"233357","quantity":1,"goodsSectionCode":"U-1-10-3"}]
必填
warehouseCode
string
仓库外部编码
必填
sourceId
string
外部单号（不允许重复）,新建时传入

### 查询下架单列表
method: erp.wms.unshelve.order.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
startModified
string
示例：起始时间 格式:yyyy-MM-dd HH:mm:ss（创建时间）
endModified
string
示例：结束时间 格式:yyyy-MM-dd HH:mm:ss（创建时间）
pageNo
string
示例：页码 取值范围:大于零的整数。默认值为1
pageSize
string
示例：每页条数 取值范围:大于零的整数;最大值：200；默认值：40
code
string
单据号

### 查询下架单明细
method: erp.wms.unshelve.order.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
下架单id
必填

### 下架单下架
method: erp.wms.unshelve.order.unshelve
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
下架单id
必填

### 货位库存查询列表
method: asso.goods.section.sku.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
startModified
string
示例：起始时间 格式:yyyy-MM-dd HH:mm:ss（修改时间）
endModified
string
示例：结束时间 格式:yyyy-MM-dd HH:mm:ss（修改时间）
pageNo
integer
示例：页码 取值范围:大于零的整数。默认值为1
pageSize
integer
示例：每页条数 取值范围:大于零的整数;最大值：1000；默认值：40
noNeedTotal
boolean
是否不需要查询总数（默认为false会查询总数。分页调取时，除了第一次调用需要总数，后面的几次都不需要再获取总数，可以传true，接口速度会更快）

### 货位库存删除数据列表
method: asso.goods.section.sku.del.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
startModified
string
示例：起始时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
示例：结束时间 格式:yyyy-MM-dd HH:mm:ss
pageNo
integer
示例：页码 取值范围:大于零的整数。默认值为1
pageSize
integer
示例：每页条数 取值范围:大于零的整数;最大值：200；默认值：40
noNeedTotal
boolean
是否不需要查询总数（默认为false会查询总数。分页调取时，除了第一次调用需要总数，后面的几次都不需要再获取总数，可以传true，接口速度会更快）

### 商品批次效期库存查询
method: erp.wms.product.stock.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
skuIds
string
示例：平台skuId 逗号拼接 格式:657225306755585,657225306755586
平台订单号 为空时，不可和numIids 同时为空
numIids
string
示例：平台numIid 逗号拼接 格式:657225306712576,657225306712577
平台订单号 为空时，不可和skuIds同时为空
tids
string
示例：平台订单号 逗号拼接 格式:5248307509112226,5248307509112227
平台订单号
userId
integer
示例：店铺id 格式:900017676
店铺ID，必填
pageNo
integer
示例：页码 取值范围:大于零的整数。默认值为1
pageSize
integer
示例：每页条数 取值范围:大于零的整数;最大值：1000；默认值：40

### 查询加工单列表
method: erp.stock.product.order.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
modifiedStart
string
修改开始时间
modifiedEnd
string
修改结束时间
productTimeStart
string
加工时间（时分秒） 开始
productTimeEnd
string
加工时间（时分秒） 结束
finishedTimeStart
string
完成时间（时分秒） 开始
finishedTimeEnd
string
完成时间（时分秒） 结束
createdStart
string
创建时间（时分秒）开始
createdEnd
string
创建时间（时分秒）结束
code
string
加工单号
type
string
加工类型 加工：ITEM_PROCESS；反加工：TYPE_ITEM_REVERSE_PROCESS；改码：ITEM_BARTER；标准：TYPE_NORMAL_PROCESS；印花：TYPE_ITEM_PRINT_PROCESS
status
string
加工状态 待审核: WAIT_VERIFY; 待加工: WAIT_PRODUCT; 加工中: PRODUCIN

### 查询加工单明细
method: erp.stock.product.order.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
productOrderId
long
加工单Id

### 暂存区批量上架
method: erp.wms.upshelf.batch
params:
请求参数​
全部展开
参数名
类型
描述
必填
shelveType
Integer
暂存区类型。字典看下方暂存区类型字典
必填
externalWarehouseCode
String
仓库外部编码。编码和ID二选一必填
必填
qualityType
Boolean
true：良品。false： 不良品。不传默认true
goodsSectionSkuVos
array
商品明细 传jsonString
必填

### 货位进出记录查询
method: goods.section.in.out.record.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
Integer
当前页
必填
pageSize
Integer
每页数，默认40
必填
orderNumber
string
单据编号
operateStartTime
string
操作开始时间 格式为：yyyy-MM-dd HH:mm:ss
operateEndTime
string
操作结束时间 格式为：yyyy-MM-dd HH:mm:ss

## purchase

### 新建修改供应商
method: supplier.addorupdate
params:
请求参数​
全部展开
参数名
类型
描述
必填
code
string
供应商编码
必填
name
string
供应商名称
必填
remark
string
备注
invoiceName
string
发票抬头
categoryName
string
供应商分类
accountBank
string
开户行
province
string
省份
city
string
城市
district
string
区县
fax
string
传真
email
string
邮箱
qq
string
QQ
zip
string
邮编
bankNumber
string
银行账号
address
string
地址
alipay
string
支付宝
contactName
string
联系人名称
webAddress
string
网址
billType
string
账期(现结、半月结、月结和其他，默认现结)
mobile
string
手机号码
wechat
string
微信
tax
string
税号
planReceiveDay
string
预计到货时长 默认0
companyI

### 查询供应商列表
method: supplier.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
integer
示例：页码 取值范围:大于零的整数。默认值为1
pageSize
integer
示例：每页条数 取值范围:大于零的整数;最大值：200；默认值：40
status
string
示例：合作状态，1表示合作中，2表示停止合作

### 新增采购单
method: purchase.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部采购订单号(不允许重复)
remark
string
备注
supplierCode
string
供应商(编码) - 必填
必填
deliveryDate
string
交货日期
items
string
采购的商品列表 json [{"outerId":"springday","quantity":1,"price":1,"remark":"备注信息","deliveryDate":"明细交货日期"}]
必填
warehouseCode
string
仓库外部编码
必填
status
string
状态 WAIT_VERIFY （草稿）、VERIFYING(提交审核)、VERIFY（审核）

### 新建修改采购单
method: purchase.addorupdate
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部采购订单号(不允许重复)
remark
string
备注
id
long
采购单id
supplierCode
string
供应商(编码)
必填
deliveryDate
string
交货日期
items
string
采购的商品列表 json [{"outerId":"springday","quantity":1,"ptSkuId":"平台skuID","remark":"备注信息","price":1,"caigouUrl":"采购链接","deliveryDate":"1751472000000"}],（price 单位：元）
必填
warehouseCode
string
仓库外部编码
必填
status
string
状态 WAIT_VERIFY （草稿）、VERIFYING(提交审核)、VERIFY（审核）
ptOrderId
string
平台订单号（客户店铺原始订单号）
ptShopId
long
平台店铺ID
templateName
string
物流公司
outSid
s

### 采购单查询
method: purchase.order.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部采购订单号
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
timeType
integer
查询时间类型 1修改时间（默认为1），2创建时间
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss
status
string
单据状态 WAIT_VERIFY 草稿,VERIFYING 待审核,GOODS_NOT_ARRIVED 未到货,GOODS_PART_ARRIVED 部分到货,FINISHED 已完成, GOODS_CLOSED 已关闭
id
integer
采购单id
code
string
采购单号

### 采购单详情
method: purchase.order.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
string
示例：采购单id
ptOrderId
string
示例：平台订单号

### 采购单状态更新
method: purchase.status.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
采购单ID集合,逗号拼接
必填
status
string
状态 ，支持 VERIFY（审核）、FINISHED(完成)、CLOSED（关闭）
必填

### 更新采购单特殊字段
method: purchase.update.ignore.status
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
对接平台外部订单号
sourceOrderId
string
对接平台外部订单号
id
string
采购单id
必填
templateName
string
物流公司
outSid
string
物流单号
logisticsCode
string
物流编码
supplierCode
string
供应商编码

### 采购单反审核
method: purchase.unAudit
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
采购单ID集合,逗号拼接

### 采退单保存
method: purchase.return.save
params:
请求参数​
全部展开
参数名
类型
描述
必填
prSourceId
string
外部单号(不允许重复)
purchaseOrderId
long
采购单ID
id
long
采退单id(修改时传入)
supplierCode
string
供应商(编码) - 必填
必填
items
string
采购的商品列表 json [{"outerId":"springday","quantity":1,"price":1}]
必填
warehouseCode
string
仓库外部编码
必填
remark
string
备注

### 采退单查询列表
method: purchase.return.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部采购订单号
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
timeType
integer
查询时间类型 1修改时间（默认为1），2创建时间
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
status
string
单据状态 0 :已作废 1 :待出库 2 :已审核 「已废弃」 4 :出库中 3 :已出库 5:草稿
code
string
单据号
tagName
string
标签名称
必填
financeStatus
string
财审状态 待财审：WAIT_FINANCE，已财审：FINANCED
必填

### 采退单详情
method: purchase.return.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
采退单id
必填

### 采退单出库
method: purchase.return.out
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
采退单id
必填

### 采退单作废
method: purchase.return.cancel
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
采退单id
必填

### 收货单新增修改
method: warehouse.entry.addorupdate
params:
请求参数​
全部展开
参数名
类型
描述
必填
purchaseOrderId
long
采购单ID
weOuterCode
string
收货单外部单号(不允许重复)
id
long
收货单id(编辑时必填)
supplierCode
string
供应商(编码) - 必填
必填
items
string
采购的商品列表 json [{"outerId":"springday","quantity":1,"price":1}],（price 单位：元）
必填
warehouseCode
string
仓库外部编码
必填
status
integer
状态，支持传入 NOT_FINISH （未完成）、FINISHED(已完成)
必填

### 收货单查询列表
method: warehouse.entry.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部采购订单号(不允许重复)
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
startModified
string
修改起始时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
修改结束时间 格式:yyyy-MM-dd HH:mm:ss
status
string
单据状态 NOT_FINISH :未完成 STATUS_UNIQUE_CODE_RECEIVING:唯一码收货中 FINISHED :已完成 SHELVED：上架完成 CLOSED :已关闭
code
string
收货单号
financeStatus
string
财审状态 WAIT_FINANCE：待财审，FINANCED：已财审
tagName
string
标签名称
flag
integer
旗帜：-1/空 无旗帜，0/1/2/3/4/5分别代表灰、红、橙、绿、蓝、紫
prei

### 收货单详情
method: warehouse.entry.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
收货单id
必填

### 收货单作废
method: warehouse.entry.cancel
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
收货单id，逗号隔开，最多20个
必填

### 收货单打回
method: warehouse.entry.finished.revert
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
收货单id,逗号拼接
必填

### 收货单收货
method: warehouse.entry.receive
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
收货单id
必填
items
string
商品列表 json [{"outerId":"springday","quantity":1,"batchNo":"001","productDate":"2024-04-11 00:00:00","expireDate":"2024-04-15 00:00:00"}]
必填

### 采购快速收货
method: warehouse.entry.fast.receive
params:
请求参数​
全部展开
参数名
类型
描述
必填
sysOuterId
string
商家编码
必填
warehouseCode
string
仓库外部编码
必填
supplierCode
string
供应商编码
batchNo
string
批次
可选
productDate
Date
生产日期，格式“2024-04-11 00:00:00”
可选
expireDate
Date
到期日期，格式“2024-04-15 00:00:00”
可选

### 上架单上架
method: erp.purchase.shelf.save
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
string
示例：上架单id
必填
items
string
商品列表 json [{"outerId":"springday","quantity":1,"quality":true,"goodsSectionCode":"A-1-1"}]
必填

### 查询上架单
method: erp.purchase.shelf.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
timeType
integer
查询时间类型 1修改时间,2创建时间,3上架时间（默认为1）
startModified
string
修改起始时间 格式:yyyy-MM-dd HH:mm:ss
endModified
string
修改结束时间 格式:yyyy-MM-dd HH:mm:ss
weCode
string
收货单编号(支持单号/外部单号)
status
integer
状态 0 待上架 1 已完成 2已作废
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40

### 查询上架单详情
method: erp.purchase.shelf.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
上架单id
必填

### 计算已售采购建议
method: sale.purchase.strategy.calculate
params:
请求参数​
全部展开
参数名
类型
描述
必填
warehouseCode
string
仓库外部编码
必填
ignoreTradeOut
int
过滤出库单 0不过滤，1过滤
ignoreRefund
int
过滤待发货退款中订单，0不过滤，1过滤

### 进度获取
method: purchase.progress.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
progressType
int
进度类型，4：已售采购建议

### 查询已售采购建议
method: sale.purchase.strategy.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
queryKey
string
查询key，从进度purchase.progress.query中获取
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：500；默认值：40

### 归档采购单查询
method: purchase.order.history.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
timeType
integer
查询时间类型 1修改时间,2创建时间（默认为1）
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss
必填
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss，起始时间和结束时间相差90天内
必填
status
string
FINISHED 已完成, GOODS_CLOSED 已关闭
id
integer
采购单id
code
string
采购单号

### 归档采购单详情
method: purchase.order.history.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
string
示例：采购单id
必填

### 归档收货单查询列表
method: warehouse.entry.history.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss
必填
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss，起始时间和结束时间间隔不超过90天
必填
timeType
integer
查询时间类型 1修改时间,2创建时间（默认为1）
status
string
单据状态 FINISHED :已完成 SHELVED：上架完成 CLOSED :已关闭
code
string
收货单号
financeStatus
string
财审状态 WAIT_FINANCE：待财审，FINANCED：已财审

### 归档收货单详情
method: warehouse.entry.history.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
收货单id
必填

### 归档采退单查询列表
method: purchase.return.history.list.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
timeType
integer
查询时间类型 1修改时间,2创建时间（默认为1）
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss
必填
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss，起始时间和结束时间相差90天内
必填
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40
status
string
单据状态 0 :已作废 1 :待出库 3 :已出库
code
string
单据号
financeStatus
string
财审状态 待财审：WAIT_FINANCE，已财审：FINANCED

### 归档采退单详情
method: purchase.return.history.list.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
采退单id
必填

### 归档上架单查询
method: erp.purchase.shelf.history.query
params:
请求参数​
全部展开
参数名
类型
描述
必填
timeType
integer
查询时间类型 1修改时间,2创建时间,3上架时间（默认为1）
startModified
string
起始时间 格式:yyyy-MM-dd HH:mm:ss
必填
endModified
string
结束时间 格式:yyyy-MM-dd HH:mm:ss，起始时间和结束时间相差90天内
必填
weCode
string
收货单编号
status
integer
状态 0 待上架 1 已完成 2已作废
pageNo
integer
页码 取值范围:大于零的整数。默认值为1
pageSize
integer
每页条数 取值范围:大于零的整数;最大值：200；默认值：40

### 归档上架单详情查询
method: erp.purchase.shelf.history.get
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
long
上架单id
必填

### 预约入库单新增
method: purchase.pre.in.order.add
params:
请求参数​
全部展开
参数名
类型
描述
必填
outerCode
string
外部预约入库单号
必填
purchaseOrderCode
string
采购单号
必填
supplierCode
string
供应商编码
必填
preinDate
string
预计到货日期，格式:yyyy-MM-dd HH:mm:ss
warehouseCode
string
仓库编码
必填
status
integer
状态编码 （2-待审核，3-已审核）
必填
remark
string
备注
items
string
采购的商品列表 json [{"outerId":"springday","quantity":1}]
必填

### 预约入库单修改
method: purchase.pre.in.order.update
params:
请求参数​
全部展开
参数名
类型
描述
必填
id
string
预约单号id
必填
preinDate
string
预计到货日期，格式:yyyy-MM-dd HH:mm:ss
remark
string
备注
items
string
采购的商品列表 json [{"outerId":"springday","quantity":1}]
必填

### 预约入库单反审核
method: purchase.pre.in.order.anti.audit
params:
请求参数​
全部展开
参数名
类型
描述
必填
ids
string
预约单号id，逗号分割
必填

## distribution

### 快麦通登录
method: UNKNOWN
params:
请求参数​
全部展开
参数名
类型
描述
必填
companyName
string
公司名
必填
userName
string
分销商账号名 是
必填
password
string
密码 md5处理之后的
必填

### 增加分销余额
method: kmt.api.dms.add.distributor.money
params:
请求参数​
全部展开
参数名
类型
描述
必填
supplierCompanyId
long
供销商公司id
必填
distributorCompanyId
long
分销商公司id
必填
paymentType
支付方式 aliPay� 支付宝 wechat� 微信 bankTransfer� 银行转账 other� 其他
必填
amount
double
金额 单位元
必填

### 添加分销商品
method: kmt.api.dms.add.distribution.item.fromsupplier
params:
请求参数​
全部展开
参数名
类型
描述
必填
supplierCompanyId
long
供销商公司id
必填
distributorCompanyId
long
分销商公司id
必填
itemOuterId
string
商品编码,只传商品编码表示把这个商品下所有sku都加入分销商品
必填
itemSkuOuterIdList
array
sku编码，如果传入sku编码 表示把传入的sku加入分销商品

### 注册分销商
method: kmt.api.dms.add.distributor
params:
请求参数​
全部展开
参数名
类型
描述
必填
supplierCompanyId
long
供销商公司id
必填
distributorCompanyName
string
分销商公司名
必填
versionNumber
string
要给分销商公司 设置什么版本，请咨询对接业务员
source
int
来源 默认 0,请咨询对接业务员，各渠道改参数对应值
必填
mainPhone
string
主手机号
必填
defaultUserName
string
分销商 默认账号的用户名
必填
defaultPassword
string
分销商 默认帐号的密码
必填

### 分页查询供销小店商品
method: kmt.api.dms.query.page.distributor.item
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通分页查询供销小店商品
分页查询供销小店商品
GET请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
multipart/form-data
必填

### 查询供销小店商品详情
method: kmt.api.dms.query.detail.distributor.item
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通查询供销小店商品详情
查询供销小店商品详情
GET请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
multipart/form-data
必填

### 供销商视角分页供销小店商品
method: kmt.api.dms.query.page.distributor.item.supplier.view
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通供销商视角-分页供销小店商品
供销商视角-分页供销小店商品
GET请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
multipart/form-data
必填

### 供销商视角查询供销小店商品详情
method: kmt.api.dms.query.detail.distributor.item.supplier.view
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通供销商视角-查询供销小店商品详情
供销商视角-查询供销小店商品详情
GET请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
multipart/form-data
必填

### 提交分销小店商品的同步
method: kmt.api.dms.submit.sync.item
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通提交分销小店商品的同步
提交分销小店商品的同步
GET请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
multipart/form-data
必填

### 获取小店商品的同步状态
method: kmt.api.dms.sync.status.item
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通获取小店商品的同步状态
获取小店商品的同步状态
GET请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
multipart/form-data
必填

### 分销商信息查询
method: kmt.api.dms.query.distributor.list
params:
请求参数​
全部展开
参数名
类型
描述
必填
supplierCompanyId
long
供销商公司id
必填
modifiedTimeStart
string
周期 开始时间 格式类型（yyyy-MM-dd hh:mm:ss,2018-01-01 17:02:07）
非必填
modifiedTimeEnd
string
周期 结束时间 格式类型（yyyy-MM-dd hh:mm:ss,2018-01-01 17:02:07）
非必填
requestState
string
全部/待审核/已生效/已作废/已拒绝 对应01234,支持多个状态查询 ,分隔
非必填
pageNo
int
页码
必填
pageSize
int
分页大小
必填

### 查询在线支付方式提示文案
method: kmt.api.dms.pay.prompt
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通查询在线支付方式提示文案
查询在线支付方式提示文案
POST请求地址/router

### 获取最新的视频链接信息
method: kmt.api.dms.query.item.video.info
params:
跳到主要内容
首页
API文档
常见问题
申请APP
常用工具
搜索
⌘
K
API对接说明
API文档
基础
商品
交易
售后
仓储
采购
快麦通
快麦通登录
增加分销余额
添加分销商品
注册分销商
分页查询供销小店商品
查询供销小店商品详情
供销商视角-分页供销小店商品
供销商视角-查询供销小店商品详情
提交分销小店商品的同步
获取小店商品的同步状态
分销商信息查询
查询在线支付方式提示文案
获取最新的视频链接信息
API场景说明
API文档快麦通获取最新的视频链接信息
获取最新的视频链接信息
GET请求地址/router
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
multipart/form-data
必填
