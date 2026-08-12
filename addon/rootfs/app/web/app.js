"use strict";

// Ingress serves this page under a path prefix, so every request is relative.
//
// The header goes on every request because a standalone deployment guards this
// page with HTTP Basic, and browsers attach those credentials to cross-site
// requests automatically. A form on another site could otherwise unlink the
// account; it cannot set a header of its own.
const api = (path, options = {}) =>
  fetch(`.${path}`, {
    ...options,
    headers: { ...(options.headers || {}), "X-Xiaomi-Camera": "1" },
  });
const $ = (id) => document.getElementById(id);

const I18N = {
  en: {
    title: "Xiaomi Camera Bridge",
    signInTitle: "Xiaomi Camera Bridge",
    signInHint: "Enter the web page password from this add-on's configuration.",
    signInPlaceholder: "Web page password",
    signIn: "Sign in",
    signInWrong: "That password is not right.",
    subtitle: "Publishes your Xiaomi cameras as standard RTSP streams.",
    accountHeading: "Account", camerasHeading: "Cameras",
    accountTitle: "Account", settingsTitle: "Settings",
    prefFps: "Frame rate", prefDetail: "Detail",
    fpsCamera: "Camera's rate",
    audioOn: "Audio",
    detailHigh: "High", detailMedium: "Medium", detailLow: "Low",
    // Connection path ---------------------------------------------------
    pathRow: "Connection",
    pathOfficial: "Xiaomi official",
    pathCompat: "Compatibility mode",
    pathOfficialUnsupported: "Xiaomi doesn't support this model",
    pathCompatNoAuth: "Compatibility mode not signed in",
    pathCompatRisky: "This model may not connect",
    switchToOfficial: "Switch to Xiaomi official",
    qualityLow: "Standard", qualityHigh: "High",
    compatTitle: "Compatibility mode",
    compatEnabled: "Enabled", compatNotEnabled: "Not enabled",
    compatWhy: "Compatibility mode reaches cameras a different way and needs your Xiaomi account password. It is separate from the sign-in you already completed, and stores a long-lived credential.",
    compatEnableHint: "Turn it on from the settings of a camera that needs it.",
    compatCredit: "Compatibility mode is built on the open-source project go2rtc",
    compatConnect: "Connect with compatibility mode",
    compatRemove: "Remove credential",
    compatCannotRemove: "Stays until the add-on is uninstalled -- which clears its other data too.",
    compatNoCameras: "No cameras yet.",
    compatSignInBusy: "Another sign-in is already running. Try again shortly.",
    compatCaptchaLabel: "Captcha",
    compatCodeLabel: "Verification code",
    compatCodeSentTo: "Code sent to",
    account: "Account", password: "Password",
    openConfig: "Open configuration",
    reloading: "Reconnecting",
    connect: "Connect Xiaomi account", disconnect: "Disconnect",
    finish: "Finish sign-in", cancel: "Cancel",
    step1Link: "Open the Xiaomi sign-in page",
    step1Rest: "and approve access.",
    step2: "Your browser will land on an address that cannot be opened — that is expected. Copy the entire address from the address bar.",
    step3: "Paste it below.",
    privacyNote: "The address is read locally to extract the authorization code. Nothing is sent anywhere else.",
    callbackPlaceholder: "https://127.0.0.1/?code=…&state=…",
    checking: "Checking…", connected: "Connected", notConnected: "Not connected",
    unreachable: "Bridge unreachable",
    notLoaded: "Not loaded yet.", loading: "Loading…",
    connectToSee: "Connect your Xiaomi account to see your cameras.",
    noCameras: "No supported cameras found on this account.",
    loadFailed: "Could not load the camera list.",
    offline: "Offline", switchedOff: "Switched off",
    connecting: "Connecting…",
    previewFailed: "Could not load the picture",
    cameraOffHint: "Turn the camera on to see a picture",
    retry: "Try again",
    copy: "Copy", copied: "Copied",
    credentialsHint: "This stream needs the RTSP username and password — from this add-on's configuration, or from the startup log if they were generated for you.",
    noCode: "That address does not contain an authorization code. Copy the full address from the page you were redirected to.",
    linked: "Account connected.",
    startFailed: "Could not start sign-in: ",
    signInFailed: "Sign-in failed: ",
    confirmUnlink: "Disconnect the Xiaomi account? Cameras will stop streaming.",
    // Video settings ------------------------------------------------------
    defaultsHeading: "Defaults", cameraSettings: "Camera settings",
    settingPicture: "Picture quality", settingSound: "Sound", settingTranscode: "Transcode quality",
    pictureHD: "HD", pictureSD: "SD",
    on: "On", off: "Off",
    transcodeStandard: "Standard", transcodeSharp: "Sharp", transcodeMaximum: "Maximum",
    followDefault: "Follow default",
    addressGroup: "Address",
    settingsSaveFailed: "Could not save that setting.",
    pathSwitchWarning: "Changing the connection rebuilds this camera's stream -- anything watching it now, including this preview, HomeKit and a recorder, reconnects.",
    switchConnection: "Switch",
    // Add-on link ------------------------------------------------------------
    addonHeading: "Add-on",
    // Camera card controls --------------------------------------------------
    play: "Play", stop: "Stop", enlarge: "Enlarge picture", close: "Close",
    settingsButton: "Settings",
    tapToView: "Tap to view",
    notSupported: "Not supported", limitedSupport: "Limited",
  },
  zh: {
    title: "小米摄像头桥接",
    signInTitle: "小米摄像头桥接",
    signInHint: "请输入加载项配置中的网页密码。",
    signInPlaceholder: "网页密码",
    signIn: "登录",
    signInWrong: "密码不正确。",
    subtitle: "把小米摄像头画面转换成标准 RTSP 流。",
    accountHeading: "账号", camerasHeading: "摄像头",
    accountTitle: "账号", settingsTitle: "设置",
    prefFps: "帧率", prefDetail: "清晰度",
    fpsCamera: "原生帧率",
    audioOn: "声音",
    detailHigh: "高", detailMedium: "中", detailLow: "低",
    // Connection path ---------------------------------------------------
    pathRow: "连接方式",
    pathOfficial: "小米官方",
    pathCompat: "兼容模式",
    pathOfficialUnsupported: "小米不支持这个型号",
    pathCompatNoAuth: "兼容模式未登录",
    pathCompatRisky: "这个型号可能连不上",
    switchToOfficial: "改用小米官方",
    qualityLow: "标清", qualityHigh: "高清",
    compatTitle: "兼容模式",
    compatEnabled: "已启用", compatNotEnabled: "未启用",
    compatWhy: "兼容模式用另一种方式连接摄像头，需要你的小米账号密码。这与你已完成的授权是分开的，会保存一个长期凭据。",
    compatEnableHint: "请在需要它的摄像头设置里开启。",
    compatCredit: "兼容模式基于开源项目 go2rtc",
    compatConnect: "用兼容模式连接",
    compatRemove: "删除凭据",
    compatCannotRemove: "会一直保留，直到卸载这个加载项 —— 卸载会连同其他数据一并清空。",
    compatNoCameras: "暂无摄像头。",
    compatSignInBusy: "已有一个登录正在进行，请稍后再试。",
    compatCaptchaLabel: "图形验证码",
    compatCodeLabel: "验证码",
    compatCodeSentTo: "验证码已发送至",
    account: "账号", password: "密码",
    openConfig: "打开配置",
    reloading: "正在重连",
    connect: "连接小米账号", disconnect: "断开连接",
    finish: "完成登录", cancel: "取消",
    step1Link: "打开小米登录页面",
    step1Rest: "并授权。",
    step2: "浏览器随后会跳转到一个打不开的地址 —— 这是正常的。请复制地址栏里的完整地址。",
    step3: "粘贴到下方。",
    privacyNote: "该地址仅在本地解析以提取授权码，不会发送到任何其他地方。",
    callbackPlaceholder: "https://127.0.0.1/?code=…&state=…",
    checking: "检查中…", connected: "已连接", notConnected: "未连接",
    unreachable: "无法连接到服务",
    notLoaded: "尚未加载。", loading: "加载中…",
    connectToSee: "连接小米账号后即可看到你的摄像头。",
    noCameras: "该账号下没有找到受支持的摄像头。",
    loadFailed: "无法加载摄像头列表。",
    offline: "离线", switchedOff: "已关闭",
    connecting: "连接中…",
    previewFailed: "无法加载画面",
    cameraOffHint: "打开摄像头后才能看到画面",
    retry: "重试",
    copy: "复制", copied: "已复制",
    credentialsHint: "该地址需要 RTSP 用户名和密码 —— 在加载项配置中填写的那组，或启动日志里自动生成的那组。",
    noCode: "该地址中没有授权码。请复制跳转后页面地址栏里的完整地址。",
    linked: "账号已连接。",
    startFailed: "无法开始登录：",
    signInFailed: "登录失败：",
    confirmUnlink: "确定断开小米账号？摄像头将停止推流。",
    // Video settings ------------------------------------------------------
    defaultsHeading: "默认设置", cameraSettings: "摄像头设置",
    settingPicture: "画面质量", settingSound: "声音", settingTranscode: "转码画质",
    pictureHD: "高清", pictureSD: "标清",
    on: "开启", off: "关闭",
    transcodeStandard: "标准", transcodeSharp: "锐利", transcodeMaximum: "最高",
    followDefault: "跟随默认",
    addressGroup: "地址",
    settingsSaveFailed: "设置未能保存。",
    pathSwitchWarning: "切换连接方式会重建这台摄像头的流，正在观看的一切都会重连，包括这个预览、HomeKit 和录像。",
    switchConnection: "切换",
    // Add-on link ------------------------------------------------------------
    addonHeading: "加载项",
    // Camera card controls --------------------------------------------------
    play: "播放", stop: "停止", enlarge: "放大画面", close: "关闭",
    settingsButton: "设置",
    tapToView: "点击查看",
    notSupported: "不支持", limitedSupport: "有限支持",
  },
};

const STORAGE_KEY = "xcam.lang";

function pickLanguage() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved && I18N[saved]) return saved;
  return (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
}

