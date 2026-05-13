let countdownEl = null;
let remainingSeconds = 0;

const formatCountdown = (seconds) => {
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return "即将执行";
  }

  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;

  if (hours > 0) {
    return `${hours}时${minutes}分${secs}秒`;
  }

  return `${minutes}分${secs}秒`;
};

const bindCountdown = () => {
  countdownEl = document.getElementById("next-run-countdown");
  remainingSeconds = Number.parseInt(countdownEl?.dataset.seconds || "0", 10);
  if (countdownEl) {
    countdownEl.textContent = formatCountdown(remainingSeconds);
  }
};

const shouldDelayAutoRefresh = () => {
  if (document.visibilityState !== "visible") {
    return true;
  }

  const activeTag = document.activeElement?.tagName || "";
  return ["INPUT", "TEXTAREA", "SELECT"].includes(activeTag);
};

const refreshDashboard = () => {
  if (shouldDelayAutoRefresh()) {
    return;
  }

  window.location.reload();
};

const bindWatchForm = () => {
  const watchForm = document.getElementById("watch-form");
  if (!watchForm) {
    return;
  }

  watchForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const input = document.getElementById("watch-symbol");
    const symbol = (input?.value || "").trim().toUpperCase();
    if (!symbol) {
      return;
    }

    try {
      const response = await fetch("/api/watchlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        alert(data.detail || "加入失败，请检查代码格式");
        return;
      }

      input.value = "";
      location.reload();
    } catch (_) {
      alert("网络异常，稍后再试");
    }
  });
};

const bindWatchRemovals = () => {
  document.querySelectorAll("[data-remove-symbol]").forEach((button) => {
    if (button.dataset.boundRemove === "true") {
      return;
    }

    button.dataset.boundRemove = "true";
    button.addEventListener("click", async () => {
      const symbol = button.dataset.removeSymbol;
      if (!symbol) {
        return;
      }

      try {
        const response = await fetch(`/api/watchlist/${encodeURIComponent(symbol)}`, {
          method: "DELETE",
        });

        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          alert(data.detail || "移除失败，请稍后再试");
          return;
        }

        location.reload();
      } catch (_) {
        alert("网络异常，稍后再试");
      }
    });
  });
};

const bindMovementSettingsForm = () => {
  const movementSettingsForm = document.getElementById("movement-settings-form");
  if (!movementSettingsForm) {
    return;
  }

  movementSettingsForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const readThresholds = (prefix) =>
      [1, 2, 3].map((tier) => Number.parseFloat(document.getElementById(`${prefix}-tier${tier}-pct`).value));
    const fast_drop = readThresholds("drop");
    const fast_rise = readThresholds("rise");

    try {
      const response = await fetch("/api/movement-alert-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fast_drop, fast_rise }),
      });

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        alert(data.detail || "保存失败，请检查三档阈值是否递增");
        return;
      }

      location.reload();
    } catch (_) {
      alert("网络异常，稍后再试");
    }
  });
};

const bindMobileTabs = () => {
  const tabs = Array.from(document.querySelectorAll(".mobile-tab"));
  const panels = Array.from(document.querySelectorAll("[data-panel]"));
  const activateTab = (target) => {
    tabs.forEach((tab) => tab.classList.toggle("active", tab.dataset.tabTarget === target));
    panels.forEach((panel) => panel.classList.toggle("active-panel", panel.dataset.panel === target));
    if (target === "settings") {
      const drawer = document.querySelector(".settings-drawer");
      if (drawer) {
        drawer.open = true;
      }
    }
  };

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tabTarget));
  });
};

bindCountdown();
bindWatchForm();
bindWatchRemovals();
bindMovementSettingsForm();
bindMobileTabs();

setInterval(() => {
  remainingSeconds = Math.max(remainingSeconds - 1, 0);
  if (countdownEl) {
    countdownEl.textContent = formatCountdown(remainingSeconds);
  }
}, 1000);

setInterval(refreshDashboard, 30000);
