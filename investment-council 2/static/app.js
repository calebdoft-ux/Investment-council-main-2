// ═══════════════════════════════════════
// AGENT DEFINITIONS (mirrors server agents/council.py)
// ═══════════════════════════════════════
const INVESTORS = [
  {id:'banker',name:'David Kessler',role:'Investment Banker',emoji:'🎩',color:'#d4a843',bg:'rgba(212,168,67,.15)',tag:'IB',qtype:'QUANT'},
  {id:'macro',name:'Rachel Stern',role:'Macro Strategist',emoji:'📊',color:'#5294e0',bg:'rgba(82,148,224,.15)',tag:'MACRO',qtype:'QUAL'},
  {id:'geo',name:'Tom Callahan',role:'Geopolitical Analyst',emoji:'🌍',color:'#9b6ee0',bg:'rgba(155,110,224,.15)',tag:'GEO',qtype:'QUAL'},
  {id:'quant',name:'Arjun Mehta',role:'Quantitative Analyst',emoji:'⚡',color:'#52c48a',bg:'rgba(82,196,138,.15)',tag:'QUANT',qtype:'QUANT'},
  {id:'growth',name:'Sarah Park',role:'Tech & Growth Investor',emoji:'🚀',color:'#e05252',bg:'rgba(224,82,82,.15)',tag:'GROWTH',qtype:'QUAL'},
  {id:'risk',name:'Mark Osei',role:'Risk Manager',emoji:'🛡️',color:'#e0834a',bg:'rgba(224,131,74,.15)',tag:'RISK',qtype:'QUANT'},
  {id:'chair',name:'The Chair',role:'Portfolio Manager',emoji:'🏦',color:'#52c48a',bg:'rgba(82,196,138,.12)',tag:'PM',qtype:'SYNTH'},
];
const RESEARCHERS = [
  {id:'fund',name:'Nina Kovac',role:'Fundamental Analyst',emoji:'🔬',color:'#4ab8c4',bg:'rgba(74,184,196,.15)',tag:'FUND',qtype:'QUAL'},
  {id:'tech',name:'Rex Huang',role:'Technical Analyst',emoji:'📈',color:'#d4a843',bg:'rgba(212,168,67,.15)',tag:'TECH',qtype:'QUANT'},
  {id:'screen',name:'Zoe Chan',role:'Quantitative Screener',emoji:'🧮',color:'#52c48a',bg:'rgba(82,196,138,.15)',tag:'SCREEN',qtype:'QUANT'},
  {id:'theme',name:'Dev Patel',role:'Thematic Researcher',emoji:'🌐',color:'#9b6ee0',bg:'rgba(155,110,224,.15)',tag:'THEME',qtype:'QUAL'},
  {id:'contra',name:'Cass Rivera',role:'Contrarian Analyst',emoji:'🎯',color:'#e05252',bg:'rgba(224,82,82,.15)',tag:'CONTRA',qtype:'QUAL'},
];
const NEWS_DESK = [
  {id:'mkts',name:'Elena Marsh',role:'Markets Reporter',emoji:'📰',color:'#e0834a',bg:'rgba(224,131,74,.15)',tag:'MKTS',qtype:'QUAL'},
  {id:'policy',name:'James Wei',role:'Macro & Policy Reporter',emoji:'🏛️',color:'#5294e0',bg:'rgba(82,148,224,.15)',tag:'POLICY',qtype:'QUAL'},
  {id:'corp',name:'Priya Das',role:'Corporate Reporter',emoji:'🏢',color:'#52c48a',bg:'rgba(82,196,138,.15)',tag:'CORP',qtype:'QUANT'},
];
const ALL_AGENTS = {};
[...INVESTORS, ...RESEARCHERS, ...NEWS_DESK].forEach(a => ALL_AGENTS[a.id] = a);

const MAX_HIST = 8; // max user/assistant pairs kept in history for API calls

// ═══════════════════════════════════════
// STATE
// ═══════════════════════════════════════
let team = 'inv', iMode = 'solo', rMode = 'solo', nMode = 'solo';
let selMember = null, debMembers = [], debTeam = 'inv';
// chatHist includes both user and advisor messages for history display + solo API context
let chatHist = [], loading = false;
let portfolio = {}, cash = 0, watchlist = [], journal = [];
let sessions = [], sesId = null;
let briefing = '';
let lastQuote = null;

// Per-agent mic state: undefined/true = enabled, false = disabled
const enabledAgents = {};

let lastPriceRefresh = 0; // timestamp of last price refresh (throttle silent refresh)

// Active streaming state
let activeBlock = null;
let activeSynthBody = null;
let soloStreamBubble = null;
let soloStreamText = '';
let seqStreamEntry = null;
let seqStreamText = '';
const blockTypingRows = {};
let activeBlockIsResearch = false;
let researchBlockTexts = [];

// ═══════════════════════════════════════
// INIT
// ═══════════════════════════════════════
async function init() {
  renderSidebars();
  await loadData();
  await loadJournal();
  updateCtx();
  renderHist();
}

async function loadData() {
  try {
    const [pfRes, sessRes] = await Promise.all([
      fetch('/api/portfolio').then(r => r.json()),
      fetch('/api/sessions').then(r => r.json()),
    ]);
    portfolio = pfRes.portfolio || {};
    cash = pfRes.cash || 0;
    briefing = pfRes.briefing || '';
    sessions = sessRes || [];
    watchlist = JSON.parse(localStorage.getItem('ic_wl') || '[]');
  } catch(e) {
    portfolio = {};
    cash = 0;
  }
  renderPfBar();
}

// ═══════════════════════════════════════
// AGENT ENABLE TOGGLE
// ═══════════════════════════════════════
function toggleAgent(id) {
  enabledAgents[id] = enabledAgents[id] === false ? true : false;
  const btn = document.getElementById('mic-' + id);
  if (btn) {
    const on = enabledAgents[id] !== false;
    btn.className = 'mic-tog' + (on ? '' : ' off');
    btn.title = on ? 'Click to mute' : 'Click to unmute';
  }
}

function getEnabledAgentIds() {
  const allIds = [...INVESTORS, ...RESEARCHERS, ...NEWS_DESK].map(a => a.id);
  const hasDisabled = allIds.some(id => enabledAgents[id] === false);
  if (!hasDisabled) return []; // empty = all enabled (backend default)
  return allIds.filter(id => enabledAgents[id] !== false);
}

// ═══════════════════════════════════════
// SIDEBAR RENDER
// ═══════════════════════════════════════
function renderSidebars() {
  renderML('ml-inv', INVESTORS);
  renderML('ml-res', RESEARCHERS);
  renderML('ml-news', NEWS_DESK);
}

function renderML(id, arr) {
  const el = document.getElementById(id);
  if (!el) return;
  el.innerHTML = arr.map(m => {
    const on = enabledAgents[m.id] !== false;
    return `<div class="mem-row">
      <button class="mem-btn" id="mb-${m.id}" onclick="selMem('${m.id}')">
        <div class="mem-av" style="background:${m.bg}">${m.emoji}</div>
        <div><div class="mem-name">${m.name}</div><div class="mem-role">${m.role}</div></div>
      </button>
      <button class="mic-tog${on ? '' : ' off'}" id="mic-${m.id}" onclick="event.stopPropagation();toggleAgent('${m.id}')" title="${on ? 'Click to mute' : 'Click to unmute'}">●</button>
    </div>`;
  }).join('');
}

