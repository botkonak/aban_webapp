/*
 * cms-editor.js — CKEditor 5 (v49, free/GPL build) wrapper for the Aban admin CMS.
 *
 * Self-hosted: the bundle is served from /static/ckeditor/ckeditor.js (global
 * window.CKEDITOR), its stylesheet from /static/ckeditor/ckeditor.css and the Persian
 * dictionary from /static/ckeditor/translations/fa.js. No CDN is contacted at runtime.
 *
 * What this file does:
 *   - builds the editor config (licenseKey GPL, fa UI + fa content, RTL editable)
 *   - exposes the requested toolbar (incl. Source editing, Fullscreen, HTML Embed,
 *     Page Break, Image resize, Table, Media Embed, colours, remove format, undo/redo)
 *   - adds an Aparat media-embed provider (YouTube/Vimeo ship with the build)
 *   - routes image uploads to the admin media endpoint already used by the CMS:
 *     POST /api/admin/media/upload (multipart field "file") -> item.path
 *   - registers a "preview" toolbar button (the free build has no premium Preview
 *     plugin) that renders the current content in a new tab with the public CSS.
 *
 * Public API (window.AbanCMSEditor):
 *   version            -> "49.0.1"
 *   isAvailable()      -> boolean (bundle + all required plugins present)
 *   missingPlugins()   -> array of plugin names absent from window.CKEDITOR
 *   create(host, opts) -> Promise<handle>   opts = { value, onPreview, previewStyles }
 *   handle = { editor, getData(), setData(html), focus(), destroy() }
 */
