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
    defaultGoal: 'Type "browser ultrafast" into the search box, then click search.',
    instructionPlaceholder: "Type the next instruction for this page and press Enter",
    append: "Continue",
    newTask: "New task",
    newTaskHint: "Start over on this address with the goal in the box, or the first goal if it is empty",
    replay: "Re-run whole task",
    appendBusy: "Appending the instruction…",
    replayBusy: "Replaying the whole task…",
    instructionStep: (n) => `Instruction ${n}`,
    instructionMeta: "Instruction boundaries",
    emptyHint: "Enter a page and a goal, then press Start (or Enter).",
    noPage: "No page yet",
    urlPlaceholder: "https://example.com/page — http or https only",
    goalPlaceholder: "Describe the task for this page, and what should be visible when it is done.",
    reuse: "Use my open tab",
    reuseHint: "Drive the tab you already have open at this address, instead of opening a new one.",
    start: "Start",
    stop: "Stop",
    targets: "Target boxes",
    stepUrl: "Page",
    stepGoal: "Task",
    stepNext: "Next instruction",
    stepRun: "Run",
    stepContinue: "Continue",
    replayRun: "Replay this run",
    deleteRun: "Delete this run",
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
    unranked: "Ranked once Jev decides",
    choicesEmpty: "Available actions will appear here.",
    runs: "Runs",
    recorded: (n) => `${n} recorded`,
    unavailable: "unavailable",
    runsEmpty: "Every run is saved locally. Click one to review its trail.",
    trail: "Decision trail",
    trailEmpty: "Each executed action leaves an observed result.",
    export: "Export trace",
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
    stopped: "Stopped · type the next instruction, or press Continue",
    pausing: "Stopping after the current step…",
    unreachable: "Cannot reach the local server",
    badUrl: "Enter a full http or https page address, for example https://example.com",
    states: {
      idle: "Ready to explore",
      ready: "Page observed · ready",
      predicted: "Choice ready · execute it or resume",
      done: "Done · waiting for the next instruction",
      blocked: "Stopped · no supported next action",
    },
    badge: { done: "done", blocked: "blocked", error: "error", running: "running", ready: "ready", predicted: "paused" },
    server: {},
    patterns: [],
  },
  zh: {
    title: "Browser Ultrafast",
    localBrowser: "本地浏览器",
    defaultGoal: "在搜索框输入 browser ultrafast，然后点击搜索。",
    instructionPlaceholder: "输入下一条指令，按回车继续在当前页面操作",
    append: "继续",
    newTask: "新任务",
    newTaskHint: "在这个网址上用输入框里的任务重新开始；输入框为空时沿用最初的任务",
    replay: "重新执行整个任务",
    appendBusy: "正在追加指令…",
    replayBusy: "正在重新执行整个任务…",
    instructionStep: (n) => `第 ${n} 条指令`,
    instructionMeta: "指令分界",
    emptyHint: "填写网址和任务，点击「开始」或直接按回车。",
    noPage: "尚未打开页面",
    urlPlaceholder: "https://example.com/page — 仅支持 http 或 https",
    goalPlaceholder: "描述这个页面上要完成的任务，以及完成时应当看到什么。",
    reuse: "复用已打开的标签页",
    reuseHint: "直接在你已打开该网址的标签页里操作，而不是新开一个标签页。",
    start: "开始",
    stop: "停止",
    targets: "目标框",
    stepUrl: "网址",
    stepGoal: "任务",
    stepNext: "下一条指令",
    stepRun: "开始",
    stepContinue: "继续",
    replayRun: "重放这次运行",
    deleteRun: "删除这条记录",
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
    unranked: "Jev 决策后排序",
    choicesEmpty: "可用动作会显示在这里。",
    runs: "运行记录",
    recorded: (n) => `已记录 ${n} 次`,
    unavailable: "不可用",
    runsEmpty: "每次运行都会保存在本地，点击任意一条即可查看当时的详情。",
    trail: "执行轨迹",
    trailEmpty: "每个已执行的动作都会留下观测结果。",
    export: "导出轨迹",
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
    stopped: "已停止 · 输入下一条指令，或点「继续」",
    pausing: "当前步骤结束后停止…",
    unreachable: "无法连接本地服务",
    badUrl: "请输入完整的网页地址（http 或 https），例如 https://example.com",
    states: {
      idle: "准备就绪",
      ready: "已观察页面 · 就绪",
      predicted: "已给出选择 · 可执行或继续",
      done: "已完成 · 等待下一条指令",
      blocked: "已停止 · 没有可推进的动作",
    },
    badge: { done: "完成", blocked: "受阻", error: "错误", running: "运行中", ready: "就绪", predicted: "暂停" },
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
      "The chosen targets could not be reached on this page. Stopped instead of retrying.":
        "选中的目标在当前页面上无法点到，已停止而不是反复重试；可以换个说法输入下一条指令",
      "Request failed": "请求失败",
    },
    patterns: [
      [/^Model provider returned HTTP (\d+); no action executed\.$/, (m) => `模型服务返回 HTTP ${m[1]}，未执行任何动作`],
      [/^Model connection failed \((.*)\); no action executed\.$/, (m) => `模型连接失败（${m[1]}），未执行任何动作`],
      [/^Stopped at the (\d+)-action demo budget$/, (m) => `已达到 ${m[1]} 步的动作上限`],
    ],
  },
};
// Line icons, drawn inline so the page needs no icon font or network request.
const svg = (d) =>
  `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const ICONS = {
  play: svg('<path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none"/>'),
  stop: svg('<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none"/>'),
  enter: svg('<path d="M20 5v7a3 3 0 0 1-3 3H5"/><path d="m9 11-4 4 4 4"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  globe: svg('<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>'),
  pencil: svg('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>'),
  tab: svg('<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 10h18M8 5v5"/>'),
  boxes: svg('<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/><path d="M15 3h6v6M3 15v6h6" stroke-dasharray="2 2"/>'),
  download: svg('<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>'),
  replay: svg('<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/>'),
  trash: svg('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5"/>'),
  close: svg('<path d="M6 6l12 12M18 6 6 18"/>'),
};
function icon(node, name) {
  if (node && node.dataset.icon !== name) {
    node.dataset.icon = name;
    node.innerHTML = ICONS[name];
  }
}
document.querySelectorAll("i[data-icon]").forEach((node) => (node.innerHTML = ICONS[node.dataset.icon]));
let openRunRecord = null;
const STORE = { url: "jev.url", goal: "jev.goal", reuse: "jev.reuse.v2", lang: "jev.lang" };
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
// Only an opening goal is worth recalling; follow-up instructions belong to the open page.
const remember = (goal = !state?.page) => {
  try {
    localStorage.setItem(STORE.url, $("target-url").value);
    if (goal) localStorage.setItem(STORE.goal, $("goal").value);
    localStorage.setItem(STORE.reuse, $("reuse").checked ? "1" : "0");
  } catch {
    /* Private mode still runs the page; it just forgets. */
  }
};
const DEFAULT_URL = "https://www.google.com";
const recall = () => {
  try {
    $("target-url").value = localStorage.getItem(STORE.url) || DEFAULT_URL;
    $("goal").value = localStorage.getItem(STORE.goal) || "";
    $("reuse").checked = localStorage.getItem(STORE.reuse) !== "0";
  } catch {
    /* Nothing remembered yet. */
  }
  if (!$("target-url").value) $("target-url").value = DEFAULT_URL;
};
function applyLanguage() {
  document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
  document.title = t("title");
  document.querySelectorAll("#lang [data-lang]").forEach((node) => {
    node.setAttribute("aria-checked", String(node.dataset.lang === lang));
  });
  $("target-url").placeholder = t("urlPlaceholder");
  $("goal").placeholder = t("goalPlaceholder");
  $("reuse-toggle").title = `${t("reuse")} — ${t("reuseHint")}`;
  $("overlays-toggle").title = t("targets");
  setText("label-step-url", t("stepUrl"));
  $("new-task").title = $("new-task").ariaLabel = t("newTaskHint");
  $("download").title = $("download").ariaLabel = t("export");
  $("run-export").title = $("run-export").ariaLabel = t("export");
  $("run-replay").title = $("run-replay").ariaLabel = t("replayRun");
  $("run-close-button").title = $("run-close-button").ariaLabel = t("close");
  setText("eyebrow-next", t("nextAction"));
  setText("label-decision", t("decisionTime"));
  setText("label-confidence", t("targetConfidence"));
  setText("label-operation", t("operation"));
  setText("label-indexed", t("indexed"));
  setText("label-runs", t("runs"));
  setText("label-trail", t("trail"));
  setText("label-model-sees", t("modelSees"));
  setText("label-run-detail", t("runDetail"));
  setText("label-run-trail", t("trail"));
  setText("label-run-texts", t("texts"));
  setText("empty-hint", t("emptyHint"));
  if (!state?.page && !$("goal").value.trim()) $("goal").value = t("defaultGoal");
  setText("page-title", state?.page?.title || t("noPage"));
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
// One box for everything: before a run it holds the goal; once a page is open it takes the next
// instruction for that same page. Editing the address back to another page starts a new task.
const continuing = () => {
  if (!state?.page) return false;
  const url = $("target-url").value.trim();
  return !url || url === state.run_url || url === state.page.url;
};
function controls() {
  const live = state?.page && !["done", "blocked"].includes(state.status);
  const next = continuing();
  // One primary button: it starts or continues a run, and while a run is going it stops it.
  $("start").disabled = busy && !automatic;
  $("start").classList.toggle("stopping", automatic);
  const label = automatic ? t("stop") : next ? t("append") : t("start");
  $("start").title = $("start").ariaLabel = label;
  icon($("start-icon"), automatic ? "stop" : next ? "enter" : "play");
  // The steps above the boxes read as a checklist: done steps tick, the next one is lit.
  const url = /^https?:\/\/\S+/i.test($("target-url").value.trim()),
    goal = Boolean($("goal").value.trim());
  setText("label-step-goal", next ? t("stepNext") : t("stepGoal"));
  setText("label-step-run", automatic ? t("stop") : next ? t("stepContinue") : t("stepRun"));
  icon($("step-run").querySelector("i"), automatic ? "stop" : next ? "enter" : "play");
  $("step-url").className = `step ${url ? "done" : "current"}`;
  $("step-goal").className = `step ${!url ? "" : goal ? "done" : "current"}`;
  $("step-run").className = `step ${automatic ? "active" : url && goal ? "current" : ""}`;
  $("goal").placeholder = next ? t("instructionPlaceholder") : t("goalPlaceholder");
  $("new-task").hidden = !state?.page;
  $("new-task").disabled = busy;
  $("goal").disabled = busy;
  $("target-url").disabled = busy;
  $("reuse").disabled = busy;
  $("download").disabled = !state?.history?.length;
  $("run-replay").disabled = busy || !openRunRecord;
  document.querySelectorAll("[data-run-action]").forEach((node) => (node.disabled = busy));
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
  placeTargets();
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
function stop() {
  automatic = false;
  $("status").textContent = t("pausing");
  controls();
}
// After each instruction the run stays open and the box waits for the next one.
function awaitNext() {
  if (!state?.page) return;
  controls();
  $("goal").focus();
}
$("task-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (automatic) return stop();
  if (busy) return;
  if (continuing()) return next();
  begin();
});
$("new-task").addEventListener("click", begin);
$("goal").addEventListener("input", controls);
$("target-url").addEventListener("input", controls);
function next() {
  const text = $("goal").value.trim();
  // An empty box resumes the current instruction if it was stopped part-way.
  if (!text && ["done", "blocked"].includes(state.status)) return $("goal").focus();
  perform(async () => {
    automatic = true;
    controls();
    try {
      if (text) await call("instruct", { instruction: text });
      $("goal").value = "";
      await settle();
    } finally {
      automatic = false;
    }
  }, text ? t("appendBusy") : t("busyRunning")).then(() => {
    awaitNext();
    loadRuns();
  });
}
function begin() {
  if (busy) return;
  const url = $("target-url").value.trim();
  if (!/^https?:\/\//i.test(url)) {
    $("error").classList.remove("notice");
    $("error").textContent = t("badUrl");
    $("error").hidden = false;
    $("target-url").focus();
    return;
  }
  if (!$("goal").value.trim() && state?.page) $("goal").value = state.plan?.[0] || "";
  remember(true);
  perform(async () => {
    automatic = true;
    controls();
    try {
      await call("open", { instruction: $("goal").value, url, reuse: $("reuse").checked });
      $("goal").value = "";
      await settle();
    } finally {
      automatic = false;
    }
  }, t("busyOpening")).then(() => {
    awaitNext();
    loadRuns();
  });
}
// Enter starts; Shift+Enter still breaks a line in a long goal.
$("goal").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("task-form").requestSubmit();
  }
});
// A run that ends while the user watches is reported as stopped, not as a failure.
async function settle() {
  const status = await runToSettle();
  if (!automatic && !["done", "blocked"].includes(status)) {
    $("status").textContent = t("stopped");
    return true;
  }
  return false;
}

async function runToSettle() {
  // One instruction runs until the model stops: done, blocked, or paused by the user.
  for (let i = 0; i < 120 && automatic; i++) {
    $("status").textContent = t("running");
    await call("tick");
    if (["done", "blocked"].includes(state.status)) break;
  }
  return state.status;
}

// Replay a recorded task from its first page: the same instructions, in order, in one run.
function replay(url, instructions) {
  if (busy || !url || !instructions.length) return;
  closeRun();
  $("target-url").value = url;
  perform(async () => {
    automatic = true;
    controls();
    try {
      await call("open", { url, reuse: $("reuse").checked, instruction: instructions[0] });
      $("goal").value = "";
      await runToSettle();
      for (const text of instructions.slice(1)) {
        if (!automatic || state.status === "blocked") break;
        await call("instruct", { instruction: text });
        await runToSettle();
      }
    } finally {
      automatic = false;
    }
  }, t("replayBusy")).then(() => {
    awaitNext();
    loadRuns();
  });
}
// A replay reads the whole stored record: list rows are summaries, and older records only keep the
// first page in their trail.
async function replayRun(id) {
  if (busy) return;
  let run;
  try {
    run = await fetch(`/api/runs/${encodeURIComponent(id)}`).then((r) => (r.ok ? r.json() : Promise.reject()));
  } catch {
    $("error").textContent = say("Unknown run");
    $("error").hidden = false;
    return;
  }
  const instructions = (run.plan?.length ? run.plan : (run.instructions || []).map((item) => item.text)).filter(
    Boolean,
  );
  replay(run.start_url || run.history?.[0]?.url || run.url, instructions);
}
async function deleteRun(id) {
  const response = await fetch(`/api/runs/${encodeURIComponent(id)}/delete`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Demo-Token": token },
    body: "{}",
  });
  if (!response.ok) {
    $("error").textContent = say((await response.json()).error || "Request failed");
    $("error").hidden = false;
  }
  if (openRunRecord?.id === id) closeRun();
  loadRuns();
}
$("lang").addEventListener("click", (event) => {
  const choice = event.target.closest("[data-lang]")?.dataset.lang;
  if (!STRINGS[choice] || choice === lang) return;
  lang = choice;
  try {
    localStorage.setItem(STORE.lang, lang);
  } catch {
    /* Nothing to remember. */
  }
  applyLanguage();
  loadRuns();
});
// The viewport can be wider or taller than the letterboxed screenshot, and the boxes are placed in
// percentages of the observed page, so their layer has to cover exactly the image as displayed.
function placeTargets() {
  const img = $("screenshot"),
    layer = $("targets");
  Object.assign(layer.style, {
    left: `${img.offsetLeft}px`,
    top: `${img.offsetTop}px`,
    width: `${img.offsetWidth}px`,
    height: `${img.offsetHeight}px`,
  });
}
$("screenshot").addEventListener("load", placeTargets);
new ResizeObserver(placeTargets).observe($("viewport"));
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
  if (!id) return;
  const action = event.target.closest("[data-run-action]")?.dataset.runAction;
  if (action === "delete") return deleteRun(id);
  if (action === "replay") return replayRun(id);
  showRun(id);
});
$("runs-list").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.matches(".run-row")) event.target.click();
});
$("run-replay").addEventListener("click", () => openRunRecord && replayRun(openRunRecord.id));
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
$("target-url").addEventListener("change", () => remember());
$("goal").addEventListener("change", () => remember());
$("reuse").addEventListener("change", () => remember());
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
  h.kind === "instruction"
    ? `<div class="trace-row instruction"><span class="number">${String(h.step).padStart(2, "0")}</span><div>${escape(t("instructionStep", (h.instruction ?? 0) + 1))} · ${escape(h.action)}</div></div>`
    : `<div class="trace-row"><span class="number">${String(h.step).padStart(2, "0")}</span><div>${escape(h.action)}${h.text ? ` <b>“${escape(h.text)}”</b><small>${escape(h.text_helper)}</small>` : ""}</div><span class="time">${h.latency_ms} ms · ${percent(h.probability)}</span><span class="effect">${h.page_changed ? t("pageChanged") : t("noChange")}${h.via === "dom" ? t("viaNode") : ""}</span></div>`;

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
          (run) => `<div class="run-row" role="button" tabindex="0" data-run="${escape(run.id)}">
            <span class="badge ${escape(run.status)}">${escape(badge(run.status))}</span>
            <div class="run-main"><strong>${escape(shortUrl(run.url))}</strong><small>${escape((run.plan?.length ? run.plan.join(" → ") : run.goal || "").slice(0, 140))}</small></div>
            <span class="run-stats">${escape(t("actions", run.steps || 0))} · ${seconds(run.elapsed_ms)}</span>
            <span class="run-time">${escape(when(run.started_at))}</span>
            <span class="run-actions">
              <button type="button" class="icon small" data-run-action="replay" title="${escape(t("replayRun"))}" aria-label="${escape(t("replayRun"))}" ${busy ? "disabled" : ""}>${ICONS.replay}</button>
              <button type="button" class="icon small danger" data-run-action="delete" title="${escape(t("deleteRun"))}" aria-label="${escape(t("deleteRun"))}">${ICONS.trash}</button>
            </span>
          </div>`,
        )
        .join("")
    : `<p class="muted">${escape(t("runsEmpty"))}</p>`;
}

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
  const steps = record.instructions || [];
  $("run-errors").innerHTML += steps.length
    ? `<p class="distribution-label">${escape(t("instructionMeta"))}</p>${steps
        .map(
          (item) =>
            `<div class="trace-row instruction"><span class="number">${String((item.index ?? 0) + 1).padStart(2, "0")}</span><div>${escape(item.text)}<small>${escape(badge(item.status))} · ${escape(t("actions", Math.max(0, (item.to_step || 0) - (item.from_step || 1) + 1)))}</small></div></div>`,
        )
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
  $("run-replay").disabled = busy;
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
    if (state.page && !busy) {
      $("goal").value = "";
      awaitNext();
    }
  })
  .catch(() => {
    $("status").textContent = t("unreachable");
  });
