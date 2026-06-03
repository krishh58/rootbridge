async function openPersonCard(personId) {
  const overlay = document.getElementById('personCardOverlay');
  overlay.innerHTML = '<div class="card-loading">Loading...</div>';
  overlay.classList.remove('hidden');

  const [personResp, hometownResp, historyResp, docsResp] = await Promise.all([
    fetch(`/api/persons/${personId}`),
    fetch(`/api/persons/${personId}/hometown`),
    fetch(`/api/alfred/${personId}/history`),
    fetch(`/api/persons/${personId}/documents`),
  ]);

  const person = await personResp.json();
  const hometown = hometownResp.ok ? await hometownResp.json() : { available: false };
  const history = historyResp.ok ? await historyResp.json() : { messages: [] };
  const documents = docsResp.ok ? await docsResp.json() : [];

  overlay.innerHTML = buildCardHTML(person, hometown, history.messages, documents);

  overlay.addEventListener('click', e => {
    if (e.target === overlay) closePersonCard();
  }, { once: true });

  initVoiceInput(personId);
  scrollAlfredHistory();
  loadPersonMatches(personId);
}

function closePersonCard() {
  document.getElementById('personCardOverlay').classList.add('hidden');
}

function buildCardHTML(person, hometown, messages, documents) {
  const name = `${person.first_name || ''} ${person.last_name || ''}`.trim();
  const dates = [person.birth_year, person.death_year].filter(Boolean).join(' – ');
  const confidenceColor = person.confidence >= 80 ? '#3b82f6' : person.confidence >= 40 ? '#f59e0b' : '#ef4444';

  const confirmedFields = [];
  const missingFields = [];
  if (person.birth_year) confirmedFields.push('Birth year');
  else missingFields.push('Birth year');
  if (person.birth_state || person.birth_country) confirmedFields.push('Birth location');
  else missingFields.push('Birth location');
  if (person.death_year) confirmedFields.push('Death year');
  else missingFields.push('Death year');
  if ((person.parent_ids || []).length > 0) confirmedFields.push('Parents');
  else missingFields.push('Parents');
  if ((person.spouse_ids || []).length > 0) confirmedFields.push('Spouse');
  else missingFields.push('Spouse');

  const sourcesHTML = (person.search_results || []).map(r =>
    `<a href="${escapeAttr(r.url)}" target="_blank" class="source-chip">${escapeHtml(r.source)}</a>`
  ).join('') || '<span class="no-data">No sources yet</span>';

  const gapsHTML = (person.gaps || []).filter(g => !g.resolved).map(g =>
    `<div class="gap-item">✗ ${escapeHtml(g.gap_type.replace(/_/g,' '))} — <em>${escapeHtml(g.suggested_source)}</em></div>`
  ).join('') || '';

  const hometownHTML = hometown.available ? `
    <div class="hometown-panel">
      ${hometown.photo ? `<img src="${escapeAttr(hometown.photo.url)}" class="hometown-photo" alt="${escapeAttr(hometown.photo.title || '')}" onerror="this.style.display='none'">
        ${hometown.photo.is_approximate ? '<p class="approx-note">Photo is regional approximation</p>' : ''}` : ''}
      ${hometown.map ? `<div class="map-info"><strong>Historical Map:</strong> ${escapeHtml(hometown.map.title)} (${escapeHtml(String(hometown.map.year))})<br><a href="${escapeAttr(hometown.map.url)}" target="_blank">View map →</a></div>` : ''}
      ${hometown.life_context ? `<div class="life-context">${escapeHtml(hometown.life_context)}</div>` : ''}
    </div>` : '<div class="hometown-panel"><p class="no-data">Birth location unknown — add a birth state or country to see the hometown visual.</p></div>';

  const messagesHTML = messages.map(m =>
    `<div class="alfred-message alfred-${escapeHtml(m.role)}">
      ${m.role === 'assistant' ? '<span class="alfred-avatar">🎩</span>' : ''}
      <div class="alfred-bubble">${escapeHtml(m.content)}</div>
    </div>`
  ).join('');

  return `
    <div class="person-card">
      <button class="card-close" onclick="closePersonCard()">✕</button>
      <div class="card-header">
        <h2>${escapeHtml(name)}</h2>
        <span class="card-dates">${escapeHtml(dates)}</span>
        <div class="confidence-bar">
          <div class="confidence-fill" style="width:${person.confidence}%;background:${confidenceColor}"></div>
          <span class="confidence-label">${person.confidence}% confidence</span>
        </div>
      </div>
      <div class="card-panels">
        <div class="card-left">
          <h4>Confirmed</h4>
          ${confirmedFields.map(f => `<div class="check-item confirmed">✓ ${escapeHtml(f)}</div>`).join('')}
          <h4>Missing</h4>
          ${missingFields.map(f => `<div class="check-item missing">✗ ${escapeHtml(f)}</div>`).join('')}
          ${gapsHTML ? `<h4>Research Gaps</h4>${gapsHTML}` : ''}
          <h4>Sources</h4>
          <div class="sources-row">${sourcesHTML}</div>
          <button class="btn-primary search-btn" onclick="runSearch(${person.id})">Search US Records</button>
          <button class="btn-secondary search-btn" onclick="runHeritageSearch(${person.id}, 'european')" title="Requires European Roots tier">Search European Records (40 tokens)</button>
          <button class="btn-secondary search-btn" onclick="runHeritageSearch(${person.id}, 'aa')" title="Requires AA Heritage tier">Search AA Records (40 tokens)</button>
          <button class="btn-secondary search-btn" onclick="runFork(${person.id})">Start New Tree From Here</button>
          <div id="match-badge-area" style="margin-top:12px"></div>
        </div>
        <div class="card-right">
          <h4>Hometown: ${escapeHtml(person.birth_state || person.birth_country || 'Unknown')}</h4>
          ${hometownHTML}
        </div>
      </div>
      <div class="docs-panel">
        <h4 style="color:#94a3b8;font-size:.8rem;text-transform:uppercase;margin:0 0 .5rem">Documents</h4>
        <div class="docs-list" id="docsList-${person.id}">${renderDocsList(documents, person.id)}</div>
        <div style="display:flex;align-items:center;gap:.75rem;margin-top:.75rem">
          <label class="btn-secondary doc-upload-label">
            Upload Document
            <input type="file" style="display:none"
                   accept=".jpg,.jpeg,.png,.tiff,.tif,.webp,.gif,.heic,.heif,.pdf,.txt"
                   onchange="uploadDocument(${person.id}, this)">
          </label>
          <span class="doc-upload-status" id="docUploadStatus-${person.id}"></span>
        </div>
      </div>
      <div class="alfred-panel">
        <div class="alfred-header"><span class="alfred-avatar">🎩</span><strong>Alfred</strong> — Research Concierge</div>
        <div class="alfred-history" id="alfredHistory">${messagesHTML}</div>
        <div class="alfred-input-row">
          <button class="mic-btn" id="micBtn" onclick="toggleVoice()" title="Voice input">🎤</button>
          <input type="text" id="alfredInput" placeholder="Ask Alfred about ${escapeAttr(name)}... (10 tokens)"
                 onkeydown="if(event.key==='Enter')sendAlfred(${person.id})">
          <button class="btn-primary" onclick="sendAlfred(${person.id})">Send <span class="token-cost">10</span></button>
        </div>
      </div>
    </div>
    <style>
      .person-card{background:#1e293b;border-radius:12px;width:min(900px,95vw);max-height:90vh;overflow-y:auto;padding:2rem;position:relative;display:flex;flex-direction:column;gap:1.5rem}
      .card-close{position:absolute;top:1rem;right:1rem;background:none;border:none;color:#94a3b8;font-size:1.2rem;cursor:pointer}
      .card-header h2{margin:0;font-size:1.4rem}
      .card-dates{color:#94a3b8;font-size:.9rem}
      .confidence-bar{background:#334155;border-radius:4px;height:6px;margin-top:.5rem;position:relative}
      .confidence-fill{height:6px;border-radius:4px;transition:width .3s}
      .confidence-label{font-size:.75rem;color:#94a3b8;position:absolute;right:0;top:8px}
      .card-panels{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}
      .card-left,.card-right{display:flex;flex-direction:column;gap:.5rem}
      .card-left h4,.card-right h4{color:#94a3b8;font-size:.8rem;text-transform:uppercase;margin:.75rem 0 .25rem}
      .check-item{font-size:.9rem;padding:.2rem 0}
      .check-item.confirmed{color:#4ade80}
      .check-item.missing{color:#f87171}
      .gap-item{font-size:.85rem;color:#fbbf24;padding:.2rem 0}
      .sources-row{display:flex;flex-wrap:wrap;gap:.5rem}
      .source-chip{background:#1e40af;color:#93c5fd;padding:.2rem .6rem;border-radius:20px;font-size:.8rem;text-decoration:none}
      .source-chip:hover{background:#2563eb}
      .no-data{color:#64748b;font-size:.85rem}
      .search-btn{margin-top:.75rem;align-self:flex-start}
      .hometown-photo{width:100%;border-radius:8px;object-fit:cover;max-height:180px}
      .approx-note{font-size:.75rem;color:#94a3b8;margin:.25rem 0}
      .map-info{font-size:.85rem;line-height:1.6}
      .life-context{font-size:.85rem;color:#cbd5e1;font-style:italic;border-left:3px solid var(--blue);padding-left:.75rem;margin-top:.5rem}
      .docs-panel{border-top:1px solid #334155;padding-top:1.25rem}
      .doc-item{background:#0f172a;border-radius:6px;padding:.6rem .75rem;margin-bottom:.5rem}
      .doc-meta{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
      .doc-name{color:#60a5fa;font-size:.85rem;text-decoration:none;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .doc-name:hover{text-decoration:underline}
      .doc-size{color:#64748b;font-size:.78rem;white-space:nowrap}
      .doc-delete{background:none;border:none;color:#64748b;cursor:pointer;font-size:.85rem;padding:0 .25rem;flex-shrink:0}
      .doc-delete:hover{color:#f87171}
      .doc-summary{font-size:.8rem;color:#94a3b8;margin-top:.35rem;line-height:1.5;border-left:2px solid #334155;padding-left:.5rem}
      .doc-upload-label{cursor:pointer;font-size:.82rem;padding:.35rem .75rem}
      .doc-upload-status{font-size:.82rem;color:#fbbf24}
      .alfred-panel{border-top:1px solid #334155;padding-top:1.5rem}
      .alfred-header{display:flex;align-items:center;gap:.5rem;margin-bottom:1rem;font-size:.9rem}
      .alfred-avatar{font-size:1.2rem}
      .alfred-history{display:flex;flex-direction:column;gap:.75rem;max-height:200px;overflow-y:auto;padding-right:.5rem;margin-bottom:1rem}
      .alfred-message{display:flex;gap:.5rem;align-items:flex-start}
      .alfred-user .alfred-bubble{background:#1e40af;border-radius:8px 8px 0 8px;padding:.5rem .75rem;font-size:.875rem;align-self:flex-end;margin-left:auto}
      .alfred-assistant .alfred-bubble{background:#334155;border-radius:0 8px 8px 8px;padding:.5rem .75rem;font-size:.875rem}
      .alfred-input-row{display:flex;gap:.5rem;align-items:center}
      .alfred-input-row input{flex:1;background:#0f172a;border:1px solid #334155;color:#f1f5f9;padding:.5rem .75rem;border-radius:6px;font-size:.9rem}
      .alfred-input-row input:focus{outline:2px solid var(--blue)}
      .mic-btn{background:none;border:1px solid #334155;color:#f1f5f9;padding:.4rem .6rem;border-radius:6px;cursor:pointer;font-size:1rem}
      .mic-btn.listening{border-color:#ef4444;color:#ef4444;animation:pulse 1s infinite}
      @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
      .token-cost{font-size:.75rem;background:#334155;padding:.1rem .3rem;border-radius:4px}
      .hometown-panel{display:flex;flex-direction:column;gap:.75rem}
      @media(max-width:640px){.card-panels{grid-template-columns:1fr}}
    </style>`;
}

