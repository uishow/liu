const hero = document.querySelector("#hero");
const guide = document.querySelector("#guide");
const todayLog = document.querySelector("#today-log");
const todayOps = document.querySelector("#today-ops");
const archiveDays = document.querySelector("#archive-days");
const archiveDetail = document.querySelector("#archive-detail");
const sources = document.querySelector("#sources");
const stamp = document.querySelector("#stamp");
let archiveDay = "";
const quoteBody = document.querySelector("#quotes tbody");
const tradeBody = document.querySelector("#trades tbody");
const months = document.querySelector("#months");
const codes = document.querySelector("#codes");
const chartNote = document.querySelector("#chart-note");
const canvas = document.querySelector("#equity");

const money = (value) => {
  const sign = value > 0 ? "+" : "";
  return sign + value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
};
const px = (value) => (value == null ? "—" : Number(value).toFixed(3));
const num = (value, digits = 2) => (value == null ? "—" : Number(value).toFixed(digits));
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
const kindText = { breakout: "突破", pullback: "回调" };
const reasonText = { take: "止盈", stop: "止损", flat: "到点平仓" };

function render(data) {
  if (!data.ready) {
    guide.hidden = false;
    guide.className = "guide";
    guide.innerHTML = `<p class="loading">${data.error ? "行情没有连上：" + esc(data.error) : "正在连接 eltdx 与 AxData，并回放 5 分钟样本…"}</p>`;
    renderJournal(data.journal);
    return;
  }
  const summary = data.summary;
  const tone = summary.net_pnl >= 0 ? "up" : "down";
  hero.innerHTML = `
    <article class="stat"><span>样本净利</span><strong class="${tone}">${money(summary.net_pnl)}</strong><em>${summary.sample.start} → ${summary.sample.end}</em></article>
    <article class="stat"><span>收益率</span><strong class="${tone}">${summary.return_pct.toFixed(2)}%</strong><em>最大回撤 ${summary.max_drawdown_pct.toFixed(2)}%</em></article>
    <article class="stat"><span>胜率</span><strong>${summary.win_rate.toFixed(1)}%</strong><em>${summary.trades} 笔 · 止盈 0.35% / 止损 0.18%</em></article>
    <article class="stat"><span>交易时段</span><strong>${data.session_open ? "开市" : "已收盘"}</strong><em>行情每 15 秒自动更新，开盘后信号随 5 分钟线重算</em></article>
  `;
  sources.innerHTML = Object.entries(data.sources).map(([name, item]) => {
    const label = name === "premium" ? "IOPV" : name;
    const latency = item.latency_ms ? " · " + item.latency_ms + "ms" : "";
    const reason = item.ok ? "" : " · " + (item.detail || "没有返回行情");
    return `<span class="pill ${item.ok ? "ok" : "bad"}">${label} ${item.ok ? "已连接" : "中断"}${reason}${latency}</span>`;
  }).join("");
  stamp.textContent = data.updated_at ? "行情自动更新 " + data.updated_at : "";
  renderGuide(data.guidance);
  renderJournal(data.journal);

  quoteBody.innerHTML = data.quotes.map((row) => {
    const change = row.axdata.change_pct;
    const changeClass = change > 0 ? "up" : change < 0 ? "down" : "";
    const signal = row.signal
      ? `${row.signal.time} ${row.signal.kind === "pullback" ? "回调" : "突破"}`
      : "—";
    const gate = row.tradable_price && !row.block_reasons.length
      ? `<span class="tag">价位合格</span>`
      : `<span class="tag wait" title="${row.block_reasons.join("；")}">观望</span>`;
    const reason = row.block_reasons[0] || row.premium_note;
    return `<tr>
      <td><span class="name">${row.name}</span><span class="sub">${row.code} · ${row.style} · ${row.class_id === "domestic" ? "境内" : "跨境"}</span></td>
      <td>${px(row.last)}</td>
      <td class="${changeClass}">${change == null ? "—" : num(change) + "%"}</td>
      <td>${px(row.eltdx.last)} / ${px(row.axdata.last)}<span class="sub">偏离 ${row.basis_bps == null ? "—" : num(row.basis_bps, 1) + " bps"}</span></td>
      <td>${px(row.iopv)}</td>
      <td class="${row.premium_pct > 1.5 ? "down" : ""}">${row.premium_pct == null ? "—" : num(row.premium_pct) + "%"}</td>
      <td>${px(row.eltdx.bid)} / ${px(row.eltdx.ask)}</td>
      <td>${signal}<span class="sub">${row.signal ? row.signal.reason : ""}</span></td>
      <td>${gate}<span class="sub">${reason}</span></td>
    </tr>`;
  }).join("");

  const equity = data.equity.length ? data.equity : [{ day: summary.sample.start, equity: summary.start_equity }, { day: summary.sample.end, equity: summary.end_equity }];
  drawEquity(equity);
  chartNote.textContent = `${summary.sample.bars.toLocaleString("zh-CN")} 根 5 分钟线 · 起始 100 万 · 结束 ${Math.round(summary.end_equity).toLocaleString("zh-CN")}`;

  const monthEntries = Object.entries(summary.by_month);
  const maxMonth = Math.max(...monthEntries.map(([, value]) => Math.abs(value)), 1);
  months.innerHTML = `<h2>分月</h2>` + monthEntries.map(([month, value]) => bar(month, value, maxMonth)).join("");
  const codeEntries = Object.entries(summary.by_code).sort((a, b) => b[1] - a[1]);
  const maxCode = Math.max(...codeEntries.map(([, value]) => Math.abs(value)), 1);
  codes.innerHTML = `<h2>分标的</h2>` + codeEntries.map(([code, value]) => bar(code, value, maxCode)).join("");

  tradeBody.innerHTML = data.trades.slice().reverse().map((trade) => `<tr>
    <td>${trade.code}</td>
    <td>${kindText[trade.kind] || trade.kind}</td>
    <td>${trade.entry_ts}</td>
    <td>${trade.exit_ts}</td>
    <td>${num(trade.fill, 3)}</td>
    <td>${num(trade.exit, 3)}</td>
    <td>${trade.qty}</td>
    <td>${reasonText[trade.reason] || trade.reason}</td>
    <td class="${trade.pnl >= 0 ? "up" : "down"}">${money(trade.pnl)}</td>
  </tr>`).join("");
}

