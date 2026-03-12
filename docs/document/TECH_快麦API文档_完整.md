
============================================================
=== 订单查询 ===
============================================================
API文档交易订单查询(非淘系,拼多多)
订单查询(非淘系,拼多多)
POST请求地址/router
系统相关界面​

界面路径：【交易】----【订单查询】

接口备注​

此接口不包含淘系、拼多多订单数据，如需获取淘系、拼多多订单，需申请奇门、方舟appkey获取。注：各平台敏感信息受平台政策影响会有不返回或者加密返回的情况，如有需要接入前可确认 点击前往奇门申请说明 点击前往方舟对接说明

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
appKey	string	是	分配给应用的AppKey
timestamp	string	是	时间戳，时区为GMT+8，例如：2020-09-21 16:58:00。API服务端允许客户端请求最大时间误差为10分钟
format	string	否	响应格式。默认为json格式，可选值：json
version	string	是	API协议版本 可选值：1.0
sign_method	string	否	签名的摘要算法(默认 hmac)，可选值为：hmac，md5，hmac-sha256。
sign	string	是	签名
session	string	是	授权会话信息 （即access_token，由系统分配）
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
string
application/x-www-form-urlencoded;charset=UTF-8
必填
API接口地址​
全部展开
参数名
类型
描述
必填
method
string
erp.trade.list.query
必填
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
每页多少条，最大支持200，不能小于20
20
pageNo
integer
页码
1
status
string
系统状态,对应响应参数的sysStatus订单系统状态(支持多个,逗号隔开).WAIT_BUYER_PAY:待付款,WAIT_AUDIT:待审核,WAIT_FINANCE_AUDIT:待财审,FINISHED_AUDIT:审核完成, WAIT_EXPRESS_PRINT:待打印快递单,WAIT_PACKAGE:待打包,WAIT_WEIGHT:待称重,WAIT_SEND_GOODS:待发货, WAIT_DEST_SEND_GOODS:待供销商发货,SELLER_SEND_GOODS:卖家已发货,FINISHED:交易完成,CLOSED:交易关闭
tagIds
string
查询指定标签ID集合,多个逗号隔开,最多支持十组
exceptIds
string
自定义异常标签,多个逗号隔开,最多支持十组
exceptionStatus
string
系统异常标签,多个逗号隔开,最多支持十组
types
string
订单类型,多个逗号0=普通订单;1=货到付款;3=平台订单;4=线下订单;6=预售订单;7=合并订单;8=拆分订单;9=加急订单;10=空包订单;11=合单提示;12=门店订单;13=换货订单;14=补发订单;16=海外仓订单;17=Lazada;18=报损单;19=领用单;20=调整单;21=客户订单;22=天猫直送;23=平台预售;24=京东直发;25=京东供销;33=分销订单;34=供销订单;35=京配订单;36=平台分销;50=天猫淘宝店铺预售;51=抖音厂商代发;53=亚马逊FBA;54=亚马逊FBM;55=亚马逊多渠道;56=奇门订单;57=得物普通现货;58=得物极速现货;60=全款预售;61=得物直发订单;62=Lazada-FBL;66=平台拆单;99=出库单
onlyContain
string
异常查询状态：1:仅包含 2:排除 3:同时包含
consignedType
integer
发货方式 0.非ERP发货 1.ERP发货
buyerNick
string
买家ID
queryType
integer
订单查询范围类型 0.三个月内订单 1.三个月之前订单
0
outSids
string
运单号，多个逗号隔开
useHasNext
boolean
传true时不会返回total（不统计总数）, 返回hasNext用以判断是否有下一页数据，传false时按原来逻辑只返回total
useCursor
boolean
传true时开启游标查询 , 仅queryType不为1时有效
cursor
string
useCursor传true时,第一次查询该参数不传，之后的每次查询传上一次接口返回的cursor
请求示例​

示例一：

{
    "pageNo": "number",
    "userIds": "string",
    "timeType": "string",
    "pageSize": "number",
    "startTime": "string",
    "endTime": "string",
    "tid": "string",
    "sid": "string",
    "status": "string"
}

