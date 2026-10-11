# YouTube官方授权：首次配置

当前没有Google Cloud应用，需要管理员使用组织专用Google账号在控制台完成。代码及部署准备可以先完成，真实授权须等待这些配置。

1. 打开[Google Cloud控制台](https://console.cloud.google.com/)，创建项目，例如EVERYDAYAI YouTube，并确认当前选中该项目。
2. 在API库启用 **YouTube Data API v3**。
3. 打开Google Auth Platform（或OAuth同意屏幕）。填写应用名称、支持邮箱和开发者联系邮箱；普通Google账号使用External，先保留Testing，把实际连接组织频道的Google账号加入Test users。已有Workspace组织且仅限内部使用时可根据实际资格选择Internal。
4. 在Data Access添加所需权限：`https://www.googleapis.com/auth/youtube.force-ssl` 和 `https://www.googleapis.com/auth/youtube.upload`。
5. 在Clients创建OAuth客户端，应用类型选 **Web application**。根据项目当前域名填写Authorized redirect URIs：`https://everydayai.com.cn/api/agent-reach/oauth/callback`。如果使用独立测试域名，控制台和服务器必须同时换成该测试域名。域名、协议、路径必须一致，不额外加尾部斜杠。该接入由服务端交换授权码，不依赖浏览器JavaScript origins；如控制台要求填写来源，填对应网站来源，不带回调路径。
6. 保存Client ID和Client Secret，由服务器配置管理写入以下字段：

```dotenv
AGENT_REACH_GOOGLE_CLIENT_ID=填写客户端ID
AGENT_REACH_GOOGLE_CLIENT_SECRET=填写客户端密钥
AGENT_REACH_GOOGLE_REDIRECT_URI=https://everydayai.com.cn/api/agent-reach/oauth/callback
```

不用把Client Secret发到聊天里，也不要放进前端或Git。现有服务通过backend/.env读取；不能用这三行覆盖整个.env。配置更新后，API与Conversation Actor工作进程需在受控发布时读取同一版本。

部署测试版本后，在设置→组织管理→互联网账号选择YouTube，准备官方授权、打开Google授权页面、选择组织频道并同意权限，再回到软件查看连接结果。普通API key不能替代账号OAuth授权。

测试状态下，这类External应用的refresh token通常7天过期，届时重新连接；正式对外提供使用前需处理Google验证要求。新API项目的视频上传可能被限制为私密，须以平台返回的实际可见性为准；OAuth应用验证与YouTube API项目上传审核不能混为一件事。

参考：[Google官方OAuth配置说明](https://developers.google.com/workspace/guides/configure-oauth-consent)、[YouTube服务端OAuth](https://developers.google.com/youtube/v3/guides/auth/server-side-web-apps)、[令牌生命周期](https://developers.google.com/identity/protocols/oauth2)、[视频上传限制](https://developers.google.com/youtube/v3/docs/videos/insert)。本说明未代替用户创建云项目或申请审核，未操作真实账号。
