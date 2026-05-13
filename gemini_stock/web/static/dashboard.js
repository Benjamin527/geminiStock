let countdownEl = null;
let remainingSeconds = 0;
let refreshInFlight = false;
const statusEndpoint = document.body.dataset.statusEndpoint || "/api/status";

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

const replaceHtml = (id, html) => {
  const element = document.getElementById(id);
  if (element && typeof html === "string") {
    element.innerHTML = html;
  }
};

const applyDashboardPayload = (payload) => {
  const fragments = payload?.fragments || {};
  replaceHtml("top-status", fragments.top_status);
  replaceHtml("priority-board", fragments.priority_cards);
  replaceHtml("symbol-cards", fragments.symbol_cards);
  replaceHtml("benchmark-cards", fragments.benchmark_cards);
  replaceHtml("metric-cards", fragments.metric_cards);
  replaceHtml("cost-panel", fragments.cost_panel);
  replaceHtml("llm-rows", fragments.llm_rows);
  replaceHtml("llm-mobile-cards", fragments.llm_mobile_cards);
  replaceHtml("error-items", fragments.error_items);
  replaceHtml("alert-rows", fragments.alert_rows);
  replaceHtml("alert-mobile-cards", fragments.alert_mobile_cards);

  const warning = document.getElementById("config-warning");
  if (warning && typeof fragments.config_warning === "string") {
    warning.textContent = fragments.config_warning;
  }

  const watchPrimary = document.getElementById("watch-primary-summary");
  if (watchPrimary && typeof fragments.watch_primary === "string") {
    watchPrimary.textContent = `主监控：${fragments.watch_primary}`;
  }

  const watchBenchmark = document.getElementById("watch-benchmark-summary");
  if (watchBenchmark && typeof fragments.watch_benchmark === "string") {
    watchBenchmark.textContent = `参考监控：${fragments.watch_benchmark}`;
  }

  bindCountdown();
  bindWatchRemovals();
};

const refreshDashboard = async () => {
  if (refreshInFlight) {
    return;
  }

  refreshInFlight = true;
  try {
    const response = await fetch(statusEndpoint, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) {
      return;
    }
    applyDashboardPayload(await response.json());
  } catch (_) {
    // Keep the current view visible if refresh fails.
  } finally {
    refreshInFlight = false;
  }
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