响应参数​
全部展开
参数名
类型
描述
必填
success
boolean
示例：true
trace_id
string
示例：3553483395407017
list
array
list
total
long
总数 仅在非归档订单查询（请求参数useHasNext，useCursor都不为true时）第一页返回
hasNext
boolean
是否有下一页
cursor
string
游标
响应示例​
{
  "traceId": "1876936694781585983",
  "list": [
    {
      "buyerNick": "XXXXX",
      "threePlTiming": 0,
      "type": "4,99,0",
      "receiverCity": "杭州市",
      "packmaCost": 0,
      "expressCode": "YUNDA",
      "payment": "42",
      "isExcep": 1,
      "receiverZip": "223700",
      "isTmallDelivery": 0,
      "isHalt": 0,
      "warehouseId": XXXX,
      "isRefund": 0,
      "receiverState": "浙江省",
      "orders": [
        {
          "discountRate": 100,
          "discountFee": "0.00",
          "payTime": 1592967330000,
          "num": 2,
          "source": "sys",
          "sysTitle": "12罐 美甲饰品天然鲍鱼贝壳片不规则指甲贴碎片 日系幻彩光疗甲",
          "type": 0,
          "tid": "XXXXXXX",
          "isPresell": 0,
          "consignTime": 946656000000,
          "updTime": 1592967337000,
          "price": "21.00",
          "giftNum": 0,
          "stockNum": 2,
          "modified": 946656000000,
          "stockStatus": "NORMAL",
          "payment": "42.00",
          "id": XXXXXXX,
          "created": 1592967330000,
          "insufficientCanceled": 0,
          "taobaoId": 0,
          "sysOuterId": "SKU000817",
          "saleFee": "0.0",
          "volume": 0,
          "picPath": "XXXXXXX",
          "netWeight": 0,
          "companyId": XXXXXX,
          "isVirtual": 0,
          "sysConsigned": 0,
          "oid": XXXXX,
          "itemSysId": XXXXXXX,
          "sid": XXXXXXX,
          "forcePackNum": 0,
          "sysStatus": "FINISHED_AUDIT",
          "sysItemOuterId": "XXXXXXX",
          "priceDouble": 21,
          "cost": 0,
          "isCancel": 0,
          "salePrice": "0.0",
          "combineId": 0,
          "ptConsignTime": 946656000000,
          "totalFee": "42.00",
          "sysPicPath": "XXXXXXX",
          "skuSysId": -1,
          "endTime": 946656000000
        }
      ],
      "expressCompanyId": 102,
      "isUrgent": 0,
      "theoryPostFee": 0,
      "warehouseName": "默认仓库",
      "itemNum": 2,
      "receiverDistrict": "滨江区",
      "taxFee": "0",
      "isHandlerMessage": 0,
      "grossProfit": 0,
      "salePrice": "0.00",
      "postFee": "0",
      "receiverMobile": "66876387840",
      "userId": 0,
      "splitType": -1,
      "itemKindNum": 1,
      "ptConsignTime": 946656000000,
      "endTime": 946656000000,
      "excep": 0,
      "splitSid": -1,
      "shortId": 63,
      "discountFee": "0.00",
      "payTime": 1592967330000,
      "mobileTail": "7840",
      "source": "sys",
      "templateId": XXXXXX,
      "tid": "XXXXXXX",
      "isPresell": 0,
      "consignTime": 946656000000,
      "updTime": 1632984391000,
      "modified": 946656000000,
      "stockStatus": "NORMAL",
      "created": 1592967330000,
      "taobaoId": 0,
      "weight": 0,
      "sysOuterId": "XXXXXXX",
      "saleFee": "0.00",
      "exceptions": [
        "EX_DELIVER"
      ],
      "volume": 0,
      "receiverAddress": "道588号恒鑫大厦10F光云科技349866876387841",
      "scalping": 0,
      "netWeight": 0,
      "companyId": XXXXXX,
      "sid": XXXXXX,
      "sysStatus": "WAIT_SEND_GOODS",
      "cost": 0,
      "isCancel": 0,
      "receiverName": "XXXXXXX",
      "isHandlerMemo": 0,
      "timeoutActionTime": 946656000000,
      "isPackage": 0,
      "expressCompanyName": "韵达快递",
      "totalFee": "42",
      "templateName": "K_韵达快递标准"
    }
  ],
  "total": 1,
  "success": true
}

异常示例​
{
    "code": "25",
    "msg": "服务方法(item.single.get:1.0)的签名无效",
    "success": false,
    "trace_id": "3553483395423660"
}

