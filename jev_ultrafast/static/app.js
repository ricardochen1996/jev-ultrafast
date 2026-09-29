const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="demo-token"]').content;
let state = null,
  busy = false,
  automatic = false,
  shownRun = null,
  listedRun = null;
// The page and the goal are the two things a tester retypes every time; keep them across visits.
const STORE = { url: "jev.url", goal: "jev.goal", reuse: "jev.reuse" };
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
  if (!response.ok) throw Error(data.error || "Request failed");
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
  $("runs-refresh").disabled = busy;
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
    $("error").textContent = error.message;
    $("error").hidden = false;
    $("status").textContent = "Paused · needs attention";
  } finally {
    busy = false;
    controls();
  }
}
function render() {
  if (!state) return;
  if (state.notice) {
    $("error").textContent = state.notice;
    $("error").classList.add("notice");
    $("error").hidden = false;
  }
  // Target boxes are placed in percentages of the observed viewport, so the frame has to match
  // that viewport instead of one fixed size: a reused tab keeps the user's own window size.
  if (state.page?.w && state.page?.h) $("viewport").style.aspectRatio = `${state.page.w} / ${state.page.h}`;
  $("helper").textContent = `Text helper · ${state.text_model}`;
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
  const labels = {
    idle: "Ready to explore",
    ready: "Page observed · ready for a decision",
    predicted: "Choice ready · inspect or execute",
    done: "Jev reports complete · inspect the page",
    blocked: "Stopped · no supported next action",
  };
  $("status").textContent = labels[state.status] || state.status;
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
  $("action-count").textContent = `${state.elements.length} elements`;
  const chosen = page.actions.find((a) => a.id === d?.choice);
  $("choice-title").textContent = d
    ? chosen?.label || d.choice
    : "Choose an action";
  $("latency").textContent = d ? `${d.latency_ms} ms` : "—";
  $("confidence").textContent = d?.target_confidence != null ? percent(d.target_confidence) : "—";
  $("completion").textContent = d ? d.operation : "—";
  $("ranking-note").textContent = d ? "Ranked by Jev" : "Unranked";
  const op = Object.entries(d?.operation_probabilities || {}).sort((a,b)=>b[1]-a[1]);
  $("operation-choices").innerHTML = op.map(([name,p]) =>
    `<span class="operation-choice ${name === d.operation ? 'best' : ''}">${escape(name)} <b>${percent(p)}</b></span>`).join('');
  const probability = e => d?.target_probabilities[e.index] ??
    Math.max(-1, ...(e.options || []).map(o=>d?.target_probabilities[o.index] ?? -1));
  const selectedIndex = d?.target?.split(':')[0];
  const elements = [...state.elements];
  if (d) elements.sort((a,b)=>probability(b)-probability(a));
  $("choices").innerHTML = elements.map(e => {
    const p = probability(e);
    return `<div class="choice ${selectedIndex === e.index ? 'best' : ''}" data-action="${escape(e.index)}"><span class="choice-id">[${escape(e.index)}]</span><div class="choice-label">${escape(e.label)}<small>${escape(e.role)} · ${escape(e.operations.join(' / '))}${e.value ? ' · '+escape(e.value) : ''}${e.checked !== undefined ? ' · checked '+escape(e.checked) : ''}</small>${p >= 0 ? `<div class="bar" style="--probability:${p*100}%"></div>` : ''}</div><span class="probability">${p >= 0 ? percent(p) : '—'}</span></div>`;
  }).join('');
  const targets = new Map();
  for (const a of page.actions) if (a.rect && !targets.has(a.node)) targets.set(a.node, a);
  $("targets").innerHTML = [...targets.values()].map((a,i) => {
    const index=String(i+1);
    return `<div class="target ${index === selectedIndex ? 'selected' : ''}" data-action="${index}" style="left:${100*a.rect.x/page.w}%;top:${100*a.rect.y/page.h}%;width:${100*a.rect.w/page.w}%;height:${100*a.rect.h/page.h}%"><span>${index}</span></div>`;
  }).join('');
  $("targets").hidden = !$("overlays").checked;
  $("history").innerHTML = state.history.length
    ? state.history
        .map(
          (h) =>
            `<div class="trace-row"><span class="number">${String(h.step).padStart(2, "0")}</span><div>${escape(h.action)}${h.text ? ` <b>“${escape(h.text)}”</b><small>${escape(h.text_helper)}</small>` : ""}</div><span class="time">${h.latency_ms} ms · ${percent(h.probability)}</span><span class="effect">${h.page_changed ? "Page changed" : "No change observed"}</span></div>`,
        )
        .join("")
    : '<p class="muted">Each executed action leaves an observed result.</p>';
  $("step-count").textContent = `${state.history.length} actions · ${(state.elapsed_ms / 1000).toFixed(2)} s`;
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
    $("error").textContent =
      "Enter a full http or https page address, for example https://example.com";
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
    "Opening the page…",
  ).then(loadRuns);
});
$("choose").addEventListener("click", () =>
  perform(() => call("predict"), "Jev is comparing the actions…"),
);
$("execute").addEventListener("click", () =>
  perform(
    () => call("act", { fingerprint: state.page.fingerprint }),
    "Executing the choice…",
  ),
);
$("auto").addEventListener("click", () =>
  perform(async () => {
    automatic = true;
    controls();
    for (let i = 0; i < state.max_steps * 2 && automatic; i++) {
      $("status").textContent = "Running…";
      if ($("pace").checked) {
        await call("predict");
        await new Promise(resolve => setTimeout(resolve, 450));
        if (!automatic) break;
        await call("act", {fingerprint: state.page.fingerprint});
      } else {
        await call("tick");
      }
      if (["done", "blocked"].includes(state.status)) break;
    }
    automatic = false;
  }, "Running the browser…"),
);
$("stop").addEventListener("click", () => {
  automatic = false;
  $("status").textContent = "Pausing after the current request…";
  controls();
});
$("overlays").addEventListener("change", () => {
  $("targets").hidden = !$("overlays").checked;
});
$("choices").addEventListener("pointerover", (event) => {
  const id = event.target.closest("[data-action]")?.dataset.action;
  document
    .querySelectorAll(".target")
    .forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === id || t.dataset.action === state?.decision?.target?.split(':')[0],
      ),
    );
});
$("choices").addEventListener("pointerleave", () =>
  document
    .querySelectorAll(".target")
    .forEach((t) =>
      t.classList.toggle(
        "selected",
        t.dataset.action === state?.decision?.target?.split(':')[0],
      ),
    ),
);
$("runs-list").addEventListener("click", (event) => {
  const id = event.target.closest("[data-run]")?.dataset.run;
  if (id) showRun(id);
});
$("runs-refresh").addEventListener("click", (event) => {
  // The button sits inside the summary: refreshing should open the list rather than toggle it shut.
  event.stopPropagation();
  event.preventDefault();
  $("runs-panel").open = true;
  loadRuns();
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
    [
      JSON.stringify(
        { ...rest, page: { ...page, screenshot: undefined } },
        null,
        2,
      ),
    ],
    { type: "application/json" },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "typesafe-browser-trace.json";
  a.click();
  URL.revokeObjectURL(url);
});

