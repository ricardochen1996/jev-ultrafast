const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="demo-token"]').content;
let state = null,
  busy = false,
  automatic = false,
  shownRun = null,
  listedRun = null;

// ---------------------------------------------------------------------------
// Two languages, one dictionary. Static labels are applied by id; dynamic text
// is produced through t(). Server messages are translated by exact match, then
// by pattern, and are shown verbatim when nothing matches.
// ---------------------------------------------------------------------------
const STRINGS = {
  en: {
    title: "Browser Ultrafast",
    localBrowser: "Local demo browser",
    emptyHint: "Enter a page and a goal, then start a run.",
    noPage: "No page yet",
    urlPlaceholder: "https://example.com/page — http or https only",
    goalPlaceholder: "Describe the task for this page, and what should be visible when it is done.",
    reuse: "Use my open tab",
    reuseHint: "Drive the tab you already have open at this address, instead of opening a new one.",
    start: "Start run",
    slow: "Slow motion",
    targets: "Targets",
    choose: "Choose next",
    execute: "Execute choice",
    auto: "Run automatically",
    stop: "Pause",
    nextAction: "NEXT ACTION",
    waitingPage: "Waiting for a page",
    chooseAction: "Choose an action",
    elements: (n) => `${n} elements`,
    options: (n) => `${n} options`,
    decisionTime: "Decision time",
    targetConfidence: "Target confidence",
    operation: "Operation",
    indexed: "Indexed elements",
    ranked: "Ranked by Jev",
    unranked: "Choose next to rank",
    choicesEmpty: "Available actions will appear here.",
    runs: "Runs",
    recorded: (n) => `${n} recorded`,
    unavailable: "unavailable",
    runsEmpty: "Every run is saved locally. Click one to review its trail.",
    trail: "Decision trail",
    trailEmpty: "Each executed action leaves an observed result.",
    export: "Export trace ↓",
    modelSees: "What the model sees",
    modelStateIdle: "Start a run to inspect its structured state.",
    runDetail: "RUN DETAIL",
    close: "Close",
    runTitle: "Run",
    statusMeta: "Status",
    goalMeta: "Goal",
    pageMeta: "Page",
    startedMeta: "Started",
    durationMeta: "Duration",
    actionsMeta: "Actions",
    actionsSummary: (steps, decisions, texts) => `${steps} executed · ${decisions} decisions · ${texts} texts`,
    finalScreen: "Final screen",
    errors: "Errors",
    noActions: "No action was executed in this run.",
    texts: "Text generated",
    noTexts: "Nothing needed generated text.",
    generated: "generated",
    pageChanged: "Page changed",
    noChange: "No change observed",
    viaNode: " · via node",
    actions: (n) => `${n} actions`,
    seconds: (value) => `${value} s`,
    running: "Running…",
    busyOpening: "Opening the page…",
    busyPredicting: "Jev is comparing the actions…",
    busyExecuting: "Executing the choice…",
    busyRunning: "Running the browser…",
    paused: "Paused · needs attention",
    pausing: "Pausing after the current request…",
    unreachable: "Cannot reach the local server",
    badUrl: "Enter a full http or https page address, for example https://example.com",
    states: {
      idle: "Ready to explore",
      ready: "Page observed · ready for a decision",
      predicted: "Choice ready · inspect or execute",
      done: "Run complete · inspect the page",
      blocked: "Stopped · no supported next action",
    },
    badge: { done: "done", blocked: "blocked", error: "error", running: "running", ready: "ready" },
    server: {
      "Enter a full http or https page address, for example https://example.com":
        "请输入完整的网页地址（http 或 https），例如 https://example.com",
      "Enter 1–2,000 characters": "请输入 1–2000 个字符",
      "Start a run first": "请先开始一次运行",
      "Start a demo first": "请先开始一次运行",
      "Observe and choose before acting": "请先让 Jev 给出选择，再执行",
      "This run has stopped. Start a fresh demo.": "本次运行已结束，请重新开始一次运行",
      "Reached the demo's model-call budget": "已达到本次运行的模型调用上限",
      "A browser step is already running": "已有一步正在执行，请稍候",
      "Local demo failed; no automatic retry. Reset to recover.":
        "本地服务出错，不会自动重试；重新开始一次运行即可恢复",
      "Target changed or is covered. Observe again.": "目标已变化或被遮挡，请重新观察页面",
      "Page changed since this decision. Observe again.": "页面在该决策之后发生了变化，请重新观察",
      "Page changed before text generation. Choose again.": "页面在生成文本前发生变化，请重新选择",
      "Invalid observed node": "观测到的节点无效",
      "Unknown command": "未知指令",
      "Unknown run": "找不到该运行记录",
      "Dropdown execution was not confirmed; inspect before retrying.": "下拉选择未确认成功，请检查后再重试",
      "Invalid TypeSafe response; no action executed.": "模型返回的结果不合法，未执行任何动作",
      "Text helper returned no valid field value; nothing typed.": "文本模型没有给出可用的值，未输入任何内容",
      "The page moved before the action, so nothing was executed. It has been re-observed; choose again.":
        "页面在动作前发生了变化，因此没有执行任何操作；已重新观察，请再次选择",
      "The text helper returned no usable value, so nothing was typed. It has been re-observed; choose again.":
        "文本模型没有给出可用的值，未输入任何内容；已重新观察，请再次选择",
      "The browser session was lost and the page was reopened. Choose again to continue.":
        "浏览器会话已断开，页面已重新打开；请再次选择以继续",
      "The page is still showing a dialog. Choose again.": "页面还停留在一个弹窗上，请再次选择",
      "The page was still moving when the block was reported. Choose again.":
        "报告无法继续时页面仍在变化，请再次选择",
      "Request failed": "请求失败",
    },
    patterns: [
      [/^Model provider returned HTTP (\d+); no action executed\.$/, (m) => `模型服务返回 HTTP ${m[1]}，未执行任何动作`],
      [/^Model connection failed \((.*)\); no action executed\.$/, (m) => `模型连接失败（${m[1]}），未执行任何动作`],
      [/^Stopped at the (\d+)-action demo budget$/, (m) => `已达到 ${m[1]} 步的动作上限`],
    ],
  },
  zh: {
    title: "Browser Ultrafast",
    localBrowser: "本地浏览器",
    emptyHint: "填写页面地址和任务目标，然后开始运行。",
    noPage: "尚未打开页面",
    urlPlaceholder: "https://example.com/page — 仅支持 http 或 https",
    goalPlaceholder: "描述这个页面上要完成的任务，以及完成时应当看到什么。",
    reuse: "复用已打开的标签页",
    reuseHint: "直接在你已打开该网址的标签页里操作，而不是新开一个标签页。",
    start: "开始运行",
    slow: "慢速演示",
    targets: "目标框",
    choose: "让 Jev 选择",
    execute: "执行该选择",
    auto: "自动运行",
    stop: "暂停",
    nextAction: "下一步动作",
    waitingPage: "等待页面",
    chooseAction: "请选择一个动作",
    elements: (n) => `${n} 个元素`,
    options: (n) => `${n} 个可选动作`,
    decisionTime: "决策耗时",
    targetConfidence: "目标置信度",
    operation: "操作类型",
    indexed: "已索引元素",
    ranked: "按 Jev 概率排序",
    unranked: "点击「让 Jev 选择」后排序",
    choicesEmpty: "可用动作会显示在这里。",
    runs: "运行记录",
    recorded: (n) => `已记录 ${n} 次`,
    unavailable: "不可用",
    runsEmpty: "每次运行都会保存在本地，点击任意一条即可查看当时的详情。",
    trail: "执行轨迹",
    trailEmpty: "每个已执行的动作都会留下观测结果。",
    export: "导出轨迹 ↓",
    modelSees: "模型看到的内容",
    modelStateIdle: "开始一次运行后，这里会显示结构化的状态。",
    runDetail: "运行详情",
    close: "关闭",
    runTitle: "运行",
    statusMeta: "状态",
    goalMeta: "任务目标",
    pageMeta: "页面地址",
    startedMeta: "起止时间",
    durationMeta: "耗时",
    actionsMeta: "动作统计",
    actionsSummary: (steps, decisions, texts) => `执行 ${steps} 步 · 决策 ${decisions} 次 · 生成文本 ${texts} 次`,
    finalScreen: "最终画面",
    errors: "错误",
    noActions: "本次运行没有执行任何动作。",
    texts: "生成的文本",
    noTexts: "本次运行无需生成文本。",
    generated: "已生成",
    pageChanged: "页面已变化",
    noChange: "未观察到变化",
    viaNode: " · 节点直达",
    actions: (n) => `${n} 个动作`,
    seconds: (value) => `${value} 秒`,
    running: "运行中…",
    busyOpening: "正在打开页面…",
    busyPredicting: "Jev 正在比较可选动作…",
    busyExecuting: "正在执行该选择…",
    busyRunning: "正在操作浏览器…",
    paused: "已暂停 · 需要处理",
    pausing: "当前请求结束后暂停…",
    unreachable: "无法连接本地服务",
    badUrl: "请输入完整的网页地址（http 或 https），例如 https://example.com",
    states: {
      idle: "准备就绪",
      ready: "已观察页面 · 可以决策",
      predicted: "已给出选择 · 可检查或执行",
      done: "运行结束 · 请核对页面结果",
      blocked: "已停止 · 没有可推进的动作",
    },
    badge: { done: "完成", blocked: "受阻", error: "错误", running: "运行中", ready: "就绪" },
    server: {},
    patterns: [],
  },
};
const STORE = { url: "jev.url", goal: "jev.goal", reuse: "jev.reuse", lang: "jev.lang" };
const stored = (key, fallback = null) => {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
};
let lang = stored(STORE.lang) || (String(navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en");
if (!STRINGS[lang]) lang = "en";
const t = (key, ...args) => {
  const value = STRINGS[lang][key] ?? STRINGS.en[key];
  return typeof value === "function" ? value(...args) : (value ?? key);
};
const say = (message) => {
  const text = String(message ?? "");
  const dict = STRINGS[lang].server || {};
  if (dict[text]) return dict[text];
  for (const [pattern, build] of STRINGS[lang].patterns || []) {
    const match = pattern.exec(text);
    if (match) return build(match);
  }
  return text;
};
const setText = (id, value) => {
  const node = $(id);
  if (node) node.textContent = value;
};

// The page and the goal are the two things a tester retypes every time; keep them across visits.
const remember = () => {
  try {
    localStorage.setItem(STORE.url, $("target-url").value);
    localStorage.setItem(STORE.goal, $("goal").value);
    localStorage.setItem(STORE.reuse, $("reuse").checked ? "1" : "0");
  } catch {
    /* Private mode still runs the page; it just forgets. */
  }
};
const recall = () => {
  try {
    $("target-url").value = localStorage.getItem(STORE.url) || "";
    $("goal").value = localStorage.getItem(STORE.goal) || "";
    $("reuse").checked = localStorage.getItem(STORE.reuse) !== "0";
  } catch {
    /* Nothing remembered yet. */
  }
};
function applyLanguage() {
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.title = t("title");
  $("lang").value = lang;
  $("target-url").placeholder = t("urlPlaceholder");
  $("goal").placeholder = t("goalPlaceholder");
  setText("label-reuse", t("reuse"));
  setText("label-start", t("start"));
  setText("label-slow", t("slow"));
  setText("label-targets", t("targets"));
  setText("choose", t("choose"));
  setText("execute", t("execute"));
  setText("auto", t("auto"));
  setText("stop", t("stop"));
  setText("eyebrow-next", t("nextAction"));
  setText("label-decision", t("decisionTime"));
  setText("label-confidence", t("targetConfidence"));
  setText("label-operation", t("operation"));
  setText("label-indexed", t("indexed"));
  setText("label-runs", t("runs"));
  setText("label-trail", t("trail"));
  setText("download", t("export"));
  setText("label-model-sees", t("modelSees"));
  setText("label-run-detail", t("runDetail"));
  setText("label-run-trail", t("trail"));
  setText("label-run-texts", t("texts"));
  setText("run-export", t("export"));
  setText("run-close-button", t("close"));
  setText("empty-hint", t("emptyHint"));
  setText("page-title", state?.page?.title || t("noPage"));
  $("label-reuse").title = t("reuseHint");
  $("page-title").title = t("reuseHint");
  if (!state) {
    // Nothing has been observed yet, so the placeholders are the only text on screen.
    setText("status", t("states").idle);
    setText("choice-title", t("waitingPage"));
    setText("ranking-note", t("unranked"));
    setText("model-state", t("modelStateIdle"));
    setText("action-count", t("elements", 0));
    setText("url", t("localBrowser"));
    setText("run-title", t("runTitle"));
    $("choices").innerHTML = `<p class="muted">${escape(t("choicesEmpty"))}</p>`;
    $("history").innerHTML = `<p class="muted">${escape(t("trailEmpty"))}</p>`;
    $("runs-list").innerHTML = `<p class="muted">${escape(t("runsEmpty"))}</p>`;
    $("step-count").textContent = `${t("actions", 0)} · ${t("seconds", "0.00")}`;
  } else {
    render();
  }
}
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const percent = (value) => `${(value * 100).toFixed(value < 0.01 ? 1 : 0)}%`;
async function call(name, body = {}) {
  const response = await fetch(`/api/${name}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Token": token },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw Error(say(data.error || "Request failed"));
  state = data;
  render();
  return data;
}
function controls() {
  const live = state?.page && !["done", "blocked"].includes(state.status);
  $("start").disabled = busy;
  $("goal").disabled = busy;
  $("target-url").disabled = busy;
  $("reuse").disabled = busy;
  $("choose").disabled = busy || !live;
  $("execute").disabled = busy || !state?.decision || !live;
  $("auto").disabled = busy || !live;
  $("auto").hidden = automatic;
  $("stop").hidden = !automatic;
  $("download").disabled = !state?.history?.length;
}
async function perform(fn, label) {
  if (busy) return;
  busy = true;
  $("error").hidden = true;
  $("error").classList.remove("notice");
  controls();
  $("status").textContent = label;
  try {
    await fn();
  } catch (error) {
    automatic = false;
    try {
      state = await fetch("/api/state").then((r) => r.json());
      render();
    } catch {
      /* Preserve the original failure if the server disconnected. */
    }
    $("error").classList.remove("notice");
    $("error").textContent = say(error.message);
    $("error").hidden = false;
    $("status").textContent = t("paused");
  } finally {
    busy = false;
    controls();
  }
}
function render() {
  if (!state) return;
  if (state.notice) {
    $("error").textContent = say(state.notice);
    $("error").classList.add("notice");
    $("error").hidden = false;
  }
  // Target boxes are placed in percentages of the observed viewport, so the frame has to match
  // that viewport instead of one fixed size: a reused tab keeps the user's own window size.
  if (state.page?.w && state.page?.h) $("viewport").style.aspectRatio = `${state.page.w} / ${state.page.h}`;
  $("helper").textContent = `${t("title")} · ${state.text_model}`;
  $("plan").innerHTML = (state.plan || [])
    .map(
      (goal, i) =>
        `<div class="plan-step ${i === state.plan_index ? "current" : ""}"><span>${i < state.plan_index ? "✓" : i + 1}</span>${escape(goal)}</div>`,
    )
    .join("");
  const page = state.page,
    d =
      state.decision ||
      (state.status === "done" ? state.decisions?.at(-1) : null);
  $("status").textContent = t("states")[state.status] || state.status;
  if (state.run_id && state.run_id !== shownRun) {
    shownRun = state.run_id;
  } else if (["done", "blocked"].includes(state.status) && state.run_id !== listedRun) {
    listedRun = state.run_id;
    loadRuns();
  }
  if (!page) {
    controls();
    return;
  }
  $("empty").hidden = true;
  $("screenshot").hidden = false;
  $("screenshot").src = `data:image/jpeg;base64,${page.screenshot}`;
  $("url").textContent = page.url;
  $("page-title").textContent = page.title;
  $("action-count").textContent = t("elements", state.elements.length);
  const chosen = page.actions.find((a) => a.id === d?.choice);
  $("choice-title").textContent = d ? chosen?.label || d.choice : t("chooseAction");
  $("latency").textContent = d ? `${d.latency_ms} ms` : "—";
  $("confidence").textContent = d?.target_confidence != null ? percent(d.target_confidence) : "—";
  $("completion").textContent = d ? d.operation : "—";
  $("ranking-note").textContent = d ? t("ranked") : t("unranked");
  const op = Object.entries(d?.operation_probabilities || {}).sort((a, b) => b[1] - a[1]);
  $("operation-choices").innerHTML = op
    .map(([name, p]) => `<span class="operation-choice ${name === d.operation ? "best" : ""}">${escape(name)} <b>${percent(p)}</b></span>`)
    .join("");
  const probability = (e) =>
    d?.target_probabilities[e.index] ??
    Math.max(-1, ...(e.options || []).map((o) => d?.target_probabilities[o.index] ?? -1));
  const selectedIndex = d?.target?.split(":")[0];
  const elements = [...state.elements];
  if (d) elements.sort((a, b) => probability(b) - probability(a));
  $("choices").innerHTML = elements
    .map((e) => {
      const p = probability(e);
      return `<div class="choice ${selectedIndex === e.index ? "best" : ""}" data-action="${escape(e.index)}"><span class="choice-id">[${escape(e.index)}]</span><div class="choice-label">${escape(e.label)}<small>${escape(e.role)} · ${escape(e.operations.join(" / "))}${e.value ? " · " + escape(e.value) : ""}${e.checked !== undefined ? " · checked " + escape(e.checked) : ""}</small>${p >= 0 ? `<div class="bar" style="--probability:${p * 100}%"></div>` : ""}</div><span class="probability">${p >= 0 ? percent(p) : "—"}</span></div>`;
    })
    .join("");
  if (!state.elements.length) $("choices").innerHTML = `<p class="muted">${escape(t("choicesEmpty"))}</p>`;
  const targets = new Map();
  for (const a of page.actions) if (a.rect && !targets.has(a.node)) targets.set(a.node, a);
  $("targets").innerHTML = [...targets.values()]
    .map((a, i) => {
      const index = String(i + 1);
      return `<div class="target ${index === selectedIndex ? "selected" : ""}" data-action="${index}" style="left:${(100 * a.rect.x) / page.w}%;top:${(100 * a.rect.y) / page.h}%;width:${(100 * a.rect.w) / page.w}%;height:${(100 * a.rect.h) / page.h}%"><span>${index}</span></div>`;
    })
    .join("");
  $("targets").hidden = !$("overlays").checked;
  $("history").innerHTML = state.history.length
    ? state.history.map(trailRow).join("")
    : `<p class="muted">${escape(t("trailEmpty"))}</p>`;
  $("step-count").textContent = `${t("actions", state.history.length)} · ${t("seconds", (state.elapsed_ms / 1000).toFixed(2))}`;
  $("runs-count").textContent = t("recorded", document.querySelectorAll(".run-row").length);
  $("model-state").textContent = JSON.stringify(
    d?.request || {
      goal: state.goal,
      url: page.url,
      text: page.text,
      actions: page.actions.map(({ rect, node, ...rest }) => rest),
    },
    null,
    2,
  );
  controls();
}
$("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  automatic = false;
  const url = $("target-url").value.trim();
  if (!/^https?:\/\//i.test(url)) {
    $("error").classList.remove("notice");
    $("error").textContent = t("badUrl");
    $("error").hidden = false;
    $("target-url").focus();
    return;
  }
  remember();
  perform(
    () =>
      call("reset", {
        goal: $("goal").value,
        url,
        reuse: $("reuse").checked,
      }),
    t("busyOpening"),
  ).then(loadRuns);
});
$("choose").addEventListener("click", () =>
  perform(() => call("predict"), t("busyPredicting")),
);
$("execute").addEventListener("click", () =>
  perform(
    () => call("act", { fingerprint: state.page.fingerprint }),
    t("busyExecuting"),
  ),
);
$("auto").addEventListener("click", () =>
  perform(async () => {
    automatic = true;
    controls();
    for (let i = 0; i < state.max_steps * 2 && automatic; i++) {
      $("status").textContent = t("running");
      if ($("pace").checked) {
        await call("predict");
        await new Promise((resolve) => setTimeout(resolve, 450));
        if (!automatic) break;
        await call("act", { fingerprint: state.page.fingerprint });
      } else {
        await call("tick");
      }
      if (["done", "blocked"].includes(state.status)) break;
    }
    automatic = false;
  }, t("busyRunning")),
);
$("stop").addEventListener("click", () => {
  automatic = false;
  $("status").textContent = t("pausing");
  controls();
});
$("lang").addEventListener("change", (event) => {
  lang = STRINGS[event.target.value] ? event.target.value : "en";
  try {
    localStorage.setItem(STORE.lang, lang);
  } catch {
    /* Nothing to remember. */
  }
  applyLanguage();
  loadRuns();
});
$("overlays").addEventListener("change", () => {
  $("targets").hidden = !$("overlays").checked;
});
$("choices").addEventListener("pointerover", (event) => {
  const id = event.target.closest("[data-action]")?.dataset.action;
  document
    .querySelectorAll(".target")
    .forEach((node) =>
      node.classList.toggle(
        "selected",
        node.dataset.action === id || node.dataset.action === state?.decision?.target?.split(":")[0],
      ),
    );
});
$("choices").addEventListener("pointerleave", () =>
  document
    .querySelectorAll(".target")
    .forEach((node) =>
      node.classList.toggle(
        "selected",
        node.dataset.action === state?.decision?.target?.split(":")[0],
      ),
    ),
);
$("runs-list").addEventListener("click", (event) => {
  const id = event.target.closest("[data-run]")?.dataset.run;
  if (id) showRun(id);
});
// Opening the list is the refresh: the panel reloads whenever it is unfolded.
$("runs-panel").addEventListener("toggle", (event) => {
  if (event.target.open) loadRuns();
});
["run-close", "run-close-button"].forEach((id) =>
  $(id).addEventListener("click", closeRun),
);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("run-detail").hidden) closeRun();
});
$("run-export").addEventListener("click", () => {
  if (!openRunRecord) return;
  const blob = new Blob([JSON.stringify(openRunRecord, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `jev-run-${openRunRecord.id}.json`;
  a.click();
  URL.revokeObjectURL(url);
});
$("target-url").addEventListener("change", remember);
$("goal").addEventListener("change", remember);
$("reuse").addEventListener("change", remember);
$("download").addEventListener("click", () => {
  const { page, ...rest } = state;
  const blob = new Blob(
    [JSON.stringify({ ...rest, page: { ...page, screenshot: undefined } }, null, 2)],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "typesafe-browser-trace.json";
  a.click();
  URL.revokeObjectURL(url);
});

const when = (iso) => {
  const date = new Date(iso || "");
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
const shortUrl = (url) => {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname;
    return `${parsed.host}${path}`.slice(0, 68);
  } catch {
    return String(url || "").slice(0, 68);
  }
};
const seconds = (ms) => t("seconds", ((ms || 0) / 1000).toFixed(2));
const badge = (status) => t("badge")[status] || status;
const trailRow = (h) =>
  `<div class="trace-row"><span class="number">${String(h.step).padStart(2, "0")}</span><div>${escape(h.action)}${h.text ? ` <b>“${escape(h.text)}”</b><small>${escape(h.text_helper)}</small>` : ""}</div><span class="time">${h.latency_ms} ms · ${percent(h.probability)}</span><span class="effect">${h.page_changed ? t("pageChanged") : t("noChange")}${h.via === "dom" ? t("viaNode") : ""}</span></div>`;

async function loadRuns() {
  let runs = [];
  try {
    runs = (await fetch("/api/runs").then((r) => r.json())).runs || [];
  } catch {
    $("runs-count").textContent = t("unavailable");
    return;
  }
  $("runs-count").textContent = t("recorded", runs.length);
  $("runs-list").innerHTML = runs.length
    ? runs
        .map(
          (run) => `<div class="run-row" data-run="${escape(run.id)}">
            <span class="badge ${escape(run.status)}">${escape(badge(run.status))}</span>
            <div class="run-main"><strong>${escape(shortUrl(run.url))}</strong><small>${escape((run.goal || "").slice(0, 96))}</small></div>
            <span class="run-meta">${escape(t("actions", run.steps || 0))} · ${seconds(run.elapsed_ms)}</span>
            <span class="run-time">${escape(when(run.started_at))}</span>
          </div>`,
        )
        .join("")
    : `<p class="muted">${escape(t("runsEmpty"))}</p>`;
}

let openRunRecord = null;
async function showRun(id) {
  let record = null;
  try {
    record = await fetch(`/api/runs/${encodeURIComponent(id)}`).then((r) => r.json());
  } catch {
    return;
  }
  if (record.error) return;
  openRunRecord = record;
  $("run-title").textContent = shortUrl(record.url);
  $("run-meta").innerHTML = [
    [t("statusMeta"), escape(badge(record.status))],
    [t("goalMeta"), escape(record.goal || "")],
    [t("pageMeta"), escape(record.url || "")],
    [t("startedMeta"), `${escape(when(record.started_at))} → ${escape(when(record.finished_at))}`],
    [t("durationMeta"), seconds(record.elapsed_ms)],
    [t("actionsMeta"), escape(t("actionsSummary", record.steps || 0, (record.decisions || []).length, (record.text_calls || []).length))],
  ]
    .map(([key, value]) => `<div><span>${escape(key)}</span><strong>${value}</strong></div>`)
    .join("");
  $("run-shot").innerHTML = record.has_shot
    ? `<p class="distribution-label">${escape(t("finalScreen"))}</p><img src="/api/runs/${encodeURIComponent(record.id)}/shot" alt="${escape(t("finalScreen"))}" />`
    : "";
  $("run-errors").innerHTML = (record.errors || []).length
    ? `<p class="distribution-label">${escape(t("errors"))}</p>${record.errors
        .map((e) => `<div class="drawer-error">${escape(say(e.message))}<small>${escape(when(e.at))}</small></div>`)
        .join("")}`
    : "";
  const history = record.history || [];
  $("run-history").innerHTML = history.length
    ? history.map(trailRow).join("")
    : `<p class="muted">${escape(t("noActions"))}</p>`;
  $("run-step-count").textContent = `${t("actions", history.length)} · ${seconds(record.elapsed_ms)}`;
  const texts = record.text_calls || [];
  $("run-texts").innerHTML = texts.length
    ? texts
        .map(
          (item) =>
            `<div class="trace-row"><span class="number">txt</span><div>${escape(item.field || "")} <b>“${escape(item.value ?? "")}”</b><small>${escape(item.model || "")}</small></div><span class="time">${item.latency_ms || 0} ms</span><span class="effect">${escape(t("generated"))}</span></div>`,
        )
        .join("")
    : `<p class="muted">${escape(t("noTexts"))}</p>`;
  $("run-export").disabled = false;
  $("run-detail").hidden = false;
}

// A declaration rather than a const arrow: the listeners above are registered while this module
// is still evaluating, so they would otherwise read an uninitialised binding and abort the module.
function closeRun() {
  $("run-detail").hidden = true;
  openRunRecord = null;
}

recall();
applyLanguage();
loadRuns();
fetch("/api/state")
  .then((r) => r.json())
  .then((s) => {
    state = s;
    render();
  })
  .catch(() => {
    $("status").textContent = t("unreachable");
  });