function renderGuide(item) {
  if (!item) {
    guide.hidden = true;
    return;
  }
  guide.hidden = false;
  guide.className = "guide " + (item.action || "wait");
  const prices = item.limit == null ? "" : `
    <div class="prices">
      <div><span>${item.action === "buy" ? "买入限价" : "持仓成本"}</span><strong>${num(item.limit, 3)}</strong></div>
      <div><span>止盈价</span><strong>${num(item.take_profit, 3)}</strong></div>
      <div><span>止损价</span><strong>${num(item.stop_loss, 3)}</strong></div>
      <div><span>数量</span><strong>${item.qty ? item.qty.toLocaleString("zh-CN") : "—"}</strong></div>
    </div>`;
  const head = item.code ? `${item.title} · ${item.name || ""} ${item.code}` : item.title;
  guide.innerHTML = `<h2>${esc(head)}</h2><p class="why">${esc(item.why || "")}</p>${prices}<ol>${(item.steps || []).map((step) => `<li>${esc(step)}</li>`).join("")}</ol>`;
}

function renderJournal(journal) {
  if (!journal) {
    todayLog.innerHTML = `<li><span>开盘后，每一条不同的建议都会出现在这里。</span></li>`;
    todayOps.innerHTML = "";
    return;
  }
  const today = journal.today;
  const suggestions = (journal.suggestions || []).filter((item) => item.day === today);
  todayLog.innerHTML = suggestions.length
    ? suggestions.map(suggestionItem).join("")
    : `<li><span>开盘后，每一条不同的建议都会出现在这里。</span></li>`;
  const operations = (journal.operations || []).filter((item) => item.day === today);
  todayOps.innerHTML = operations.length
    ? `<div class="ops"><h2>今天的纸面成交</h2><div class="table-wrap"><table><thead><tr><th>标的</th><th>开仓</th><th>平仓</th><th>买价</th><th>卖价</th><th>数量</th><th>原因</th><th>盈亏</th></tr></thead><tbody>${operations.map(operationRow).join("")}</tbody></table></div><p class="note">这是程序按规则算出的纸面成交，用来对照你在券商里实际下的单。</p></div>`
    : `<p class="note">今天还没有纸面成交。</p>`;
  const days = archiveDaysOf(journal);
  if (!archiveDay || !days.includes(archiveDay)) archiveDay = days[0] || "";
  archiveDays.innerHTML = days.map((day) => `<button type="button" data-day="${esc(day)}" class="${day === archiveDay ? "active" : ""}">${esc(day)}</button>`).join("");
  archiveDays.querySelectorAll("button").forEach((button) => {
    button.addEventListener("click", () => {
      archiveDay = button.dataset.day;
      renderJournal(window.__desk && window.__desk.journal);
    });
  });
  renderArchiveDetail(journal, archiveDay);
}

