# Changelog

## 1.1.1-preview

Fix the regression that froze video after 1.1.0, and clean up stream setup.

- Live video froze on the first frame because the muxer dropped every frame
  from cameras whose firmware reports timestamps as an int64-negative offset
  (near 2**64 unsigned). Those are real times -- the frame deltas match the
  camera's rate -- so they are now rebased instead of discarded; only the
  explicit "time unknown" marker is dropped.
- Choosing streams is now two steps: pick the cameras, then pick streams per
  camera, one selector each, instead of one collapsed section.
- Variant entities carry the camera name in their own name, so a consumer
  that shows only the entity name -- HomeKit, a voice assistant -- still says
  which camera it is.
- The add-on page no longer spells out technical details like the Opus codec
  or the loopback-only address.

## 1.1.0

Sound, and a stream for every player.

- Each camera now publishes eight streams instead of two: four sizes, each in
  the camera's own H.265 and in H.264 for players that cannot decode it. Choose
  the ones you want per camera; each becomes its own entity. Nothing is
  converted until something starts watching.
- **Include audio** now does what it says. It never worked before: the add-on
  asked your camera for sound and threw every frame away. The microphone now
  travels with every published stream, in the camera's own encoding, with
  nothing re-encoded to carry it. It stays off by default -- a microphone
  recording a room is your decision to make, not an upgrade's.
- Where you can hear it depends on what plays the stream. The Home Assistant
  camera card can over WebRTC; Apple Home can once you turn on its own audio
  support; an NVR reading the stream directly can. The H.264 streams carry an
  extra AAC copy for players that need one.
- Live view starts faster, and a camera whose connection had dropped now
  reconnects on its own. Before, it could stay dark until the add-on was
  restarted.
- The add-on page no longer shows an RTSP address that cannot be reached. In
  `local` mode it shows the real one and explains who can use it.
- The page reports whether sound is actually arriving from each camera, so a
  silent stream can be told apart from a silent room.

**Licence change:** the image now ships the GPL build of ffmpeg instead of the
LGPL one. It is the only way to get the standard H.264 and H.265 encoders,
which the new streams need. ffmpeg runs as a separate program, so nothing about
how the rest of this project is licensed changes. The build's sources are
linked from the README.

**Upgrading:** if you chose a stream codec for a camera before, that choice
carries over and the camera keeps the entity it already had. Automations,
dashboards and Apple Home pairings are unaffected.

英文以下为中文：

声音，以及给每种播放器准备的画面。

- 每台摄像头现在发布八路画面，而不是两路：四种尺寸，每种都有摄像头原生的 H.265，
  以及一路供无法解码 H.265 的播放器使用的 H.264。你可以按摄像头挑选需要哪几路，
  每一路各自成为一个实体。没有人观看时不做任何转换。
- **包含声音**现在名副其实。它此前从未生效过：加载项向摄像头要了声音，然后把每一帧
  都丢掉。现在麦克风的声音会跟随每一路发布的画面，用的是摄像头自己的编码，不为了
  携带它而做任何重新编码。默认仍然关闭——麦克风录下房间里的声音是你自己的决定，
  不该由一次升级替你做。
- 能不能听到取决于播放方。Home Assistant 的摄像头卡片走 WebRTC 时可以；Apple 家庭
  需要你打开它那边的声音支持；直接读取视频流的 NVR 可以。H.264 那几路额外带一路
  AAC 拷贝，供需要它的播放器使用。
- 实时画面出得更快；连接断掉的摄像头现在会自行重连。此前它可能一直黑屏，直到重启
  加载项。
- 加载项页面不再显示一个无法访问的 RTSP 地址。在 `local` 模式下它显示真实地址，并
  说明谁能使用它。
- 页面会报告每台摄像头是否真的有声音传来，这样"流是哑的"和"房间是静的"可以区分开。

**许可证变更：** 镜像现在内置 GPL 版的 ffmpeg，替代原先的 LGPL 版。这是取得标准
H.264 与 H.265 编码器的唯一途径，而新增的这些画面需要它们。ffmpeg 以独立程序运行，
因此本项目其余部分的许可方式不受影响。该构建的源码链接见 README。

**升级说明：** 如果你此前为某台摄像头选择过视频编码，该选择会被沿用，摄像头保留原有
实体。自动化、仪表盘和 Apple 家庭配对均不受影响。

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