(function (global) {
  "use strict";

  var VERSION = "49.0.1";

  // Plugins resolved by name from the window.CKEDITOR namespace (same realm as `global`).
  var PLUGIN_NAMES = [
    // core / essentials
    "Essentials", "Autoformat", "Undo", "Paragraph", "Clipboard",
    // basic styles
    "Bold", "Italic", "Underline", "Strikethrough",
    // block structures
    "Heading", "List", "Link", "LinkImage",
    "Image", "ImageUpload", "ImageResize", "ImageStyle", "ImageToolbar", "ImageTextAlternative",
    "MediaEmbed", "Table", "TableToolbar", "TableProperties", "TableCellProperties", "TableCaption",
    "BlockQuote", "CodeBlock", "Alignment", "Font", "FontColor", "FontBackgroundColor",
    "RemoveFormat", "Indent",
    // productivity / advanced
    "Fullscreen", "SourceEditing", "HtmlEmbed", "PageBreak", "BalloonToolbar", "GeneralHtmlSupport"
  ];

  // Every feature asked for by the spec, minus Preview (not in the free build).
  var REQUIRED_PLUGINS = [
    "Essentials", "Bold", "Italic", "Underline", "Strikethrough", "Heading", "List", "Link",
    "Image", "ImageUpload", "ImageResize", "MediaEmbed", "Table", "BlockQuote", "CodeBlock",
    "Alignment", "FontColor", "RemoveFormat", "Undo", "Fullscreen", "SourceEditing",
    "HtmlEmbed", "PageBreak"
  ];

  var TOOLBAR = [
    "sourceEditing", "abanPreview", "|",
    "undo", "redo", "|",
    "bold", "italic", "underline", "strikethrough", "removeFormat", "|",
    "heading", "|",
    "numberedList", "bulletedList", "|",
    "link", "|",
    "insertImage", "mediaEmbed", "insertTable", "|",
    "blockQuote", "codeBlock", "htmlEmbed", "pageBreak", "|",
    "alignment", "fontColor", "fontBackgroundColor", "|",
    "fullscreen"
  ];

  // v49 heading schema: { model, view, title, class, icon } (the property is "model").
  var HEADING = {
    options: [
      { model: "paragraph", title: "پاراگراف", class: "ck-heading_paragraph" },
      { model: "heading1", view: "h1", title: "تیتر ۱", class: "ck-heading_heading1", icon: "h1" },
      { model: "heading2", view: "h2", title: "تیتر ۲", class: "ck-heading_heading2", icon: "h2" },
      { model: "heading3", view: "h3", title: "تیتر ۳", class: "ck-heading_heading3", icon: "h3" },
      { model: "heading4", view: "h4", title: "تیتر ۴", class: "ck-heading_heading4", icon: "h4" },
      { model: "heading5", view: "h5", title: "تیتر ۵", class: "ck-heading_heading5", icon: "h5" },
      { model: "heading6", view: "h6", title: "تیتر ۶", class: "ck-heading_heading6", icon: "h6" }
    ]
  };

  var FONT_COLORS = [
    "#000000", "#333333", "#757575", "#bcbcbc", "#efefef", "#ffffff",
    "#e74c3c", "#e67e22", "#f1c40f", "#2ecc71", "#16a085", "#3498db",
    "#2f6bff", "#9b59b6", "#8e44ad", "#7f8c8d"
  ];

  var BG_COLORS = [
    "transparent", "#ffffff", "#fef9e7", "#fdebd0", "#fadbd8", "#f4ecf7",
    "#eaf0ff", "#d6eaf8", "#d5f5e3", "#e5e8e8", "#fcf3cf", "#f5eef8",
    "#d4e6f1", "#d6efdf", "#f9e79f", "#e6b0aa"
  ];

  // The official fa dictionary is not 100% complete; CKEditor falls back to the
  // English source string for missing keys. Fill the gaps we can see in this toolbar.
  var FA_MISSING = {
    "Source": "منبع",
    "Source editing": "ویرایش منبع",
    "Show or hide the source editing view.": "نمایش یا پنهان‌کردن نمای منبع.",
    "Fullscreen": "تمام‌صفحه",
    "Exit fullscreen": "خروج از تمام‌صفحه",
    "Enter fullscreen": "ورود به تمام‌صفحه",
    "Choose highlight color": "انتخاب رنگ پس‌زمینه",
    "Choose text color": "انتخاب رنگ متن",
    "More colors…": "رنگ‌های بیشتر…",
    "Remove color": "برداشتن رنگ",
    "Embed HTML": "جاسازی HTML",
    "Embed HTML source": "منبع HTML جای‌داده‌شده",
    "Save changes": "ذخیره تغییرات",
    "Cancel": "لغو",
    "Page break": "شکست صفحه",
    "Insert a page break": "درج شکست صفحه",
    "Media embed": "جاسازی رسانه",
    "Insert media": "درج رسانه",
    "Paste the media URL in the input.": "نشانی رسانه را در فیلد بچسبانید.",
    "Tip: Paste the URL into the content to embed faster.": "نکته: با چسباندن نشانی در محتوا، درج رسانه سریع‌تر انجام می‌شود.",
    "Insert image via URL": "درج تصویر با نشانی",
    "Insert image": "درج تصویر",
    "Image toolbar": "نوار ابزار تصویر",
    "Image resize list": "اندازه تصویر",
    "Original": "اصلی",
    "Change image text alternative": "تغییر متن جایگزین تصویر",
    "Text alternative": "متن جایگزین",
    "Save": "ذخیره",
    "Insert table": "درج جدول",
    "Table toolbar": "نوار ابزار جدول",
    "Remove format": "برداشتن قالب",
    "Show more items": "نمایش موارد بیشتر",
    "Hide the toolbar": "پنهان‌کردن نوار ابزار",
    "Rich Text Editor": "ویرایشگر متن غنی",
    "Rich Text Editor, %0": "ویرایشگر متن غنی، %0"
  };

  function patchFaDictionary() {
    try {
      var t = global.CKEDITOR_TRANSLATIONS;
      var dict = t && t.fa && t.fa.dictionary;
      if (!dict) return;
      Object.keys(FA_MISSING).forEach(function (key) {
        if (!dict[key]) dict[key] = FA_MISSING[key];
      });
    } catch (e) { /* non-fatal */ }
  }

  function ns() { return global.CKEDITOR || null; }

  function getEditorClass() {
    var k = ns();
    return k ? (k.ClassicEditor || null) : null;
  }

  function missingPlugins() {
    var k = ns();
    if (!k) return PLUGIN_NAMES.slice();
    return PLUGIN_NAMES.filter(function (n) { return !k[n]; });
  }

  function isAvailable() {
    var k = ns();
    if (!k || !k.ClassicEditor) return false;
    return REQUIRED_PLUGINS.every(function (n) { return !!k[n]; });
  }

  // RegExp objects must be built by the same realm as window.CKEDITOR; `global.RegExp`
  // guarantees that (matters when this script is evaluated in an unusual realm).
  function rx(pattern) { return new global.RegExp(pattern); }

  // Aparat provider. CKEditor matches `url`, captures the video id and calls html()
  // with the RegExpMatchArray, so the id is match[1].
  function aparatProvider() {
    return {
      name: "Aparat",
      url: [
        rx("^(?:https?://)?(?:www\\.)?aparat\\.com/v/([A-Za-z0-9]+)/?(?:[?#].*)?$"),
        rx("aparat\\.com/v/([A-Za-z0-9]+)")
      ],
      html: function (match) {
        var id = match && match[1];
        if (!id) return "";
        var src = "https://www.aparat.com/video/video/embed/videohash/" + id + "/vt/frame";
        return '<iframe src="' + src + '" width="640" height="360" ' +
          'style="width:100%;height:auto;aspect-ratio:16 / 9;border:0;display:block" ' +
          'allowfullscreen="true" title="ویدیوی آپارات"></iframe>';
      }
    };
  }

  /* ---------------- preview (free build has no premium Preview plugin) -------------- */

  function openPreviewWindow(editor, opts) {
    var doc = global.document;
    var styles = (opts && opts.previewStyles) || ["/assets/css/public.css"];
    var title = "";
    try {
      var input = doc.querySelector('#editorForm [name="title"]');
      if (input) title = input.value || "";
    } catch (e) { /* non-fatal */ }
    var theme = "";
    try { theme = doc.documentElement.getAttribute("data-theme") || ""; } catch (e) { /* non-fatal */ }
    var html = editor.getData();
    var win = global.open("", "_blank", "width=1120,height=820,resizable=yes,scrollbars=yes");
    if (!win) return false;
    var esc = function (value) {
      return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
      });
    };
    var links = styles.map(function (href) {
      return '<link rel="stylesheet" href="' + esc(href) + '">';
    }).join("");
    var body =
      '<!doctype html><html lang="fa" dir="rtl"' + (theme ? ' data-theme="' + esc(theme) + '"' : "") + ">" +
      "<head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
      "<title>پیش‌نمایش</title>" + links + "</head>" +
      "<body><main class=\"pub-main\"><div class=\"pub-container\">" +
      "<article class=\"pub-card article\"><header class=\"article-head\">" +
      "<h1 class=\"article-title\">" + esc(title || "پیش‌نمایش محتوا") + "</h1></header>" +
      "<div class=\"prose\">" + html + "</div></article></div></main></body></html>";
    win.document.open();
    win.document.write(body);
    win.document.close();
    win.focus();
    return true;
  }

  // A tiny plugin that registers the `abanPreview` command + toolbar button.
  function makePreviewPlugin(opts) {
    var k = ns();
    if (!k || !k.Plugin || !k.ButtonView) return null;
    var PluginBase = k.Plugin;
    var ButtonView = k.ButtonView;
    var icon = k.IconEye || "";
    return class AbanPreviewPlugin extends PluginBase {
      init() {
        var editor = this.editor;
        var command = {
          execute: function () {
            if (opts && typeof opts.onPreview === "function") { opts.onPreview(editor); return; }
            openPreviewWindow(editor, opts);
          }
        };
        try {
          if (k.Command) {
            var Cmd = new k.Command(editor, command);
            editor.commands.add("abanPreview", Cmd);
          } else {
            editor.commands.add("abanPreview", command);
          }
        } catch (e) { /* non-fatal */ }
        editor.ui.componentFactory.add("abanPreview", function () {
          var button = new ButtonView(editor.locale);
          button.set({ label: "پیش‌نمایش", tooltip: true, icon: icon, withText: false });
          try { button.bind("isEnabled").to(editor.commands.get("abanPreview")); } catch (e) { /* non-fatal */ }
          button.on("execute", function () {
            try { editor.execute("abanPreview"); } catch (e) { openPreviewWindow(editor, opts); }
          });
          return button;
        });
      }
    };
  }

  /* --------------------------- config --------------------------------------------- */

  // CKEditor برای نمایش پیش‌نمایشِ «جاسازی HTML» به یک پاک‌ساز سمت مرورگر نیاز دارد
  // (بدون آن اخطار html-embed-provide-sanitize-function داده و پیش‌نمایش خاموش می‌شود).
  // این نسخه‌ی سبک، تگ‌های اجرایی و پیوند‌های خطرناک را دور می‌ریزد؛ سرور هم دوباره
  // همان محتوا را در مسیر ذخیره پاک‌سازی می‌کند.
  var EMBED_DROP_TAGS = {
    script: 1, style: 1, link: 1, meta: 1, base: 1, noscript: 1, template: 1,
    form: 1, input: 1, button: 1, select: 1, textarea: 1, option: 1, fieldset: 1,
    object: 1, embed: 1, frame: 1, frameset: 1, iframe: 1, svg: 1, math: 1
  };
  var EMBED_UNSAFE_URL = /^\s*(?:javascript|vbscript|data)\s*:/i;

  function sanitizeEmbedHtml(contents) {
    var doc;
    try {
      doc = global.document.implementation.createHTMLDocument("embed");
      doc.body.innerHTML = String(contents || "");
    } catch (e) { return ""; }
    var all = Array.prototype.slice.call(doc.body.querySelectorAll("*"));
    all.forEach(function (el) {
      if (EMBED_DROP_TAGS[String(el.nodeName).toLowerCase()]) {
        if (el.parentNode) el.parentNode.removeChild(el);
        return;
      }
      Array.prototype.slice.call(el.attributes).forEach(function (attr) {
        var name = String(attr.name || "").toLowerCase();
        var value = String(attr.value || "");
        if (/^on/.test(name) || name === "style" || name === "srcdoc") { el.removeAttribute(name); return; }
        if ((name === "href" || name === "src") && EMBED_UNSAFE_URL.test(value)) el.setAttribute(name, "#");
      });
    });
    return doc.body.innerHTML;
  }

  // قرارداد CKEditor 5 (v49): تابع sanitizeHtml شیء { html, hasChanged } برمی‌گرداند.
  function embedSanitizeHtml(contents) {
    return { html: sanitizeEmbedHtml(contents), hasChanged: false };
  }

  function buildConfig(opts) {
    var k = ns();
    opts = opts || {};
    var plugins = PLUGIN_NAMES.map(function (n) { return k[n]; }).filter(Boolean);
    var toolbar = TOOLBAR.slice();
    var Preview = makePreviewPlugin(opts);
    if (Preview) {
      plugins.push(Preview);
    } else {
      toolbar = toolbar.filter(function (i) { return i !== "abanPreview"; });
    }
    return {
      licenseKey: "GPL",
      language: { ui: "fa", content: "fa" },
      plugins: plugins,
      toolbar: toolbar,
      heading: HEADING,
      mediaEmbed: { previewsInData: true, extraProviders: [aparatProvider()] },
      image: {
        resizeUnit: "%",
        resizeOptions: [
          { name: "resizeImage:original", label: "اندازه اصلی", value: null },
          { name: "resizeImage:50", label: "۵۰٪", value: "50" },
          { name: "resizeImage:75", label: "۷۵٪", value: "75" }
        ],
        toolbar: [
          "imageStyle:alignLeft", "imageStyle:alignCenter", "imageStyle:alignRight", "|",
          "resizeImage", "|", "toggleImageCaption", "imageTextAlternative"
        ],
        upload: { showProgressBar: true }
      },
      table: { defaultColumns: 3, defaultRows: 3 },
      alignment: { options: ["left", "center", "right", "justify"] },
      fontColor: { columns: 8, documentColors: FONT_COLORS },
      fontBackgroundColor: { columns: 8, documentColors: BG_COLORS },
      htmlEmbed: { showPreviews: true, sanitizeHtml: embedSanitizeHtml },
      link: { addTargetToExternalLinks: true }
    };
  }

  /* --------------------------- upload adapter ------------------------------------- */

  // Uses the endpoint the CMS media library already calls:
  //   POST /api/admin/media/upload  (multipart field "file")
  // response: { ok, item: { path: "/uploads/media/YYYY/MM/<uuid>.<ext>" } }
  // The stored URL is server-relative, exactly like the media library rows.
  function makeUploadAdapter(loader) {
    // admin.js exposes the shared helpers as window.Aban (A is only a local alias).
    var A = global.Aban || global.A;
    return {
      upload: function () {
        return loader.file.then(function (file) {
          if (!A || typeof A.api !== "function") {
            throw new Error("سرویس آپلود در دسترس نیست.");
          }
          var fd = new global.FormData();
          fd.append("file", file);
          fd.append("alt_text", file && file.name ? file.name : "");
          fd.append("title", file && file.name ? file.name : "");
          return A.api("/api/admin/media/upload", { method: "POST", body: fd });
        }).then(function (res) {
          var path = res && res.item && res.item.path;
          if (!path) throw new Error("آدرس تصویر از سرور دریافت نشد.");
          return { default: path };
        });
      },
      abort: function () { /* no-op */ }
    };
  }

  function applyRtl(editor) {
    try {
      var editable = editor.ui.getEditableElement();
      if (editable) {
        editable.setAttribute("dir", "rtl");
        editable.setAttribute("lang", "fa");
      }
    } catch (e) { /* non-fatal */ }
  }

  function wireUploads(editor) {
    try {
      var k = ns();
      var FR = k.FileRepository ? editor.plugins.get(k.FileRepository) : editor.plugins.get("FileRepository");
      if (FR) FR.createUploadAdapter = function (loader) { return makeUploadAdapter(loader); };
    } catch (e) { /* non-fatal */ }
  }

  function configureGhs(editor) {
    // Keep the classes/inline styles CKEditor itself emits (image_resized, page-break,
    // raw-html-embed, text-align, colour…) intact through paste round-trips.
    try {
      var ghs = editor.plugins.get("GeneralHtmlSupport");
      if (!ghs) return;
      ghs.allowAttributes(["class", "style", "dir", "lang", "data-oembed-url"]);
      ghs.allowClasses(["page-break", "raw-html-embed", "image", "image_resized", "table", "media", "ck-widget", "text-*"]);
    } catch (e) { /* non-fatal */ }
  }

  /* ------------------------------ create ------------------------------------------ */

  function create(hostEl, opts) {
    opts = opts || {};
    var Cls = getEditorClass();
    if (!Cls) return Promise.reject(new Error("CKEditor بارگذاری نشده است؛ مسیر /static/ckeditor در دسترس نیست."));
    if (!hostEl) return Promise.reject(new Error("عنصر میزبان ویرایشگر یافت نشد."));
    patchFaDictionary();
    return Cls.create(hostEl, buildConfig(opts)).then(function (editor) {
      applyRtl(editor);
      wireUploads(editor);
      configureGhs(editor);
      if (typeof opts.value === "string" && opts.value) {
        try { editor.setData(opts.value); } catch (e) { /* non-fatal */ }
      }
      return {
        editor: editor,
        getData: function () { return editor.getData(); },
        setData: function (html) { try { editor.setData(html || ""); } catch (e) { /* non-fatal */ } },
        focus: function () { try { editor.editing.view.focus(); } catch (e) { /* non-fatal */ } },
        destroy: function () { try { return editor.destroy(); } catch (e) { return global.Promise.resolve(); } }
      };
    });
  }

  global.AbanCMSEditor = {
    version: VERSION,
    isAvailable: isAvailable,
    missingPlugins: missingPlugins,
    toolbar: TOOLBAR.slice(),
    aparatProvider: aparatProvider,
    create: create
  };
})(window);