错误码解释​
错误信息	错误码	解决方案
资源异常,请联系管理员!	400	检查参数是否合法
非法参数!	50	检查参数类型等是否合法
查询的时间类型不合法！created: 下单时间, pay_time：付款时间，consign_time：发货时间，audit_time：审核时间，upd_time：修改时间]字段：[timeType]	50	检查参数是否合法
店铺ID格式不正确！	20014	检查参数是否合法
店铺ID数量最大支持10组！	20015	参数过长
订单状态不合法！[WAIT_BUYER_PAY：待付款，WAIT_AUDIT：待审核，WAIT_FINANCE_AUDIT：待财审，FINISHED_AUDIT：审核完成，WAIT_EXPRESS_PRINT：待打印快递单，WAIT_PACKAGE：待打包，WAIT_WEIGHT：待称重，WAIT_SEND_GOODS：待发货，WAIT_DEST_SEND_GOODS：待供销商发货，SELLER_SEND_GOODS：卖家已发货，FINISHED：交易完成，CLOSED：交易关闭]字段：[status]	50	检查参数是否合法
标签ID格式不正确	20101	检查参数是否合法
查询指定标签ID数量最大支持10组	20102	检查参数是否合法
标签ID格式不正确	20103	检查参数是否合法
排除标签ID数量最大支持10组	20104	检查参数是否合法
自定义异常标签ID格式不正确	20105	检查参数是否合法
自定义异常标签ID数量最大支持10组	20106	检查参数是否合法
系统异常标签ID数量最大支持10组	20108	检查参数是否合法
异常查询状态不合法！[1:仅包含 2:排除 3:同时包含]	50	检查参数是否合法
订单查询失败	30077	请根据返回的错误信息进行处理

============================================================
=== 销售出库查询 ===
============================================================
API文档交易销售出库查询
销售出库查询
POST请求地址/router
系统相关界面​

界面路径：【交易】----【订单查询】

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

接口备注​
此接口不包含淘系、拼多多敏感数据（如收件人信息、平台敏感数据等），平台敏感数据不返回，其它平台的敏感信息会根据平台规则同步调整
公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
appKey	string	是	分配给应用的AppKey
timestamp	string	是	时间戳，时区为GMT+8，例如：2020-09-21 16:58:00。API服务端允许客户端请求最大时间误差为10分钟
format	string	否	响应格式。默认为json格式，可选值：json
version	string	是	API协议版本 可选值：1.0
sign_method	string	否	签名的摘要算法(默认 hmac)，可选值为：hmac，md5，hmac-sha256。
sign	string	是	签名
session	string	是	授权会话信息 （即access_token，由系统分配）
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
string
application/x-www-form-urlencoded;charset=UTF-8
必填
API接口地址​
全部展开
参数名
类型
描述
必填
method
string
erp.trade.outstock.simple.query
必填
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
pageNo
integer
页码
1
status
string
系统状态,对应响应参数的sysStatus订单系统状态(支持多个,逗号隔开).WAIT_BUYER_PAY:待付款,WAIT_AUDIT:待审核,WAIT_FINANCE_AUDIT:待财审,FINISHED_AUDIT:审核完成, WAIT_EXPRESS_PRINT:待打印快递单,WAIT_PACKAGE:待打包,WAIT_WEIGHT:待称重,WAIT_SEND_GOODS:待发货, WAIT_DEST_SEND_GOODS:待供销商发货,SELLER_SEND_GOODS:卖家已发货,FINISHED:交易完成,CLOSED:交易关闭
tagIds
string
查询指定标签ID集合,多个逗号隔开,最多支持十组
exceptIds
string
自定义异常标签,多个逗号隔开,最多支持十组
exceptionStatus
string
系统异常标签,多个逗号隔开,最多支持十组
onlyContain
string
异常查询状态：1:仅包含 2:排除 3:同时包含
types
string
订单类型,多个逗号1=货到付款; 3=平台订单; 4=线下订单; 6=预售订单; 7=合并订单; 8=拆分订单; 9=加急订单; 10=空包订单; 11=合单提示; 12=门店订单; 13=换货订单; 14=补发订单; 16=海外仓订单; 17=Lazada; 18=报损单; 19=领用单; 20=调整单; 21=客户订单; 22=天猫直送; 23=平台预售; 24=京东直发; 25=京东供销; 33=分销订单; 34=供销订单; 35=京配订单; 36=平台分销; 50=天猫淘宝店铺预售; 51=抖音厂商代发; 53=亚马逊FBA; 54=亚马逊FBM; 55=亚马逊多渠道; 56=奇门订单; 57=得物普通现货; 58=得物极速现货; 60=全款预售; 61=得物直发订单; 62=Lazada-FBL;66=平台拆单 99=出库单
consignedType
integer
发货方式 0.非ERP发货 1.ERP发货
buyerNick
string
买家ID
queryType
integer
0.三个月内订单 1.三个月之前订单
0
outSids
string
运单号，多个逗号隔开
useHasNext
boolean
传true时不会返回total（不统计总数）, 返回hasNext用以判断是否有下一页数据，传false时按原来逻辑只返回total
useCursor
boolean
传true时开启游标查询 , 仅queryType不为1时有效
cursor
string
useCursor传true时,第一次查询该参数不传，之后的每次查询传上一次接口返回的cursor
请求示例​

