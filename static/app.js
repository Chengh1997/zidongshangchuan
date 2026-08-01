const $ = (selector) => document.querySelector(selector);
let state = {items: [], members: []};
let selected = "";

async function api(url, data = {}) {
  const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data)});
  const result = await response.json();
  if (result.state) render(result.state);
  if (!result.ok) throw Error(result.message || "操作失败");
  return result;
}

function notice(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  setTimeout(() => toast.classList.remove("show"), 4200);
}

function esc(value) {
  return String(value || "").replace(/[&<>"']/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character]);
}

function renderMembers(current) {
  const members = current.members || [];
  const typeLabels = {"1": "项目负责人/总监", "3": "技术负责人", "4": "安全负责人", "9": "其他成员"};
  $("#memberStatus").textContent = members.length ? `已预览 ${members.length} 人` : "尚未加载";
  $("#memberStatus").classList.toggle("complete", members.length > 0);
  $("#memberPreview").innerHTML = members.slice(0, 20).map((member) => `
    <div class="member">
      <strong>${esc(member.bid_member_name)}</strong><span class="member-role">${esc(typeLabels[member.bid_member_type] || member.bid_member_type)}</span>
      <div class="info-line"><em>身份证</em><span>${esc(member.bid_member_cert_no)}</span></div>
      <div class="info-line"><em>证书</em><span>${esc(member.bid_member_certificate_name || "无")}</span></div>
      <div class="info-line"><em>证号</em><span>${esc(member.bid_member_certificate_num || "无")}</span></div>
    </div>`).join("");
}

function renderBidder(current) {
  const profile = current.bidderProfile || {};
  const agent = current.bidderAgent || {};
  const hasProfile = Boolean(profile.name);
  $("#bidderStatus").textContent = hasProfile ? "已识别，等待确认写入" : "尚未识别";
  $("#bidderStatus").classList.toggle("complete", hasProfile);
  $("#bidderPreview").innerHTML = hasProfile ? `
    <div class="member"><strong>${esc(profile.name)}</strong><span class="member-role">企业资料</span><div class="info-line"><em>信用代码</em><span>${esc(profile.uscc)}</span></div><div class="info-line"><em>地址</em><span>${esc(profile.address)}</span></div><div class="info-line"><em>邮编</em><span>${esc(profile.postalCode)}</span></div></div>
    <div class="member"><strong>${esc(profile.legalPersonName)}</strong><span class="member-role">法定代表人</span><div class="info-line"><em>性别/年龄</em><span>${esc(profile.legalPersonSex)} · ${esc(profile.legalPersonAge)} 岁</span></div><div class="info-line"><em>身份证</em><span>${esc(profile.legalPersonIdCard)}</span></div><div class="info-line"><em>职务</em><span>${esc(profile.legalPersonDuty)}</span></div></div>
    <div class="member"><strong>${esc(profile.contactPerson)}</strong><span class="member-role">联系人</span><div class="info-line"><em>电话</em><span>${esc(profile.phone)}</span></div><div class="info-line"><em>代理人</em><span>${esc(agent.agentPerson || "无")}</span></div><div class="info-line"><em>代理证件</em><span>${esc(agent.agentPersonIdCard || "无")}</span></div><div class="info-line"><em>代理电话</em><span>${esc(agent.agentPhone || "无")}</span></div></div>` : "";
}

function render(current) {
  state = current;
  const labels = {upload: "上传文件", optional_skip: "无文件则跳过", missing_skip: "项目无文件·下一章", user_skip: "用户选择跳过", manual: "人工填写", date: "选择日期", generated: "软件自动生成", boq: "清单导入生成"};
  $("#status").textContent = current.message || "";
  $("#connection").querySelector("span").textContent = current.projectId ? "已连接编制工具" : "未连接编制工具";
  $("#connection").classList.toggle("connected", Boolean(current.projectId));
  const items = current.items || [];
  const completed = items.filter((item) => item.chapter_submitted).length;
  $("#chapterMetric").textContent = current.chapterCount || items.length || 0;
  $("#completedMetric").textContent = completed;
  $("#pendingMetric").textContent = Math.max(items.length - completed, 0);
  $("#pilotMetric").textContent = current.pilotVerified ? "已通过" : "未验证";
  $("#boqStatus").textContent = current.boqVerified ? `已导入并核验：${(current.boqPath || "").split(/[\\/]/).pop()}` : "尚未导入";
  $("#boqStatus").classList.toggle("complete", Boolean(current.boqVerified));
  renderMembers(current);
  renderBidder(current);

  const selectedItem = current.items?.find((item) => item.chapter_code === selected);
  $("#verifyOne").disabled = !selectedItem || selectedItem.action !== "upload" || !selectedItem.source_path;
  const blockers = (current.items || []).some((item) => ["manual", "date", "boq"].includes(item.action) && item.status !== "success") || (current.items || []).some((item) => item.action === "upload" && item.status === "pending" && !item.source_path);
  $("#runBatch").disabled = !current.pilotVerified || current.batchRunning || blockers;
  $("#runBatch").textContent = current.batchRunning ? "正在逐章处理…" : "开始批量处理";

  const body = $("#chaptersBody");
  if (!current.items?.length) {
    body.innerHTML = '<tr><td colspan="7" class="empty">先读取软件里的真实章节树</td></tr>';
    return;
  }
  body.innerHTML = current.items.map((item) => {
    const selectable = item.action === "upload" && item.source_path;
    const controls = [];
    if (["upload", "optional_skip", "missing_skip", "user_skip"].includes(item.action) && !item.chapter_submitted) controls.push(`<button class="small source" data-code="${esc(item.chapter_code)}">指定文件</button>`);
    if (item.action === "upload" && !item.chapter_submitted) {
      if (["failed", "retry"].includes(item.status)) controls.push(`<button class="small retry" data-code="${esc(item.chapter_code)}">重试</button>`);
      controls.push(`<button class="small skip" data-code="${esc(item.chapter_code)}">跳过</button>`);
    }
    if (item.action === "user_skip" && item.source_path && !item.chapter_submitted) controls.push(`<button class="small retry" data-code="${esc(item.chapter_code)}">恢复上传</button>`);
    if (item.action === "manual") controls.push(`<button class="small manual-check" data-code="${esc(item.chapter_code)}">重新读取核验</button>`);
    const canAdvance = !item.chapter_submitted && (
      ["generated", "optional_skip", "missing_skip", "user_skip"].includes(item.action) ||
      (["manual", "date", "boq"].includes(item.action) && item.status === "success")
    );
    if (canAdvance) controls.push(`<button class="small advance" data-code="${esc(item.chapter_code)}">执行下一章</button>`);
    const completion = item.chapter_submitted ? "已点下一章" : "未完成";
    const statusClass = item.chapter_submitted ? "success" : (item.status === "failed" ? "failed" : "pending");
    return `<tr class="${selected === item.chapter_code ? "selected" : ""}"><td>${selectable ? `<input aria-label="选择 ${esc(item.chapter_title)}" type="radio" name="chapter" ${selected === item.chapter_code ? "checked" : ""} data-code="${esc(item.chapter_code)}">` : ""}</td><td><strong class="chapter-name">${esc(item.chapter_title)}</strong></td><td><span class="action-tag ${esc(item.action)}">${esc(labels[item.action] || item.action)}</span></td><td class="file">${esc(item.source_path ? item.source_path.split(/[\\/]/).pop() : "—")}</td><td><span class="status-badge ${statusClass}">${esc(completion)}</span></td><td class="evidence">${esc(item.completion_evidence || item.reason)}</td><td><div class="row-actions">${controls.join(" ")}</div></td></tr>`;
  }).join("");
}

async function run(action) {
  try { await action(); } catch (error) { notice(error.message); }
}

document.querySelectorAll("[data-choose]").forEach((button) => {
  button.onclick = () => run(async () => {
    const kind = button.dataset.choose;
    const result = await api(`/api/choose/${kind}`, {initial: kind === "project" ? $("#projectDir").value : $("#zjzbsPath").value});
    if (!result.path) return;
    if (kind === "project") {
      $("#projectDir").value = result.path;
      $("#bidder").innerHTML = (result.bidders || []).map((name) => `<option value="${esc(name)}">${name === "__project_self__" ? "当前项目（单单位结构）" : esc(name)}</option>`).join("") || '<option value="">未找到投标文件目录</option>';
    } else $("#zjzbsPath").value = result.path;
  });
});

$("#launchTool").onclick = () => run(() => api("/api/launch-tool", {exePath: $("#exePath").value}));
$("#readChapters").onclick = () => run(async () => {
  if (!confirm("只会导入并读取真实章节树，不会上传附件。继续吗？")) return;
  selected = "";
  await api("/api/read-chapters", {projectDir: $("#projectDir").value, bidder: $("#bidder").value, zjzbsPath: $("#zjzbsPath").value, endpoint: $("#endpoint").value});
});
$("#chaptersBody").onclick = (event) => {
  const code = event.target.dataset.code;
  if (event.target.matches('input[type="radio"]')) { selected = code; render(state); }
  if (event.target.classList.contains("source")) run(() => api("/api/choose-source", {chapterCode: code}));
  if (event.target.classList.contains("manual-check")) run(() => api("/api/manual/verify", {chapterCode: code}));
  if (event.target.classList.contains("retry")) run(() => api("/api/item/retry", {chapterCode: code}));
  if (event.target.classList.contains("advance")) run(async () => {
    const item = state.items.find((candidate) => candidate.chapter_code === code);
    if (item && confirm(`确认在编制工具中选中“${item.chapter_title}”并真实点“下一章”吗？`)) await api("/api/advance-one", {chapterCode: code});
  });
  if (event.target.classList.contains("skip")) run(async () => { if (confirm("确认跳过该小章节？执行时仍会在编制工具点“下一章”并记录。")) await api("/api/item/skip", {chapterCode: code}); });
};
$("#verifyOne").onclick = () => run(async () => {
  const item = state.items.find((candidate) => candidate.chapter_code === selected);
  if (!item || !confirm(`确认把“${item.source_path.split(/[\\/]/).pop()}”上传到“${item.chapter_title}”吗？上传回读后，程序会在编制工具中选中该小章节并真实点“下一章”。`)) return;
  await api("/api/verify-one", {chapterCode: selected});
});
$("#runBatch").onclick = () => run(async () => { if (confirm("将逐小章节上传/跳过，真实点“下一章”并回读完成状态。失败项会保留报告。确认开始？")) await api("/api/run-batch"); });
$("#stopBatch").onclick = () => run(() => api("/api/stop"));

$("#memberExtract").onclick = () => run(async () => {
  const result = await api("/api/members/extract");
  notice(`已识别 ${result.state.memberCount} 人；请核对身份证和证书后再点写入`);
});
$("#bidderExtract").onclick = () => run(async () => {
  await api("/api/bidder/extract");
  notice("投标人资料已识别；请逐字段核对后再写入");
});
$("#bidderWrite").onclick = () => run(async () => {
  if (confirm("将用当前预览资料修正编制工具中的投标人、法人、联系人和委托代理人；已有身份证附件保持不动。确认写入？")) await api("/api/bidder/write");
});
$("#memberTemplate").onclick = () => run(async () => { const result = await api("/api/members/template"); notice(`模板已保存到：${result.path}`); });
$("#memberLoad").onclick = () => run(() => api("/api/members/load", {}));
$("#memberWrite").onclick = () => run(async () => { if (confirm("将用当前预览的成员资料覆盖软件成员列表，并重新读取核验。确认资料已与投标文件一致？")) await api("/api/members/write"); });
$("#refresh").onclick = () => run(() => api("/api/state"));
$("#boqImport").onclick = () => run(async () => { if (!state.projectId) throw Error("请先连接软件并读取项目"); await api("/api/boq/import", {}); });
$("#saveLegalDate").onclick = () => run(() => api("/api/legal-date", {date: $("#legalDate").value}));

setInterval(() => api("/api/state").catch(() => {}), 2000);
api("/api/state").catch(() => {});