// ═══════════════════════════════════════
// TEAM / MODE SWITCHING
// ═══════════════════════════════════════
function switchTeam(t) {
  team = t; selMember = null;
  ['inv','res','news'].forEach(x => {
    document.getElementById('nt-'+x)?.classList.toggle('active', x === t);
    const sb = document.getElementById('sb-'+x);
    if (sb) sb.style.display = x === t ? 'block' : 'none';
  });
  showChat(); clearMsgs(); updateCtx();
  const h = document.getElementById('hints');
  if (t === 'inv') h.innerHTML = '<span class="hint" onclick="fc(\'/add \')">/add</span><span class="hint" onclick="fc(\'/sell \')">/sell</span><span class="hint" onclick="fc(\'/watch \')">/watch</span><span class="hint" onclick="fc(\'/pm\')">/pm</span><span class="hint" onclick="setMode(\'all\')">/council</span><span class="hint" onclick="setMode(\'round\')">/round</span><span class="hint" onclick="openDebate(\'inv\')">/debate</span>';
  else if (t === 'res') h.innerHTML = '<span class="hint" onclick="setRMode(\'all\')">All Researchers</span><span class="hint" onclick="setRMode(\'round\')">Round Table</span><span class="hint" onclick="setRMode(\'brief\')">Brief Investors</span><span class="hint" onclick="fc(\'/find \')">/find</span>';
  else h.innerHTML = '<span class="hint" onclick="fc(\'/news \')">/news</span><span class="hint" onclick="setNMode(\'all\')">Full Desk</span><span class="hint" onclick="setNMode(\'round\')">Round Table</span><span class="hint" onclick="setNMode(\'brief\')">Brief All Teams</span>';
}

function showChat() {
  document.getElementById('mainArea').style.display = 'flex';
  ['pfView','journalView','setupView'].forEach(id => document.getElementById(id)?.classList.remove('on'));
  ['pf','journal','setup'].forEach(t => document.getElementById('nt-'+t)?.classList.remove('active'));
}

function switchTab(t) {
  ['pf','journal','setup'].forEach(x => document.getElementById('nt-'+x)?.classList.toggle('active', x === t));
  ['inv','res','news'].forEach(x => document.getElementById('nt-'+x)?.classList.remove('active'));
  document.getElementById('mainArea').style.display = 'none';
  document.getElementById('pfView').classList.toggle('on', t === 'pf');
  document.getElementById('journalView').classList.toggle('on', t === 'journal');
  document.getElementById('setupView').classList.toggle('on', t === 'setup');
  if (t === 'pf') { renderPfView(); renderWL(); silentRefreshPrices(); }
  if (t === 'journal') {
    renderJournal();
    const jd = document.getElementById('jdate');
    if (jd && !jd.value) jd.value = new Date().toISOString().split('T')[0];
  }
  if (t === 'setup') renderSetup();
}

function setMode(m) {
  iMode = m;
  ['solo','all','pm','debate','round'].forEach(x => document.getElementById('mi-'+x)?.classList.remove('active'));
  document.getElementById('mi-'+m)?.classList.add('active');
  if (m === 'debate') { openDebate('inv'); return; }
  if (m !== 'solo') selMember = null;
  chatHist = []; updateCtx(); clearMsgs();
}

function setRMode(m) {
  rMode = m;
  ['solo','all','brief','round'].forEach(x => document.getElementById('mr-'+x)?.classList.remove('active'));
  document.getElementById('mr-'+m)?.classList.add('active');
  if (m !== 'solo') selMember = null;
  chatHist = []; updateCtx(); clearMsgs();
}

function setNMode(m) {
  nMode = m;
  ['solo','all','brief','round'].forEach(x => document.getElementById('mn-'+x)?.classList.remove('active'));
  document.getElementById('mn-'+m)?.classList.add('active');
  if (m !== 'solo') selMember = null;
  chatHist = []; updateCtx(); clearMsgs();
}

function selMem(id) {
  selMember = id;
  document.querySelectorAll('.mem-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('mb-'+id)?.classList.add('active');
  if (team === 'inv') { iMode = 'solo'; ['solo','all','pm','debate','round'].forEach(x => document.getElementById('mi-'+x)?.classList.remove('active')); document.getElementById('mi-solo')?.classList.add('active'); }
  else if (team === 'res') { rMode = 'solo'; ['solo','all','brief','round'].forEach(x => document.getElementById('mr-'+x)?.classList.remove('active')); document.getElementById('mr-solo')?.classList.add('active'); }
  else { nMode = 'solo'; ['solo','all','brief','round'].forEach(x => document.getElementById('mn-'+x)?.classList.remove('active')); document.getElementById('mn-solo')?.classList.add('active'); }
  chatHist = []; updateCtx(); clearMsgs();
}

// ═══════════════════════════════════════
// CONTEXT BAR
// ═══════════════════════════════════════
function updateCtx() {
  const avs = document.getElementById('ctxAvs'), name = document.getElementById('ctxName'), badge = document.getElementById('ctxBadge');
  badge.style.display = 'inline-block';
  if (team === 'inv') {
    if (iMode === 'solo' && selMember) { const m = ALL_AGENTS[selMember]; avs.innerHTML = `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`; name.textContent = m.name; badge.className = 'ctx-badge b-solo'; badge.textContent = '1-ON-1'; }
    else if (iMode === 'all') { avs.innerHTML = INVESTORS.slice(0,6).map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'Investment Council'; badge.className = 'ctx-badge b-all'; badge.textContent = 'PARALLEL'; }
    else if (iMode === 'round') { avs.innerHTML = INVESTORS.slice(0,6).map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'Round Table'; badge.className = 'ctx-badge b-all'; badge.textContent = 'SEQUENTIAL'; }
    else if (iMode === 'pm') { avs.innerHTML = `<div class="ctx-av" style="background:rgba(82,196,138,.15)">🏦</div>`; name.textContent = 'PM Synthesis'; badge.className = 'ctx-badge b-pm'; badge.textContent = 'PM'; }
    else if (iMode === 'debate' && debMembers.length >= 2) { const dm = debMembers.map(x => ALL_AGENTS[x]); avs.innerHTML = dm.map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = dm.map(m => m.name.split(' ')[0]).join(' vs '); badge.className = 'ctx-badge b-debate'; badge.textContent = 'DEBATE'; }
    else { avs.innerHTML = ''; name.textContent = 'Investment Council'; badge.style.display = 'none'; }
  } else if (team === 'res') {
    if (rMode === 'solo' && selMember) { const m = ALL_AGENTS[selMember]; avs.innerHTML = `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`; name.textContent = m.name; badge.className = 'ctx-badge b-res'; badge.textContent = 'RESEARCH'; }
    else if (rMode === 'all') { avs.innerHTML = RESEARCHERS.map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'Research Team'; badge.className = 'ctx-badge b-res'; badge.textContent = 'PARALLEL'; }
    else if (rMode === 'round') { avs.innerHTML = RESEARCHERS.map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'Research Dialogue'; badge.className = 'ctx-badge b-res'; badge.textContent = 'SEQUENTIAL'; }
    else if (rMode === 'brief') { avs.innerHTML = [...RESEARCHERS,...INVESTORS.slice(0,2)].map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'Research → Investors'; badge.className = 'ctx-badge b-res'; badge.textContent = 'BRIEFING'; }
    else { avs.innerHTML = ''; name.textContent = 'Research Team'; badge.style.display = 'none'; }
  } else {
    if (nMode === 'solo' && selMember) { const m = ALL_AGENTS[selMember]; avs.innerHTML = `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`; name.textContent = m.name; badge.className = 'ctx-badge b-news'; badge.textContent = 'NEWS'; }
    else if (nMode === 'all') { avs.innerHTML = NEWS_DESK.map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'News Desk'; badge.className = 'ctx-badge b-news'; badge.textContent = 'PARALLEL'; }
    else if (nMode === 'round') { avs.innerHTML = NEWS_DESK.map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'News Dialogue'; badge.className = 'ctx-badge b-news'; badge.textContent = 'SEQUENTIAL'; }
    else if (nMode === 'brief') { avs.innerHTML = [...NEWS_DESK,...INVESTORS.slice(0,3)].map(m => `<div class="ctx-av" style="background:${m.bg}">${m.emoji}</div>`).join(''); name.textContent = 'News → All Teams'; badge.className = 'ctx-badge b-news'; badge.textContent = 'BRIEFING'; }
    else { avs.innerHTML = ''; name.textContent = 'News Desk'; badge.style.display = 'none'; }
  }
}

function clearMsgs() {
  const msgs = document.getElementById('messages');
  msgs.innerHTML = '';
  const d = document.createElement('div');
  d.style.cssText = 'padding:28px 24px;font-family:"JetBrains Mono",monospace;font-size:11px;color:var(--t3)';
  let txt = '';
  if (team === 'inv') {
    if (iMode === 'solo' && selMember) { const m = ALL_AGENTS[selMember]; txt = `<div style="font-size:22px;margin-bottom:8px">${m.emoji}</div><div style="font-size:13px;font-family:'Instrument Serif',serif;color:var(--text);margin-bottom:3px">${m.name}</div><div>${m.role}</div><div style="margin-top:6px;color:var(--green)">● Streaming enabled</div>`; }
    else if (iMode === 'all') txt = 'All enabled advisors run concurrently — fastest responder appears first. Toggle ● buttons to mute/unmute.';
    else if (iMode === 'round') txt = 'Advisors respond one by one, each building on what the previous said. Toggle ● buttons to control who speaks.';
    else if (iMode === 'pm') txt = 'Council deliberates in parallel, then the Chair streams a structured verdict.';
    else if (iMode === 'debate') txt = 'Debate ready. Members argue in parallel.';
    else txt = 'Pick a mode above or select a member from the sidebar. Try <em>Full Council</em> for all 6 advisors at once, or <em>Round Table</em> to have them build on each other.';
  } else if (team === 'res') {
    if (rMode === 'solo' && selMember) { const m = ALL_AGENTS[selMember]; txt = `<div style="font-size:22px;margin-bottom:8px">${m.emoji}</div><div style="font-size:13px;font-family:'Instrument Serif',serif;color:var(--text);margin-bottom:3px">${m.name}</div><div>${m.role}</div>`; }
    else if (rMode === 'all') txt = 'All enabled researchers run concurrently. Toggle ● to mute/unmute.';
    else if (rMode === 'round') txt = 'Researchers respond sequentially, each building on prior analysis.';
    else if (rMode === 'brief') txt = 'Researchers analyze in parallel, then brief the investment council.';
    else txt = 'Pick a mode above or select a researcher. Try <em>Full Team</em> for all 5 analysts in parallel, or <em>Brief Investors</em> to have research feed the council.';
  } else {
    if (nMode === 'solo' && selMember) { const m = ALL_AGENTS[selMember]; txt = `<div style="font-size:22px;margin-bottom:8px">${m.emoji}</div><div style="font-size:13px;font-family:'Instrument Serif',serif;color:var(--text);margin-bottom:3px">${m.name}</div><div>${m.role}</div>`; }
    else if (nMode === 'all') txt = 'Full news desk runs concurrently. Toggle ● to mute/unmute.';
    else if (nMode === 'round') txt = 'News reporters respond sequentially, building a layered analysis.';
    else if (nMode === 'brief') txt = 'News desk analyzes, then briefs investors and research team.';
    else txt = 'Paste a news headline or ask about market events. Try <em>Brief All Teams</em> to have news desk, investors, and researchers all react together.';
  }
  d.innerHTML = txt;
  msgs.appendChild(d);
}

// ═══════════════════════════════════════
// SSE STREAMING ENGINE
// ═══════════════════════════════════════

async function streamChat(requestBody) {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestBody),
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({ detail: 'Unknown error' }));
    addErr('Error: ' + (err.detail || response.statusText));
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop();
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      try {
        const event = JSON.parse(line.slice(6));
        handleSSEEvent(event);
      } catch (e) { /* ignore parse errors */ }
    }
  }
}