const STATUS_LABEL = { done: "done", blocked: "blocked", error: "error", running: "running" };
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
const seconds = (ms) => `${((ms || 0) / 1000).toFixed(2)} s`;
const trailRow = (h) =>
  `<div class="trace-row"><span class="number">${String(h.step).padStart(2, "0")}</span><div>${escape(h.action)}${h.text ? ` <b>“${escape(h.text)}”</b><small>${escape(h.text_helper)}</small>` : ""}</div><span class="time">${h.latency_ms} ms · ${percent(h.probability)}</span><span class="effect">${h.page_changed ? "Page changed" : "No change observed"}${h.via === "dom" ? " · via node" : ""}</span></div>`;

async function loadRuns() {
  let runs = [];
  try {
    runs = (await fetch("/api/runs").then((r) => r.json())).runs || [];
  } catch {
    $("runs-count").textContent = "unavailable";
    return;
  }
  $("runs-count").textContent = `${runs.length} recorded`;
  $("runs-list").innerHTML = runs.length
    ? runs
        .map(
          (run) => `<div class="run-row" data-run="${escape(run.id)}">
            <span class="badge ${escape(run.status)}">${escape(STATUS_LABEL[run.status] || run.status)}</span>
            <div class="run-main"><strong>${escape(shortUrl(run.url))}</strong><small>${escape((run.goal || "").slice(0, 96))}</small></div>
            <span class="run-meta">${run.steps || 0} actions · ${seconds(run.elapsed_ms)}</span>
            <span class="run-time">${escape(when(run.started_at))}</span>
          </div>`,
        )
        .join("")
    : '<p class="muted">Every run is saved locally. Click one to review its trail.</p>';
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
    ["Status", escape(STATUS_LABEL[record.status] || record.status)],
    ["Goal", escape(record.goal || "")],
    ["Page", escape(record.url || "")],
    ["Started", `${escape(when(record.started_at))} → ${escape(when(record.finished_at))}`],
    ["Duration", seconds(record.elapsed_ms)],
    ["Actions", `${record.steps || 0} executed · ${(record.decisions || []).length} decisions · ${(record.text_calls || []).length} texts`],
  ]
    .map(([key, value]) => `<div><span>${key}</span><strong>${value}</strong></div>`)
    .join("");
  $("run-shot").innerHTML = record.has_shot
    ? `<p class="distribution-label">Final screen</p><img src="/api/runs/${encodeURIComponent(record.id)}/shot" alt="Last observed page" />`
    : "";
  $("run-errors").innerHTML = (record.errors || []).length
    ? `<p class="distribution-label">Errors</p>${record.errors
        .map((e) => `<div class="drawer-error">${escape(e.message)}<small>${escape(when(e.at))}</small></div>`)
        .join("")}`
    : "";
  const history = record.history || [];
  $("run-history").innerHTML = history.length
    ? history.map(trailRow).join("")
    : '<p class="muted">No action was executed in this run.</p>';
  $("run-step-count").textContent = `${history.length} actions · ${seconds(record.elapsed_ms)}`;
  const texts = record.text_calls || [];
  $("run-texts").innerHTML = texts.length
    ? texts
        .map(
          (t) =>
            `<div class="trace-row"><span class="number">txt</span><div>${escape(t.field || "")} <b>“${escape(t.value ?? "")}”</b><small>${escape(t.model || "")}</small></div><span class="time">${t.latency_ms || 0} ms</span><span class="effect">generated</span></div>`,
        )
        .join("")
    : '<p class="muted">Nothing needed generated text.</p>';
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
loadRuns();
fetch("/api/state")
  .then((r) => r.json())
  .then((s) => {
    state = s;
    render();
  })
  .catch(() => {
    $("status").textContent = "Cannot reach local demo server";
  });
