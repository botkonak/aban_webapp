(function () {
  "use strict";
  const A = window.Aban;
  const form = A.$("#registerForm");
  const holder = A.$("#registerFields");
  const btn = A.$("#registerBtn");
  const errBox = A.$("#registerError");
  const errText = A.$("#registerErrorText");

  A.theme.sync();
  const themeBtn = A.$("#themeBtn");
  if (themeBtn) themeBtn.addEventListener("click", A.theme.toggle);

  let fields = [];
  let passwordMin = 8;
  let loading = true;

  function showError(message) {
    errText.textContent = message;
    errBox.classList.add("on");
  }
  function clearError() {
    errBox.classList.remove("on");
    errText.textContent = "";
  }

  function fieldLabel(field) {
    const label = document.createElement("label");
    label.className = "label";
    label.setAttribute("for", "rf_" + field.key);
    label.textContent = field.label || field.key;
    if (field.required) {
      const req = document.createElement("span");
      req.className = "req";
      req.textContent = " *";
      label.appendChild(req);
    }
    return label;
  }

  function fieldHint(field) {
    if (field.key === "username") return "۳ تا ۳۲ کاراکتر؛ فقط حروف کوچک انگلیسی، عدد و زیرخط. این نام قابل تغییر نیست.";
    if (field.key === "password") return "حداقل " + passwordMin + " کاراکتر.";
    if (field.type === "tel") return "مثال: +989121234567";
    return "";
  }

  function makeField(field) {
    const wrap = document.createElement("div");
    wrap.className = "field";
    wrap.appendChild(fieldLabel(field));

    let input;
    if (field.type === "password") {
      const box = document.createElement("div");
      box.className = "password-wrap";
      input = document.createElement("input");
      input.className = "input";
      input.type = "password";
      input.autocomplete = "new-password";
      const eyeBtn = document.createElement("button");
      eyeBtn.className = "eye";
      eyeBtn.type = "button";
      eyeBtn.setAttribute("aria-label", "نمایش رمز");
      eyeBtn.innerHTML = '<svg class="icon"><use href="/assets/icons/sprite.svg#i-eye"></use></svg>';
      eyeBtn.addEventListener("click", function () {
        const hidden = input.type === "password";
        input.type = hidden ? "text" : "password";
        eyeBtn.querySelector("use").setAttribute("href", "/assets/icons/sprite.svg#i-" + (hidden ? "eye-off" : "eye"));
        input.focus();
      });
      box.appendChild(input);
      box.appendChild(eyeBtn);
      wrap.appendChild(box);
    } else {
      input = document.createElement("input");
      input.className = "input";
      input.type = field.type === "email" ? "email" : (field.type === "tel" ? "tel" : (field.type === "number" ? "number" : "text"));
      input.autocomplete = field.key === "email" ? "email" : (field.key === "phone" ? "tel" : (field.key === "first_name" ? "given-name" : (field.key === "last_name" ? "family-name" : "off")));
      if (field.key === "username" || field.type === "tel" || field.type === "email" || field.type === "number") {
        input.classList.add("mono");
      }
      if (field.type === "tel" || field.type === "number") input.inputMode = "tel";
      wrap.appendChild(input);
    }

    input.id = "rf_" + field.key;
    input.name = field.key;
    input.dataset.key = field.key;
    input.spellcheck = false;
    if (field.key === "username") {
      input.autocapitalize = "none";
      input.addEventListener("input", function () {
        input.value = input.value.toLowerCase().replace(/[^a-z0-9_]/g, "").slice(0, 32);
        clearError();
      });
    } else {
      input.addEventListener("input", clearError);
    }

    const hint = fieldHint(field);
    if (hint) {
      const hintEl = document.createElement("div");
      hintEl.className = "hint";
      hintEl.textContent = hint;
      wrap.appendChild(hintEl);
    }
    return wrap;
  }

  function render() {
    holder.innerHTML = "";
    if (!fields.length) {
      const empty = document.createElement("div");
      empty.className = "auth-closed";
      empty.textContent = "در حال حاضر فیلدی برای ثبت‌نام تعریف نشده است. با پشتیبانی در ارتباط باشید.";
      holder.appendChild(empty);
      btn.hidden = true;
      return;
    }
    fields.forEach(function (field) { holder.appendChild(makeField(field)); });
    btn.hidden = false;
  }

  A.api("/api/register/fields", { redirectOn401: false }).then(function (data) {
    loading = false;
    fields = (data && data.fields) || [];
    passwordMin = Math.max(4, parseInt((data && data.password_min) || 8, 10) || 8);
    if (!data || !data.open) {
      holder.innerHTML = "";
      const closed = document.createElement("div");
      closed.className = "auth-closed";
      closed.innerHTML = "ثبت‌نام فعلاً بسته است. برای دریافت دسترسی از منوی ربات آبان ترید وارد بخش «اشتراک تحلیل ساختاری» شوید یا بعداً دوباره تلاش کنید.";
      holder.appendChild(closed);
      btn.hidden = true;
      return;
    }
    render();
    const first = holder.querySelector("input");
    if (first) first.focus();
  }).catch(function (err) {
    loading = false;
    holder.innerHTML = "";
    showError(err.message || "دریافت فرم ثبت‌نام انجام نشد.");
  });

  function normalizePhone(value) {
    let v = String(value || "")
      .replace(/[۰-۹]/g, function (d) { return String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)); })
      .replace(/[\s\-()]/g, "");
    if (v.indexOf("00989") === 0) v = "+989" + v.slice(5);
    else if (v.indexOf("09") === 0) v = "+98" + v.slice(1);
    else if (v.indexOf("9") === 0 && v.length === 10) v = "+98" + v;
    return v;
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearError();
    if (loading) return;

    const inputs = holder.querySelectorAll("input[data-key]");
    const values = {};
    for (let i = 0; i < inputs.length; i += 1) {
      const el = inputs[i];
      const field = fields.filter(function (f) { return f.key === el.dataset.key; })[0] || {};
      let value = el.value.trim();
      if (field.required && !value) {
        showError("«" + (field.label || el.dataset.key) + "» را وارد کنید.");
        el.focus();
        return;
      }
      if (el.dataset.key === "username" && value && !/^[a-z0-9_]{3,32}$/.test(value)) {
        showError("نام کاربری باید ۳ تا ۳۲ کاراکتر و شامل حروف کوچک انگلیسی، عدد یا _ باشد.");
        el.focus();
        return;
      }
      if (el.dataset.key === "password" && value && value.length < passwordMin) {
        showError("رمز عبور باید حداقل " + passwordMin + " کاراکتر باشد.");
        el.focus();
        return;
      }
      if (field.type === "email" && value && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(value)) {
        showError("ایمیل وارد شده معتبر نیست.");
        el.focus();
        return;
      }
      if (field.type === "tel" && value) {
        value = normalizePhone(value);
        if (!/^\+989\d{9}$/.test(value)) {
          showError("شماره موبایل معتبر نیست؛ آن را با کد کشور وارد کنید. مثال: +989121234567");
          el.focus();
          return;
        }
      }
      values[el.dataset.key] = value;
    }

    const password = values.password || "";
    const username = values.username || "";
    if (password && username && password.toLowerCase() === username) {
      showError("رمز عبور نباید با نام کاربری یکسان باشد.");
      return;
    }

    A.setBusy(btn, true, "در حال ثبت‌نام…");
    try {
      const data = await A.api("/api/register", {
        method: "POST",
        body: { values: values },
        redirectOn401: false
      });
      A.toast((data && data.message) || "ثبت‌نام انجام شد.", "success");
      setTimeout(function () { location.replace("/login?registered=1"); }, 700);
    } catch (err) {
      showError(err.message || "ثبت‌نام انجام نشد.");
      A.setBusy(btn, false);
      if (err.status === 409) {
        const uname = holder.querySelector('#rf_username');
        if (uname) uname.focus();
      }
    }
  });
})();
