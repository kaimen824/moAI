const BASE = "/api";
const TOKEN_KEY = "molan-token";

/* ---- 认证(ADR-0022):Bearer token,localStorage 持久 ---- */
export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (t) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

/** 401 统一处理:清 token 并广播,App 层跳回登录。 */
function onUnauthorized() {
  clearToken();
  window.dispatchEvent(new Event("molan-unauthorized"));
}

function authHeaders(extra = {}) {
  const t = getToken();
  return t ? { Authorization: `Bearer ${t}`, ...extra } : { ...extra };
}

async function j(path, opts = {}) {
  const r = await fetch(BASE + path, {
    headers: authHeaders({ "Content-Type": "application/json" }),
    ...opts,
  });
  if (r.status === 401) onUnauthorized();
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

/** POST SSE:解析 event/data 流,回调 onEvent(kind, data)。 */
async function sse(path, body, onEvent) {
  const r = await fetch(BASE + path, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  if (r.status === 401) { onUnauthorized(); throw new Error("401 未登录或会话已过期"); }
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let kind = null, data = null;
      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) kind = line.slice(7).trim();
        else if (line.startsWith("data: ")) data = JSON.parse(line.slice(6));
      }
      if (kind) onEvent(kind, data);
      if (kind === "interrupt" || kind === "done" || kind === "error") return;
    }
  }
}

export const api = {
  /* 认证 */
  login: async (username, password) => {
    const r = await fetch(`${BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const data = await r.json();
    setToken(data.access_token);
    return data;
  },
  logout: () => clearToken(),
  me: () => getToken() || "",

  createStory: (title, premise) => j("/stories", { method: "POST", body: JSON.stringify({ title, premise }) }),
  listStories: () => j("/stories"),
  storyDetail: (id) => j(`/stories/${id}`),
  chapter: (id, no) => j(`/stories/${id}/chapters/${no}`),
  generate: (id, payload, onEvent) => sse(`/stories/${id}/generate`, payload, onEvent),
  resume: (id, payload, onEvent) => sse(`/stories/${id}/resume`, payload, onEvent),
  stop: (id) => j(`/stories/${id}/stop`, { method: "POST" }),
  attach: (id, onEvent) => sse(`/stories/${id}/attach`, {}, onEvent),
  runState: (id) => j(`/stories/${id}/run-state`),
  directive: (id, text) => j(`/stories/${id}/directive`, { method: "POST", body: JSON.stringify({ text }) }),
  pendingFacts: () => j("/facts/pending"),
  reviewFact: (fid, approve) => j(`/facts/${fid}/review`, { method: "POST", body: JSON.stringify({ approve }) }),
  entityProposals: () => j("/entities/pending"),
  reviewEntityProposal: (pid, action) => j(`/entities/${pid}/review`, { method: "POST", body: JSON.stringify({ action }) }),
  usage: (id) => j(`/stories/${id}/usage`),
  traces: (id, limit = 100) => j(`/stories/${id}/traces?limit=${limit}`),
  codex: (id) => j(`/stories/${id}/codex`),
  models: () => j("/config/models"),
  setModel: (role, model) => j("/config/models", { method: "POST", body: JSON.stringify({ role, model }) }),
};