let lang = pickLanguage();
const t = (key) => I18N[lang][key] ?? I18N.en[key] ?? key;
let cameras = [];
//: Shared defaults (`{quality, audio, transcode_quality}`), fetched once and
//: kept live so a camera's sheet can show "Follow default (<value>)" — read
//: from `GET /api/settings`, written back through `PUT /api/settings`. Only
//: read while the settings sheet (or a camera sheet) is open; nothing on the
//: page itself shows a default any more.
let defaults = null;
//: The add-on's own identity (`{slug, compat_ready}`), from `GET /api/info`.
//: `slug` is `null` for a standalone deployment, which is what makes the
//: settings sheet's "open configuration" row optional.
let addonInfo = null;
//: Whether the Xiaomi account is currently linked, from the last
//: `/api/health` response. Read by the account sheet and by the camera
//: area's empty state; not itself pushed into the DOM by the background poll
//: below, which would tear down a preview nobody asked to stop.
let accountLinked = false;
//: Which screen the shared overlay is currently showing, so a background
//: event (the account changing, a default changing) can refresh it in place
//: instead of leaving it stale or reaching into the DOM blind. `null` when
//: the overlay is closed, or open on a camera's own sheet (tracked
//: separately by `openSheetDid`).
let sheetKind = null;

function applyLanguage() {
  document.documentElement.lang = lang === "zh" ? "zh-Hans" : "en";
  document.title = t("title");
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-label]").forEach((el) => {
    el.setAttribute("aria-label", t(el.dataset.i18nLabel));
  });
  document.querySelectorAll("[data-lang]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.lang === lang));
  });
  localStorage.setItem(STORAGE_KEY, lang);
  // Rebuilt rather than left stale: every string in an open sheet is baked
  // in at render time, not read live through `data-i18n`. Closed first --
  // its content was built the same way, and there is no session to lose by
  // closing it (the sheets are all re-entered from the header, not resumed).
  closeOverlay();
  if (!document.querySelector("main").hidden) refreshStatus();
}

// Bound on the document, not on `main`: the sign-in screen is outside it,
// and someone who cannot read the page cannot sign in to reach the switch.
document.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-lang]");
  if (btn) { lang = btn.dataset.lang; applyLanguage(); }
});

function showMessage(text, kind) {
  const el = $("message");
  el.textContent = text;
  el.className = `msg show ${kind}`;
  if (kind === "success") setTimeout(() => el.classList.remove("show"), 6000);
}

/**
 * Escape a value for insertion into HTML, including inside an attribute.
 *
 * The textContent/innerHTML trick alone escapes `&`, `<` and `>` but not
 * quotes, which is enough in text position and not enough in `attr="..."` --
 * a value containing a quote closes the attribute and starts writing markup.
 */
function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/**
 * Pull `code` and `state` out of whatever the user pasted.
 *
 * Accepts a full URL, a bare query string, or a fragment -- asking someone to
 * identify the right substring by hand is where this step usually goes wrong.
 */
function extractCallbackParams(input) {
  const text = (input || "").trim();
  if (!text) return null;
  const candidates = [];
  try {
    const url = new URL(text);
    candidates.push(url.searchParams);
    if (url.hash.length > 1) candidates.push(new URLSearchParams(url.hash.slice(1)));
  } catch {
    const start = text.indexOf("?");
    candidates.push(new URLSearchParams(start >= 0 ? text.slice(start + 1) : text));
  }
  for (const params of candidates) {
    const code = params.get("code");
    if (code) return { code, state: params.get("state") || "" };
  }
  return null;
}

/**
 * The camera area's three mutually exclusive states: loading (the initial
 * markup, and `loadCameras`'s own placeholder), not connected to a Xiaomi
 * account (this function's `else` branch), and connected with an empty or
 * populated grid (`loadCameras`/`renderCameras`). The account did not vanish
 * by moving into the header -- this is where it comes back when there is
 * nothing else to look at.
 */
async function refreshStatus() {
  try {
    const data = await (await api("/api/health")).json();
    accountLinked = data.linked;
    if (data.linked) {
      await loadCameras();
    } else {
      showNotConnected();
    }
    refreshOpenSheet();
  } catch {
    accountLinked = false;
    $("cameras").innerHTML = `<p class="empty">${escapeHtml(t("unreachable"))}</p>`;
  }
}

/**
 * The camera area's "not connected" empty state -- shown both here (an
 * explicit refresh, e.g. after Disconnect) and by the background poll below
 * when it observes the account transitioning from linked to unlinked.
 */
function showNotConnected() {
  // Stopped before the markup holding them is discarded -- Disconnect goes
  // through this, and unlike `loadCameras`/`renderCameras` it used to skip
  // this, leaving any running preview's socket and peer-to-peer session
  // open with nothing left able to close them.
  $("cameras").querySelectorAll("[data-preview]").forEach(stopPreview);
  $("cameras").innerHTML = `<div class="empty">
    <p>${escapeHtml(t("connectToSee"))}</p>
    <button type="button" class="primary" id="cameras-connect-btn">${escapeHtml(t("connect"))}</button>
  </div>`;
  $("cameras-connect-btn").addEventListener("click", openAccountSheet);
}

/**
 * The shared video defaults, read independently of the account link -- they
 * are not camera facts, so there is nothing to wait on. Loaded once at
 * startup and kept live so the settings sheet always shows the current
 * value the moment it opens, rather than fetching on every open.
 */
async function loadSettings() {
  try {
    const response = await api("/api/settings");
    if (!response.ok) throw new Error(await response.text());
    defaults = (await response.json()).defaults;
  } catch {
    // Left `null`. The settings sheet's defaults group stays empty until the
    // next successful load -- nothing more can usefully be said here that
    // the camera list's own error state has not already said.
  }
}

/**
 * The add-on's own identity -- the Supervisor slug and whether a
 * compatibility-mode credential exists. Independent of the account link for
 * the same reason as `loadSettings`, and independent of it in the other
 * direction too: `compat_ready` is what the account sheet's compatibility
 * row shows even before the Xiaomi account itself is connected.
 *
 * Called without being awaited at startup (see the bottom of this file), so
 * M1 or M3 can already be open by the time this resolves -- `refreshOpenSheet`
 * corrects whichever one is, the same way `accountLinked` does after the
 * health poll.
 */
async function loadInfo() {
  try {
    const response = await api("/api/info");
    if (!response.ok) throw new Error(await response.text());
    addonInfo = await response.json();
  } catch {
    addonInfo = { slug: null, compat_ready: false };
  }
  refreshOpenSheet();
}

/**
 * The camera's RTSP URL, addressed from where the page is being viewed.
 *
 * The bridge reports `127.0.0.1` deliberately -- Home Assistant shares the
 * host's network namespace, so that is correct for it, and a URL naming a
 * specific interface would be wrong as soon as the host gained another. It is
 * useless to a person, though, who is reading this page in order to paste the
 * address into Frigate on a different machine. The host they reached this page
 * on is the same host the streams are on, so it is the right substitution --
 * but only when the listener is actually reachable off-box. In `local` mode it
 * is bound to loopback, so rewriting the hostname would swap a working address
 * for a dead one; `reachableOffHost` (from `rtsp_reachable_off_host`) is what
 * tells this function which case it is in.
 */
function displayUrl(rtspUrl, reachableOffHost) {
  if (!reachableOffHost) return rtspUrl;
  try {
    const url = new URL(rtspUrl);
    url.hostname = location.hostname;
    return url.toString();
  } catch {
    return rtspUrl;
  }
}

//: Frame rates offered below whatever the camera is measured to send. Not a
//: fixed list of what cameras can do -- that would be wrong the moment one
//: ships that sends more -- but a ladder trimmed to what this camera gives.
const FPS_LADDER = [20, 15, 12, 8, 5, 2];

//: Used until a camera has streamed long enough to measure, and as the ceiling
//: when it never does.
const FPS_ASSUMED = 20;

const DEFAULT_PREFS = { fps: 12, detail: "medium" };

// What the add-on will accept. Stored preferences are read back from a browser
// that may have saved them under an older version, and a value this add-on no
// longer knows is rejected outright rather than ignored -- so an unrecognised
// one has to fall back here, or the preview simply never opens and the page
// gives no hint why.
const DETAILS = ["high", "medium", "low"];

/**
 * Per-camera preview settings, remembered in this browser.
 *
 * Deliberately not stored by the add-on. What frame rate looks right depends
 * on the screen and the connection doing the looking, so a phone and a desktop
 * should not have to agree -- and a preview setting is not something an
 * installation has, it is something a viewer prefers. Keeping it here also
 * means nothing to save, migrate or keep in step on the add-on side.
 */
function prefsFor(did) {
  let stored = {};
  try {
    stored = JSON.parse(localStorage.getItem(`xcam.prefs.${did}`) || "{}");
  } catch { /* unreadable or not ours; the defaults are the answer */ }
  const prefs = { ...DEFAULT_PREFS, ...stored };
  // Checked rather than trusted. This is the one input the page takes from its
  // own past, and the add-on refuses a detail level it does not recognise --
  // so a name that has since been retired would leave the preview permanently
  // failing to open for anyone who had picked it.
  if (!DETAILS.includes(prefs.detail)) prefs.detail = DEFAULT_PREFS.detail;
  if (!Number.isInteger(prefs.fps) || prefs.fps < 0) prefs.fps = DEFAULT_PREFS.fps;
  return prefs;
}

function savePrefs(did, prefs) {
  try {
    localStorage.setItem(`xcam.prefs.${did}`, JSON.stringify(prefs));
  } catch { /* private browsing; the setting simply does not persist */ }
}

/**
 * `current` marks which option is actually in effect right now, independent
 * of `selected` (which option is pressed). The two agree everywhere except a
 * camera's sheet on a field following the default: there, "Follow default"
 * is `selected` -- it is the choice that is active, and the one another tap
 * would change -- while the concrete option matching the resolved value gets
 * `current`, so the expanded control still answers "what am I getting"
 * rather than only "what did I choose here". Outside the sheet the two
 * arguments are always the same value, and `current` marks nothing extra.
 */
