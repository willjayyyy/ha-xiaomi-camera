# Changelog

## 1.0.0

First release.

- Xiaomi cameras appear in Home Assistant as camera entities, with the names
  and rooms they have in the Mi Home app.
- Live view, snapshots and recording, like any other camera.
- A switch for each camera's lens, so an automation can turn one on before
  asking for a picture.
- The same streams are published as standard RTSP for Frigate, Scrypted or any
  NVR, and a second time as H.264 for browsers and Apple Home, converted only
  while something is watching.
- Choose which cameras to bring in, and change that at any time.
- Video stays on your own network unless you deliberately publish it. Doing so
  requires passwords; the add-on will not start without them.

英文以下为中文：

首个版本。

- 小米摄像头以 camera 实体出现在 Home Assistant 中，名称和房间沿用米家 App。
- 实时画面、快照、录像，与其他摄像头无异。
- 每台摄像头附带镜头开关实体，自动化可以在取画面之前先打开摄像头。
- 同一路画面以标准 RTSP 发布，供 Frigate、Scrypted 或任意 NVR 使用；并额外发布
  一路 H.264 供浏览器和 Apple 家庭使用，仅在有人观看时才转换。
- 可以选择接入哪些摄像头，并随时修改。
- 除非你主动发布，画面不会离开你自己的网络。发布时必须设置密码，否则加载项拒绝启动。