function scrollIfBottom() {
  const m = document.getElementById('messages');
  if (m && m.scrollHeight - m.scrollTop - m.clientHeight < 120) m.scrollTop = m.scrollHeight;
}

const TICKER_EXCLUDE = new Set([
  'THE','AND','FOR','NOT','BUT','ALL','ARE','CAN','GET','HAS','ITS','MAY','NEW',
  'NOW','OUR','OUT','SEE','SET','TOP','USE','CEO','CFO','EPS','ETF','FCF','GDP',
  'IPO','FED','SEC','YOY','USD','EUR','GBP','BUY','SELL','HOLD','WATCH','AVOID',
  'NEWS','RATE','RISK','FUND','HIGH','FLOW','TECH','DATA','CORP','COST','CASH',
  'DEBT','LOSS','GAIN','LONG','TERM','GOOD','REAL','FULL','NEXT','LAST','BEST',
]);

function autoAddWatchlist(texts) {
  const combined = texts.join('\n');
  const found = new Set();
  (combined.match(/\$([A-Z]{1,5})\b/g) || []).forEach(m => found.add(m.slice(1)));
  (combined.match(/(?:BUY|WATCH|AVOID)[:\s]+([A-Z]{2,5})\b/g) || []).forEach(m => {
    const t = m.match(/([A-Z]{2,5})$/)?.[1]; if (t) found.add(t);
  });
  const tickers = [...found].filter(t => t.length >= 2 && !TICKER_EXCLUDE.has(t));
  if (!tickers.length) return;
  const newOnes = tickers.filter(t => !watchlist.find(w => w.t === t));
  if (!newOnes.length) return;
  newOnes.forEach(t => watchlist.push({ t, added: new Date().toLocaleDateString() }));
  localStorage.setItem('ic_wl', JSON.stringify(watchlist));
  renderWL();
  addSys(`📋 Auto-added to watchlist: ${newOnes.join(', ')}`);
}

