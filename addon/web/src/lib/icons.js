//: Inline rather than an icon font: an icon font is a web font, and this page
//: works offline. Each is small enough that inlining costs nothing worth
//: measuring.
export const ICONS = {
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6h12v12H6z"/></svg>',
  enlarge: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3H3v6h2V5h4V3zm12 0h-6v2h4v4h2V3zM5 15H3v6h6v-2H5v-4zm14 4h-4v2h6v-6h-2v4z"/></svg>',
  retry: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5V2L7.5 6.5 12 11V8a5 5 0 1 1-5 5H5a7 7 0 1 0 7-7z"/></svg>',
  gear: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19.4 13a7.4 7.4 0 0 0 0-2l2.03-1.58a.5.5 0 0 0 .12-.64l-1.92-3.32a.5.5 0 0 0-.6-.22l-2.4.96a7.5 7.5 0 0 0-1.73-1l-.36-2.54a.5.5 0 0 0-.5-.42h-3.84a.5.5 0 0 0-.5.42l-.36 2.54a7.5 7.5 0 0 0-1.73 1l-2.4-.96a.5.5 0 0 0-.6.22L2.4 8.78a.5.5 0 0 0 .12.64L4.55 11a7.4 7.4 0 0 0 0 2L2.52 14.6a.5.5 0 0 0-.12.64l1.92 3.32c.13.22.39.31.6.22l2.4-.96a7.5 7.5 0 0 0 1.73 1l.36 2.54a.5.5 0 0 0 .5.42h3.84a.5.5 0 0 0 .5-.42l.36-2.54a7.5 7.5 0 0 0 1.73-1l2.4.96c.22.09.48 0 .6-.22l1.92-3.32a.5.5 0 0 0-.12-.64L19.4 13zM12 15.5A3.5 3.5 0 1 1 12 8.5a3.5 3.5 0 0 1 0 7z"/></svg>',
  warn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/></svg>',
};