async function sendAlfred(personId) {
  const input = document.getElementById('alfredInput');
  const message = input.value.trim();
  if (!message) return;
  input.value = '';

  const history = document.getElementById('alfredHistory');
  history.innerHTML += `<div class="alfred-message alfred-user"><div class="alfred-bubble">${escapeHtml(message)}</div></div>`;
  history.innerHTML += `<div class="alfred-message alfred-assistant"><div class="alfred-bubble typing">Alfred is thinking...</div></div>`;
  scrollAlfredHistory();

  const r = await fetch(`/api/alfred/${personId}/chat`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message}),
  });
  const data = await r.json();

  const typingEl = history.querySelector('.typing');
  if (typingEl) typingEl.parentElement.outerHTML =
    `<div class="alfred-message alfred-assistant"><span class="alfred-avatar">🎩</span><div class="alfred-bubble">${escapeHtml(data.response || 'Sorry, I had trouble with that.')}</div></div>`;

  scrollAlfredHistory();
  fetch('/api/me').then(r => r.json()).then(d => {
    document.getElementById('tokenBadge').textContent = `${d.tokens} tokens`;
  });
}

async function runSearch(personId) {
  const p = await fetch(`/api/persons/${personId}`).then(r => r.json());
  const params = new URLSearchParams({
    last: p.last_name || '', first: p.first_name || '',
    birth_year: p.birth_year || '', birth_place: p.birth_state || '',
  });
  window.location.href = `/?${params}`;
}