function handleSSEEvent(ev) {
  const msgs = document.getElementById('messages');

  switch (ev.type) {

    // ── Solo streaming ──────────────────────────────────────
    case 'typing': {
      if (!activeBlock) {
        const row = addTyping(ev.advisor);
        row.dataset.advisorId = ev.advisor.id;
      } else {
        addDTypingToBlock(activeBlock, ev.advisor);
      }
      break;
    }

    case 'token': {
      const m = ALL_AGENTS[ev.advisor_id];
      if (!m) break;
      if (ev.advisor_id === 'chair' && activeSynthBody) {
        const existing = activeSynthBody.dataset.rawText || '';
        const newText = existing + ev.text;
        activeSynthBody.dataset.rawText = newText;
        activeSynthBody.innerHTML = fmt(newText);
        scrollIfBottom();
      } else if (soloStreamBubble) {
        soloStreamText += ev.text;
        soloStreamBubble.innerHTML = fmt(soloStreamText);
        scrollIfBottom();
      }
      break;
    }

    case 'advisor_complete': {
      document.querySelectorAll('.typing-row').forEach(r => {
        if (r.dataset.advisorId === ev.advisor.id) r.remove();
      });
      soloStreamBubble?.classList.remove('streaming');
      soloStreamBubble = null;
      soloStreamText = '';
      if (!document.querySelector(`.msg-row[data-advisor="${ev.advisor.id}"]`)) {
        addBot(ev.full_text, ev.advisor);
      }
      // Save to history
      chatHist.push({ role: 'assistant', content: ev.full_text, mid: ev.advisor.id });
      msgs.scrollTop = 9e9;
      break;
    }

    // ── Block events ─────────────────────────────────────────
    case 'block_start': {
      activeBlock = mkBlock(ev.title, ev.color, ev.bg);
      msgs.appendChild(activeBlock);
      activeBlockIsResearch = !!(ev.title && (ev.title.includes('RESEARCH') || ev.title.includes('🔬')));
      researchBlockTexts = [];
      msgs.scrollTop = msgs.scrollHeight;
      break;
    }

    case 'typing_in_block': {
      if (activeBlock) {
        const row = addDTypingToBlock(activeBlock, ev.advisor);
        blockTypingRows[ev.advisor.id] = row;
        scrollIfBottom();
      }
      break;
    }

    case 'block_entry': {
      if (blockTypingRows[ev.advisor.id]) {
        blockTypingRows[ev.advisor.id].remove();
        delete blockTypingRows[ev.advisor.id];
      }
      if (activeBlock) addDEntry(activeBlock, ev.advisor, ev.text);
      if (activeBlockIsResearch) researchBlockTexts.push(ev.text);
      // Save to history
      chatHist.push({ role: 'assistant', content: ev.text, mid: ev.advisor.id });
      scrollIfBottom();
      break;
    }

    case 'block_error': {
      if (blockTypingRows[ev.advisor.id]) {
        blockTypingRows[ev.advisor.id].remove();
        delete blockTypingRows[ev.advisor.id];
      }
      if (activeBlock) {
        const d = document.createElement('div');
        d.className = 'block-entry';
        d.innerHTML = `<div class="be-av" style="background:${ev.advisor.bg}">${ev.advisor.emoji}</div><div style="color:var(--red);font-family:'JetBrains Mono',monospace;font-size:11px">${ev.advisor.name}: ${ev.message}</div>`;
        activeBlock.appendChild(d);
      }
      break;
    }

    case 'block_end': {
      if (activeBlockIsResearch && researchBlockTexts.length) autoAddWatchlist(researchBlockTexts);
      activeBlock = null;
      activeBlockIsResearch = false;
      researchBlockTexts = [];
      Object.keys(blockTypingRows).forEach(k => {
        blockTypingRows[k]?.remove();
        delete blockTypingRows[k];
      });
      break;
    }

    // ── Sequential (Round Table) events ───────────────────────
    case 'seq_token_start': {
      // Remove the typing placeholder for this agent
      if (blockTypingRows[ev.advisor.id]) {
        blockTypingRows[ev.advisor.id].remove();
        delete blockTypingRows[ev.advisor.id];
      }
      if (activeBlock) {
        const d = document.createElement('div');
        d.className = 'block-entry';
        d.innerHTML = `<div class="be-av" style="background:${ev.advisor.bg}">${ev.advisor.emoji}</div>
          <div style="flex:1">
            <div class="be-name" style="color:${ev.advisor.color}">${ev.advisor.name} <span style="color:var(--t3);font-weight:400">${ev.advisor.tag}</span></div>
            <div class="be-body streaming" id="seq-body-${ev.advisor.id}"></div>
          </div>`;
        activeBlock.appendChild(d);
        seqStreamEntry = document.getElementById('seq-body-' + ev.advisor.id);
        seqStreamText = '';
      }
      scrollIfBottom();
      break;
    }

    case 'seq_token': {
      if (seqStreamEntry) {
        seqStreamText += ev.text;
        seqStreamEntry.innerHTML = fmt(seqStreamText);
        scrollIfBottom();
      }
      break;
    }

    case 'seq_entry_done': {
      if (seqStreamEntry) {
        seqStreamEntry.classList.remove('streaming');
        seqStreamEntry.innerHTML = fmt(ev.text);
        seqStreamEntry = null;
        seqStreamText = '';
      }
      // Save to history
      chatHist.push({ role: 'assistant', content: ev.text, mid: ev.advisor.id });
      scrollIfBottom();
      break;
    }

    // ── Synthesis ─────────────────────────────────────────────
    case 'synth_start': {
      const sb = document.createElement('div');
      sb.className = 'synth-block';
      sb.innerHTML = `<div class="synth-hdr">🏦 CHAIR'S VERDICT</div><div class="synth-body streaming" data-raw-text=""></div>`;
      msgs.appendChild(sb);
      activeSynthBody = sb.querySelector('.synth-body');
      msgs.scrollTop = 9e9;
      break;
    }

    case 'synth_complete': {
      if (activeSynthBody) {
        activeSynthBody.classList.remove('streaming');
        activeSynthBody.innerHTML = fmt(ev.full_text);
        activeSynthBody = null;
      }
      document.querySelectorAll('.typing-row').forEach(r => {
        if (r.dataset.advisorId === 'chair') r.remove();
      });
      // Save to history
      chatHist.push({ role: 'assistant', content: ev.full_text, mid: ev.advisor.id });
      scrollIfBottom();
      break;
    }

    // ── Error / Done ─────────────────────────────────────────
    case 'error': {
      addErr('Error: ' + ev.message);
      break;
    }

    case 'done': {
      document.querySelectorAll('.typing-row').forEach(r => r.remove());
      Object.keys(blockTypingRows).forEach(k => delete blockTypingRows[k]);
      activeBlock = null;
      activeSynthBody = null;
      seqStreamEntry = null;
      seqStreamText = '';
      break;
    }
  }

  // Create the solo stream bubble on first token
  if (ev.type === 'token' && ev.advisor_id !== 'chair' && !soloStreamBubble && !activeBlock) {
    const m = ALL_AGENTS[ev.advisor_id];
    if (m) {
      document.querySelectorAll('.typing-row').forEach(r => {
        if (r.dataset.advisorId === ev.advisor_id) r.remove();
      });
      soloStreamText = ev.text;
      const row = document.createElement('div');
      row.className = 'msg-row';
      row.dataset.advisor = ev.advisor_id;
      const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      row.innerHTML = `<div class="m-av" style="background:${m.bg}">${m.emoji}</div>
        <div class="m-body">
          <div class="m-head">
            <span class="m-name" style="color:${m.color}">${m.name}</span>
            <span class="m-tag" style="color:${m.color};border-color:${m.color}33;background:${m.bg}">${m.tag}</span>
            <span class="m-type">${m.qtype}</span>
            <span class="m-time">${now}</span>
          </div>
          <div class="bubble streaming" id="stream-${m.id}"></div>
        </div>`;
      msgs.appendChild(row);
      soloStreamBubble = document.getElementById('stream-' + m.id);
      soloStreamBubble.innerHTML = fmt(soloStreamText);
      msgs.scrollTop = 9e9;
    }
  }
}

// ═══════════════════════════════════════
// SEND
// ═══════════════════════════════════════
function trimChatHist(msgs) {
  // Keep only last MAX_HIST user/assistant pairs for API, strip extra fields
  return msgs.slice(-(MAX_HIST * 2));
}

async function send() {
  if (loading) return;
  const inp = document.getElementById('ui'), txt = inp.value.trim();
  if (!txt) return;
  if (parseCmd(txt)) { inp.value = ''; ar(inp); return; }
  inp.value = ''; ar(inp);
  loading = true;
  document.getElementById('sb').disabled = true;
  document.getElementById('welcome')?.remove();

  addUser(txt);
  chatHist.push({ role: 'user', content: txt });

  // Reset streaming state
  soloStreamBubble = null;
  soloStreamText = '';
  activeBlock = null;
  activeSynthBody = null;
  seqStreamEntry = null;
  seqStreamText = '';

  try {
    const payload = {
      team,
      mode: team === 'inv' ? iMode : team === 'res' ? rMode : nMode,
      advisor_id: selMember,
      debate_members: debMembers,
      messages: trimChatHist(chatHist),
      enabled_agents: getEnabledAgentIds(),
    };
    await streamChat(payload);
    saveSess();
    renderHist();
  } catch (e) {
    addErr('Error: ' + (e.message || 'Check server logs.'));
  }

  loading = false;
  document.getElementById('sb').disabled = false;
}

// ═══════════════════════════════════════
// DOM HELPERS
// ═══════════════════════════════════════
function addUser(txt) {
  const msgs = document.getElementById('messages');
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const d = document.createElement('div');
  d.className = 'msg-row user';
  d.innerHTML = `<div class="m-av" style="background:var(--s3)">👤</div>
    <div class="m-body">
      <div class="m-head"><span class="m-name">You</span><span class="m-time">${now}</span></div>
      <div class="bubble">${esc(txt).replace(/\n/g,'<br>')}</div>
    </div>`;
  msgs.appendChild(d);
  msgs.scrollTop = 9e9;
}