示例一：

{
    "pageNo": "number",
    "userIds": "string",
    "timeType": "string",
    "pageSize": "number",
    "startTime": "string",
    "endTime": "string",
    "tid": "string",
    "sid": "string",
    "status": "string"
}

响应参数​
全部展开
参数名
类型
描述
必填
success
boolean
示例：true
trace_id
string
示例：3553483395407017
list
array
list
total
long
总数 仅在非归档订单查询（请求参数useHasNext，useCursor都不为true时）第一页返回
hasNext
boolean
是否有下一页
cursor
string
游标
响应示例​
{
	"total": "0",
	"list": [{
		"paymentDiff": "number",
		"buyerNick": "string",
		"threePlTiming": "number",
		"type": "string",
		"receiverCity": "string",
		"invoiceRemark": "string",
		"poNos": "string",
		"packmaCost": "number",
		"receiverPhone": "string",
		"expressCode": "string",
		"payment": "string",
		"adjustFee": "string",
		"isExcep": "number",
		"receiverZip": "string",
		"isTmallDelivery": "number",
		"buyerTaxNo": "string",
		"isHalt": "number",
		"warehouseId": long,
		"isRefund": "number",
		"receiverState": "string",
		"expressCompanyId": "number",
		"status": "string",
		"isUrgent": "number",
		"theoryPostFee": "number",
		"warehouseName": "string",
		"hasSuit": "boolean",
		"itemNum": "number",
		"receiverDistrict": "string",
		"taxFee": "string",
		"isHandlerMessage": "number",
		"grossProfit": "number",
		"postFee": "string",
		"receiverMobile": "string",
		"singleItemKindNum": "number",
		"userId": long,
		"itemKindNum": "number",
		"exceptMemo": "string",
		"ptConsignTime": long,
		"buyerMessage": "string",
		"unifiedStatus": "string",
		"excep": "number",
		"shortId": "number",
		"discountFee": "string",
		"sellerFlagString": "string",
		"payTime": long,
		"mobileTail": "string",
		"source": "string",
		"tradePurchaseAmount": "number",
		"tid": "string",
		"invoiceFormat": "number",
		"consignTime": long,
		"isPresell": "number",
		"receiverCountry": "string",
		"updTime": long,
		"stockStatus": "string",
		"modified": long,
		"invoiceType": "string",
		"created": long,
		"taobaoId": long,
		"weight": "number",
		"auditMatchRule": "number",
		"sysOuterId": "string",
		"list": [{
			"bgColor": "string",
			"remark": "string",
			"id": "number",
			"tagName": "string",
			"fontColor": "string",
		}],
		"saleFee": "string",
		"exceptions": "string[]",
		"outSid": "string",
		"receiverAddress": "string",
		"volume": double,
		"scalping": integer,
		"companyId": long,
		"netWeight": double,
		"sellerMemo": "string",
		"chSysStatus": "string",
		"destName": "string",
		"invoiceName": "string",
		"subSource": "string",
		"sysMemo": "string",
		"shopName": "string",
		"sid": long,
		"cancelFrom": "number",
		"acPayment": "string",
		"sysStatus": "string",
		"manualPaymentAmount": "number",
		"fxIsUpload": "number",
		"promiseService": "string",
		"cost": double,
		"isCancel": integer,
		"receiverName": "string",
		"timeoutActionTime": long,
		"isHandlerMemo": "number",
		"expressCompanyName": "string",
		"isCancelDistributorAttribute": "number",
		"tradeFrom": "string",
		"platformPaymentAmount": "number",
		"totalFee": "string",
		"needInvoice": "number",
		"wlbTemplateType": "number",
		"timingPromise": "string",
		"sourceName": "string",
		"invoiceKind": "string"
	}],
	"success": "boolean",
	"trace_id": "long"
}

异常示例​
{
    "code": "25",
    "msg": "服务方法(item.single.get:1.0)的签名无效",
    "success": false,
    "trace_id": "3553483395423660"
}

