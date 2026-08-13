//: The user-facing strings, in both languages. One fact, one name -- every
//: key exists in both `en` and `zh` (the test `test_both_languages_have_the
//: _same_keys` in the Python suite holds that). Extracted verbatim from the
//: pre-Svelte app.js so no string drifted during the migration.
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
    pathCompatNoAuth: "Compatibility mode not signed in",
    pathCompatRisky: "This model may not connect",
    switchToOfficial: "Switch to Xiaomi official",
    qualityLow: "Standard", qualityHigh: "High",
    compatTitle: "Compatibility mode",
    compatEnabled: "Enabled", compatNotEnabled: "Not enabled",
    compatWhy: "Compatibility mode reaches cameras a different way and needs your Xiaomi account password. It is separate from the sign-in you already completed, and stores a long-lived credential.",
    compatConnect: "Connect with compatibility mode",
    compatCannotRemove: "Stays until the add-on is uninstalled -- which clears its other data too.",
    compatNoCameras: "No cameras yet.",
    compatSignInBusy: "Another sign-in is already running. Try again shortly.",
    compatCaptchaLabel: "Captcha",
    compatCaptchaRefresh: "Tap the picture for a new captcha",
    compatCodeLabel: "Verification code",
    compatCodeSentTo: "Code sent to",
    compatNoStep: "Xiaomi did not return a verification step. Try again.",
    account: "Account", password: "Password",
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
    save: "Save", saved: "Saved",
    pathSwitchWarning: "Changing the connection rebuilds this camera's stream -- anything watching it now, including this preview, HomeKit and a recorder, reconnects.",
    switchConnection: "Switch",
    // Camera card controls --------------------------------------------------
    play: "Play", stop: "Stop", enlarge: "Enlarge picture", close: "Close",
    settingsButton: "Settings",
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
    pathCompatNoAuth: "兼容模式未登录",
    pathCompatRisky: "这个型号可能连不上",
    switchToOfficial: "改用小米官方",
    qualityLow: "标清", qualityHigh: "高清",
    compatTitle: "兼容模式",
    compatEnabled: "已启用", compatNotEnabled: "未启用",
    compatWhy: "兼容模式用另一种方式连接摄像头，需要你的小米账号密码。这与你已完成的授权是分开的，会保存一个长期凭据。",
    compatConnect: "用兼容模式连接",
    compatCannotRemove: "会一直保留，直到卸载这个加载项 —— 卸载会连同其他数据一并清空。",
    compatNoCameras: "暂无摄像头。",
    compatSignInBusy: "已有一个登录正在进行，请稍后再试。",
    compatCaptchaLabel: "图形验证码",
    compatCaptchaRefresh: "点击图片刷新验证码",
    compatCodeLabel: "验证码",
    compatCodeSentTo: "验证码已发送至",
    compatNoStep: "小米没有返回验证步骤，请重试。",
    account: "账号", password: "密码",
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
    save: "保存", saved: "已保存",
    pathSwitchWarning: "切换连接方式会重建这台摄像头的流，正在观看的一切都会重连，包括这个预览、HomeKit 和录像。",
    switchConnection: "切换",
    // Camera card controls --------------------------------------------------
    play: "播放", stop: "停止", enlarge: "放大画面", close: "关闭",
    settingsButton: "设置",
    notSupported: "不支持", limitedSupport: "有限支持",
  },
};

//: Which language the page is being read in, as module-level reactive state
//: rather than a store: `t()` reads it during render, so any component that
//: calls `t()` re-renders when the language changes -- no manual
//: `applyLanguage()` sweep, which is how a translated string used to be left
//: stale somewhere the sweep did not reach.
const STORAGE_KEY = "xcam.lang";

let lang = $state(pickLanguage());

export function pickLanguage() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved && I18N[saved]) return saved;
  return (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function t(key) {
  return I18N[lang][key] ?? I18N.en[key] ?? key;
}

export function setLang(next) {
  lang = next;
  localStorage.setItem(STORAGE_KEY, next);
  document.documentElement.lang = next === "zh" ? "zh-Hans" : "en";
  document.title = t("title");
}

export function currentLang() {
  return lang;
}