function addBot(txt, m) {
  const msgs = document.getElementById('messages');
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const d = document.createElement('div');
  d.className = 'msg-row';
  d.dataset.advisor = m.id;
  d.innerHTML = `<div class="m-av" style="background:${m.bg}">${m.emoji}</div>
    <div class="m-body">
      <div class="m-head">
        <span class="m-name" style="color:${m.color}">${m.name}</span>
        <span class="m-tag" style="color:${m.color};border-color:${m.color}33;background:${m.bg}">${m.tag}</span>
        <span class="m-type">${m.qtype}</span>
        <span class="m-time">${now}</span>
      </div>
      <div class="bubble">${fmt(txt)}</div>
    </div>`;
  msgs.appendChild(d);
  msgs.scrollTop = 9e9;
}

function addTyping(m) {
  const msgs = document.getElementById('messages');
  const d = document.createElement('div');
  d.className = 'typing-row';
  d.dataset.advisorId = m.id;
  d.innerHTML = `<div class="m-av" style="background:${m.bg};width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0">${m.emoji}</div>
    <div><div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t3);margin-bottom:5px">${m.name}</div>
    <div class="t-bubble"><div class="td"></div><div class="td"></div><div class="td"></div></div></div>`;
  msgs.appendChild(d);
  msgs.scrollTop = 9e9;
  return d;
}

function mkBlock(title, color, bg) {
  const d = document.createElement('div');
  d.className = 'block';
  d.innerHTML = `<div class="block-hdr" style="color:${color};background:${bg}">${title}</div>`;
  return d;
}

function addDTypingToBlock(bl, m) {
  const d = document.createElement('div');
  d.className = 'block-entry';
  d.innerHTML = `<div class="be-av" style="background:${m.bg}">${m.emoji}</div>
    <div><div class="be-name" style="color:${m.color}">${m.name}</div>
    <div class="be-typing"><div class="td"></div><div class="td"></div><div class="td"></div></div></div>`;
  bl.appendChild(d);
  return d;
}

function addDEntry(bl, m, txt) {
  const d = document.createElement('div');
  d.className = 'block-entry';
  d.innerHTML = `<div class="be-av" style="background:${m.bg}">${m.emoji}</div>
    <div style="flex:1">
      <div class="be-name" style="color:${m.color}">${m.name} <span style="color:var(--t3);font-weight:400">${m.tag}</span></div>
      <div class="be-body">${fmt(txt)}</div>
    </div>`;
  bl.appendChild(d);
}

function addErr(t) {
  const msgs = document.getElementById('messages');
  const d = document.createElement('div');
  d.className = 'err-row';
  d.textContent = t;
  msgs.appendChild(d);
  msgs.scrollTop = 9e9;
}

function addSys(t) {
  document.getElementById('welcome')?.remove();
  const msgs = document.getElementById('messages');
  const d = document.createElement('div');
  d.className = 'sys-row';
  d.innerHTML = t;
  msgs.appendChild(d);
  msgs.scrollTop = 9e9;
}

// ═══════════════════════════════════════
// FORMAT
// ═══════════════════════════════════════
function fmt(t) {
  let h = esc(t)
    .replace(/^([A-Z][A-Z\s\&\/\-]{3,}):?\s*$/gm, '<h4>$1</h4>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    .replace(/(\+\$[\d,\.]+|\+[\d\.]+%)/g, '<span class="pos">$1</span>')
    .replace(/(-\$[\d,\.]+|-[\d\.]+%)/g, '<span class="neg">$1</span>')
    .replace(/(\$[\d,]+(?:\.\d+)?(?:[BMT])?(?!\d))/g, '<span class="num">$1</span>');
  return h.split(/\n\n+/).map(p => {
    p = p.replace(/\n/g, '<br>');
    return p.startsWith('<h4>') ? p : `<p>${p}</p>`;
  }).join('');
}

function esc(t) {
  return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function fmtDate(d) {
  if (!d) return '';
  try {
    const dt = new Date(d);
    if (isNaN(dt)) return d;
    const today = new Date();
    const yest = new Date(today); yest.setDate(today.getDate() - 1);
    if (dt.toDateString() === today.toDateString()) return 'Today';
    if (dt.toDateString() === yest.toDateString()) return 'Yesterday';
    return dt.toLocaleDateString([], { month: 'short', day: 'numeric' });
  } catch(e) { return d; }
}

// ═══════════════════════════════════════
// COMMANDS
// ═══════════════════════════════════════
function parseCmd(t) {
  const addM = t.match(/^\/add\s+([A-Za-z\-\.]+)\s+([\d\.]+)/i);
  if (addM) { addPos(addM[1].toUpperCase(), parseFloat(addM[2])); addSys(`✓ Added ${addM[1].toUpperCase()}`); return true; }
  const sellM = t.match(/^\/sell\s+([A-Za-z]+)/i);
  if (sellM) { const tk = sellM[1].toUpperCase(); if (portfolio[tk]) { delPos(tk); addSys(`✓ Closed ${tk}`); } else addSys(`No position in ${tk}`); return true; }
  const wM = t.match(/^\/watch\s+([A-Za-z\-\.]+)/i);
  if (wM) { addWatch(wM[1].toUpperCase()); addSys(`✓ Added ${wM[1].toUpperCase()} to watchlist`); return true; }
  if (/^\/pm\s/i.test(t)) { setMode('pm'); const q = t.replace(/^\/pm\s*/i, ''); if (q) { document.getElementById('ui').value = q; setTimeout(send, 100); } return true; }
  if (/^\/round$/i.test(t)) { setMode('round'); return true; }
  if (/^\/council$/i.test(t)) { setMode('all'); return true; }
  if (/^\/news\s/i.test(t)) { const q = t.replace(/^\/news\s+/i, ''); switchTeam('news'); setNMode('all'); setTimeout(() => { document.getElementById('ui').value = q; }, 50); return false; }
  if (/^\/brief\s/i.test(t)) { const q = t.replace(/^\/brief\s*/i, ''); switchTeam('news'); setNMode('brief'); setTimeout(() => { document.getElementById('ui').value = q; }, 50); return false; }
  const shortcuts = { '/banker':'banker','/macro':'macro','/geo':'geo','/quant':'quant','/growth':'growth','/risk':'risk','/nina':'fund','/rex':'tech','/zoe':'screen','/dev':'theme','/cass':'contra','/elena':'mkts','/james':'policy','/priya':'corp' };
  for (const [cmd, id] of Object.entries(shortcuts)) {
    if (t.toLowerCase().startsWith(cmd)) {
      const inInv = INVESTORS.find(m => m.id === id), inRes = RESEARCHERS.find(m => m.id === id);
      switchTeam(inInv ? 'inv' : inRes ? 'res' : 'news');
      selMem(id);
      const q = t.slice(cmd.length).trim();
      if (q) { document.getElementById('ui').value = q; setTimeout(send, 100); }
      return true;
    }
  }
  return false;
}

// ═══════════════════════════════════════
// SESSION HISTORY
// ═══════════════════════════════════════
function newChat() {
  if (chatHist.length) saveSess();
  chatHist = []; sesId = null; showChat(); switchTeam(team); clearMsgs();
}

async function saveSess() {
  if (!chatHist.length) return;
  const preview = chatHist.find(m => m.role === 'user')?.content?.slice(0, 50) || 'Chat';
  if (!sesId) sesId = Date.now() + '';
  try {
    await fetch('/api/sessions', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: sesId, preview, team, messages: chatHist }),
    });
    const existing = sessions.find(s => s.id === sesId);
    if (existing) {
      existing.preview = preview;
      existing.messages = chatHist;
    } else {
      sessions.unshift({ id: sesId, preview, team, date: new Date().toLocaleDateString(), messages: chatHist });
    }
  } catch(e) {}
}

