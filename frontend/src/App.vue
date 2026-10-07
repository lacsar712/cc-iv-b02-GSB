<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <nav class="topbar">
        <button :class="{ tab: true, active: page === 'scan' }" @click="page = 'scan'">扫描交单</button>
        <button :class="{ tab: true, active: page === 'shadow' }" @click="gotoShadow">阴影剔除</button>
        <span class="spacer"></span>
        <span class="who">{{ session.username }}（{{ isWriter ? "可提交" : "旁观只读" }}）</span>
        <button class="secondary" @click="refreshAll">刷新</button>
        <button class="secondary" @click="logout">退出</button>
      </nav>

      <!-- 扫描交单页 -->
      <div v-show="page === 'scan'">
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
          <p v-if="hint" class="ok-text">{{ hint }}</p>
          <p class="note" v-if="settingsLive">
            邻串阴影剔除当前：<b :class="settingsLive.enabled ? 'on' : 'off'">{{ settingsLive.enabled ? "开启" : "关闭" }}</b>
            （门槛 ±{{ settingsLive.threshold }}，窗长 {{ settingsLive.window_minutes }} 分钟）
            ；贴门槛近时重复单笔始终只收一笔。被拦的单子不进队，可在「阴影剔除」页翻拦住履历。
          </p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v }}</td>
                <td>{{ row.isc_a }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <!-- 邻串阴影剔除专页 -->
      <div v-show="page === 'shadow'">
        <section>
          <h2>邻串阴影剔除</h2>
          <p class="note">
            遮挡会让单点填充因子飞掉。开启时，交单口会拿本串 FF 与同阵列<b>邻串</b>近窗 FF 中位数对照，
            跳幅超过门槛则整笔退回、不入队，只在下方拦住履历留底；关闭后新单不再拦，旧履历仍可翻。
          </p>
          <div class="settings">
            <div class="switch-row">
              <span>剔除开关</span>
              <label class="switch">
                <input type="checkbox" v-model="formEnabled" :disabled="!isWriter || saving" />
                <span class="slider"></span>
              </label>
              <b :class="formEnabled ? 'on' : 'off'">{{ formEnabled ? "开启" : "关闭" }}</b>
              <span class="live" v-if="settingsLive">
                （线上当前：<b :class="settingsLive.enabled ? 'on' : 'off'">{{ settingsLive.enabled ? "开启" : "关闭" }}</b>
                ，{{ settingsLive.updated_by ? "最近由 " + settingsLive.updated_by + " 修改" : "尚未修改过" }}）
              </span>
            </div>
            <div class="field-row">
              <label>跳幅门槛（FF 绝对差）</label>
              <input type="number" step="0.01" min="0.01" max="1" v-model="formThreshold" :disabled="!isWriter || saving" />
              <label>对照窗长（分钟）</label>
              <input type="number" step="1" min="1" max="1440" v-model="formWindow" :disabled="!isWriter || saving" />
            </div>
            <p v-if="!isWriter" class="err">旁观账号只能翻拦住履历，不能扳开关或改对照参数。</p>
            <button v-if="isWriter" :disabled="saving" @click="saveSettings">保存设置</button>
            <p v-if="settingsError" class="err">{{ settingsError }}</p>
            <p v-if="settingsHint" class="ok-text">{{ settingsHint }}</p>
          </div>
        </section>

        <section>
          <h2>拦住履历 <span class="muted">（最近 {{ rejections.length }} 笔）</span></h2>
          <table>
            <thead>
              <tr>
                <th>时间</th><th>拦住类型</th><th>组串</th><th>Voc</th><th>Isc</th><th>FF</th>
                <th>对照中位</th><th>当时开关/门槛/窗长</th><th>原因</th><th>提交人</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in rejections" :key="r.id">
                <td class="nowrap">{{ fmtTime(r.rejected_at) }}</td>
                <td><span class="tag" :class="r.kind === 'shadow' ? 'bad' : 'pending'">{{ r.kind === "shadow" ? "邻串阴影" : "近时重复" }}</span></td>
                <td>{{ r.string_code }}</td>
                <td>{{ r.voc_v }}</td>
                <td>{{ r.isc_a }}</td>
                <td>{{ r.fill_factor }}</td>
                <td>
                  <template v-if="r.kind === 'shadow'">{{ r.neighbor_median }}（邻串 {{ r.neighbor_count }} 笔）</template>
                  <span v-else>—</span>
                </td>
                <td class="nowrap">
                  <span :class="r.settings_enabled ? 'on' : 'off'">{{ r.settings_enabled ? "开" : "关" }}</span>
                  / {{ r.threshold }} / {{ r.window_minutes }}分
                </td>
                <td class="reason">{{ r.reason }}</td>
                <td>{{ r.rejected_by }}</td>
              </tr>
              <tr v-if="rejections.length === 0">
                <td colspan="10" class="muted center">还没有被拦住的单子</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const session = ref(null);
const page = ref("scan");
const logs = ref([]);
const rejections = ref([]);
const settingsLive = ref(null);
const formEnabled = ref(true);
const formThreshold = ref("0.15");
const formWindow = ref("30");
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const error = ref("");
const hint = ref("");
const settingsError = ref("");
const settingsHint = ref("");
const loading = ref(false);
const saving = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmtTime(s) {
  return s ? s.replace("T", " ").slice(0, 19) + " UTC" : "";
}

async function refreshLogs() {
  const res = await fetch("/api/logs", { headers: headers() });
  if (res.status === 401) { logout(); return; }
  if (res.ok) logs.value = await res.json();
}
async function refreshRejections() {
  const res = await fetch("/api/shadow/rejections", { headers: headers() });
  if (res.ok) rejections.value = await res.json();
}
async function refreshSettings() {
  const res = await fetch("/api/shadow/settings", { headers: headers() });
  if (res.ok) settingsLive.value = await res.json();
}
async function refreshAll() {
  if (!session.value) return;
  await Promise.all([refreshLogs(), refreshRejections(), refreshSettings()]);
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
    await refreshAll();
    syncForm();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  rejections.value = [];
  localStorage.removeItem("pv_session");
}

async function submit() {
  error.value = "";
  hint.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      // 409 近时重复 / 422 邻串阴影：灯不亮、单子未入队，详情即拦住原因
      error.value = (res.status === 409 ? "已拦（近时重复）：" : res.status === 422 ? "已拦（邻串阴影）：" : "")
        + (data.detail || "提交失败");
      await refreshRejections();
      return;
    }
    hint.value = `已收单（单号 ${data.id}），等待工人出结论`;
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refreshLogs();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}

