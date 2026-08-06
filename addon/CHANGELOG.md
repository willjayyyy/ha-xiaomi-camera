# Changelog

## 1.2.1

Fix previews that stop working until the add-on is restarted.

**Upgrade if the add-on page has ever stopped showing pictures.**

- Watching a preview and then leaving the page could stop every preview from
  working, for good: no picture arrived, refreshing changed nothing, and only
  restarting the add-on brought it back. Shutting down the decoder behind a
  preview can hang -- ending a process is not the same as being able to
  collect its exit status, and this add-on shares itself with the vendor's
  closed-source library -- and that shutdown was waited on while holding a
  lock every other preview needed.
- That lock is now gone rather than made faster. The table of running
  previews is a dictionary touched from a single thread, so its own
  operations were never divisible and it never needed guarding; all that
  did was that one camera must not be opened twice, and that now follows
  from putting a preview into the table before starting it instead of
  after. Clearing away departed cameras cannot stall because it no longer
  waits for anything, and shutting a decoder down runs in the background
  and gives up rather than waiting forever.
- The same hang quietly stopped the add-on from noticing cameras being added,
  removed or switched off, because the periodic refresh ended by queueing on
  the same lock. Home Assistant kept seeing the right cameras throughout, so
  nothing looked wrong.
- A camera removed while its preview was still opening used to leave that
  preview running, reading a stream nobody would ever look at, with nothing
  later going looking for it -- the camera it belonged to was gone from every
  list. It now ends itself as soon as it starts.
- A decoder that cannot be shut down is now reported. It is stopped away from
  the request that triggered it, and a background failure that nobody reads
  surfaces only when the garbage collector happens to reach it, if ever.
- go2rtc was shut down the same unbounded way. It could have left the add-on
  unable to stop on its own, waiting to be killed instead.

### 中文

修复预览画面失效、必须重启加载项才能恢复的问题。

**如果你的加载项页面曾经不再出图，请升级。**

- 看过一次预览再离开页面，可能导致此后所有预览都不再工作，且无法自行恢复：
  画面出不来，刷新也没用，只有重启加载项才行。关闭预览背后的解码进程有可能
  卡住——结束一个进程，和还能不能取回它的退出状态，是两回事，而本加载项与
  厂商的闭源库共处同一进程——而这个关闭操作是在持有其它预览都要用的那把锁
  时等待的。
- 那把锁不是被改快，而是被整个删掉了。存放运行中预览的表就是一个 dict，只在
  单线程里增删，它自身的操作本来就不可分割，从来不需要保护；真正需要保证的只
  是同一台摄像头不会被打开两次，而这一点现在由「先把预览放进表、再启动它」自
  然成立。清理已移除的摄像头不再等待任何东西，因此不可能卡住；关闭解码进程改
  在后台进行，并且会放弃等待而不是一直等下去。
- 同一次卡死还会让加载项不再察觉摄像头的新增、移除和开关，而且毫无提示——
  定时刷新的最后一步正是排队等这把锁。这期间 Home Assistant 看到的摄像头列表
  始终是对的，所以表面上一切正常。
- 摄像头在其预览正启动时被移除，此前会留下一路仍在运行的预览，读着一条再也不
  会有人看的流，而且之后没有任何环节会去找它——它所属的摄像头已经不在任何列表
  里了。现在它一启动完就会自行结束。
- 关不掉的解码进程现在会被记录下来。它是在触发它的那个请求之外被关闭的，而后
  台失败若无人读取，只有等垃圾回收碰巧处理到它时才会浮现，甚至永远不会。
- go2rtc 此前也用同样的无限等待方式关闭，可能导致加载项无法自行退出，只能等
  着被强制结束。

## 1.2.0

Fix the video freeze from 1.1.0, and make the un-transcoded stream
codec-neutral.

**Upgrade if you are on 1.1.0 — live video did not play there.**

- Live video froze on the first frame because the muxer dropped every frame
  from cameras whose firmware reports timestamps as an int64-negative offset
  (near 2**64 unsigned). Those are real times -- the frame deltas match the
  camera's rate -- so they are now rebased instead of discarded; only the
  explicit "time unknown" marker is dropped.
- The root stream is no longer assumed to be H.265. It is named for the camera
  (`camera_<did>`), so a camera whose native codec is H.264 is no longer
  mislabelled.
- `h265` is now a real transcode to H.265 at full resolution, alongside the
  existing `h264` family. Choosing a codec-named stream always means a
  transcode; the original stream is the only one that is not.
- Existing entries keep their entities: the stored "h265" root selection is
  migrated to "original" on upgrade.
- Choosing streams is now two steps: pick the cameras, then pick streams per
  camera, one selector each, instead of one collapsed section.
- Every stream is called the same thing in the options form and on the device
  page, in your own language. The form used to show English labels, and to
  word them differently from the entities. The original stream's entity is
  named after the camera itself; every other one carries its codec and size.
- The full-resolution transcodes are now called "H.264 full size" rather than
  just "H.264", so they read as comparable to the scaled ones next to them.
- The add-on page no longer spells out technical details like the Opus codec
  or the loopback-only address.

### 中文

修复 1.1.0 的视频卡死，并让未转码的那一路码流不再绑定编码。

**如果你在用 1.1.0，请升级——那个版本的实时画面根本放不出来。**

- 实时画面停在第一帧：部分摄像头的固件把时间戳上报为 int64 负数偏移（接近
  2**64 无符号），封装器于是把这些帧全部丢弃。它们其实是真实时间——帧间隔与
  摄像头的帧率吻合——所以现在改为重新基准化而不是丢弃，只有明确的"时间未知"
  标记才丢。
- 原始码流不再假定是 H.265，它按摄像头命名（`camera_<did>`）。原生编码本就是
  H.264 的摄像头不会再被标错。
- `h265` 现在是真正的 H.265 全分辨率转码流，与已有的 `h264` 系列并列。凡是以
  编码命名的码流都意味着转码，只有原始码流不转。
- 已有配置的实体不受影响：升级时存量的 "h265" 原始码流选择会迁移为 "original"。
- 选择码流现在分两步：先选摄像头，再逐台选码流，每台一个选择器，不再挤在一个
  折叠区里。
- 每一路码流在选项表单和设备页上叫同一个名字，并跟随你的语言。此前表单显示的
  是英文，而且措辞与实体名不一致。原始码流的实体以摄像头自身命名，其余都带上
  编码和分辨率。
- 全分辨率转码流现在叫"H.264 原始尺寸"而不是简单的"H.264"，与旁边的缩放档位
  放在一起才读得出可比性。
- 加载项页面不再罗列 Opus 编码、仅回环地址这类技术细节。

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
