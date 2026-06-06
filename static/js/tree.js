const NODE_H = 48;
const NODE_RX = 16;
const NODE_PAD = 18; // horizontal padding inside oval

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
  const treeLayout = d3.tree().size([height - 120, width - 200]);
  treeLayout(hierarchyData);

  const layoutNodes = hierarchyData.descendants().map(d => d.data.data);
  const layoutIds = new Set(layoutNodes.map(p => p.id));
  const floating = persons.filter(p => !layoutIds.has(p.id));

  const svg = d3.select('#treeContainer').append('svg')
    .attr('width', width).attr('height', height);

  const g = svg.append('g').attr('transform', 'translate(80, 60)');

  g.selectAll('.link')
    .data(hierarchyData.links())
    .enter().append('path')
    .attr('class', 'link')
    .attr('d', d3.linkHorizontal().x(d => d.y).y(d => d.x));

  const node = g.selectAll('.node')
    .data(hierarchyData.descendants())
    .enter().append('g')
    .attr('class', d => `node ${nodeClass(d.data.data.confidence)}`)
    .attr('transform', d => `translate(${d.y},${d.x})`)
    .style('cursor', 'pointer')
    .on('click', (event, d) => openPersonCard(d.data.data.id));

  node.append('text').attr('class', 'node-label').attr('dy', -4).attr('text-anchor', 'middle')
    .text(d => fullName(d.data.data));
  node.append('text').attr('dy', 13).attr('text-anchor', 'middle')
    .style('font-size', '9px').style('fill', '#94a3b8')
    .text(d => d.data.data.birth_year || '?');
  node.each(function() {
    const bbox = d3.select(this).select('.node-label').node().getBBox();
    const w = Math.max(bbox.width + NODE_PAD * 2, 80);
    d3.select(this).insert('rect', '.node-label')
      .attr('x', -w / 2).attr('y', -NODE_H / 2)
      .attr('width', w).attr('height', NODE_H)
      .attr('rx', NODE_RX).attr('ry', NODE_RX);
  });

  floating.forEach((p, i) => {
    const fx = 60 + (i * 80) % (width - 100);
    const fy = height - 80;
    const fn = g.append('g')
      .attr('class', `node ${nodeClass(p.confidence)}`)
      .attr('transform', `translate(${fx},${fy})`)
      .style('cursor', 'pointer')
      .on('click', () => openPersonCard(p.id));
    fn.append('text').attr('class', 'node-label').attr('dy', -4).attr('text-anchor', 'middle').text(fullName(p));
    fn.append('text').attr('dy', 13).attr('text-anchor', 'middle')
      .style('font-size', '9px').style('fill', '#94a3b8')
      .text(p.birth_year || '?');
    const bbox = fn.select('.node-label').node().getBBox();
    const w = Math.max(bbox.width + NODE_PAD * 2, 80);
    fn.insert('rect', '.node-label')
      .attr('x', -w / 2).attr('y', -NODE_H / 2)
      .attr('width', w).attr('height', NODE_H)
      .attr('rx', NODE_RX).attr('ry', NODE_RX);
  });

  svg.call(d3.zoom().scaleExtent([0.3, 2]).on('zoom', e => g.attr('transform', e.transform)));
}

function nodeClass(confidence) {
  if (confidence >= 80) return 'node-confirmed';
  if (confidence >= 40) return 'node-partial';
  return 'node-gap';
}

function fullName(p) {
  const first = (p.first_name || '').trim();
  const last = (p.last_name || '').trim();
  return [first, last].filter(Boolean).join(' ') || '?';
}
