"use strict";
const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>"']/g, (character) =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[character]);
const stamp = (value) => value ? String(value).replace("T", " ").slice(0, 19) : "—";
const integer = (value) => Number.isFinite(Number(value)) ? Math.max(0, Math.trunc(Number(value))) : 0;
const statusNames = {success: "成功", failed: "失败", running: "运行中"};
let currentPage = 1;
let lastPage = 1;
let requestVersion = 0;

function beijingDate() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit"
  }).formatToParts(new Date());
  const part = (name) => parts.find((entry) => entry.type === name).value;
  return part("year") + "-" + part("month") + "-" + part("day");
}

async function getJson(url) {
  const response = await fetch(url, {cache: "no-store"});
  if (response.status === 401) {
    window.location.assign("/login?next=%2Flogs");
    throw new Error("登录已过期，请重新登录");
  }
  if (!response.ok) throw new Error("请求失败（" + response.status + "）");
  return response.json();
}

function renderSummary(data) {
  const summary = data.summary || {};
  const total = integer(summary.total);
  const success = integer(summary.success);
  $("#total-count").textContent = total;
  $("#success-count").textContent = success;
  $("#success-rate").textContent = total ? "成功率 " + (success / total * 100).toFixed(1) + "%" : "当天尚无执行记录";
  $("#failed-count").textContent = integer(summary.failed);
  $("#running-count").textContent = "运行中 " + integer(summary.running) + " 次";
  $("#duplicate-count").textContent = integer(summary.duplicates);
  $("#last-update").textContent = "最近成功采集 " + stamp(data.last_success_at);
}

function renderNotices(data) {
  $("#collection-mode").textContent = data.auto_collect
    ? "采集配置：随网页服务自动运行，每 " + String(data.interval_minutes ?? 10) + " 分钟执行一次（可在设置中修改）。"
    : "采集配置：内置调度已关闭，使用外部采集任务。";
  const notices = Array.isArray(data.notices) ? data.notices : [];
  $("#notices").innerHTML = notices.map((notice) => {
    const level = ["info", "warning", "error"].includes(notice.level) ? notice.level : "info";
    return '<div class="warning ' + level + '">' + escapeHtml(notice.message) + "</div>";
  }).join("");
  $("#empty-notices").hidden = notices.length > 0;
  $("#baseline").textContent = data.baseline_at
    ? "首次有效采集：" + stamp(data.baseline_at) + "；此前没有监控记录。"
    : "暂无首次有效采集。";
  const index = data.index_window || {};
  $("#index-window").textContent = "指数观测区间：" + stamp(index.start_at) + " 至 " + stamp(index.end_at) + "。";
  if (data.trial) {
    const trial = data.trial;
    $("#trial").textContent = "24 小时试运行：" + (trial.passed ? "通过" : "有缺口或失败")
      + "；成功 " + integer(trial.success_count) + " 次，失败 " + integer(trial.failed_count)
      + " 次，最大间隔 " + String(trial.max_gap_minutes ?? "—") + " 分钟。";
  } else if (data.trial_started_at) {
    $("#trial").textContent = "24 小时试运行进行中，开始时间 " + stamp(data.trial_started_at) + "。";
  } else {
    $("#trial").textContent = "完成首次有效采集后开始记录试运行结果。";
  }
}

function renderRuns(data) {
  const entries = Array.isArray(data.entries) ? data.entries : [];
  $("#runs").innerHTML = entries.map((entry) => {
    const status = Object.hasOwn(statusNames, entry.status) ? entry.status : "unknown";
    const duration = entry.duration_seconds === null || entry.duration_seconds === undefined
      || !Number.isFinite(Number(entry.duration_seconds))
      ? "—" : Math.max(0, Number(entry.duration_seconds)).toFixed(1) + " 秒";
    const details = entry.error
      ? '<details class="log-error"><summary>查看错误</summary><pre>' + escapeHtml(entry.error) + "</pre></details>"
      : '<span class="muted">—</span>';
    return "<tr><td>#" + integer(entry.id) + "</td>"
      + '<td class="log-time">' + escapeHtml(stamp(entry.started_at)) + "</td>"
      + '<td class="log-time">' + escapeHtml(stamp(entry.finished_at)) + "</td>"
      + '<td><span class="tag ' + status + '">' + (statusNames[status] || "未知") + "</span></td>"
      + '<td class="log-number">' + duration + "</td>"
      + '<td class="log-number">' + integer(entry.row_count) + "</td>"
      + '<td class="log-number">' + integer(entry.duplicate_count) + "</td>"
      + "<td>" + details + "</td></tr>";
  }).join("");
  $("#empty-runs").hidden = entries.length > 0;
  $("#row-count").textContent = "共 " + integer(data.total) + " 条记录";
  currentPage = Math.max(1, integer(data.page));
  lastPage = Math.max(1, integer(data.pages));
  $("#page-label").textContent = "第 " + currentPage + " / " + lastPage + " 页";
  $("#previous-page").disabled = currentPage <= 1;
  $("#next-page").disabled = currentPage >= lastPage;
}

async function refresh() {
  const version = ++requestVersion;
  const query = new URLSearchParams({
    day: $("#day").value, status: $("#status").value, page: String(currentPage), page_size: "50"
  });
  $("#overview-link").href = "/?day=" + encodeURIComponent($("#day").value);
  try {
    const data = await getJson("/api/logs?" + query.toString());
    if (version !== requestVersion) return;
    renderSummary(data);
    renderNotices(data);
    renderRuns(data);
    $("#load-error").hidden = true;
  } catch (error) {
    if (version !== requestVersion) return;
    $("#load-error").hidden = false;
    $("#load-error").innerHTML = '<div class="warning error">日志加载失败：' + escapeHtml(error.message) + "</div>";
  }
}

const initialDay = new URLSearchParams(window.location.search).get("day");
$("#day").value = initialDay && /^\d{4}-\d{2}-\d{2}$/.test(initialDay) ? initialDay : beijingDate();
$("#day").addEventListener("change", () => { currentPage = 1; refresh(); });
$("#status").addEventListener("change", () => { currentPage = 1; refresh(); });
$("#today").addEventListener("click", () => { $("#day").value = beijingDate(); currentPage = 1; refresh(); });
$("#refresh").addEventListener("click", refresh);
$("#previous-page").addEventListener("click", () => { if (currentPage > 1) { currentPage -= 1; refresh(); } });
$("#next-page").addEventListener("click", () => { if (currentPage < lastPage) { currentPage += 1; refresh(); } });
refresh();