function renderHist() {
  const el = document.getElementById('histList');
  if (!el) return;
  if (!sessions.length) { el.innerHTML = '<div style="padding:7px 11px;font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--t3)">No history yet</div>'; return; }
  el.innerHTML = sessions.slice(0, 30).map(s => `
    <div class="hi ${s.id === sesId ? 'active' : ''}" onclick="loadSess('${s.id}')">
      <div class="hi-prev">${s.preview}</div>
      <div class="hi-meta"><span>${fmtDate(s.date)}</span><button class="hi-del" onclick="event.stopPropagation();delSess('${s.id}')">✕</button></div>
    </div>`).join('');
}

async function loadSess(id) {
  const s = sessions.find(x => x.id === id);
  if (!s) return;
  if (chatHist.length) saveSess();
  sesId = id;
  chatHist = [...(s.messages || [])];
  team = s.team || 'inv';
  switchTeam(team);
  const msgs = document.getElementById('messages');
  msgs.innerHTML = '';
  for (const m of chatHist) {
    if (m.role === 'user') {
      addUser(m.content);
    } else if (m.role === 'assistant' && m.mid) {
      const mem = ALL_AGENTS[m.mid];
      if (mem) addBot(m.content, mem);
    }
  }
  renderHist();
}

async function delSess(id) {
  sessions = sessions.filter(s => s.id !== id);
  if (sesId === id) { chatHist = []; sesId = null; clearMsgs(); }
  try { await fetch('/api/sessions/' + id, { method: 'DELETE' }); } catch(e) {}
  renderHist();
}

// ═══════════════════════════════════════
// PORTFOLIO
// ═══════════════════════════════════════
function renderPfBar() {
  const bar = document.getElementById('pfBar'), tks = Object.keys(portfolio);
  if (!tks.length && !cash) { bar.classList.remove('on'); return; }
  bar.classList.add('on');
  let h = '<span style="font-family:\'JetBrains Mono\',monospace;font-size:9px;color:var(--t3);flex-shrink:0">PF:</span>';
  tks.forEach(t => {
    const p = portfolio[t]; let pnl = '';
    if (p.entry && p.price) { const v = (p.size/p.entry)*p.price-p.size, pct = (v/p.size*100).toFixed(1); pnl = `<span style="color:${v>=0?'var(--green)':'var(--red)'};">${v>=0?'+':''}${pct}%</span>`; }
    h += `<div class="pfchip"><span class="sym">${t}</span><span style="color:var(--t2)">$${p.size.toLocaleString()}</span>${pnl}<span class="xbtn" onclick="delPos('${t}')">✕</span></div>`;
  });
  if (cash > 0) h += `<div class="pfchip cash-chip"><span class="sym">CASH</span><span style="color:var(--green)">$${cash.toLocaleString(undefined,{minimumFractionDigits:2})}</span></div>`;
  bar.innerHTML = h;
}

async function addPos(tk, size, entry, name, price) {
  // price should only be set if it's the current market price, not defaulted to entry
  portfolio[tk] = { size, entry: entry||null, price: price||null, name: name||tk };
  try {
    await fetch('/api/portfolio/position', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker: tk, size, entry, price, name }),
    });
  } catch(e) {}
  renderPfBar();
}

async function delPos(tk) {
  delete portfolio[tk];
  try { await fetch('/api/portfolio/position/' + tk, { method: 'DELETE' }); } catch(e) {}
  renderPfBar();
  if (document.getElementById('pfView').classList.contains('on')) renderPfView();
}

function editCash() {
  const e = document.getElementById('cashEdit');
  document.getElementById('ci').value = cash || '';
  e.style.display = 'flex';
  document.getElementById('ci').focus();
}

async function saveCash() {
  cash = parseFloat(document.getElementById('ci').value) || 0;
  try {
    await fetch('/api/portfolio/cash', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: cash }),
    });
  } catch(e) {}
  document.getElementById('sc').textContent = '$' + cash.toLocaleString(undefined,{minimumFractionDigits:2});
  document.getElementById('cashEdit').style.display = 'none';
  renderPfBar();
}

async function silentRefreshPrices() {
  const now = Date.now();
  if (now - lastPriceRefresh < 60000) return; // throttle: max once per minute
  const tks = Object.keys(portfolio);
  if (!tks.length) return;
  lastPriceRefresh = now;
  for (const t of tks) {
    try {
      const q = await fetch('/api/quotes/' + t).then(r => r.json());
      if (q?.price) {
        portfolio[t].price = q.price;
        await fetch(`/api/portfolio/position/${t}/price?price=${q.price}`, { method: 'PATCH' });
      }
    } catch(e) {}
  }
  renderPfBar(); renderPfView();
}

async function refreshPrices() {
  const tks = Object.keys(portfolio);
  if (!tks.length) return;
  const btn = document.getElementById('refreshBtn');
  if (btn) { btn.textContent = '↺ …'; btn.disabled = true; }
  addSys('Refreshing prices…');
  for (const t of tks) {
    try {
      const q = await fetch('/api/quotes/' + t).then(r => r.json());
      if (q?.price) {
        portfolio[t].price = q.price;
        await fetch(`/api/portfolio/position/${t}/price?price=${q.price}`, { method: 'PATCH' });
      }
    } catch(e) {}
  }
  lastPriceRefresh = Date.now();
  renderPfBar(); renderPfView();
  addSys('✓ Prices updated');
  if (btn) { btn.textContent = '↺ Refresh'; btn.disabled = false; }
}

function briefAll() {
  const tks = Object.keys(portfolio);
  if (!tks.length) { alert('Add positions first.'); return; }
  const lines = tks.map(t => { const p = portfolio[t]; return `${t}: $${p.size.toLocaleString()}${p.entry ? ` @ $${p.entry}` : ''}`; }).join(', ');
  const cashStr = cash > 0 ? ` Cash: $${cash.toLocaleString()}.` : '';
  switchTeam('inv'); setMode('pm');
  setTimeout(() => { document.getElementById('ui').value = `Full portfolio review: ${lines}.${cashStr} Biggest risks and what would you change?`; send(); }, 200);
}

