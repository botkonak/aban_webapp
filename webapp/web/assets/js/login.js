(function () {
  "use strict";
  const A = window.Aban;
  const form = A.$("#loginForm");
  const input = A.$("#password");
  const userInput = A.$("#username");
  const btn = A.$("#loginBtn");
  const errBox = A.$("#loginError");
  const errText = A.$("#loginErrorText");
  const state = A.$("#loginState");
  const toggle = A.$("#togglePass");
  const eye = A.$("#eyeIcon");

  const tabs = A.$("#authTabs");
  const otpTab = A.$("#otpTab");
  const otpForm = A.$("#otpForm");
  const otpErrBox = A.$("#otpError");
  const otpErrText = A.$("#otpErrorText");
  const phoneInput = A.$("#phone");
  const otpSendBtn = A.$("#otpSendBtn");
  const otpCodeField = A.$("#otpCodeField");
  const otpCode = A.$("#otpCode");
  const otpVerifyBtn = A.$("#otpVerifyBtn");
  const otpHint = A.$("#otpHint");

  let cooldownTimer = null;
  let cooldownLeft = 0;
  let otpSent = false;

  A.theme.sync();
  const themeBtn = A.$("#themeBtn");
  if (themeBtn) themeBtn.addEventListener("click", A.theme.toggle);

  const params = new URLSearchParams(location.search);

  A.api("/api/auth/session", { redirectOn401: false }).then(function (data) {
    if (data && data.authenticated) {
      location.replace(data.user && data.user.role === "admin" ? "/admin" : "/");
      return;
    }
    if (params.get("admin") === "1") {
      state.innerHTML = "ورود مدیر فقط با لینک اختصاصی داخل ربات انجام می‌شود. اگر لینک را تازه دریافت کرده‌اید، دوباره روی آن بزنید.";
      if (params.get("state") === "invalid") {
        showError("لینک ورود مدیر معتبر نیست یا منقضی شده است. از ربات لینک جدید بگیرید.");
      }
    } else if (params.get("registered") === "1") {
      state.innerHTML = "ثبت‌نام شما انجام شد. اکنون با نام کاربری و رمز عبور وارد شوید.";
    }
  }).catch(function () {});

  A.api("/api/register/fields", { redirectOn401: false }).then(function (data) {
    if (data && data.otp_enabled && otpTab) otpTab.hidden = false;
  }).catch(function () {});

  function showError(message) {
    errText.textContent = message;
    errBox.classList.add("on");
  }
  function clearError() {
    errBox.classList.remove("on");
    errText.textContent = "";
  }
  function showOtpError(message) {
    otpErrText.textContent = message;
    otpErrBox.classList.add("on");
  }
  function clearOtpError() {
    otpErrBox.classList.remove("on");
    otpErrText.textContent = "";
  }

  function switchTab(name) {
    if (!tabs) return;
    Array.prototype.forEach.call(tabs.querySelectorAll(".seg"), function (el) {
      el.classList.toggle("active", el.getAttribute("data-tab") === name);
    });
    const isOtp = name === "otp";
    form.hidden = isOtp;
    otpForm.hidden = !isOtp;
    clearError();
    clearOtpError();
    (isOtp ? (otpSent ? otpCode : phoneInput) : (userInput && userInput.value ? input : (userInput || input))).focus();
  }

  if (tabs) {
    tabs.addEventListener("click", function (e) {
      const tab = e.target.closest("[data-tab]");
      if (!tab || tab.hidden) return;
      switchTab(tab.getAttribute("data-tab"));
    });
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      const hidden = input.type === "password";
      input.type = hidden ? "text" : "password";
      eye.querySelector("use").setAttribute("href", "/assets/icons/sprite.svg#i-" + (hidden ? "eye-off" : "eye"));
      input.focus();
    });
  }

  input.addEventListener("input", clearError);
  if (userInput) userInput.addEventListener("input", clearError);
  phoneInput.addEventListener("input", function () {
    clearOtpError();
    if (otpSent) resetOtpCode();
  });
  otpCode.addEventListener("input", function () {
    clearOtpError();
    otpCode.value = toEnglishDigits(otpCode.value).replace(/[^\d]/g, "").slice(0, 8);
  });

  function toEnglishDigits(value) {
    return String(value || "")
      .replace(/[۰-۹]/g, function (d) { return String("۰۱۲۳۴۵۶۷۸۹".indexOf(d)); })
      .replace(/[٠-٩]/g, function (d) { return String("٠١٢٣٤٥٦٧٨٩".indexOf(d)); });
  }

  function normPhone(value) {
    let v = toEnglishDigits(value).replace(/[\s\-()]/g, "");
    if (v.indexOf("00989") === 0) v = "+989" + v.slice(5);
    else if (v.indexOf("+98") === 0) v = "+98" + v.slice(3);
    else if (v.indexOf("98") === 0 && v.length === 12) v = "+" + v;
    else if (v.indexOf("09") === 0) v = "+98" + v.slice(1);
    else if (v.indexOf("9") === 0 && v.length === 10) v = "+98" + v;
    return v;
  }

  form.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearError();
    const password = input.value.trim();
    const username = userInput ? userInput.value.trim().toLowerCase() : "";
    if (!password) {
      showError("رمز عبور را وارد کنید.");
      input.focus();
      return;
    }
    if (username && !/^[a-z0-9_]{3,32}$/.test(username)) {
      showError("نام کاربری باید ۳ تا ۳۲ کاراکتر و شامل حروف انگلیسی، عدد یا _ باشد.");
      userInput.focus();
      return;
    }
    A.setBusy(btn, true, "در حال ورود…");
    try {
      const body = { password: password };
      if (username) body.username = username;
      const data = await A.api("/api/auth/login", {
        method: "POST",
        body: body,
        redirectOn401: false
      });
      location.replace((data && data.redirect) || "/");
    } catch (err) {
      const custom = A.brand && A.brand.login && A.brand.login.error;
      const message = err.status === 401 && custom ? custom : (err.message || "ورود انجام نشد.");
      showError(message);
      A.setBusy(btn, false);
      input.select();
    }
  });

  function tickCooldown() {
    clearInterval(cooldownTimer);
    if (cooldownLeft <= 0) {
      otpSendBtn.disabled = false;
      otpSendBtn.textContent = otpSent ? "ارسال دوباره کد" : "ارسال کد یکبار مصرف";
      return;
    }
    otpSendBtn.disabled = true;
    otpSendBtn.textContent = "ارسال دوباره تا " + cooldownLeft + " ثانیه";
    cooldownTimer = setInterval(function () {
      cooldownLeft -= 1;
      if (cooldownLeft <= 0) {
        clearInterval(cooldownTimer);
        otpSendBtn.disabled = false;
        otpSendBtn.textContent = otpSent ? "ارسال دوباره کد" : "ارسال کد یکبار مصرف";
        return;
      }
      otpSendBtn.textContent = "ارسال دوباره تا " + cooldownLeft + " ثانیه";
    }, 1000);
  }

  function resetOtpCode() {
    otpSent = false;
    otpCodeField.hidden = true;
    otpVerifyBtn.hidden = true;
    otpCode.value = "";
    otpHint.hidden = true;
  }

  otpSendBtn.addEventListener("click", async function () {
    clearOtpError();
    const phone = normPhone(phoneInput.value);
    if (!/^\+989\d{9}$/.test(phone)) {
      showOtpError("شماره موبایل معتبر نیست؛ آن را با کد کشور وارد کنید. مثال: +989121234567");
      phoneInput.focus();
      return;
    }
    A.setBusy(otpSendBtn, true, "در حال ارسال…");
    try {
      const data = await A.api("/api/auth/otp/request", {
        method: "POST",
        body: { phone: phone },
        redirectOn401: false
      });
      otpSent = true;
      phoneInput.value = phone;
      otpCodeField.hidden = false;
      otpVerifyBtn.hidden = false;
      otpHint.hidden = false;
      otpHint.textContent = "کد به شماره " + ((data && data.phone) || phone) + " ارسال شد و تا " +
        Math.round(((data && data.expires_in) || 120) / 60) + " دقیقه اعتبار دارد.";
      A.setBusy(otpSendBtn, false);
      cooldownLeft = Math.max(10, parseInt((data && data.cooldown) || 60, 10) || 60);
      tickCooldown();
      otpCode.focus();
      A.toast("کد یکبار مصرف ارسال شد.", "success");
    } catch (err) {
      A.setBusy(otpSendBtn, false);
      showOtpError(err.message || "ارسال کد انجام نشد.");
      if (err.status === 429 && err.data) {
        const wait = parseInt(err.data.retry_after || 0, 10);
        if (wait > 0) {
          cooldownLeft = wait;
          tickCooldown();
        }
      }
    }
  });

  otpForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearOtpError();
    const phone = normPhone(phoneInput.value);
    const code = toEnglishDigits(otpCode.value).replace(/[^\d]/g, "");
    if (!/^\+989\d{9}$/.test(phone)) {
      showOtpError("شماره موبایل معتبر نیست.");
      phoneInput.focus();
      return;
    }
    if (code.length < 4) {
      showOtpError("کد یکبار مصرف را کامل وارد کنید.");
      otpCode.focus();
      return;
    }
    A.setBusy(otpVerifyBtn, true, "در حال بررسی…");
    try {
      const data = await A.api("/api/auth/otp/verify", {
        method: "POST",
        body: { phone: phone, code: code },
        redirectOn401: false
      });
      location.replace((data && data.redirect) || "/");
    } catch (err) {
      showOtpError(err.message || "ورود انجام نشد.");
      A.setBusy(otpVerifyBtn, false);
      otpCode.select();
    }
  });
})();