错误码解释​
错误信息	错误码	解决方案
资源异常,请联系管理员!	400	检查参数是否合法
非法参数!	50	检查参数类型等是否合法
查询的时间类型不合法！created: 下单时间, pay_time：付款时间，consign_time：发货时间，audit_time：审核时间，upd_time：修改时间]字段：[timeType]	50	检查参数是否合法
店铺ID格式不正确！	20014	检查参数是否合法
店铺ID数量最大支持10组！	20015	参数过长
订单状态不合法！[WAIT_BUYER_PAY：待付款，WAIT_AUDIT：待审核，WAIT_FINANCE_AUDIT：待财审，FINISHED_AUDIT：审核完成，WAIT_EXPRESS_PRINT：待打印快递单，WAIT_PACKAGE：待打包，WAIT_WEIGHT：待称重，WAIT_SEND_GOODS：待发货，WAIT_DEST_SEND_GOODS：待供销商发货，SELLER_SEND_GOODS：卖家已发货，FINISHED：交易完成，CLOSED：交易关闭]字段：[status]	50	检查参数是否合法
标签ID格式不正确	20101	检查参数是否合法
查询指定标签ID数量最大支持10组	20102	检查参数是否合法
标签ID格式不正确	20103	检查参数是否合法
排除标签ID数量最大支持10组	20104	检查参数是否合法
自定义异常标签ID格式不正确	20105	检查参数是否合法
自定义异常标签ID数量最大支持10组	20106	检查参数是否合法
系统异常标签ID数量最大支持10组	20108	检查参数是否合法
异常查询状态不合法！[1:仅包含 2:排除 3:同时包含]	50	检查参数是否合法
销售出库查询失败	30077	请根据返回的错误信息进行处理

============================================================
=== 查询商品列表 ===
============================================================
API文档商品查询商品列表
查询商品列表
POST请求地址/router
接口描述​

查询商品列表，支持获取商品列表，包括普通商品，套件商品、组合商品、加工商品

系统相关界面​

"界面路径：【商品】----【商品档案】"

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
appKey	string	是	分配给应用的AppKey
timestamp	string	是	时间戳，时区为GMT+8，例如：2020-09-21 16:58:00。API服务端允许客户端请求最大时间误差为10分钟
format	string	否	响应格式。默认为json格式，可选值：json
version	string	是	API协议版本 可选值：1.0
sign_method	string	否	签名的摘要算法(默认 hmac)，可选值为：hmac，md5，hmac-sha256。
sign	string	是	签名
session	string	是	授权会话信息 （即access_token，由系统分配）
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
string
application/x-www-form-urlencoded
必填
API接口地址​
全部展开
参数名
类型
描述
必填
method
string
item.list.query
必填
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
是否返回采购链接 0.否 1.是
0
请求示例​

示例一：

{
  "pageNo":integer,
  "pageSize":integer
}

响应参数​
全部展开
参数名
类型
描述
必填
msg
string
返回消息
必填
traceId
string
流水id
必填
total
long
返回总数
必填
code
string
返回码
必填
solution
string
解决方案
必填
body
string
返回内容
必填
items
array
数据列表
必填
响应示例​
{
  "msg": "zhangsan",
  "traceId": "zhangsan",
  "total": 12345,
  "code": "zhangsan",
  "solution": "zhangsan",
  "subCode": "zhangsan",
  "subMsg": "zhangsan",
  "body": "zhangsan",
  "forbiddenField": "zhangsan",
  "items": [
    {
      "typeTag": 1,
      "created": "zhangsan",
      "sysItemId": 12345,
      "weight": 10000,
      "shortTitle": "zhangsan",
      "title": "zhangsan",
      "type": "zhangsan",
      "isSkuItem": 1,
      "picPath": "zhangsan",
      "catId": "zhangsan",
      "unit": "zhangsan",
      "activeStatus": 1,
      "hasSupplier": 1,
      "modified": "zhangsan",
      "outerId": "zhangsan",
      "makeGift": true,
      "isVirtual": 1,
      "items": [
        {
          "created": "zhangsan",
          "sysItemId": 12345,
          "skuPicPath": "zhangsan",
          "weight": 10000,
          "shortTitle": "zhangsan",
          "propertiesName": "zhangsan",
          "propertiesAlias": "zhangsan",
          "sysSkuId": 12345,
          "unit": "zhangsan",
          "activeStatus": 1,
          "hasSupplier": 1,
          "modified": "zhangsan",
          "makeGift": true,
          "barcode": "zhangsan",
          "skuOuterId": "zhangsan"
        }
      ],
      "barcode": "zhangsan"
    }
  ]
}

异常示例​
{
    "code": "25",
    "msg": "服务方法(item.list.query:1.0)的签名无效",
    "success": false,
    "trace_id": "3553483395423660"
}

