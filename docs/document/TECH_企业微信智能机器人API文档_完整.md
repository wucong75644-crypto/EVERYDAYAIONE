# 企业微信智能机器人 API 文档（完整版）

> 来源：https://developer.work.weixin.qq.com/document/
> 爬取时间：2026-03-15
> 涵盖：智能机器人全部 API 接口文档

## 目录

- [概述](https://developer.work.weixin.qq.com/document/path/101039)
- [接收消息](https://developer.work.weixin.qq.com/document/path/100719)
- [接收事件](https://developer.work.weixin.qq.com/document/path/101027)
- [被动回复消息](https://developer.work.weixin.qq.com/document/path/101031)
- [模板卡片类型](https://developer.work.weixin.qq.com/document/path/101032)
- [回调和回复的加解密方案](https://developer.work.weixin.qq.com/document/path/101033)
- [主动回复消息](https://developer.work.weixin.qq.com/document/path/101138)
- [智能机器人长连接](https://developer.work.weixin.qq.com/document/path/101463)
- [API模式机器人文档使用说明](https://developer.work.weixin.qq.com/document/path/101468)

**自建应用 消息接收与发送：**

- [消息接收与发送 概述](https://developer.work.weixin.qq.com/document/path/90235)
- [发送应用消息](https://developer.work.weixin.qq.com/document/path/90236)
- [更新模版卡片消息](https://developer.work.weixin.qq.com/document/path/94888)
- [撤回应用消息](https://developer.work.weixin.qq.com/document/path/94867)
- [接收消息与事件 概述](https://developer.work.weixin.qq.com/document/path/90238)
- [接收消息与事件 消息格式](https://developer.work.weixin.qq.com/document/path/90239)
- [接收消息与事件 事件格式](https://developer.work.weixin.qq.com/document/path/90240)
- [被动回复消息格式](https://developer.work.weixin.qq.com/document/path/90241)
- [应用发送消息到群聊会话](https://developer.work.weixin.qq.com/document/path/90244)
- [创建群聊会话](https://developer.work.weixin.qq.com/document/path/90245)
- [应用推送消息](https://developer.work.weixin.qq.com/document/path/90248)

---

# 概述

> 最后更新：2025/08/18

目录

- API设置
- 接收回调与被动回复
- 示例代码

### API设置

若智能机器人开启API模式，当用户跟智能机器人交互时，企业微信会向智能机器人API设置中的URL的回调地址推送相关消息跟事件。


### 接收回调与被动回复

当用户跟智能机器人交互时，企业微信会向智能机器人的回调URL上推送相关消息或者事件，开发者可根据接收的消息或者事件，被动回复消息。具体流程如下：


### 示例代码

我们以python为例，提供了一份示例代码，以供开发者参考（包含python2与python3两个版本）：点击下载。


---

# 接收消息

> 最后更新：2026/03/12

目录

- 概述
- 消息推送
-       文本消息
-       图片消息
-       图文混排消息
-       语音消息
-       文件消息
-       视频消息
-       结构体说明
-             文本
-             图片
-             图文混排
-             语音
-             文件
-             视频
-             引用
- 流式消息刷新

## 概述

当用户与智能机器人发生交互，向机器人发送消息时，交互事件将加密回调给机器人接受消息url，智能机器人服务通过接收并处理回调消息，实现更加丰富的自定义功能。 目前支持触发消息回调交互场景：

- 用户群里@智能机器人或者单聊中向智能机器人发送文本消息
- 用户群里@智能机器人或者单聊中向智能机器人发送图文混排消息
- 用户单聊中向智能机器人发送图片消息
- 用户单聊中向智能机器人发送语音消息
- 用户单聊中向智能机器人发送本地文件消息
- 用户单聊中向智能机器人发送视频消息
- 用户群里@智能机器人或者单聊中向智能机器人发送引用消息         交互流程如下图所示：流程说明：1.当用户跟智能机器人交互发送支持的消息类型时，企业微信后台会向开发者后台推送消息推送。用户跟同一个智能机器人最多同时有三条消息交互中。2.开发者回调url接收到新消息推送后：可选择生成流式消息回复，并使用用户消息内容调用大模型/AIAgent；也可直接回复模板卡片消息。3.若开发者回复消息类型包含流式消息，企业微信在未收到流式消息回复结束前，会不断向开发者回调url推送流式消息刷新（从用户发消息开始最多等待6min，超过6min结束推送）。开发者接收到流式消息刷新后，生成流式消息回复。接收消息与被动回复消息都是加密的，加密方式参考回调和回复的加解密方案。

## 消息推送


### 文本消息

协议格式如下：

```json
{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "aibotid": "AIBOTID",
    "chatid": "CHATID",
    "chattype": "group",
    "from": {
        "userid": "USERID"
    },
    "response_url": "RESPONSEURL",
    "msgtype": "text",
    "text": {
        "content": "@RobotA hello robot"
    },
    "quote": {
        "msgtype": "text",
        "text": {
            "content": "这是今日的测试情况"
        }
    }
}
```


| 参数 | 说明 |
| --- | --- |
| msgid | 本次回调的唯一性标志，开发者需据此进行事件排重（可能因为网络等原因重复回调） |
| aibotid | 智能机器人id |
| chatid | 会话id，仅群聊类型时候返回 |
| chattype | 会话类型，single\group，分别表示：单聊\群聊 |
| from | 该事件触发者的信息 |
| from.userid | 操作者的userid |
| response_url | 支持主动回复消息的临时url |
| msgtype | 消息类型，此时固定是text |
| text | 文本消息内容，可参考 文本 结构体说明 |
| quote | 引用内容，若用户引用了其他消息则有该字段，可参考 引用 结构体说明 |


### 图片消息

```json
{
    "msgid": "CAIQz7/MjQYY/NGagIOAgAMgl8jK/gI=",
    "aibotid": "AIBOTID",
    "chattype": "single",
    "from": {
        "userid": "USERID"
    },
    "response_url": "RESPONSEURL",
    "msgtype": "image",
    "image": {
        "url": "https://ww-aibot-img-1258476243.cos.ap-guangzhou.myqcloud.com/BHoPdA3/7571665296904772241?sign=q-sign-algorithm%3Dsha1%26q-ak%3DAKIDbBpaJvdLBvpnibcYcfyPuaO5f9U1UoGo%26q-sign-time%3D1733467811%3B1733468111%26q-key-time%3D1733467811%3B1733468111%26q-header-list%3Dhost%26q-url-param-list%3D%26q-signature%3D0f7b6576943685f82870bc8db306dbf09dfe0fd6"
    }
}
```


### 图文混排消息

```json
{
    "msgid": "CAIQrcjMjQYY/NGagIOAgAMg6PDc/w0=",
    "aibotid": "AIBOTID",
    "chatid": "CHATID",
    "chattype": "group",
    "from": {
        "userid": "USERID"
    },
    "response_url": "RESPONSEURL",
    "msgtype": "mixed",
    "mixed": {
        "msg_item": [
            {
                "msgtype": "text",
                "text": {
                    "content": "@机器人 这是今日的测试情况"
                }
            },
            {
                "msgtype": "image",
                "image": {
                    "url": "https://ww-aibot-img-1258476243.cos.ap-guangzhou.myqcloud.com/BHoPdA3/7571665296904772241?sign=q-sign-algorithm%3Dsha1%26q-ak%3DAKIDbBpaJvdLBvpnibcYcfyPuaO5f9U1UoGo%26q-sign-time%3D1733467811%3B1733468111%26q-key-time%3D1733467811%3B1733468111%26q-header-list%3Dhost%26q-url-param-list%3D%26q-signature%3D0f7b6576943685f82870bc8db306dbf09dfe0fd6"
                }
            }
        ]
    },
    "quote": {
        "msgtype": "text",
        "text": {
            "content": "这是今日的测试情况"
        }
    }
}
```


### 语音消息

{
    "msgid": "CAIQrcjMjQYY/NGagIOAgAMg6PDc/w0=",
    "aibotid": "AIBOTID",
    "chattype": "single",
    "from": {
        "userid": "USERID"
    },
    "response_url": "RESPONSEURL",
    "msgtype": "voice",
    "voice": {
        "content": "这是语音转成文本的内容"
    }
}
    参数说明：


### 文件消息

{
    "msgid": "CAIQrcjMjQYY/NGagIOAgAMg6PDc/w0=",
    "aibotid": "AIBOTID",
    "chattype": "single",
    "from": {
        "userid": "USERID"
    },
    "response_url": "RESPONSEURL",
    "msgtype": "file",
    "file": {
        "url": "https://ww-aibot-img-1258476243.cos.ap-guangzhou.myqcloud.com/BHoPdA3/7571665296904772241?sign=q-sign-algorithm%3Dsha1%26q-ak%3DAKIDbBpaJvdLBvpnibcYcfyPuaO5f9U1UoGo%26q-sign-time%3D1733467811%3B1733468111%26q-key-time%3D1733467811%3B1733468111%26q-header-list%3Dhost%26q-url-param-list%3D%26q-signature%3D0f7b6576943685f82870bc8db306dbf09df00000"
    }
}
    参数说明：


### 视频消息

{
    "msgid": "CAIQrcjMjQYY/NGagIOAgAMg6PDc/w0=",
    "aibotid": "AIBOTID",
    "chattype": "single",
    "from": {
        "userid": "USERID"
    },
    "response_url": "RESPONSEURL",
    "msgtype": "video",
    "video": {
        "url": "https://ww-aibot-img-1258476243.cos.ap-guangzhou.myqcloud.com/BHoPdA3/7571665296904772241?sign=q-sign-algorithm%3Dsha1%26q-ak%3DAKIDbBpaJvdLBvpnibcYcfyPuaO5f9U1UoGo%26q-sign-time%3D1733467811%3B1733468111%26q-key-time%3D1733467811%3B1733468111%26q-header-list%3Dhost%26q-url-param-list%3D%26q-signature%3D0f7b6576943685f82870bc8db306dbf09df00000"
    }
}
    参数说明：


### 结构体说明


#### 文本

```json
{
    "content": "@RobotA hello robot"
}
```


#### 图片

```json
{
    "url": "https://ww-aibot-img-1258476243.cos.ap-guangzhou.myqcloud.com/BHoPdA3/7571665296904772241?sign=q-sign-algorithm%3Dsha1%26q-ak%3DAKIDbBpaJvdLBvpnibcYcfyPuaO5f9U1UoGo%26q-sign-time%3D1733467811%3B1733468111%26q-key-time%3D1733467811%3B1733468111%26q-header-list%3Dhost%26q-url-param-list%3D%26q-signature%3D0f7b6576943685f82870bc8db306dbf09dfe0fd6"
}
```


#### 图文混排

```json
{
    "msg_item": [
        {
            "msgtype": "text",
            "text": {
                "content": "@机器人 这是今日的测试情况"
            }
        },
        {
            "msgtype": "image",
            "image": {
                "url": "URL"
            }
        }
    ]
}
```


#### 语音

```json
{
    "content": "这是语音转成文本的内容"
}
```


#### 文件

{
    "url": "https://ww-aibot-img-1258476243.cos.ap-guangzhou.myqcloud.com/BHoPdA3/7571665296904772241?sign=q-sign-algorithm%3Dsha1%26q-ak%3DAKIDbBpaJvdLBvpnibcYcfyPuaO5f9U1UoGo%26q-sign-time%3D1733467811%3B1733468111%26q-key-time%3D1733467811%3B1733468111%26q-header-list%3Dhost%26q-url-param-list%3D%26q-signature%3D0f7b6576943685f82870bc8db306dbf09dfe0fd6"
}
    参数说明：


#### 视频

{
    "url": "https://ww-aibot-img-1258476243.cos.ap-guangzhou.myqcloud.com/BHoPdA3/7571665296904772241?sign=q-sign-algorithm%3Dsha1%26q-ak%3DAKIDbBpaJvdLBvpnibcYcfyPuaO5f9U1UoGo%26q-sign-time%3D1733467811%3B1733468111%26q-key-time%3D1733467811%3B1733468111%26q-header-list%3Dhost%26q-url-param-list%3D%26q-signature%3D0f7b6576943685f82870bc8db306dbf09dfe0fd6"
}
    参数说明：


#### 引用

```json
{
    "msgtype": "text",
    "text": {
        "content": "这是今日的测试情况"
    },
    "image": {
        "url": "URL"
    },
    "mixed": {
        "msg_item": [
            {
                "msgtype": "text",
                "text": {
                    "content": "@机器人 这是今日的测试情况"
                }
            },
            {
                "msgtype": "image",
                "image": {
                    "url": "URL"
                }
            }
        ]
    },
    "voice": {
        "content": "这是语音转成文本的内容"
    },
    "file": {
        "url": "URL"
    },
    "video": {
        "url": "URL"
    }
}
```


## 流式消息刷新

{
    "msgid": "CAIQz7/MjQYY/NGagIOAgAMgl8jK/gI=",
    "aibotid": "AIBOTID",
    "chatid": "CHATID",
    "chattype": "group",
    "from": {
        "userid": "USERID"
    },
    "msgtype": "stream",
    "stream": {
        "id": "STREAMID"
    }
}
    参数说明：


---

# 接收事件

> 最后更新：2025/11/25

目录

- 事件通用格式
- 事件格式
-       进入会话事件
-       模板卡片事件
-             按钮交互模版卡片的事件
-             投票选择模版卡片的事件
-             多项选择模版卡片的事件
-             模版卡片右上角菜单事件
-       用户反馈事件
当用户与智能机器人发生交互的时候，交互事件将加密回调给机器人接受消息url，智能机器人服务通过接收并处理回调事件，实现更加丰富的自定义功能。


## 事件通用格式

智能机器人回调事件通用协议示例：

```json
{
   
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
	"create_time":1700000000,
    "aibotid": "AIBOTID",
	"chatid":"CHATID",
	"chattype":"single",
    "from": {
	 	"corpid": "wpxxxx",
        "userid": "USERID"
    },
    "msgtype": "event",
    "event": {
        "eventtype": "eventtype_name",
		      "eventtype_name":{
			  }
    }
}
```


| 参数 | 是否必填 | 说明 |
| --- | --- | --- |
| msgid | 是 | 本次回调的唯一性标志，开发者需据此进行事件排重（可能因为网络等原因重复回调） |
| create_time | 是 | 本次回调事件产生的时间 |
| aibotid | 是 | 智能机器人id |
| chatid | 否 | 群聊id |
| chattype | 是 | 会话类型，single\group，分别表示：单聊\群聊 |
| from | 是 | 该事件触发者的信息，详见From结构体 |
| msgtype | 是 | 消息类型，若为事件通知，固定为event |
| event | 是 | 若为事件通知，事件结构体，参考Event结构体说明 |

From结构体说明：

Event结构体说明：


## 事件格式

所有的回调事件都遵循通用协议格式。


### 进入会话事件

当用户当天首次进入智能机器人单聊会话时，触发该事件。开发者可回复一条文本消息或者模板卡片消息。

协议格式如下：

```json
{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
	"create_time":1700000000,
    "aibotid": "AIBOTID",
    "from": {
		"corpid": "wpxxxx",
        "userid": "USERID"
    },
    "msgtype": "event",
    "event": {
        "eventtype": "enter_chat"
    }
}
```


| 参数 | 说明 |
| --- | --- |
| eventtype | 事件类型，此处固定为enter_chat |


### 模板卡片事件

按钮交互、投票选择和多项选择模版卡片中的按钮点击后，企业微信会将相应事件发送给机器人

模板卡片事件通用协议示例：

{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "create_time": 1700000000,
    "aibotid": "AIBOTID",
    "from": {
        "corpid": "CORPID",
        "userid": "USERID"
    },
    "chatid": "CHATID",
    "chattype": "group",
    "response_url": "RESPONSEURL",
    "msgtype": "event",
    "event": {
        "eventtype": "template_card_event",
        "template_card_event": {
            "card_type": "vote_interaction",
            "event_key": "button_replace_text",
            "task_id": "fBmjTL7ErRCQSNA6GZKMlcFiWX1shOvg",
            "selected_items": {
                "selected_item": [
                    {
                        "question_key": "button_selection_key1",
                        "option_ids": {
                            "option_id": [
                                "button_selection_id1"
                            ]
                        }
                    }
                ]
            }
        }
    }
}
    其中，eventtype固定为template_card_event。对应结构体TemplateCardEvent。

```json
{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "create_time": 1700000000,
    "aibotid": "AIBOTID",
    "from": {
        "corpid": "CORPID",
        "userid": "USERID"
    },
    "chatid": "CHATID",
    "chattype": "group",
    "response_url": "RESPONSEURL",
    "msgtype": "event",
    "event": {
        "eventtype": "template_card_event",
        "template_card_event": {
            "card_type": "vote_interaction",
            "event_key": "button_replace_text",
            "task_id": "fBmjTL7ErRCQSNA6GZKMlcFiWX1shOvg",
            "selected_items": {
                "selected_item": [
                    {
                        "question_key": "button_selection_key1",
                        "option_ids": {
                            "option_id": [
                                "button_selection_id1"
                            ]
                        }
                    }
                ]
            }
        }
    }
}
```

参数说明：

TemplateCardEvent结构说明：

参考SeletedItem结构说明：


#### 按钮交互模版卡片的事件

当用户点击机器人的按钮交互卡片模块消息的按钮时候，触发相应的回调事件回调示例

{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "create_time": 1700000000,
    "aibotid": "AIBOTID",
    "from": {
        "corpid": "CORPID",
        "userid": "USERID"
    },
    "chatid": "CHATID",
    "chattype": "group",
    "response_url": "RESPONSEURL",
    "msgtype": "event",
    "event": {
        "eventtype": "template_card_event",
        "template_card_event": {
            "card_type": "button_interaction",
            "event_key": "button_replace_text",
            "task_id": "fBmjTL7ErRCQSNA6GZKMlcFiWX1shOvg",
            "selected_items": {
                "selected_item": [
                    {
                        "question_key": "button_selection_key1",
                        "option_ids": {
                            "option_id": [
                                "button_selection_id1"
                            ]
                        }
                    }
                ]
            }
        }
    }
}
    参数说明：


#### 投票选择模版卡片的事件

当用户在选择选项后，点击按钮触发相应的回调事件回调示例

{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "create_time": 1700000000,
    "aibotid": "AIBOTID",
    "from": {
        "corpid": "CORPID",
        "userid": "USERID"
    },
    "chatid": "CHATID",
    "chattype": "group",
    "response_url": "RESPONSEURL",
    "msgtype": "event",
    "event": {
        "eventtype": "template_card_event",
        "template_card_event": {
            "card_type": "vote_interaction",
            "event_key": "button_replace_text",
            "task_id": "fBmjTL7ErRCQSNA6GZKMlcFiWX1shOvg",
            "selected_items": {
                "selected_item": [
                    {
                        "question_key": "button_selection_key1",
                        "option_ids": {
                            "option_id": [
                                "one",
                                "two"
                            ]
                        }
                    }
                ]
            }
        }
    }
}
    
        参数说明cardtype模版卡片的模版类型,此处固定为 vote_interactionselected_items用户点击提交的投票选择框数据 


#### 多项选择模版卡片的事件

当用户在下拉框选择选项后，点击按钮触发相应的回调事件回调示例

{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "create_time": 1700000000,
    "aibotid": "AIBOTID",
    "from": {
        "userid": "USERID"
    },
    "chatid": "CHATID",
    "chattype": "group",
    "response_url": "RESPONSEURL",
    "msgtype": "event",
    "event": {
        "eventtype": "template_card_event",
        "template_card_event": {
            "card_type": "multiple_interaction",
            "event_key": "button_replace_text",
            "task_id": "fBmjTL7ErRCQSNA6GZKMlcFiWX1shOvg",
            "selected_items": {
                "selected_item": [
                    {
                        "question_key": "button_selection_key1",
                        "option_ids": {
                            "option_id": [
                                "button_selection_id1"
                            ]
                        }
                    },
                    {
                        "question_key": "button_selection_key2",
                        "option_ids": {
                            "option_id": [
                                "button_selection_id2"
                            ]
                        }
                    }
                ]
            }
        }
    }
}
    
        参数说明cardtype模版卡片的模版类型,此处固定为 multiple_interactionselected_items用户点击提交的下拉菜单选择框列表数据模版卡片右上角菜单事件用户点击文本通知，图文展示和按钮交互卡片右上角的菜单时会弹出菜单选项，当用户点击具体选项的时候会触发相应的回调事件回调示例


#### 模版卡片右上角菜单事件

{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "create_time": 1700000000,
    "aibotid": "AIBOTID",
    "from": {
        "userid": "USERID"
    },
    "chatid": "CHATID",
    "chattype": "group",
    "response_url": "RESPONSEURL",
    "msgtype": "event",
    "event": {
        "eventtype": "template_card_event",
        "template_card_event": {
            "card_type": "text_notice",
            "event_key": "button_replace_text",
            "task_id": "fBmjTL7ErRCQSNA6GZKMlcFiWX1shOvg"
        }
    }
}

    
        参数说明cardtype模版卡片的模版类型,此处可能为 text_notice ,  news_notice 和 button_interaction用户反馈事件开发者接收到智能机器人的消息事件后，可以将事件与即将回复的流式消息关联起来，并在被动回复/主动回复消息中设置反馈信息。当用户进行反馈时，可以收到用户反馈事件，复盘智能机器人的回复效果


### 用户反馈事件

{
    "msgid": "CAIQ16HMjQYY\/NGagIOAgAMgq4KM0AI=",
    "create_time": 1700000000,
    "aibotid": "AIBOTID",
    "chatid": "CHATID",
    "chattype": "group",
    "from": {
        "userid": "USERID"
    },
    "msgtype": "event",
    "event": {
        "eventtype": "feedback_event",
        "feedback_event": {
            "id": "FEEDBACKID",
            "type": 2,
            "content": "能再详细一些么",
            "inaccurate_reason_list": [
                2,
                4
            ]
        }
    }
}
    参数说明：


---

# 被动回复消息

> 最后更新：2025/10/21

目录

- 概述
- 回复欢迎语
-       文本消息
-       模板卡片消息
- 回复用户消息
-       流式消息回复
-       模板卡片消息
-       流式消息+模板卡片回复
- 回复消息更新模板卡片
-       模版卡片更新消息

## 概述

当用户与智能机器人进行交互时，企业微信会将相关的交互事件回调到开发者设置的回调URL，开发者可根据事件类型做出相应的响应，实现丰富的自定义功能。

目前主要有以下场景支持回复消息：

- 用户当天首次进入智能机器人单聊会话，回复欢迎语
- 用户向智能机器人发送消息 ，回复消息
- 用户点击模板卡片相关按钮等，回复消息更新模板卡片

## 回复欢迎语


### 文本消息

```json
{
  "msgtype": "text",
  "text": {
    "content": "hello\nI'm RobotA\n"
  }
}
```


| 参数 | 类型 | 必须 | 说明 |
| --- | --- | --- | --- |
| msgtype | String | 是 | 此时固定为text |
| text | Object | 是 | 文本消息的详细内容 |
| text.content | String | 是 | 文本内容 |


### 模板卡片消息

```json
{
    "msgtype": "template_card",
    "template_card": {
        "card_type": "multiple_interaction",
        "source": {
            "icon_url": 		 "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0",
            "desc": "企业微信"
        },
        "main_title": {
            "title": "欢迎使用企业微信",
            "desc": "您的好友正在邀请您加入企业微信"
        },
        "select_list": [
            {
                "question_key": "question_key_one",
                "title": "选择标签1",
                "disable": false,
                "selected_id": "id_one",
                "option_list": [
                    {
                        "id": "id_one",
                        "text": "选择器选项1"
                    },
                    {
                        "id": "id_two",
                        "text": "选择器选项2"
                    }
                ]
            },
            {
                "question_key": "question_key_two",
                "title": "选择标签2",
                "selected_id": "id_three",
                "option_list": [
                    {
                        "id": "id_three",
                        "text": "选择器选项3"
                    },
                    {
                        "id": "id_four",
                        "text": "选择器选项4"
                    }
                ]
            }
        ],
        "submit_button": {
            "text": "提交",
            "key": "submit_key"
        },
        "task_id": "task_id"
    }
}
```


## 回复用户消息


### 流式消息回复

```json
{
    "msgtype": "stream",
    "stream": {
        "id": "STREAMID",
        "finish": false,
        "content": "**广州**今日天气：29度，大部分多云，降雨概率：60%",
        "msg_item": [
            {
                "msgtype": "image",
                "image": {
                    "base64": "BASE64",
                    "md5": "MD5"
                }
            }
        ],
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


| 参数 | 类型 | 是否必填 | 说明 |
| --- | --- | --- | --- |
| msgtype | String | 是 | 消息类型，此时固定为：stream |
| stream.id | String | 否，流式消息首次回复的时候要求设置 | 自定义的唯一id，某次回调的首次回复第一次设置，后续回调会根据这个id来获取最新的流式消息 |
| stream.finish | Bool | 否 | 流式消息是否结束 |
| stream.content | String | 否 | 流式消息内容，最长不超过20480个字节，必须是utf8编码。特殊的，第一次回复内容为"1"，第二次回复"123"，则此时消息展示内容"123" |
| stream.msg_item | Object[] | 否 | 流式消息图文混排消息列表。 |
| stream.msg_item.msgtype | String | 否 | 图文混排消息类型，目前支持：image特殊的，目前image的消息类型仅当finish=true，即流式消息结束的最后一次回复中设置 |
| stream.msg_item.image | Object | 否 | 图片混排的图片资源。目前最多支持设置10个 |
| image.base64 | String | 是 | 图片内容的base64编码。图片（base64编码前）最大不能超过10M，支持JPG,PNG格式 |
| image.md5 | String | 是 | 图片内容（base64编码前）的md5值 |
| stream.feedback.id | String | 否 | 流式消息首次回复时候若字段不为空值，回复的消息被用户反馈时候会触发回调事件。有效长度为 256 字节以内，必须是 utf-8 编码。 |


### 模板卡片消息

```json
{
    "msgtype": "template_card",
    "template_card": {
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


### 流式消息+模板卡片回复

若开发者需要回复流式消息外，还需要回复模板卡片，可回复该消息类型。

```json
{
    "msgtype": "stream_with_template_card",
    "stream": {
        "id": "STREAMID",
        "finish": false,
        "content": "**广州**今日天气：29度，大部分多云，降雨概率：60%",
        "msg_item": [
            {
                "msgtype": "image",
                "image": {
                    "base64": "BASE64",
                    "md5": "MD5"
                }
            }
        ],
        "feedback": {
            "id": "FEEDBACKID"
        }
    },
    "template_card": {
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


## 回复消息更新模板卡片


### 模版卡片更新消息

当机器人服务接收到模版卡片事件后，可以在该事件的返回包中添加消息进行即时响应。

```json
{
    "response_type": "update_template_card",
    "userids": [
        "USERID1",
        "USERID2"
    ],
    "template_card": {
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


---

# 模板卡片类型

> 最后更新：2026/02/06

目录

- 模版卡片类型
-       文本通知模版卡片
-       图文展示模版卡片
-       按钮交互模版卡片
-       投票选择模版卡片
-       多项选择模版卡片
- 结构体说明
-             Source结构体
-             ActionMenu结构体
-             MainTitle结构体
-             EmphasisContent结构体
-             QuoteArea结构体
-             HorizontalContent结构体
-             JumpAction结构体
-             CardAction结构体
-             VerticalContent结构体
-             CardImage结构体
-             ImageTextArea结构体
-             SubmitButton结构体
-             SelectionItem结构体
-             Button结构体
-             Checkbox结构体

## 模版卡片类型

该文档主要说明各种类型模板卡片TemplateCard结构体说明。


### 文本通知模版卡片

文本通知模版卡片消息示例完整文本通知模版卡片示例

{
    "card_type": "text_notice",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0",
        "desc": "企业微信",
        "desc_color": 0
    },
    "action_menu": {
        "desc": "消息气泡副交互辅助文本说明",
        "action_list": [
            {
                "text": "接收推送",
                "key": "action_key1"
            },
            {
                "text": "不再推送",
                "key": "action_key2"
            }
        ]
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "emphasis_content": {
        "title": "100",
        "desc": "数据含义"
    },
    "quote_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH",
        "title": "引用文本标题",
        "quote_text": "Jack：企业微信真的很好用~\nBalian：超级好的一款软件！"
    },
    "sub_title_text": "下载企业微信还能抢红包！",
    "horizontal_content_list": [
        {
            "keyname": "邀请人",
            "value": "张三"
        },
        {
            "keyname": "企微官网",
            "value": "点击访问",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        },
        {
            "keyname": "企微下载",
            "value": "企业微信.apk",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        }
    ],
    "jump_list": [
        {
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi",
            "title": "企业微信官网"
        },
        {
            "type": 2,
            "appid": "APPID",
            "pagepath": "PAGEPATH",
            "title": "跳转小程序"
        },
        {
            "type": 3,
            "title": "企业微信官网",
            "question": "如何登录企业微信官网"
        }
    ],
    "card_action": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH"
    },
    "task_id": "task_id"
}
    请求参数

```json
{
    "card_type": "text_notice",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0",
        "desc": "企业微信",
        "desc_color": 0
    },
    "action_menu": {
        "desc": "消息气泡副交互辅助文本说明",
        "action_list": [
            {
                "text": "接收推送",
                "key": "action_key1"
            },
            {
                "text": "不再推送",
                "key": "action_key2"
            }
        ]
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "emphasis_content": {
        "title": "100",
        "desc": "数据含义"
    },
    "quote_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH",
        "title": "引用文本标题",
        "quote_text": "Jack：企业微信真的很好用~\nBalian：超级好的一款软件！"
    },
    "sub_title_text": "下载企业微信还能抢红包！",
    "horizontal_content_list": [
        {
            "keyname": "邀请人",
            "value": "张三"
        },
        {
            "keyname": "企微官网",
            "value": "点击访问",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        },
        {
            "keyname": "企微下载",
            "value": "企业微信.apk",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        }
    ],
    "jump_list": [
        {
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi",
            "title": "企业微信官网"
        },
        {
            "type": 2,
            "appid": "APPID",
            "pagepath": "PAGEPATH",
            "title": "跳转小程序"
        },
        {
            "type": 3,
            "title": "企业微信官网",
            "question": "如何登录企业微信官网"
        }
    ],
    "card_action": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH"
    },
    "task_id": "task_id"
}
```


| 参数 | 类型 | 必须 | 说明 |
| --- | --- | --- | --- |
| card_type | String | 是 | 模版卡片的模版类型，文本通知模版卡片的类型为text_notice |
| source | Object | 否 | 卡片来源样式信息，不需要来源样式可不填写。参考Source结构体说明 |
| action_menu | Object | 否 | 卡片右上角更多操作按钮。参考ActionMenu结构体说明 |
| main_title | Object | 否 | 模版卡片的主要内容，包括一级标题和标题辅助信息。参考MainTitle结构体说明 |
| emphasis_content | Object | 否 | 关键数据样式，建议不与引用样式共用。参考EmphasisContent结构体说明 |
| quote_area | Object | 否 | 引用文献样式，建议不与关键数据共用。参考QuoteArea结构体说明 |
| sub_title_text | String | 否 | 二级普通文本，建议不超过112个字。模版卡片主要内容的一级标题main_title.title和二级普通文本sub_title_text必须有一项填写 |
| horizontal_content_list | Object[] | 否 | 二级标题+文本列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过6。参考HorizontalContent结构体说明 |
| jump_list | Object[] | 否 | 跳转指引样式的列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过3。参考JumpAction结构体说明 |
| card_action | Object | 是 | 整体卡片的点击跳转事件，text_notice模版卡片中该字段为必填项。参考CardAction结构体说明 |
| task_id | String | 否 | 任务id，当文本通知模版卡片有action_menu字段的时候，该字段必填。同一个机器人任务id不能重复，只能由数字、字母和“_-@”组成，最长128字节。任务id只在发消息时候有效，更新消息的时候无效。任务id将会在相应的回调事件中返回 |


### 图文展示模版卡片

图文展示模版卡片消息示例完整图文展示模版卡片示例

{
    "card_type": "news_notice",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0",
        "desc": "企业微信",
        "desc_color": 0
    },
    "action_menu": {
        "desc": "消息气泡副交互辅助文本说明",
        "action_list": [
            {
                "text": "接收推送",
                "key": "action_key1"
            },
            {
                "text": "不再推送",
                "key": "action_key2"
            }
        ]
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "card_image": {
        "url": "https://wework.qpic.cn/wwpic/354393_4zpkKXd7SrGMvfg_1629280616/0",
        "aspect_ratio": 2.25
    },
    "image_text_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com",
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信",
        "image_url": "https://wework.qpic.cn/wwpic/354393_4zpkKXd7SrGMvfg_1629280616/0"
    },
    "quote_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH",
        "title": "引用文本标题",
        "quote_text": "Jack：企业微信真的很好用~\nBalian：超级好的一款软件！"
    },
    "vertical_content_list": [
        {
            "title": "惊喜红包等你来拿",
            "desc": "下载企业微信还能抢红包！"
        }
    ],
    "horizontal_content_list": [
        {
            "keyname": "邀请人",
            "value": "张三"
        },
        {
            "keyname": "企微官网",
            "value": "点击访问",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        },
        {
            "keyname": "企微下载",
            "value": "企业微信.apk",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        }
    ],
    "jump_list": [
        {
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi",
            "title": "企业微信官网"
        },
        {
            "type": 2,
            "appid": "APPID",
            "pagepath": "PAGEPATH",
            "title": "跳转小程序"
        },
        {
            "type": 3,
            "title": "企业微信官网",
            "question": "如何登录企业微信官网"
        }
    ],
    "card_action": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH"
    },
    "task_id": "task_id"
}
    
        参数类型必须说明card_typeString是模版卡片的模版类型，图文展示模版卡片的类型为news_noticesourceObject否卡片来源样式信息，不需要来源样式可不填写。参考Source结构体说明action_menuObject否卡片右上角更多操作按钮。参考ActionMenu结构体说明main_titleObject是模版卡片的主要内容，包括一级标题和标题辅助信息。参考MainTitle结构体说明card_imageObject否图片样式，news_notice类型的卡片，card_image和image_text_area两者必填一个字段，不可都不填。参考CardImage结构体说明image_text_areaObject否左图右文样式。参考ImageTextArea结构体说明quote_areaObject否引用文献样式。参考QuoteArea结构体说明vertical_content_listObject[]否卡片二级垂直内容，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过4。参考VerticalContent结构体说明horizontal_content_listObject[]否二级标题+文本列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过6。参考HorizontalContent结构体说明jump_listObject[]否跳转指引样式的列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过3。参考JumpAction结构体说明card_actionObject是整体卡片的点击跳转事件，news_notice模版卡片中该字段为必填项。参考CardAction结构体说明task_idString否任务id，当图文展示模版卡片有action_menu字段的时候，该字段必填。同一个机器人任务id不能重复，只能由数字、字母和“_-@”组成，最长128字节。任务id只在发消息时候有效，更新消息的时候无效。任务id将会在相应的回调事件中返回 

```json
{
    "card_type": "news_notice",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0",
        "desc": "企业微信",
        "desc_color": 0
    },
    "action_menu": {
        "desc": "消息气泡副交互辅助文本说明",
        "action_list": [
            {
                "text": "接收推送",
                "key": "action_key1"
            },
            {
                "text": "不再推送",
                "key": "action_key2"
            }
        ]
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "card_image": {
        "url": "https://wework.qpic.cn/wwpic/354393_4zpkKXd7SrGMvfg_1629280616/0",
        "aspect_ratio": 2.25
    },
    "image_text_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com",
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信",
        "image_url": "https://wework.qpic.cn/wwpic/354393_4zpkKXd7SrGMvfg_1629280616/0"
    },
    "quote_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH",
        "title": "引用文本标题",
        "quote_text": "Jack：企业微信真的很好用~\nBalian：超级好的一款软件！"
    },
    "vertical_content_list": [
        {
            "title": "惊喜红包等你来拿",
            "desc": "下载企业微信还能抢红包！"
        }
    ],
    "horizontal_content_list": [
        {
            "keyname": "邀请人",
            "value": "张三"
        },
        {
            "keyname": "企微官网",
            "value": "点击访问",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        },
        {
            "keyname": "企微下载",
            "value": "企业微信.apk",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        }
    ],
    "jump_list": [
        {
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi",
            "title": "企业微信官网"
        },
        {
            "type": 2,
            "appid": "APPID",
            "pagepath": "PAGEPATH",
            "title": "跳转小程序"
        },
        {
            "type": 3,
            "title": "企业微信官网",
            "question": "如何登录企业微信官网"
        }
    ],
    "card_action": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi",
        "appid": "APPID",
        "pagepath": "PAGEPATH"
    },
    "task_id": "task_id"
}
```


### 按钮交互模版卡片

按钮交互模版卡片消息示例完整按钮交互模版卡片示例

{
    "card_type": "button_interaction",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0 ",
        "desc": "企业微信",
        "desc_color": 0
    },
    "action_menu": {
        "desc": "消息气泡副交互辅助文本说明",
        "action_list": [
            {
                "text": "接收推送",
                "key": "action_key1"
            },
            {
                "text": "不再推送",
                "key": "action_key2"
            }
        ]
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "quote_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi ",
        "appid": "APPID",
        "pagepath": "PAGEPATH",
        "title": "引用文本标题",
        "quote_text": "Jack：企业微信真的很好用~\nBalian：超级好的一款软件！"
    },
    "sub_title_text": "下载企业微信还能抢红包！",
    "horizontal_content_list": [
        {
            "keyname": "邀请人",
            "value": "张三"
        },
        {
            "keyname": "企微官网",
            "value": "点击访问",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        },
        {
            "keyname": "企微下载",
            "value": "企业微信.apk",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        }
    ],
    "button_selection": {
        "question_key": "button_selection_key1",
        "title": "你的身份",
        "disable": false,
        "option_list": [
            {
                "id": "button_selection_id1",
                "text": "企业负责人"
            },
            {
                "id": "button_selection_id2",
                "text": "企业用户"
            }
        ],
        "selected_id": "button_selection_id1"
    },
    "button_list": [
        {
            "text": "按钮1",
            "style": 4,
            "key": "BUTTONKEYONE"
        },
        {
            "text": "按钮2",
            "style": 1,
            "key": "BUTTONKEYTWO"
        }
    ],
    "card_action": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi ",
        "appid": "APPID",
        "pagepath": "PAGEPATH"
    },
    "task_id": "task_id"
}

    
        参数类型必须说明card_typeString是模版卡片的模版类型，按钮交互模版卡片的类型为button_interaction。当机器人设置了回调URL时，才能下发按钮交互模版卡片sourceObject否卡片来源样式信息，不需要来源样式可不填写。参考Source结构体说明action_menuObject否卡片右上角更多操作按钮。参考ActionMenu结构体说明main_titleObject是模版卡片的主要内容，包括一级标题和标题辅助信息。参考MainTitle结构体说明quote_areaObject否引用文献样式，建议不与关键数据共用。参考QuoteArea结构体说明sub_title_textString否二级普通文本，建议不超过112个字horizontal_content_listObject[]否二级标题+文本列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过6。参考HorizontalContent结构体说明button_selectionObject否下拉式的选择器。参考SelectionItem结构体说明button_listObject[]是按钮列表，列表长度不超过6。参考Button结构体说明结构体说明card_actionObject否整体卡片的点击跳转事件。参考CardAction结构体说明task_idString是任务id，同一个机器人任务id不能重复，只能由数字、字母和“_-@”组成，最长128字节。任务id只在发消息时候有效，更新消息的时候无效。任务id将会在相应的回调事件中返回 

```json
{
    "card_type": "button_interaction",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0 ",
        "desc": "企业微信",
        "desc_color": 0
    },
    "action_menu": {
        "desc": "消息气泡副交互辅助文本说明",
        "action_list": [
            {
                "text": "接收推送",
                "key": "action_key1"
            },
            {
                "text": "不再推送",
                "key": "action_key2"
            }
        ]
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "quote_area": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi ",
        "appid": "APPID",
        "pagepath": "PAGEPATH",
        "title": "引用文本标题",
        "quote_text": "Jack：企业微信真的很好用~\nBalian：超级好的一款软件！"
    },
    "sub_title_text": "下载企业微信还能抢红包！",
    "horizontal_content_list": [
        {
            "keyname": "邀请人",
            "value": "张三"
        },
        {
            "keyname": "企微官网",
            "value": "点击访问",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        },
        {
            "keyname": "企微下载",
            "value": "企业微信.apk",
            "type": 1,
            "url": "https://work.weixin.qq.com/?from=openApi"
        }
    ],
    "button_selection": {
        "question_key": "button_selection_key1",
        "title": "你的身份",
        "disable": false,
        "option_list": [
            {
                "id": "button_selection_id1",
                "text": "企业负责人"
            },
            {
                "id": "button_selection_id2",
                "text": "企业用户"
            }
        ],
        "selected_id": "button_selection_id1"
    },
    "button_list": [
        {
            "text": "按钮1",
            "style": 4,
            "key": "BUTTONKEYONE"
        },
        {
            "text": "按钮2",
            "style": 1,
            "key": "BUTTONKEYTWO"
        }
    ],
    "card_action": {
        "type": 1,
        "url": "https://work.weixin.qq.com/?from=openApi ",
        "appid": "APPID",
        "pagepath": "PAGEPATH"
    },
    "task_id": "task_id"
}
```


### 投票选择模版卡片

投票选择模版卡片消息示例

完整投票选择模版卡片示例

{
    "card_type": "vote_interaction",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0 ",
        "desc": "企业微信"
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "checkbox": {
        "question_key": "question_key",
        "option_list": [
            {
                "id": "id_one",
                "text": "选择题选项1"
            },
            {
                "id": "id_two",
                "text": "选择题选项2",
                "is_checked": true
            }
        ],
        "disable": false,
        "mode": 1
    },
    "submit_button": {
        "text": "提交",
        "key": "submit_key"
    },
    "task_id": "task_id"
}
    
        参数类型必须说明card_typeString是模版卡片的模版类型，投票选择模版卡片的类型为vote_interaction。当机器人设置了回调URL时，才能下发投票选择模版卡片sourceObject否卡片来源样式信息，不需要来源样式可不填写。参考Source结构体说明main_titleObject是模版卡片的主要内容，包括一级标题和标题辅助信息。参考MainTitle结构体说明checkboxObject是选择题样式。参考CheckBox结构体说明submit_buttonObject是提交按钮样式。参考SubmitButtion结构体说明task_idString是任务id，同一个机器人任务id不能重复，只能由数字、字母和“_-@”组成，最长128字节。任务id只在发消息时候有效，更新消息的时候无效。任务id将会在相应的回调事件中返回 

```json
{
    "card_type": "vote_interaction",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0 ",
        "desc": "企业微信"
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "checkbox": {
        "question_key": "question_key",
        "option_list": [
            {
                "id": "id_one",
                "text": "选择题选项1"
            },
            {
                "id": "id_two",
                "text": "选择题选项2",
                "is_checked": true
            }
        ],
        "disable": false,
        "mode": 1
    },
    "submit_button": {
        "text": "提交",
        "key": "submit_key"
    },
    "task_id": "task_id"
}
```


### 多项选择模版卡片

投票选择模版卡片消息示例完整多项选择模版卡片示例

{
    "card_type": "multiple_interaction",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0 ",
        "desc": "企业微信"
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "select_list": [
        {
            "question_key": "question_key_one",
            "title": "选择标签1",
            "disable": false,
            "selected_id": "id_one",
            "option_list": [
                {
                    "id": "id_one",
                    "text": "选择器选项1"
                },
                {
                    "id": "id_two",
                    "text": "选择器选项2"
                }
            ]
        },
        {
            "question_key": "question_key_two",
            "title": "选择标签2",
            "selected_id": "id_three",
            "option_list": [
                {
                    "id": "id_three",
                    "text": "选择器选项3"
                },
                {
                    "id": "id_four",
                    "text": "选择器选项4"
                }
            ]
        }
    ],
    "submit_button": {
        "text": "提交",
        "key": "submit_key"
    },
    "task_id": "task_id"
}
    
        参数类型必须说明card_typeString是模版卡片的模版类型，多项选择模版卡片的类型为multiple_interaction。当机器人设置了回调URL时，才能下发多项选择模版卡片sourceObject否卡片来源样式信息，不需要来源样式可不填写。参考Source结构体说明main_titleObject是模版卡片的主要内容，包括一级标题和标题辅助信息。参考MainTitle结构体说明select_listObject[]是下拉式的选择器列表，multiple_interaction类型的卡片该字段不可为空，一个消息最多支持 3 个选择器。参考SelectionItem结构体说明submit_buttonObject是提交按钮样式。参考SubmitButton结构体说明task_idString否任务id，同一个机器人任务id不能重复，只能由数字、字母和“_-@”组成，最长128字节。任务id只在发消息时候有效，更新消息的时候无效。任务id将会在相应的回调事件中返回 

```json
{
    "card_type": "multiple_interaction",
    "source": {
        "icon_url": "https://wework.qpic.cn/wwpic/252813_jOfDHtcISzuodLa_1629280209/0 ",
        "desc": "企业微信"
    },
    "main_title": {
        "title": "欢迎使用企业微信",
        "desc": "您的好友正在邀请您加入企业微信"
    },
    "select_list": [
        {
            "question_key": "question_key_one",
            "title": "选择标签1",
            "disable": false,
            "selected_id": "id_one",
            "option_list": [
                {
                    "id": "id_one",
                    "text": "选择器选项1"
                },
                {
                    "id": "id_two",
                    "text": "选择器选项2"
                }
            ]
        },
        {
            "question_key": "question_key_two",
            "title": "选择标签2",
            "selected_id": "id_three",
            "option_list": [
                {
                    "id": "id_three",
                    "text": "选择器选项3"
                },
                {
                    "id": "id_four",
                    "text": "选择器选项4"
                }
            ]
        }
    ],
    "submit_button": {
        "text": "提交",
        "key": "submit_key"
    },
    "task_id": "task_id"
}
```


## 结构体说明


#### Source结构体

卡片来源样式信息


#### ActionMenu结构体

卡片右上角更多操作按钮


#### MainTitle结构体

模版卡片的主要内容，包括一级标题和标题辅助信息


#### EmphasisContent结构体

关键数据样式


#### QuoteArea结构体

引用文献样式


#### HorizontalContent结构体

二级标题+文本列表


#### JumpAction结构体

跳转指引样式的列表


#### CardAction结构体

整体卡片的点击跳转事件


#### VerticalContent结构体

卡片二级垂直内容


#### CardImage结构体

图片样式


#### ImageTextArea结构体

左图右文样式


#### SubmitButton结构体

提交按钮样式


#### SelectionItem结构体

下拉式的选择器列表


#### Button结构体

按钮列表


#### Checkbox结构体

选择题样式


---

# 回调和回复的加解密方案

> 最后更新：2025/07/23

目录

- 验证URL有效性
- 接收回调解密
- 加密与被动回复

### 验证URL有效性

当点击“保存”提交开发配置信息时，企业微信会发送一条验证消息到填写的URL，发送方法为GET。智能机器人的接收消息服务器接收到验证请求后，需要作出正确的响应才能通过URL验证。

假设接收消息地址设置为：https://api.3dept.com/，企业微信将向该地址发送如下验证请求：

请求方式：GET

请求地址：https://api.3dept.com/?msg_signature=ASDFQWEXZCVAQFASDFASDFSS&timestamp=13500001234&nonce=123412323&echostr=ENCRYPT_STR参数说明


| 参数 | 必须 | 说明 |
| --- | --- | --- |
| msg_signature | 是 | 企业微信加密签名，msg_signature结合了开发者填写的token、请求中的timestamp、nonce参数、加密的消息体 |
| timestamp | 是 | 时间戳 |
| nonce | 是 | 随机数，两个小时内保证不重复 |
| echostr | 是 | 加密的字符串。需要解密得到消息内容明文，解密后有random、msg_len、msg三个字段，其中msg即为消息内容明文 |

智能机器人后台收到请求后，需要做如下操作：

- 对收到的请求做Urldecode处理
- 通过参数msg_signature对请求进行校验，确认调用者的合法性。
- 解密echostr参数得到消息内容(即msg字段)
- 在1秒内响应GET请求，响应内容为上一步得到的明文消息内容(不能加引号，不能带bom头，不能带换行符)
以上2~3步骤可以直接使用验证URL函数一步到位。之后接入验证生效，接收消息开启成功。


### 接收回调解密

智能机器人的回调格式为json，参考接收数据格式说明。开发者可以直接使用企业微信为应用提供的加解密库（目前已有c++/python/php/java/c#等语言版本）解密encrypt字段，获取事件明文json报文。需要注意的是，加解密库要求传 receiveid 参数，企业自建智能机器人的使用场景里，receiveid直接传空字符串即可；-->。

加密数据格式 ：

```json
{
	"encrypt": "msg_encrypt"
}
```


| 参数 | 是否必填 | 说明 |
| --- | --- | --- |
| encrypt | 是 | 消息结构体加密后的字符串 |


### 加密与被动回复

开发者解密数据得到用户消息内容后，可以选择直接回复空包，也可以在响应本次请求的时候直接回复消息。回复的消息需要先按明文协议构造json数据包，然后对明文消息进行加密，然后填充到下述协议中的encrypt字段中，之后再回复最终的密文json数据包。加密过程参见“明文msg的加密过程”。

加密数据格式:

```json
{
	"encrypt": "msg_encrypt",
	"msgsignature": "msg_signaturet",
	"timestamp": 1641002400,
	"nonce": "nonce"
}
```


| 参数 | 是否必须 | 说明 |
| --- | --- | --- |
| encrypt | 是 | 加密后的消息内容 |
| msgsignature | 是 | 消息签名 |
| timestamp | 是 | 时间戳，要求为秒级别的 |
| nonce | 是 | 随机数，需要用回调url中的nonce |


---

# 主动回复消息

> 最后更新：2025/12/12

目录

- 概述
- 如何主动回复消息
- 消息类型及数据格式
-       markdown消息
-       模板卡片消息

### 概述

当用户与智能机器人进行交互时，企业微信会将相关的交互事件回调到开发者设置的回调URL，回调中会返回一个 response_url 。开发者可根据事件类型先做出相应的响应，待处理完业务逻辑后，使用response_url主动调用接口回复消息，实现丰富的自定义功能。

目前有以下场景回调会返回 response_url ，支持主动回复消息：1. 用户向智能机器人发送消息，前往查看2. 用户点击模板卡片相关按钮等，前往查看

交互流程如下图所示：


### 如何主动回复消息

开发者获取到response_url后，可以按以下说明向这个地址发起HTTP POST 请求，即可对相应的回调进行主动回复。下面举个简单的例子.

- 假设 response_url 是：https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=RESPONSE_CODE。以下是用curl工具往群组推送文本消息的示例（注意要将url替换成对应的response_url，content必须是utf8编码）：
curl 'https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=RESPONSE_CODE' \
   -H 'Content-Type: application/json' \
   -d '
{
    "msgtype": "markdown",
    "markdown": {
        "content": "hello world"
    }
}'
    消息类型及数据格式markdown消息
```json
curl 'https://qyapi.weixin.qq.com/cgi-bin/aibot/response?response_code=RESPONSE_CODE' \
   -H 'Content-Type: application/json' \
   -d '
{
    "msgtype": "markdown",
    "markdown": {
        "content": "hello world"
    }
}'
```


### 消息类型及数据格式


#### markdown消息

```json
{
    "msgtype": "markdown",
    "markdown": {
        "content": "# 一、标题\n## 二级标题\n### 三级标题\n# 二、字体\n*斜体*\n\n**加粗**\n# 三、列表 \n- 无序列表 1 \n- 无序列表 2\n  - 无序列表 2.1\n  - 无序列表 2.2\n1. 有序列表 1\n2. 有序列表 2\n# 四、引用\n> 一级引用\n>>二级引用\n>>>三级引用\n# 五、链接\n[这是一个链接](https:work.weixin.qq.com\/api\/doc)\n![](https://res.mail.qq.com/node/ww/wwopenmng/images/independent/doc/test_pic_msg1.png)\n# 六、分割线\n\n---\n# 七、代码\n`这是行内代码`\n```\n这是独立代码块\n```\n\n# 八、表格\n| 姓名 | 文化衫尺寸 | 收货地址 |\n| :----- | :----: | -------: |\n| 张三 | S | 广州 |\n| 李四 | L | 深圳 |\n",
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


| 参数 | 类型 | 是否必填 | 说明 |
| --- | --- | --- | --- |
| msgtype | String | 是 | 消息类型，此时固定为：markdown |
| markdown.content | String | 是 | 消息内容，最长不超过20480个字节，必须是utf8编码。 |
| markdown.feedback.id | String | 否 | 若字段不为空值，回复的消息被用户反馈时候会触发回调事件。有效长度为 256 字节以内，必须是 utf-8 编码。 |


#### 模板卡片消息

```json
{
    "msgtype": "template_card",
    "template_card": {
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


---

# 智能机器人长连接

> 最后更新：2026/03/13

目录

- 概述
-       通过部署SDK建立长连接
-       长连接与短连接（Webhook）方式对比
-       适用场景
-       整体交互流程
- 长连接配置说明
-       开启长连接 API 模式
-       获取凭证
- 建立长连接
-       WebSocket 连接地址
-       连接数量限制
-       连接建立流程
-       订阅请求
- 接收消息回调
-       消息推送格式
-       支持的消息类型
-       多媒体资源解密
- 接收事件回调
-       事件推送格式
-       支持的事件类型
-             连接断开事件格式示例
- 回复消息
-       回复欢迎语
-       回复普通消息
-             流式消息回复机制
-       更新模板卡片
- 主动推送消息
-       请求格式
-       响应格式
-       消息类型格式说明
-             markdown消息
-             模板卡片消息
-             文件消息
-             图片消息
-             语音消息
-             视频消息
- 保持心跳
-       心跳机制说明
-       心跳请求
- 上传临时素材
-       注意事项
-       流程图
-       上传初始化
-       上传分片
-       上传结束

## 概述


### 通过部署SDK建立长连接


| 语言 | 下载地址 |
| --- | --- |
| Node.js | aibot-node-sdk |
| Python | aibot-python-sdk |


### 长连接与短连接（Webhook）方式对比

智能机器人支持两种 API 模式接收消息回调：


| 特性 | Webhook（短连接） | WebSocket（长连接） |
| --- | --- | --- |
| 连接方式 | 每次回调建立新连接 | 复用已建立的长连接 |
| 延迟 | 较高（每次需建连） | 低（复用连接） |
| 实时性 | 一般 | 好 |
| 服务端要求 | 需要公网可访问的 URL | 无需固定的公网 IP |
| 加解密 | 需要对消息加解密 | 无需加解密 |
| 复杂度 | 低 | 较高（需维护心跳） |
| 可靠性 | 高（无状态） | 需要心跳保活、断线重连 |
| 适用场景 | 普通回调场景 | 高实时性要求、无固定公网 IP 场景 |


### 适用场景

推荐使用 WebSocket 长连接方式的场景：

- 无公网 IP：开发者服务部署在内网环境，无法配置公网可访问的回调 URL
- 高实时性要求：需要更低的消息延迟
- 简化开发：无需处理消息加解密逻辑

### 整体交互流程

长连接模式的交互流程如下：

- 开发者服务企业微信用户连接建立阶段建立WebSocket连接 (aibot_subscribe)1连接建立成功2进入会话事件进入机器人会话3事件回调 (aibot_event_callback)4回复欢迎语 (aibot_respond_welcome_msg)5展示欢迎消息6消息回调与流式消息@机器人发消息7消息回调 (aibot_msg_callback)8回复流式消息 (aibot_respond_msg, finish=false)9展示流式消息10更新流式内容 (aibot_respond_msg, finish=false)11更新流式消息12loop[开发者主动推送更新]完成流式消息 (aibot_respond_msg, finish=true)13展示最终消息14模板卡片交互点击模板卡片按钮15事件回调 (aibot_event_callback)16更新卡片 (aibot_respond_update_msg)17展示更新后的卡片18主动推送消息（无回调触发）主动推送消息 (aibot_send_msg)19展示推送消息20心跳保活ping21pong22loop[定时心跳]开发者服务企业微信用户流程说明：连接建立阶段：开发者服务使用 BotID 和 Secret 向企业微信发起 WebSocket 连接请求（aibot_subscribe），连接建立成功后保持长连接状态
连接建立阶段建立WebSocket连接 (aibot_subscribe)1连接建立成功2进入会话事件进入机器人会话3事件回调 (aibot_event_callback)4回复欢迎语 (aibot_respond_welcome_msg)5展示欢迎消息6消息回调与流式消息@机器人发消息7消息回调 (aibot_msg_callback)8回复流式消息 (aibot_respond_msg, finish=false)9展示流式消息10更新流式内容 (aibot_respond_msg, finish=false)11更新流式消息12loop[开发者主动推送更新]完成流式消息 (aibot_respond_msg, finish=true)13展示最终消息14模板卡片交互点击模板卡片按钮15事件回调 (aibot_event_callback)16更新卡片 (aibot_respond_update_msg)17展示更新后的卡片18主动推送消息（无回调触发）主动推送消息 (aibot_send_msg)19展示推送消息20心跳保活ping21pong22loop[定时心跳]开发者服务企业微信用户流程说明：

- 进入会话事件：用户首次进入机器人单聊会话时，企业微信推送事件回调（aibot_event_callback），开发者可回复欢迎语（aibot_respond_welcome_msg）
- 消息回调与流式消息：用户在群聊中@机器人或向机器人发送单聊消息时，企业微信推送消息回调（aibot_msg_callback）。与「设置接收消息 URL」模式不同，长连接模式下不再有流式刷新回调，开发者需主动推送流式更新内容，直到设置 finish=true 结束流式消息
- 模板卡片交互：用户点击模板卡片按钮时，企业微信推送事件回调（aibot_event_callback），开发者可更新卡片内容（aibot_respond_update_msg）
- 主动推送消息：开发者可在没有用户消息触发的情况下，通过 aibot_send_msg 主动向用户或群聊推送消息，适用于定时提醒、异步任务通知、告警推送等场景
- 心跳保活：开发者需定期发送心跳（ping）保持连接活跃，建议间隔 30 秒

## 长连接配置说明


### 开启长连接 API 模式

在企业微信管理后台，进入智能机器人的配置页面，开启「API 模式」并选择「长连接」方式：


### 获取凭证

开启长连接 API 模式后，需要获取以下凭证用于建立连接：


| 凭证 | 说明 |
| --- | --- |
| BotID | 智能机器人的唯一标识，用于标识机器人身份 |
| Secret | 长连接专用密钥，用于身份校验 |


## 建立长连接


### WebSocket 连接地址

wss://openws.work.weixin.qq.com
    连接数量限制每个智能机器人同一时间只能保持一个有效的长连接。当同一个机器人发起新的连接请求并完成订阅（aibot_subscribe）时，新连接会踢掉旧连接，旧连接将被服务端主动断开。

```json
wss://openws.work.weixin.qq.com
```


### 连接数量限制


### 连接建立流程

建立长连接的完整流程：

- 开发者服务企业微信1. 发起 WebSocket 连接wss://openws.work.weixin.qq.com12. WebSocket 握手成功23. 发送订阅请求 (aibot_subscribe)携带 BotID 和 Secret34. 校验凭证5. 返回订阅结果4连接建立完成，开始接收回调开发者服务企业微信订阅请求WebSocket 连接建立后，需要发送订阅请求（aibot_subscribe）进行身份校验。注意：该请求有频率保护，订阅成功后应避免反复请求，否则可能触发系统限制。请求示例：
      {
    "cmd": "aibot_subscribe",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "bot_id": "BOTID",
        "secret": "SECRET"
    }
}
    请求字段说明：
        字段类型必填说明cmdstring是命令类型，固定值 aibot_subscribeheaders.req_idstring是请求唯一标识，由开发者自行生成，用于关联请求和响应body.bot_idstring是智能机器人的 BotID，获取方法参考配置说明body.secretstring是长连接专用密钥 Secret，获取方法参考配置说明响应示例：
      {
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    响应字段说明：
        字段类型说明headers.req_idstring透传请求中的 req_iderrcodeint错误码，0 表示成功errmsgstring错误信息，成功时为 "ok"接收消息回调用户向智能机器人发送消息时，企业微信会通过长连接推送消息回调（aibot_msg_callback）。消息推送格式请求示例（文本消息）：
      {
    "cmd": "aibot_msg_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "aibotid": "AIBOTID",
        "chatid": "CHATID",
        "chattype": "group",
        "from": {
            "userid": "USERID"
        },
        "msgtype": "text",
        "text": {
            "content": "@RobotA hello robot"
        }
    }
}
    请求字段说明：
        字段类型说明cmdstring命令类型，固定值 aibot_msg_callbackheaders.req_idstring请求唯一标识，回复消息时需透传body.msgidstring本次回调的唯一性标志，用于事件排重body.aibotidstring智能机器人 BotIDbody.chatidstring会话 ID，仅群聊类型时返回body.chattypestring会话类型，single 单聊 / group 群聊body.from.useridstring消息发送者的 useridbody.msgtypestring消息类型支持的消息类型长连接模式支持以下消息类型的回调：
        消息类型msgtype说明文本消息text用户发送的文本内容图片消息image用户发送的图片，仅支持单聊图文混排mixed用户发送的图文混排内容语音消息voice用户发送的语音（转为文本），仅支持单聊文件消息file用户发送的文件，仅支持单聊视频消息video用户发送的视频，仅支持单聊说明：各消息类型的 body 结构与设置接收消息回调地址模式一致，详细字段请参考对应链接。多媒体资源解密长连接模式下，image、file 和 video 结构体中会额外返回解密密钥 aeskey，用于解密下载的资源文件：图片结构体示例：
      {
    "image": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
    文件结构体示例：
      {
    "file": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
    视频结构体示例：
      {
    "video": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
    
        字段类型说明urlstring资源下载地址，5 分钟内有效aeskeystring解密密钥，每个下载链接的 aeskey 唯一注意：- 每个 URL 对应的 aeskey 都是唯一的，不同于设置接收消息回调地址模式使用统一的 EncodingAESKey- 加密方式：AES-256-CBC，数据采用 PKCS#7 填充至 32 字节的倍数- IV 初始向量大小为 16 字节，取 aeskey 前 16 字节接收事件回调用户与智能机器人发生交互时，企业微信会通过长连接推送事件回调（aibot_event_callback）。事件推送格式请求示例（进入会话事件）：
      {
    "cmd": "aibot_event_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "create_time": 1700000000,
        "aibotid": "AIBOTID",
        "from": {
            "userid": "USERID"
        },
        "msgtype": "event",
        "event": {
            "eventtype": "enter_chat"
        }
    }
}
    请求字段说明：
        字段类型说明cmdstring命令类型，固定值 aibot_event_callbackheaders.req_idstring请求唯一标识，回复消息时需透传body.msgidstring本次回调的唯一性标志，用于事件排重body.create_timeint事件产生的时间戳body.aibotidstring智能机器人 BotIDbody.chatidstring会话 ID，仅群聊类型时返回body.chattypestring会话类型，single 单聊 / group 群聊body.from.useridstring事件触发者的 useridbody.msgtypestring消息类型，事件回调固定为 eventbody.event.eventtypestring事件类型支持的事件类型长连接模式支持以下事件类型的回调：
        事件类型eventtype说明进入会话事件enter_chat用户当天首次进入机器人单聊会话模板卡片事件template_card_event用户点击模板卡片按钮用户反馈事件feedback_event用户对机器人回复进行反馈连接断开事件disconnected_event当有新连接建立时，系统会给旧连接发送该事件并且主动断开旧连接连接断开事件格式示例
      {
    "cmd": "aibot_event_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "create_time": 1700000000,
        "aibotid": "AIBOTID",
        "msgtype": "event",
        "event": {
            "eventtype": "disconnected_event"
        }
    }
}
    连接断开事件字段说明：
        字段类型说明cmdstring命令类型，固定值 aibot_event_callbackheaders.req_idstring请求唯一标识，回复消息时需透传body.msgidstring本次回调的唯一性标志，用于事件排重body.create_timeint事件产生的时间戳body.aibotidstring智能机器人 BotIDbody.msgtypestring消息类型，事件回调固定为 eventbody.event.eventtypestring此时固定为disconnected_event说明：除连接断开事件外，其他各事件类型的 body 结构与设置接收消息回调地址模式一致，详细字段请参考对应链接。回复消息收到消息回调或事件回调后，开发者可通过长连接主动回复消息。回复欢迎语收到进入会话事件（enter_chat）后，开发者可使用 aibot_respond_welcome_msg 命令回复欢迎语。请求示例（文本欢迎语）：
      {
    "cmd": "aibot_respond_welcome_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgtype": "text",
        "text": {
            "content": "您好！我是智能助手，有什么可以帮您的吗？"
        }
    }
}
    请求字段说明：
        字段类型必填说明cmdstring是命令类型，固定值 aibot_respond_welcome_msgheaders.req_idstring是透传事件回调中的 req_idbodyobject是消息内容，详见回复欢迎语响应示例：
      {
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    注意：- 该命令仅适用于进入会话事件，其他事件类型不支持- 收到事件回调后需在 5 秒内 发送回复，超时将无法发送欢迎语回复普通消息收到消息回调（aibot_msg_callback）后，开发者可使用 aibot_respond_msg 命令回复消息。支持流式消息、模板卡片、流式消息和模板卡片组合消息、markdown、文件消息、语音消息、图片消息和视频消息。模板卡片、markdown、文件消息、语音消息、图片消息和视频消息的消息格式参考支持的消息类型频率限制：收到消息回调后，24 小时内可以往该会话回复消息。 无论是回复还是主动推送消息，总共给某个会话发消息的限制为 30 条/分钟，1000 条/小时。流式消息回复机制流式消息的发送和刷新通过 stream.id 进行关联：发送流式消息：首次使用某个 stream.id 回复时，会创建一条新的流式消息
1. 发起 WebSocket 连接wss://openws.work.weixin.qq.com12. WebSocket 握手成功23. 发送订阅请求 (aibot_subscribe)携带 BotID 和 Secret34. 校验凭证5. 返回订阅结果4连接建立完成，开始接收回调开发者服务企业微信订阅请求WebSocket 连接建立后，需要发送订阅请求（aibot_subscribe）进行身份校验。


### 订阅请求

请求示例：

{
    "cmd": "aibot_subscribe",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "bot_id": "BOTID",
        "secret": "SECRET"
    }
}
    请求字段说明：

```json
{
    "cmd": "aibot_subscribe",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "bot_id": "BOTID",
        "secret": "SECRET"
    }
}
```


| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| cmd | string | 是 | 命令类型，固定值 aibot_subscribe |
| headers.req_id | string | 是 | 请求唯一标识，由开发者自行生成，用于关联请求和响应 |
| body.bot_id | string | 是 | 智能机器人的 BotID，获取方法参考配置说明 |
| body.secret | string | 是 | 长连接专用密钥 Secret，获取方法参考配置说明 |

响应示例：

{
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    响应字段说明：

```json
{
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
```


| 字段 | 类型 | 说明 |
| --- | --- | --- |
| headers.req_id | string | 透传请求中的 req_id |
| errcode | int | 错误码，0 表示成功 |
| errmsg | string | 错误信息，成功时为 "ok" |


## 接收消息回调

用户向智能机器人发送消息时，企业微信会通过长连接推送消息回调（aibot_msg_callback）。


### 消息推送格式

请求示例（文本消息）：

{
    "cmd": "aibot_msg_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "aibotid": "AIBOTID",
        "chatid": "CHATID",
        "chattype": "group",
        "from": {
            "userid": "USERID"
        },
        "msgtype": "text",
        "text": {
            "content": "@RobotA hello robot"
        }
    }
}
    请求字段说明：

```json
{
    "cmd": "aibot_msg_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "aibotid": "AIBOTID",
        "chatid": "CHATID",
        "chattype": "group",
        "from": {
            "userid": "USERID"
        },
        "msgtype": "text",
        "text": {
            "content": "@RobotA hello robot"
        }
    }
}
```


### 支持的消息类型

长连接模式支持以下消息类型的回调：


| 消息类型 | msgtype | 说明 |
| --- | --- | --- |
| 文本消息 | text | 用户发送的文本内容 |
| 图片消息 | image | 用户发送的图片，仅支持单聊 |
| 图文混排 | mixed | 用户发送的图文混排内容 |
| 语音消息 | voice | 用户发送的语音（转为文本），仅支持单聊 |
| 文件消息 | file | 用户发送的文件，仅支持单聊 |
| 视频消息 | video | 用户发送的视频，仅支持单聊 |


### 多媒体资源解密

长连接模式下，image、file 和 video 结构体中会额外返回解密密钥 aeskey，用于解密下载的资源文件：

图片结构体示例：

{
    "image": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
    文件结构体示例：

```json
{
    "image": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
```

{
    "file": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
    视频结构体示例：

```json
{
    "file": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
```

{
    "video": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
    
        字段类型说明urlstring资源下载地址，5 分钟内有效aeskeystring解密密钥，每个下载链接的 aeskey 唯一注意：- 每个 URL 对应的 aeskey 都是唯一的，不同于设置接收消息回调地址模式使用统一的 EncodingAESKey- 加密方式：AES-256-CBC，数据采用 PKCS#7 填充至 32 字节的倍数- IV 初始向量大小为 16 字节，取 aeskey 前 16 字节接收事件回调用户与智能机器人发生交互时，企业微信会通过长连接推送事件回调（aibot_event_callback）。

```json
{
    "video": {
        "url": "URL",
        "aeskey": "AESKEY"
    }
}
```


## 接收事件回调


### 事件推送格式

请求示例（进入会话事件）：

{
    "cmd": "aibot_event_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "create_time": 1700000000,
        "aibotid": "AIBOTID",
        "from": {
            "userid": "USERID"
        },
        "msgtype": "event",
        "event": {
            "eventtype": "enter_chat"
        }
    }
}
    请求字段说明：

```json
{
    "cmd": "aibot_event_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "create_time": 1700000000,
        "aibotid": "AIBOTID",
        "from": {
            "userid": "USERID"
        },
        "msgtype": "event",
        "event": {
            "eventtype": "enter_chat"
        }
    }
}
```


### 支持的事件类型

长连接模式支持以下事件类型的回调：


| 事件类型 | eventtype | 说明 |
| --- | --- | --- |
| 进入会话事件 | enter_chat | 用户当天首次进入机器人单聊会话 |
| 模板卡片事件 | template_card_event | 用户点击模板卡片按钮 |
| 用户反馈事件 | feedback_event | 用户对机器人回复进行反馈 |
| 连接断开事件 | disconnected_event | 当有新连接建立时，系统会给旧连接发送该事件并且主动断开旧连接 |


#### 连接断开事件格式示例

{
    "cmd": "aibot_event_callback",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgid": "MSGID",
        "create_time": 1700000000,
        "aibotid": "AIBOTID",
        "msgtype": "event",
        "event": {
            "eventtype": "disconnected_event"
        }
    }
}
    连接断开事件字段说明：


## 回复消息

收到消息回调或事件回调后，开发者可通过长连接主动回复消息。


### 回复欢迎语

收到进入会话事件（enter_chat）后，开发者可使用 aibot_respond_welcome_msg 命令回复欢迎语。

请求示例（文本欢迎语）：

{
    "cmd": "aibot_respond_welcome_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgtype": "text",
        "text": {
            "content": "您好！我是智能助手，有什么可以帮您的吗？"
        }
    }
}
    请求字段说明：

```json
{
    "cmd": "aibot_respond_welcome_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgtype": "text",
        "text": {
            "content": "您好！我是智能助手，有什么可以帮您的吗？"
        }
    }
}
```

响应示例：

{
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    注意：- 该命令仅适用于进入会话事件，其他事件类型不支持- 收到事件回调后需在 5 秒内 发送回复，超时将无法发送欢迎语回复普通消息收到消息回调（aibot_msg_callback）后，开发者可使用 aibot_respond_msg 命令回复消息。支持流式消息、模板卡片、流式消息和模板卡片组合消息、markdown、文件消息、语音消息、图片消息和视频消息。


### 回复普通消息

频率限制：收到消息回调后，24 小时内可以往该会话回复消息。 无论是回复还是主动推送消息，总共给某个会话发消息的限制为 30 条/分钟，1000 条/小时。


#### 流式消息回复机制

流式消息的发送和刷新通过 stream.id 进行关联：

- 刷新流式消息：继续使用相同的 stream.id 推送时，会更新该流式消息的内容
- 完成流式消息：设置 finish=true 结束流式消息，消息将不再可更新
两种模式的流式刷新方式对比：


| 模式 | 刷新方式 | 说明 |
| --- | --- | --- |
| 设置接收消息地址 | 回调轮询 | 企业微信通过轮询回调开发者的接收消息地址来获取流式消息的刷新内容 |
| 长连接 | 主动推送 | 开发者服务主动通过长连接推送流式刷新消息，无需等待回调 |

流式消息交互流程：

- 开发者服务企业微信用户@机器人发消息1aibot_msg_callback (req_id=xxx)2生成唯一的 stream.idaibot_respond_msg (req_id=xxx)(stream.id=abc, finish=false)content="正在查询..."3首次使用该 stream.id创建新的流式消息展示流式消息4aibot_respond_msg (req_id=xxx)(stream.id=abc, finish=false)content="广州今日天气：29度..."5继续使用相同 stream.id刷新消息内容更新流式消息6aibot_respond_msg (req_id=xxx)(stream.id=abc, finish=false)content="广州今日天气：29度，多云，降雨概率60%..."7更新流式消息8aibot_respond_msg (req_id=xxx)(stream.id=abc, finish=true)content="广州今日天气：29度，多云，降雨概率60%，建议携带雨具。"9finish=true流式消息结束展示最终消息106分钟超时限制：从首次发送开始计时开发者服务企业微信用户说明：在长连接模式下，针对同一次消息回调的所有流式消息回复（包括首次发送和后续刷新），都需要使用回调中相同的 req_id。req_id 用于关联回调请求与响应，stream.id 用于标识同一条流式消息。请求示例（流式消息回复）：
      {
    "cmd": "aibot_respond_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgtype": "stream",
        "stream": {
            "id": "STREAMID",
            "finish": false,
            "content": "正在为您查询天气信息..."
        }
    }
}
    请求字段说明：
        字段类型必填说明cmdstring是命令类型，固定值 aibot_respond_msgheaders.req_idstring是透传消息回调中的 req_idbodyobject是消息内容，详见回复用户消息中的流式消息和模块卡片消息不支持流式消息+模板卡片的组合消息注意：目前暂不支持 msg_item 字段。响应示例：
      {
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
     更新模板卡片收到模板卡片点击事件（template_card_event）后，开发者可使用 aibot_respond_update_msg 命令更新卡片内容。请求示例：
      {
    "cmd": "aibot_respond_update_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "response_type": "update_template_card",
        "template_card": {
            "card_type": "button_interaction",
            "main_title": {
                "title": "xx系统告警",
                "desc": "服务器CPU使用率超过90%"
            },
            "button_list": [
                {
                    "text": "确认中",
                    "style": 1,
                    "key": "confirm"
                },
                {
                    "text": "误报",
                    "style": 2,
                    "key": "false_alarm"
                }
            ],
            "task_id": "TASK_ID",
            "feedback": {
                "id": "FEEDBACKID"
            }
        }
    }
}
    请求字段说明：
        字段类型必填说明cmdstring是命令类型，固定值 aibot_respond_update_msgheaders.req_idstring是透传事件回调中的 req_idbodyobject是消息内容，详见更新模板卡片响应示例：
      {
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    注意：- 该命令仅适用于模板卡片点击事件，其他事件类型不支持- 收到事件回调后需在 5 秒内 发送回复，超时将无法更新卡片主动推送消息在某些场景下，开发者需要在没有用户消息触发的情况下，主动向用户或群聊推送消息。例如：定时提醒：定时推送日报、周报、待办提醒等
@机器人发消息1aibot_msg_callback (req_id=xxx)2生成唯一的 stream.idaibot_respond_msg (req_id=xxx)(stream.id=abc, finish=false)content="正在查询..."3首次使用该 stream.id创建新的流式消息展示流式消息4aibot_respond_msg (req_id=xxx)(stream.id=abc, finish=false)content="广州今日天气：29度..."5继续使用相同 stream.id刷新消息内容更新流式消息6aibot_respond_msg (req_id=xxx)(stream.id=abc, finish=false)content="广州今日天气：29度，多云，降雨概率60%..."7更新流式消息8aibot_respond_msg (req_id=xxx)(stream.id=abc, finish=true)content="广州今日天气：29度，多云，降雨概率60%，建议携带雨具。"9finish=true流式消息结束展示最终消息106分钟超时限制：从首次发送开始计时开发者服务企业微信用户说明：在长连接模式下，针对同一次消息回调的所有流式消息回复（包括首次发送和后续刷新），都需要使用回调中相同的 req_id。req_id 用于关联回调请求与响应，stream.id 用于标识同一条流式消息。请求示例（流式消息回复）：

{
    "cmd": "aibot_respond_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgtype": "stream",
        "stream": {
            "id": "STREAMID",
            "finish": false,
            "content": "正在为您查询天气信息..."
        }
    }
}
    请求字段说明：

```json
{
    "cmd": "aibot_respond_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "msgtype": "stream",
        "stream": {
            "id": "STREAMID",
            "finish": false,
            "content": "正在为您查询天气信息..."
        }
    }
}
```

响应示例：

{
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
     


### 更新模板卡片

收到模板卡片点击事件（template_card_event）后，开发者可使用 aibot_respond_update_msg 命令更新卡片内容。

请求示例：

{
    "cmd": "aibot_respond_update_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "response_type": "update_template_card",
        "template_card": {
            "card_type": "button_interaction",
            "main_title": {
                "title": "xx系统告警",
                "desc": "服务器CPU使用率超过90%"
            },
            "button_list": [
                {
                    "text": "确认中",
                    "style": 1,
                    "key": "confirm"
                },
                {
                    "text": "误报",
                    "style": 2,
                    "key": "false_alarm"
                }
            ],
            "task_id": "TASK_ID",
            "feedback": {
                "id": "FEEDBACKID"
            }
        }
    }
}
    请求字段说明：

```json
{
    "cmd": "aibot_respond_update_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "response_type": "update_template_card",
        "template_card": {
            "card_type": "button_interaction",
            "main_title": {
                "title": "xx系统告警",
                "desc": "服务器CPU使用率超过90%"
            },
            "button_list": [
                {
                    "text": "确认中",
                    "style": 1,
                    "key": "confirm"
                },
                {
                    "text": "误报",
                    "style": 2,
                    "key": "false_alarm"
                }
            ],
            "task_id": "TASK_ID",
            "feedback": {
                "id": "FEEDBACKID"
            }
        }
    }
}
```

响应示例：

{
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    注意：- 该命令仅适用于模板卡片点击事件，其他事件类型不支持- 收到事件回调后需在 5 秒内 发送回复，超时将无法更新卡片主动推送消息在某些场景下，开发者需要在没有用户消息触发的情况下，主动向用户或群聊推送消息。例如：


## 主动推送消息

- 异步任务通知：后台任务完成后主动通知用户结果
- 告警推送：系统监控告警主动推送给相关人员
长连接模式支持通过 aibot_send_msg 命令主动推送消息，无需依赖消息回调。特殊的，需要用户在会话中给机器人发消息，后续机器人才能主动推送消息给对应会话中。

频率限制：无论是回复还是主动推送消息，总共给某个会话发消息的限制为 30 条/分钟，1000 条/小时。


### 请求格式

请求示例（markdown 消息）：

{
    "cmd": "aibot_send_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "chatid": "CHATID",
        "chat_type": 1,
        "msgtype": "markdown",
        "markdown": {
            "content": "这是一条**主动推送**的消息"
        }
    }
}
    请求字段说明：

```json
{
    "cmd": "aibot_send_msg",
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "body": {
        "chatid": "CHATID",
        "chat_type": 1,
        "msgtype": "markdown",
        "markdown": {
            "content": "这是一条**主动推送**的消息"
        }
    }
}
```


### 响应格式

响应示例：

{
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    响应字段说明：


### 消息类型格式说明


#### markdown消息

```json
{
    "msgtype": "markdown",
    "markdown": {
        "content": "# 一、标题\n## 二级标题\n### 三级标题\n# 二、字体\n*斜体*\n\n**加粗**\n# 三、列表 \n- 无序列表 1 \n- 无序列表 2\n  - 无序列表 2.1\n  - 无序列表 2.2\n1. 有序列表 1\n2. 有序列表 2\n# 四、引用\n> 一级引用\n>>二级引用\n>>>三级引用\n# 五、链接\n[这是一个链接](https:work.weixin.qq.com\/api\/doc)\n![](https://res.mail.qq.com/node/ww/wwopenmng/images/independent/doc/test_pic_msg1.png)\n# 六、分割线\n\n---\n# 七、代码\n`这是行内代码`\n```\n这是独立代码块\n```\n\n# 八、表格\n| 姓名 | 文化衫尺寸 | 收货地址 |\n| :----- | :----: | -------: |\n| 张三 | S | 广州 |\n| 李四 | L | 深圳 |\n",
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


| 参数 | 类型 | 是否必填 | 说明 |
| --- | --- | --- | --- |
| msgtype | String | 是 | 消息类型，此时固定为：markdown |
| markdown.content | String | 是 | 消息内容，最长不超过20480个字节，必须是utf8编码。 |
| markdown.feedback.id | String | 否 | 若字段不为空值，回复的消息被用户反馈时候会触发回调事件。有效长度为 256 字节以内，必须是 utf-8 编码。 |


#### 模板卡片消息

```json
{
    "msgtype": "template_card",
    "template_card": {
        "feedback": {
            "id": "FEEDBACKID"
        }
    }
}
```


#### 文件消息

请求示例：

```json
{
   "msgtype": "file",
   "file" : {
        "media_id" : "1Yv-zXfHjSjU-7LH-GwtYqDGS-zz6w22KmWAT5COgP7o"
   }
}
```


| 参数 | 是否必须 | 说明 |
| --- | --- | --- |
| msgtype | 是 | 消息类型，此时固定为：file |
| media_id | 是 | 文件id，可以调用上传临时素材接口获取 |


#### 图片消息

请求示例：

{
    "msgtype":"image",
   "image" : {
        "media_id" : "MEDIA_ID"
   }
}
    请求参数：

```json
{
    "msgtype":"image",
   "image" : {
        "media_id" : "MEDIA_ID"
   }
}
```


#### 语音消息

请求示例：

```json
{
   "msgtype" : "voice",
   "voice" : {
        "media_id" : "MEDIA_ID"
   }
}
```


#### 视频消息

请求示例：

```json
{
  "msgtype": "video",
  "video": {
    "media_id": "MEDIA_ID",
    "title": "Title",
    "description": "Description"
  }
}
```


## 保持心跳

连接建立成功后，开发者需定期发送心跳请求（ping）保持连接活跃，防止连接被服务端主动断开。


### 心跳机制说明

- 心跳间隔：建议每 30 秒 发送一次心跳
- 超时断开：若长时间未收到心跳，服务端会主动断开连接
- 断线重连：开发者需实现断线检测和自动重连机制

### 心跳请求

请求示例：

{
    "cmd": "ping",
    "headers": {
        "req_id": "REQUEST_ID"
    }
}
    请求字段说明：

```json
{
    "cmd": "ping",
    "headers": {
        "req_id": "REQUEST_ID"
    }
}
```

响应示例：

{
    "headers": {
        "req_id": "REQUEST_ID"
    },
    "errcode": 0,
    "errmsg": "ok"
}
    响应字段说明：


## 上传临时素材

长连接模式支持通过分片方式上传临时素材。上传流程分为三步：

- 初始化上传：获取 upload_id
- 逐片上传：按分片发送文件数据
- 完成上传：服务端合并分片并返回 media_id上传会话有效期为 30 分钟，超时未完成的上传会话将自动清理。单个分片不能超过 512KB（Base64 编码前），最多支持 100 个分片。

### 注意事项

- 上传会话有效期：初始化后 30 分钟内需完成所有分片上传并调用完成接口，超时后会话自动失效
- 分片幂等：同一分片重复上传会被自动忽略，不会报错
- 分片顺序：分片可以乱序上传，服务端会按 chunk_index 顺序合并
- MD5 校验：建议在初始化时提供文件 MD5，服务端会在合并后校验完整性
- 安全校验：每个请求都会校验当前连接的机器人身份，不同机器人无法操作同一个上传会话
- 断线重传：如果连接断开后重连，只要上传会话未过期，可以继续上传未完成的分片
- 有效期：上传的临时素材有效期为3天
- 频率限制：单个智能机器人上传频率不能超过30次/分钟和1000次/小时

### 流程图


### 上传初始化

请求示例：

{
  "cmd": "aibot_upload_media_init",
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "type": "file",
    "filename": "test.pdf",
    "total_size": 2333,
    "total_chunks": 124,
    "md5": "jgieosgmiesmgienogieisjgoejsgeo"
  }
}
    请求字段说明：

```json
{
  "cmd": "aibot_upload_media_init",
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "type": "file",
    "filename": "test.pdf",
    "total_size": 2333,
    "total_chunks": 124,
    "md5": "jgieosgmiesmgienogieisjgoejsgeo"
  }
}
```

响应示例：

{
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "upload_id": "UPLOADID"
  },
  "errcode": 0,
  "errmsg": "ok"
}
    响应字段说明：

```json
{
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "upload_id": "UPLOADID"
  },
  "errcode": 0,
  "errmsg": "ok"
}
```


### 上传分片

逐片上传文件数据。分片可以乱序上传，重复上传同一分片会被自动忽略（幂等）。 请求示例：

{
  "cmd": "aibot_upload_media_chunk",
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "upload_id": "UPLOADID",
    "chunk_index": 1,
    "base64_data": "JGNEIOGJGE"
  }
}
    请求字段说明：

```json
{
  "cmd": "aibot_upload_media_chunk",
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "upload_id": "UPLOADID",
    "chunk_index": 1,
    "base64_data": "JGNEIOGJGE"
  }
}
```

响应示例：

{
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "errcode": 0,
  "errmsg": "ok"
}
    响应字段说明：

```json
{
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "errcode": 0,
  "errmsg": "ok"
}
```


### 上传结束

所有分片上传完成后，调用此接口通知服务端合并分片。服务端会校验所有分片是否齐全、文件 MD5 是否匹配，然后返回 media_id。 请求示例：

{
  "cmd": "aibot_upload_media_finish",
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "upload_id": "UPLOADID"
  }
}
    请求字段说明：

```json
{
  "cmd": "aibot_upload_media_finish",
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "upload_id": "UPLOADID"
  }
}
```

响应示例：

{
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "type": "file",
    "media_id": "1G6nrLmr5EC3MMb_-zK1dDdzmd0p7cNliYu9V5w7o8K0",
    "created_at": "1380000000"
  },
  "errcode": 0,
  "errmsg": "ok"
}
    响应字段说明：

```json
{
  "headers": {
    "req_id": "REQUEST_ID"
  },
  "body": {
    "type": "file",
    "media_id": "1G6nrLmr5EC3MMb_-zK1dDdzmd0p7cNliYu9V5w7o8K0",
    "created_at": "1380000000"
  },
  "errcode": 0,
  "errmsg": "ok"
}
```


---

# API模式机器人文档使用说明

> 最后更新：2026/03/13

目录

- 概述
- 授权操作
- 工具介绍
-       create_doc：新建文档或智能表格
-       edit_doc_content：编辑文档内容
-       smartsheet_add_sheet：添加智能表格子表
-       smartsheet_get_sheet：查询智能表格子表
-       smartsheet_add_fields：添加智能表格字段
-       smartsheet_update_fields：更新智能表格字段
-       smartsheet_get_fields：查询智能表格字段：
-       smartsheet_add_records：添加智能表格记录

#### 概述

API模式创建的机器人，已支持由成员授权机器人「文档」使用权限。授权后，机器人可以便捷地新建、写入文档和智能表格，提高办公效率。成员可以让机器人完成报告生成、信息汇集等工作。文档创建后，机器人将成为文档的创建者；机器人仅可编辑自己创建的文档。目前该能力支持以MCP方式调用。具体能力如下：

- 新建文档：用于新建文档、智能表格，创建后可获得文档链接
- 编辑文档内容：支持写入文档内容，支持markdown格式
- 添加智能表格子表：在智能表格内添加工作表
- 查询智能表格子表：查询智能表格工作表基本信息
- 添加智能表格字段：在智能表格的工作表内新增一列或多列字段
- 更新智能表格字段：更新智能表格字段的标题
- 查询智能表格字段：查询智能表格字段的标题和类型
- 添加智能表格记录：在智能表格内新增一行或多行记录

#### 授权操作

前置要求：需要创建一个API模式机器人

授权流程：

- 入口：工作台-智能机器人-找到对应API模式机器人，点击“编辑”
- 在编辑页，点击「可使用权限」
- 点击授权
- 授权成功，当前授权有效期为7天，点击查看使用方式
- 点击复制streamableHTTP URL或者JSON Config，可根据实际使用场景自行选择
若需在openclaw使用，可使用通过企业微信openclaw插件进行使用，具体可见：以长连接方式接入OpenClaw


#### 工具介绍


##### create_doc：新建文档或智能表格

新建成功后返回文档访问链接和 docid（docid 仅在创建时返回，需妥善保存）。注意：创建智能表格（doc_type=10）时，文档会默认包含一个子表，可通过 smartsheet_get_sheet 查询其 sheet_id，无需额外调用 smartsheet_add_sheet。WARNING: 创建智能表格后，默认子表自带一个默认字段（标题"文本"）。你在添加字段前，必须按以下步骤处理：

- 调用 smartsheet_get_fields 获取默认字段的 field_id
- 调用 smartsheet_update_fields 将默认字段重命名为你需要的第一个字段
- 调用 smartsheet_add_fields 只添加剩余字段，如果跳过步骤1-2直接add_fields，会多出一个无用的默认列
入参

{
    "inputSchema": {
        "properties": {
            "doc_type": {
                "description": "文档类型：3-文档，10-智能表格",
                "enum": [
                    3,
                    10
                ],
                "title": "Doc Type",
                "type": "integer"
            },
            "doc_name": {
                "description": "文档名字，最多 255 个字符，超过会被截断",
                "title": "Doc Name",
                "type": "string"
            }
        },
        "required": [
            "doc_type",
            "doc_name"
        ],
        "title": "create_docArguments",
        "type": "object"
    }
}
    edit_doc_content：编辑文档内容编辑文档内容。content 参数直接传入 Markdown 原文，例如 "# 标题\n正文内容"，不要将 Markdown 文本再用引号包成 JSON 字符串。

```json
{
    "inputSchema": {
        "properties": {
            "doc_type": {
                "description": "文档类型：3-文档，10-智能表格",
                "enum": [
                    3,
                    10
                ],
                "title": "Doc Type",
                "type": "integer"
            },
            "doc_name": {
                "description": "文档名字，最多 255 个字符，超过会被截断",
                "title": "Doc Name",
                "type": "string"
            }
        },
        "required": [
            "doc_type",
            "doc_name"
        ],
        "title": "create_docArguments",
        "type": "object"
    }
}
```


##### edit_doc_content：编辑文档内容

入参

{
    "inputSchema": {
        "properties": {
            "docid": {
                "description": "文档 id",
                "title": "Docid",
                "type": "string"
            },
            "content": {
                "description": "覆写的文档内容，直接传入原始 Markdown 文本，不要对内容做额外的 JSON 转义或用引号包裹",
                "title": "Content",
                "type": "string"
            },
            "content_type": {
                "description": "内容类型格式。1:markdown",
                "enum": [
                    1
                ],
                "title": "Content Type",
                "type": "integer"
            }
        },
        "required": [
            "docid",
            "content",
            "content_type"
        ],
        "title": "edit_doc_contentArguments",
        "type": "object"
    }
}
    smartsheet_add_sheet：添加智能表格子表在指定文档中添加一个空的智能表（子表）。注意：新建的智能表格文档默认已包含一个子表，仅在需要多个子表时才需调用此接口。WARNING: 新建的子表自带一个默认字段（标题"智能表列"）。你在添加字段前，必须按以下步骤处理：

```json
{
    "inputSchema": {
        "properties": {
            "docid": {
                "description": "文档 id",
                "title": "Docid",
                "type": "string"
            },
            "content": {
                "description": "覆写的文档内容，直接传入原始 Markdown 文本，不要对内容做额外的 JSON 转义或用引号包裹",
                "title": "Content",
                "type": "string"
            },
            "content_type": {
                "description": "内容类型格式。1:markdown",
                "enum": [
                    1
                ],
                "title": "Content Type",
                "type": "integer"
            }
        },
        "required": [
            "docid",
            "content",
            "content_type"
        ],
        "title": "edit_doc_contentArguments",
        "type": "object"
    }
}
```


##### smartsheet_add_sheet：添加智能表格子表

- 调用 smartsheet_get_fields 获取默认字段的 field_id
- 调用 smartsheet_update_fields 将默认字段重命名为你需要的第一个字段
- 调用 smartsheet_add_fields 只添加剩余字段，如果跳过步骤1-2直接add_fields，表格会多出一个无用的默认列。
入参

{
    "inputSchema": {
        "$defs": {
            "SheetProperties": {
                "description": "智能表属性",
                "properties": {
                    "title": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "智能表标题",
                        "title": "Title"
                    }
                },
                "title": "SheetProperties",
                "type": "object"
            }
        },
        "properties": {
            "docid": {
                "description": "文档的 docid",
                "title": "Docid",
                "type": "string"
            },
            "properties": {
                "anyOf": [
                    {
                        "$ref": "#/$defs/SheetProperties"
                    },
                    {
                        "type": "null"
                    }
                ],
                "default": null,
                "description": "智能表属性"
            }
        },
        "required": [
            "docid"
        ],
        "title": "smartsheet_add_sheetArguments",
        "type": "object"
    }
}
    smartsheet_get_sheet：查询智能表格子表查询指定文档中的智能表（子表）信息，返回 sheet_id 列表。IMPORTANT: 获取 sheet_id 后，下一步必须调用 smartsheet_get_fields 查看该子表的现有字段。子表默认自带一个文本字段，你需要先用 smartsheet_update_fields 重命名该默认字段，再用 smartsheet_add_fields 添加其余字段。

```json
{
    "inputSchema": {
        "$defs": {
            "SheetProperties": {
                "description": "智能表属性",
                "properties": {
                    "title": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "智能表标题",
                        "title": "Title"
                    }
                },
                "title": "SheetProperties",
                "type": "object"
            }
        },
        "properties": {
            "docid": {
                "description": "文档的 docid",
                "title": "Docid",
                "type": "string"
            },
            "properties": {
                "anyOf": [
                    {
                        "$ref": "#/$defs/SheetProperties"
                    },
                    {
                        "type": "null"
                    }
                ],
                "default": null,
                "description": "智能表属性"
            }
        },
        "required": [
            "docid"
        ],
        "title": "smartsheet_add_sheetArguments",
        "type": "object"
    }
}
```


##### smartsheet_get_sheet：查询智能表格子表

入参

{
    "properties": {
        "docid": {
            "description": "文档的 docid",
            "title": "Docid",
            "type": "string"
        }
    }
}
    smartsheet_add_fields：添加智能表格字段入参

```json
{
    "properties": {
        "docid": {
            "description": "文档的 docid",
            "title": "Docid",
            "type": "string"
        }
    }
}
```


##### smartsheet_add_fields：添加智能表格字段

{
    "inputSchema": {
        "properties": {
            "docid": {
                "description": "文档的 docid",
                "title": "Docid",
                "type": "string"
            },
            "sheet_id": {
                "description": "子表的 sheet ID",
                "title": "Sheet Id",
                "type": "string"
            },
            "fields": {
                "description": "要添加的字段列表",
                "items": {
                    "properties": {
                        "field_title": {
                            "description": "字段标题",
                            "type": "string"
                        },
                        "field_type": {
                            "description": "字段类型。FIELD_TYPE_TEXT: 文本（适用于名称、标题、描述、负责人姓名等自由文本）, FIELD_TYPE_NUMBER: 数字（适用于金额、工时、数量等数值）, FIELD_TYPE_CHECKBOX: 复选框, FIELD_TYPE_DATE_TIME: 日期时间, FIELD_TYPE_IMAGE: 图片, FIELD_TYPE_USER: 用户/成员（需要传入 user_id，仅在明确知道成员ID时使用；如果用户只提供了姓名，应使用 TEXT 类型代替）, FIELD_TYPE_URL: 链接, FIELD_TYPE_SELECT: 多选, FIELD_TYPE_PROGRESS: 进度（适用于完成进度、完成百分比，值为 0-100 的整数）, FIELD_TYPE_PHONE_NUMBER: 手机号, FIELD_TYPE_EMAIL: 邮箱, FIELD_TYPE_SINGLE_SELECT: 单选（适用于状态、优先级、严重程度、分类等有固定选项的字段）, FIELD_TYPE_LOCATION: 位置, FIELD_TYPE_CURRENCY: 货币, FIELD_TYPE_PERCENTAGE: 百分比（适用于比率类数值，如完成率、转化率）, FIELD_TYPE_BARCODE: 条码",
                            "enum": [
                                "FIELD_TYPE_TEXT",
                                "FIELD_TYPE_NUMBER",
                                "FIELD_TYPE_CHECKBOX",
                                "FIELD_TYPE_DATE_TIME",
                                "FIELD_TYPE_IMAGE",
                                "FIELD_TYPE_USER",
                                "FIELD_TYPE_URL",
                                "FIELD_TYPE_SELECT",
                                "FIELD_TYPE_PROGRESS",
                                "FIELD_TYPE_PHONE_NUMBER",
                                "FIELD_TYPE_EMAIL",
                                "FIELD_TYPE_SINGLE_SELECT",
                                "FIELD_TYPE_LOCATION",
                                "FIELD_TYPE_CURRENCY",
                                "FIELD_TYPE_PERCENTAGE",
                                "FIELD_TYPE_BARCODE"
                            ],
                            "type": "string"
                        }
                    },
                    "required": [
                        "field_title",
                        "field_type"
                    ],
                    "type": "object"
                },
                "title": "Fields",
                "type": "array"
            }
        },
        "required": [
            "docid",
            "sheet_id",
            "fields"
        ],
        "title": "smartsheet_add_fieldsArguments",
        "type": "object"
    }
}
    smartsheet_update_fields：更新智能表格字段更新企业微信智能表格子表中一个或多个字段的标题。注意：该接口只能更新字段名，不能更新字段类型（field_type 必须为字段当前的原始类型）。field_title 不能被更新为原值。入参


##### smartsheet_update_fields：更新智能表格字段

{
    "inputSchema": {
        "properties": {
            "docid": {
                "description": "文档的 docid",
                "title": "Docid",
                "type": "string"
            },
            "sheet_id": {
                "description": "子表的 sheet ID",
                "title": "Sheet Id",
                "type": "string"
            },
            "fields": {
                "description": "要更新的字段列表",
                "items": {
                    "properties": {
                        "field_id": {
                            "description": "字段 ID，用于标识要更新的字段，不可更改",
                            "type": "string"
                        },
                        "field_title": {
                            "description": "需要更新为的字段标题，不能更新为原值。",
                            "type": "string"
                        },
                        "field_type": {
                            "description": "字段类型，必须传该字段当前的原始类型，不能更改。FIELD_TYPE_TEXT: 文本, FIELD_TYPE_NUMBER: 数字, FIELD_TYPE_CHECKBOX: 复选框, FIELD_TYPE_DATE_TIME: 日期时间, FIELD_TYPE_IMAGE: 图片, FIELD_TYPE_USER: 用户/成员（需要传入 user_id）, FIELD_TYPE_URL: 链接, FIELD_TYPE_SELECT: 多选, FIELD_TYPE_PROGRESS: 进度, FIELD_TYPE_PHONE_NUMBER: 手机号, FIELD_TYPE_EMAIL: 邮箱, FIELD_TYPE_SINGLE_SELECT: 单选, FIELD_TYPE_LOCATION: 位置, FIELD_TYPE_CURRENCY: 货币, FIELD_TYPE_PERCENTAGE: 百分比, FIELD_TYPE_BARCODE: 条码",
                            "enum": [
                                "FIELD_TYPE_TEXT",
                                "FIELD_TYPE_NUMBER",
                                "FIELD_TYPE_CHECKBOX",
                                "FIELD_TYPE_DATE_TIME",
                                "FIELD_TYPE_IMAGE",
                                "FIELD_TYPE_USER",
                                "FIELD_TYPE_URL",
                                "FIELD_TYPE_SELECT",
                                "FIELD_TYPE_PROGRESS",
                                "FIELD_TYPE_PHONE_NUMBER",
                                "FIELD_TYPE_EMAIL",
                                "FIELD_TYPE_SINGLE_SELECT",
                                "FIELD_TYPE_LOCATION",
                                "FIELD_TYPE_CURRENCY",
                                "FIELD_TYPE_PERCENTAGE",
                                "FIELD_TYPE_BARCODE"
                            ],
                            "type": "string"
                        }
                    },
                    "required": [
                        "field_id",
                        "field_type"
                    ],
                    "type": "object"
                },
                "title": "Fields",
                "type": "array"
            }
        },
        "required": [
            "docid",
            "sheet_id",
            "fields"
        ],
        "title": "smartsheet_update_fieldsArguments",
        "type": "object"
    }
}
    smartsheet_get_fields：查询智能表格字段：
      {
    "inputSchema": {
        "properties": {
            "docid": {
                "description": "文档的 docid",
                "title": "Docid",
                "type": "string"
            },
            "sheet_id": {
                "description": "子表的 sheet ID",
                "title": "Sheet Id",
                "type": "string"
            }
        },
        "required": [
            "docid",
            "sheet_id"
        ],
        "title": "smartsheet_get_fieldsArguments",
        "type": "object"
    }
}
    smartsheet_add_records：添加智能表格记录在企业微信智能表格的某个子表里添加一行或多行新记录。在调用该工具前，你要先了解目标表的各列的类型。你可能需要重点关注在查询字段工具返回的field_type，对于添加记录中的一些复杂嵌套字段，比如Option，你需要注意查询中返回的 id 的匹配。单次添加记录建议在 500 行内。


##### smartsheet_get_fields：查询智能表格字段：


##### smartsheet_add_records：添加智能表格记录

入参

```json
{
    "inputSchema": {
        "properties": {
            "AddRecord": {
                "description": "单条待添加的记录（AddRecord）",
                "properties": {
                    "values": {
                        "additionalProperties": {
                            "anyOf": [
                                {
                                    "items": {
                                        "$ref": "#/$defs/CellTextValue"
                                    },
                                    "type": "array"
                                },
                                {
                                    "type": "number"
                                },
                                {
                                    "type": "boolean"
                                },
                                {
                                    "type": "string"
                                },
                                {
                                    "items": {
                                        "$ref": "#/$defs/CellImageValue"
                                    },
                                    "type": "array"
                                },
                                {
                                    "items": {
                                        "$ref": "#/$defs/CellUserValue"
                                    },
                                    "type": "array"
                                },
                                {
                                    "items": {
                                        "$ref": "#/$defs/CellUrlValue"
                                    },
                                    "type": "array"
                                },
                                {
                                    "items": {
                                        "$ref": "#/$defs/Option"
                                    },
                                    "type": "array"
                                },
                                {
                                    "items": {
                                        "$ref": "#/$defs/CellLocationValue"
                                    },
                                    "type": "array"
                                }
                            ]
                        },
                        "description": "记录的具体内容。【重要】key 必须是字段标题（field_title），不能使用字段ID（field_id），否则会报错。各字段类型的值格式说明：1. 文本(TEXT)：必须使用数组格式 [{\"type\":\"text\", \"text\":\"内容\"}]，注意外层方括号不可省略，不能传单个对象 {\"type\":\"text\",\"text\":\"内容\"}；2. 数字(NUMBER)/货币(CURRENCY)/百分比(PERCENTAGE)/进度(PROGRESS)：直接传数字，例如 100、0.6；3. 复选框(CHECKBOX)：直接传 true/false；4. 单选(SINGLE_SELECT)/多选(SELECT)：必须使用数组格式 [{\"text\":\"选项内容\"}]，不能直接传字符串；5. 日期时间(DATE_TIME)：传日期时间字符串，支持格式：\"YYYY-MM-DD HH:MM:SS\"（精确到秒）、\"YYYY-MM-DD HH:MM\"（精确到分）、\"YYYY-MM-DD\"（精确到天），例如 \"2026-01-15 14:30:00\" 或 \"2026-01-15\"。系统会自动按东八区转换为时间戳，无需手动计算；6. 手机号(PHONE_NUMBER)/邮箱(EMAIL)/条码(BARCODE)：直接传字符串，例如 \"13800138000\"；7. 成员(USER)：数组格式 [{\"user_id\":\"成员ID\"}]；8. 超链接(URL)：数组格式 [{\"type\":\"url\", \"text\":\"显示文本\", \"link\":\"https://...\"}]；9. 图片(IMAGE)：数组格式 [{\"image_url\":\"图片链接\"}]；10. 地理位置(LOCATION)：数组格式 [{\"source_type\":1, \"id\":\"地点ID\", \"latitude\":\"纬度\", \"longitude\":\"经度\", \"title\":\"地点名\"}]",
                        "title": "Values",
                        "type": "object"
                    }
                },
                "required": [
                    "values"
                ],
                "title": "AddRecord",
                "type": "object"
            },
            "CellImageValue": {
                "description": "图片类型字段的单元值",
                "properties": {
                    "id": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "图片 ID，自定义 id",
                        "title": "Id"
                    },
                    "title": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "图片标题",
                        "title": "Title"
                    },
                    "image_url": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "图片链接，通过上传图片接口获取",
                        "title": "Image Url"
                    },
                    "width": {
                        "anyOf": [
                            {
                                "type": "integer"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "图片宽度",
                        "title": "Width"
                    },
                    "height": {
                        "anyOf": [
                            {
                                "type": "integer"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "图片高度",
                        "title": "Height"
                    }
                },
                "title": "CellImageValue",
                "type": "object"
            },
            "CellLocationValue": {
                "description": "地理位置类型字段的单元值",
                "properties": {
                    "source_type": {
                        "const": 1,
                        "description": "来源类型，填 1 表示来源为腾讯地图，目前只支持腾讯地图来源",
                        "title": "Source Type",
                        "type": "integer"
                    },
                    "id": {
                        "description": "地点 ID",
                        "title": "Id",
                        "type": "string"
                    },
                    "latitude": {
                        "description": "纬度",
                        "title": "Latitude",
                        "type": "string"
                    },
                    "longitude": {
                        "description": "经度",
                        "title": "Longitude",
                        "type": "string"
                    },
                    "title": {
                        "description": "地点名称",
                        "title": "Title",
                        "type": "string"
                    }
                },
                "required": [
                    "source_type",
                    "id",
                    "latitude",
                    "longitude",
                    "title"
                ],
                "title": "CellLocationValue",
                "type": "object"
            },
            "CellTextValue": {
                "description": "文本类型字段的单元值",
                "properties": {
                    "type": {
                        "description": "内容类型，text 为文本，url 为链接",
                        "enum": [
                            "text",
                            "url"
                        ],
                        "title": "Type",
                        "type": "string"
                    },
                    "text": {
                        "description": "单元格文本内容",
                        "title": "Text",
                        "type": "string"
                    },
                    "link": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "当 type 为 url 时，表示链接跳转 URL",
                        "title": "Link"
                    }
                },
                "required": [
                    "type",
                    "text"
                ],
                "title": "CellTextValue",
                "type": "object"
            },
            "CellUrlValue": {
                "description": "超链接类型字段的单元值。数组类型为预留能力，目前只支持展示一个链接，建议只传入一个链接",
                "properties": {
                    "type": {
                        "const": "url",
                        "description": "固定填 url",
                        "title": "Type",
                        "type": "string"
                    },
                    "text": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "链接显示文本",
                        "title": "Text"
                    },
                    "link": {
                        "description": "链接跳转 URL",
                        "title": "Link",
                        "type": "string"
                    }
                },
                "required": [
                    "type",
                    "link"
                ],
                "title": "CellUrlValue",
                "type": "object"
            },
            "CellUserValue": {
                "description": "成员类型字段的单元值",
                "properties": {
                    "user_id": {
                        "description": "成员 ID",
                        "title": "User Id",
                        "type": "string"
                    }
                },
                "required": [
                    "user_id"
                ],
                "title": "CellUserValue",
                "type": "object"
            },
            "Option": {
                "description": "选项",
                "properties": {
                    "id": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "选项 ID，当选项存在时通过 ID 识别选项；需要新增选项时不填写此字段",
                        "title": "Id"
                    },
                    "style": {
                        "anyOf": [
                            {
                                "maximum": 27,
                                "minimum": 1,
                                "type": "integer"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "选项颜色，取值 1-27 对应不同颜色，分别为浅红1, 浅橙1, 浅天蓝1, 浅绿1, 浅紫1, 浅粉红1, 浅灰1, 白, 灰, 浅蓝1, 浅蓝2, 蓝, 浅天蓝2, 天蓝, 浅绿2, 绿, 浅红2, 红, 浅橙2, 橙, 浅黄1, 浅黄2, 黄, 浅紫2, 紫, 浅粉红2, 粉红",
                        "title": "Style"
                    },
                    "text": {
                        "anyOf": [
                            {
                                "type": "string"
                            },
                            {
                                "type": "null"
                            }
                        ],
                        "default": null,
                        "description": "要填写的选项内容。新增选项时填写，已经存在时优先匹配已存在的选项，否则会新增选项",
                        "title": "Text"
                    }
                },
                "title": "Option",
                "type": "object"
            }
        },
        "properties": {
            "docid": {
                "description": "文档的 docid",
                "title": "Docid",
                "type": "string"
            },
            "sheet_id": {
                "description": "Smartsheet 子表 ID",
                "title": "Sheet Id",
                "type": "string"
            },
            "records": {
                "description": "需要添加的记录的具体内容组成的 JSON 数组",
                "items": {
                    "$ref": "#/$defs/AddRecord"
                },
                "title": "Records",
                "type": "array"
            }
        },
        "required": [
            "docid",
            "sheet_id",
            "records"
        ],
        "title": "smartsheet_add_recordsArguments",
        "type": "object"
    }
}
```


---


# ====== 自建应用消息接收与发送 ======

> 以下为「消息接收与发送」板块中自建应用相关的 API 文档

---

# 消息接收与发送 概述

> 最后更新：2021/02/02

目录

- 接口概括
企业微信开放了消息发送接口，企业可以使用这些接口让自定义应用与企业微信后台或用户间进行双向通信。


### 接口概括

消息接口总体上分为主动发送单聊消息、接收单聊消息以及发送消息到群三部分

- 主动发送应用消息：企业后台调用接口通过应用向指定成员发送单聊消息
- 接收消息：企业后台接收来自成员的消息或事件要使用接收消息，需要在应用中设置开发者的回调服务器配置。
- 接收消息分为两种：1. 成员在应用客户端里发送的消息；2. 某种条件下触发的事件消息。
- 开发者后台在接收消息后，可以在响应的返回包里带上回复消息，企业微信会将这条消息推送给成员。这就是“被动回复消息”。
- 发送消息到群聊会话：企业后台调用接口创建群聊后，可通过应用推送消息到群内。（暂不支持接收群聊消息）
消息接口流程图如下：(图中"URL"为用户配置的接收消息服务器地址)


---

# 发送应用消息

> 最后更新：2025/09/24

目录

- 接口定义
- 消息类型
-       文本消息
-       图片消息
-       语音消息
-       视频消息
-       文件消息
-       文本卡片消息
-       图文消息
-       图文消息（mpnews）
-       markdown消息
-       小程序通知消息
-       模板卡片消息
-             文本通知型
-             图文展示型
-             按钮交互型
-             投票选择型
-             多项选择型
- 附录
-       支持的markdown语法
-       id转译说明

### 接口定义

应用支持推送文本、图片、视频、文件、图文等类型。 请求方式：POST（HTTPS）请求地址： https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=ACCESS_TOKEN

参数说明：


| 参数 | 是否必须 | 说明 |
| --- | --- | --- |
| access_token | 是 | 调用接口凭证 |

返回示例：

{
  "errcode" : 0,
  "errmsg" : "ok",
  "invaliduser" : "userid1|userid2",
  "invalidparty" : "partyid1|partyid2",
  "invalidtag": "tagid1|tagid2",
  "unlicenseduser" : "userid3|userid4",
  "msgid": "xxxx",
  "response_code": "xyzxyz"
}
    如果部分接收人无权限或不存在，发送仍然执行，但会返回无效的部分（即invaliduser或invalidparty或invalidtag或unlicenseduser），常见的原因是接收人不在应用的可见范围内。权限包含应用可见范围和基础接口权限(基础账号、互通账号均可)，unlicenseduser中的用户在应用可见范围内但没有基础接口权限。如果全部接收人无权限或不存在，则本次调用返回失败，errcode为81013。返回包中的userid，不区分大小写，统一转为小写 

```json
{
  "errcode" : 0,
  "errmsg" : "ok",
  "invaliduser" : "userid1|userid2",
  "invalidparty" : "partyid1|partyid2",
  "invalidtag": "tagid1|tagid2",
  "unlicenseduser" : "userid3|userid4",
  "msgid": "xxxx",
  "response_code": "xyzxyz"
}
```

参数说明：


| 参数 | 说明 |
| --- | --- |
| errcode | 返回码 |
| errmsg | 对返回码的文本描述内容 |
| invaliduser | 不合法的userid，不区分大小写，统一转为小写 |
| invalidparty | 不合法的partyid |
| invalidtag | 不合法的标签id |
| unlicenseduser | 没有基础接口许可(包含已过期)的userid |
| msgid | 消息id，用于撤回应用消息 |
| response_code | 仅消息类型为“按钮交互型”，“投票选择型”和“多项选择型”，以及填写了action_menu字段的文本通知型、图文展示型的模板卡片消息返回，应用可使用response_code调用更新模版卡片消息接口，72小时内有效，且只能使用一次 |


### 消息类型


#### 文本消息

请求示例：

```json
{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1|PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "text",
   "agentid" : 1,
   "text" : {
       "content" : "你的快递已到，请携带工卡前往邮件中心领取。\n出发前可查看<a href=\"https://work.weixin.qq.com\">邮件中心视频实况</a>，聪明避开排队。"
   },
   "safe":0,
   "enable_id_trans": 0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
```

文本消息展现：

特殊说明：其中text参数的content字段可以支持换行、以及A标签，即可打开自定义的网页（可参考以上示例代码）(注意：换行符请用转义过的\n)


#### 图片消息

请求示例：

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1|PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "image",
   "agentid" : 1,
   "image" : {
        "media_id" : "MEDIA_ID"
   },
   "safe":0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    请求参数：


#### 语音消息

请求示例：

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1|PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "voice",
   "agentid" : 1,
   "voice" : {
        "media_id" : "MEDIA_ID"
   },
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    参数说明：


#### 视频消息

请求示例：

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1|PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "video",
   "agentid" : 1,
   "video" : {
        "media_id" : "MEDIA_ID",
        "title" : "Title",
       "description" : "Description"
   },
   "safe":0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    参数说明：

视频消息展现：


#### 文件消息

请求示例：

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1|PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "file",
   "agentid" : 1,
   "file" : {
        "media_id" : "1Yv-zXfHjSjU-7LH-GwtYqDGS-zz6w22KmWAT5COgP7o"
   },
   "safe":0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    参数说明：

文件消息展现：


#### 文本卡片消息

请求示例：

```json
{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1 | PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "textcard",
   "agentid" : 1,
   "textcard" : {
            "title" : "领奖通知",
            "description" : "<div class=\"gray\">2016年9月26日</div> <div class=\"normal\">恭喜你抽中iPhone 7一台，领奖码：xxxx</div><div class=\"highlight\">请于2016年10月10日前联系行政同事领取</div>",
            "url" : "URL",
                        "btntxt":"更多"
   },
   "enable_id_trans": 0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
```

文本卡片消息展现 ：

特殊说明：卡片消息的展现形式非常灵活，支持使用br标签或者空格来进行换行处理，也支持使用div标签来使用不同的字体颜色，目前内置了3种文字颜色：灰色(gray)、高亮(highlight)、默认黑色(normal)，将其作为div标签的class属性即可，具体用法请参考上面的示例。


#### 图文消息

请求示例：

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1 | PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "news",
   "agentid" : 1,
   "news" : {
       "articles" : [
           {
               "title" : "中秋节礼品领取",
               "description" : "今年中秋节公司有豪礼相送",
               "url" : "URL",
               "picurl" : "https://res.mail.qq.com/node/ww/wwopenmng/images/independent/doc/test_pic_msg1.png", 
			   "appid": "wx123123123123123",
        	   "pagepath": "pages/index?userid=zhangsan&orderid=123123123"
           }
        ]
   },
   "enable_id_trans": 0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    参数说明：

图文消息展现：


#### 图文消息（mpnews）

请求示例：

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1 | PartyID2",
   "totag": "TagID1 | TagID2",
   "msgtype" : "mpnews",
   "agentid" : 1,
   "mpnews" : {
       "articles":[
           {
               "title": "Title", 
               "thumb_media_id": "MEDIA_ID",
               "author": "Author",
               "content_source_url": "URL",
               "content": "Content",
               "digest": "Digest description"
            }
       ]
   },
   "safe":0,
   "enable_id_trans": 0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}

    参数说明：


#### markdown消息

请求示例：

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1|PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype": "markdown",
   "agentid" : 1,
   "markdown": {
        "content": "您的会议室已经预定，稍后会同步到`邮箱`  \n>**事项详情**  \n>事　项：<font color=\"info\">开会</font>  \n>组织者：@miglioguan  \n>参与者：@miglioguan、@kunliu、@jamdeezhou、@kanexiong、@kisonwang  \n>  \n>会议室：<font color=\"info\">广州TIT 1楼 301</font>  \n>日　期：<font color=\"warning\">2018年5月18日</font>  \n>时　间：<font color=\"comment\">上午9:00-11:00</font>  \n>  \n>请准时参加会议。  \n>  \n>如需修改会议信息，请点击：[修改会议信息](https://work.weixin.qq.com)"
   },
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    示例效果：

参数说明：


#### 小程序通知消息

请求示例：

{
   "touser" : "zhangsan|lisi",
   "toparty": "1|2",
   "totag": "1|2",
   "msgtype" : "miniprogram_notice",
   "miniprogram_notice" : {
        "appid": "wx123123123123123",
        "page": "pages/index?userid=zhangsan&orderid=123123123",
        "title": "会议室预订成功通知",
        "description": "4月27日 16:16",
        "emphasis_first_item": true,
        "content_item": [
            {
                "key": "会议室",
                "value": "402"
            },
            {
                "key": "会议地点",
                "value": "广州TIT-402会议室"
            },
            {
                "key": "会议时间",
                "value": "2018年8月1日 09:00-09:30"
            },
            {
                "key": "参与人员",
                "value": "周剑轩"
            }
        ]
    },
   "enable_id_trans": 0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    示例效果：

```json
{
   "touser" : "zhangsan|lisi",
   "toparty": "1|2",
   "totag": "1|2",
   "msgtype" : "miniprogram_notice",
   "miniprogram_notice" : {
        "appid": "wx123123123123123",
        "page": "pages/index?userid=zhangsan&orderid=123123123",
        "title": "会议室预订成功通知",
        "description": "4月27日 16:16",
        "emphasis_first_item": true,
        "content_item": [
            {
                "key": "会议室",
                "value": "402"
            },
            {
                "key": "会议地点",
                "value": "广州TIT-402会议室"
            },
            {
                "key": "会议时间",
                "value": "2018年8月1日 09:00-09:30"
            },
            {
                "key": "参与人员",
                "value": "周剑轩"
            }
        ]
    },
   "enable_id_trans": 0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
```

参数说明：

<!-- ###任务卡片消息

{
   "touser" : "UserID1|UserID2|UserID3",
   "toparty" : "PartyID1 | PartyID2",
   "totag" : "TagID1 | TagID2",
   "msgtype" : "interactive_taskcard",
   "agentid" : 1,
   "interactive_taskcard" : {
		"title" : "赵明登的礼物申请",
		"description" : "礼品：A31茶具套装\n用途：赠与小黑科技张总经理",
		"url" : "URL",
		"task_id" : "taskid123",
		"btn":[
			{
				"key": "key111",
				"name": "批准",
				"color":"red",
				"is_bold": true
			},
			{
				"key": "key222",
				"name": "驳回"
			}
		]
   },
   "enable_id_trans": 0,
   "enable_duplicate_check": 0,
   "duplicate_check_interval": 1800
}
    参数说明：

任务卡片消息展现 ：

特殊说明：

- 任务卡片消息的展现支持简单的markdown语法，详情请见附录支持的markdown语法 。
- 要发送该类型的消息，应用必须配置好回调URL，详见配置应用回调，用户点击任务卡片的按钮后，企业微信会回调任务卡片事件到该URL，配置的URL服务必须按照任务卡片更新消息协议返回数据，否则客户端会报错。
- 如果不想成员再点击卡片，可以通过更新任务卡片消息状态接口更新卡片状态（该接口不可代替回调协议的返回数据，配置的回调URL服务必须按照协议返回数据）。-->模板卡片消息投票选择型和多项选择型卡片仅企业微信3.1.12及以上版本支持文本通知型、图文展示型和按钮交互型三种卡片仅企业微信3.1.6及以上版本支持（但附件下载功能仍需更新至3.1.12）微工作台（原企业号）不支持展示模板卡片消息3.1.18版本新增source字段支持设置字体颜色

#### 模板卡片消息

3.1.18版本新增

- horizontal_content_list新增type 3，代表点击跳转成员详情（仅企业微信3.1.18及以上版本支持）
- 新增action_menu(右上角菜单)（仅企业微信3.1.18及以上版本支持）
- quote_area(引用样式)、image_text_area(左图右文样式)、button_selection(按钮型卡片的下拉框样式)等字段
特殊说明

- 仅有 按钮交互型、投票选择型、多项选择型 以及填写了action_menu字段的文本通知型、图文展示型的卡片支持回调更新或通过接口更新卡片。
- 支持回调更新的卡片可支持用户点击触发交互事件，需要开发者设置的回调接口来处理回调事件，回调协议可见文档 模板卡片事件推送，注意 没有配置回调接口的应用不可发送支持回调的卡片。
- 开发者的服务收到回调事件后，需要根据协议返回相应的数据以更新卡片，对应的协议见文档 更新模版卡片消息。
- 此接口发送支持回调更新的卡片消息之后，返回的参数里会带上response_code，可使用response_code调用更新模版卡片消息接口，response_code 72小时内有效，且只能调用一次接口。

##### 文本通知型

```json
{
    "touser" : "UserID1|UserID2|UserID3",
    "toparty" : "PartyID1 | PartyID2",
    "totag" : "TagID1 | TagID2",
    "msgtype" : "template_card",
    "agentid" : 1,
    "template_card" : {
        "card_type" : "text_notice",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信",
            "desc_color": 1
        },
        "action_menu": {
            "desc": "卡片副交互辅助文本说明",
            "action_list": [
                {"text": "接受推送", "key": "A"},
                {"text": "不再推送", "key": "B"}
            ]
        },
        "task_id": "task_id",
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "quote_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的引用样式",
            "quote_text": "企业微信真好用呀真好用"
        },
        "emphasis_content": {
            "title": "100",
            "desc": "核心数据"
        },
        "sub_title_text" : "下载企业微信还能抢红包！",
        "horizontal_content_list" : [
            {
                "keyname": "邀请人",
                "value": "张三"
            },
            {
                "type": 1,
                "keyname": "企业微信官网",
                "value": "点击访问",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "keyname": "企业微信下载",
                "value": "企业微信.apk",
                "media_id": "文件的media_id"
            },
            {
                "type": 3,
                "keyname": "员工信息",
                "value": "点击查看",
                "userid": "zhangsan"
            }
        ],
        "jump_list" : [
            {
                "type": 1,
                "title": "企业微信官网",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "title": "跳转小程序",
                "appid": "小程序的appid",
                "pagepath": "/index.html"
            }
        ],
        "card_action": {
            "type": 2,
            "url": "https://work.weixin.qq.com",
            "appid": "小程序的appid",
            "pagepath": "/index.html"
        }
    },
    "enable_id_trans": 0,
    "enable_duplicate_check": 0,
    "duplicate_check_interval": 1800
}
```


##### 图文展示型

{
    "touser" : "UserID1|UserID2|UserID3",
    "toparty" : "PartyID1 | PartyID2",
    "totag" : "TagID1 | TagID2",
    "msgtype" : "template_card",
    "agentid" : 1,
    "template_card" : {
        "card_type" : "news_notice",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信",
            "desc_color": 1
        },
        "action_menu": {
            "desc": "卡片副交互辅助文本说明",
            "action_list": [
                {"text": "接受推送", "key": "A"},
                {"text": "不再推送", "key": "B"}
            ]
        },
        "task_id": "task_id",
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "quote_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的引用样式",
            "quote_text": "企业微信真好用呀真好用"
        },
        "image_text_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的左图右文样式",
            "desc": "企业微信真好用呀真好用",
            "image_url": "https://img.iplaysoft.com/wp-content/uploads/2019/free-images/free_stock_photo_2x.jpg"
        },
        "card_image": {
            "url": "图片的url",
            "aspect_ratio": 1.3
        },
        "vertical_content_list": [
            {
                "title": "惊喜红包等你来拿",
                "desc": "下载企业微信还能抢红包！"
            }
        ],
        "horizontal_content_list" : [
            {
                "keyname": "邀请人",
                "value": "张三"
            },
            {
                "type": 1,
                "keyname": "企业微信官网",
                "value": "点击访问",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "keyname": "企业微信下载",
                "value": "企业微信.apk",
                "media_id": "文件的media_id"
            },
            {
                "type": 3,
                "keyname": "员工信息",
                "value": "点击查看",
                "userid": "zhangsan"
            }
        ],
        "jump_list" : [
            {
                "type": 1,
                "title": "企业微信官网",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "title": "跳转小程序",
                "appid": "小程序的appid",
                "pagepath": "/index.html"
            }
        ],
        "card_action": {
            "type": 2,
            "url": "https://work.weixin.qq.com",
            "appid": "小程序的appid",
            "pagepath": "/index.html"
        }
    },
    "enable_id_trans": 0,
    "enable_duplicate_check": 0,
    "duplicate_check_interval": 1800
}
    
        参数是否必须说明touser否成员ID列表（消息接收者，多个接收者用‘|’分隔，最多支持1000个）。特殊情况：指定为@all，则向关注该企业应用的全部成员发送toparty否部门ID列表，多个接收者用‘|’分隔，最多支持100个。当touser为@all时忽略本参数totag否标签ID列表，多个接收者用‘|’分隔，最多支持100个。当touser为@all时忽略本参数msgtype是消息类型，此时固定为：template_cardagentid是企业应用的id，整型。企业内部开发，可在应用的设置页面查看；第三方服务商，可通过接口 获取企业授权信息 获取该参数值card_type是模板卡片类型，图文展示型卡片此处填写 "news_notice"source否卡片来源样式信息，不需要来源样式可不填写source.icon_url否来源图片的url，来源图片的尺寸建议为72*72source.desc否来源图片的描述，建议不超过20个字，（支持id转译）source.desc_color否来源文字的颜色，目前支持：0(默认) 灰色，1 黑色，2 红色，3 绿色action_menu否卡片右上角更多操作按钮action_menu.desc否更多操作界面的描述action_menu.action_list是操作列表，列表长度取值范围为 [1, 3]action_menu.action_list.text是操作的描述文案action_menu.action_list.key是操作key值，用户点击后，会产生回调事件将本参数作为EventKey返回，回调事件会带上该key值，最长支持1024字节，不可重复main_title.title是一级标题，建议不超过36个字，（支持id转译）main_title.desc否标题辅助信息，建议不超过44个字，（支持id转译）quote_area否引用文献样式quote_area.type否引用文献样式区域点击事件，0或不填代表没有点击事件，1 代表跳转url，2 代表跳转小程序quote_area.url否点击跳转的url，quote_area.type是1时必填quote_area.appid否点击跳转的小程序的appid，必须是与当前应用关联的小程序，quote_area.type是2时必填quote_area.pagepath否点击跳转的小程序的pagepath，quote_area.type是2时选填quote_area.title否引用文献样式的标题quote_area.quote_text否引用文献样式的引用文案image_text_area否左图右文样式，news_notice类型的卡片，card_image和image_text_area两者必填一个字段，不可都不填image_text_area.type否左图右文样式区域点击事件，0或不填代表没有点击事件，1 代表跳转url，2 代表跳转小程序image_text_area.url否点击跳转的url，image_text_area.type是1时必填image_text_area.appid否点击跳转的小程序的appid，必须是与当前应用关联的小程序，image_text_area.type是2时必填image_text_area.pagepath否点击跳转的小程序的pagepath，image_text_area.type是2时选填image_text_area.title否左图右文样式的标题image_text_area.desc否左图右文样式的描述image_text_area.image_url是左图右文样式的图片urlcard_image否图片样式，news_notice类型的卡片，card_image和image_text_area两者必填一个字段，不可都不填card_image.url是图片的urlcard_image.aspect_ratio否图片的宽高比，宽高比要小于2.25，大于1.3，不填该参数默认1.3vertical_content_list否卡片二级垂直内容，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过4vertical_content_list.title是卡片二级标题，建议不超过38个字vertical_content_list.desc否二级普通文本，建议不超过160个字horizontal_content_list否二级标题+文本列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过6horizontal_content_list.type否链接类型，0或不填代表不是链接，1 代表跳转url，2 代表下载附件，3 代表点击跳转成员详情horizontal_content_list.keyname是二级标题，建议不超过5个字horizontal_content_list.value否二级文本，如果horizontal_content_list.type是2，该字段代表文件名称（要包含文件类型），建议不超过30个字，（支持id转译）horizontal_content_list.url否链接跳转的url，horizontal_content_list.type是1时必填horizontal_content_list.media_id否附件的media_id，horizontal_content_list.type是2时必填horizontal_content_list.userid否成员详情的userid，horizontal_content_list.type是3时必填jump_list否跳转指引样式的列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过3jump_list.type否跳转链接类型，0或不填代表不是链接，1 代表跳转url，2 代表跳转小程序jump_list.title是跳转链接样式的文案内容，建议不超过18个字jump_list.url否跳转链接的url，jump_list.type是1时必填jump_list.appid否跳转链接的小程序的appid，必须是与当前应用关联的小程序，jump_list.type是2时必填jump_list.pagepath否跳转链接的小程序的pagepath，jump_list.type是2时选填card_action是整体卡片的点击跳转事件，news_notice必填本字段card_action.type是跳转事件类型，1 代表跳转url，2 代表打开小程序。news_notice卡片模版中该字段取值范围为[1,2]card_action.url否跳转事件的url，card_action.type是1时必填card_action.appid否跳转事件的小程序的appid，必须是与当前应用关联的小程序，card_action.type是2时必填card_action.pagepath否跳转事件的小程序的pagepath，card_action.type是2时选填task_id否任务id，同一个应用任务id不能重复，只能由数字、字母和“_-@”组成，最长128字节，填了action_menu字段的话本字段必填enable_id_trans否表示是否开启id转译，0表示否，1表示是，默认0enable_duplicate_check否表示是否开启重复消息检查，0表示否，1表示是，默认0duplicate_check_interval否表示是否重复消息检查的时间间隔，默认1800s，最大不超过4小时按钮交互型


##### 按钮交互型

{
    "touser" : "UserID1|UserID2|UserID3",
    "toparty" : "PartyID1 | PartyID2",
    "totag" : "TagID1 | TagID2",
    "msgtype" : "template_card",
    "agentid" : 1,
    "template_card" : {
        "card_type" : "button_interaction",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信",
            "desc_color": 1
        },
        "action_menu": {
            "desc": "卡片副交互辅助文本说明",
            "action_list": [
                {"text": "接受推送", "key": "A"},
                {"text": "不再推送", "key": "B"}
            ]
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "quote_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的引用样式",
            "quote_text": "企业微信真好用呀真好用"
        },
        "sub_title_text" : "下载企业微信还能抢红包！",
        "horizontal_content_list" : [
            {
                "keyname": "邀请人",
                "value": "张三"
            },
            {
                "type": 1,
                "keyname": "企业微信官网",
                "value": "点击访问",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "keyname": "企业微信下载",
                "value": "企业微信.apk",
                "media_id": "文件的media_id"
            },
            {
                "type": 3,
                "keyname": "员工信息",
                "value": "点击查看",
                "userid": "zhangsan"
            }
        ],
        "card_action": {
            "type": 2,
            "url": "https://work.weixin.qq.com",
            "appid": "小程序的appid",
            "pagepath": "/index.html"
        },
        "task_id": "task_id",
        "button_selection": {
            "question_key": "btn_question_key1",
            "title": "企业微信评分",
            "option_list": [
                {
                    "id": "btn_selection_id1",
                    "text": "100分"
                },
                {
                    "id": "btn_selection_id2",
                    "text": "101分"
                }
            ],
            "selected_id": "btn_selection_id1"
        },
        "button_list": [
            {
                "text": "按钮1",
                "style": 1,
                "key": "button_key_1"
            },
            {
                "text": "按钮2",
                "style": 2,
                "key": "button_key_2"
            }
        ]
    },
    "enable_id_trans": 0,
    "enable_duplicate_check": 0,
    "duplicate_check_interval": 1800
}
    
        参数是否必须说明touser否成员ID列表（消息接收者，多个接收者用‘|’分隔，最多支持1000个）。特殊情况：指定为@all，则向关注该企业应用的全部成员发送toparty否部门ID列表，多个接收者用‘|’分隔，最多支持100个。当touser为@all时忽略本参数totag否标签ID列表，多个接收者用‘|’分隔，最多支持100个。当touser为@all时忽略本参数msgtype是消息类型，此时固定为：template_cardagentid是企业应用的id，整型。企业内部开发，可在应用的设置页面查看；第三方服务商，可通过接口 获取企业授权信息 获取该参数值card_type是模板卡片类型，按钮交互型卡片填写"button_interaction"source否卡片来源样式信息，不需要来源样式可不填写source.icon_url否来源图片的url，来源图片的尺寸建议为72*72source.desc否来源图片的描述，建议不超过20个字，（支持id转译）source.desc_color否来源文字的颜色，目前支持：0(默认) 灰色，1 黑色，2 红色，3 绿色action_menu否卡片右上角更多操作按钮action_menu.desc否更多操作界面的描述action_menu.action_list是操作列表，列表长度取值范围为 [1, 3]action_menu.action_list.text是操作的描述文案action_menu.action_list.key是操作key值，用户点击后，会产生回调事件将本参数作为EventKey返回，回调事件会带上该key值，最长支持1024字节，不可重复main_title.title是一级标题，建议不超过36个字，（支持id转译）main_title.desc否标题辅助信息，建议不超过44个字，（支持id转译）quote_area否引用文献样式quote_area.type否引用文献样式区域点击事件，0或不填代表没有点击事件，1 代表跳转url，2 代表跳转小程序quote_area.url否点击跳转的url，quote_area.type是1时必填quote_area.appid否点击跳转的小程序的appid，必须是与当前应用关联的小程序，quote_area.type是2时必填quote_area.pagepath否点击跳转的小程序的pagepath，quote_area.type是2时选填quote_area.title否引用文献样式的标题quote_area.quote_text否引用文献样式的引用文案sub_title_text否二级普通文本，建议不超过160个字，（支持id转译）horizontal_content_list否二级标题+文本列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过6horizontal_content_list.type否链接类型，0或不填代表不是链接，1 代表跳转url，2 代表下载附件，3 代表点击跳转成员详情horizontal_content_list.keyname是二级标题，建议不超过5个字horizontal_content_list.value否二级文本，如果horizontal_content_list.type是2，该字段代表文件名称（要包含文件类型），建议不超过30个字，（支持id转译）horizontal_content_list.url否链接跳转的url，horizontal_content_list.type是1时必填horizontal_content_list.media_id否附件的media_id，horizontal_content_list.type是2时必填horizontal_content_list.userid否成员详情的userid，horizontal_content_list.type是3时必填card_action否整体卡片的点击跳转事件card_action.type否跳转事件类型，0或不填代表不是链接，1 代表跳转url，2 代表打开小程序card_action.url否跳转事件的url，card_action.type是1时必填card_action.appid否跳转事件的小程序的appid，必须是与当前应用关联的小程序，card_action.type是2时必填card_action.pagepath否跳转事件的小程序的pagepath，card_action.type是2时选填task_id是任务id，同一个应用任务id不能重复，只能由数字、字母和“_-@”组成，最长128字节button_selection.question_key是下拉式的选择器的key，用户提交选项后，会产生回调事件，回调事件会带上该key值表示该题，最长支持1024字节button_selection.title否下拉式的选择器左边的标题button_selection.option_list是选项列表，下拉选项不超过 10 个，最少1个button_selection.selected_id否默认选定的id，不填或错填默认第一个button_selection.option_list.id是下拉式的选择器选项的id，用户提交后，会产生回调事件，回调事件会带上该id值表示该选项，最长支持128字节，不可重复button_selection.option_list.text是下拉式的选择器选项的文案，建议不超过16个字button_list是按钮列表，列表长度不超过6button_list.type否按钮点击事件类型，0 或不填代表回调点击事件，1 代表跳转urlbutton_list.text是按钮文案，建议不超过10个字button_list.style否按钮样式，目前可填1~4，不填或错填默认1button_list.key否按钮key值，用户点击后，会产生回调事件将本参数作为EventKey返回，回调事件会带上该key值，最长支持1024字节，不可重复，button_list.type是0时必填button_list.url否跳转事件的url，button_list.type是1时必填备注：按钮样式


##### 投票选择型

{
    "touser" : "UserID1|UserID2|UserID3",
    "toparty" : "PartyID1 | PartyID2",
    "totag" : "TagID1 | TagID2",
    "msgtype" : "template_card",
    "agentid" : 1,
    "template_card" : {
        "card_type" : "vote_interaction",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信"
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "task_id": "task_id",
        "checkbox": {
            "question_key": "question_key1",
            "option_list": [
                {
                    "id": "option_id1",
                    "text": "选择题选项1",
                    "is_checked": true
                },
                {
                    "id": "option_id2",
                    "text": "选择题选项2",
                    "is_checked": false
                }
            ],
            "mode": 1
        },
        "submit_button": {
            "text": "提交",
            "key": "key"
        }
    },
    "enable_id_trans": 0,
    "enable_duplicate_check": 0,
    "duplicate_check_interval": 1800
}
    参数说明：


##### 多项选择型

{
    "touser" : "UserID1|UserID2|UserID3",
    "toparty" : "PartyID1 | PartyID2",
    "totag" : "TagID1 | TagID2",
    "msgtype" : "template_card",
    "agentid" : 1,
    "template_card" : {
        "card_type" : "multiple_interaction",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信"
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "task_id": "task_id",
        "select_list": [
            {
                "question_key": "question_key1",
                "title": "选择器标签1",
                "selected_id": "selection_id1",
                "option_list": [
                    {
                        "id": "selection_id1",
                        "text": "选择器选项1"
                    },
                    {
                        "id": "selection_id2",
                        "text": "选择器选项2"
                    }
                ]
            },
            {
                "question_key": "question_key2",
                "title": "选择器标签2",
                "selected_id": "selection_id3",
                "option_list": [
                    {
                        "id": "selection_id3",
                        "text": "选择器选项3"
                    },
                    {
                        "id": "selection_id4",
                        "text": "选择器选项4"
                    }
                ]
            }
        ],
        "submit_button": {
            "text": "提交",
            "key": "key"
        }
    },
    "enable_id_trans": 0,
    "enable_duplicate_check": 0,
    "duplicate_check_interval": 1800
}
    参数说明：


### 附录


#### 支持的markdown语法

目前应用消息中支持的markdown语法是如下的子集：

- 标题 （支持1至6级标题，注意#与文字中间要有空格） 
      # 标题一
## 标题二
### 标题三
#### 标题四
##### 标题五
###### 标题六
# 标题一
## 标题二
### 标题三
#### 标题四
##### 标题五
###### 标题六
    加粗
      **bold**
    链接
      [这是一个链接](http://work.weixin.qq.com/api/doc)
    行内代码段（暂不支持跨行）
      `code`
    引用
      > 引用文字
    字体颜色(只支持3种内置颜色)
      <font color="info">绿色</font>
<font color="comment">灰色</font>
<font color="warning">橙红色</font>
    id转译说明1.支持的消息类型和对应的字段

```json
# 标题一
## 标题二
### 标题三
#### 标题四
##### 标题五
###### 标题六
```

- 加粗
      **bold**
```json
**bold**
```

- 链接
      [这是一个链接](http://work.weixin.qq.com/api/doc)
```json
[这是一个链接](http://work.weixin.qq.com/api/doc)
```

- 行内代码段（暂不支持跨行）
      `code`
```json
`code`
```

- 引用
      > 引用文字
```json
> 引用文字
```

- 字体颜色(只支持3种内置颜色)
      <font color="info">绿色</font>
<font color="comment">灰色</font>
<font color="warning">橙红色</font>
```json
<font color="info">绿色</font>
<font color="comment">灰色</font>
<font color="warning">橙红色</font>
```


#### id转译说明


| 消息类型 | 支持字段 |
| --- | --- |
| 文本（text） | content |
| 文本卡片（textcard） | title、description |
| 图文（news） | title、description |
| 图文（mpnews） | title、digest、content |
| 小程序通知（miniprogram_notice） | title、description、content_item.value |
| 模版消息（template_msg） | value |
| 模板卡片消息（template_card） | source.desc、main_title.title、main_title.desc、sub_title_text、horizontal_content_list.value |

2.id转译模版语法

$departmentName=DEPARTMENT_ID$
$userName=USERID$
$userAlias=USERID$
$userAliasOrName=USERID$
    其中 DEPARTMENT_ID 是数字类型的部门id，USERID 是成员账号。譬如，将$departmentName=1$替换成部门id为1对应的部门名，如“企业微信部”；将$userName=lisi007$替换成userid为lisi007对应的用户姓名，如“李四”；将$userAlias=lisi007$替换成userid为lisi007对应的用户别名，如“lisi”；将$userAliasOrName=lisi007$替换成userid为lisi007对应的用户别名或姓名，别名优先级高于姓名，如"lisi"。

```json
$departmentName=DEPARTMENT_ID$
$userName=USERID$
$userAlias=USERID$
$userAliasOrName=USERID$
```

在企业授权了会话内容存档接口权限时，也支持转译消息内容和群聊名称，语法如下：

$chatName=CHATID$
$msgContent=MSGID/SECRET-KEY$
$externalUserName=EXTERNAL_USERID$
    CHATID是群聊ID，MSGID是消息ID，SECRET-KEY是获取会话记录接口返回的这条消息对应的 encrypted_secretkey 字段进行解密得到，参考 encrypt_secretkey 解密方式。EXTERNAL_USERID是客户的ID。若是企业客户填：externalUserId若是客户群的外部成员填：chatid/externalUserId，例如：wraaaabbbb/wmccccdddd。若当前企业为K12教育行业，externalUserId在家校通讯录中，则会转译为家长或者学生的名称。譬如，将$chatName=xxxxx$替换成群聊ID为xxxxx对应的群聊名称；将$msgContent=xxxxx/yyyyyy$替换成消息ID为xxxxx对应的消息内容，其中获取会话记录接口返回的这条消息对应的 encrypted_secretkey 字段进行解密得到的密钥为yyyyyy；将 $externalUserName=xxxxx$ 替换成客户ID为 xxxxx 对应的客户名称；如果当前企业是K12教育行业，客户ID为家长或者学生的externalUserId，则展示家长或者学生的名称，若非家长或者学生，则展示“非学校家长”。若员工对客户有设置备注名，则展示“备注名(名称）”；若名称和备注名一致时仅展示“备注名”；多个员工给客户有备注的，展示最早添加的员工给客户的备注。

```json
$chatName=CHATID$
$msgContent=MSGID/SECRET-KEY$
$externalUserName=EXTERNAL_USERID$
```

- CHATID是群聊ID，MSGID是消息ID，SECRET-KEY是获取会话记录接口返回的这条消息对应的 encrypted_secretkey 字段进行解密得到，参考 encrypt_secretkey 解密方式。
- EXTERNAL_USERID是客户的ID。若是企业客户填：externalUserId若是客户群的外部成员填：chatid/externalUserId，例如：wraaaabbbb/wmccccdddd。若当前企业为K12教育行业，externalUserId在家校通讯录中，则会转译为家长或者学生的名称。
群聊ID支持转译内部群和外部群的名称；不包括单聊；对于无名称的企业内部群聊，展示为未命名内部群；对于无名称的企业客户群聊，展示为未命名客户群；对于非企业客户群，展示为非企业客户群。

消息ID


| 消息类型 | 对应转译结果 |
| --- | --- |
| 文本消息 | 文本消息的内容 |
| 图片消息 | [图片] |
| Markdown消息 | Markdown消息的文本内容 |
| 图文混排消息 | 文本的内容，涉及到其他类型的部分用消息类型名称代替，如[图片] |
| 其他 | 展示对应的消息类型名称，如[小程序]、[红包消息] |


---

# 更新模版卡片消息

> 最后更新：2025/07/24

目录

- 接口定义
- 更新按钮为不可点击状态
- 更新为新的卡片
-       文本通知型
-       图文展示型
-       按钮交互型
-       投票选择型
-       多项选择型

### 接口定义

应用可以发送模板卡片消息，发送之后可再通过接口更新可回调的用户任务卡片消息的替换文案信息（仅原卡片为 按钮交互型、投票选择型、多项选择型的卡片以及填写了action_menu字段的文本通知型、图文展示型可以调用本接口更新）。

请注意，当应用调用发送模版卡片消息后，接口会返回一个response_code，通过response_code用户可以调用本接口一次。后续如果有用户点击任务卡片，回调接口也会带上response_code，开发者通过该code也可以调用本接口一次，注意response_code的有效期是72小时，超过72小时后将无法使用。 请求方式：POST（HTTPS）请求地址： https://qyapi.weixin.qq.com/cgi-bin/message/update_template_card?access_token=ACCESS_TOKEN

参数说明：


| 参数 | 是否必须 | 说明 |
| --- | --- | --- |
| access_token | 是 | 调用接口凭证 |

请求示例：


### 更新按钮为不可点击状态

仅原卡片为 按钮交互型、投票选择型、多项选择型的卡片可以更新按钮，可以将按钮更新为不可点击状态，并且自定义文案

```json
{
    "userids" : ["userid1","userid2"],
    "partyids" : [2,3],
    "tagids" : [44,55],
    "atall" : 0,
    "agentid" : 1,
    "response_code": "response_code",
    "button":{
        "replace_name": "replace_name"
    }
}
```


### 更新为新的卡片

可回调的卡片可以更新成任何一种模板卡片


#### 文本通知型

```json
{
    "userids" : ["userid1","userid2"],
    "partyids" : [2,3],
    "agentid" : 1,
    "response_code": "response_code",
    "enable_id_trans": 1,
    "template_card" : {
        "card_type" : "text_notice",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信",
            "desc_color": 1
        },
        "action_menu": {
            "desc": "卡片副交互辅助文本说明",
            "action_list": [
                {"text": "接受推送", "key": "A"},
                {"text": "不再推送", "key": "B"}
            ]
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "quote_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的引用样式",
            "quote_text": "企业微信真好用呀真好用"
        },
        "emphasis_content": {
            "title": "100",
            "desc": "核心数据"
        },
        "sub_title_text" : "下载企业微信还能抢红包！",
        "horizontal_content_list" : [
            {
                "keyname": "邀请人",
                "value": "张三"
            },
            {
                "type": 1,
                "keyname": "企业微信官网",
                "value": "点击访问",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "keyname": "企业微信下载",
                "value": "企业微信.apk",
                "media_id": "文件的media_id"
            },
            {
                "type": 3,
                "keyname": "员工信息",
                "value": "点击查看",
                "userid": "zhangsan"
            }
        ],
        "jump_list" : [
            {
                "type": 1,
                "title": "企业微信官网",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "title": "跳转小程序",
                "appid": "小程序的appid",
                "pagepath": "/index.html"
            }
        ],
        "card_action": {
            "type": 2,
            "url": "https://work.weixin.qq.com",
            "appid": "小程序的appid",
            "pagepath": "/index.html"
        }
    }
}
```


#### 图文展示型

{
    "userids" : ["userid1","userid2"],
    "partyids" : [2,3],
    "agentid" : 1,
    "response_code": "response_code",
    "enable_id_trans": 1,
    "template_card" : {
        "card_type" : "news_notice",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信",
            "desc_color": 1
        },
        "action_menu": {
            "desc": "卡片副交互辅助文本说明",
            "action_list": [
                {"text": "接受推送", "key": "A"},
                {"text": "不再推送", "key": "B"}
            ]
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "quote_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的引用样式",
            "quote_text": "企业微信真好用呀真好用"
        },
        "image_text_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的左图右文样式",
            "desc": "企业微信真好用呀真好用",
            "image_url": "https://img.iplaysoft.com/wp-content/uploads/2019/free-images/free_stock_photo_2x.jpg"
        },
        "card_image": {
            "url": "图片的url",
            "aspect_ratio": 1.3
        },
        "vertical_content_list": [
            {
                "title": "惊喜红包等你来拿",
                "desc": "下载企业微信还能抢红包！"
            }
        ],
        "horizontal_content_list" : [
            {
                "keyname": "邀请人",
                "value": "张三"
            },
            {
                "type": 1,
                "keyname": "企业微信官网",
                "value": "点击访问",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "keyname": "企业微信下载",
                "value": "企业微信.apk",
                "media_id": "文件的media_id"
            },
            {
                "type": 3,
                "keyname": "员工信息",
                "value": "点击查看",
                "userid": "zhangsan"
            }
        ],
        "jump_list" : [
            {
                "type": 1,
                "title": "企业微信官网",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "title": "跳转小程序",
                "appid": "小程序的appid",
                "pagepath": "/index.html"
            }
        ],
        "card_action": {
            "type": 2,
            "url": "https://work.weixin.qq.com",
            "appid": "小程序的appid",
            "pagepath": "/index.html"
        }
    }
}
    参数说明：


#### 按钮交互型

{
    "userids" : ["userid1","userid2"],
    "partyids" : [2,3],
    "agentid" : 1,
    "response_code": "response_code",
    "enable_id_trans": 1,
    "template_card" : {
        "card_type" : "button_interaction",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信",
            "desc_color": 1
        },
        "action_menu": {
            "desc": "卡片副交互辅助文本说明",
            "action_list": [
                {"text": "接受推送", "key": "A"},
                {"text": "不再推送", "key": "B"}
            ]
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "quote_area": {
            "type": 1,
            "url": "https://work.weixin.qq.com",
            "title": "企业微信的引用样式",
            "quote_text": "企业微信真好用呀真好用"
        },
        "sub_title_text" : "下载企业微信还能抢红包！",
        "horizontal_content_list" : [
            {
                "keyname": "邀请人",
                "value": "张三"
            },
            {
                "type": 1,
                "keyname": "企业微信官网",
                "value": "点击访问",
                "url": "https://work.weixin.qq.com"
            },
            {
                "type": 2,
                "keyname": "企业微信下载",
                "value": "企业微信.apk",
                "media_id": "文件的media_id"
            },
            {
                "type": 3,
                "keyname": "员工信息",
                "value": "点击查看",
                "userid": "zhangsan"
            }
        ],
        "card_action": {
            "type": 2,
            "url": "https://work.weixin.qq.com",
            "appid": "小程序的appid",
            "pagepath": "/index.html"
        },
        "button_selection": {
            "question_key": "btn_question_key1",
            "title": "企业微信评分",
            "option_list": [
                {
                    "id": "btn_selection_id1",
                    "text": "100分"
                },
                {
                    "id": "btn_selection_id2",
                    "text": "101分"
                }
            ],
            "selected_id": "btn_selection_id1"
        },
        "button_list": [
            {
                "text": "按钮1",
                "style": 1,
                "key": "button_key_1"
            },
            {
                "text": "按钮2",
                "style": 2,
                "key": "button_key_2"
            }
        ],
        "replace_text": "已提交"
    }

}
    参数说明：

备注：按钮样式


#### 投票选择型

{
    "userids" : ["userid1","userid2"],
    "partyids" : [2,3],
    "agentid" : 1,
    "response_code": "response_code",
    "enable_id_trans": 1,
    "template_card" : {
        "card_type" : "vote_interaction",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信"
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "checkbox": {
            "question_key": "question_key1",
            "option_list": [
                {
                    "id": "option_id1",
                    "text": "选择题选项1",
                    "is_checked": true
                },
                {
                    "id": "option_id2",
                    "text": "选择题选项2",
                    "is_checked": false
                }
            ],
            "disable": false,
            "mode": 1
        },
        "submit_button": {
            "text": "提交",
            "key": "key"
        },
        "replace_text": "已提交"
    }
}
    参数说明：


#### 多项选择型

{
    "userids" : ["userid1","userid2"],
    "partyids" : [2,3],
    "tagids" : [44,55],
    "atall" : 0,
    "agentid" : 1,
    "response_code": "response_code",
    "enable_id_trans": 1,
    "template_card" : {
        "card_type" : "multiple_interaction",
        "source" : {
            "icon_url": "图片的url",
            "desc": "企业微信"
        },
        "main_title" : {
            "title" : "欢迎使用企业微信",
            "desc" : "您的好友正在邀请您加入企业微信"
        },
        "select_list": [
            {
                "question_key": "question_key1",
                "title": "选择器标签1",
                "selected_id": "selection_id1",
                "disable": false,
                "option_list": [
                    {
                        "id": "selection_id1",
                        "text": "选择器选项1"
                    },
                    {
                        "id": "selection_id2",
                        "text": "选择器选项2"
                    }
                ]
            },
            {
                "question_key": "question_key2",
                "title": "选择器标签2",
                "selected_id": "selection_id3",
                "disable": false,
                "option_list": [
                    {
                        "id": "selection_id3",
                        "text": "选择器选项3"
                    },
                    {
                        "id": "selection_id4",
                        "text": "选择器选项4"
                    }
                ]
            }
        ],
        "submit_button": {
            "text": "提交",
            "key": "key"
        },
        "replace_text": "已提交"
    }
}
    参数说明：

返回示例：

```json
{
  "errcode" : 0,
  "errmsg" : "ok",
  "invaliduser" : ["userid1","userid2"]
}
```


---

# 撤回应用消息

> 最后更新：2021/08/11

请求方式：POST（HTTPS）请求地址：https://qyapi.weixin.qq.com/cgi-bin/message/recall?access_token=ACCESS_TOKEN本接口可以撤回24小时内通过发送应用消息接口推送的消息，仅可撤回企业微信端的数据，微信插件端的数据不支持撤回。请求包体：

```json
{
	"msgid": "vcT8gGc-7dFb4bxT35ONjBDz901sLlXPZw1DAMC_Gc26qRpK-AK5sTJkkb0128t"
}
```


| 参数 | 必须 | 说明 |
| --- | --- | --- |
| access_token | 是 | 调用接口凭证。获取方法查看“获取access_token” |
| msgid | 是 | 消息ID。从应用发送消息接口处获得。 |

返回结果：

```json
{
   "errcode": 0,
   "errmsg": "ok"
}
```


| 参数 | 说明 |
| --- | --- |
| errcode | 返回码 |
| errmsg | 对返回码的文本描述内容 |


---

# 接收消息与事件 概述

> 最后更新：2024/07/22

目录

- 关于接收消息
- 开启接收消息
-       设置接收消息的参数
-       设置接收消息的格式
-       验证URL有效性
- 使用接收消息
-       接收消息协议的说明
-       接收消息请求的说明
- 获取企业微信服务器的ip段

### 关于接收消息

为了能够让自建应用和企业微信进行双向通信，企业可以在应用的管理后台开启接收消息模式。开启接收消息模式的企业，需要提供可用的接收消息服务器URL（建议使用https）。开启接收消息模式后，用户在应用里发送的消息会推送给企业后台。此外，还可配置地理位置上报等事件消息，当事件触发时企业微信会把相应的数据推送到企业的后台。企业后台接收到消息后，可在回复该消息请求的响应包里带上新消息，企业微信会将该被动回复消息推送给用户。


### 开启接收消息


#### 设置接收消息的参数

在企业的管理端后台，进入需要设置接收消息的目标应用，点击“接收消息”的“设置API接收”按钮，进入配置页面。配置回调参数以及选择需要接收的事件类型。

要求填写应用的URL、Token、EncodingAESKey三个参数

- URL是企业后台接收企业微信推送请求的访问协议和地址，支持http或https协议（为了提高安全性，建议使用https）。
- Token可由企业任意填写，用于生成签名。
- EncodingAESKey用于消息体的加密。
这三个参数的用处在 加解密方案说明 章节会介绍，此处不用细究。<!--


#### 设置接收消息的格式

系统回调支持xml和json两种数据消息回调，企业可通过企业后台管理端进行设定，如果不设置默认回调xml数据格式。-->


#### 验证URL有效性

当点击“保存”提交以上信息时，企业微信会发送一条验证消息到填写的URL，发送方法为GET。企业的接收消息服务器接收到验证请求后，需要作出正确的响应才能通过URL验证。

假设接收消息地址设置为：https://api.3dept.com/，企业微信将向该地址发送如下验证请求：

请求方式：GET请求地址：https://api.3dept.com/?msg_signature=ASDFQWEXZCVAQFASDFASDFSS&timestamp=13500001234&nonce=123412323&echostr=ENCRYPT_STR参数说明


| 参数 | 必须 | 说明 |
| --- | --- | --- |
| msg_signature | 是 | 企业微信加密签名，msg_signature结合了企业填写的token、请求中的timestamp、nonce参数、加密的消息体 |
| timestamp | 是 | 时间戳 |
| nonce | 是 | 随机数 |
| echostr | 是 | 加密的字符串。需要解密得到消息内容明文，解密后有random、msg_len、msg、receiveid四个字段，其中msg即为消息内容明文 |

企业后台收到请求后，需要做如下操作：

- 对收到的请求做Urldecode处理
- 通过参数msg_signature对请求进行校验，确认调用者的合法性。
- 解密echostr参数得到消息内容(即msg字段)
- 在1秒内响应GET请求，响应内容为上一步得到的明文消息内容(不能加引号，不能带bom头，不能带换行符)
以上2~3步骤可以直接使用验证URL函数一步到位。之后接入验证生效，接收消息开启成功。


### 使用接收消息

开启接收消息模式后，企业微信会将消息发送给企业填写的URL，企业后台需要做正确的响应。


#### 接收消息协议的说明

- 企业微信服务器在五秒内收不到响应会断掉连接，并且重新发起请求，总共重试三次。如果企业在调试中，发现成员无法收到被动回复的消息，可以检查是否消息处理超时。
- 当接收成功后，http头部返回200表示接收ok，其他错误码企业微信后台会一律当做失败并发起重试。
- 关于重试的消息排重，有msgid的消息推荐使用msgid排重。事件类型消息推荐使用FromUserName + CreateTime排重。
- 假如企业无法保证在五秒内处理并回复，或者不想回复任何内容，可以直接返回200（即以空串为返回包）。企业后续可以使用主动发消息接口进行异步回复。

#### 接收消息请求的说明

假设企业的接收消息的URL设置为https://api.3dept.com。请求方式：POST请求地址 ：https://api.3dept.com/?msg_signature=ASDFQWEXZCVAQFASDFASDFSS&timestamp=13500001234&nonce=123412323

接收数据格式 ：xml数据格式：

<xml> 
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <AgentID><![CDATA[toAgentID]]></AgentID>
   <Encrypt><![CDATA[msg_encrypt]]></Encrypt>
</xml>
    json数据格式：

```json
<xml> 
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <AgentID><![CDATA[toAgentID]]></AgentID>
   <Encrypt><![CDATA[msg_encrypt]]></Encrypt>
</xml>
```

{
	"ToUserName": "toUser",
	"AgentID": "toAgentID",
	"Encrypt": "msg_encrypt"
}
    -->参数说明

```json
{
	"ToUserName": "toUser",
	"AgentID": "toAgentID",
	"Encrypt": "msg_encrypt"
}
```


| 参数 | 说明 |
| --- | --- |
| ToUserName | 企业微信的CorpID，当为第三方套件回调事件时，CorpID的内容为suiteid |
| AgentID | 接收的应用id，可在应用的设置页面获取 |
| Encrypt | 消息结构体加密后的字符串 |

企业收到消息后，需要作如下处理：

- 对msg_signature进行校验
- 解密Encrypt，得到明文的消息结构体（消息结构体后面章节会详说）
- 如果需要被动回复消息，构造被动响应包
- 正确响应本次请求
以上1~2步骤可以直接使用解密函数一步到位。3步骤其实包含加密被动回复消息、生成新签名、构造被动响应包三个步骤，可以直接使用加密函数一步到位。

被动响应包的数据格式：xml格式：

<xml>
   <Encrypt><![CDATA[msg_encrypt]]></Encrypt>
   <MsgSignature><![CDATA[msg_signature]]></MsgSignature>
   <TimeStamp>timestamp</TimeStamp>
   <Nonce><![CDATA[nonce]]></Nonce>
</xml>
    json格式：

```json
<xml>
   <Encrypt><![CDATA[msg_encrypt]]></Encrypt>
   <MsgSignature><![CDATA[msg_signature]]></MsgSignature>
   <TimeStamp>timestamp</TimeStamp>
   <Nonce><![CDATA[nonce]]></Nonce>
</xml>
```

{
	"Encrypt": "msg_encrypt",
	"MsgSignature": "msg_signature",
	"TimeStamp": "timestamp",
	"Nonce": "nonce"
}
    -->参数说明

```json
{
	"Encrypt": "msg_encrypt",
	"MsgSignature": "msg_signature",
	"TimeStamp": "timestamp",
	"Nonce": "nonce"
}
```


| 参数 | 是否必须 | 说明 |
| --- | --- | --- |
| Encrypt | 是 | 经过加密的消息结构体 |
| MsgSignature | 是 | 消息签名 |
| TimeStamp | 是 | 时间戳 |
| Nonce | 是 | 随机数，由企业自行生成 |


### 获取企业微信服务器的ip段

企业微信在回调企业指定的URL时，是通过特定的IP发送出去的。如果企业需要做防火墙配置，那么可以通过这个接口获取到所有相关的IP段。

请求方式：GET（HTTPS）请求地址： https://qyapi.weixin.qq.com/cgi-bin/getcallbackip?access_token=ACCESS_TOKEN

参数说明：

权限说明：

无限定。

返回结果：

```json
{
	"errcode": 0,
	"errmsg": "ok",
	"ip_list": ["101.226.103.*", "101.226.62.*"]
}
```


---

# 接收消息与事件 消息格式

> 最后更新：2019/10/23

目录

- 文本消息
- 图片消息
- 语音消息
- 视频消息
- 位置消息
- 链接消息
开启接收消息模式后，企业成员在企业微信应用里发送消息时，企业微信会将消息同步到企业应用的后台。如何接收消息已经在使用接收消息说明，本小节是对普通消息结构体的说明。消息类型支持：文本、图片、语音、视频、位置以及链接信息。注：以下出现的xml包仅是接收的消息包中的Encrypt参数解密后的内容说明


#### 文本消息

消息示例：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName> 
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[text]]></MsgType>
   <Content><![CDATA[this is a test]]></Content>
   <MsgId>1234567890123456</MsgId>
   <AgentID>1</AgentID>
</xml>
    参数说明：

```json
<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName> 
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[text]]></MsgType>
   <Content><![CDATA[this is a test]]></Content>
   <MsgId>1234567890123456</MsgId>
   <AgentID>1</AgentID>
</xml>
```


| 参数 | 说明 |
| --- | --- |
| ToUserName | 企业微信CorpID |
| FromUserName | 成员UserID |
| CreateTime | 消息创建时间（整型） |
| MsgType | 消息类型，此时固定为：text |
| Content | 文本消息内容 |
| MsgId | 消息id，64位整型 |
| AgentID | 企业应用的id，整型。可在应用的设置页面查看 |


#### 图片消息

消息示例：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[image]]></MsgType>
   <PicUrl><![CDATA[this is a url]]></PicUrl>
   <MediaId><![CDATA[media_id]]></MediaId>
   <MsgId>1234567890123456</MsgId>
   <AgentID>1</AgentID>
</xml>

    参数说明：


#### 语音消息

消息示例：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1357290913</CreateTime>
   <MsgType><![CDATA[voice]]></MsgType>
   <MediaId><![CDATA[media_id]]></MediaId>
   <Format><![CDATA[Format]]></Format>
   <MsgId>1234567890123456</MsgId>
   <AgentID>1</AgentID>
</xml>


    参数说明：


#### 视频消息

消息示例：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1357290913</CreateTime>
   <MsgType><![CDATA[video]]></MsgType>
   <MediaId><![CDATA[media_id]]></MediaId>
   <ThumbMediaId><![CDATA[thumb_media_id]]></ThumbMediaId>
   <MsgId>1234567890123456</MsgId>
   <AgentID>1</AgentID>
</xml>



    参数说明：


#### 位置消息

消息示例：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1351776360</CreateTime>
   <MsgType><![CDATA[location]]></MsgType>
   <Location_X>23.134</Location_X>
   <Location_Y>113.358</Location_Y>
   <Scale>20</Scale>
   <Label><![CDATA[位置信息]]></Label>
   <MsgId>1234567890123456</MsgId>
   <AgentID>1</AgentID>
   <AppType><![CDATA[wxwork]]></AppType>
</xml>

    参数说明：


#### 链接消息

消息示例：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName> 
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[link]]></MsgType>
   <Title><![CDATA[this is a title！]]></Title>
   <Description><![CDATA[this is a description！]]></Description>
   <Url><![CDATA[URL]]></Url>
   <PicUrl><![CDATA[this is a url]]></PicUrl>
   <MsgId>1234567890123456</MsgId>
   <AgentID>1</AgentID>
</xml>


    参数说明：


---

# 接收消息与事件 事件格式

> 最后更新：2025/11/26

目录

- 成员关注及取消关注事件
- 进入应用
- 上报地理位置
- 异步任务完成事件推送
- 通讯录变更事件
-       新增成员事件
-       更新成员事件
-       删除成员事件
-       新增部门事件
-       更新部门事件
-       删除部门事件
-       标签成员变更事件
- 菜单事件
-       点击菜单拉取消息的事件推送
-       点击菜单跳转链接的事件推送
-       点击菜单跳转小程序的事件推送
-       扫码推事件的事件推送
-       扫码推事件且弹出“消息接收中”提示框的事件推送
-       弹出系统拍照发图的事件推送
-       弹出拍照或者相册发图的事件推送
-       弹出微信相册发图器的事件推送
-       弹出地理位置选择器的事件推送
- 审批状态通知事件
- 企业互联共享应用事件回调
- 上下游共享应用事件回调
- 模板卡片事件推送
- 通用模板卡片右上角菜单事件推送
- 长期未使用应用停用预警事件
- 长期未使用应用临时停用事件
- 长期未使用应用重新启用事件
- 应用低活跃预警事件
- 低活跃应用事件
- 低活跃应用活跃恢复事件
开启接收消息模式后，可以配置接收事件消息。当企业成员通过企业微信APP或微信插件（原企业号）触发进入应用、上报地理位置、点击菜单等事件时，企业微信会将这些事件消息发送给企业后台。如何接收消息已经在使用接收消息说明，本小节是对事件消息结构体的说明。

注：以下出现的数据包仅是接收的消息包中的Encrypt参数解密后的内容说明


#### 成员关注及取消关注事件

- 成员已经加入企业，管理员添加成员到应用可见范围(或移除可见范围)时
- 成员已经在应用可见范围，成员加入(或退出)企业时、或者被禁用
事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[UserID]]></FromUserName>
	<CreateTime>1348831860</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[subscribe]]></Event>
	<AgentID>1</AgentID>
</xml>
    参数说明：

```json
<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[UserID]]></FromUserName>
	<CreateTime>1348831860</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[subscribe]]></Event>
	<AgentID>1</AgentID>
</xml>
```


| 参数 | 说明 |
| --- | --- |
| ToUserName | 企业微信CorpID |
| FromUserName | 成员UserID |
| CreateTime | 消息创建时间（整型） |
| MsgType | 消息类型，此时固定为：event |
| Event | 事件类型，subscribe(关注)、unsubscribe(取消关注) |
| EventKey | 事件KEY值，此事件该值为空 |
| AgentID | 企业应用的id，整型。可在应用的设置页面查看 |


#### 进入应用

事件示例：

<xml><ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>1408091189</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[enter_agent]]></Event>
<EventKey><![CDATA[]]></EventKey>
<AgentID>1</AgentID>
</xml>


    参数说明：

```json
<xml><ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>1408091189</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[enter_agent]]></Event>
<EventKey><![CDATA[]]></EventKey>
<AgentID>1</AgentID>
</xml>
```


#### 上报地理位置

事件示例：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[FromUser]]></FromUserName>
   <CreateTime>123456789</CreateTime>
   <MsgType><![CDATA[event]]></MsgType>
   <Event><![CDATA[LOCATION]]></Event>
   <Latitude>23.104</Latitude>
   <Longitude>113.320</Longitude>
   <Precision>65.000</Precision>
   <AgentID>1</AgentID>
   <AppType><![CDATA[wxwork]]></AppType>
</xml>
    参数说明：

```json
<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[FromUser]]></FromUserName>
   <CreateTime>123456789</CreateTime>
   <MsgType><![CDATA[event]]></MsgType>
   <Event><![CDATA[LOCATION]]></Event>
   <Latitude>23.104</Latitude>
   <Longitude>113.320</Longitude>
   <Precision>65.000</Precision>
   <AgentID>1</AgentID>
   <AppType><![CDATA[wxwork]]></AppType>
</xml>
```


#### 异步任务完成事件推送

事件示例：

<xml><ToUserName><![CDATA[wwddddccc7775555aaa]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>1425284517</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[batch_job_result]]></Event>
<BatchJob><JobId><![CDATA[S0MrnndvRG5fadSlLwiBqiDDbM143UqTmKP3152FZk4]]></JobId>
<JobType><![CDATA[sync_user]]></JobType>
<ErrCode>0</ErrCode>
<ErrMsg><![CDATA[ok]]></ErrMsg>
</BatchJob>
</xml>

     

```json
<xml><ToUserName><![CDATA[wwddddccc7775555aaa]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>1425284517</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[batch_job_result]]></Event>
<BatchJob><JobId><![CDATA[S0MrnndvRG5fadSlLwiBqiDDbM143UqTmKP3152FZk4]]></JobId>
<JobType><![CDATA[sync_user]]></JobType>
<ErrCode>0</ErrCode>
<ErrMsg><![CDATA[ok]]></ErrMsg>
</BatchJob>
</xml>
```

参数说明：


#### 通讯录变更事件


##### 新增成员事件

企业内部开发参考新增成员事件，第三方参考新增成员事件


##### 更新成员事件

企业内部开发参考更新成员事件，第三方参考更新成员事件


##### 删除成员事件

企业内部开发参考删除成员事件，第三方参考删除成员事件


##### 新增部门事件

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[sys]]></FromUserName> 
	<CreateTime>1403610513</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[change_contact]]></Event>
	<ChangeType>create_party</ChangeType>
	<Id>2</Id>
	<Name><![CDATA[张三]]></Name>
	<ParentId>1</ParentId>
	<Order>1</Order>
</xml>
    参数说明：

```json
<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[sys]]></FromUserName> 
	<CreateTime>1403610513</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[change_contact]]></Event>
	<ChangeType>create_party</ChangeType>
	<Id>2</Id>
	<Name><![CDATA[张三]]></Name>
	<ParentId>1</ParentId>
	<Order>1</Order>
</xml>
```


##### 更新部门事件

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[sys]]></FromUserName> 
	<CreateTime>1403610513</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[change_contact]]></Event>
	<ChangeType>update_party</ChangeType>
	<Id>2</Id>
	<Name><![CDATA[张三]]></Name>
	<ParentId>1</ParentId>
</xml>
    参数说明：


##### 删除部门事件

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[sys]]></FromUserName> 
	<CreateTime>1403610513</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[change_contact]]></Event>
	<ChangeType>delete_party</ChangeType>
	<Id>2</Id>
</xml>
    参数说明：


##### 标签成员变更事件

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[sys]]></FromUserName> 
	<CreateTime>1403610513</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[change_contact]]></Event>
	<ChangeType><![CDATA[update_tag]]></ChangeType>
	<TagId>1</TagId>
	<AddUserItems><![CDATA[zhangsan,lisi]]></AddUserItems>
	<DelUserItems><![CDATA[zhangsan1,lisi1]]></DelUserItems>
	<AddPartyItems><![CDATA[1,2]]></AddPartyItems>
	<DelPartyItems><![CDATA[3,4]]></DelPartyItems>
</xml>
    参数说明：


#### 菜单事件


##### 点击菜单拉取消息的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>123456789</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[click]]></Event>
	<EventKey><![CDATA[EVENTKEY]]></EventKey>
	<AgentID>1</AgentID>
</xml>

    参数说明：

```json
<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>123456789</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[click]]></Event>
	<EventKey><![CDATA[EVENTKEY]]></EventKey>
	<AgentID>1</AgentID>
</xml>
```


##### 点击菜单跳转链接的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>123456789</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[view]]></Event>
	<EventKey><![CDATA[www.qq.com]]></EventKey>
	<AgentID>1</AgentID>
</xml>

    参数说明：


##### 点击菜单跳转小程序的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>123456789</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[view_miniprogram]]></Event>
	<EventKey><![CDATA[index]]></EventKey>
	<AgentID>1</AgentID>
</xml>

    参数说明：


##### 扫码推事件的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>1408090502</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[scancode_push]]></Event>
	<EventKey><![CDATA[6]]></EventKey>
	<ScanCodeInfo><ScanType><![CDATA[qrcode]]></ScanType>
	<ScanResult><![CDATA[1]]></ScanResult>
	</ScanCodeInfo>
	<AgentID>1</AgentID>
</xml>

     

参数说明：


##### 扫码推事件且弹出“消息接收中”提示框的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>1408090606</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[scancode_waitmsg]]></Event>
	<EventKey><![CDATA[6]]></EventKey>
	<ScanCodeInfo><ScanType><![CDATA[qrcode]]></ScanType>
	<ScanResult><![CDATA[2]]></ScanResult>
	</ScanCodeInfo>
	<AgentID>1</AgentID>
</xml>

     

参数说明：


##### 弹出系统拍照发图的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>1408090651</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[pic_sysphoto]]></Event>
	<EventKey><![CDATA[6]]></EventKey>
	<SendPicsInfo><Count>1</Count>
	<PicList><item><PicMd5Sum><![CDATA[1b5f7c23b5bf75682a53e7b6d163e185]]></PicMd5Sum>
	</item>
	</PicList>
	</SendPicsInfo>
	<AgentID>1</AgentID>
</xml>

     

参数说明：


##### 弹出拍照或者相册发图的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>1408090816</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[pic_photo_or_album]]></Event>
	<EventKey><![CDATA[6]]></EventKey>
	<SendPicsInfo><Count>1</Count>
	<PicList><item><PicMd5Sum><![CDATA[5a75aaca956d97be686719218f275c6b]]></PicMd5Sum>
	</item>
	</PicList>
	</SendPicsInfo>
	<AgentID>1</AgentID>
</xml>

    参数说明：


##### 弹出微信相册发图器的事件推送

事件示例：

<xml>
	<ToUserName><![CDATA[toUser]]></ToUserName>
	<FromUserName><![CDATA[FromUser]]></FromUserName>
	<CreateTime>1408090816</CreateTime>
	<MsgType><![CDATA[event]]></MsgType>
	<Event><![CDATA[pic_weixin]]></Event>
	<EventKey><![CDATA[6]]></EventKey>
	<SendPicsInfo><Count>1</Count>
	<PicList><item><PicMd5Sum><![CDATA[5a75aaca956d97be686719218f275c6b]]></PicMd5Sum>
	</item>
	</PicList>
	</SendPicsInfo>
	<AgentID>1</AgentID>
</xml>

    参数说明：


##### 弹出地理位置选择器的事件推送

事件示例：

<xml><ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>1408091189</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[location_select]]></Event>
<EventKey><![CDATA[6]]></EventKey>
<SendLocationInfo><Location_X><![CDATA[23]]></Location_X>
<Location_Y><![CDATA[113]]></Location_Y>
<Scale><![CDATA[15]]></Scale>
<Label><![CDATA[ 广州市海珠区客村艺苑路 106号]]></Label>
<Poiname><![CDATA[]]></Poiname>
</SendLocationInfo>
<AgentID>1</AgentID>
<AppType><![CDATA[wxwork]]></AppType>
</xml>


     

参数说明：


#### 审批状态通知事件

事件示例：

<xml>
 <ToUserName><![CDATA[wwddddccc7775555aaa]]></ToUserName>
  <FromUserName><![CDATA[sys]]></FromUserName>
  <CreateTime>1527838022</CreateTime>
  <MsgType><![CDATA[event]]></MsgType>
  <Event><![CDATA[open_approval_change]]></Event>
  <AgentID>1</AgentID>
  <ApprovalInfo>
    <ThirdNo><![CDATA[201806010001]]></ThirdNo>
    <OpenSpName><![CDATA[付款]]></OpenSpName>
    <OpenTemplateId><![CDATA[1234567890]]></OpenTemplateId>
    <OpenSpStatus>1</OpenSpStatus>
    <ApplyTime>1527837645</ApplyTime>
    <ApplyUserName><![CDATA[xiaoming]]></ApplyUserName>
    <ApplyUserId><![CDATA[1]]></ApplyUserId>
    <ApplyUserParty><![CDATA[产品部]]></ApplyUserParty>
    <ApplyUserImage><![CDATA[http://www.qq.com/xxx.png]]></ApplyUserImage>
    <ApprovalNodes>
      <ApprovalNode>
        <NodeStatus>1</NodeStatus>
        <NodeAttr>1</NodeAttr>
        <NodeType>1</NodeType>
        <Items>
          <Item>
            <ItemName><![CDATA[xiaohong]]></ItemName>
            <ItemUserId><![CDATA[2]]></ItemUserId>
            <ItemImage><![CDATA[http://www.qq.com/xxx.png]]></ItemImage>
            <ItemStatus>1</ItemStatus>
            <ItemSpeech><![CDATA[]]></ItemSpeech>
            <ItemOpTime>0</ItemOpTime>
          </Item>
        </Items>
      </ApprovalNode>
    </ApprovalNodes>
    <NotifyNodes>
      <NotifyNode>
        <ItemName><![CDATA[xiaogang]]></ItemName>
        <ItemUserId><![CDATA[3]]></ItemUserId>
        <ItemImage><![CDATA[http://www.qq.com/xxx.png]]></ItemImage>
      </NotifyNode>
    </NotifyNodes>
    <approverstep>0</approverstep>
  </ApprovalInfo>
</xml>

    参数说明：

```json
<xml>
 <ToUserName><![CDATA[wwddddccc7775555aaa]]></ToUserName>
  <FromUserName><![CDATA[sys]]></FromUserName>
  <CreateTime>1527838022</CreateTime>
  <MsgType><![CDATA[event]]></MsgType>
  <Event><![CDATA[open_approval_change]]></Event>
  <AgentID>1</AgentID>
  <ApprovalInfo>
    <ThirdNo><![CDATA[201806010001]]></ThirdNo>
    <OpenSpName><![CDATA[付款]]></OpenSpName>
    <OpenTemplateId><![CDATA[1234567890]]></OpenTemplateId>
    <OpenSpStatus>1</OpenSpStatus>
    <ApplyTime>1527837645</ApplyTime>
    <ApplyUserName><![CDATA[xiaoming]]></ApplyUserName>
    <ApplyUserId><![CDATA[1]]></ApplyUserId>
    <ApplyUserParty><![CDATA[产品部]]></ApplyUserParty>
    <ApplyUserImage><![CDATA[http://www.qq.com/xxx.png]]></ApplyUserImage>
    <ApprovalNodes>
      <ApprovalNode>
        <NodeStatus>1</NodeStatus>
        <NodeAttr>1</NodeAttr>
        <NodeType>1</NodeType>
        <Items>
          <Item>
            <ItemName><![CDATA[xiaohong]]></ItemName>
            <ItemUserId><![CDATA[2]]></ItemUserId>
            <ItemImage><![CDATA[http://www.qq.com/xxx.png]]></ItemImage>
            <ItemStatus>1</ItemStatus>
            <ItemSpeech><![CDATA[]]></ItemSpeech>
            <ItemOpTime>0</ItemOpTime>
          </Item>
        </Items>
      </ApprovalNode>
    </ApprovalNodes>
    <NotifyNodes>
      <NotifyNode>
        <ItemName><![CDATA[xiaogang]]></ItemName>
        <ItemUserId><![CDATA[3]]></ItemUserId>
        <ItemImage><![CDATA[http://www.qq.com/xxx.png]]></ItemImage>
      </NotifyNode>
    </NotifyNodes>
    <approverstep>0</approverstep>
  </ApprovalInfo>
</xml>
```

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[taskcard_click]]></Event>
<EventKey><![CDATA[key111]]></EventKey>
<TaskId><![CDATA[taskid111]]></TaskId >
<AgentId>1</AgentId>
</xml>

     

```json
<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[taskcard_click]]></Event>
<EventKey><![CDATA[key111]]></EventKey>
<TaskId><![CDATA[taskid111]]></TaskId >
<AgentId>1</AgentId>
</xml>
```

参数说明：

-->


#### 企业互联共享应用事件回调

事件示例：

<xml>
 <ToUserName><![CDATA[wwddddccc7775555aaa]]></ToUserName>
  <FromUserName><![CDATA[sys]]></FromUserName>
  <CreateTime>1527838022</CreateTime>
  <MsgType><![CDATA[event]]></MsgType>
  <Event><![CDATA[share_agent_change]]></Event>
  <AgentID>1</AgentID>
</xml>

     

参数说明：


#### 上下游共享应用事件回调

事件示例：

<xml>
 <ToUserName><![CDATA[wwddddccc7775555aaa]]></ToUserName>
  <FromUserName><![CDATA[sys]]></FromUserName>
  <CreateTime>1527838022</CreateTime>
  <MsgType><![CDATA[event]]></MsgType>
  <Event><![CDATA[share_chain_change]]></Event>
  <AgentID>1</AgentID>
</xml>

     

参数说明：


#### 模板卡片事件推送

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[template_card_event]]></Event>
<EventKey><![CDATA[key111]]></EventKey>
<TaskId><![CDATA[taskid111]]></TaskId>
<CardType><![CDATA[text_notice]]></CardType>
<ResponseCode><![CDATA[ResponseCode]]></ResponseCode>
<AgentID>1</AgentID>
<SelectedItems>
    <SelectedItem>
        <QuestionKey><![CDATA[QuestionKey1]]></QuestionKey>
        <OptionIds>
            <OptionId><![CDATA[OptionId1]]></OptionId>
            <OptionId><![CDATA[OptionId2]]></OptionId>
        </OptionIds>
    </SelectedItem>
    <SelectedItem>
        <QuestionKey><![CDATA[QuestionKey2]]></QuestionKey>
        <OptionIds>
            <OptionId><![CDATA[OptionId3]]></OptionId>
            <OptionId><![CDATA[OptionId4]]></OptionId>
        </OptionIds>
    </SelectedItem>
</SelectedItems>
</xml>

    参数说明：


#### 通用模板卡片右上角菜单事件推送

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[template_card_menu_event]]></Event>
<EventKey><![CDATA[key111]]></EventKey>
<TaskId><![CDATA[taskid111]]></TaskId>
<CardType><![CDATA[text_notice]]></CardType>
<ResponseCode><![CDATA[ResponseCode]]></ResponseCode>
<AgentID>1</AgentID>
</xml>

    参数说明：


#### 长期未使用应用停用预警事件

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[inactive_alert]]></Event>
<AgentID>1</AgentID>
<EffectTime>1764518400</EffectTime>
</xml>

    参数说明：


#### 长期未使用应用临时停用事件

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[close_inactive_agent]]></Event>
<AgentID>1</AgentID>
</xml>

    参数说明：


#### 长期未使用应用重新启用事件

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[reopen_inactive_agent]]></Event>
<AgentID>1</AgentID>
</xml>

    参数说明：


#### 应用低活跃预警事件

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[low_active_alert]]></Event>
<AgentID>1</AgentID>
<EffectTime>1764518400</EffectTime>
</xml>

    参数说明：


#### 低活跃应用事件

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[low_active]]></Event>
<AgentID>1</AgentID>
</xml>

    参数说明：


#### 低活跃应用活跃恢复事件

事件示例：

<xml>
<ToUserName><![CDATA[toUser]]></ToUserName>
<FromUserName><![CDATA[FromUser]]></FromUserName>
<CreateTime>123456789</CreateTime>
<MsgType><![CDATA[event]]></MsgType>
<Event><![CDATA[active_restored]]></Event>
<AgentID>1</AgentID>
</xml>

    参数说明：


---

# 被动回复消息格式

> 最后更新：2024/11/21

目录

-             文本消息
-             图片消息
-             语音消息
-             视频消息
-             图文消息
-             模板卡片更新消息
-                   更新点击用户的按钮文案
-                   更新点击用户的整张卡片
-                         文本通知型
-                         图文展示型
-                         按钮交互型
-                         投票选择型
-                         多项选择型
- 支持被动回复的事件类型
当企业后台收到推送过来的普通消息或事件消息（支持被动回复的事件类型）后，可以在响应里带上被动回复消息如何被动回复消息在使用接收消息已经说明，本小节是对回复消息的结构体的说明

注：以下出现的xml包仅是发送的消息包中的Encrypt参数加密前的内容说明


#### 文本消息

明文XML结构：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName> 
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[text]]></MsgType>
   <Content><![CDATA[this is a test]]></Content>
</xml>

    参数说明：

```json
<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName> 
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[text]]></MsgType>
   <Content><![CDATA[this is a test]]></Content>
</xml>
```


| 参数 | 说明 |
| --- | --- |
| ToUserName | 成员UserID |
| FromUserName | 企业微信CorpID |
| CreateTime | 消息创建时间（整型） |
| MsgType | 消息类型，此时固定为：text |
| Content | 文本消息内容,最长不超过2048个字节，超过将截断 |


#### 图片消息

明文XML结构：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1348831860</CreateTime>
   <MsgType><![CDATA[image]]></MsgType>
   <Image>
       <MediaId><![CDATA[media_id]]></MediaId>
   </Image>
</xml>


    参数说明：


#### 语音消息

明文XML结构：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1357290913</CreateTime>
   <MsgType><![CDATA[voice]]></MsgType>
   <Voice>
       <MediaId><![CDATA[media_id]]></MediaId>
   </Voice>
</xml>



    参数说明：


#### 视频消息

明文XML结构：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1357290913</CreateTime>
   <MsgType><![CDATA[video]]></MsgType>
   <Video>
       <MediaId><![CDATA[media_id]]></MediaId>
       <Title><![CDATA[title]]></Title>
       <Description><![CDATA[description]]></Description>
   </Video>
</xml>

    参数说明：


#### 图文消息

明文XML结构：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>12345678</CreateTime>
   <MsgType><![CDATA[news]]></MsgType>
   <ArticleCount>2</ArticleCount>
   <Articles>
       <item>
           <Title><![CDATA[title1]]></Title> 
           <Description><![CDATA[description1]]></Description>
           <PicUrl><![CDATA[picurl]]></PicUrl>
           <Url><![CDATA[url]]></Url>
       </item>
       <item>
           <Title><![CDATA[title]]></Title>
           <Description><![CDATA[description]]></Description>
           <PicUrl><![CDATA[picurl]]></PicUrl>
           <Url><![CDATA[url]]></Url>
       </item>
   </Articles>
</xml>


    参数说明：

明文xml结构：

<xml>
   <ToUserName><![CDATA[toUser]]></ToUserName>
   <FromUserName><![CDATA[fromUser]]></FromUserName>
   <CreateTime>1357290913</CreateTime>
   <MsgType><![CDATA[update_taskcard]]></MsgType>
   <TaskCard>
       <ReplaceName><![CDATA[ReplaceName]]></ReplaceName>
   </TaskCard>
</xml>

    参数说明：

-->


#### 模板卡片更新消息

当应用收到模板卡片事件推送时，可立即回复，以更新用户（仅限点击者本人）的按钮状态，或者更新用户（仅限点击者本人）的整个消息卡片。


##### 更新点击用户的按钮文案

<xml>
    <ToUserName><![CDATA[toUser]]></ToUserName>
    <FromUserName><![CDATA[fromUser]]></FromUserName>
    <CreateTime>1357290913</CreateTime>
    <MsgType><![CDATA[update_button]]></MsgType>
    <Button>
        <ReplaceName><![CDATA[ReplaceName]]></ReplaceName>
    </Button>
</xml>
    参数说明：

```json
<xml>
    <ToUserName><![CDATA[toUser]]></ToUserName>
    <FromUserName><![CDATA[fromUser]]></FromUserName>
    <CreateTime>1357290913</CreateTime>
    <MsgType><![CDATA[update_button]]></MsgType>
    <Button>
        <ReplaceName><![CDATA[ReplaceName]]></ReplaceName>
    </Button>
</xml>
```


##### 更新点击用户的整张卡片


###### 文本通知型

<xml>
	<ToUserName><![CDATA[ToUserName]]></ToUserName>
	<FromUserName><![CDATA[FromUserName]]></FromUserName>
	<CreateTime>1357290913</CreateTime>
	<MsgType><![CDATA[update_template_card]]></MsgType>
	<TemplateCard>
		<CardType><![CDATA[text_notice]]></CardType>
		<Source>
			<IconUrl><![CDATA[source_url]]></IconUrl>
			<Desc><![CDATA[更新后的卡片]]></Desc>
			<DescColor>2</DescColor>
		</Source>
		<MainTitle>
			<Title><![CDATA[更新后的卡片标题]]></Title>
			<Desc><![CDATA[更新后的卡片副标题]]></Desc>
		</MainTitle>
		<SubTitleText><![CDATA[更新后的卡片二级标题]]></SubTitleText>
        <HorizontalContentList>
			<KeyName><![CDATA[应用名称]]></KeyName>
			<Value><![CDATA[企业微信]]></Value>
		</HorizontalContentList>
		<HorizontalContentList>
			<KeyName><![CDATA[跳转企业微信]]></KeyName>
			<Value><![CDATA[跳转企业微信]]></Value>
			<Type>1</Type>
			<Url><![CDATA[url]]></Url>
		</HorizontalContentList>
		<JumpList>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</JumpList>
        <CardAction>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</CardAction>
        <EmphasisContent>
			<Title><![CDATA[100万]]></Title>
			<Desc><![CDATA[核心数据实例]]></Desc>
		</EmphasisContent>
		<ActionMenu>
			<Desc><![CDATA[您可以使用以下功能]]></Desc>
			<ActionList>
				<Text><![CDATA[您将收到A回调]]></Text>
				<Key><![CDATA[A]]></Key>
			</ActionList>
			<ActionList>
				<Text><![CDATA[您将收到B回调]]></Text>
				<Key><![CDATA[B]]></Key>
			</ActionList>
		</ActionMenu>
		<QuoteArea>
			<Type>1</Type>
			<Url><![CDATA[quote_area_url]]></Url>
			<Title><![CDATA[企业微信]]></Title>
			<QuoteText><![CDATA[企业微信真好用呀]]></QuoteText>
		</QuoteArea>
	</TemplateCard>
</xml>
    参数说明

```json
<xml>
	<ToUserName><![CDATA[ToUserName]]></ToUserName>
	<FromUserName><![CDATA[FromUserName]]></FromUserName>
	<CreateTime>1357290913</CreateTime>
	<MsgType><![CDATA[update_template_card]]></MsgType>
	<TemplateCard>
		<CardType><![CDATA[text_notice]]></CardType>
		<Source>
			<IconUrl><![CDATA[source_url]]></IconUrl>
			<Desc><![CDATA[更新后的卡片]]></Desc>
			<DescColor>2</DescColor>
		</Source>
		<MainTitle>
			<Title><![CDATA[更新后的卡片标题]]></Title>
			<Desc><![CDATA[更新后的卡片副标题]]></Desc>
		</MainTitle>
		<SubTitleText><![CDATA[更新后的卡片二级标题]]></SubTitleText>
        <HorizontalContentList>
			<KeyName><![CDATA[应用名称]]></KeyName>
			<Value><![CDATA[企业微信]]></Value>
		</HorizontalContentList>
		<HorizontalContentList>
			<KeyName><![CDATA[跳转企业微信]]></KeyName>
			<Value><![CDATA[跳转企业微信]]></Value>
			<Type>1</Type>
			<Url><![CDATA[url]]></Url>
		</HorizontalContentList>
		<JumpList>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</JumpList>
        <CardAction>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</CardAction>
        <EmphasisContent>
			<Title><![CDATA[100万]]></Title>
			<Desc><![CDATA[核心数据实例]]></Desc>
		</EmphasisContent>
		<ActionMenu>
			<Desc><![CDATA[您可以使用以下功能]]></Desc>
			<ActionList>
				<Text><![CDATA[您将收到A回调]]></Text>
				<Key><![CDATA[A]]></Key>
			</ActionList>
			<ActionList>
				<Text><![CDATA[您将收到B回调]]></Text>
				<Key><![CDATA[B]]></Key>
			</ActionList>
		</ActionMenu>
		<QuoteArea>
			<Type>1</Type>
			<Url><![CDATA[quote_area_url]]></Url>
			<Title><![CDATA[企业微信]]></Title>
			<QuoteText><![CDATA[企业微信真好用呀]]></QuoteText>
		</QuoteArea>
	</TemplateCard>
</xml>
```


| 参数 | 说明 |  |
| --- | --- | --- |
| ToUserName | 成员UserID |  |
| FromUserName | 企业微信CorpID |  |
| CreateTime | 消息创建时间（整型） |  |
| MsgType | update_template_card |  |
| TemplateCard.CardType | 模板卡片类型，文本通知型填写 "text_notice" |  |
| TemplateCard.Source | 卡片来源样式信息，不需要来源样式可不填写 |  |
| TemplateCard.Source.IconUrl | 来源图片的url |  |
| TemplateCard.Source.Desc | 来源图片的描述 |  |
| TemplateCard.Source.DescColor | 来源文字的颜色，目前支持：0(默认) 灰色，1 黑色，2 红色，3 绿色 |  |
| TemplateCard.MainTitle.Title | 一级标题，文本通知型卡片本字段非必填，但不可本字段和sub_title_text都不填 |  |
| TemplateCard.MainTitle.Desc | 标题辅助信息 |  |
| TemplateCard.SubTitleText | 二级普通文本 |  |
| TemplateCard.HorizontalContentList | 二级标题+文本列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过6 |  |
| TemplateCard.HorizontalContentList.Type | 链接类型，0或不填或错填代表不是链接，1 代表跳转url，2 代表下载附件，3 代表点击跳转成员详情 |  |
| TemplateCard.HorizontalContentList.KeyName | 二级标题，必填 |  |
| TemplateCard.HorizontalContentList.Value | 二级文本，如果HorizontalContentList.Type是2，该字段代表文件名称（要包含文件类型） |  |
| TemplateCard.HorizontalContentList.Url | 链接跳转的url，HorizontalContentList.Type是1时必填 |  |
| TemplateCard.HorizontalContentList.MediaId | 附件的media_id，HorizontalContentList.Type是2时必填 |  |
| TemplateCard.HorizontalContentList.UserId | 成员详情的userid，HorizontalContentList.Type是3时必填 |  |
| TemplateCard.JumpList | 跳转指引样式的列表，该字段可为空数组，但有数据的话需确认对应字段是否必填，列表长度不超过3 |  |
| TemplateCard.JumpList.Type | 跳转链接类型，0或不填或错填代表不是链接，1 代表跳转url，2 代表跳转小程序 |  |
| TemplateCard.JumpList.Title | 跳转链接样式的文案内容，必填 |  |
| TemplateCard.JumpList.Url | 跳转链接的url，JumpList.Type是1时必填 |  |
| TemplateCard.JumpList.AppId | 跳转链接的小程序的appid，JumpList.Type是2时必填 |  |
| TemplateCard.JumpList.PagePath | 跳转链接的小程序的pagepath，JumpList.Type是2时选填 |  |
| TemplateCard.CardAction | 整体卡片的点击跳转事件，必填 |  |
| TemplateCard.CardAction.Type | 跳转事件类型，0或不填或错填代表不是链接，1 代表跳转url，2 代表下载附件 |  |
| TemplateCard.CardAction.Url | 跳转事件的url，CardAction.Type是1时必填 |  |
| TemplateCard.CardAction.AppId | 跳转事件的小程序的appid，CardAction.Type是2时必填 |  |
| TemplateCard.CardAction.PagePath | 跳转事件的小程序的pagepath，CardAction.Type是2时选填 |  |
| TemplateCard.EmphasisContent.Title | 关键数据样式的数据内容 |  |
| TemplateCard.EmphasisContent.Desc | 关键数据样式的数据描述内容 |  |
| TemplateCard.ActionMenu | 卡片右上角更多操作按钮容 |  |
| TemplateCard.ActionMenu.Desc | 更多操作界面的描述 |  |
| TemplateCard.ActionMenu.ActionList | 操作列表，列表长度取值范围为 [1, 10] |  |
| TemplateCard.ActionMenu.ActionList.Text | 操作的描述文案 |  |
| TemplateCard.ActionMenu.ActionList.Key | 操作key值，用户点击后，会产生回调事件将本参数作为EventKey回调，最长支持1024字节，不可重复，必填 |  |
| TemplateCard.QuoteArea | 引用文献样式 |  |
| TemplateCard.QuoteArea.Type | 引用文献样式区域点击事件，0或不填代表没有点击事件，1 代表跳转url，2 代表跳转小程序 |  |
| TemplateCard.QuoteArea.Url | 点击跳转的url，QuoteArea.Type是1时必填 |  |
| TemplateCard.QuoteArea.Appid | 点击跳转的小程序的appid，必须是与当前应用关联的小程序，QuoteArea.Type是2时必填 |  |
| TemplateCard.QuoteArea.PagePath | 点击跳转的小程序的pagepath，QuoteArea.Type是2时选填 |  |
| TemplateCard.QuoteArea.Title | 引用文献样式的标题 |  |
| TemplateCard.QuoteArea.QuoteText | 引用文献样式的引用文案 |  |


###### 图文展示型

<xml>
	<ToUserName><![CDATA[ToUserName]]></ToUserName>
	<FromUserName><![CDATA[FromUserName]]></FromUserName>
	<CreateTime>1357290913</CreateTime>
	<MsgType><![CDATA[update_template_card]]></MsgType>
	<TemplateCard>
		<CardType><![CDATA[news_notice]]></CardType>
		<Source>
			<IconUrl><![CDATA[source_url]]></IconUrl>
			<Desc><![CDATA[更新后的卡片]]></Desc>
			<DescColor>2</DescColor>
		</Source>
		<MainTitle>
			<Title><![CDATA[更新后的卡片标题]]></Title>
			<Desc><![CDATA[更新后的卡片副标题]]></Desc>
		</MainTitle>
        <HorizontalContentList>
			<KeyName><![CDATA[应用名称]]></KeyName>
			<Value><![CDATA[企业微信]]></Value>
		</HorizontalContentList>
		<HorizontalContentList>
			<KeyName><![CDATA[跳转企业微信]]></KeyName>
			<Value><![CDATA[跳转企业微信]]></Value>
			<Type>1</Type>
			<Url><![CDATA[url]]></Url>
		</HorizontalContentList>
		<JumpList>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</JumpList>
        <CardAction>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</CardAction>
        <CardImage>
			<Url><![CDATA[image_url]]></Url>
            <AspectRatio>1.3</AspectRatio>
		</CardImage>
        <VerticalContentList>
			<Title><![CDATA[卡片二级标题1]]></Title>
			<Desc><![CDATA[卡片二级内容1]]></Desc>
		</VerticalContentList>
        <VerticalContentList>
			<Title><![CDATA[卡片二级标题2]]></Title>
			<Desc><![CDATA[卡片二级内容2]]></Desc>
		</VerticalContentList>
		<ActionMenu>
			<Desc><![CDATA[您可以使用以下功能]]></Desc>
			<ActionList>
				<Text><![CDATA[您将收到A回调]]></Text>
				<Key><![CDATA[A]]></Key>
			</ActionList>
			<ActionList>
				<Text><![CDATA[您将收到B回调]]></Text>
				<Key><![CDATA[B]]></Key>
			</ActionList>
		</ActionMenu>
		<QuoteArea>
			<Type>1</Type>
			<Url><![CDATA[quote_area_url]]></Url>
			<Title><![CDATA[企业微信]]></Title>
			<QuoteText><![CDATA[企业微信真好用呀]]></QuoteText>
		</QuoteArea>
		<ImageTextArea>
			<Type>1</Type>
			<Url><![CDATA[image_text_area_url]]></Url>
			<Title><![CDATA[企业微信]]></Title>
			<Desc><![CDATA[企业微信真好用呀]]></Desc>
			<ImageUrl><![CDATA[image_url]]></ImageUrl>
		</ImageTextArea>
	</TemplateCard>
</xml>
    参数说明


###### 按钮交互型

<xml>
	<ToUserName><![CDATA[ToUserName]]></ToUserName>
	<FromUserName><![CDATA[FromUserName]]></FromUserName>
	<CreateTime>1357290913</CreateTime>
	<MsgType><![CDATA[update_template_card]]></MsgType>
	<TemplateCard>
		<CardType><![CDATA[button_interaction]]></CardType>
		<Source>
			<IconUrl><![CDATA[source_url]]></IconUrl>
			<Desc><![CDATA[更新后的卡片]]></Desc>
			<DescColor>2</DescColor>
		</Source>
		<MainTitle>
			<Title><![CDATA[更新后的卡片标题]]></Title>
			<Desc><![CDATA[更新后的卡片副标题]]></Desc>
		</MainTitle>
		<SubTitleText><![CDATA[更新后的卡片二级标题]]></SubTitleText>
        <HorizontalContentList>
			<KeyName><![CDATA[应用名称]]></KeyName>
			<Value><![CDATA[企业微信]]></Value>
		</HorizontalContentList>
		<HorizontalContentList>
			<KeyName><![CDATA[跳转企业微信]]></KeyName>
			<Value><![CDATA[跳转企业微信]]></Value>
			<Type>1</Type>
			<Url><![CDATA[url]]></Url>
		</HorizontalContentList>
		<JumpList>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</JumpList>
        <CardAction>
			<Title><![CDATA[跳转企业微信]]></Title>
			<Type>1</Type>
			<Url><![CDATA[jump_url]]></Url>
		</CardAction>
        <ButtonList>
			<Text><![CDATA[按钮1]]></Text>
			<Style>1</Style>
            <Key><![CDATA[button_key_1]]></Key>
		</ButtonList>
        <ButtonList>
			<Text><![CDATA[按钮2]]></Text>
			<Style>2</Style>
            <Key><![CDATA[button_key_2]]></Key>
		</ButtonList>
		<ReplaceText><![CDATA[已提交]]></ReplaceText>
		<ActionMenu>
			<Desc><![CDATA[您可以使用以下功能]]></Desc>
			<ActionList>
				<Text><![CDATA[您将收到A回调]]></Text>
				<Key><![CDATA[A]]></Key>
			</ActionList>
			<ActionList>
				<Text><![CDATA[您将收到B回调]]></Text>
				<Key><![CDATA[B]]></Key>
			</ActionList>
		</ActionMenu>
		<QuoteArea>
			<Type>1</Type>
			<Url><![CDATA[quote_area_url]]></Url>
			<Title><![CDATA[企业微信]]></Title>
			<QuoteText><![CDATA[企业微信真好用呀]]></QuoteText>
		</QuoteArea>
		<ButtonSelection>
			<QuestionKey><![CDATA[QuestionKey1]]></QuestionKey>
            <Title><![CDATA[下拉式选择器]]></Title>
            <SelectedId><![CDATA[option_id2]]></SelectedId>
            <Disable>false</Disable>
            <OptionList>
                <Id><![CDATA[option_id2]]></Id>
                <Text><![CDATA[选择题选项2]]></Text>
            </OptionList>
            <OptionList>
                <Id><![CDATA[option_id2]]></Id>
                <Text><![CDATA[选择题选项2]]></Text>
            </OptionList>
		</ButtonSelection>
	</TemplateCard>
</xml>
    参数说明


###### 投票选择型

<xml>
	<ToUserName><![CDATA[ToUserName]]></ToUserName>
	<FromUserName><![CDATA[FromUserName]]></FromUserName>
	<CreateTime>1357290913</CreateTime>
	<MsgType><![CDATA[update_template_card]]></MsgType>
	<TemplateCard>
		<CardType><![CDATA[vote_interaction]]></CardType>
		<Source>
			<IconUrl><![CDATA[source_url]]></IconUrl>
			<Desc><![CDATA[更新后的卡片]]></Desc>
		</Source>
		<MainTitle>
			<Title><![CDATA[更新后的卡片标题]]></Title>
			<Desc><![CDATA[更新后的卡片副标题]]></Desc>
		</MainTitle>
        <CheckBox>
			<QuestionKey><![CDATA[QuestionKey1]]></QuestionKey>
			<OptionList>
                <Id><![CDATA[option_id1]]></Id>
                <Text><![CDATA[选择题选项1]]></Text>
                <IsChecked>true</IsChecked>
            </OptionList>
            <OptionList>
                <Id><![CDATA[option_id2]]></Id>
                <Text><![CDATA[选择题选项2]]></Text>
                <IsChecked>false</IsChecked>
            </OptionList>
            <Disable>false</Disable>
            <Mode>1</Mode>
		</CheckBox>
        <SubmitButton>
			<Text><![CDATA[提交]]></Text>
            <Key><![CDATA[Key]]></Key>
		</SubmitButton>
		<ReplaceText><![CDATA[已提交]]></ReplaceText>
	</TemplateCard>
</xml>
    参数说明


###### 多项选择型

<xml>
	<ToUserName><![CDATA[ToUserName]]></ToUserName>
	<FromUserName><![CDATA[FromUserName]]></FromUserName>
	<CreateTime>1357290913</CreateTime>
	<MsgType><![CDATA[update_template_card]]></MsgType>
	<TemplateCard>
		<CardType><![CDATA[multiple_interaction]]></CardType>
		<Source>
			<IconUrl><![CDATA[source_url]]></IconUrl>
			<Desc><![CDATA[更新后的卡片]]></Desc>
		</Source>
		<MainTitle>
			<Title><![CDATA[更新后的卡片标题]]></Title>
			<Desc><![CDATA[更新后的卡片副标题]]></Desc>
		</MainTitle>
        <SelectList>
            <QuestionKey><![CDATA[QuestionKey1]]></QuestionKey>
            <Title><![CDATA[下拉式选择器]]></Title>
            <SelectedId><![CDATA[option_id2]]></SelectedId>
            <Disable>false</Disable>
            <OptionList>
                <Id><![CDATA[option_id2]]></Id>
                <Text><![CDATA[选择题选项2]]></Text>
            </OptionList>
            <OptionList>
                <Id><![CDATA[option_id2]]></Id>
                <Text><![CDATA[选择题选项2]]></Text>
            </OptionList>
        </SelectList>
        <SubmitButton>
			<Text><![CDATA[提交]]></Text>
            <Key><![CDATA[Key]]></Key>
		</SubmitButton>
		<ReplaceText><![CDATA[已提交]]></ReplaceText>
	</TemplateCard>
</xml>
    参数说明


## 支持被动回复的事件类型

- 成员关注事件
- 进入应用
- 上报地理位置
- 点击菜单拉取消息的事件
- 点击菜单跳转链接的事件
- 点击菜单跳转小程序的事件
- 扫码推事件且弹出“消息接收中”提示框的事件推送
- 通用模板卡片右上角菜单事件

---

# 应用发送消息到群聊会话 概述

> 最后更新：2022/08/13

企业微信支持企业自建应用通过接口创建群聊并发送消息到群，让重要的消息可更及时推送给群成员，方便协同处理。应用消息仅限于发送到通过接口创建的内部群聊，不支持添加企业外部联系人进群。此接口暂时仅支持企业内的自建应用接入使用，且要求自建应用的可见范围是根部门。

示意图：


---

# 创建群聊会话

> 最后更新：2024/11/29

请求方式： POST（HTTPS）  请求地址： https://qyapi.weixin.qq.com/cgi-bin/appchat/create?access_token=ACCESS_TOKEN

请求包体:

```json
{
    "name" : "NAME",
    "owner" : "userid1",
    "userlist" : ["userid1", "userid2", "userid3"],
    "chatid" : "CHATID"
}
```


| 参数 | 是否必须 | 说明 |
| --- | --- | --- |
| access_token | 是 | 调用接口凭证 |
| name | 否 | 群聊名，最多50个utf8字符，超过将截断 |
| owner | 否 | 指定群主的id。如果不指定，系统会随机从userlist中选一人作为群主 |
| userlist | 是 | 群成员id列表。至少2人，至多2000人 |
| chatid | 否 | 群聊的唯一标志，不能与已有的群重复；字符串类型，最长32个字符。只允许字符0-9及字母a-zA-Z。如果不填，系统会随机生成群id |

权限说明：只允许企业自建应用调用，且应用的可见范围必须是根部门。

限制说明：群成员人数不可超过管理端配置的“群成员人数上限”，且最大不可超过2000人（含应用）。每企业创建群数不可超过1000/天。

返回示例：

```json
{
   "errcode" : 0,
   "errmsg" : "ok",
   "chatid" : "CHATID"
 }
```


| 参数 | 说明 |
| --- | --- |
| errcode | 返回码 |
| errmsg | 对返回码的文本描述内容 |
| chatid | 群聊的唯一标志 |


---

# 应用推送消息

> 最后更新：2026/02/06

目录

- 接口定义
- 消息类型
-       文本消息
-       图片消息
-       语音消息
-       视频消息
-       文件消息
-       文本卡片消息
-       图文消息
-       图文消息（mpnews）
-       markdown消息

### 接口定义

应用支持推送文本、图片、视频、文件、图文等类型。

请求方式： POST（HTTPS）  请求地址： https://qyapi.weixin.qq.com/cgi-bin/appchat/send?access_token=ACCESS_TOKEN

*请求包体: *

参数说明：


| 参数 | 是否必须 | 说明 |
| --- | --- | --- |
| access_token | 是 | 调用接口凭证 |

权限说明：只允许企业自建应用调用，且应用的可见范围必须是根部门。

限制说明：chatid所代表的群必须是该应用所创建。每企业消息发送量不可超过2万人次/分（若群有100人，每发一次消息算100人次）。未认证或小型企业不可超过 15w 人次/小时；中型企业 不可超过35w 人次/小时；大型企业 不可超过70w人次/小时。出于对成员的保护，每个成员在群中收到的同一个应用的消息不可超过200条/分，1万条/天，超过会被丢弃，而接口不会报错。（若应用创建了两个群，成员张三同时在这两个群中，应用往第一个群发送1条消息，再往第二个群发送2条消息，则张三累计收到该应用3条消息）。

返回示例：

{
   "errcode" : 0,
   "errmsg" : "ok"
 }
    消息类型文本消息请求示例：

```json
{
   "errcode" : 0,
   "errmsg" : "ok"
 }
```


### 消息类型


#### 文本消息

```json
{
	"chatid": "CHATID",
	"msgtype":"text",
	"text":{
		"content" : "你的快递已到\n请携带工卡前往邮件中心领取",
		"mentioned_list":["wangqing","@all"]
	},
	"safe":0
}
```

文本消息展现：

特殊说明：其中text参数的content字段可以支持换行，换行符请用转义过的'\n';支持使用<@userid>扩展语法来@群成员（企业微信 5.0.6 及以上版本支持）


#### 图片消息

请求示例：

{
	"chatid": "CHATID",
	"msgtype":"image",
	"image":{
		"media_id": "MEDIAID"
	},
	"safe":0
}
    请求参数：

```json
{
	"chatid": "CHATID",
	"msgtype":"image",
	"image":{
		"media_id": "MEDIAID"
	},
	"safe":0
}
```

图片消息展现：


#### 语音消息

请求示例：

```json
{
   "chatid" : "CHATID",
   "msgtype" : "voice",
   "voice" : {
        "media_id" : "MEDIA_ID"
   }
}
```

语音消息展现：


#### 视频消息

请求示例：

```json
{
   "chatid" : "CHATID",
   "msgtype" : "video",
   "video" : {
       "media_id" : "MEDIA_ID",
       "description" : "Description",
	   "title": "Title"
   },
   "safe":0
}
```

视频消息展现：


#### 文件消息

请求示例：

```json
{
   "chatid" : "CHATID",
   "msgtype" : "file",
   "file" : {
        "media_id" : "1Yv-zXfHjSjU-7LH-GwtYqDGS-zz6w22KmWAT5COgP7o"
   },
   "safe":0
}
```

文件消息展现：


#### 文本卡片消息

请求示例：

```json
{
	"chatid": "CHATID",
	"msgtype":"textcard",
	"textcard":{
		"title" : "领奖通知",
		"description" : "<div class=\"gray\">2016年9月26日</div> <div class=\"normal\"> 恭喜你抽中iPhone 7一台，领奖码:520258</div><div class=\"highlight\">请于2016年10月10日前联系行 政同事领取</div>",
		"url":"https://work.weixin.qq.com/",
		"btntxt":"更多"
	},
	"safe":0
}
```

特殊说明：卡片消息的展现形式非常灵活，支持使用br标签或者空格来进行换行处理，也支持使用div标签来使用不同的字体颜色，目前内置了3种文字颜色：灰色(gray)、高亮(highlight)、默认黑色(normal)，将其作为div标签的class属性即可，具体用法请参考上面的示例。

文本卡片消息展现 ：


#### 图文消息

请求示例：

```json
{
	"chatid": "CHATID",
	"msgtype":"news",
	"news":{
		"articles" :
		[
			{
				"title" : "中秋节礼品领取",
				"description" : "今年中秋节公司有豪礼相送",
				"url":"https://work.weixin.qq.com/",
				"picurl":"http://res.mail.qq.com/node/ww/wwopenmng/images/independent/doc/test_pic_msg1.png"
			 }
		]
	},
	"safe":0
}
```

图文消息展现：


#### 图文消息（mpnews）

请求示例：

```json
{
	"chatid": "CHATID",
	"msgtype":"mpnews",
	"mpnews":{
		"articles":[
			{
				"title": "地球一小时",
				"thumb_media_id": "biz_get(image)",
				"author": "Author",
				"content_source_url": "https://work.weixin.qq.com",
				"content": "3月24日20:30-21:30 \n办公区将关闭照明一小时，请各部门同事相互转告",
				"digest": "3月24日20:30-21:30 \n办公区将关闭照明一小时"
			}
		 ]
	},
	"safe":0
}
```

图文消息展现：


#### markdown消息

请求示例：

```json
{
   "chatid": "CHATID",
   "msgtype":"markdown",
   "markdown": {
        "content": "您的会议室已经预定，稍后会同步到`邮箱`  \n>**事项详情**  \n>事　项：<font color=\"info\">开会</font>  \n>组织者：@miglioguan  \n>参与者：@miglioguan、@kunliu、@jamdeezhou、@kanexiong、@kisonwang  \n>  \n>会议室：<font color=\"info\">广州TIT 1楼 301</font>  \n>日　期：<font color=\"warning\">2018年5月18日</font>  \n>时　间：<font color=\"comment\">上午9:00-11:00</font>  \n>  \n>请准时参加会议。  \n>  \n>如需修改会议信息，请点击：[修改会议信息](https://work.weixin.qq.com)"
   }
}
```

示例效果：

特殊说明：其中markdown参数的content字段支持使用<@userid>扩展语法来@群成员（企业微信 5.0.6 及以上版本支持）


---

