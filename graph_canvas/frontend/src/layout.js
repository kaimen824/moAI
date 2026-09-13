import dagre from '@dagrejs/dagre'

/** 结构层布局:与 /api/graph 同源(dagre 自动),TB/LR 可切换。 */
export function layoutGraph(graph, dir = 'TB') {
  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: dir, nodesep: 55, ranksep: 95, edgesep: 18, marginx: 40, marginy: 40 })
  g.setDefaultEdgeLabel(() => ({}))
  const NW = 196, NH = 62
  graph.nodes.forEach((n) =>
    g.setNode(n.id, { width: n.kind === 'terminal' ? 84 : NW, height: n.kind === 'terminal' ? 36 : NH }))
  graph.edges.forEach((e) => g.setEdge(e.source, e.target))
  dagre.layout(g)
  const pos = {}
  graph.nodes.forEach((n) => {
    const p = g.node(n.id)
    pos[n.id] = { x: p.x - p.width / 2, y: p.y - p.height / 2 }
  })
  return pos
}
