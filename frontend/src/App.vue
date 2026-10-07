<template>
  <main>
    <header class="topbar">
      <h1>光伏组串IV扫描台</h1>
      <nav v-if="session" class="tabs">
        <button :class="{ active: view === 'scan' }" @click="switchView('scan')">IV扫描</button>
        <button :class="{ active: view === 'shadow' }" @click="switchView('shadow')">邻串阴影剔除</button>
        <span class="who">{{ session.username }}（{{ isWriter ? "可提交" : "旁观只读" }}）</span>
        <button class="secondary" @click="logout">退出</button>
      </nav>
    </header>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456，旁观账号 watcher / watch123456 只能翻履历。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <!-- IV 扫描页 -->
    <div v-else-if="view === 'scan'">
      <section class="bar">
        <button class="secondary" @click="refreshLogs">刷新列表</button>
      </section>
      <section v-if="isWriter">
        <h2>提交扫描</h2>
        <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
        <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
        <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
        <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
        <button :disabled="loading" @click="submit">提交扫描</button>
        <p v-if="error" class="err">{{ error }}</p>
        <p v-if="lastBlocked" class="warn">上一笔已被阴影闸门拦进拦住履历：{{ lastBlocked }}</p>
      </section>
      <section>
        <table>
          <thead>
            <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论 / 拦住原因</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in logs" :key="row.id" :class="{ blockedRow: row.status === 'blocked' }">
              <td>{{ row.id }}</td>
              <td>{{ row.string_code }}</td>
              <td>{{ row.voc_v }}</td>
              <td>{{ row.isc_a }}</td>
              <td>{{ row.fill_factor }}</td>
              <td>
                <span class="tag" :class="statusClass(row.status)">{{ statusText(row.status) }}</span>
              </td>
              <td>
                <span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span>
                <span v-else-if="row.block_reason" class="reason" :title="row.block_reason">{{ row.block_reason }}</span>
                <span v-else>—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>

    <!-- 邻串阴影剔除专页 -->
    <div v-else>
      <section>
        <h2>阴影剔除闸门</h2>
        <p class="hint">遮挡会让单点填充因子飞掉。闸门打开时，交单笔先和同阵列相邻组串的近窗样本对照：相对邻串中位跳得太猛整笔退回，不进工人队列；同组串窗长内已有一笔时，贴门槛同时到的两笔最多留一笔。关掉后旧拦住履历仍可翻，只是新单不再拦。</p>
        <div class="switch-row">
          <label class="switch">
            <input type="checkbox" :checked="settings.shadow_gate_enabled"
                   :disabled="!isWriter || loading" @change="toggleGate" />
            <span class="slider"></span>
          </label>
          <strong>闸门{{ settings.shadow_gate_enabled ? "已打开（飞点会被拦住）" : "已关闭（新单不再拦）" }}</strong>
        </div>
        <p v-if="!isWriter" class="hint">旁观账号只能翻拦住履历，不能扳开关或改参数。</p>
        <div class="params">
          <div>
            <label>对照窗长（秒，5–600）</label>
            <input type="number" min="5" max="600" v-model="windowInput" :disabled="!isWriter" />
          </div>
          <div>
            <label>跳变阈值（相对邻串中位，0.01–1）</label>
            <input type="number" step="0.01" min="0.01" max="1" v-model="jumpInput" :disabled="!isWriter" />
          </div>
          <button v-if="isWriter" :disabled="loading" @click="saveParams">保存参数</button>
        </div>
        <p v-if="settingMsg" :class="settingOk ? 'oktext' : 'err'">{{ settingMsg }}</p>
        <table v-if="changeLog.length" class="changelog">
          <thead><tr><th>设置项</th><th>最近改动</th></tr></thead>
          <tbody>
            <tr v-for="item in changeLog" :key="item.key">
              <td>{{ item.label }}</td>
              <td>{{ item.by }} · {{ fmtTime(item.at) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section>
        <h2>开关对照试算</h2>
        <p class="hint">按当下窗长与阈值模拟一笔，不写库：打开时丢飞点应被拦住，关掉后再丢同类应能进队。</p>
        <div class="params">
          <div>
            <label>组串编号</label>
            <input v-model="previewCode" placeholder="例如 阵列B-串12" />
          </div>
          <div>
            <label>填充因子</label>
            <input type="number" step="0.01" v-model="previewFf" />
          </div>
          <button :disabled="loading" @click="runPreview">试算对照</button>
        </div>
        <div v-if="preview" class="preview-box" :class="preview.would_block ? 'block-hit' : 'pass-hit'">
          <p>
            闸门当前<strong>{{ preview.gate_enabled ? "打开" : "关闭" }}</strong>，
            窗长 {{ preview.window_sec }} 秒，阈值 {{ preview.jump }}。
          </p>
          <p v-if="!preview.gate_enabled" class="oktext">闸门关闭：即使是同类飞点也会直接进队，不再添拦住履历。</p>
          <p v-else-if="preview.would_block" class="warn">
            此笔{{ preview.duplicate ? "撞近窗重复" : "跳变超阈" }}，<strong>会被拦住，不进队</strong>。
          </p>
          <p v-else class="oktext">此笔会进队。</p>
          <p v-if="preview.reason" class="reason">{{ preview.reason }}</p>
          <p class="hint">
            邻串窗口中位：
            <template v-if="preview.neighbor_median !== null">{{ preview.neighbor_median.toFixed(3) }}</template>
            <template v-else>—</template>
            （参与对照邻串 {{ preview.neighbor_samples }} 串，少于 2 串时不判定、放行）
          </p>
        </div>
      </section>

      <section>
        <h2>拦住履历 <button class="secondary small" @click="refreshBlocks">刷新</button></h2>
        <p v-if="!blocks.length" class="hint">还没有被拦住的扫描。闸门打开后退回的飞点会记在这里；关掉闸门也不删旧账。</p>
        <table v-else>
          <thead>
            <tr><th>时间</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>拦住原因</th><th>提交人</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in blocks" :key="row.id">
              <td>{{ fmtTime(row.blocked_at || row.created_at) }}</td>
              <td>{{ row.string_code }}</td>
              <td>{{ row.voc_v }}</td>
              <td>{{ row.isc_a }}</td>
              <td>{{ row.fill_factor }}</td>
              <td class="reason">{{ row.block_reason }}</td>
              <td>{{ row.created_by }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const view = ref("scan");
const logs = ref([]);
const blocks = ref([]);
const settings = ref({ shadow_gate_enabled: false, shadow_window_sec: 90, shadow_jump: 0.2, updated: {} });
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const windowInput = ref(90);
const jumpInput = ref(0.2);
const previewCode = ref("");
const previewFf = ref("");
const preview = ref(null);
const error = ref("");
const settingMsg = ref("");
const settingOk = ref(false);
const lastBlocked = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");
const changeLog = computed(() => {
  const labels = {
    shadow_gate_enabled: "阴影闸门开关",
    shadow_window_sec: "对照窗长",
    shadow_jump: "跳变阈值",
  };
  return Object.entries(settings.value.updated || {}).map(([key, m]) => ({
    key, label: labels[key] || key, by: m.by, at: m.at,
  }));
});
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmtTime(s) {
  if (!s) return "—";
  return new Date(s).toLocaleString("zh-CN", { hour12: false });
}
function statusText(s) {
  return { pending: "待处理", done: "已完成", blocked: "已拦截" }[s] || s;
}
function statusClass(s) {
  return { pending: "pending", done: "ok", blocked: "block-tag" }[s] || "";
}
async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { ...(opts.body ? { "Content-Type": "application/json" } : {}), ...headers() },
    ...opts,
  });
  if (res.status === 401) { logout(); throw new Error("未登录"); }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || "请求失败");
  return data;
}
async function refreshLogs() {
  if (!session.value || view.value !== "scan") return;
  try { logs.value = await api("/api/logs"); } catch { /* 轮询静默 */ }
}
async function refreshBlocks() {
  if (!session.value || view.value !== "shadow") return;
  try { blocks.value = await api("/api/blocks"); } catch { /* 轮询静默 */ }
}
async function loadSettings() {
  try {
    const data = await api("/api/settings");
    settings.value = data;
    windowInput.value = data.shadow_window_sec;
    jumpInput.value = data.shadow_jump;
  } catch { /* 静默 */ }
}
async function switchView(v) {
  view.value = v;
  preview.value = null;
  settingMsg.value = "";
  if (v === "scan") await refreshLogs();
  else { await loadSettings(); await refreshBlocks(); }
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshLogs();
    timer = setInterval(tick, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  blocks.value = [];
  localStorage.removeItem("pv_session");
}
async function tick() {
  if (view.value === "scan") await refreshLogs();
  else await refreshBlocks();
}
async function submit() {
  error.value = "";
  lastBlocked.value = "";
  loading.value = true;
  try {
    const row = await api("/api/logs", {
      method: "POST",
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    stringCode.value = voc.value = isc.value = ff.value = "";
    if (row.status === "blocked") lastBlocked.value = row.block_reason || "疑似阴影飞点";
    await refreshLogs();
  } catch (e) { error.value = e.message || "提交时网络异常"; }
  finally { loading.value = false; }
}
async function toggleGate(e) {
  const enabled = e.target.checked;
  settingMsg.value = "";
  try {
    const data = await api("/api/settings", { method: "POST", body: JSON.stringify({ enabled }) });
    settings.value = data;
    settingOk.value = true;
    settingMsg.value = `闸门已${enabled ? "打开" : "关闭"}：${enabled ? "飞点整笔退回拦住履历，不进队" : "旧拦住履历仍可翻，新单不再拦"}`;
    await refreshBlocks();
  } catch (err) {
    settingOk.value = false;
    settingMsg.value = err.message;
    await loadSettings();
  }
}
async function saveParams() {
  settingMsg.value = "";
  try {
    const data = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ window_sec: Number(windowInput.value), jump: Number(jumpInput.value) }),
    });
    settings.value = data;
    settingOk.value = true;
    settingMsg.value = "窗长与跳变阈值已保存";
  } catch (err) {
    settingOk.value = false;
    settingMsg.value = err.message;
  }
}
async function runPreview() {
  settingMsg.value = "";
  try {
    preview.value = await api("/api/shadow/preview", {
      method: "POST",
      body: JSON.stringify({ string_code: previewCode.value, fill_factor: Number(previewFf.value) }),
    });
  } catch (err) {
    settingOk.value = false;
    settingMsg.value = err.message;
  }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshLogs();
      timer = setInterval(tick, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1020px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0; font-size: 1.4rem; }
h2 { color: #bbf7d0; margin: 0 0 0.75rem; font-size: 1.05rem; }
.topbar { display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.25rem; }
.tabs { display: flex; align-items: center; gap: 0.4rem; }
.tabs .who { color: #a7f3d0; font-size: 0.85rem; margin: 0 0.5rem; }
.tabs button.active { background: #16a34a; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.bar { background: transparent; border: none; padding: 0; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
input:disabled { opacity: 0.55; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary { background: #365314; }
button.small { padding: 0.15rem 0.6rem; font-size: 0.75rem; font-weight: 400; }
.err { color: #fecaca; }
.oktext { color: #bbf7d0; }
.warn { color: #fde68a; }
.hint { color: #a7f3d0; font-size: 0.85rem; line-height: 1.5; }
.reason { color: #fde68a; font-size: 0.8rem; display: inline-block; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: middle; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: middle; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.block-tag { background: #9a3412; color: #fed7aa; }
.blockedRow { background: rgba(154, 52, 18, 0.18); }
.changelog { margin-top: 0.75rem; font-size: 0.82rem; }
.switch-row { display: flex; align-items: center; gap: 0.75rem; margin: 0.5rem 0 0.75rem; }
.switch { position: relative; display: inline-block; width: 48px; height: 26px; margin: 0; }
.switch input { position: absolute; opacity: 0; width: 0; height: 0; margin: 0; }
.slider { position: absolute; inset: 0; background: #365314; border: 1px solid #4ade80; border-radius: 26px; transition: 0.2s; }
.slider::before { content: ""; position: absolute; width: 18px; height: 18px; left: 3px; top: 3px; background: #dcfce7; border-radius: 50%; transition: 0.2s; }
.switch input:checked + .slider { background: #16a34a; }
.switch input:checked + .slider::before { transform: translateX(22px); }
.params { display: flex; gap: 1rem; align-items: flex-end; flex-wrap: wrap; }
.params > div { flex: 1 1 180px; }
.params button { margin-bottom: 0.75rem; }
.preview-box { border-radius: 6px; padding: 0.75rem 1rem; margin-top: 0.75rem; }
.preview-box p { margin: 0.3rem 0; }
.block-hit { border: 1px solid #fb923c; background: rgba(154, 52, 18, 0.35); }
.pass-hit { border: 1px solid #4ade80; background: rgba(20, 83, 45, 0.5); }
</style>