function renderPfView() {
  const sc = document.getElementById('sc');
  if (sc) sc.textContent = '$' + cash.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
  const tks = Object.keys(portfolio);
  const total = tks.reduce((a,t) => a + portfolio[t].size, 0);
  document.getElementById('sv').textContent = '$' + total.toLocaleString(undefined,{minimumFractionDigits:2});
  document.getElementById('sp').textContent = tks.length;
  if (!tks.length) {
    document.getElementById('spnl').textContent = '—';
    document.getElementById('pfTbl').innerHTML = '<div style="padding:16px;font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--t3);text-align:center">No positions. Use screener above.</div>';
    return;
  }
  let totalPnl = 0, hasPnl = false;
  const rows = tks.map(t => {
    const p = portfolio[t], pct = total > 0 ? ((p.size/total)*100).toFixed(1) : '—';
    let pnlH = '<span style="color:var(--t3)">—</span>';
    if (p.entry && p.price) { const pnl = (p.size/p.entry)*p.price-p.size, pp = (pnl/p.size*100).toFixed(2); totalPnl += pnl; hasPnl = true; pnlH = `<span class="${pnl>=0?'up':'dn'}">${pnl>=0?'+':''}$${Math.abs(pnl).toFixed(0)} (${pnl>=0?'+':''}${pp}%)</span>`; }
    const prH = p.price ? `<span style="color:var(--t2)">$${p.price.toFixed(2)}</span>` : '<span style="color:var(--t3)">—</span>';
    return `<tr><td class="tk">${t}${p.name&&p.name!==t?`<div style="font-size:9px;color:var(--t3)">${p.name}</div>`:''}</td><td>$${p.size.toLocaleString(undefined,{minimumFractionDigits:2})}</td><td>${p.entry?'$'+p.entry:'—'}</td><td>${prH}</td><td>${pnlH}</td><td>${pct}%</td><td><span style="cursor:pointer;color:var(--t3)" onclick="delPos('${t}')">✕</span></td></tr>`;
  }).join('');
  if (hasPnl) { const el = document.getElementById('spnl'); el.textContent = `${totalPnl>=0?'+':''}$${Math.abs(totalPnl).toFixed(0)}`; el.className = 'sum-val '+(totalPnl>=0?'up':'dn'); }
  document.getElementById('pfTbl').innerHTML = `<table class="pf-tbl"><thead><tr><th>Ticker</th><th>Cost Basis</th><th>Entry</th><th>Price</th><th>P&L</th><th>% Book</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

function exportCSV() {
  const tks = Object.keys(portfolio);
  if (!tks.length) { alert('No positions to export.'); return; }
  const rows = [['Ticker','Cost Basis','Entry','Current Price','P&L','% Book']];
  const total = tks.reduce((a,t) => a + portfolio[t].size, 0);
  tks.forEach(t => {
    const p = portfolio[t];
    const pct = total > 0 ? ((p.size/total)*100).toFixed(1) : '';
    const pnl = p.entry && p.price ? ((p.size/p.entry)*p.price - p.size).toFixed(2) : '';
    rows.push([t, p.size, p.entry||'', p.price||'', pnl, pct]);
  });
  const csv = rows.map(r => r.join(',')).join('\n');
  const a = document.createElement('a');
  a.href = 'data:text/csv;charset=utf-8,' + encodeURIComponent(csv);
  a.download = 'portfolio.csv';
  a.click();
}

// ═══════════════════════════════════════
// SCREENER (via backend)
// ═══════════════════════════════════════
async function doSearch() {
  const q = document.getElementById('screenerInput').value.trim().toUpperCase();
  if (!q) return;
  const res = document.getElementById('sResult');
  res.innerHTML = `<div style="padding:9px 0;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--t3)">Fetching ${q}…</div>`;
  try {
    const qt = await fetch('/api/quotes/' + q).then(r => r.json());
    lastQuote = qt;
    const up = (qt.change_pct || 0) >= 0, cs = up ? 'var(--green)' : 'var(--red)', sg = up ? '+' : '';
    const fB = v => { if (v == null) return '—'; if (v >= 1e12) return '$'+(v/1e12).toFixed(2)+'T'; if (v >= 1e9) return '$'+(v/1e9).toFixed(2)+'B'; if (v >= 1e6) return '$'+(v/1e6).toFixed(2)+'M'; return '$'+v.toLocaleString(); };
    res.innerHTML = `<div class="quote-card">
      <div class="qc-h">
        <div><div class="q-sym">${qt.symbol}</div><div class="q-name">${qt.name||''}</div><div class="q-exch">${qt.exchange||''}</div></div>
        <div><div class="q-price" style="color:var(--text)">$${qt.price!=null?qt.price.toFixed(2):'—'}</div><div class="q-chg" style="color:${cs}">${sg}${(qt.change||0).toFixed(2)} (${sg}${(qt.change_pct||0).toFixed(2)}%)</div></div>
      </div>
      <div class="qstats">
        <div class="qstat"><div class="ql">Mkt Cap</div><div class="qv">${fB(qt.market_cap)}</div></div>
        <div class="qstat"><div class="ql">P/E</div><div class="qv">${qt.pe!=null?qt.pe.toFixed(1):'—'}</div></div>
        <div class="qstat"><div class="ql">52W High</div><div class="qv">${qt.high_52?'$'+qt.high_52.toFixed(2):'—'}</div></div>
        <div class="qstat"><div class="ql">52W Low</div><div class="qv">${qt.low_52?'$'+qt.low_52.toFixed(2):'—'}</div></div>
        <div class="qstat"><div class="ql">Volume</div><div class="qv">${qt.volume?(qt.volume/1e6).toFixed(2)+'M':'—'}</div></div>
        <div class="qstat"><div class="ql">Day High</div><div class="qv">${qt.day_high?'$'+qt.day_high.toFixed(2):'—'}</div></div>
        <div class="qstat"><div class="ql">Day Low</div><div class="qv">${qt.day_low?'$'+qt.day_low.toFixed(2):'—'}</div></div>
        <div class="qstat"><div class="ql">Prev Close</div><div class="qv">${qt.prev_close?'$'+qt.prev_close.toFixed(2):'—'}</div></div>
      </div>
      <div class="qadd">
        <span class="qadd-lbl">Add to portfolio:</span>
        <input type="number" class="qi" id="addAmt" placeholder="$ amount" min="1">
        <input type="number" class="qi" id="addEntry" placeholder="entry px" step="0.01" value="${qt.price!=null?qt.price.toFixed(2):''}">
        <button class="add-btn" onclick="addFromQ()">+ Add</button>
        <button class="w-btn" onclick="addWatch('${qt.symbol}')">👁 Watch</button>
        <span id="qfb" style="font-family:'JetBrains Mono',monospace;font-size:10px"></span>
      </div>
    </div>`;
  } catch(e) {
    res.innerHTML = `<div style="margin-top:9px;background:rgba(224,82,82,.07);border:1px solid rgba(224,82,82,.2);border-radius:7px;padding:9px;font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--red)">Could not fetch ${q}. Check ticker.</div>`;
  }
}

function addFromQ() {
  if (!lastQuote) return;
  const amt = parseFloat(document.getElementById('addAmt').value), entry = parseFloat(document.getElementById('addEntry').value), fb = document.getElementById('qfb');
  if (!amt || amt <= 0) { fb.style.color = 'var(--red)'; fb.textContent = 'Enter amount'; return; }
  addPos(lastQuote.symbol, amt, entry||lastQuote.price, lastQuote.name, lastQuote.price);
  renderPfView(); fb.style.color = 'var(--green)'; fb.textContent = '✓ Added'; setTimeout(() => fb.textContent = '', 3000);
}

// ═══════════════════════════════════════
// WATCHLIST
// ═══════════════════════════════════════
async function addWatch(t) {
  const tk = (t || document.getElementById('wi')?.value?.trim())?.toUpperCase();
  if (!tk) return;
  if (!watchlist.find(w => w.t === tk)) watchlist.push({ t: tk, added: new Date().toLocaleDateString() });
  localStorage.setItem('ic_wl', JSON.stringify(watchlist));
  if (document.getElementById('wi')) document.getElementById('wi').value = '';
  renderWL();
  try {
    const q = await fetch('/api/quotes/' + tk).then(r => r.json());
    if (q?.price) { const w = watchlist.find(x => x.t === tk); if (w) { w.price = q.price; w.chg = q.change_pct; } localStorage.setItem('ic_wl', JSON.stringify(watchlist)); renderWL(); }
  } catch(e) {}
}

function rmWatch(t) { watchlist = watchlist.filter(w => w.t !== t); localStorage.setItem('ic_wl', JSON.stringify(watchlist)); renderWL(); }

function renderWL() {
  const el = document.getElementById('wItems'); if (!el) return;
  if (!watchlist.length) { el.innerHTML = '<div style="font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--t3);padding:8px 0">Empty. Add a ticker above.</div>'; return; }
  el.innerHTML = watchlist.map(w => `<div class="w-item"><span class="w-tk">${w.t}</span><span class="w-px">${w.price?'$'+w.price.toFixed(2):'—'}</span><span class="w-chg ${(w.chg||0)>=0?'up':'dn'}">${w.chg!=null?(w.chg>=0?'+':'')+w.chg.toFixed(2)+'%':''}</span><button class="wadd" onclick="moveWatch('${w.t}')">+ Add</button><button class="wrm" onclick="rmWatch('${w.t}')">✕</button></div>`).join('');
}

function moveWatch(t) {
  const w = watchlist.find(x => x.t === t);
  const s = prompt(`$ amount for ${t}?`);
  if (!s) return;
  addPos(t, parseFloat(s), w?.price, t, w?.price);
  rmWatch(t); renderPfView();
}

// ═══════════════════════════════════════
// JOURNAL
// ═══════════════════════════════════════
async function logTrade() {
  const tk = document.getElementById('jt').value.trim().toUpperCase();
  const type = document.getElementById('jtype').value;
  const price = parseFloat(document.getElementById('jp').value) || null;
  const size = parseFloat(document.getElementById('jsize').value) || null;
  const rawDate = document.getElementById('jdate').value;
  const date = rawDate ? new Date(rawDate + 'T12:00:00').toLocaleDateString() : new Date().toLocaleDateString();
  const thesis = document.getElementById('jthesis').value.trim();

  const err = document.getElementById('jErr');
  if (!tk) {
    if (err) { err.textContent = '✗ Ticker is required.'; err.style.display = 'block'; }
    document.getElementById('jt').focus();
    return;
  }
  if (err) err.style.display = 'none';

  try {
    const entry = await fetch('/api/journal', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker: tk, type, price, size, date, thesis }),
    }).then(r => r.json());
    journal.unshift(entry);
  } catch(e) {}
  renderJournal();
  ['jt','jp','jsize','jthesis'].forEach(id => { const e = document.getElementById(id); if (e) e.value = ''; });
}

async function loadJournal() {
  try { journal = await fetch('/api/journal').then(r => r.json()); } catch(e) {}
}

function renderJournal() {
  const el = document.getElementById('jEntries'); if (!el) return;
  if (!journal.length) { el.innerHTML = '<div style="padding:16px;font-family:\'JetBrains Mono\',monospace;font-size:10px;color:var(--t3);text-align:center">No trades yet. Log one above.</div>'; return; }
  el.innerHTML = journal.map(e => `<div class="j-entry">
    <div class="je-top"><span class="je-sym">${e.ticker||e.tk}</span><span class="je-badge j${e.type}">${e.type.toUpperCase()}</span>${e.price?`<span class="je-px">@ $${e.price}</span>`:''}${e.size?`<span class="je-px">· $${e.size.toLocaleString()}</span>`:''}<span class="je-dt">${e.date||''}</span></div>
    ${e.thesis?`<div class="je-thesis">${e.thesis}</div>`:''}
    <div class="je-acts"><button class="j-rev" onclick="reviewTrade('${e.id}')">⚔️ Council Review</button><button class="j-del" onclick="delTrade('${e.id}')">✕ Delete</button></div>
  </div>`).join('');
}

async function delTrade(id) {
  if (!confirm('Delete this journal entry? This cannot be undone.')) return;
  journal = journal.filter(e => e.id !== id);
  try { await fetch('/api/journal/' + id, { method: 'DELETE' }); } catch(e) {}
  renderJournal();
}

function reviewTrade(id) {
  const e = journal.find(x => x.id === id); if (!e) return;
  const q = `Review this trade: I ${e.type==='buy'?'bought':e.type==='sell'?'sold':'am watching'} ${e.ticker||e.tk}${e.price?` @ $${e.price}`:''}${e.size?` ($${e.size.toLocaleString()})`:''}. Thesis: "${e.thesis||'none'}". Was this a good decision? What did I get right or wrong?`;
  switchTeam('inv'); setMode('pm');
  setTimeout(() => { document.getElementById('ui').value = q; send(); }, 200);
}

// ═══════════════════════════════════════
// SETUP
// ═══════════════════════════════════════
function renderSetup() {
  const bi = document.getElementById('briefIn'); if (bi && briefing) bi.value = briefing;
  document.getElementById('briefInd').className = briefing?.trim() ? 'ind on' : 'ind off';
  document.getElementById('briefInd').textContent = briefing?.trim() ? 'ACTIVE' : 'NOT SET';
}

async function saveBrief() {
  const v = document.getElementById('briefIn').value.trim(), st = document.getElementById('briefSt');
  if (!v) { st.className = 'sc-st er'; st.textContent = '✗ Write something first.'; return; }
  briefing = v;
  try {
    await fetch('/api/portfolio/briefing', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: v }),
    });
    st.className = 'sc-st ok'; st.textContent = '✓ Saved.';
  } catch(e) { st.className = 'sc-st er'; st.textContent = '✗ Save failed.'; }
  document.getElementById('briefInd').className = 'ind on';
  document.getElementById('briefInd').textContent = 'ACTIVE';
}

const TPLS = {
  aggressive: `I'm an aggressive growth investor, 17 years old. Long-term (5+ years), comfortable with high volatility. Focus on tech, AI, and disruptive sectors. Challenge my theses aggressively.`,
  balanced: `I'm a balanced investor, 3-5 year horizon. Mix of growth and stability. Help me think through macro risks and sector rotation.`,
  macro: `I'm macro-focused. I position around rate cycles, geopolitical shifts, commodity supercycles. I hold liquid ETFs. Stress-test my macro views.`,
  student: `I'm learning markets. Explain your reasoning so I build real investment intuition — don't just give verdicts, show the thought process.`,
};
function setTpl(k) { const ta = document.getElementById('briefIn'); if (ta) { ta.value = TPLS[k]; ta.focus(); } }