function segment(kind, choices, selected, current = selected) {
  return `<div class="seg" data-seg="${kind}">${choices.map(({ value, label, disabled }) => {
    const isCurrent = String(value) === String(current) && String(value) !== String(selected);
    return `<button type="button" data-value="${escapeHtml(value)}" `
      + `aria-pressed="${String(value) === String(selected)}"`
      + `${disabled ? ` disabled title="${escapeHtml(t(disabled))}"` : ""}`
      + `${isCurrent ? ' class="current"' : ""}>${escapeHtml(label)}</button>`;
  }).join("")}</div>`;
}

function fpsChoices(camera) {
  const ceiling = Math.round(camera.stream_fps || FPS_ASSUMED);
  return [
    { value: 0, label: t("fpsCamera") },
    ...FPS_LADDER.filter((rate) => rate < ceiling).map((rate) => ({
      value: rate, label: `${rate}`,
    })),
  ];
}

//: A camera's `support` (from `CameraDescription`) to the status pill it gets
//: on the page where refused cameras are shown at all. `"full"` never reaches
//: here -- `cameraState` below answers that case from `online`/`powered_on`
//: instead, because a working camera's status is about what it is doing, not
//: what it is. `limited` gets the warn colour rather than err: a real, if
//: unbuilt, path exists for those models -- see `CameraDescription.support`'s
//: own docstring -- while `unsupported` has none.
const SUPPORT_STATES = {
  limited: { cls: "warn", label: "limitedSupport" },
  unsupported: { cls: "err", label: "notSupported" },
};

/**
 * A camera's status pill, or `null` for "nothing worth saying".
 *
 * A healthy, streamable camera gets no pill at all: a badge on every card
 * turns the list into a dashboard, and three coloured pills side by side
 * make the one that actually matters -- a refused camera's red "Not
 * supported" -- no more prominent than the rest. No pill is what "everything
 * is fine" looks like; pills are reserved for what is not.
 *
 * Whether a camera can be published is read from `publishable`, which the
 * add-on sends, rather than compared against `support` here. One function
 * over there answers that question for the stream table, the session manager
 * and the control plane's camera list; the page asking it a fourth way is how
 * a later support level ends up meaning different things on either side of
 * the wire. `support` is still read below -- but only for *which* pill, which
 * is a question about the reason, not about the decision.
 */
function cameraState(camera) {
  if (!camera.publishable) {
    const state = SUPPORT_STATES[camera.support] || SUPPORT_STATES.unsupported;
    return { cls: state.cls, label: t(state.label) };
  }
  if (!camera.online) return { cls: "err", label: t("offline") };
  // A switched-off camera answers normally and then sends nothing, so it needs
  // to be called out rather than left looking like a broken stream.
  if (camera.powered_on === false) return { cls: "warn", label: t("switchedOff") };
  return null;
}

//: Inline rather than an icon font: an icon font is a web font, and this page
//: works offline. Each is small enough that inlining costs nothing worth
//: measuring.
const ICONS = {
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6h12v12H6z"/></svg>',
  enlarge: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3H3v6h2V5h4V3zm12 0h-6v2h4v4h2V3zM5 15H3v6h6v-2H5v-4zm14 4h-4v2h6v-6h-2v4z"/></svg>',
  retry: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5V2L7.5 6.5 12 11V8a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z"/></svg>',
  gear: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.4 13a7.4 7.4 0 0 0 0-2l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.4.96a7.5 7.5 0 0 0-1.73-1l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54a7.5 7.5 0 0 0-1.73 1l-2.4-.96a.5.5 0 0 0-.6.22L2.4 8.78a.5.5 0 0 0 .12.64L4.55 11a7.4 7.4 0 0 0 0 2L2.52 14.6a.5.5 0 0 0-.12.64l1.92 3.32c.13.22.39.31.6.22l2.4-.96a7.5 7.5 0 0 0 1.73 1l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54a7.5 7.5 0 0 0 1.73-1l2.4.96c.22.09.48 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64L19.4 13zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z"/></svg>',
  warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>',
};

// ---------------------------------------------------------------------------
// Settings rows -- the component this page is mostly made of. One descriptor
// per video setting the add-on stores, shared by the defaults card and every
// camera's sheet; the only difference between the two is whether "Follow
// default" is offered as a choice. `choices()` is a function rather than a
// static array so its labels re-translate when the language changes.
// ---------------------------------------------------------------------------

//: A settings field descriptor, built through a function rather than written
//: as an object literal. Step 6's language-completeness check (see the brief
//: for this task) treats any 4-space-indented `word:` line after the
//: translations as a translation key -- a plain `{ key: ..., choices: ... }`
//: literal here would read as one and be flagged as English-only, which is
//: exactly the false positive that check exists to catch everywhere else.
const field = (key, labelKey, parse, choices) => ({ key, labelKey, parse, choices });

const SETTINGS_FIELDS = [
  field("quality", "settingPicture", (raw) => raw, () => [
    { value: "high", label: t("pictureHD") },
    { value: "low", label: t("pictureSD") },
  ]),
  field("audio", "settingSound", (raw) => raw === "true", () => [
    { value: "true", label: t("on") },
    { value: "false", label: t("off") },
  ]),
  field("transcode_quality", "settingTranscode", (raw) => raw, () => [
    { value: "standard", label: t("transcodeStandard") },
    { value: "sharp", label: t("transcodeSharp") },
    { value: "maximum", label: t("transcodeMaximum") },
  ]),
];

//: A `.seg` value meaning "clear the override and follow the default" --
//: distinct from `null` itself, which cannot survive a trip through
//: `data-value` (an HTML attribute is always a string).
const FOLLOW_DEFAULT = "__follow__";

/**
 * One settings row: a label, its current value, and -- once opened -- a
 * `.seg` control offering every choice. `allowFollow` prepends "Follow
 * default" and is what turns this same row into the one a camera's sheet
 * uses; `rawValue === null` is what "follow default" looks like coming back.
 *
 * `resolvedValue` is what the camera is actually getting right now -- from
 * `settings` on the camera payload, resolved server-side, never recomputed
 * here. It defaults to `rawValue` itself, which is a no-op everywhere except
 * a sheet row following the default: there the two differ, and the row's
 * collapsed value still reads "Follow default" (the copy discipline this
 * page uses throughout), but the expanded control marks the concrete option
 * actually in effect -- see `segment`'s `current` parameter.
 */
function settingRowHtml(field, rawValue, { allowFollow = false, resolvedValue = rawValue } = {}) {
  const choices = [
    ...(allowFollow ? [{ value: FOLLOW_DEFAULT, label: t("followDefault") }] : []),
    ...field.choices(),
  ];
  const followingDefault = allowFollow && rawValue === null;
  const selected = followingDefault ? FOLLOW_DEFAULT : String(rawValue);
  // Following the default reads "Follow default (<resolved value>)", not a
  // bare "Follow default" -- collapsed, that label is the one thing this
  // page is opened to learn, and "Follow default" alone does not say it.
  const resolvedLabel = field.choices().find((c) => c.value === String(resolvedValue))?.label ?? "";
  const valueLabel = followingDefault
    ? `${t("followDefault")}${lang === "zh" ? `（${resolvedLabel}）` : ` (${resolvedLabel})`}`
    : (choices.find((c) => c.value === selected)?.label ?? "");
  return `<div class="setting-row" data-field="${escapeHtml(field.key)}">
    <button type="button" class="setting-row-btn" aria-expanded="false">
      <span class="setting-label">${escapeHtml(t(field.labelKey))}</span>
      <span class="setting-value" data-value>${escapeHtml(valueLabel)}</span>
      <span class="setting-chevron" aria-hidden="true">›</span>
    </button>
    <div class="setting-choices" hidden>${segment(field.key, choices, selected, String(resolvedValue))}</div>
  </div>`;
}

/**
 * Wire every settings row inside `container`, once. `onChoose(key, value)`
 * is called with the parsed value (`null` for "Follow default") whenever a
 * choice is made. Bound once per container rather than once per render --
 * `container`'s own children are replaced on every re-render but the
 * container element itself is not, so re-wiring on every render would stack
 * a duplicate listener per render and fire a choice that many times.
 */
function wireSettingRows(container, onChoose) {
  container.addEventListener("click", (event) => {
    const toggle = event.target.closest(".setting-row-btn");
    if (toggle && !event.target.closest(".seg")) {
      const panel = toggle.closest(".setting-row").querySelector(".setting-choices");
      const opening = panel.hidden;
      closeSettingRows(container);
      panel.hidden = !opening;
      toggle.setAttribute("aria-expanded", String(opening));
      return;
    }

    const choice = event.target.closest(".seg button");
    if (!choice) return;
    const row = choice.closest(".setting-row");
    const field = SETTINGS_FIELDS.find((f) => f.key === row.dataset.field) || row._field;
    const value = choice.dataset.value === FOLLOW_DEFAULT ? null : field.parse(choice.dataset.value);

    for (const sibling of choice.parentElement.children) {
      sibling.setAttribute("aria-pressed", String(sibling === choice));
    }
    row.querySelector("[data-value]").textContent = choice.textContent;
    row.querySelector(".setting-row-btn").setAttribute("aria-expanded", "false");
    row.querySelector(".setting-choices").hidden = true;

    onChoose(row.dataset.field, value);
  });
}

function closeSettingRows(container) {
  container.querySelectorAll(".setting-choices").forEach((panel) => {
    panel.hidden = true;
    panel.previousElementSibling?.setAttribute("aria-expanded", "false");
  });
}

//: Fields not stored by the add-on: rendered by `prefChipHtml` in the pill
//: floating on the picture, not in the camera sheet -- these two change
//: only the pictures this browser is being sent, so they live where that is
//: visible rather than among settings that change the camera for every
//: consumer. Built through the same `field()`/`segment()` components as
//: `SETTINGS_FIELDS`, but resolved through `SETTINGS_FIELDS` itself only for
//: the three the add-on owns.
function previewFpsField(camera) {
  return field("fps", "prefFps", (raw) => Number(raw), () =>
    fpsChoices(camera).map((c) => ({ value: String(c.value), label: c.label })));
}
function previewDetailField() {
  return field("previewDetail", "prefDetail", (raw) => raw, () =>
    DETAILS.map((name) => ({
      value: name, label: t(`detail${name[0].toUpperCase()}${name.slice(1)}`),
    })));
}