错误码解释​
错误码	错误信息	解决方案
20001	页码为空或不符合规定	页码的值不能为空，且不能小于1
20002	页数为空或不符合规定	页数的值不能为空，且不能小于1或超过200
20004	排序方式不正确	排序方式的值不能为空，且只能为desc或asc
20109	系统商品状态不正确	请检查系统商品状态是否填写规范，其中 0-表示停用，1-表示启用
20103	查询商品列表有误	请根据返回错误信息进行处理
50	只能选择[0,1,3,6,7,8,9,10]的其中一位商品类型数值	请检查该参数的值是否规范

============================================================
=== 查询单个商品明细 ===
============================================================
API文档商品查询单个商品明细
查询单个商品明细
POST请求地址/router
接口描述​

用于获取单个商品sku信息。支持获取指定主商品下的sku商品信息，包括普通商品，套件商品、组合商品、加工商品

系统相关界面​

"界面路径：【商品】----【商品档案】----【编辑】"

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
appKey	string	是	分配给应用的AppKey
timestamp	string	是	时间戳，时区为GMT+8，例如：2020-09-21 16:58:00。API服务端允许客户端请求最大时间误差为10分钟
format	string	否	响应格式。默认为json格式，可选值：json
version	string	是	API协议版本 可选值：1.0
sign_method	string	否	签名的摘要算法(默认 hmac)，可选值为：hmac，md5，hmac-sha256。
sign	string	是	签名
session	string	是	授权会话信息 （即access_token，由系统分配）
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
string
application/x-www-form-urlencoded
必填
API接口地址​
全部展开
参数名
类型
描述
必填
method
string
item.single.get
必填
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
请求示例​

示例一：

{
  "outerId":"string"
}


示例二：

{
  "sysItemId":long
}

响应参数​
全部展开
参数名
类型
描述
必填
item
object
数据字典
必填
success
boolean
示例：true
必填
trace_id
string
示例：3553483395407017
必填
响应示例​
{
    "item": {
        "sysItemId": long,
        "barcode": "string",
        "picPath": "string",
        "activeStatus": long,
        "title": "string",
        "outerId": "string",
        "type": "string",
        "typeTag": long,
        "catId": "string",
        "unit": "string",
        "isSkuItem": long,
        "isVirtual": long,
        "hasSupplier": long,
        "makeGift": boolean,
        "created": long,
        "modified": long,
        "sellerCids": "string",
        "sellerCats": [
            {
                "id": long,
                "cid": long,
                "name": "string"
            }
        ]
    },
    "success": boolean,
    "trace_id": "long"
}

异常示例​
{
    "code": "25",
    "msg": "服务方法(item.single.get:1.0)的签名无效",
    "success": false,
    "trace_id": "3553483395423660"
}

错误码解释​
错误码	错误信息	解决方案
20105	系统主商品ID与平台商家编码，必须二选一！	系统主商品ID与平台商家编码，必须二选一！
20110	查询商品信息有误	请根据返回错误信息进行处理

============================================================
=== 查询库存状态 ===
============================================================
API文档商品查询库存状态
查询库存状态
POST请求地址/router
系统相关界面​

"界面路径：【库存】----【库存状态】"

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
appKey	string	是	分配给应用的AppKey
timestamp	string	是	时间戳，时区为GMT+8，例如：2020-09-21 16:58:00。API服务端允许客户端请求最大时间误差为10分钟
format	string	否	响应格式。默认为json格式，可选值：json
version	string	是	API协议版本 可选值：1.0
sign_method	string	否	签名的摘要算法(默认 hmac)，可选值为：hmac，md5，hmac-sha256。
sign	string	是	签名
session	string	是	授权会话信息 （即access_token，由系统分配）
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
string
application/x-www-form-urlencoded
必填
API接口地址​
全部展开
参数名
类型
描述
必填
method
string
stock.api.status.query
必填
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
brands
string
品牌名称，多个以逗号隔开
relationInfoFields
string
关联字段，多个用逗号隔开，指定查询关联字段信息会在relationInfo中返回， publicStock：公有可用数
请求示例​

示例一：

{
  "stockStatuses": integer,
  "warehouseId": long,
  "pageSize": integer,
  "pageNo": long
}

