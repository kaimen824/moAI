export async function fetchJSON(url) {
  const r = await fetch(url, { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

export const getGraph = () => fetchJSON('/api/graph')
export const getThreads = () => fetchJSON('/api/threads')
export const getRun = (tid) => fetchJSON(`/api/runs/${encodeURIComponent(tid)}`)
export const getState = (tid, ck) =>
  fetchJSON(`/api/state/${encodeURIComponent(tid)}/${encodeURIComponent(ck)}`)
