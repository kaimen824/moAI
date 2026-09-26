import dagre from '@dagrejs/dagre'

/* 结构层布局:dagre 分区拼装——组内自动布局,组间按依赖顺序堆叠。
   返回 {positions, boxes}:boxes 供组容器(背景节点)渲染边界。 */

const NW = 210, NH = 66          // 标准卡片栅格
const TERM_W = 84, TERM_H = 36   // START/END 胶囊
const BUCKET_GAP = 120           // 组间距
const PAD_X = 34                 // 组容器左右内边距
const PAD_TOP = 46               // 组容器顶部(容纳组名标签)
const PAD_BOTTOM = 26

function sizeOf(n) {
  return n.kind === 'terminal'
    ? { width: TERM_W, height: TERM_H }
    : { width: NW, height: NH }
}

/** 单桶 dagre:返回桶内相对坐标与桶内容尺寸。 */
function layoutBucket(bucketNodes, allEdges, dir) {
  const ids = new Set(bucketNodes.map((n) => n.id))
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: dir, nodesep: 72, ranksep: 96, edgesep: 20, marginx: 8, marginy: 8 })
  g.setDefaultEdgeLabel(() => ({}))
  bucketNodes.forEach((n) => {
    const { width, height } = sizeOf(n)
    g.setNode(n.id, { width, height })
  })
  // 只有同桶两端的边参与本桶布局;跨组边不进 dagre(避免拉扯桶内排布)
  allEdges.forEach((e) => {
    if (ids.has(e.source) && ids.has(e.target)) g.setEdge(e.source, e.target)
  })
  dagre.layout(g)
  const pos = {}
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity
  bucketNodes.forEach((n) => {
    const p = g.node(n.id)
    pos[n.id] = { x: p.x - p.width / 2, y: p.y - p.height / 2 }
    minx = Math.min(minx, pos[n.id].x)
    miny = Math.min(miny, pos[n.id].y)
    maxx = Math.max(maxx, pos[n.id].x + p.width)
    maxy = Math.max(maxy, pos[n.id].y + p.height)
  })
  // 归零:桶内坐标从 (0,0) 起
  Object.values(pos).forEach((p) => { p.x -= minx; p.y -= miny })
  return { pos, w: maxx - minx, h: maxy - miny }
}

/** 桶顺序:START → 三个业务组(按 graph.groups 声明序)→ 其余(含 END/未登记)。 */
function bucketOrder(graph) {
  const order = [{ key: '__start__' }]
  ;(graph.groups || []).forEach((grp) => order.push({ key: grp.key }))
  order.push({ key: '__rest__' })
  return order
}

export function layoutGraph(graph, dir = 'TB') {
  const groupOf = (n) => n.meta?.group
    ?? (n.id === '__start__' ? '__start__' : n.kind === 'terminal' ? '__rest__' : '__rest__')

  const buckets = new Map()   // key -> { nodes, def }
  for (const ord of bucketOrder(graph)) buckets.set(ord.key, { nodes: [], def: ord })
  graph.nodes.forEach((n) => {
    const key = groupOf(n)
    if (!buckets.has(key)) buckets.set(key, { nodes: [], def: { key } })
    buckets.get(key).nodes.push(n)
  })

  // 每桶布局
  const laid = []
  for (const [key, bucket] of buckets) {
    if (!bucket.nodes.length) continue
    const { pos, w, h } = layoutBucket(bucket.nodes, graph.edges, dir)
    const bw = w + PAD_X * 2
    const bh = h + PAD_TOP + PAD_BOTTOM
    laid.push({ key, def: bucket.def, pos, bw, bh })
  }

  // 桶间堆叠:主轴按序累积,交叉轴整体居中于最宽桶
  const maxW = Math.max(...laid.map((b) => b.bw))
  const positions = {}
  const boxes = []
  let along = 0
  for (const b of laid) {
    const cross = (maxW - b.bw) / 2
    const box = dir === 'TB'
      ? { x: cross, y: along, w: b.bw, h: b.bh }
      : { x: along, y: cross, w: b.bw, h: b.bh }
    for (const [id, p] of Object.entries(b.pos)) {
      positions[id] = { x: box.x + PAD_X + p.x, y: box.y + PAD_TOP + p.y }
    }
    const grpDef = (graph.groups || []).find((g) => g.key === b.key)
    boxes.push({ ...box, key: b.key, zh: grpDef?.zh || null, accent: grpDef?.accent || null })
    along += (dir === 'TB' ? b.bh : b.bw) + BUCKET_GAP
  }
  return { positions, boxes }
}