响应参数​
全部展开
参数名
类型
描述
必填
msg
string
返回消息
必填
traceId
string
流水id
必填
total
long
返回总数(仅在请求参数pageNo传1时才会返回)
必填
stockStatusVoList
array
数据列表
必填
响应示例​
{
  "msg": "zhangsan",
  "traceId": "zhangsan",
  "total": 12345,
  "code": "zhangsan",
  "solution": "zhangsan",
  "subCode": "zhangsan",
  "stockStatusVoList": [
    {
      "supplierCodes": "zhangsan",
      "marketPrice": 10000,
      "totalAvailableStock": 12345,
      "sysItemId": 12345,
      "skuPicPath": "zhangsan",
      "itemCategoryNames": "zhangsan",
      "shortTitle": "zhangsan",
      "totalLockStock": 12345,
      "purchasePrice": 10000,
      "title": "zhangsan",
      "allocateNum": 12345,
      "totalAvailableStockSum": 12345,
      "supplierNames": "zhangsan",
      "sellingPrice": 10000,
      "totalDefectiveStock": 12345,
      "sellableNum": 12345,
      "stockStatus": 1,
      "wareHouseId": 12345,
      "place": "zhangsan",
      "brand": "zhangsan",
      "refundStock": 12345,
      "itemBarcode": "zhangsan",
      "propertiesName": "zhangsan",
      "picPath": "zhangsan",
      "sysSkuId": 12345,
      "mainOuterId": "zhangsan",
      "cidName": "zhangsan",
      "unit": "zhangsan",
      "stockModifiedTime": "@date",
      "outerId": "zhangsan",
      "purchaseNum": 12345,
      "skuBarcode": "zhangsan"
    }
  ],
  "subMsg": "zhangsan",
  "body": "zhangsan",
  "forbiddenField": "zhangsan"
}

异常示例​
{
    "code": "25",
    "msg": "服务方法(stock.api.status.query:1.0)的签名无效",
    "success": false,
    "trace_id": "3553483395423660"
}

错误码解释​
错误码	错误信息	解决方案
50	库存状态不合法	库存状态(1正常，2警戒，3,无货，4超卖)
20001	页码为空或不符合规定	页码的值不能为空，且不能小于1
20002	页数为空或不符合规定	页数的值不能为空，且不能小于1
20019	页数不能超过100	页数不能超过100
20307	商品类型不合法	请检查该参数的值是否规范
20305	仓库ID不能为空或小于0	请检查该参数的值是否为空或小于0

============================================================
=== 销售出库单查询 ===
============================================================
API文档交易销售出库单查询
销售出库单查询
POST请求地址/router
系统相关界面​

"界面路径：【仓储】--【波次】--【销售出库单】"

请求地址​
环境	服务地址(HTTP/HTTPS)
V2正式环境（推荐）	https://gw.superboss.cc/router
V2测试环境	https://gw3.superboss.cc/router

2022年4月1日以后申请的APP Key，统一使用V2正式环境的请求地址：https://gw.superboss.cc/router

公共参数​

调用任何一个API都必须传入的参数，目前支持的公共参数有：

参数名称	参数类型	是否必须	参数描述
method	string	是	API接口名称
appKey	string	是	分配给应用的AppKey
timestamp	string	是	时间戳，时区为GMT+8，例如：2020-09-21 16:58:00。API服务端允许客户端请求最大时间误差为10分钟
format	string	否	响应格式。默认为json格式，可选值：json
version	string	是	API协议版本 可选值：1.0
sign_method	string	否	签名的摘要算法(默认 hmac)，可选值为：hmac，md5，hmac-sha256。
sign	string	是	签名
session	string	是	授权会话信息 （即access_token，由系统分配）
请求头​
全部展开
参数名
类型
描述
必填
Content-Type
string
application/x-www-form-urlencoded;charset=UTF-8
必填
API接口地址​
全部展开
参数名
类型
描述
必填
method
string
erp.wave.logistics.order.query
必填
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
string
物流单号列表，多个用逗号分隔，最多50个
否
waveIds
string
波次号列表，多个用逗号分隔，最多50个
否
pageNo
integer
页码
否
1
pageSize
integer
每页数量，最大200
否
40
请求示例​

示例一：

{
 "timeType": 2,
 "timeBegin": 1609459200000,
 "timeEnd": 1612137599000,
 "userIds": "123456,789012",
 "statusList": "10,20,30",
 "pageNo": 1,
 "pageSize": 40
}


示例二：

{
 "timeType": 2,
 "timeBegin": 1609459200000,
 "timeEnd": 1612137599000,
 "userIds": "123456,789012",
 "statusList": "10,20,30",
 "sids": "202401010001,202401010002",
 "shortIds": "SH001,SH002",
 "tids": "TB202401010001,TB202401010002",
 "outSids": "SF1234567890,SF0987654321",
 "waveIds": "1001,1002",
 "pageNo": 1,
 "pageSize": 40
}