function scrollAlfredHistory() {
  const h = document.getElementById('alfredHistory');
  if (h) h.scrollTop = h.scrollHeight;
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escapeAttr(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

let recognition = null;
let isListening = false;

function initVoiceInput(personId) {
  const micBtn = document.getElementById('micBtn');
  if (!micBtn) return;
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) { micBtn.style.display = 'none'; return; }

  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.lang = 'en-US';

  recognition.onresult = e => {
    let transcript = '';
    for (let i = e.resultIndex; i < e.results.length; i++) {
      transcript += e.results[i][0].transcript;
    }
    document.getElementById('alfredInput').value = transcript;
    if (e.results[e.results.length - 1].isFinal) {
      stopListening();
      sendAlfred(personId);
    }
  };

  recognition.onend = () => stopListening();
  recognition.onerror = () => stopListening();
}

function toggleVoice() {
  if (isListening) { stopListening(); return; }
  if (!recognition) return;
  isListening = true;
  document.getElementById('micBtn').classList.add('listening');
  recognition.start();
}

function stopListening() {
  isListening = false;
  const btn = document.getElementById('micBtn');
  if (btn) btn.classList.remove('listening');
  if (recognition) { try { recognition.stop(); } catch (e) {} }
}

async function runFork(personId) {
  const r = await fetch(`/api/persons/${personId}/fork`, { method: 'POST' });
  if (r.status === 201) {
    alert('New tree created! Redirecting...');
    window.location.href = '/';
  } else if (r.status === 404) {
    alert('Access denied.');
  } else {
    alert('Fork failed. Please try again.');
  }
}

async function runHeritageSearch(personId, type) {
  const btn = event.currentTarget || event.target;
  btn.disabled = true;
  btn.textContent = 'Searching...';

  const payload = {};
  if (type === 'european') {
    const country = prompt('Enter origin country (e.g. Germany, Ireland, Italy, Poland, Sweden):');
    if (country) payload.origin_country = country;
  }

  const r = await fetch(`/api/heritage/${personId}/${type}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  });

  const label = type === 'european' ? 'Search European Records (40 tokens)' : 'Search AA Records (40 tokens)';
  btn.disabled = false;
  btn.textContent = label;

  if (r.status === 403) {
    const data = await r.json();
    alert(`This feature requires an upgrade.\nCurrent tier: ${data.current_tier}\nVisit /pricing to upgrade.`);
    return;
  }
  if (r.status === 402) {
    alert('Insufficient tokens. Please top up to run a heritage search (40 tokens required).');
    return;
  }
  if (!r.ok) {
    alert('Search failed. Please try again.');
    return;
  }

  const data = await r.json();

  if (data.wall_1870) {
    const wall = data.wall_1870;
    const guidance = wall.guidance.map(g => `• ${g}`).join('\n');
    alert(`⚠️ 1870 Wall Detected\n\n${wall.message}\n\nNext steps:\n${guidance}`);
  }

  openPersonCard(personId);

  fetch('/api/me').then(r => r.json()).then(d => {
    const badge = document.getElementById('tokenBadge');
    if (badge) badge.textContent = `${d.tokens} tokens`;
  }).catch(() => {});
}

const _personMatchCache = {};

async function loadPersonMatches(personId) {
  const area = document.getElementById('match-badge-area');
  if (!area) return;
  try {
    const r = await fetch(`/api/persons/${personId}/matches`, { credentials: 'include' });
    if (!r.ok) return;
    const matches = await r.json();
    if (!matches.length) return;
    _personMatchCache[personId] = matches;
    area.innerHTML = `
      <div style="background:#1a3d2b;border:1px solid #4a7c59;border-radius:8px;padding:10px 14px;cursor:pointer"
           onclick="openMatchModal(_personMatchCache[${parseInt(personId, 10)}])">
        <span style="color:#4ade80;font-weight:600">&#x1f465; ${matches.length} researcher${matches.length > 1 ? 's' : ''} found</span>
        <span style="color:#86efac;font-size:0.85em;margin-left:8px">Connect &#x2192;</span>
      </div>`;
  } catch (e) { /* silently ignore — don't break the card if matches fail to load */ }
}

function openMatchModal(matches) {
  const existing = document.getElementById('match-modal');
  if (existing) existing.remove();

  const safeUrl = (u) => (u && /^https?:\/\//.test(u)) ? u : '#';

  const modal = document.createElement('div');
  modal.id = 'match-modal';
  modal.style.cssText = `
    position:fixed;inset:0;background:rgba(0,0,0,0.7);z-index:9999;
    display:flex;align-items:center;justify-content:center;padding:16px`;
  modal.onclick = (e) => { if (e.target === modal) modal.remove(); };

  const matchCards = matches.map(m => {
    const theirNew = m.their_records.filter(r => r.you_dont_have).length;
    const theirRows = m.their_records.map(r => `
      <div style="padding:4px 0;border-bottom:1px solid #1e293b;${r.you_dont_have ? 'color:#4ade80' : 'color:#94a3b8'}">
        ${r.you_dont_have ? '&#x2605; ' : ''}${escapeHtml(r.source)} &mdash; ${escapeHtml(r.record_type) || 'Record'}
        ${r.url ? `<a href="${escapeAttr(safeUrl(r.url))}" target="_blank" style="color:#60a5fa;font-size:0.8em;margin-left:6px">view</a>` : ''}
      </div>`).join('');
    const yourRows = m.your_records.map(r => `
      <div style="padding:4px 0;border-bottom:1px solid #1e293b;${m.your_records_they_dont_have.includes(r.url) ? 'color:#60a5fa' : 'color:#94a3b8'}">
        ${m.your_records_they_dont_have.includes(r.url) ? '&#x2605; ' : ''}${escapeHtml(r.source)} &mdash; ${escapeHtml(r.record_type) || 'Record'}
      </div>`).join('');
    return `
      <div style="background:#1e293b;border-radius:10px;padding:16px;margin-bottom:12px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
          <div>
            <span style="color:#f8fafc;font-weight:600">${escapeHtml(m.display_name)}</span>
            <span style="color:#94a3b8;font-size:0.85em;margin-left:8px">${escapeHtml(m.ancestor_name)}</span>
          </div>
          <span style="color:#4ade80;font-size:0.8em">${theirNew} new records for you</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px">
          <div>
            <div style="color:#60a5fa;font-size:0.75em;font-weight:600;margin-bottom:6px">YOUR RECORDS</div>
            ${yourRows || '<div style="color:#64748b;font-size:0.85em">None yet</div>'}
          </div>
          <div>
            <div style="color:#4ade80;font-size:0.75em;font-weight:600;margin-bottom:6px">${escapeHtml(m.display_name).toUpperCase()}&rsquo;S RECORDS</div>
            ${theirRows || '<div style="color:#64748b;font-size:0.85em">None yet</div>'}
          </div>
        </div>
        <button onclick="startConversation(${Number(m.match_id)}); document.getElementById('match-modal').remove();"
                style="width:100%;background:#4a7c59;color:#fff;border:none;border-radius:6px;
                       padding:10px;font-size:0.9em;cursor:pointer">
          Start conversation with ${escapeHtml(m.display_name)} &#x2192;
        </button>
      </div>`;
  }).join('');

  modal.innerHTML = `
    <div style="background:#0f172a;border-radius:12px;padding:20px;max-width:640px;width:100%;
                max-height:80vh;overflow-y:auto;border:1px solid #334155">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
        <h3 style="color:#f8fafc;margin:0">Researchers on this ancestor</h3>
        <button onclick="document.getElementById('match-modal').remove()"
                style="background:none;border:none;color:#94a3b8;font-size:1.2em;cursor:pointer">&#x2715;</button>
      </div>
      <p style="color:#94a3b8;font-size:0.85em;margin-bottom:16px">
        &#x2605; = records the other researcher has that you don&rsquo;t (green) or you have that they don&rsquo;t (blue)
      </p>
      ${matchCards}
    </div>`;
  document.body.appendChild(modal);
}

function renderDocsList(docs, personId) {
  if (!docs || !docs.length) return '<p class="no-data" style="font-size:.85rem">No documents uploaded yet.</p>';
  return docs.map(doc => `
    <div class="doc-item">
      <div class="doc-meta">
        <a href="/api/documents/${doc.id}" target="_blank" class="doc-name" title="${escapeAttr(doc.filename)}">${escapeHtml(doc.filename)}</a>
        <span class="doc-size">${formatBytes(doc.file_size)}</span>
        <button class="doc-delete" onclick="deleteDocument(${doc.id}, ${personId})" title="Delete document">✕</button>
      </div>
      ${doc.ai_summary ? `<div class="doc-summary">${escapeHtml(doc.ai_summary)}</div>` : ''}
    </div>`).join('');
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

async function uploadDocument(personId, input) {
  const file = input.files[0];
  if (!file) return;
  const status = document.getElementById(`docUploadStatus-${personId}`);
  status.textContent = 'Uploading & analyzing…';

  const formData = new FormData();
  formData.append('file', file);

  try {
    const r = await fetch(`/api/persons/${personId}/documents`, { method: 'POST', body: formData });
    const data = await r.json();
    if (r.status === 201) {
      status.textContent = '';
      const docsResp = await fetch(`/api/persons/${personId}/documents`);
      const docs = docsResp.ok ? await docsResp.json() : [];
      document.getElementById(`docsList-${personId}`).innerHTML = renderDocsList(docs, personId);
    } else {
      status.textContent = data.error || 'Upload failed.';
    }
  } catch (e) {
    status.textContent = 'Upload failed — check connection.';
  }
  input.value = '';
}

async function deleteDocument(docId, personId) {
  if (!confirm('Delete this document?')) return;
  const r = await fetch(`/api/documents/${docId}`, { method: 'DELETE' });
  if (r.ok) {
    const docsResp = await fetch(`/api/persons/${personId}/documents`);
    const docs = docsResp.ok ? await docsResp.json() : [];
    document.getElementById(`docsList-${personId}`).innerHTML = renderDocsList(docs, personId);
  }
}

function startConversation(matchId) {
  const tab = document.querySelector('[data-tab="connections"]');
  if (tab) tab.click();
  setTimeout(() => {
    if (typeof openThread === 'function') openThread(matchId);
  }, 100);
}