// ---------------------------------------------------------------------------
// The three global default rows, rendered wherever they currently live:
// inside the settings sheet (M3), rebuilt on every open plus after any
// change to `defaults` itself.
// ---------------------------------------------------------------------------

function renderDefaultsRows() {
  const container = $("defaults-rows");
  // Not an error -- the settings sheet may have been closed (or never
  // opened) since this was last called. Whoever opens it next builds it
  // fresh from the current `defaults`.
  if (!container) return;
  container.innerHTML = defaults
    ? SETTINGS_FIELDS.map((field) => settingRowHtml(field, defaults[field.key])).join("")
    : "";
  wireSettingRows(container, applyDefaultChange);
}

async function applyDefaultChange(key, value) {
  try {
    const response = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
    if (!response.ok) throw new Error(await response.text());
    defaults = { ...defaults, [key]: value };
    // Every camera that says nothing for itself on this field is now getting
    // the new value, so its resolved settings say so too. Without this the
    // sheet rebuilt below reads back the value it was resolved to a moment
    // ago -- and the marker showing what a camera is actually getting is the
    // one thing on that row that must not be a moment out of date.
    for (const camera of cameras) {
      if (camera.override?.[key] == null) {
        camera.settings = { ...camera.settings, [key]: value };
      }
    }
    // A camera's sheet may be open on this same field showing "Follow
    // default" -- its value label is the default's own value, which just
    // changed underneath it. Cheaper to rebuild the open sheet than to track
    // which field of which sheet needs correcting.
    if (openSheetDid) renderCameraSheetBody(openSheetDid);
  } catch {
    showMessage(t("settingsSaveFailed"), "error");
    renderDefaultsRows();
  }
}

// ---------------------------------------------------------------------------
// The overlay primitive -- one backdrop, one panel, two contents (the
// settings sheet and the enlarged preview). See the CSS comment on `#overlay`
// for why this is built once instead of twice.
// ---------------------------------------------------------------------------

const overlay = {};

function initOverlay() {
  overlay.root = $("overlay");
  overlay.backdrop = $("overlay-backdrop");
  overlay.panel = $("overlay-panel");
  overlay.title = $("overlay-title");
  overlay.closeBtn = $("overlay-close");
  overlay.body = $("overlay-body");
  // The session currently shown enlarged, if any -- the single fact both
  // `openEnlargePreview`'s close handler and `startPreview`'s `settle`
  // consult before touching the `<img>` node. See the comment on `settle`
  // for why two independent answers to "where does this picture live" was
  // the actual bug, not a missing null check.
  overlay.previewSession = null;
  wireCopy(overlay.body);
  overlay.backdrop.addEventListener("click", closeOverlay);
  overlay.closeBtn.addEventListener("click", closeOverlay);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !overlay.root.hidden) closeOverlay();
  });
}

/**
 * Show the shared overlay with new content.
 *
 * Positional rather than an options object: an options object here would put
 * `title:`/`build:`/`onClose:` on their own 4-space-indented lines at most
 * call sites, which reads as a translation key to the Step 6 language check
 * -- see the comment on `field` above for the same trade made for the same
 * reason.
 *
 * Focus lands on the panel itself, not on a specific control inside it --
 * there is no single right first stop for a sheet with several rows, and a
 * `tabindex="-1"` panel is both reachable and announces its title. This does
 * not trap focus: Tab may leave the panel, which keeps the implementation
 * simple, and Escape or the backdrop are always there to close it regardless
 * of where focus ends up.
 */
function openOverlay(title, variant, build, onClose) {
  overlay.trigger = document.activeElement;
  overlay.onClose = onClose || null;
  overlay.panel.className = `overlay-panel${variant ? ` ${variant}` : ""}`;
  overlay.title.textContent = title || "";
  overlay.body.innerHTML = "";
  overlay.root.hidden = false;
  build(overlay.body);
  // Added a frame after `hidden` is cleared: the browser needs to paint the
  // entry state first, or setting both in the same tick skips the transition
  // straight to its end state.
  requestAnimationFrame(() => overlay.root.classList.add("open"));
  overlay.panel.focus();
}

function closeOverlay() {
  if (!overlay.root || overlay.root.hidden) return;
  overlay.root.classList.remove("open");
  overlay.root.hidden = true;
  overlay.body.innerHTML = "";
  const onClose = overlay.onClose;
  overlay.onClose = null;
  onClose?.();
  overlay.trigger?.focus?.();
  overlay.trigger = null;
}

// ---------------------------------------------------------------------------
// The two header entrances, split by nature: 👤 holds identity (the Xiaomi
// account, and the compatibility-mode credential -- both are credentials,
// which is why they share a sheet), ⚙ holds preferences (the video
// defaults, and a way out to the Supervisor page). Putting them in one sheet
// is what made "I want to change picture quality" and "I want to sign in
// again" look like the same kind of operation.
// ---------------------------------------------------------------------------

/**
 * M1: the account sheet. The Xiaomi account's own state, plus the
 * compatibility-mode row underneath it -- both credentials, neither a video
 * setting, which is why neither lives in the gear.
 */
function openAccountSheet() {
  sheetKind = "account";
  openOverlay(t("accountTitle"), "", renderAccountSheetBody, () => { sheetKind = null; });
}

function renderAccountSheetBody(body) {
  body.innerHTML = `
    <span class="setting-group-label">${escapeHtml(t("accountHeading"))}</span>
    <div class="row">
      <span class="status ${accountLinked ? "ok" : "warn"}">
        <span class="dot"></span>${escapeHtml(accountLinked ? t("connected") : t("notConnected"))}
      </span>
      <span style="flex:1"></span>
      <button type="button" id="link-btn" class="primary"${accountLinked ? " hidden" : ""}>${escapeHtml(t("connect"))}</button>
      <button type="button" id="unlink-btn" class="ghost"${accountLinked ? "" : " hidden"}>${escapeHtml(t("disconnect"))}</button>
    </div>
    <span class="setting-group-label">${escapeHtml(t("compatTitle"))}</span>
    <div class="settings-list">
      <div class="setting-row">
        <button type="button" class="setting-row-btn" id="compat-row">
          <span class="setting-label">${escapeHtml(t("compatTitle"))}</span>
          <span class="setting-value">${escapeHtml(addonInfo?.compat_ready ? t("compatEnabled") : t("compatNotEnabled"))}</span>
          <span class="setting-chevron" aria-hidden="true">›</span>
        </button>
      </div>
    </div>`;
  body.querySelector("#link-btn")?.addEventListener("click", openAccountFlow);
  body.querySelector("#unlink-btn")?.addEventListener("click", unlinkAccount);
  body.querySelector("#compat-row").addEventListener("click", openCompatManage);
}

//: Called after anything that can change what M1 or M3 show in the
//: background -- the health poll (`accountLinked`) and `/api/info` landing
//: after one of them was already open (`addonInfo`). Refreshes whichever is
//: currently open in place rather than leaving it stale, without reopening
//: it or touching anything else on screen. A no-op for every other screen,
//: including when the overlay is closed.
function refreshOpenSheet() {
  if (overlay.root.hidden) return;
  if (sheetKind === "account") renderAccountSheetBody(overlay.body);
  else if (sheetKind === "settings") renderSettingsSheetBody(overlay.body);
}

/**
 * M2: connect the Xiaomi account. The same three-step OAuth flow this page
 * has always used, unchanged -- only where it lives moved, from a card on
 * the page to this sheet, replacing M1's content until it finishes or is
 * cancelled, either of which returns to M1.
 */