响应参数​
全部展开
参数名
类型
描述
必填
list
array
销售出库单列表
total
long
总记录数
pageNo
integer
当前页码
pageSize
integer
每页数量
success
boolean
是否成功
trace_id
string
追踪ID
响应示例​
{
    "traceId": "4115985342831359490",
    "success": true,
    "total": 100,
    "pageNo": 1,
    "pageSize": 40,
    "list": [
        {
            "id": 123456789,
            "userId": 100001,
            "shopName": "测试店铺",
            "sid": "202401010001",
            "shortId": "SH001",
            "tid": "TB202401010001",
            "outSid": "SF1234567890",
            "createTime": "2024-01-01 10:00:00",
            "modifyTime": "2024-01-01 11:00:00",
            "outTime": "2024-01-01 12:00:00",
            "payTime": "2024-01-01 09:00:00",
            "status": 30,
            "statusName": "发货中",
            "receiverCountry": "CN",
            "receiverState": "浙江省",
            "receiverCity": "杭州市",
            "receiverDistrict": "西湖区",
            "receiverStreet": "文三路",
            "receiverAddress": "文三路123号",
            "receiverName": "",
            "receiverMobile": "",
            "receiverPhone": "",
            "buyerMessage": "请尽快发货",
            "sellerMemo": "重要客户",
            "sysMemo": "系统备注",
            "payment": 199.00,
            "payAmount": 199.00,
            "discountFee": 0.00,
            "logisticsCompanyId": 1,
            "logisticsCompanyName": "顺丰速运",
            "netWeight": "1.5",
            "volume": "0.5",
            "theoryPostFee": "15.00",
            "actualPostFee": "15.00",
            "warehouseId": 10,
            "warehouseName": "杭州仓",
            "waveId": 1001,
            "itemNum": 2,
            "itemKindNum": 1,
            "tradeTagList": [],
            "details": [
                {
                    "logisticsOrderId": 123456789,
                    "itemOuterId": "ITEM001",
                    "skuOuterId": "SKU001",
                    "sysTitle": "测试商品",
                    "sysSkuPropertiesName": "颜色:红色;尺码:L",
                    "sysPicPath": "https://example.com/pic.jpg",
                    "num": 2,
                    "type": 0,
                    "isGift": 0,
                    "isPick": 1,
                    "isNonConsign": 0,
                    "isVirtual": 0,
                    "weight": 1.5,
                    "volume": 0.5,
                    "status": 0,
                    "priceImport": 100.00,
                    "price": 100.00,
                    "payment": 100.00,
                    "payAmount": 100.00,
                    "discountFee": 0.00,
                    "sysItemRemark": "商品备注"
                    "relatedDetailId": 987654321
                }
            ]
        }
    ]
}

异常示例​
{
    "code": "25",
    "msg": "服务方法(erp.wave.logistics.order.query:1.0)的签名无效",
    "success": false,
    "trace_id": "3553483395423660"
}

{
    "code": "32",
    "msg": "请选择时间类型！",
    "success": false,
    "trace_id": "3553483395423661"
}

{
    "code": "33",
    "msg": "单页数量不能超过200！",
    "success": false,
    "trace_id": "3553483395423662"
}

{
    "code": "33",
    "msg": "波次号最多不能超过50个",
    "success": false,
    "trace_id": "3553483395423663"
}

{
    "code": "9",
    "msg": "销售出库单查询失败！",
    "success": false,
    "trace_id": "3553483395423664"
}

错误码解释​
错误信息	错误码	解决方案
资源异常,请联系管理员!	400	检查参数是否合法
销售出库单查询失败！	9	业务逻辑错误，请联系管理员
请选择时间类型！	32	未设置单号时，timeType参数必填
请输入开始时间！	32	未设置单号时，timeBegin参数必填且大于0
请输入结束时间！	32	未设置单号时，timeEnd参数必填且大于0
单页数量不能超过200！	33	pageSize参数最大值为200
波次号最多不能超过50个	33	waveIds参数最多不能超过50个
系统单号最多不能超过50个	33	sids参数最多不能超过50个
平台单号最多不能超过50个	33	tids参数最多不能超过50个
内部单号最多不能超过50个	33	shortIds参数最多不能超过50个
物流单号最多不能超过50个	33	outSids参数最多不能超过50个
状态说明​

销售出库单状态（status）：

10：待处理
20：预处理完成
30：发货中
50：已发货
70：已关闭
90：已作废

时间类型（timeType）：

1：创建时间
2：发货时间
3：付款时间
4：下单时间
5：承诺时间
6：打印时间

商品类型（type）：

1：普通商品
2：套件本身
3：套件子商品
4：加工商品本身
5：加工子商品
6：组合商品本身
7：组合子商品
8：新版包材商品