function suggestionItem(item) {
  return `<li><time>${esc((item.ts || "").slice(11, 16))}</time><div><strong class="${esc(item.action || "")}">${esc(item.title || "")}</strong><span class="sub">${esc(item.instruction || item.why || "")}</span></div></li>`;
}

function operationRow(item) {
  const pnl = Number(item.pnl || 0);
  return `<tr>
    <td>${esc(item.code)}<span class="sub">${esc(kindText[item.kind] || item.kind || "")}</span></td>
    <td>${esc(item.entry_ts)}</td>
    <td>${esc(item.exit_ts)}</td>
    <td>${num(item.fill, 3)}</td>
    <td>${num(item.exit, 3)}</td>
    <td>${item.qty ? Number(item.qty).toLocaleString("zh-CN") : "—"}</td>
    <td>${esc(reasonText[item.reason] || item.reason || "")}</td>
    <td class="${pnl >= 0 ? "up" : "down"}">${money(pnl)}</td>
  </tr>`;
}

function archiveDaysOf(journal) {
  const days = new Set();
  (journal.suggestions || []).forEach((item) => item.day && days.add(item.day));
  (journal.operations || []).forEach((item) => item.day && days.add(item.day));
  return Array.from(days).sort().reverse();
}

function renderArchiveDetail(journal, day) {
  if (!day) {
    archiveDetail.innerHTML = `<p class="note">还没有存档。</p>`;
    return;
  }
  const suggestions = (journal.suggestions || []).filter((item) => item.day === day);
  const operations = (journal.operations || []).filter((item) => item.day === day);
  const suggestionHtml = suggestions.length
    ? `<ol class="log">${suggestions.map(suggestionItem).join("")}</ol>`
    : `<p class="note">这一天没有留下实时建议。建议从本机开始记录的那一天起才有。</p>`;
  const operationHtml = operations.length
    ? `<div class="table-wrap"><table><thead><tr><th>标的</th><th>开仓</th><th>平仓</th><th>买价</th><th>卖价</th><th>数量</th><th>原因</th><th>盈亏</th></tr></thead><tbody>${operations.map(operationRow).join("")}</tbody></table></div>`
    : `<p class="note">这一天没有纸面成交。</p>`;
  archiveDetail.innerHTML = `<h2>${esc(day)} 的建议</h2>${suggestionHtml}<h2>${esc(day)} 的纸面成交</h2>${operationHtml}`;
}

function bar(label, value, max) {
  const width = Math.max(4, Math.abs(value) / max * 100);
  const color = value >= 0 ? "var(--gold)" : "var(--down)";
  return `<div class="bar-row"><span>${label}</span><div class="track"><i style="width:${width}%;background:${color}"></i></div><span class="${value >= 0 ? "up" : "down"}">${money(value)}</span></div>`;
}

function drawEquity(points) {
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(640, Math.floor(rect.width || 960));
  canvas.width = width * 2;
  canvas.height = 280 * 2;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const pad = 36;
  const values = points.map((point) => point.equity);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(max - min, 1);
  const x = (index) => pad + (index / Math.max(points.length - 1, 1)) * (canvas.width - pad * 2);
  const y = (value) => canvas.height - pad - ((value - min) / span) * (canvas.height - pad * 2);
  ctx.strokeStyle = "#313a32";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(pad, pad);
  ctx.lineTo(pad, canvas.height - pad);
  ctx.lineTo(canvas.width - pad, canvas.height - pad);
  ctx.stroke();
  ctx.beginPath();
  ctx.strokeStyle = "#e3c27a";
  ctx.lineWidth = 4;
  points.forEach((point, index) => {
    const px = x(index);
    const py = y(point.equity);
    if (index === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  });
  ctx.stroke();
  ctx.fillStyle = "#93a396";
  ctx.font = "22px sans-serif";
  ctx.fillText(points[0].day.slice(5), pad, canvas.height - 8);
  ctx.fillText(points[points.length - 1].day.slice(5), canvas.width - 120, canvas.height - 8);
}

async function load(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  render(data);
  return data;
}

document.querySelectorAll(".tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".tabs button").forEach((item) => item.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    document.querySelector("#" + button.dataset.tab).classList.add("active");
    if (button.dataset.tab === "backtest" && window.__desk) drawEquity(window.__desk.equity);
  });
});

document.querySelector("#refresh").addEventListener("click", async () => {
  const data = await load("/api/refresh", { method: "POST" });
  window.__desk = data;
});

async function poll() {
  try {
    const data = await load("/api/desk");
    window.__desk = data;
    setTimeout(poll, data.ready ? 5000 : 3000);
  } catch (error) {
    hero.innerHTML = `<p class="loading">页面还没拿到数据，正在重试。</p>`;
    setTimeout(poll, 3000);
  }
}

poll();