// ═══════════════════════════════════════
// DEBATE MODAL
// ═══════════════════════════════════════
function openDebate(t) {
  debTeam = t || team; debMembers = [];
  const pool = (debTeam === 'res' ? RESEARCHERS : debTeam === 'news' ? NEWS_DESK : INVESTORS).filter(m => m.id !== 'chair');
  document.getElementById('dPicks').innerHTML = pool.map(m => `<div class="dpick" id="dp-${m.id}" onclick="togglePick('${m.id}')">
    <div class="dchk" id="dpc-${m.id}"></div>
    <div style="font-size:14px">${m.emoji}</div>
    <div><div style="font-size:11px;font-weight:500">${m.name}</div><div style="font-family:'JetBrains Mono',monospace;font-size:9px;color:var(--t3)">${m.role}</div></div>
  </div>`).join('');
  document.getElementById('dModal').classList.add('open');
}

function closeDebate() { document.getElementById('dModal').classList.remove('open'); }

function togglePick(id) {
  const i = debMembers.indexOf(id);
  if (i === -1) { if (debMembers.length >= 6) return; debMembers.push(id); document.getElementById('dp-'+id)?.classList.add('sel'); if (document.getElementById('dpc-'+id)) document.getElementById('dpc-'+id).textContent = '✓'; }
  else { debMembers.splice(i, 1); document.getElementById('dp-'+id)?.classList.remove('sel'); if (document.getElementById('dpc-'+id)) document.getElementById('dpc-'+id).textContent = ''; }
}

function startDebate() {
  const err = document.getElementById('dErr');
  if (debMembers.length < 2) {
    if (err) { err.textContent = 'Select at least 2 members to start a debate.'; err.style.display = 'block'; }
    return;
  }
  if (err) err.style.display = 'none';
  closeDebate();
  if (debTeam === 'inv') { iMode = 'debate'; ['solo','all','pm','debate','round'].forEach(x => document.getElementById('mi-'+x)?.classList.remove('active')); document.getElementById('mi-debate')?.classList.add('active'); }
  chatHist = []; updateCtx(); clearMsgs();
}

// ═══════════════════════════════════════
// UTILS
// ═══════════════════════════════════════
function qsend(el) {
  showChat();
  const t = el.textContent;
  if (t.includes('AXP') || t.includes('portfolio') || t.includes('cash')) { switchTeam('inv'); setMode('pm'); }
  else if (t.includes('undervalued') || t.includes('stock')) { switchTeam('res'); setRMode('all'); }
  else if (t.includes('news') || t.includes('today')) { switchTeam('news'); setNMode('all'); }
  else if (t.includes('round') || t.includes('discuss')) { switchTeam('inv'); setMode('round'); }
  else { switchTeam('inv'); setMode('all'); }
  document.getElementById('ui').value = t;
  send();
}
function fc(cmd) { const ta = document.getElementById('ui'); ta.value = cmd; ta.focus(); }
function hk(e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } }
function ar(el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 130) + 'px'; }

// ═══════════════════════════════════════
// START
// ═══════════════════════════════════════
init();
