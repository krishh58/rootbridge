const NODE_W = 110;
const NODE_H = 48;
const NODE_RX = 16;

async function loadTree(treeId) {
  const r = await fetch(`/api/trees/${treeId}`);
  if (!r.ok) return;
  const tree = await r.json();
  renderTree(tree.persons, treeId);
  if (typeof showExportButtons === 'function') showExportButtons(tree.persons && tree.persons.length > 0);
}

function renderTree(persons, treeId) {
  const container = document.getElementById('treeContainer');
  container.innerHTML = '';
  if (!persons || persons.length === 0) {
    container.innerHTML = '<p class="placeholder-text">No persons in tree yet.</p>';
    return;
  }

  const width = container.clientWidth || 900;
  const height = container.clientHeight || 600;

  const idSet = new Set(persons.map(p => p.id));
  const edges = [];
  persons.forEach(p => {
    (p.parent_ids || []).forEach(parentId => {
      if (idSet.has(parentId)) {
        edges.push({ source: parentId, target: p.id });
      }
    });
  });

  const hasParent = new Set(edges.map(e => e.target));
  const roots = persons.filter(p => !hasParent.has(p.id));
  if (roots.length === 0) roots.push(persons[0]);

  const childMap = {};
  persons.forEach(p => { childMap[p.id] = []; });
  edges.forEach(e => { childMap[e.source].push(e.target); });

  const personById = Object.fromEntries(persons.map(p => [p.id, p]));

  function buildHierarchy(id) {
    const p = personById[id];
    return { data: p, children: childMap[id].map(buildHierarchy) };
  }

  const hierarchyData = d3.hierarchy(buildHierarchy(roots[0].id));
  const treeLayout = d3.tree().size([width - 80, height - 120]);
  treeLayout(hierarchyData);

  const layoutNodes = hierarchyData.descendants().map(d => d.data.data);
  const layoutIds = new Set(layoutNodes.map(p => p.id));
  const floating = persons.filter(p => !layoutIds.has(p.id));

  const svg = d3.select('#treeContainer').append('svg')
    .attr('width', width).attr('height', height);

  const g = svg.append('g').attr('transform', 'translate(40, 60)');

  g.selectAll('.link')
    .data(hierarchyData.links())
    .enter().append('path')
    .attr('class', 'link')
    .attr('d', d3.linkVertical().x(d => d.x).y(d => d.y));

  const node = g.selectAll('.node')
    .data(hierarchyData.descendants())
    .enter().append('g')
    .attr('class', d => `node ${nodeClass(d.data.data.confidence)}`)
    .attr('transform', d => `translate(${d.x},${d.y})`)
    .style('cursor', 'pointer')
    .on('click', (event, d) => openPersonCard(d.data.data.id));

  node.append('rect')
    .attr('x', -NODE_W / 2).attr('y', -NODE_H / 2)
    .attr('width', NODE_W).attr('height', NODE_H)
    .attr('rx', NODE_RX).attr('ry', NODE_RX);
  node.append('text').attr('dy', -4).attr('text-anchor', 'middle')
    .text(d => shortName(d.data.data));
  node.append('text').attr('dy', 13).attr('text-anchor', 'middle')
    .style('font-size', '9px').style('fill', '#94a3b8')
    .text(d => d.data.data.birth_year || '?');

  floating.forEach((p, i) => {
    const fx = 60 + (i * 80) % (width - 100);
    const fy = height - 80;
    const fn = g.append('g')
      .attr('class', `node ${nodeClass(p.confidence)}`)
      .attr('transform', `translate(${fx},${fy})`)
      .style('cursor', 'pointer')
      .on('click', () => openPersonCard(p.id));
    fn.append('rect')
      .attr('x', -NODE_W / 2).attr('y', -NODE_H / 2)
      .attr('width', NODE_W).attr('height', NODE_H)
      .attr('rx', NODE_RX).attr('ry', NODE_RX);
    fn.append('text').attr('dy', -4).attr('text-anchor', 'middle').text(shortName(p));
    fn.append('text').attr('dy', 13).attr('text-anchor', 'middle')
      .style('font-size', '9px').style('fill', '#94a3b8')
      .text(p.birth_year || '?');
  });

  svg.call(d3.zoom().scaleExtent([0.3, 2]).on('zoom', e => g.attr('transform', e.transform)));
}

function nodeClass(confidence) {
  if (confidence >= 80) return 'node-confirmed';
  if (confidence >= 40) return 'node-partial';
  return 'node-gap';
}

function shortName(p) {
  const first = (p.first_name || '').charAt(0);
  const last = (p.last_name || '').slice(0, 8);
  return first ? `${first}. ${last}` : last;
}
