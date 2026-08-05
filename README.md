# Xiaomi Camera for Home Assistant

**English** | [简体中文](README.zh-CN.md)

See your Xiaomi cameras in Home Assistant.

Xiaomi's official integration gives you a camera's sensors and buttons, but not
its picture. This project adds the picture — as ordinary Home Assistant camera
entities, and as standard RTSP streams that Frigate, Scrypted or any NVR can
use as well.

- Cameras appear on their own, with their names and rooms from the Mi Home app
- Live view, snapshots and recording, like any other Home Assistant camera
- Your video stays on your own network

## Before you start

- **Home Assistant 2024.11 or newer**, on a 64-bit system
- **Home Assistant must be on the same network as your cameras.** This one is
  not negotiable — see [why](#why-it-has-to-be-on-the-same-network)
- Your cameras already set up in the Mi Home app

## Install

There are two pieces, and this repository gets added in two different places in
Home Assistant. Doing only one leaves you with half of it.

**1. Add the repository to your add-on store**

Settings → Add-ons → Add-on Store → ⋮ → Repositories, and paste:

```
https://github.com/willjayyyy/ha-xiaomi-camera
```

You do not need to install anything here — this just makes it available.

**2. Install the integration through HACS**

In HACS, add the same address as a custom repository (type: *Integration*),
then install **Xiaomi Camera**. Restart Home Assistant.

**3. Add the integration**

Settings → Devices & Services → Add Integration → **Xiaomi Camera**.

It offers to install the streaming service for you. That downloads about 180 MB,
so allow a few minutes for it.

Once it finishes, open its page and sign in to your Xiaomi account. Your cameras
show up a few seconds later.

## Signing in

After signing in, Xiaomi redirects your browser to an address that **will not
open**. A blank page or an error there is expected. Copy the full address from
the address bar and paste it into the add-on page to complete sign-in.

## Choosing streams

Each camera can publish several versions of the same picture. Pick per camera
in the integration's options, under **Video streams**.

| Stream | Good for |
|---|---|
| H.265 · Original quality | Recording, NVR software. The camera's own encoding, no re-encoding. |
| H.264 · Original quality | Browsers and players that cannot decode H.265. |
| 360p / 180p | Apple Home, remote viewing, dashboards showing several cameras at once. |
| 720p | A middle ground when the camera's own resolution is higher. |

A camera starts with its original encoding. If the picture will not play in
your browser, add an H.264 stream — most browsers cannot decode H.265.

**Apple Home:** add a 360p stream and point the HomeKit bridge at that entity.
Home Assistant re-encodes at a bitrate HomeKit chooses, which is low, so a
smaller picture looks considerably better than a larger one. Leave *Cameras
that support native H.264 streams* unticked.

## Without Home Assistant

The bridge is a self-contained service: it talks to the cameras and publishes
RTSP, and Home Assistant is one consumer of that among others. If you only want
the streams — for Frigate, Scrypted, an NVR, or anything that reads RTSP — you
can run the same image on its own.

Keep everything on one machine, and nothing needs a password:

```bash
docker run -d --name xiaomi-camera \
  --network host \
  -v xiaomi-camera:/data \
  ghcr.io/willjayyyy/addon-xiaomi-camera-bridge:latest
```

Every listener is then bound to `127.0.0.1`, so nothing else can open a
connection to it in the first place. Reach the page over an SSH tunnel —
`ssh -L 8098:127.0.0.1:8098 user@host`, then open `http://127.0.0.1:8098` — and
connect your Xiaomi account there.

To let other machines read the streams, publish them. That is the one setting
that changes the security model, so it comes with the passwords that go with
it, and the bridge will not start without them:

```bash
docker run -d --name xiaomi-camera \
  --network host \
  -v xiaomi-camera:/data \
  -e XIAOMI_CAMERA_ACCESS_MODE=lan \
  -e XIAOMI_CAMERA_RTSP_USERNAME=xiaomi \
  -e XIAOMI_CAMERA_RTSP_PASSWORD=... \
  -e XIAOMI_CAMERA_WEB_PASSWORD=... \
  ghcr.io/willjayyyy/addon-xiaomi-camera-bridge:latest
```

The streams are then at `rtsp://<host>:8554/camera_<device id>`, and the page
at `http://<host>:8098` lists the exact URL for each camera. Any setting can be
given this way: `XIAOMI_CAMERA_` followed by the option name in capitals.

`-v xiaomi-camera:/data` is not optional. The Xiaomi account authorization
lives there, so without it every update — which replaces the container — asks
you to sign in to Xiaomi again. The bridge warns in its log if it detects this.

Two things carry over unchanged from the add-on:

- **`--network host` is required**, for the same reason the add-on needs it:
  the cameras are found by broadcast and connected to directly, neither of
  which survives Docker's bridge network.
- **The machine must be on the cameras' own network.** A VPN does not qualify.

One caveat worth stating plainly: the page is served over plain HTTP, so the
password crosses the network in a form anyone watching it can read. That is the
same exposure the RTSP credentials already have. Put a reverse proxy with TLS
in front if that matters where you are running it.

## Why it has to be on the same network

Xiaomi cameras are reached directly, device to device, rather than through a
server. Finding them depends on being on the same local network.

If Home Assistant is somewhere else, the connection either falls back to
relaying your video through Xiaomi's servers — slower, lower quality, and your
video leaves your home — or fails outright.

**A VPN does not count as the same network.** We tested it: over a VPN the
connection fails completely, while on the same network a camera connects in
about two seconds.

## A camera can be online and switched off

Xiaomi cameras have a lens switch, separate from being connected to Wi-Fi. With
the lens off, the camera answers normally and then sends no picture at all —
which looks exactly like something is broken.

Both the add-on page and Home Assistant show this clearly, and you get a switch
entity so an automation can turn a camera on before taking a snapshot.

## Settings

| Setting | Default | What it does |
|---|---|---|
| Stream access | `local` | `local` keeps video on the Home Assistant machine. `lan` shares it with your whole network for tools like Frigate, and then requires a username and password. |
| RTSP username / password | — | Required when stream access is `lan` |
| Web page password | — | Required when stream access is `lan`. Optional otherwise, but **if you set it, it is always asked for** — through the Home Assistant panel too. |
| Video quality | `low` | Enough for a dashboard tile. Higher quality uses more bandwidth. |
| Include audio | off | Carry the camera's sound as well |
| Log level | `info` | Turn up to `debug` when reporting a problem |

## Privacy

By default, **video never leaves the machine Home Assistant runs on.** Home
Assistant can display it; nothing else on the network can reach it. This is the
initial configuration and requires no setup.

If you want another machine to use the streams, switch stream access to `lan`.
That shares them with your whole network, so a username and password become
required — the add-on will not start without them, because an unprotected
camera stream is found within seconds by anything scanning the network.

Your Xiaomi account credentials are kept only in the add-on's own private
storage, and are stripped out of logs and error messages before they are
written.

One thing outside this project's control: if you expose Home Assistant itself
to the internet, that is a risk regardless of anything set here.

## Which cameras work

Support follows Xiaomi's own list, which leaves out most models released before
2022. Unsupported cameras are simply not shown, rather than appearing as
something that never works.

This reflects a real limitation rather than caution: a camera on Xiaomi's
exclusion list was tested and could not be connected to at all. A camera that
does not appear is almost certainly on that list.

## Known limits

- Cameras send H.265 video. Every other stream variant is produced by
  converting it, and only while something is watching.
- Only Xiaomi's China account region has been tested.
- No two-way audio, no pan/tilt control, no SD-card playback.

## Troubleshooting

**No cameras listed** — check the account is connected on the add-on page, and
that the cameras are in Mi Home under that same account.

**A camera says "Switched off"** — turn its lens on, in Mi Home or with the
switch entity.

**No picture while the camera reports "Ready"** — Home Assistant is most likely
not on the same network as the camera. See above.

Turn the log level up to `debug` before reporting a problem, and include the log.

## Contributing

Conventions and the release process are in
[CONTRIBUTING.md](CONTRIBUTING.md).

## For developers

[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) explains how this works
internally, why it is split into two components, and the non-obvious behaviour
of Xiaomi's streaming library.

## Licence

This project's own code is Apache-2.0. See [LICENSE](LICENSE).

Two third-party components are bundled or downloaded rather than written here,
each under its own licence:

- **Xiaomi's own streaming library** is closed source and **not redistributed
  here** — it is downloaded from Xiaomi during the build and is covered by
  their licence, not ours.
- The add-on image bundles a static [ffmpeg](https://ffmpeg.org/) from
  [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds), GPL variant,
  which is licensed under the GPL. Its sources are available from that
  project.

Built by studying [Xiaomi Miloco](https://github.com/XiaoMi/xiaomi-miloco).