function syncForm() {
  if (!settingsLive.value) return;
  formEnabled.value = settingsLive.value.enabled;
  formThreshold.value = settingsLive.value.threshold;
  formWindow.value = settingsLive.value.window_minutes;
}
function gotoShadow() {
  page.value = "shadow";
  syncForm();
  settingsError.value = "";
  settingsHint.value = "";
}
async function saveSettings() {
  settingsError.value = "";
  settingsHint.value = "";
  saving.value = true;
  try {
    const res = await fetch("/api/shadow/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        enabled: formEnabled.value,
        threshold: Number(formThreshold.value),
        window_minutes: Number(formWindow.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      settingsError.value = res.status === 403
        ? (data.detail || "旁观账号不能扳开关")
        : (data.detail || "保存失败");
      return;
    }
    settingsLive.value = data;
    syncForm();
    settingsHint.value = "已保存，新交单立即按新开关/门槛/窗长判定";
  } catch { settingsError.value = "保存时网络异常"; }
  finally { saving.value = false; }
}

onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll().then(syncForm);
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1080px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.75rem; }
h2 { margin: 0 0 0.75rem; font-size: 1.05rem; color: #bbf7d0; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.topbar { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
.topbar .spacer { flex: 1; }
.topbar .who { color: #a7f3d0; font-size: 0.9rem; margin-right: 0.4rem; }
button.tab { background: #052e16; border: 1px solid #166534; color: #a7f3d0; }
button.tab.active { background: #16a34a; color: #fff; border-color: #22c55e; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
input:disabled { opacity: 0.55; }
.field-row input { width: 120px; margin-right: 1rem; margin-bottom: 0.4rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
.note { color: #a7f3d0; font-size: 0.85rem; line-height: 1.5; }
.muted { color: #86efac; font-weight: 400; opacity: 0.75; }
.center { text-align: center; }
.nowrap { white-space: nowrap; }
.reason { max-width: 320px; font-size: 0.82rem; color: #d1fae5; }
.err { color: #fecaca; }
.ok-text { color: #bbf7d0; }
.on { color: #4ade80; }
.off { color: #fca5a5; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; vertical-align: top; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; white-space: nowrap; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.settings .switch-row { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.9rem; }
.settings .live { font-size: 0.82rem; color: #a7f3d0; }
.field-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 0.25rem; }
.switch { position: relative; display: inline-block; width: 46px; height: 24px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; inset: 0; background: #7f1d1d; border-radius: 24px; transition: 0.2s; cursor: pointer; }
.slider::before { content: ""; position: absolute; width: 18px; height: 18px; left: 3px; top: 3px; background: #fff; border-radius: 50%; transition: 0.2s; }
.switch input:checked + .slider { background: #16a34a; }
.switch input:checked + .slider::before { transform: translateX(22px); }
.switch input:disabled + .slider { opacity: 0.5; cursor: not-allowed; }
</style>