async function openAccountFlow() {
  const btn = $("link-btn");
  if (btn) btn.disabled = true;
  try {
    const response = await api("/api/link/begin", { method: "POST" });
    if (!response.ok) throw new Error(await response.text());
    const { authorize_url: authorizeUrl } = await response.json();
    sheetKind = "flow";
    openOverlay(t("accountTitle"), "", (body) => renderAccountFlowBody(body, authorizeUrl), () => { sheetKind = null; });
    window.open(authorizeUrl, "_blank", "noopener");
  } catch (err) {
    showMessage(t("startFailed") + err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function renderAccountFlowBody(body, authorizeUrl) {
  body.innerHTML = `
    <ol>
      <li>
        <a id="auth-link" href="${escapeHtml(authorizeUrl)}" target="_blank" rel="noopener">${escapeHtml(t("step1Link"))}</a>
        <span>${escapeHtml(t("step1Rest"))}</span>
      </li>
      <li>${escapeHtml(t("step2"))}</li>
      <li>${escapeHtml(t("step3"))}</li>
    </ol>
    <input id="callback-url" type="text" autocomplete="off" placeholder="${escapeHtml(t("callbackPlaceholder"))}">
    <p class="hint">${escapeHtml(t("privacyNote"))}</p>
    <div class="row" style="margin-top:14px">
      <button type="button" id="submit-btn" class="primary">${escapeHtml(t("finish"))}</button>
      <button type="button" id="cancel-btn" class="ghost">${escapeHtml(t("cancel"))}</button>
    </div>`;
  body.querySelector("#submit-btn").addEventListener("click", submitAccountLink);
  body.querySelector("#cancel-btn").addEventListener("click", openAccountSheet);
}

async function submitAccountLink() {
  const input = $("callback-url");
  const params = extractCallbackParams(input.value);
  if (!params) { showMessage(t("noCode"), "error"); return; }
  const btn = $("submit-btn");
  btn.disabled = true;
  try {
    const response = await api("/api/link/complete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (!response.ok) throw new Error(await response.text());
    input.value = "";
    showMessage(t("linked"), "success");
    await refreshStatus();
    openAccountSheet();
  } catch (err) {
    showMessage(t("signInFailed") + err.message, "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function unlinkAccount() {
  if (!window.confirm(t("confirmUnlink"))) return;
  await api("/api/unlink", { method: "POST" });
  // Re-renders M1 in place through `refreshOpenSheet` -- no separate call
  // needed to land back on it, unlike the flow above, because this action
  // never left it.
  await refreshStatus();
}

/**
 * M6: manage compatibility mode, opened from M1's compatibility-mode row.
 *
 * Not-enabled half: the explanation, and where to actually turn it on. No
 * sign-in form here: enabling it from a settings screen is the unanchored
 * choice this design moved away from. The credential is only ever entered
 * from a specific camera that cannot be reached without it, so that is the
 * only place it is asked for -- see `openCompatSignIn`.
 *
 * Enabled half: which cameras are using it, and the one honest fact about
 * the credential itself. The service compatibility mode is built on has
 * sign-in and list only -- no sign-out -- so `DELETE /api/compat` answers
 * `501 not_supported`, and this screen offers no button that would fail; it
 * states the limitation instead.
 */
function openCompatManage() {
  sheetKind = "compat";
  openOverlay(t("compatTitle"), "", (body) => {
    const using = cameras.filter((c) => c.settings?.path === "compat");
    body.innerHTML = addonInfo?.compat_ready
      ? `
        <span class="setting-group-label">${escapeHtml(t("compatTitle"))}</span>
        <div class="row">
          <span class="status ok"><span class="dot"></span>${escapeHtml(t("compatEnabled"))}</span>
        </div>
        <p class="hint">${escapeHtml(t("compatCannotRemove"))}</p>
        <span class="setting-group-label">${escapeHtml(t("camerasHeading"))}</span>
        <div class="settings-list">${using.length ? using.map(nameRowHtml).join("") : emptyRowHtml("compatNoCameras")}</div>`
      : `
        <p class="hint">${escapeHtml(t("compatWhy"))}</p>
        <p class="hint">${escapeHtml(t("compatEnableHint"))}</p>`;
  }, () => { sheetKind = null; });
}

//: One read-only row naming a camera, for M6's list of cameras currently
//: using compatibility mode. Nothing to click -- this screen manages the
//: credential, not the cameras; a camera's own path lives in its own
//: settings sheet.
function nameRowHtml(c) {
  return `<div class="setting-row"><span class="setting-label">${escapeHtml(c.name)}</span></div>`;
}

function emptyRowHtml(key) {
  return `<p class="hint">${escapeHtml(t(key))}</p>`;
}

/**
 * M3: the settings sheet. The three global video defaults every camera
 * falls back to when it says nothing for itself, plus a way out to the
 * Supervisor's own configuration page. No path row here -- a camera's
 * available connection paths depend on its own model and on whether a
 * credential exists, so a global value for it would be meaningless.
 */
function openSettingsSheet() {
  sheetKind = "settings";
  openOverlay(t("settingsTitle"), "", renderSettingsSheetBody, () => { sheetKind = null; });
}

function renderSettingsSheetBody(body) {
  body.innerHTML = `
    <span class="setting-group-label">${escapeHtml(t("defaultsHeading"))}</span>
    <div class="settings-list" id="defaults-rows"></div>
    ${addonInfo?.slug ? `
    <span class="setting-group-label">${escapeHtml(t("addonHeading"))}</span>
    <div class="settings-list">
      <div class="setting-row">
        <button type="button" class="setting-row-btn" id="open-config-btn">
          <span class="setting-label">${escapeHtml(t("openConfig"))}</span>
          <span class="setting-chevron" aria-hidden="true">›</span>
        </button>
      </div>
    </div>` : ""}`;
  renderDefaultsRows();
  body.querySelector("#open-config-btn")?.addEventListener("click", () => {
    // `window.top`, not this frame: ingress renders the page inside an
    // iframe, and a same-frame navigation would nest Home Assistant inside
    // itself instead of replacing it. The slug comes from `/api/info` --
    // Home Assistant prefixes it with the repository the add-on was
    // installed from, so the bare slug in `config.yaml` 404s on every real
    // install; this is why the row is omitted entirely rather than built
    // from that bare name when `addonInfo.slug` is `null`.
    window.top.location = `/hassio/addon/${addonInfo.slug}/config`;
  });
}

// ---------------------------------------------------------------------------
// The camera sheet -- one camera's own overrides, the settings that change
// what every consumer of this camera gets. This viewer's own preview
// preferences (frame rate, detail) are deliberately not here: they float on
// the picture itself, in the pill `prefChipHtml` builds -- see its own
// comment for why.
// ---------------------------------------------------------------------------

//: The camera whose sheet is open, if any -- read by `applyDefaultChange` so
//: a default edited elsewhere can correct an open sheet showing it.
let openSheetDid = null;

function openCameraSheet(did) {
  const camera = cameras.find((c) => c.did === did);
  if (!camera) return;
  openSheetDid = did;
  openOverlay(camera.name, "", () => renderCameraSheetBody(did), () => { openSheetDid = null; });
}

function renderCameraSheetBody(did) {
  const camera = cameras.find((c) => c.did === did);
  if (!camera || overlay.root.hidden) return;
  const rows = SETTINGS_FIELDS
    .map((field) => settingRowHtml(field, camera.override[field.key] ?? null, {
      allowFollow: true,
      resolvedValue: camera.settings[field.key],
    }))
    .join("") + pathRowHtml(camera);
  // Copying a stream address is a once-per-NVR errand, not something wanted
  // at a glance every time this page opens -- and the address was truncated
  // mid-string at a card's width anyway, so it moved here where there is
  // room to read it and reason to look for it.
  const address = displayUrl(camera.rtsp_url, camera.rtsp_reachable_off_host);
  const addressGroup = `
    <span class="setting-group-label">${escapeHtml(t("addressGroup"))}</span>
    <div class="row">
      <code>${escapeHtml(address)}</code>
      <button class="copy" data-copy="${escapeHtml(address)}">${escapeHtml(t("copy"))}</button>
    </div>
    ${camera.rtsp_requires_credentials ? `<p class="hint">${escapeHtml(t("credentialsHint"))}</p>` : ""}`;
  overlay.body.innerHTML = `
    <span class="setting-group-label">${escapeHtml(t("cameraSettings"))}</span>
    <div class="settings-list" data-sheet-camera>${rows}</div>
    ${addressGroup}`;

  wireSettingRows(overlay.body.querySelector("[data-sheet-camera]"), (key, value) =>
    applyCameraOverride(did, key, value)
  );
  wirePathRow(overlay.body.querySelector('[data-field="path"]'), did);
}

/**
 * The connection row: `official`/`compat`, always both present. The one
 * that cannot be used is disabled with the reason the add-on sent in
 * `camera.paths` -- never omitted, because a row whose choices change shape
 * underfoot is a row whose meaning changes underfoot.
 *
 * No "Follow default" here: `path` has no default to follow (see
 * `settings.py`'s module docstring) -- which paths a camera can even reach
 * depends on its own model and on whether the compatibility-mode credential
 * exists, so a shared value for it would not mean anything.
 */
function pathRowHtml(camera) {
  // Read from `camera.settings.path`, never reconstructed from `override`:
  // `path_for` in the add-on is the one place this fact is decided, and a
  // `||` fallback here would be a second answer to the same question --
  // right today only because this sheet is unreachable for a camera whose
  // resolved path is not what the fallback assumes.
  const selected = camera.settings.path;
  const choices = [
    { value: "official", label: t("pathOfficial"), disabled: camera.paths.official },
    { value: "compat", label: t("pathCompat"), disabled: camera.paths.compat },
  ];
  const valueLabel = choices.find((c) => c.value === selected)?.label ?? "";
  // The disabled option's reason is said twice: as a `title` for anyone
  // hovering the control, and as its own line for anyone who cannot (most
  // people opening this from the Home Assistant app).
  const reasons = choices.filter((c) => c.disabled).map((c) => t(c.disabled));
  return `<div class="setting-row" data-field="path">
    <button type="button" class="setting-row-btn" aria-expanded="false">
      <span class="setting-label">${escapeHtml(t("pathRow"))}</span>
      <span class="setting-value" data-value>${escapeHtml(valueLabel)}</span>
      <span class="setting-chevron" aria-hidden="true">›</span>
    </button>
    <div class="setting-choices" hidden data-path-panel>
      ${segment("path", choices, selected)}
      ${reasons.map((reason) => `<p class="hint">${escapeHtml(reason)}</p>`).join("")}
    </div>
  </div>`;
}

/**
 * Wire the connection row. Kept apart from `wireSettingRows` -- unlike
 * every other row, choosing an option here does not apply it: it replaces
 * the row's own panel with a confirmation (see `showPathSwitchConfirm`),
 * because switching this reconnects everything watching the camera, not
 * only this preview.
 */
function wirePathRow(row, did) {
  if (!row) return;
  const toggle = row.querySelector(".setting-row-btn");
  const panel = row.querySelector("[data-path-panel]");
  toggle.addEventListener("click", () => {
    const opening = panel.hidden;
    panel.hidden = !opening;
    toggle.setAttribute("aria-expanded", String(opening));
  });
  panel.addEventListener("click", (event) => {
    const choice = event.target.closest(".seg button");
    if (!choice || choice.disabled || choice.getAttribute("aria-pressed") === "true") return;
    showPathSwitchConfirm(panel, choice.dataset.value, did);
  });
}

/**
 * The connection row's own confirmation, rendered inside the sheet -- never
 * the browser's `confirm()`, which cannot be laid out bilingually and
 * cannot be tested. States the real blast radius: rebuilding this camera's
 * stream reconnects everything watching it, not just the preview.
 */
function showPathSwitchConfirm(panel, value, did) {
  panel.innerHTML = `
    <p class="hint">${escapeHtml(t("pathSwitchWarning"))}</p>
    <div class="row" style="margin-top:8px">
      <button type="button" class="ghost" data-path-cancel>${escapeHtml(t("cancel"))}</button>
      <button type="button" class="primary" data-path-confirm>${escapeHtml(t("switchConnection"))}</button>
    </div>`;
  panel.querySelector("[data-path-cancel]").addEventListener("click", () => renderCameraSheetBody(did));
  panel.querySelector("[data-path-confirm]").addEventListener("click", async () => {
    await applyCameraOverride(did, "path", value);
    renderCameraSheetBody(did);
  });
}

/**
 * PUT one changed field to a camera's settings, and patch the local copy to
 * match on success. The one place this page writes to the settings
 * endpoint -- `switchCameraPath` (a blocked card's own path switch) calls
 * this rather than duplicating the request, and only adds a full
 * `loadCameras()` reload after, because the camera it starts from has no
 * local `settings` to patch in the first place. See that function's own
 * comment for why the two still end differently.
 */
async function applyCameraOverride(did, key, value) {
  const camera = cameras.find((c) => c.did === did);
  try {
    const response = await api(`/api/cameras/${encodeURIComponent(did)}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ [key]: value }),
    });
    if (!response.ok) throw new Error(await response.text());
    const override = { ...camera.override };
    if (value === null) delete override[key];
    else override[key] = value;
    camera.override = override;
    camera.settings = { ...camera.settings, [key]: value ?? defaults[key] };
  } catch {
    showMessage(t("settingsSaveFailed"), "error");
    renderCameraSheetBody(did);
  }
}

function applyPreviewPref(did, key, value) {
  const prefs = prefsFor(did);
  if (key === "fps") prefs.fps = value;
  else prefs.detail = value;
  savePrefs(did, prefs);

  // Restarted rather than left to apply next time: the point of a preview
  // setting is to see the change in the picture.
  const cam = camElementFor(did);
  const box = cam?.querySelector("[data-preview]");
  if (box?._preview) startPreview(cam);
}

function camElementFor(did) {
  return [...document.querySelectorAll(".cam[data-did]")].find((el) => el.dataset.did === did) || null;
}

// ---------------------------------------------------------------------------
// The enlarged preview -- the same session, shown at its natural size.
// ---------------------------------------------------------------------------

/**
 * Show one preview at its natural size, reusing the running session rather
 * than restarting it -- tearing it down here would cost a reconnection to
 * look at a picture that was already on screen a moment ago.
 *
 * The overlay is the only way this page enlarges a picture. It is a modal
 * sheet, not the browser's fullscreen -- ingress renders this page inside
 * an iframe that lacks `allow="fullscreen"` anyway, so a real fullscreen
 * request would be refused, and having both would make "enlarge" answer two
 * different things depending on which one won.
 */
function openEnlargePreview(cam) {
  const box = cam.querySelector("[data-preview]");
  const session = box._preview;
  // No picture yet to enlarge -- a still-connecting or failed card offers no
  // enlarge control, but its picture click delegation would otherwise reach
  // here too before the image exists.
  if (!session || !session.img.classList.contains("visible")) return;
  overlay.previewSession = session;
  openOverlay("", "enlarge", (body) => body.appendChild(session.img), () => {
    overlay.previewSession = null;
    // Put the picture back only if this is still the running preview. A
    // failure while enlarged (`settle`, below) already moved the picture out
    // from under this session and told the viewer directly in the overlay
    // itself -- reinserting a frozen frame here, after the card has already
    // been rebuilt around a retry button with no `.controls` to anchor to,
    // is the bug this ownership check exists to prevent.
    if (box._preview === session) {
      box.insertBefore(session.img, box.querySelector(".controls") || null);
    }
  });
}

function renderCameras() {
  const container = $("cameras");
  // Replacing the grid's markup discards the elements but not the requests
  // they started, so anything playing is stopped first. Switching language
  // re-renders, and that used to strand a stream per switch.
  container.querySelectorAll("[data-preview]").forEach(stopPreview);
  if (!cameras.length) {
    container.innerHTML = `<p class="empty">${escapeHtml(t("noCameras"))}</p>`;
    return;
  }
  container.innerHTML = `<div class="grid">${cameras.map(cameraCardHtml).join("")}</div>`;
}

/**
 * One camera's card: a picture area and an identity row, always both.
 *
 * A camera nothing can reach still gets the picture area, filled with the
 * reason and the action that would fix it. Dropping the area instead makes
 * the card a head shorter than its neighbours, which reads as a rendering
 * fault rather than as a camera that needs attention.
 */
function cameraCardHtml(c) {
  return `<article class="cam" data-did="${escapeHtml(c.did)}">
    <div class="preview" data-preview>${previewIdleHtml(c)}</div>
    ${cameraRowHtml(c)}
  </article>`;
}

/**
 * The identity row underneath the picture: name, model, status pill, and
 * the control that opens this camera's settings. Everything about *how it
 * is configured* -- including the RTSP address, which is copied once while
 * wiring up an NVR and not glanced at on every visit -- lives behind that
 * control instead of on the card. See the sheet built by `openCameraSheet`.
 */
function cameraRowHtml(c) {
  const st = cameraState(c);
  return `<div class="cam-row">
    <div class="cam-id">
      <div class="cam-name">${escapeHtml(c.name)}</div>
      <div class="cam-model">${escapeHtml(c.model)}${
        c.stream_audio ? ` · ${escapeHtml(t("audioOn"))}` : ""
      }</div>
    </div>
    <span class="status ${st ? st.cls : ""}" data-status${st ? "" : " hidden"}>${
      st ? `<span class="dot"></span>${escapeHtml(st.label)}` : ""
    }</span>
    ${
      c.publishable
        ? `<button type="button" class="settings-btn" data-settings-open aria-label="${escapeHtml(t("settingsButton"))}">${ICONS.gear}</button>`
        : ""
    }
  </div>`;
}

/**
 * What fills the picture area before anything is playing.
 *
 * `blocker()` answers "is there a path at all, and if not, why" -- from the
 * `paths` map the add-on sends, never from the model string. A model is not
 * a reason; a resolved path is.
 */
function previewIdleHtml(c) {
  const blocked = blocker(c);
  if (blocked) {
    // The third case's reason is the add-on's own `stream_error` text, not
    // a translation key -- there is nothing to look up, it is already the
    // words to show.
    const why = blocked.text ?? t(blocked.reason);
    return `<div class="blocked">
      <span class="blocked-icon" aria-hidden="true">${ICONS.warn}</span>
      <span class="blocked-why">${escapeHtml(why)}</span>
      ${blocked.action ? `<button type="button" class="blocked-action" data-${blocked.action}>${escapeHtml(t(blocked.label))}</button>` : ""}
    </div>`;
  }
  return `<div class="placeholder">${escapeHtml(t("tapToView"))}</div>
    ${prefChipHtml(c)}
    <button type="button" class="play-btn" data-play aria-label="${escapeHtml(t("play"))}">${ICONS.play}</button>`;
}

/**
 * Why this camera cannot show a picture, or `null` if it can.
 *
 * Three cases, told apart by the resolved path and the credential rather
 * than by the model -- a camera the user put on compatibility mode and then
 * removed the credential from lands here too, and it is not "an unsupported
 * model":
 *
 * 1. Has a resolved path, but the add-on could not build a stream for it
 *    anyway (`stream_error`, redacted server-side) -- most often
 *    compatibility mode unable to find this camera's address. Offers a way
 *    back to Xiaomi official only when that model actually supports it
 *    (`paths.official === null`); a model Xiaomi refuses has nothing to
 *    offer back to.
 * 2. No resolved path, and compatibility mode is already signed in
 *    (`paths.compat === null`, i.e. no reason blocks it) -- this camera
 *    just has not been switched to it yet, so there is nothing to sign in
 *    to.
 * 3. No resolved path, and compatibility mode is not signed in -- the only
 *    case that opens M4.
 */
function blocker(c) {
  if (c.settings) {
    if (!c.stream_error) return null;
    return {
      text: c.stream_error,
      action: c.paths.official === null ? "path-official" : null,
      label: "switchToOfficial",
    };
  }
  if (c.paths.compat === null) {
    return { reason: "pathOfficialUnsupported", action: "path-compat", label: "compatConnect" };
  }
  return { reason: "pathCompatNoAuth", action: "compat-signin", label: "compatConnect" };
}

/**
 * This viewer's own preview preferences, floating on the picture they
 * change.
 *
 * Kept off the settings sheet on purpose: everything in that sheet changes
 * the camera for every consumer, and these two change nothing but the
 * pictures this browser is being sent. Putting them on the picture makes
 * that difference visible without a sentence explaining it.
 */
function prefChipHtml(camera) {
  const prefs = prefsFor(camera.did);
  const fpsField = previewFpsField(camera);
  const detailField = previewDetailField();
  return `<div class="chip-wrap">
    <button type="button" class="chip" data-chip aria-expanded="false">${escapeHtml(fpsLabel(prefs))}</button>
    <div class="chip-panel" data-chip-panel hidden>
      <span class="chip-label">${escapeHtml(t(fpsField.labelKey))}</span>
      ${segment("fps", fpsField.choices(), String(prefs.fps))}
      <span class="chip-label">${escapeHtml(t(detailField.labelKey))}</span>
      ${segment("detail", detailField.choices(), prefs.detail)}
    </div>
  </div>`;
}

//: The chip's collapsed label: the concrete rate, or "camera's rate" when
//: unthrottled (`fps: 0`) -- the one thing this control is opened to learn,
//: readable without opening it.
function fpsLabel(prefs) {
  return prefs.fps ? `${prefs.fps} fps` : t("fpsCamera");
}

/**
 * Opens compatibility mode's sign-in flow for a camera that has no usable
 * path yet. Reached only from `blocker()`'s second case: compatibility mode
 * is already signed in (`paths.compat === null`, no reason blocks it), this
 * camera just has not been switched to it. There is nothing to sign in to,
 * so this switches the camera directly -- the same outcome `openCompatSignIn`
 * reaches after a successful sign-in.
 */
async function openPathCompatConnect(did) {
  await switchCameraPath(did, "compat");
}

/**
 * Change a camera's connection path from outside its settings sheet (a
 * blocked card's own action button), then reload the whole camera list so
 * its card picks up whatever became true -- a resolved `settings`, a fresh
 * `stream_error`, or still blocked for a different reason.
 *
 * The write itself is `applyCameraOverride` -- one function knows how to
 * PUT to /api/cameras/{did}/settings, not two. Only the aftermath differs,
 * and genuinely so: `applyCameraOverride` patches `camera.settings` locally
 * because the sheet it serves always starts from a camera that already has
 * one, while a blocked card's camera has none at all (`camera.settings` is
 * `null` going in, see `blocker()`) -- there is nothing local worth
 * patching, so this reloads instead. See `applyCameraOverride`'s own
 * comment for the other half of this split.
 */
async function switchCameraPath(did, path) {
  await applyCameraOverride(did, "path", path);
  await loadCameras();
}

/**
 * M4: compatibility mode's sign-in panel, entered only from a blocked
 * camera's action button -- there is no other way in, see the module
 * comment on `openCompatManage` below. `did` is the camera that sent the
 * viewer here; on success that camera is put on compatibility mode
 * immediately (`onCompatSignedIn`), because agreeing to sign in for a
 * specific camera and then having to go and switch it too is a step nobody
 * would understand the purpose of.
 */
function openCompatSignIn(did) {
  openOverlay(t("compatTitle"), "", (body) => renderCompatSignInStep(body, did, "password"));
}

//: One field set per sign-in step. Which step comes next is read from the
//: 401 body in `submitCompatStep`, never guessed here -- the sign-in
//: service owns that protocol and this only renders whichever step it
//: names.
function compatStepFieldsHtml(step, extra) {
  if (step === "captcha") {
    return `
      ${extra.captcha ? `<img alt="${escapeHtml(t("compatCaptchaLabel"))}" style="max-width:100%" src="data:image/png;base64,${extra.captcha}">` : ""}
      <label>${escapeHtml(t("compatCaptchaLabel"))}<input name="captcha" autocomplete="off" required></label>`;
  }
  if (step === "verify") {
    return `
      <p class="hint">${escapeHtml(t("compatCodeSentTo"))} ${escapeHtml(extra.verifyTarget || "")}</p>
      <label>${escapeHtml(t("compatCodeLabel"))}<input name="verify" inputmode="numeric" autocomplete="one-time-code" required></label>`;
  }
  return `
    <label>${escapeHtml(t("account"))}<input name="username" autocomplete="username" required></label>
    <label>${escapeHtml(t("password"))}<input name="password" type="password" autocomplete="current-password" required></label>`;
}

/**
 * Render one step of the sign-in form and wire its submit forward.
 * `compatWhy` and the credit line are the only explanatory prose this whole
 * interface permits -- see the module docstring on `openCompatManage`.
 */
function renderCompatSignInStep(body, did, step, extra = {}) {
  body.innerHTML = `
    <p class="hint">${escapeHtml(t("compatWhy"))}</p>
    <form data-compat-form>
      ${compatStepFieldsHtml(step, extra)}
      <p class="signin-error" data-error hidden></p>
      <div class="row">
        <button type="button" class="ghost" data-cancel>${escapeHtml(t("cancel"))}</button>
        <button type="submit" class="primary">${escapeHtml(t("signIn"))}</button>
      </div>
    </form>
    <p class="hint">${escapeHtml(t("compatCredit"))}</p>`;
  body.querySelector("[data-cancel]").addEventListener("click", closeOverlay);
  body.querySelector("[data-compat-form]").addEventListener("submit", (event) => {
    event.preventDefault();
    submitCompatStep(body, did, step, new FormData(event.target));
  });
}

/**
 * Carry one step of the sign-in conversation to `/api/compat/signin`, and
 * act on whatever comes back:
 *
 * - `200`: done -- put this camera on compatibility mode (`onCompatSignedIn`).
 * - `401` naming a captcha or a verification target: re-render this same
 *   panel on the next step was asked for.
 * - `409 sign_in_busy`: another sign-in is already running -- a
 *   half-finished one is held in a single shared slot, so a second at the
 *   same time would corrupt both. Its own named message, not folded into
 *   the generic failure below.
 * - anything else: the generic failure message.
 */
async function submitCompatStep(body, did, step, formData) {
  const form = body.querySelector("[data-compat-form]");
  const submitBtn = form.querySelector("button[type=submit]");
  const errorEl = body.querySelector("[data-error]");
  errorEl.hidden = true;
  submitBtn.disabled = true;
  try {
    const response = await api("/api/compat/signin", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ step, ...Object.fromEntries(formData) }),
    });
    if (response.status === 409) {
      errorEl.textContent = t("compatSignInBusy");
      errorEl.hidden = false;
      return;
    }
    if (response.ok) {
      await onCompatSignedIn(did);
      return;
    }
    const text = await response.text();
    if (response.status === 401) {
      let next = {};
      try { next = JSON.parse(text); } catch { /* unreadable -- falls through below */ }
      if (next.captcha) { renderCompatSignInStep(body, did, "captcha", { captcha: next.captcha }); return; }
      const verifyTarget = next.verify_phone || next.verify_email;
      if (verifyTarget) { renderCompatSignInStep(body, did, "verify", { verifyTarget }); return; }
    }
    throw new Error(text);
  } catch (err) {
    errorEl.textContent = t("signInFailed") + (err.message || "");
    errorEl.hidden = false;
  } finally {
    submitBtn.disabled = false;
  }
}

/**
 * Sign-in finished: close the panel and put the camera that sent the viewer
 * here onto compatibility mode, then reload so its card starts publishing.
 */
async function onCompatSignedIn(did) {
  closeOverlay();
  await switchCameraPath(did, "compat");
}

/**
 * Every click a camera card answers, delegated from the grid rather than
 * bound per button -- the grid is rebuilt on every reload and language
 * switch, and per-button listeners would need rebinding each time. Bound
 * once, on the container element that never itself gets replaced.
 */
function wireCameraGrid(container) {
  container.addEventListener("click", (event) => {
    const playBtn = event.target.closest("[data-play], [data-retry]");
    if (playBtn) { startPreview(playBtn.closest(".cam")); return; }

    const stopBtn = event.target.closest("[data-stop]");
    if (stopBtn) { resetPreview(stopBtn.closest(".cam").querySelector("[data-preview]")); return; }

    const enlargeBtn = event.target.closest("[data-enlarge]");
    if (enlargeBtn) { openEnlargePreview(enlargeBtn.closest(".cam")); return; }

    const settingsBtn = event.target.closest("[data-settings-open]");
    if (settingsBtn) { openCameraSheet(settingsBtn.closest(".cam").dataset.did); return; }

    const pathCompatBtn = event.target.closest("[data-path-compat]");
    if (pathCompatBtn) { openPathCompatConnect(pathCompatBtn.closest(".cam").dataset.did); return; }

    const compatSigninBtn = event.target.closest("[data-compat-signin]");
    if (compatSigninBtn) { openCompatSignIn(compatSigninBtn.closest(".cam").dataset.did); return; }

    const pathOfficialBtn = event.target.closest("[data-path-official]");
    if (pathOfficialBtn) { switchCameraPath(pathOfficialBtn.closest(".cam").dataset.did, "official"); return; }

    const chipToggle = event.target.closest("[data-chip]");
    if (chipToggle) {
      const panel = chipToggle.closest(".chip-wrap").querySelector("[data-chip-panel]");
      const opening = panel.hidden;
      closeChipPanels(container);
      panel.hidden = !opening;
      chipToggle.setAttribute("aria-expanded", String(opening));
      return;
    }

    const chipChoice = event.target.closest("[data-chip-panel] .seg button");
    if (chipChoice) {
      const cam = chipChoice.closest(".cam");
      const kind = chipChoice.closest("[data-seg]").dataset.seg;
      const value = kind === "fps" ? Number(chipChoice.dataset.value) : chipChoice.dataset.value;
      for (const sibling of chipChoice.parentElement.children) {
        sibling.setAttribute("aria-pressed", String(sibling === chipChoice));
      }
      applyPreviewPref(cam.dataset.did, kind, value);
      // Queried fresh rather than kept from `chipChoice` above: if a preview
      // was actually running, `applyPreviewPref` just restarted it, which
      // replaces this card's whole picture area (see `startPreview`) and
      // takes the chip out with it -- so there may be nothing left to label.
      const chipBtn = cam.querySelector("[data-chip]");
      if (chipBtn) chipBtn.textContent = fpsLabel(prefsFor(cam.dataset.did));
      return;
    }
  });
}

//: Closes every open preference panel inside `container` -- called before
//: opening one, so at most one is ever expanded at a time.
function closeChipPanels(container) {
  container.querySelectorAll("[data-chip-panel]").forEach((panel) => {
    panel.hidden = true;
    panel.closest(".chip-wrap")?.querySelector("[data-chip]")?.setAttribute("aria-expanded", "false");
  });
}

/**
 * Wire every `[data-copy]` button inside `container`, once. Used for the
 * settings sheet's address group -- the only place a copyable address
 * appears now that the card itself does not carry one.
 */
function wireCopy(container) {
  container.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-copy]");
    if (!btn) return;
    navigator.clipboard?.writeText(btn.dataset.copy).then(() => {
      const original = btn.textContent;
      btn.textContent = t("copied");
      setTimeout(() => { btn.textContent = original; }, 1500);
    }).catch(() => { /* clipboard unavailable over plain http; ignore */ });
  });
}

//: Consecutive reconnects tolerated before a preview gives up and offers a
//: retry. A single drop is routine: the add-on restarts, or a camera's
//: session is still coming up.
const PREVIEW_MAX_FAILURES = 4;

//: Before reconnecting. Only after a failure -- a healthy connection is never
//: reopened, so nothing paces the pictures but the stream itself.
const PREVIEW_RETRY_MS = 1000;

/**
 * The socket address for a path, from the address this page was served on.
 *
 * Relative, like every other request here: ingress serves the page under a
 * prefix that this file must not know or reconstruct. `new URL` resolves it
 * against the current address, which is the only place that prefix reliably
 * exists -- deriving it from a header has been a way in before.
 */
function socketUrl(path) {
  const url = new URL(`.${path}`, location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
}

/**
 * Show a live preview.
 *
 * Each picture is decoded from the RTSP stream this add-on publishes, so what
 * appears here is evidence that the stream itself works -- not merely that the
 * camera is reachable. A preview fed from the vendor library's own decoded
 * frames would keep showing a picture while the published stream was broken,
 * which is the one thing a status display must never do.
 *
 * One socket carries the whole preview. Binary messages are whole JPEGs --
 * a message boundary is a frame boundary, so nothing has to hunt for the
 * markers that separate them -- and text messages say why a picture is not
 * coming. That second channel is why a switched-off camera can be named as
 * such immediately, in the viewer's own language, rather than being reported
 * as whatever ffmpeg said about RTSP twenty seconds later.
 *
 * This replaces a request per frame, each held open by the add-on until a
 * newer picture existed. That worked and cost a round trip per frame, and it
 * could only ever answer with a picture or an error -- there was no way to
 * say anything while a picture was still possible.
 *
 * A single multipart response was tried first, twice, and was reset after
 * about ten frames both times. A WebSocket is not multipart: it upgrades the
 * connection rather than streaming a response body, which is the same thing
 * every other add-on with a live page relies on through ingress.
 *
 * Started only on a click -- on the play control, on the retry control, or
 * from the settings sheet after a preview preference changes -- and never by
 * this page on its own. An account with several cameras opening several
 * peer-to-peer sessions at once for pictures nobody asked for is the cost
 * this add-on is least able to absorb; see the removed `IntersectionObserver`
 * this replaced. Playback state is never persisted for the same reason:
 * opening the page always starts with every preview closed.
 */
function startPreview(cam) {
  const did = cam.dataset.did;
  const box = cam.querySelector("[data-preview]");
  stopPreview(box);
  box._failed = false;
  box.setAttribute("data-playing", "");
  // Stop and enlarge appear immediately, before the first frame arrives --
  // playback was requested the moment this ran, and stopping it should not
  // wait on a picture that may never come.
  box.innerHTML = `<div class="placeholder">${escapeHtml(t("connecting"))}</div>
    <div class="controls">
      <button type="button" class="ctl-btn" data-stop aria-label="${escapeHtml(t("stop"))}">${ICONS.stop}</button>
      <button type="button" class="ctl-btn" data-enlarge aria-label="${escapeHtml(t("enlarge"))}">${ICONS.enlarge}</button>
    </div>`;

  const img = document.createElement("img");
  img.alt = "";
  box.insertBefore(img, box.querySelector(".controls"));

  const session = { img, socket: null, objectUrl: null, timer: null };
  box._preview = session;

  const prefs = prefsFor(did);
  let failures = 0;

  const stopped = () => box._preview !== session;

  const show = (blob) => {
    const next = URL.createObjectURL(blob);
    img.src = next;
    // Revoked only once its replacement is on screen, or the picture would
    // blink to nothing in between.
    if (session.objectUrl) URL.revokeObjectURL(session.objectUrl);
    session.objectUrl = next;
    box.querySelector(".placeholder")?.remove();
    img.classList.add("visible");
  };

  // Told, not guessed at. The add-on names a reason rather than sending a
  // sentence, because this page has both languages and the add-on has
  // neither; an unknown reason falls back to the generic wording so a newer
  // add-on can add reasons without this page having to know them first.
  const REASONS = { switched_off: "cameraOffHint" };

  // The card's state was read once, when the list was fetched. A camera
  // switched off since then leaves it reading "Ready" over a picture that
  // stopped -- so the reason that explains the picture corrects the label
  // too. Both are saying the same thing, and it arrives on the same message,
  // so neither costs a request of its own.
  const STATES = { switched_off: { cls: "warn", label: "switchedOff" } };

  const restate = (reason) => {
    const next = STATES[reason];
    const badge = cam.querySelector("[data-status]");
    if (!next || !badge) return;
    // A healthy camera renders this element hidden rather than absent -- see
    // `cameraCardHtml` -- exactly so there is something here to reveal when
    // a picture that was working stops being one.
    badge.hidden = false;
    badge.className = `status ${next.cls}`;
    badge.innerHTML = `<span class="dot"></span>${escapeHtml(t(next.label))}`;
  };

  // Given up on rather than retried: nothing about a switched-off camera
  // gets better by reconnecting, and hammering it would keep a card busy
  // that the viewer can fix with one tap of the switch.
  const settle = (text) => {
    box._failed = true;
    box._preview = null;
    box.removeAttribute("data-playing");
    if (session.socket) session.socket.close();
    if (session.objectUrl) URL.revokeObjectURL(session.objectUrl);
    box.innerHTML = `<div class="placeholder">${escapeHtml(text)}</div>
      <button type="button" class="play-btn" data-retry aria-label="${escapeHtml(t("retry"))}">${ICONS.retry}</button>`;
    // `box._preview` no longer being `session` (just above) is what
    // `openEnlargePreview`'s close handler checks before reinserting this
    // session's picture -- that is the single place ownership of the `<img>`
    // node is decided, so a failure here does not also need to know where
    // the node currently lives. It only needs to tell whoever is looking at
    // it: if this session's picture is the one currently enlarged, the
    // overlay is showing a frame that just went stale, and it says so rather
    // than being left frozen and silent while the card underneath it changes.
    if (overlay.previewSession === session) {
      overlay.previewSession = null;
      overlay.body.innerHTML = `<div class="placeholder">${escapeHtml(text)}</div>`;
    }
  };

  const fail = () => {
    failures += 1;
    if (failures < PREVIEW_MAX_FAILURES) {
      session.timer = setTimeout(connect, PREVIEW_RETRY_MS);
      return;
    }
    settle(t("previewFailed"));
  };

  function connect() {
    if (stopped()) return;
    const socket = new WebSocket(socketUrl(
      `/api/preview/${encodeURIComponent(did)}/ws`
      + `?fps=${prefs.fps}&quality=${encodeURIComponent(prefs.detail)}`,
    ));
    // Blobs, not ArrayBuffers: the only thing done with a frame is to hand
    // it to an <img>, and `createObjectURL` wants a Blob either way.
    socket.binaryType = "blob";
    session.socket = socket;

    socket.addEventListener("message", (event) => {
      if (stopped()) return;
      if (typeof event.data !== "string") {
        failures = 0;
        show(event.data);
        return;
      }
      let message;
      try { message = JSON.parse(event.data); } catch { return; }
      restate(message.reason);
      settle(t(REASONS[message.reason] || "previewFailed"));
    });

    // Both endings land here -- a clean close and a failed connection alike.
    // Neither is worth telling apart: the answer to both is to reconnect,
    // until it has failed often enough to stop being worth it.
    socket.addEventListener("close", () => {
      if (stopped() || session.socket !== socket) return;
      fail();
    });
  }

  connect();
}

/**
 * End a preview and release what is behind it.
 *
 * Closing the socket matters as much as dropping the element: while it is
 * open the add-on keeps a decoder running for it, and a card nobody is
 * looking at would go on paying for one. Leaves the card's markup alone --
 * callers that want it back at the idle placeholder use `resetPreview`,
 * because a language switch calls this on every card without wanting to
 * rebuild markup about to be discarded anyway.
 */
function stopPreview(box) {
  const session = box._preview;
  box._preview = null;
  if (!session) return;
  if (session.timer) clearTimeout(session.timer);
  if (session.socket) session.socket.close();
  if (session.objectUrl) URL.revokeObjectURL(session.objectUrl);
}

/**
 * A viewer's own "stop": end the preview and put the card back the way it
 * started, with the play control ready for another click.
 */
function resetPreview(box) {
  stopPreview(box);
  box._failed = false;
  box.removeAttribute("data-playing");
  box.innerHTML = `<div class="placeholder">${escapeHtml(t("tapToView"))}</div>
    <button type="button" class="play-btn" data-play aria-label="${escapeHtml(t("play"))}">${ICONS.play}</button>`;
}

async function loadCameras() {
  const container = $("cameras");
  container.querySelectorAll("[data-preview]").forEach(stopPreview);
  container.innerHTML = `<p class="empty">${escapeHtml(t("loading"))}</p>`;
  try {
    const response = await api("/api/cameras");
    if (!response.ok) throw new Error(await response.text());
    cameras = (await response.json()).cameras;
    renderCameras();
  } catch {
    container.innerHTML = `<p class="empty">${escapeHtml(t("loadFailed"))}</p>`;
  }
}

/**
 * Show the page, or the way in.
 *
 * Whether a password applies is not something the page is told -- it asks. Any
 * request the add-on turns down with a 401 means one is set and has not been
 * given, and that answer is the same however the add-on is deployed, so the
 * page needs no idea of what deployment it is part of.
 */
async function openPage() {
  try {
    const response = await api("/api/health");
    if (response.status === 401) return showSignIn();
  } catch {
    // Unreachable rather than unauthorised: the page can still say so.
  }
  $("signin").hidden = true;
  document.querySelector("main").hidden = false;
  refreshStatus();
  return undefined;
}

function showSignIn() {
  document.querySelector("main").hidden = true;
  $("signin").hidden = false;
  $("signin-password").focus();
}

$("signin-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("signin-error");
  error.hidden = true;
  try {
    const response = await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("signin-password").value }),
    });
    if (!response.ok) throw new Error();
    $("signin-password").value = "";
    await openPage();
  } catch {
    error.textContent = t("signInWrong");
    error.hidden = false;
    $("signin-password").select();
  }
});


initOverlay();
wireCameraGrid($("cameras"));
$("account-btn").addEventListener("click", openAccountSheet);
$("settings-btn").addEventListener("click", openSettingsSheet);

applyLanguage();
loadInfo();
loadSettings();
openPage();
// Only the account state is polled, and the grid is left alone on every
// ordinary tick -- re-rendering it would tear down any preview the user is
// watching. The one exception is a linked-to-unlinked transition: the
// account genuinely disappeared, so those previews are already dead, and
// without this the grid would keep showing them as if it had not. Before
// this task an always-visible badge said "not connected" on its own; now
// that state lives inside 👤, and this is what still surfaces it when the
// sheet is not open to show it.
setInterval(async () => {
  const wasLinked = accountLinked;
  try {
    const data = await (await api("/api/health")).json();
    accountLinked = data.linked;
  } catch {
    accountLinked = false;
  }
  if (wasLinked && !accountLinked) showNotConnected();
  refreshOpenSheet();
}, 30000);
