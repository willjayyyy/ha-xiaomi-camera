# Xiaomi Camera Bridge

Streams your Xiaomi cameras over the vendor's peer-to-peer protocol and
republishes them as standard RTSP, so Home Assistant and any NVR can use them.

*[中文说明见下方](#中文说明)*

## Setup

Installing downloads about 180 MB, so allow a few minutes for it.

1. Start the add-on.
2. Open **Xiaomi Camera** in the sidebar and select **Connect Xiaomi account**.
3. Sign in on the page that opens. Your browser will then land on an address
   that **cannot be opened** — this is expected. Copy the whole address from
   the address bar and paste it back into the add-on page.
4. Your cameras appear within a few seconds.

Install the **Xiaomi Camera** integration as well, so the cameras show up as
entities in Home Assistant. Without it the streams still work, but you would
have to add each one by hand as a Generic Camera.

## The bridge must be on the cameras' network

Xiaomi cameras are reached directly, device to device, and finding them depends
on being on the same local network.

**A VPN does not qualify as the same network.** Measured: on the same network a
camera connects in about two seconds and the picture appears almost
immediately; over a VPN the connection fails entirely.

## A camera can be online and switched off

Xiaomi cameras have a lens power switch, separate from being connected to
Wi-Fi. When it is off the camera still accepts a connection and then sends
nothing at all — which looks exactly like a broken stream.

The add-on page shows this as **Switched off**, and the integration exposes the
switch as its own entity so automations can turn a camera on before asking for
a picture.

## Configuration

### Stream access

The default, `local`, keeps every stream on this machine. Home Assistant can
still show them; nothing else on your network can reach them.

Choose `lan` only if another machine needs the streams — Frigate or an NVR, for
example. That publishes this add-on to your whole network, so all three
passwords become mandatory: the RTSP username and password, and the web page
password. The add-on will not start without them, and says which are missing.

### Video quality

`low` is the default and is enough for a dashboard tile. `high` uses more
bandwidth and more resources on the machine running Home Assistant.

## Supported cameras

Support follows the vendor's own device list, which excludes most models
released before 2022. Cameras on that exclusion list are left out rather than
shown as entities that can never connect — tested against an excluded model,
the connection never completes.

If a camera does not appear, it is almost certainly on that list.

## Running it outside Home Assistant

This add-on is also a standalone service. The same image runs under plain
Docker for anyone who wants only the RTSP streams -- see *Without Home
Assistant* in the project README. Nothing here depends on that; it is listed
so the option is known.

The `web_password` option is required whenever stream access is `lan` — that
is when this page becomes reachable from your network. It is optional
otherwise, but setting it always means being asked for it, including through
the Home Assistant panel. A password that is configured and then never
requested is worse than none, because you would believe you had one.

## Troubleshooting

**No cameras listed** — check that the account is connected on the add-on page,
and that the cameras are in the Mi Home app under the same account.

**A camera shows "Switched off"** — turn its lens on in the Mi Home app, or via
the switch entity the integration creates.

**No picture, camera shows "Ready"** — the bridge must be on the same network as
the cameras. See above.

Raise **Log level** to `debug` before reporting a problem, and include the log.

---

# 中文说明

通过小米官方的点对点协议获取摄像头画面，并转换成标准 RTSP 输出，
供 Home Assistant 与各类 NVR 使用。

## 配置步骤

安装需下载约 180 MB，请耐心等待几分钟。

1. 启动加载项。
2. 在侧边栏打开 **Xiaomi Camera**，点击 **连接小米账号**。
3. 在打开的页面登录。登录后浏览器会跳转到一个**打不开的地址**，这是正常的。
   把地址栏里的完整地址复制下来，粘贴回加载项页面。
4. 几秒钟后摄像头就会出现。

请同时安装 **Xiaomi Camera** 集成，摄像头才会成为 Home Assistant 中的实体。
没有集成时画面依然可用，但需要你手动为每台摄像头添加一个 Generic Camera。

## 加载项必须与摄像头在同一局域网

小米摄像头是设备之间直接连接的，而找到它依赖于处在同一个局域网内。

**VPN 不属于同一网络。** 实测：同一网络下约 2 秒建立连接、画面几乎瞬间出现；
经 VPN 连接则完全失败。

## 摄像头可能“在线但关着”

小米摄像头的镜头开关与联网状态是两回事。镜头关闭时，摄像头仍会接受连接，
但**一帧画面都不会发送** —— 这与画面故障的表现完全一样。

加载项页面会把这种状态显示为**已关闭**，集成也会为它创建一个开关实体，
这样自动化就能在取画面之前先把摄像头打开。

## 配置项说明

### 画面访问范围

默认值 `local` 会把所有画面限制在本机。Home Assistant 依然能显示，
而局域网上的其他设备访问不到。

只有当其他机器需要这些画面时（例如 Frigate 或 NVR）才选择 `lan`。
这会把本加载项发布到整个局域网，因此三项密码全部变为必填：RTSP 用户名、
RTSP 密码，以及网页密码。缺任何一项加载项都会拒绝启动，并说明缺的是哪些。

### 画质

默认 `low`，用于仪表盘缩略图足够。`high` 会占用更多带宽和主机资源。

## 支持的机型

支持范围以厂商自己的设备名单为准，其中排除了大部分 2022 年之前的机型。
被排除的机型不会出现在列表中，而不是变成一个永远连不上的实体 ——
我们实测过一台被排除的机型，连接始终无法建立。

如果某台摄像头没有出现，几乎可以确定它在排除名单上。

## 脱离 Home Assistant 运行

本加载项同时也是一个可独立运行的服务。只想要 RTSP 流的用户，可以用普通 Docker
运行同一个镜像 —— 见项目 README 的《不用 Home Assistant 也能跑》。
这里的功能不依赖那种用法，写在这里只是让你知道有这个选择。

只要画面访问范围是 `lan`，`web_password` 就是必填的 —— 那时这个页面会暴露在
局域网上。其他情况下可以不填，但**只要设置了就一定会要求输入**，通过 Home
Assistant 面板进入时也一样。配置了却从不校验的密码比没有更糟，因为你会以为
自己有保护。

## 常见问题

**没有列出任何摄像头** —— 确认加载项页面已连接账号，且摄像头在米家 App 中
属于同一个账号。

**摄像头显示“已关闭”** —— 在米家 App 中打开镜头，或使用集成创建的开关实体。

**摄像头显示“就绪”但没有画面** —— 加载项必须与摄像头处于同一局域网，见上文。

反馈问题前请把**日志级别**调到 `debug`，并附上日志。
