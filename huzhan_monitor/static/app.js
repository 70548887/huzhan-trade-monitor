"use strict";
const $ = (selector) => document.querySelector(selector);
const number = new Intl.NumberFormat("zh-CN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const money = (cents) => cents === null || cents === undefined ? "—" : "¥ " + number.format(cents / 100);
const stamp = (value) => value ? value.replace("T", " ").slice(0, 19) : "—";
const labels = {success: "新增成功", pending: "新增进行中", baseline: "启动时存量", unknown: "未知状态", observed_pending: "当日观察到交易中", snapshot_pending: "最后采集交易中"};
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const safeLink = (url, title) => {
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:" || parsed.protocol === "http:")
      return `<a href="${escapeHtml(parsed.href)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title)}</a>`;
  } catch (_) {}
  return escapeHtml(title);
};
function beijingDate() {
  const values = new Intl.DateTimeFormat("en-CA", {timeZone:"Asia/Shanghai", year:"numeric", month:"2-digit", day:"2-digit"}).formatToParts(new Date());
  const get = (name) => values.find((part) => part.type === name).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}
async function getJson(url) {
  const response = await fetch(url, {cache:"no-store"});
  if (!response.ok) throw new Error(`请求失败（${response.status}）`);
  return response.json();
}
function query() {
  return new URLSearchParams({day:$("#day").value, status:$("#status").value, q:$("#search").value});
}
function renderSummary(data) {
  $("#success-money").textContent = money(data.success.cents);
  $("#success-count").textContent = data.available ? `${data.success.count} 个成功条目` : "当天无成功采集记录";
  $("#observed-pending-money").textContent = money(data.observed_pending.cents);
  $("#observed-pending-count").textContent = data.observed_pending.count === null ? "当天无成功采集记录" : `${data.observed_pending.count} 个不同条目`;
  $("#snapshot-pending-money").textContent = money(data.snapshot_pending.cents);
  $("#snapshot-pending-count").textContent = data.snapshot_pending.observed_at ? `${data.snapshot_pending.count} 个条目 · ${stamp(data.snapshot_pending.observed_at)}` : "当天无成功采集记录";
  $("#pending-money").textContent = money(data.pending.cents);
  $("#pending-count").textContent = data.available ? `${data.pending.count} 个新增进行中条目` : "当天无成功采集记录";
  $("#index-money").textContent = money(data.platform.delta_cents);
  $("#index-count").textContent = data.platform.delta_count === null ? "缺少可比较的快照" : `交易笔数变化 ${data.platform.delta_count}`;
  $("#run-count").textContent = `${data.runs.success || 0} / ${data.runs.total || 0}`;
  $("#run-detail").textContent = `成功 / 执行；失败 ${data.runs.failed || 0} 次`;
  $("#last-update").textContent = `最近成功采集 ${stamp(data.last_success_at)}`;
  const platform = data.platform;
  $("#index-window").textContent = `指数观测区间：${stamp(platform.start_at)} 至 ${stamp(platform.end_at)}${platform.partial ? "（当天截至最近快照）" : ""}。`;
  const warnings = [];
  if (!data.available)
    warnings.push("当天没有成功采集，无法判断当天的交易金额。");
  if (data.coverage.gaps.length)
    warnings.push(`存在 ${data.coverage.gaps.length} 段超过 15 分钟的采集间隔：` + data.coverage.gaps.slice(0, 3).map((g) => `${stamp(g.start)} 至 ${stamp(g.end)}（${g.minutes} 分钟）`).join("；") + (data.coverage.gaps.length > 3 ? "；其余间隔请检查运行日志。" : "。"));
  if (data.coverage.failures.length)
    warnings.push(`${data.coverage.failures.length} 次采集失败；最近一次：${stamp(data.coverage.failures[0].started_at)} ${data.coverage.failures[0].error || ""}`);
  if (!platform.start_boundary_ok || !platform.end_boundary_ok)
    warnings.push("指数缺少靠近日期边界的快照，显示的是实际观测区间的变化，不是完整自然日数值。");
  if (platform.rollback)
    warnings.push("平台累计指数出现回退，已停止计算该区间增量，请检查源数据。");
  if (data.latest_run?.status === "failed")
    warnings.push(`最近一次采集失败：${data.latest_run.error || "未知错误"}`);
  $("#baseline").textContent = data.baseline_at ? `首次有效采集：${stamp(data.baseline_at)}；此前没有监控记录。` : "尚无首次有效采集。";
  if (data.maintenance_error)
    warnings.push(`备份或试运行评估失败：${data.maintenance_error}`);
  $("#warnings").innerHTML = warnings.map((message) => `<div class="warning">${escapeHtml(message)}</div>`).join("");
  if (data.trial) {
    $("#trial").textContent = `24 小时试运行：${data.trial.passed ? "通过" : "有缺口或失败"}；成功 ${data.trial.success_count} 次，失败 ${data.trial.failed_count} 次，最大间隔 ${data.trial.max_gap_minutes} 分钟。`;
  } else if (data.trial_started_at) {
    $("#trial").textContent = `24 小时试运行进行中，开始时间 ${stamp(data.trial_started_at)}。`;
  } else {
    $("#trial").textContent = "尚未完成首次有效采集。";
  }
}
function renderRows(rows) {
  $("#transactions").innerHTML = rows.map((row) => `<tr>
    <td><span class="tag ${escapeHtml(row.category)}">${labels[row.category] || escapeHtml(row.category)}</span></td>
    <td>${safeLink(row.product_url, row.title)}${row.status_conflict ? ' <span class="tag unknown">状态异常</span>' : ""}</td>
    <td class="amount">${money(row.amount_cents)}</td><td>${escapeHtml(row.buyer)}</td>
    <td>${escapeHtml(row.current_status_text)}</td><td>${stamp(row.report_time)}</td></tr>`).join("");
  $("#row-count").textContent = `${rows.length} 条显示记录`;
  $("#empty-transactions").hidden = rows.length > 0;
}
function renderProducts(rows, available) {
  $("#products").innerHTML = rows.map((row) => `<tr><td>${safeLink(row.product_url, row.title)}</td><td>${row.count}</td><td class="amount">${money(row.cents)}</td></tr>`).join("");
  $("#empty-products").hidden = rows.length > 0;
  $("#empty-products").textContent = available ? "当天暂无新增成功条目。" : "当天没有成功采集，暂无可用数据。";
}
function renderTrend(rows) {
  const max = Math.max(1, ...rows.map((row) => row.observed_cents || 0));
  $("#trend-chart").innerHTML = rows.map((row) => `<div class="bar" style="height:${Math.max(2, Math.round(100 * (row.observed_cents || 0) / max))}%;${row.observed_cents === null ? "background:#cdd5df" : ""}" title="${row.date} ${row.observed_cents === null ? "无成功采集" : money(row.observed_cents)}"></div>`).join("");
  $("#trend").innerHTML = [...rows].reverse().map((row) => `<tr><td>${row.date}</td><td>${money(row.observed_cents)}</td><td title="${stamp(row.index_start_at)} 至 ${stamp(row.index_end_at)}">${money(row.index_delta_cents)}${row.index_delta_cents !== null && !row.index_boundary_ok ? " *" : ""}${row.index_rollback ? "（回退）" : ""}</td></tr>`).join("");
}
let requestVersion = 0;
async function refresh() {
  const version = ++requestVersion;
  const params = query();
  $("#export").href = "/api/export.csv?" + params.toString();
  try {
    const day = encodeURIComponent($("#day").value);
    const [summary, transactions, products, trend] = await Promise.all([
      getJson("/api/summary?day=" + day),
      getJson("/api/transactions?" + params.toString()),
      getJson("/api/products?day=" + day),
      getJson("/api/trend?day=" + day + "&days=14")
    ]);
    if (version !== requestVersion) return;
    renderSummary(summary);
    renderRows(transactions.items);
    renderProducts(products.items, summary.available);
    renderTrend(trend.items);
  } catch (error) {
    if (version !== requestVersion) return;
    $("#warnings").innerHTML = `<div class="warning error">加载失败：${escapeHtml(error.message)}</div>`;
  }
}
$("#day").value = beijingDate();
$("#day").addEventListener("change", refresh);
$("#status").addEventListener("change", refresh);
$("#today").addEventListener("click", () => { $("#day").value = beijingDate(); refresh(); });
$("#refresh").addEventListener("click", refresh);
let searchTimer;
$("#search").addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = setTimeout(refresh, 300); });
refresh();
