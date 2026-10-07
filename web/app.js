
let DATA = null, SEL = 0, HIDE_FOLDS = false;
// 🎯 차트 이탈만 보기 — 프리플랍 결정이 가져온 차트와 어긋난 핸드(서버가 붙인 chart_dev)만
let DEV_ONLY = false;   // SEL: -1 복기, -2 통계, -3 검색, -4 드릴다운, -5 뱅크롤, -6 타이머, -7 문제
let STACK_UNIT = 'chips';   // 스택 변화 차트 단위: 'chips'(절대 칩) | 'bb'
let DRILL = null;   // 그리드 칸 클릭 시 해당 조합 핸드 목록 ({id,name,hand_count,hands})
let BANKROLL = null, BANK_EDIT = null, BANK_SHOWFORM = false, BANK_FILTER = 'all', BANK_PREFILL = null, BANK_CHART = 'cum';
let BANK_PAGE = 0;
let BANK_TAB = 'results';   // 'results'(토너 성적) | 'cash'(입출금)
let BANK_CF_PAGE = 0;       // 입출금 내역 페이지
const BANK_PAGE_SIZE = 50;
let REPORT = null, ANALYZED_TOTAL = 0, REPORT_STREAMING = false;
let REVIEW_HANDS = null, REVIEW_COUNT = 0, STATS = null;
let REVIEW_PAGE = 0, REVIEW_UNANALYZED_ONLY = false, REVIEW_HIDE_ALLIN = false, REVIEW_BATCH = null;   // 복기: 페이징·필터·배치분석
const REVIEW_PAGE_SIZE = 50;
let SEARCH_Q = '', SEARCH_SORT = 'recent', SEARCH_PAGE = 0;
const SEARCH_PAGE_SIZE = 20;
let GRID_CACHE = {}, GRID_POS = 'all', GRID_STACK = 'all', GRID_METRIC = 'mix', STATS_TAB = 'summary';   // 통계 하위 탭
let LEAKS = null;   // 리크 리포트 캐시 (/api/leaks)

const $ = s => document.querySelector(s);

function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 1800);
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// 카드 토큰(As, Td, 9c...)에 무늬 색 입히기
function colorCards(html) {
  return html.replace(/\b([2-9TJQKA])([shdc])\b/g, (m, r, s) => {
    const glyph = {s:'♠', h:'♥', d:'♦', c:'♣'}[s];
    return `<span class="suit-${s}">${r}${glyph}</span>`;
  });
}

// 핸드 상세 표시용: 상단 헤더 2줄(## Hand #... / NLH | Blinds ...) 제거
// 복사/다운로드용 마크다운에는 유지됨 (AI에 단독으로 줄 때 필요한 컨텍스트)
function stripHeader(md) {
  return md.replace(/^## [^\n]*\n[^\n]*\n+/, '');
}

// 변환 마크다운 → 간단 HTML
function mdToHtml(md) {
  const lines = md.split('\n');
  let out = [], inList = false;
  for (let line of lines) {
    let h = esc(line);
    h = h.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    if (line.startsWith('## ')) { if(inList){out.push('</ul>');inList=false;}
      out.push('<h2>' + h.slice(3) + '</h2>'); continue; }
    if (line.startsWith('- ')) {
      if (!inList) { out.push('<ul>'); inList = true; }
      const cls = line.includes('당신의 차례') ? ' class="askturn"'
                : line.includes('HERO DECISION') ? ' class="decision"' : '';
      out.push(`<li${cls}>` + h.slice(2) + '</li>'); continue;
    }
    if (inList) { out.push('</ul>'); inList = false; }
    if (line.trim() === '') continue;
    if (/^\*\*(PREFLOP|FLOP|TURN|RIVER|SHOWDOWN|RESULT|Players:)/.test(line))
      out.push('<p class="sect">' + h + '</p>');
    else out.push('<p>' + h + '</p>');
  }
  if (inList) out.push('</ul>');
  return colorCards(out.join('\n'));
}

function cardsHtml(cards) {
  return colorCards(esc(cards.join(' '))) || '<span style="color:var(--dim)">—</span>';
}

function netHtml(net, netBb) {
  const cls = net >= 0 ? 'win' : 'lose';
  const sign = net >= 0 ? '+' : '';
  return `<span class="net ${cls}">${sign}${net.toLocaleString()} (${sign}${netBb}bb)</span>`;
}

function renderSidebar() {
  // SEL >= 0 은 검색에서 연 토너먼트 핸드 뷰 → 🔍 토너먼트 항목을 활성 표시
  const inTourney = SEL >= 0;
  $('#sidebar').innerHTML = `
    <div class="tourney stats ${(SEL===-2||SEL===-4)?'sel':''}" onclick="selectStats()">
      <div class="tname">📈 통계</div>
      <div class="tmeta">포지션별 칩 EV · VPIP/PFR · WTSD</div>
    </div>
    <div class="tourney ${SEL===-5?'sel':''}" style="border-color:rgba(63,191,111,.4)" onclick="selectBankroll()">
      <div class="tname">💰 뱅크롤</div>
      <div class="tmeta">실제 손익($) · ROI · 토너 결과 입력</div>
    </div>
    <div class="tourney review ${SEL===-1?'sel':''}" onclick="selectReview()">
      <div class="tname">📌 복기 추천</div>
      <div class="tmeta">큰 손실 · 쇼다운/올인 패배 핸드 ${REVIEW_COUNT}개</div>
    </div>
    <div class="tourney search ${(SEL===-3||inTourney)?'sel':''}" onclick="selectSearch()">
      <div class="tname">🔍 토너먼트</div>
      <div class="tmeta">${DATA.tournaments.length}개 · 검색해서 열기</div>
    </div>
    <div class="tourney quiz ${SEL===-7?'sel':''}" onclick="selectQuiz()">
      <div class="tname">🎯 문제 풀기</div>
      <div class="tmeta">${qzSidebarMeta()}</div>
    </div>
    <div class="tourney ${SEL===-8?'sel':''}" style="border-color:rgba(77,163,255,.4)"
         onclick="selectRangeChart()">
      <div class="tname">📊 레인지 차트</div>
      <div class="tmeta">${rgvSidebarMeta()}</div>
    </div>
    <div class="tourney ${SEL===-9?'sel':''}" style="border-color:rgba(180,140,255,.4)"
         onclick="selectCoach()">
      <div class="tname">💬 AI 코치${COACH.busy ? ' <span class="ai-spinner"></span>' : ''}</div>
      <div class="tmeta">${coSidebarMeta()}</div>
    </div>
    <div class="tourney timer ${SEL===-6?'sel':''}" onclick="selectTimer()">
      <div class="tname">⏱ 토너먼트 타이머${TIMER.run.running ? ' <span style="color:var(--green)">●</span>' : ''}</div>
      <div class="tmeta">${tmSidebarMeta()}</div>
    </div>`;
}

// 토너먼트 검색 뷰 — 사이드바 대신 본문에서 검색/페이징으로 토너 열기
function selectSearch() {
  SEL = -3; renderSidebar();
  $('#mainhead').innerHTML = `
    <h2 style="flex:0 0 auto">🔍 토너먼트</h2>
    <input type="text" id="tsearch" placeholder="이름 또는 #번호 검색..."
       value="${esc(SEARCH_Q)}" oninput="onSearchInput(this.value)">
    <label class="small">정렬
      <select class="ts-sort" onchange="onSearchSort(this.value)">
        <option value="recent">최신순</option>
        <option value="name">이름순</option>
        <option value="hands">핸드 많은 순</option>
      </select>
    </label>`;
  document.querySelector('.ts-sort').value = SEARCH_SORT;
  renderSearchResults();
  $('#main').scrollTop = 0;
  const inp = document.getElementById('tsearch');
  if (inp) { inp.focus(); inp.setSelectionRange(inp.value.length, inp.value.length); }
}

function onSearchInput(v) { SEARCH_Q = v; SEARCH_PAGE = 0; renderSearchResults(); }
function onSearchSort(v) { SEARCH_SORT = v; SEARCH_PAGE = 0; renderSearchResults(); }
function gotoSearchPage(p) { SEARCH_PAGE = p; renderSearchResults(); $('#main').scrollTop = 0; }

function filteredTournaments() {
  let list = DATA.tournaments.slice();
  const q = SEARCH_Q.trim().toLowerCase();
  if (q) list = list.filter(t =>
    (t.name || '').toLowerCase().includes(q) || String(t.id).toLowerCase().includes(q));
  if (SEARCH_SORT === 'name') list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  else if (SEARCH_SORT === 'hands') list.sort((a, b) => b.hand_count - a.hand_count);
  else list.sort((a, b) => (b.start || '').localeCompare(a.start || ''));
  return list;
}

function pager(pages) {
  if (pages <= 1) return '';
  const cur = SEARCH_PAGE;
  const set = new Set([0, pages - 1, cur - 1, cur, cur + 1]);
  const nums = [...set].filter(p => p >= 0 && p < pages).sort((a, b) => a - b);
  let html = `<div class="ts-pager">
    <button ${cur === 0 ? 'disabled' : ''} onclick="gotoSearchPage(${cur - 1})">‹ 이전</button>`;
  let prev = -1;
  for (const p of nums) {
    if (prev >= 0 && p - prev > 1) html += `<span class="ts-ellip">…</span>`;
    html += `<button class="${p === cur ? 'primary' : ''}" onclick="gotoSearchPage(${p})">${p + 1}</button>`;
    prev = p;
  }
  html += `<button ${cur === pages - 1 ? 'disabled' : ''} onclick="gotoSearchPage(${cur + 1})">다음 ›</button></div>`;
  return html;
}

function renderSearchResults() {
  const all = filteredTournaments();
  const pages = Math.max(1, Math.ceil(all.length / SEARCH_PAGE_SIZE));
  if (SEARCH_PAGE >= pages) SEARCH_PAGE = pages - 1;
  if (SEARCH_PAGE < 0) SEARCH_PAGE = 0;
  const start = SEARCH_PAGE * SEARCH_PAGE_SIZE;
  const page = all.slice(start, start + SEARCH_PAGE_SIZE);
  const rows = page.map(t => {
    const idx = DATA.tournaments.indexOf(t);
    const end = (t.end || '').slice(11, 16);
    return `<div class="ts-card" onclick="selectTourney(${idx})">
      <div class="ts-top">
        <span class="ts-name">${esc(t.name || '')}</span>
        <span class="ts-id">#${esc(String(t.id))}</span>
        <span class="ts-arrow">→</span>
      </div>
      <div class="ts-meta">핸드 ${t.hand_count}${t.analyzed ? ' · 🤖' + t.analyzed : ''} · ${esc((t.start || '').slice(0, 16))}${end ? ' ~ ' + end : ''}</div>
    </div>`;
  }).join('');
  const counter = SEARCH_Q.trim()
    ? `${DATA.tournaments.length}개 중 <strong style="color:var(--text)">${all.length}</strong>개 검색됨`
    : `전체 ${all.length}개`;
  $('#hands').innerHTML = `
    <div class="ts-counter">${counter}</div>
    ${all.length ? rows : '<p style="color:var(--dim)">검색 결과가 없습니다.</p>'}
    ${pager(pages)}`;
}

// 통계 대시보드 뷰 — 전체 핸드 집계 (요약 / 핸드 그리드 탭)
async function selectStats() {
  SEL = -2; renderSidebar();
  $('#mainhead').innerHTML = '<h2>📈 통계</h2>';
  if (!STATS) {
    $('#hands').innerHTML = '<div class="ai-loading">집계 중</div>';
    const res = await fetch('/api/stats');
    STATS = await res.json();
  }
  if (SEL !== -2) return;
  renderStatsView(); $('#main').scrollTop = 0;
}

function setStatsTab(tab) { STATS_TAB = tab; renderStatsView(); $('#main').scrollTop = 0; }

async function renderStatsView() {
  $('#mainhead').innerHTML = `
    <h2 style="flex:0 0 auto">📈 통계</h2>
    <button class="${STATS_TAB === 'summary' ? 'primary' : ''}" onclick="setStatsTab('summary')">요약</button>
    <button class="${STATS_TAB === 'grid' ? 'primary' : ''}" onclick="setStatsTab('grid')">핸드 그리드</button>
    <button class="${STATS_TAB === 'leaks' ? 'primary' : ''}" onclick="setStatsTab('leaks')">🩹 리크</button>`;
  if (STATS_TAB === 'grid') {
    if (!GRID_CACHE[GRID_POS]) $('#hands').innerHTML = '<div class="ai-loading">핸드 그리드 집계 중</div>';
    await ensureGrid();
    if (SEL !== -2 || STATS_TAB !== 'grid') return;
    renderGrid();
  } else if (STATS_TAB === 'leaks') {
    if (!LEAKS) $('#hands').innerHTML = '<div class="ai-loading">리크 집계 중</div>';
    await ensureLeaks();
    if (SEL !== -2 || STATS_TAB !== 'leaks') return;
    renderLeaks();
  } else {
    renderStats();
  }
}

async function ensureLeaks() {
  if (!LEAKS) LEAKS = await fetch('/api/leaks').then(r => r.json());
  return LEAKS;
}

// 포지션+스택 조합별 그리드를 받아 캐시 (조합당 1회만 fetch)
function gridKey() { return GRID_POS + '|' + GRID_STACK; }
async function ensureGrid() {
  const k = gridKey();
  if (!GRID_CACHE[k]) {
    const params = [];
    if (GRID_POS !== 'all') params.push('pos=' + encodeURIComponent(GRID_POS));
    if (GRID_STACK !== 'all') params.push('stack=' + encodeURIComponent(GRID_STACK));
    const url = '/api/handgrid' + (params.length ? '?' + params.join('&') : '');
    GRID_CACHE[k] = await fetch(url).then(r => r.json());
  }
  return GRID_CACHE[k];
}

async function setGridFilter(kind, v) {
  if (kind === 'pos') { if (GRID_POS === v) return; GRID_POS = v; }
  else { if (GRID_STACK === v) return; GRID_STACK = v; }
  if (!GRID_CACHE[gridKey()]) $('#hands').innerHTML = '<div class="ai-loading">집계 중</div>';
  await ensureGrid();
  if (SEL !== -2 || STATS_TAB !== 'grid') return;
  renderGrid();
}
function setGridPos(p) { return setGridFilter('pos', p); }
function setGridStack(s) { return setGridFilter('stack', s); }

// --- 스타팅 핸드 13×13 매트릭스 ---
const GRID_RANKS = 'AKQJT98765432'.split('');

// 행 i, 열 j → 조합 라벨 (대각선=페어, ↗위=수딧, ↙아래=오프수딧)
function comboLabel(i, j) {
  const hi = GRID_RANKS[Math.min(i, j)], lo = GRID_RANKS[Math.max(i, j)];
  if (i === j) return hi + lo;
  return hi + lo + (i < j ? 's' : 'o');
}

function setGridMetric(m) { GRID_METRIC = m; renderGrid(); }

function gridTd(cells, i, j, maxAbs) {
  const combo = comboLabel(i, j);
  const d = cells[combo];
  const pairCls = i === j ? ' pair' : '';
  if (!d || !d.n) return `<td><div class="hc empty${pairCls}"><div class="lab">${combo}</div></div></td>`;
  const n = d.n;
  const vpip = Math.round(100 * d.vpip / n), pfr = Math.round(100 * d.pfr / n);
  const opp = d.rfi_opp || 0;
  const rfiPct = opp ? Math.round(100 * d.rfi / opp) : null;   // RFI는 기회(폴드 투 히어로) 대비
  // 액션 구성 모드 — 셀을 오픈/3벳/콜/올인/폴드 스택바로 채움
  if (GRID_METRIC === 'mix') {
    // 레이즈 계열을 옅은→진한 블루 램프로 연속 배치 (오픈→3벳→올인), 그 뒤 콜(틸)·폴드(회색)
    const po = 100 * (d.open || 0) / n, ptb = 100 * (d.tb || 0) / n;
    const pai = 100 * (d.allin || 0) / n, pcl = 100 * (d.call || 0) / n;
    const s1 = po, s2 = s1 + ptb, s3 = s2 + pai, s4 = s3 + pcl;
    const a = 0.7;   // 반투명 — 셀 배경(panel2) 위에 은은하게 얹힘, 폴드는 투명
    const bg = `linear-gradient(90deg,rgba(124,196,255,${a}) 0 ${s1}%,rgba(74,143,224,${a}) ${s1}% ${s2}%,` +
               `rgba(44,91,208,${a}) ${s2}% ${s3}%,rgba(45,212,167,${a}) ${s3}% ${s4}%,transparent ${s4}% 100%)`;
    const t = `${combo} · ${n}핸드 · 오픈 ${Math.round(po)}% · 3벳 ${Math.round(ptb)}% · ` +
              `올인 ${Math.round(pai)}% · 콜 ${Math.round(pcl)}% · 폴드 ${Math.round(100 - s4)}%`;
    return `<td><div class="hc mix${pairCls}" style="background-image:${bg};cursor:pointer" title="${t} — 클릭하면 핸드 보기" onclick="drillCombo('${combo}')"><div class="lab">${combo}</div></div></td>`;
  }
  let val, bg, dim = '';
  if (GRID_METRIC === 'bb') {
    val = (d.bb >= 0 ? '+' : '') + Math.round(d.bb);
    const t = Math.min(1, Math.abs(d.bb) / maxAbs);
    const rgb = d.bb >= 0 ? '63,191,111' : '224,85,106';
    bg = `rgba(${rgb},${(0.1 + 0.6 * t).toFixed(2)})`;
    if (n < 20) dim = ' dim';
  } else if (GRID_METRIC === 'rfi') {
    if (rfiPct === null) { val = '·'; bg = 'var(--panel2)'; dim = ' dim'; }   // 오픈 기회 없던 조합
    else { val = rfiPct; bg = `rgba(77,163,255,${(rfiPct / 100 * 0.6).toFixed(2)})`; if (opp < 10) dim = ' dim'; }
  } else {
    const pct = GRID_METRIC === 'vpip' ? vpip : pfr;
    val = pct;
    bg = `rgba(77,163,255,${(pct / 100 * 0.6).toFixed(2)})`;
  }
  const rfiTxt = opp ? `${Math.round(100 * d.rfi / opp)}% (${opp}회 기회)` : '기회없음';
  const title = `${combo} · ${n}핸드 · VPIP ${vpip}% · PFR ${pfr}% · RFI ${rfiTxt} · 칩EV ${d.bb >= 0 ? '+' : ''}${d.bb}bb`;
  return `<td><div class="hc${dim}${pairCls}" style="background:${bg};cursor:pointer" title="${title} — 클릭하면 핸드 보기" onclick="drillCombo('${combo}')">
    <div class="lab">${combo}</div><div class="val">${val}</div></div></td>`;
}

function renderGrid() {
  const cells = (GRID_CACHE[gridKey()] && GRID_CACHE[gridKey()].cells) || {};
  let maxAbs = 1, totalN = 0, totalOpp = 0, totalActs = 0;
  for (const k in cells) {
    maxAbs = Math.max(maxAbs, Math.abs(cells[k].bb));
    totalN += cells[k].n; totalOpp += (cells[k].rfi_opp || 0);
    totalActs += (cells[k].open || 0) + (cells[k].tb || 0) + (cells[k].call || 0) + (cells[k].allin || 0);
  }
  let rows = '<tr><th></th>' + GRID_RANKS.map(r => `<th>${r}</th>`).join('') + '</tr>';
  for (let i = 0; i < 13; i++) {
    let tds = `<th>${GRID_RANKS[i]}</th>`;
    for (let j = 0; j < 13; j++) tds += gridTd(cells, i, j, maxAbs);
    rows += `<tr>${tds}</tr>`;
  }
  const mBtn = (k, l) => `<button class="${GRID_METRIC === k ? 'primary' : ''}" onclick="setGridMetric('${k}')">${l}</button>`;
  const pBtn = (k, l) => `<button class="${GRID_POS === k ? 'primary' : ''}" onclick="setGridPos('${k}')">${l}</button>`;
  const sBtn = (k, l) => `<button class="${GRID_STACK === k ? 'primary' : ''}" onclick="setGridStack('${k}')">${l}</button>`;
  const positions = ((STATS && STATS.positions) || []).map(p => p.pos).filter(p => p !== '?');
  const stacks = [['all', '전체'], ['pf', '<15'], ['short', '15-25'], ['mid', '25-40'], ['deep', '40+']];
  const stackLabel = {all: '전체 스택', pf: '<15bb', short: '15-25bb', mid: '25-40bb', deep: '40bb+'}[GRID_STACK];
  const unit = {bb: '칩 EV(bb)', rfi: 'RFI%(오픈)', mix: '액션 비율'}[GRID_METRIC];
  const posLabel = GRID_POS === 'all' ? '전체 포지션' : GRID_POS;
  // RFI/구성 모드인데 데이터가 0 → 기존 DB에 해당 필드 없음
  const needRebuild = (GRID_METRIC === 'rfi' && totalOpp === 0) || (GRID_METRIC === 'mix' && totalActs === 0 && totalN > 0);
  const rebuildHint = needRebuild
    ? `<div class="ai-error" style="color:var(--gold)">이 지표는 <code>python3 gui.py --rebuild</code>로 1회 재변환해야 표시됩니다 (기존 DB엔 해당 필드가 없음).</div>`
    : '';
  const chip = (c, l) => `<span style="display:inline-flex;align-items:center;gap:4px"><span style="width:11px;height:11px;border-radius:2px;background:${c};display:inline-block"></span>${l}</span>`;
  const legend = GRID_METRIC === 'mix'
    ? `<div style="display:flex;gap:14px;margin-bottom:10px;flex-wrap:wrap;color:var(--dim);font-size:12px">
         ${chip('rgba(124,196,255,.7)', '오픈')} ${chip('rgba(74,143,224,.7)', '3벳')} ${chip('rgba(44,91,208,.7)', '올인')} ${chip('rgba(45,212,167,.7)', '콜')} ${chip('var(--panel2)', '폴드')}
       </div>` : '';
  const note = GRID_METRIC === 'mix'
    ? ' 각 칸을 프리플랍 첫 액션 비율로 채움 (바 길이=VPIP). 칸 호버로 정확한 %.'
    : (GRID_METRIC === 'rfi'
      ? ' RFI는 폴드로 히어로까지 온 경우(오픈 기회) 대비 첫 레이즈 비율 — 솔버 오픈 차트와 같은 정의. 기회 10회 미만은 흐리게, 기회 없던 칸은 · 표시.'
      : ' 포지션별로 보면 표본이 작아지니 칩 EV는 참고만 (20핸드 미만 흐리게).');
  $('#hands').innerHTML = `
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">
      <span style="color:var(--dim);font-size:13px">포지션:</span>
      ${pBtn('all', '전체')} ${positions.map(p => pBtn(p, p)).join(' ')}
    </div>
    <div style="display:flex;align-items:center;gap:6px;margin-bottom:8px;flex-wrap:wrap">
      <span style="color:var(--dim);font-size:13px">스택(bb):</span>
      ${stacks.map(([k, l]) => sBtn(k, l)).join(' ')}
    </div>
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap">
      <span style="color:var(--dim);font-size:13px">표시 기준:</span>
      ${mBtn('mix', '액션')} ${mBtn('rfi', 'RFI')} ${mBtn('bb', '칩 EV')}
      <span style="color:var(--dim);font-size:12px;margin-left:auto">${esc(posLabel)} · ${esc(stackLabel)} · ${totalN.toLocaleString()}핸드 · ${unit}</span>
    </div>
    ${legend}${rebuildHint}
    <div class="grid-wrap"><table class="hgrid">${rows}</table></div>
    <p style="color:var(--dim);font-size:12px;margin-top:10px">
      대각선=페어 · ↗ 수딧 · ↙ 오프수딧. 칸에 마우스를 올리면 핸드 수·VPIP·PFR·RFI·칩 EV 전체가, <strong style="color:var(--text)">클릭하면 해당 조합 핸드 목록</strong>이 열립니다.${note}</p>`;
}

function statCard(val, label, sub, cls) {
  return `<div class="stat-card">
    <div class="v ${cls || ''}">${val}</div>
    <div class="l">${label}</div>
    ${sub ? `<div class="sub">${sub}</div>` : ''}
  </div>`;
}

function bbCell(v) {
  const cls = v >= 0 ? 'win' : 'lose';
  return `<span class="tnum ${cls}">${v >= 0 ? '+' : ''}${v.toLocaleString()}</span>`;
}

function renderStats() {
  const s = STATS;
  if (!s || !s.total) {
    $('#hands').innerHTML = '<p style="color:var(--dim)">집계할 핸드가 없습니다.</p>';
    return;
  }
  const pfr = s.pfr_pct === null
    ? statCard('—', 'PFR', '<code>--rebuild</code> 후 표시')
    : statCard(s.pfr_pct + '%', 'PFR',
        s.pfr_known < s.total ? `${s.pfr_known.toLocaleString()}핸드 기준` : '프리플랍 레이즈');

  const cards = [
    statCard(s.total.toLocaleString(), '핸드', `${s.tournaments}개 토너먼트`),
    statCard(s.vpip_pct + '%', 'VPIP', '자발적 팟 참여'),
    pfr,
    statCard(s.wtsd_pct + '%', 'WTSD', 'VPIP 대비 쇼다운'),
    statCard(s.wsd_pct + '%', 'W$SD', `쇼다운 ${s.showdown}회 중 승`),
  ].join('');

  const posRows = s.positions.map(p => {
    const vpip = p.hands ? Math.round(100 * p.vpip / p.hands) : 0;
    return `<tr>
      <td>${esc(p.pos)}</td><td>${p.hands.toLocaleString()}</td>
      <td>${vpip}%</td><td>${bbCell(p.net_bb)}</td></tr>`;
  }).join('');

  $('#hands').innerHTML = `
    <div class="stats-grid">${cards}</div>
    <div class="stat-section">
      <h3>포지션별 <span style="color:var(--dim);font-size:12px;font-weight:400">(칩 EV — 플레이 품질 지표, 상금 아님)</span></h3>
      <table class="stat-table">
        <thead><tr><th>포지션</th><th>핸드</th><th>VPIP</th><th>칩 EV(bb)</th></tr></thead>
        <tbody>${posRows}</tbody>
      </table>
    </div>`;
}

// --- 🩹 리크 대시보드 (AI 등급 집계) ---
const LEAK_GRADES = ['좋음', '무난', '의문', '실수'];
const LEAK_COLOR = {'좋음': 'var(--green)', '무난': 'var(--accent)', '의문': 'var(--gold)', '실수': 'var(--red)'};

// 전체 평가 분포 — 가로 스택 막대 + 범례
function leakOverallBar(o) {
  const tot = LEAK_GRADES.reduce((a, g) => a + (o[g] || 0), 0);
  if (!tot) return '<p style="color:var(--dim)">총평 등급이 집계된 핸드가 없습니다.</p>';
  const bars = LEAK_GRADES.map(g => {
    const v = o[g] || 0; if (!v) return '';
    const pct = 100 * v / tot;
    return `<div title="${g} ${v}핸드" style="width:${pct}%;background:${LEAK_COLOR[g]};display:flex;align-items:center;justify-content:center;font-size:11px;color:#0c1117;font-weight:600">${pct >= 9 ? Math.round(pct) + '%' : ''}</div>`;
  }).join('');
  const legend = LEAK_GRADES.map(g =>
    `<span style="font-size:12px;color:var(--dim)"><span style="display:inline-block;width:9px;height:9px;background:${LEAK_COLOR[g]};border-radius:2px;margin-right:4px;vertical-align:middle"></span>${VERDICT_EMOJI[g]} ${g} ${o[g] || 0}</span>`
  ).join('  ');
  return `<div style="display:flex;height:22px;border-radius:5px;overflow:hidden;margin:8px 0 6px">${bars}</div>
    <div style="display:flex;gap:16px;flex-wrap:wrap">${legend}</div>`;
}

// 리크율 막대 한 줄 (의문+실수 / 평가횟수). dimUnder 미만 표본은 흐리게.
function leakRateRow(label, leak, evald, isMax, dimUnder) {
  const rate = evald ? Math.round(100 * leak / evald) : 0;
  const dim = evald < (dimUnder || 0);
  const col = rate >= 35 ? 'var(--red)' : rate >= 20 ? 'var(--gold)' : 'var(--accent)';
  return `<div style="display:flex;align-items:center;gap:10px;margin:6px 0;${dim ? 'opacity:.4' : ''}">
    <div style="width:62px;color:var(--dim);font-size:13px;text-align:right">${esc(label)}</div>
    <div style="flex:1;height:14px;background:var(--panel2);border-radius:4px;overflow:hidden">
      <div style="width:${Math.min(100, rate)}%;height:100%;background:${col}"></div>
    </div>
    <div style="width:120px;font-size:12px">${evald ? rate + '%' : '—'} <span style="color:var(--dim)">(${leak}/${evald})</span>${isMax && rate > 0 ? ' 🔴' : ''}</div>
  </div>`;
}

function renderLeaks() {
  const L = LEAKS;
  if (!L) return;
  if (!L.analyzed) {
    $('#hands').innerHTML = `<p style="color:var(--dim)">AI 분석된 핸드가 없습니다. 📌 복기 탭에서 핸드를 분석하면 여기에 리크가 집계됩니다.</p>`;
    return;
  }
  const pct = Math.round(100 * L.analyzed / L.total);

  // 스트리트별 리크율 (의문+실수 / 4등급 합)
  const streetData = L.streets.map(s => {
    const evald = LEAK_GRADES.reduce((a, g) => a + (s[g] || 0), 0);
    return {label: s.street, leak: (s['의문'] || 0) + (s['실수'] || 0), evald};
  });
  const streetMax = Math.max(0, ...streetData.filter(d => d.evald >= 10).map(d => d.evald ? d.leak / d.evald : 0));
  const streetRows = streetData.map(d =>
    leakRateRow(d.label, d.leak, d.evald, d.evald >= 10 && d.evald && d.leak / d.evald === streetMax && streetMax > 0, 5)
  ).join('');

  // 포지션별 리크율 (총평 등급 기준)
  const posMax = Math.max(0, ...L.positions.filter(p => p.n >= 10).map(p => p.n ? p.leak / p.n : 0));
  const posRows = L.positions.map(p =>
    leakRateRow(p.pos, p.leak, p.n, p.n >= 10 && p.n && p.leak / p.n === posMax && posMax > 0, 10)
  ).join('');

  // 가장 큰 리크 핸드 (실수 우선 · 칩손실 큰 순)
  const handRows = L.leak_hands.map(h => {
    const nb = h.net_bb;
    const netH = nb == null ? '' :
      `<span style="color:${nb >= 0 ? 'var(--green)' : 'var(--red)'};font-size:12px">${nb >= 0 ? '+' : ''}${nb.toFixed(1)}bb</span>`;
    return `<div onclick="openHandFromLeak('${h.tournament_id}','${h.hand_id}')"
        style="display:flex;align-items:center;gap:9px;padding:7px 8px;border-bottom:1px solid var(--border);cursor:pointer"
        onmouseover="this.style.background='var(--panel2)'" onmouseout="this.style.background=''">
      <span style="width:22px;text-align:center">${VERDICT_EMOJI[h.grade] || ''}</span>
      <span class="pos pos-badge" style="min-width:42px;text-align:center">${esc(h.hero_pos)}</span>
      <span style="width:46px;color:var(--dim);font-size:12px">${esc(h.street)}</span>
      <span style="width:52px">${cardsHtml(h.hero_cards)}</span>
      <span style="width:60px;text-align:right">${netH}</span>
      <span style="flex:1;color:var(--dim);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(h.snippet || '')}</span>
      <span style="color:var(--dim)">›</span>
    </div>`;
  }).join('') || '<p style="color:var(--dim);padding:8px">의문·실수로 분류된 핸드가 없습니다. 👍</p>';

  $('#hands').innerHTML = `
    <div style="color:var(--dim);font-size:13px;margin-bottom:14px">
      분석된 <strong style="color:var(--text)">${L.analyzed.toLocaleString()}</strong> / ${L.total.toLocaleString()}핸드 기준 (${pct}%)
      · <span style="font-size:12px">표본 적은 항목은 흐리게</span>
    </div>
    <div class="stat-section">
      <h3>전체 평가 분포</h3>
      ${leakOverallBar(L.overall)}
    </div>
    <div class="stat-section">
      <h3>스트리트별 리크율 <span style="color:var(--dim);font-size:12px;font-weight:400">(의문+실수 비율 · 어디서 새는지)</span></h3>
      ${streetRows}
    </div>
    <div class="stat-section">
      <h3>포지션별 리크율 <span style="color:var(--dim);font-size:12px;font-weight:400">(총평 등급 기준)</span></h3>
      ${posRows}
    </div>
    <div class="stat-section">
      <h3>❌ 가장 큰 리크 핸드 <span style="color:var(--dim);font-size:12px;font-weight:400">(실수 우선 · 칩손실 큰 순 · 클릭→핸드)</span></h3>
      <div style="border:1px solid var(--border);border-radius:8px;overflow:hidden">${handRows}</div>
    </div>`;
}

// 리크 핸드 클릭 → 해당 토너먼트 열고 그 핸드로 스크롤·펼치기·하이라이트
async function openHandFromLeak(tid, handId) {
  const i = DATA.tournaments.findIndex(t => t.id === tid);
  if (i < 0) { toast('연결된 핸드를 찾을 수 없습니다'); return; }
  await selectTourney(i);
  requestAnimationFrame(() => {
    const box = document.getElementById('ai-' + handId);
    const card = box && box.closest('.hand');
    if (!card) { toast('핸드를 찾지 못했습니다'); return; }
    card.classList.add('open');
    card.scrollIntoView({behavior: 'smooth', block: 'center'});
    card.style.transition = 'background .4s';
    card.style.background = 'rgba(232,184,79,.16)';
    setTimeout(() => { card.style.background = ''; }, 1500);
  });
}

// 복기 추천 뷰 — 전체 토너에서 추천 핸드만 모아서 표시
async function selectReview() {
  SEL = -1; REVIEW_PAGE = 0; renderSidebar();
  if (!REVIEW_HANDS) {
    $('#mainhead').innerHTML = '<h2>📌 복기 추천</h2>';
    $('#hands').innerHTML = '<div class="ai-loading">핸드 불러오는 중</div>';
    const res = await fetch('/api/review');
    const data = await res.json();
    if (SEL !== -1) return;
    REVIEW_HANDS = data.hands;
    for (const h of REVIEW_HANDS)
      if (h.analysis && !AI_CACHE[h.hand_id])
        AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  }
  renderMain(); $('#main').scrollTop = 0;
}

// 현재 선택된 뷰 (토너먼트 / 복기 추천 / 그리드 드릴다운)
function currentTourney() {
  if (SEL === -1) {
    const hands = REVIEW_HANDS || [];
    return {id: 'review', name: '📌 복기 추천', hand_count: hands.length, hands};
  }
  if (SEL === -4) return DRILL || {id: 'drill', name: '🃏', hand_count: 0, hands: []};
  return DATA.tournaments[SEL];
}

// 현재 표시 대상 핸드 (필터 적용) — 지연 로딩 전이면 빈 배열
function visibleHands() {
  const t = currentTourney();
  const hands = (t && t.hands) || [];
  // 이탈 핸드는 대부분 '차트는 오픈인데 폴드'라 프리폴드 숨기기보다 이 필터가 우선이다
  if (DEV_ONLY) return hands.filter(h => h.chart_dev);
  return HIDE_FOLDS ? hands.filter(h => !h.no_action_fold) : hands;
}

function toggleFolds() { HIDE_FOLDS = !HIDE_FOLDS; renderMain(); }
function toggleDevOnly() { DEV_ONLY = !DEV_ONLY; renderMain(); $('#main').scrollTop = 0; }

function setStackUnit(u) { if (STACK_UNIT === u) return; STACK_UNIT = u; renderMain(); }

// 리바이 감지: hand_id Set 반환 (리바이로 돌아온 직후 첫 핸드들)
function detectRebuys(allHands) {
  const pairs = allHands
    .filter(h => h.stack_bb != null && h.blinds)
    .map(h => { const b = parseFloat((h.blinds || '').split('/')[1]) || 0; return b ? {hand: h, chips: Math.round(h.stack_bb * b)} : null; })
    .filter(v => v != null);
  const ids = new Set();
  for (let i = 0; i < pairs.length - 1; i++) {
    const expected = Math.max(0, pairs[i].chips + (pairs[i].hand.net || 0));
    const bbVal = parseFloat((pairs[i].hand.blinds || '').split('/')[1]) || 100;
    if (pairs[i + 1].chips > expected + bbVal * 2) ids.add(pairs[i + 1].hand.hand_id);   // 리바이로 돌아온 첫 판에 태그
  }
  return ids;
}

function _fmtChips(v) { return v >= 1000 ? (v / 1000).toFixed(1).replace(/\.0$/, '') + 'k' : String(Math.round(v)); }

function stackChartHover(e, id) {
  const d = window[id]; if (!d) return;
  const svg = e.currentTarget;
  const rect = svg.getBoundingClientRect();
  const vx = (e.clientX - rect.left) / rect.width * d.W;
  const idx = Math.max(0, Math.min(d.pts.length - 1, Math.round(vx / d.W * (d.pts.length - 1))));
  const tip = document.getElementById(id + '_tip'); if (!tip) return;
  const hand = d.hands[idx];
  const chips = d.pts[idx];
  tip.style.display = 'block';
  const tx = e.clientX - rect.left, ty = e.clientY - rect.top;
  tip.style.left = (tx + 12) + 'px';
  tip.style.top = Math.max(0, ty - 28) + 'px';
  const unit = d.unit === 'bb' ? ' bb' : ' chips';
  const txt = d.unit === 'bb' ? (Math.round(chips * 10) / 10) : _fmtChips(chips);
  tip.textContent = (hand ? '#' + hand.hand_id + '  ' : '') + txt + unit;
}
function stackChartHide(id) { const t = document.getElementById(id + '_tip'); if (t) t.style.display = 'none'; }

// 토너먼트 스택 변화 차트 (절대 칩량 / BB 토글)
function tourneyStackChart(allHands) {
  const pairs = allHands
    .filter(h => h.stack_bb != null && h.blinds)
    .map(h => {
      const b = parseFloat((h.blinds || '').split('/')[1]) || 0;
      if (!b) return null;
      const chips = Math.round(h.stack_bb * b);
      // 단위에 맞춰 표시값(val)·핸드 손익(net) 동시 보유
      return STACK_UNIT === 'bb'
        ? {hand: h, val: h.stack_bb, net: (h.net || 0) / b}
        : {hand: h, val: chips, net: h.net || 0};
    })
    .filter(v => v != null);
  if (pairs.length < 2) return '';
  const valid = pairs.map(p => p.hand);
  const pts = pairs.map(p => p.val);
  // 세그먼트 분할: 버스트+리바이 시점에 선을 끊고 리바이 시작점에 점 마커 표시
  const drawPts = [];
  const drawHands = [];  // drawPts 인덱스 → hand (버스트/최종점은 null)
  const segments = [[]];   // 세그먼트별 drawPts 인덱스 목록
  const rebuyDots = [];    // 리바이 시작점 drawPts 인덱스
  let rebuyCount = 0;
  const gap = STACK_UNIT === 'bb' ? 2 : null;   // 리바이 판정 임계 (bb: 2bb, chips: 2*BB)
  for (let i = 0; i < valid.length; i++) {
    const idx = drawPts.length;
    drawPts.push(pts[i]); drawHands.push(valid[i]);
    segments[segments.length - 1].push(idx);
    const end = Math.max(0, pts[i] + (pairs[i].net || 0));
    const thresh = gap != null ? gap : (parseFloat((valid[i].blinds || '').split('/')[1]) || 100) * 2;
    if (i < valid.length - 1 && pts[i + 1] > end + thresh) {
      const bustIdx = drawPts.length;
      drawPts.push(end); drawHands.push(null);   // 버스트 → 0
      segments[segments.length - 1].push(bustIdx);
      segments.push([]);                         // 새 세그먼트 (선 끊김)
      rebuyDots.push(drawPts.length);            // 다음에 push될 인덱스 = 리바이 시작점
      rebuyCount++;
    }
  }
  const finalIdx = drawPts.length;
  drawPts.push(Math.max(0, pts[pts.length - 1] + (pairs[pairs.length - 1].net || 0)));
  drawHands.push(null);
  segments[segments.length - 1].push(finalIdx);
  const W = 900, H = 175;
  const mn = Math.min(...drawPts), mx = Math.max(...drawPts), range = mx - mn || 1;
  const X = i => (i / (drawPts.length - 1) * W).toFixed(1);
  const Y = v => (H - (v - mn) / range * H).toFixed(1);
  const start = drawPts[0], last = drawPts[drawPts.length - 1];
  const color = last >= start ? 'var(--green)' : 'var(--red)';
  const polylines = segments
    .filter(seg => seg.length >= 2)
    .map(seg => `<polyline points="${seg.map(i => `${X(i)},${Y(drawPts[i])}`).join(' ')}"
      fill="none" stroke="${color}" stroke-width="2" vector-effect="non-scaling-stroke"/>`)
    .join('');
  const dots = rebuyDots.map(i => {
    const cx = parseFloat(X(i)), cy = parseFloat(Y(drawPts[i])), s = 2.5;
    return `<polygon points="${cx},${cy-s} ${cx+s},${cy} ${cx},${cy+s} ${cx-s},${cy}"
      fill="${color}" vector-effect="non-scaling-stroke"/>`;
  }).join('');
  const rebuyLabel = rebuyCount
    ? ` · <span style="color:var(--gold)">리바이 ${rebuyCount}회</span>` : '';
  const cid = 'sc_' + Date.now();
  window[cid] = { pts: drawPts, hands: drawHands, W, unit: STACK_UNIT };
  const uBtn = (k, l) => `<button onclick="setStackUnit('${k}')" style="font-size:11px;padding:2px 9px;border-radius:6px;border:1px solid var(--border);cursor:pointer;${STACK_UNIT === k ? 'background:var(--accent);color:#0c1117;font-weight:600' : 'background:transparent;color:var(--dim)'}">${l}</button>`;
  return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:10px 12px;margin-bottom:14px;max-width:900px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">
      <div style="color:var(--dim);font-size:12px">스택 변화 · ${valid.length}핸드${rebuyLabel}</div>
      <div style="display:flex;gap:4px">${uBtn('chips', '칩')}${uBtn('bb', 'BB')}</div>
    </div>
    <div style="position:relative">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:175px;display:block;cursor:crosshair"
        onmousemove="stackChartHover(event,'${cid}')" onmouseleave="stackChartHide('${cid}')">
        <line x1="0" y1="${Y(0)}" x2="${W}" y2="${Y(0)}" stroke="var(--border)" stroke-width="1"/>
        ${polylines}
        ${dots}
      </svg>
      <div id="${cid}_tip" style="display:none;position:absolute;pointer-events:none;background:rgba(20,20,30,0.92);color:var(--text);font-size:11px;padding:3px 8px;border-radius:4px;white-space:nowrap;border:1px solid var(--border)"></div>
    </div>
  </div>`;
}

// 복기 핸드 분석 완료 여부 (저장된 analysis 또는 이번 세션 AI_CACHE done)
function reviewAnalyzed(h) {
  const c = AI_CACHE[h.hand_id];
  return !!(h.analysis || (c && c.status === 'done'));
}

function renderMain() {
  const t = currentTourney();
  let hands = visibleHands();
  const nDev = ((t && t.hands) || []).filter(h => h.chart_dev).length;
  let countLabel = DEV_ONLY ? `${hands.length}핸드 표시 (🎯 차트 이탈만)`
    : HIDE_FOLDS
    ? `${hands.length}핸드 표시 (프리폴드 ${t.hand_count - hands.length}개 숨김)`
    : `${t.hand_count}핸드`;

  // 복기 탭 전용: 미분석/프리올인 필터 + 페이징(50개씩) + 배치 분석. 필터는 별도 필터 바로 분리.
  let pager = '', banner = '', filterBar = '', headBtns;
  if (SEL === -1) {
    const f = reviewFiltered();          // 프리올인·미분석 필터 적용 (페이징 전)
    hands = f.list;
    const total = hands.length;
    const pages = Math.max(1, Math.ceil(total / REVIEW_PAGE_SIZE));
    if (REVIEW_PAGE >= pages) REVIEW_PAGE = pages - 1;
    if (REVIEW_PAGE < 0) REVIEW_PAGE = 0;
    hands = hands.slice(REVIEW_PAGE * REVIEW_PAGE_SIZE, (REVIEW_PAGE + 1) * REVIEW_PAGE_SIZE);
    const pageUnan = hands.filter(h => !reviewAnalyzed(h)).length;
    const running = !!(REVIEW_BATCH && REVIEW_BATCH.running);
    // 헤더: 기능 버튼만 (배치 분석 + 펼치기/접기). 복사·다운로드는 복기에서 제거.
    headBtns = `
      <button class="primary" onclick="reviewAnalyzePage()" ${running || pageUnan === 0 ? 'disabled' : ''}>🤖 이 페이지 분석 (미분석 ${pageUnan})</button>
      <button onclick="toggleAll(true)">모두 펼치기</button>
      <button onclick="toggleAll(false)">모두 접기</button>`;
    // 필터 바: 필터만 따로 묶음
    filterBar = `<div style="display:flex;align-items:center;gap:8px;padding:8px 12px;margin-bottom:12px;background:var(--panel);border:1px solid var(--border);border-radius:9px">
      <span style="color:var(--dim);font-size:12px;margin-right:2px">필터</span>
      <button onclick="reviewToggleUnanalyzed()" class="${REVIEW_UNANALYZED_ONLY ? 'primary' : ''}">${REVIEW_UNANALYZED_ONLY ? '✓ ' : ''}미분석만</button>
      <button onclick="reviewToggleAllin()" class="${REVIEW_HIDE_ALLIN ? 'primary' : ''}" title="히어로가 프리플랍에 올인(푸시)한 핸드 숨김">${REVIEW_HIDE_ALLIN ? '✓ ' : ''}프리올인 숨기기</button>
    </div>`;
    pager = reviewPager(pages, total, f.totalUnan);
    if (running) banner = reviewBatchBanner();
    countLabel = `${t.hand_count}핸드`;
  } else {
    headBtns = `
      <button onclick="toggleDevOnly()" class="${DEV_ONLY ? 'primary' : ''}" ${nDev || DEV_ONLY ? '' : 'disabled'}
        title="프리플랍 결정이 가져온 GTO 차트와 어긋난 핸드 (고른 액션의 차트 빈도 25% 이하)">${DEV_ONLY ? '✓ ' : ''}🎯 차트 이탈 ${nDev}</button>
      <button onclick="toggleFolds()" class="${HIDE_FOLDS ? 'primary' : ''}">${HIDE_FOLDS ? '✓ ' : ''}프리폴드 숨기기</button>
      <button onclick="toggleAll(true)">모두 펼치기</button>
      <button onclick="toggleAll(false)">모두 접기</button>
      <button onclick="copyMd()">📋 마크다운 복사</button>
      <button class="primary" onclick="downloadMd()">⬇ .md 다운로드</button>`;
  }

  const backBtn = SEL === -4 ? (DRILL && DRILL.back ? `<button onclick="${DRILL.back[0]}">${DRILL.back[1]}</button>`
                                                      : `<button onclick="backToGrid()">← 그리드로</button>`)
    : SEL >= 0 ? `<button onclick="selectSearch()">← 검색으로</button>` : '';
  $('#mainhead').innerHTML = `
    ${backBtn}
    <h2>${esc(t.name)} <span style="color:var(--dim);font-size:13px">#${t.id} · ${countLabel}</span></h2>
    ${headBtns}`;
  const rebuyIds = detectRebuys(t.hands || []);
  $('#hands').innerHTML = banner + filterBar + (SEL !== -1 ? tourneyStackChart(t.hands || []) : '') + pager + hands.map((h, i) => {
    const tags = [];
    if (rebuyIds.has(h.hand_id)) tags.push('<span style="color:var(--gold)">리바이</span>');
    if (!h.vpip) tags.push('fold');
    if (h.showdown) tags.push('showdown');
    if (SEL === -1 && h.tournament_name) tags.unshift(esc(h.tournament_name));  // 복기 뷰: 출처 토너
    if (h.review && h.review.length) tags.push('📌 ' + h.review.join('·'));
    if (h.chart_dev) tags.push(`<span class="dev-tag" title="${esc(h.chart_dev.chart + ' · 차트 ' + h.chart_dev.mix)}">🎯 ${
      esc(h.chart_dev.did)} · 차트 ${esc(h.chart_dev.best)} ${h.chart_dev.best_pct}%</span>`);
    return `
    <div class="hand" id="hand${i}">
      <div class="hand-head" onclick="document.getElementById('hand${i}').classList.toggle('open')">
        <span class="hid">#${h.hand_id.slice(-6)} ${esc(h.datetime.slice(11,19))}</span>
        <span class="pos pos-badge">${h.hero_pos || '?'}</span>
        <span class="cards">${cardsHtml(h.hero_cards)}</span>
        <span class="tags">${h.blinds} · ${h.players}p${tags.length ? ' · ' + tags.join(' · ') : ''}</span>
        <span class="ai-flag" id="flag-${h.hand_id}">${aiFlagHtml(h.hand_id)}</span>
        ${netHtml(h.net, h.net_bb)}
      </div>
      <div class="hand-body">
        ${h.chart_dev ? `<div class="dev-note">🎯 <b>차트 이탈</b> — ${esc(h.chart_dev.chart)} ${esc(h.chart_dev.combo)}:
          실제 <b>${esc(h.chart_dev.did)}</b> (차트 ${h.chart_dev.did_pct}%) · 차트는 ${esc(h.chart_dev.mix)}</div>` : ''}
        ${mdToHtml(stripHeader(h.markdown))}
        <div class="ai-box" id="ai-${h.hand_id}">${aiBoxHtml(h.hand_id)}</div>
        <div style="margin-top:8px"><button onclick="coachFromHand('${h.hand_id}')">💬 이 핸드로 대화</button></div>
      </div>
    </div>`;
  }).join('') + pager;
}

// 복기 페이저 + 핸드/미분석 카운트
function reviewPager(pages, total, totalUnan) {
  const info = `${total}핸드${REVIEW_UNANALYZED_ONLY ? '(미분석)' : ` · 미분석 ${totalUnan}`}${pages > 1 ? ` · ${REVIEW_PAGE + 1}/${pages}p` : ''}`;
  if (pages <= 1) return `<div style="color:var(--dim);font-size:12px;margin:8px 0;text-align:center">${info}</div>`;
  return `<div style="display:flex;gap:6px;justify-content:center;align-items:center;margin:8px 0">
    <button ${REVIEW_PAGE <= 0 ? 'disabled' : ''} onclick="reviewGotoPage(${REVIEW_PAGE - 1})">‹</button>
    <span style="color:var(--dim);font-size:12px">${info}</span>
    <button ${REVIEW_PAGE >= pages - 1 ? 'disabled' : ''} onclick="reviewGotoPage(${REVIEW_PAGE + 1})">›</button></div>`;
}
function reviewGotoPage(p) { REVIEW_PAGE = p; renderMain(); $('#main').scrollTop = 0; }
function reviewToggleUnanalyzed() { REVIEW_UNANALYZED_ONLY = !REVIEW_UNANALYZED_ONLY; REVIEW_PAGE = 0; renderMain(); }
function reviewToggleAllin() { REVIEW_HIDE_ALLIN = !REVIEW_HIDE_ALLIN; REVIEW_PAGE = 0; renderMain(); }

// 복기 필터(프리올인 숨기기 + 미분석만) 적용 — 페이징 전 목록. 렌더·배치가 공유해 일관성 유지.
function reviewFiltered() {
  let base = (REVIEW_HANDS || []).slice();
  if (REVIEW_HIDE_ALLIN) base = base.filter(h => h.pf_action !== 'allin');   // 히어로 프리플랍 올인 제외
  const totalUnan = base.filter(h => !reviewAnalyzed(h)).length;
  const list = REVIEW_UNANALYZED_ONLY ? base.filter(h => !reviewAnalyzed(h)) : base;
  return {list, totalUnan};
}

// 배치 진행 배너 (배치 중에만 표시) — 전체 재렌더 없이 이 요소만 갱신
function reviewBatchBanner() {
  const b = REVIEW_BATCH || {done: 0, total: 0};
  const pct = b.total ? Math.round(b.done / b.total * 100) : 0;
  const cur = b.currentId ? ` · 현재 #${b.currentId.slice(-6)}` : '';
  return `<div id="batch-banner" style="border:1px solid var(--border);border-radius:9px;padding:10px 14px;margin-bottom:12px;background:var(--panel);display:flex;align-items:center;gap:12px">
    <span>🤖 배치 분석 중 <b>${b.done}/${b.total}</b> (${pct}%)${cur}</span>
    <div style="flex:1;height:6px;background:var(--panel2);border-radius:3px;overflow:hidden;min-width:80px">
      <div style="width:${pct}%;height:100%;background:var(--accent)"></div></div>
    <button onclick="reviewStopBatch()">■ 중단</button></div>`;
}
function updateBatchProgress() {
  const el = document.getElementById('batch-banner');
  if (el && REVIEW_BATCH) el.outerHTML = reviewBatchBanner();
}
function reviewStopBatch() { if (REVIEW_BATCH) REVIEW_BATCH.stop = true; }

// 현재 페이지의 미분석 핸드만 순차 분석 (기존 /api/analyze 재사용, 각 핸드 완료 시 서버 저장 → 중단/재개 안전)
async function reviewAnalyzePage() {
  if (REVIEW_BATCH && REVIEW_BATCH.running) return;
  const page = reviewFiltered().list.slice(REVIEW_PAGE * REVIEW_PAGE_SIZE, (REVIEW_PAGE + 1) * REVIEW_PAGE_SIZE);
  const targets = page.filter(h => !reviewAnalyzed(h));
  if (!targets.length) { toast('이 페이지에 미분석 핸드가 없습니다'); return; }
  REVIEW_BATCH = {running: true, done: 0, total: targets.length, stop: false, currentId: null};
  renderMain();
  for (const h of targets) {
    if (REVIEW_BATCH.stop || SEL !== -1) break;
    REVIEW_BATCH.currentId = h.hand_id;
    updateBatchProgress();
    await analyzeHand(h.hand_id);                 // 스트리밍 + 서버 저장
    const c = AI_CACHE[h.hand_id];
    if (c && c.status === 'done') h.analysis = c.text;   // 로컬도 분석됨 표시(필터 일관성)
    REVIEW_BATCH.done++;
    updateBatchProgress();
  }
  const stopped = REVIEW_BATCH.stop;
  REVIEW_BATCH = null;
  if (SEL === -1) renderMain();
  toast(stopped ? '분석 중단됨' : '페이지 분석 완료');
}

// --- AI 분석 (스트리밍) ---
const AI_CACHE = {};  // hand_id -> {status: 'loading'|'streaming'|'done'|'error', text, backend}

// 분석 텍스트에서 전체 평가 이모지 추출
// 1순위: 총평의 "전체 평가: [X]" / 2순위: 스트리트 평가 중 최악 등급
const VERDICT_EMOJI = {'좋음': '✅', '무난': '🙂', '의문': '🤔', '실수': '❌'};
function verdictEmoji(text) {
  if (!text) return '';
  const overall = text.match(/전체\s*평가\s*[:：]\s*\[?(좋음|무난|의문|실수)\]?/);
  if (overall) return VERDICT_EMOJI[overall[1]];
  const found = [...text.matchAll(/\[(좋음|무난|의문|실수)\]/g)].map(m => m[1]);
  for (const v of ['실수', '의문', '무난', '좋음'])   // 최악 등급 우선
    if (found.includes(v)) return VERDICT_EMOJI[v];
  return '';
}

// 접힌 핸드 줄에 표시할 배지: 분석 완료 시 🤖 + 총평 이모지
function aiFlagHtml(handId) {
  const c = AI_CACHE[handId];
  if (!c) return '';
  // 접힌 줄에서도 진행 상태가 보이게: 분석중 🤖+초록스피너 · 에러 🤖⚠️ · 완료 🤖+등급
  if (c.status === 'loading' || c.status === 'streaming') return '🤖<span class="ai-spinner"></span>';
  if (c.status === 'error') return '🤖⚠️';
  if (c.status === 'done') return '🤖' + verdictEmoji(c.text);
  return '';
}

function aiBoxHtml(handId) {
  const c = AI_CACHE[handId];
  if (!c) return `<button onclick="analyzeHand('${handId}')">🤖 AI 분석</button>`;
  if (c.status === 'loading') return `<div class="ai-loading">AI가 핸드를 분석하는 중</div>`;
  if (c.status === 'error') return `
    <div class="ai-error">분석 실패: ${esc(c.text)}</div>
    <button onclick="analyzeHand('${handId}')">다시 시도</button>`;
  const streaming = c.status === 'streaming';
  return `
    <div class="ai-result">${mdToHtml(c.text)}${streaming ? '<span class="ai-cursor">▍</span>' : ''}
      ${streaming ? '' : `<div class="ai-meta">분석: ${esc(c.backend || 'AI')} · <a href="#" style="color:var(--dim)"
        onclick="event.preventDefault(); analyzeHand('${handId}')">다시 분석</a></div>`}
    </div>`;
}

function renderAIBox(handId) {
  const el = document.getElementById('ai-' + handId);
  if (el) el.innerHTML = aiBoxHtml(handId);
  const flag = document.getElementById('flag-' + handId);
  if (flag) flag.innerHTML = aiFlagHtml(handId);  // 접힌 줄 배지도 갱신
}

async function analyzeHand(handId) {
  let hand = (REVIEW_HANDS || []).find(h => h.hand_id === handId) || null;
  for (const t of DATA.tournaments) {
    if (hand) break;
    hand = (t.hands || []).find(h => h.hand_id === handId);
  }
  if (!hand) return;
  AI_CACHE[handId] = {status: 'loading'};
  renderAIBox(handId);
  try {
    const res = await fetch('/api/analyze', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({markdown: hand.markdown, hand_id: handId}),
    });
    if (!res.ok) {
      const data = await res.json();
      AI_CACHE[handId] = {status: 'error', text: data.error || ('HTTP ' + res.status)};
      renderAIBox(handId);
      return;
    }
    const backend = res.headers.get('X-AI-Backend') || 'AI';
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let text = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      text += decoder.decode(value, {stream: true});
      AI_CACHE[handId] = {status: 'streaming', text, backend};
      renderAIBox(handId);
    }
    text += decoder.decode();
    if (!text.trim()) {
      AI_CACHE[handId] = {status: 'error', text: 'AI가 빈 응답을 반환했습니다.'};
    } else {
      AI_CACHE[handId] = {status: 'done', text, backend};
      LEAKS = null;   // 새 분석 반영되도록 리크 캐시 무효화
    }
  } catch (e) {
    AI_CACHE[handId] = {status: 'error', text: String(e)};
  }
  renderAIBox(handId);
}

// 토너먼트 선택 — 핸드는 처음 선택될 때 서버에서 지연 로드
async function selectTourney(i) {
  SEL = i; renderSidebar();
  const t = DATA.tournaments[i];
  if (!t) { $('#mainhead').innerHTML = ''; $('#hands').innerHTML = ''; return; }
  if (!t.hands) {
    $('#mainhead').innerHTML = `<h2>${esc(t.name)}</h2>`;
    $('#hands').innerHTML = '<div class="ai-loading">핸드 불러오는 중</div>';
    const res = await fetch('/api/tournament?id=' + encodeURIComponent(t.id));
    const data = await res.json();
    if (SEL !== i) return;  // 로딩 중 다른 토너먼트로 이동함
    t.hands = data.hands;
    for (const h of t.hands)
      if (h.analysis && !AI_CACHE[h.hand_id])
        AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  }
  renderMain(); $('#main').scrollTop = 0;
}
function toggleAll(open) {
  document.querySelectorAll('.hand').forEach(el => el.classList.toggle('open', open));
}

// 그리드 칸 클릭 → 해당 조합 핸드 목록 (현재 포지션·스택 필터 적용)
const STACK_LABEL = {pf: '<15bb', short: '15-25bb', mid: '25-40bb', deep: '40bb+'};
async function drillCombo(combo) {
  const pos = GRID_POS, stack = GRID_STACK;   // 그리드와 같은 필터로 좁혀서 조회
  const params = ['combo=' + encodeURIComponent(combo)];
  if (pos !== 'all') params.push('pos=' + encodeURIComponent(pos));
  if (stack !== 'all') params.push('stack=' + encodeURIComponent(stack));
  const filterLabel = [pos !== 'all' ? pos : null, stack !== 'all' ? STACK_LABEL[stack] : null]
    .filter(Boolean).join(' · ');
  const name = `🃏 ${combo}${filterLabel ? ' · ' + filterLabel : ''}`;
  SEL = -4; DRILL = null; renderSidebar();
  $('#mainhead').innerHTML = `<button onclick="backToGrid()">← 그리드로</button><h2>${esc(name)}</h2>`;
  $('#hands').innerHTML = '<div class="ai-loading">핸드 불러오는 중</div>';
  const data = await fetch('/api/handsby?' + params.join('&')).then(r => r.json());
  if (SEL !== -4) return;   // 로딩 중 다른 뷰로 이동함
  for (const h of data.hands)
    if (h.analysis && !AI_CACHE[h.hand_id])
      AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  DRILL = {id: 'drill', name, hand_count: data.hands.length, hands: data.hands};
  renderMain(); $('#main').scrollTop = 0;
}
function backToGrid() { STATS_TAB = 'grid'; selectStats(); }

// --- 💰 뱅크롤 (실제 돈 — 칩 EV와 별개 도메인) ---
async function selectBankroll() {
  SEL = -5; renderSidebar();
  $('#mainhead').innerHTML = '<h2>💰 뱅크롤</h2>';
  if (!BANKROLL) $('#hands').innerHTML = '<div class="ai-loading">집계 중</div>';
  const data = await fetch('/api/bankroll').then(r => r.json());
  if (SEL !== -5) return;
  BANKROLL = data; BANK_PAGE = 0; renderBankroll(); $('#main').scrollTop = 0;
}

// 뱅크롤 페이지네이션 (50개씩)
function bankPager(pages) {
  if (pages <= 1) return '';
  const cur = BANK_PAGE;
  const nums = [...new Set([0, pages - 1, cur - 1, cur, cur + 1])].filter(p => p >= 0 && p < pages).sort((a, b) => a - b);
  let html = `<div class="ts-pager"><button ${cur === 0 ? 'disabled' : ''} onclick="bankGotoPage(${cur - 1})">‹ 이전</button>`;
  let prev = -1;
  for (const p of nums) {
    if (prev >= 0 && p - prev > 1) html += `<span class="ts-ellip">…</span>`;
    html += `<button class="${p === cur ? 'primary' : ''}" onclick="bankGotoPage(${p})">${p + 1}</button>`;
    prev = p;
  }
  return html + `<button ${cur === pages - 1 ? 'disabled' : ''} onclick="bankGotoPage(${cur + 1})">다음 ›</button></div>`;
}
function bankGotoPage(p) { BANK_PAGE = p; renderBankroll(); $('#main').scrollTop = 0; }

function bankMoney(v, plus) {
  const c = v >= 0 ? 'var(--green)' : 'var(--red)';
  const s = v > 0 && plus ? '+' : (v < 0 ? '−' : '');
  return `<span style="color:${c}">${s}$${Math.abs(v).toFixed(2)}</span>`;
}
function bankAutoBuyin() {
  const m = ($('#bf-name').value || '').match(/₮\s*([0-9]+(?:\.[0-9]+)?)/);
  if (m) { $('#bf-buyin').value = parseFloat(m[1]); bankRecost(); }
}
function bankRecost() {
  const bi = +$('#bf-buyin').value || 0, en = +$('#bf-entries').value || 1;
  $('#bf-cost').value = (bi * en).toFixed(2);
}

// 바이인 추천 카드
function bankRecommend(b) {
  const r = b.recommendation;
  if (!r) return '';
  const colors = {up: 'var(--green)', stay: 'var(--accent)', caution: '#f0a500', down: 'var(--red)', neutral: 'var(--dim)'};
  const c = colors[r.level] || 'var(--dim)';
  let tierHtml = '';
  if (r.tier_from) {
    if (r.tier_to) {
      tierHtml = `<div style="font-size:26px;font-weight:800;color:${c};letter-spacing:0.03em;margin:8px 0 6px">
        ${esc(r.tier_from)}<span style="font-size:20px;margin:0 10px">→</span>${esc(r.tier_to)}
      </div>`;
    } else {
      tierHtml = `<div style="font-size:26px;font-weight:800;color:${c};letter-spacing:0.03em;margin:8px 0 6px">
        ${esc(r.tier_from)}
      </div>`;
    }
  }
  return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:12px 14px;height:100%;box-sizing:border-box">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <span style="font-size:12px;color:var(--dim)">💡 바이인 추천</span>
      <span style="font-weight:700;color:${c};font-size:13px">${esc(r.title)}</span>
    </div>
    ${tierHtml}
    <div style="font-size:12px;color:var(--dim);margin-bottom:3px">${esc(r.stats || '')}</div>
    <div style="font-size:13px;color:var(--text)">${esc(r.desc || '')}</div>
    ${r.next_step ? `<div style="font-size:12px;color:var(--accent);margin-top:6px">↗ ${esc(r.next_step)}</div>` : ''}
    ${r.warning ? `<div style="font-size:12px;color:var(--gold);margin-top:6px">⚠ ${esc(r.warning)}</div>` : ''}
  </div>`;
}

// 손익 차트 (누적 라인 ↔ 일별 막대 토글) — inline SVG
// ref = '진짜 손익분기선' 값 = 실현 현금손익이 0이 되는 누적손익 레벨. null이면 안 그림.
// 곡선 끝점에서 이 선까지의 세로 간격이 곧 실현 현금손익(= 잔고+출금−입금).
// 곡선이 선 위면 실제 흑자, 아래면 실제 적자.
function bankSparkBody(entries, ref) {
  if (entries.length < 2) return '';
  const ys = entries.map(e => e.cum_pnl);
  const hasRef = ref != null && isFinite(ref);
  const pool = hasRef ? [0, ref, ...ys] : [0, ...ys];
  const mn = Math.min(...pool), mx = Math.max(...pool), W = 800, H = 135, n = ys.length;
  const X = i => (i / (n - 1) * W).toFixed(1);
  const Y = v => (H - (v - mn) / ((mx - mn) || 1) * H).toFixed(1);
  const pts = ys.map((v, i) => `${X(i)},${Y(v)}`).join(' ');
  const last = ys[ys.length - 1];
  const realPnl = hasRef ? last - ref : 0;   // 선까지의 세로 거리 = 실현 현금손익
  const refLine = hasRef
    ? `<line x1="0" y1="${Y(ref)}" x2="${W}" y2="${Y(ref)}" stroke="var(--gold)" stroke-width="1.5"
             stroke-dasharray="6 4" vector-effect="non-scaling-stroke"/>` : '';
  const refLbl = hasRef
    ? ` <span style="color:var(--gold)">· ┄ 진짜 손익분기 · 현재 ${realPnl>=0?'+':'−'}$${Math.abs(realPnl).toFixed(2)}</span>` : '';
  return `<div style="color:var(--dim);font-size:12px;margin-bottom:4px">누적 손익 (${entries[0].date} ~ ${entries[n-1].date})${refLbl}</div>
    <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:135px;display:block">
      <line x1="0" y1="${Y(0)}" x2="${W}" y2="${Y(0)}" stroke="var(--border)" stroke-width="1"/>
      ${refLine}
      <polyline points="${pts}" fill="none" stroke="${last>=0?'var(--green)':'var(--red)'}" stroke-width="2" vector-effect="non-scaling-stroke"/>
    </svg>`;
}
function bankDailyBody(daily) {
  if (!daily || !daily.length) return '';
  window.bankDailyData = daily;
  const W = 800, H = 135, n = daily.length;
  const vals = daily.map(d => d.pnl);
  const mx = Math.max(0, ...vals), mn = Math.min(0, ...vals);
  const range = (mx - mn) || 1, zeroY = H * mx / range, bw = W / n;
  const bars = daily.map((d, i) => {
    const h = Math.abs(d.pnl) / range * H;
    const y = (d.pnl >= 0 ? zeroY - h : zeroY).toFixed(1);
    const col = d.pnl >= 0 ? 'var(--green)' : 'var(--red)';
    return `<rect x="${(i*bw).toFixed(1)}" y="${y}" width="${Math.max(0.6, bw*0.8).toFixed(1)}" height="${Math.max(0.6, h).toFixed(1)}" fill="${col}"/>`;
  }).join('');
  return `<div style="color:var(--dim);font-size:12px;margin-bottom:4px">일별 손익 (${daily[0].date} ~ ${daily[n-1].date})</div>
    <div style="position:relative">
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="width:100%;height:135px;display:block"
           onmousemove="bankDailyHover(event)" onmouseleave="bankDailyHide()">
        <line x1="0" y1="${zeroY.toFixed(1)}" x2="${W}" y2="${zeroY.toFixed(1)}" stroke="var(--border)" stroke-width="1"/>
        ${bars}
      </svg>
      <div id="bankDaily_tip" style="position:absolute;display:none;pointer-events:none;background:var(--panel2);border:1px solid var(--border);border-radius:5px;padding:3px 8px;font-size:11px;white-space:nowrap;z-index:20;box-shadow:0 2px 6px rgba(0,0,0,.3)"></div>
    </div>`;
}
function bankDailyHover(e) {
  const d = window.bankDailyData; if (!d || !d.length) return;
  const r = e.currentTarget.getBoundingClientRect();
  const idx = Math.max(0, Math.min(d.length - 1, Math.floor((e.clientX - r.left) / r.width * d.length)));
  const day = d[idx];
  const tip = document.getElementById('bankDaily_tip'); if (!tip) return;
  tip.innerHTML = `${day.date} · <b style="color:${day.pnl>=0?'var(--green)':'var(--red)'}">${day.pnl>=0?'+':''}$${day.pnl.toFixed(2)}</b>`;
  tip.style.display = 'block';
  const x = e.clientX - r.left, tw = tip.offsetWidth;
  let left = x + 12;
  if (left + tw > r.width) left = x - tw - 12;     // 커서가 오른쪽이면 툴팁을 왼쪽으로 뒤집어 안 가리게
  tip.style.left = Math.max(2, left) + 'px';
  tip.style.top = Math.max(2, e.clientY - r.top - 6) + 'px';
}
function bankDailyHide() { const t = document.getElementById('bankDaily_tip'); if (t) t.style.display = 'none'; }
function bankChartCol(b) {
  // 진짜 손익분기선 — 실현 현금손익이 0이 되는 누적손익 레벨(잔고 입력됐을 때만).
  // 실현 현금손익 = 현재잔고 + 총출금 − 총입금. 토너 외 수입(레이크백 등)을 상수 오프셋으로 보면,
  // 그 손익이 0인 지점 = 마지막 누적손익 − 실현손익 → 곡선~선 간격이 곧 실현 손익이 된다.
  let ref = null;
  if (b.balance && b.balance.balance != null && b.entries.length) {
    const realPnl = b.balance.balance + b.cf_withdraw - b.cf_deposit;
    const lastCum = b.entries[b.entries.length - 1].cum_pnl;
    ref = lastCum - realPnl;
  }
  const cumBody = bankSparkBody(b.entries, ref), dayBody = bankDailyBody(b.daily);
  if (!cumBody && !dayBody) return '';
  const body = BANK_CHART === 'daily' ? (dayBody || cumBody) : (cumBody || dayBody);
  const tbtn = (m, l) => `<button class="${BANK_CHART===m?'primary':''}" onclick="bankSetChart('${m}')" style="font-size:11px;padding:2px 9px">${l}</button>`;
  return `<div style="background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:10px 12px;height:100%;box-sizing:border-box">
    <div style="display:flex;gap:5px;margin-bottom:6px">${tbtn('cum','누적')}${tbtn('daily','일별')}</div>
    ${body}
  </div>`;
}

function bankForm() {
  const fv = BANK_EDIT ? (BANKROLL.entries.find(e => e.id === BANK_EDIT) || {}) : (BANK_PREFILL || {});
  const v = (k, d) => fv[k] !== undefined && fv[k] !== null ? esc(String(fv[k])) : (d || '');
  const inp = (id, ph, val, extra = '') => `<input id="bf-${id}" placeholder="${ph}" value="${val}" ${extra}
     style="background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:6px 8px;font-size:13px">`;
  return `<div style="background:var(--panel);border:1px solid var(--accent);border-radius:9px;padding:14px;margin-bottom:14px">
    <input type="hidden" id="bf-id" value="${BANK_EDIT || ''}">
    <div style="font-weight:600;margin-bottom:10px">${BANK_EDIT ? '✏️ 결과 수정' : '➕ 토너 결과 입력'}</div>
    <div style="display:grid;grid-template-columns:130px 1fr;gap:8px;align-items:center;max-width:640px">
      ${bankKindRows(fv)}
      <label class="small">날짜</label>${inp('date', 'YYYY-MM-DD', v('date'), 'type="date"')}
      <label class="small">토너먼트명</label>${inp('name', '예: ₮5.50 Turbo', v('name'), 'oninput="bankAutoBuyin()"')}
      <label class="small">바이인 ($)</label>${inp('buyin', '0', v('buyin'), 'type="number" step="0.01" oninput="bankRecost()"')}
      <label class="small">바이인/리바이 횟수</label>${inp('entries', '1', v('entries', '1'), 'type="number" min="1" oninput="bankRecost()"')}
      <label class="small">총 비용 ($)</label>${inp('cost', '자동', v('cost'), 'type="number" step="0.01"')}
      <label class="small">상금 ($)</label>${inp('cash', '0', v('cash'), 'type="number" step="0.01"')}
      <label class="small">순위</label>${inp('rank', '선택', v('rank'))}
      <label class="small">메모</label>${inp('memo', '선택', v('memo'))}
    </div>
    <div style="margin-top:12px;display:flex;gap:8px">
      <button class="primary" onclick="bankSave()">${BANK_EDIT ? '저장' : '추가'}</button>
      <button onclick="bankCancel()">취소</button>
    </div>
  </div>`;
}
// 게임 타입 (수동 지정) — 자동 감지가 틀리거나 Day1→Day2처럼 연결된 게임을 직접 묶을 때.
// 세틀/퀄리파잉을 고르면 상위 게임을 골라 그 밑으로 들어감. 손대지 않으면 kind를 안 보냄(자동 감지 유지).
const BANK_KIND_LABEL = {single: '싱글데이 (단일 게임)', satellite: '새틀라이트', qualifier: '퀄리파잉 (Day 1 · 플라이트)'};
function bankTreePos(id) {      // 트리에서 이 엔트리의 현재 (효과) 타입·상위
  for (const n of (BANKROLL.tree || [])) {
    if (n.id === id) return {kind: n.kind_eff || 'single', parent: n.parent_id || ''};
    const c = (n.children || []).find(c => c.id === id);
    if (c) return {kind: c.kind_eff || 'satellite', parent: c.parent_id || n.id};
  }
  return {kind: 'single', parent: ''};
}
function bankKindRows(fv) {
  const pos = fv.id ? bankTreePos(fv.id) : {kind: 'single', parent: ''};
  const manual = !!fv.kind;
  const ents = BANKROLL.entries;
  // 상위 후보: 자기 자신과 (수동) 자손 제외, 이 게임 날짜 이후(−1일)부터 가까운 순 → 검색으로 좁힘
  const desc = new Set(fv.id ? [fv.id] : []);
  for (let grew = true; grew;) {
    grew = false;
    for (const e of ents) if (e.parent_id && desc.has(e.parent_id) && !desc.has(e.id)) { desc.add(e.id); grew = true; }
  }
  const d0 = fv.date ? Date.parse(fv.date) : Date.now();
  const dd = e => (Date.parse(e.date) - d0) / 864e5;
  BANK_PCANDS = ents.filter(e => !desc.has(e.id) && e.date)
    .sort((a, b) => ((dd(a) < -1) - (dd(b) < -1)) || Math.abs(dd(a)) - Math.abs(dd(b)))
    .map(e => ({id: e.id, label: `${e.date} · ${e.name}${e.cash ? ` · $${e.cash.toFixed(2)}` : ''}`}));
  const cur = BANK_PCANDS.find(c => c.id === pos.parent);
  const opt = (val, label, sel) => `<option value="${val}" ${sel ? 'selected' : ''}>${esc(label)}</option>`;
  const kindSel = Object.keys(BANK_KIND_LABEL).map(k => opt(k, BANK_KIND_LABEL[k], k === pos.kind)).join('');
  const st = 'background:var(--panel2);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:6px 8px;font-size:13px';
  const note = manual
    ? `수동 지정됨 · <a href="#" onclick="event.preventDefault();bankKindReset()">↺ 자동 감지로 되돌리기</a>`
    : (fv.id ? '자동 감지된 값 — 바꾸면 수동 지정으로 고정됩니다' : '');
  const hide = pos.kind === 'single' ? 'display:none' : '';
  return `<label class="small">게임 타입</label>
    <div><select id="bf-kind" onchange="bankKindChange()" style="${st}">${kindSel}</select>
      <input type="hidden" id="bf-kind-dirty" value="">
      <span class="small" style="color:var(--dim);margin-left:8px">${note}</span></div>
    <label class="small" id="bf-parent-lbl" style="${hide}">상위 게임</label>
    <div id="bf-parent-box" style="position:relative;${hide}">
      <input type="hidden" id="bf-parent" value="${esc(pos.parent)}">
      <input id="bf-parent-q" autocomplete="off" placeholder="🔍 이름·날짜로 검색 (예: legends 09-28)"
        value="${cur ? esc(cur.label) : ''}" style="${st};width:100%;box-sizing:border-box"
        onfocus="this.select();bankParentList()" oninput="bankParentList()" onkeydown="bankParentKey(event)"
        onblur="setTimeout(bankParentClose, 150)">
      <div id="bf-parent-list" style="display:none;position:absolute;left:0;right:0;top:100%;z-index:30;margin-top:2px;
        max-height:260px;overflow-y:auto;background:var(--panel2);border:1px solid var(--border);border-radius:6px;
        box-shadow:0 4px 12px rgba(0,0,0,.35)"></div>
    </div>`;
}
// 검색 콤보: 공백으로 나눈 단어가 모두 포함된 후보만 (대소문자 무시), 최대 50개
let BANK_PCANDS = [], BANK_PIDX = 0;
function bankParentList() {
  const words = ($('#bf-parent-q').value || '').toLowerCase().split(/\s+/).filter(Boolean);
  const cur = BANK_PCANDS.find(c => c.id === $('#bf-parent').value);
  // 이미 선택된 값이 그대로 들어있으면 필터 없이 전체(가까운 순) 보여줌
  const all = cur && $('#bf-parent-q').value === cur.label;
  const hits = BANK_PCANDS.filter(c => all || words.every(w => c.label.toLowerCase().includes(w))).slice(0, 50);
  BANK_PIDX = 0;
  const box = $('#bf-parent-list');
  box.innerHTML = hits.length
    ? hits.map((c, i) => `<div class="bpc" data-id="${c.id}" onmousedown="event.preventDefault();bankParentPick('${c.id}')"
        style="padding:6px 9px;cursor:pointer;font-size:13px;border-bottom:1px solid var(--border);
        ${c.id === $('#bf-parent').value ? 'color:var(--accent);' : ''}${i === 0 ? 'background:rgba(77,163,255,.15)' : ''}"
        onmouseenter="bankParentHi(${i})">${esc(c.label)}</div>`).join('')
    : `<div style="padding:6px 9px;color:var(--dim);font-size:13px">검색 결과 없음</div>`;
  box.style.display = 'block';
}
function bankParentHi(i) {
  const items = document.querySelectorAll('#bf-parent-list .bpc');
  if (!items.length) return;
  BANK_PIDX = Math.max(0, Math.min(items.length - 1, i));
  items.forEach((el, j) => el.style.background = j === BANK_PIDX ? 'rgba(77,163,255,.15)' : '');
  items[BANK_PIDX].scrollIntoView({block: 'nearest'});
}
function bankParentKey(ev) {
  const box = $('#bf-parent-list');
  if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
    ev.preventDefault();
    if (box.style.display !== 'block') bankParentList();
    else bankParentHi(BANK_PIDX + (ev.key === 'ArrowDown' ? 1 : -1));
  } else if (ev.key === 'Enter') {
    ev.preventDefault();
    const el = document.querySelectorAll('#bf-parent-list .bpc')[BANK_PIDX];
    if (el && box.style.display === 'block') bankParentPick(el.dataset.id);
  } else if (ev.key === 'Escape') bankParentClose();
}
function bankParentPick(id) {
  const c = BANK_PCANDS.find(c => c.id === id); if (!c) return;
  $('#bf-parent').value = id; $('#bf-parent-q').value = c.label;
  $('#bf-kind-dirty').value = '1';
  bankParentClose();
}
function bankParentClose() {
  const box = $('#bf-parent-list'); if (box) box.style.display = 'none';
  // 검색어만 치고 고르지 않았으면 선택된 값의 라벨로 복원
  const q = $('#bf-parent-q'); if (!q) return;
  const cur = BANK_PCANDS.find(c => c.id === $('#bf-parent').value);
  q.value = cur ? cur.label : '';
}
function bankKindChange() {
  const single = $('#bf-kind').value === 'single';
  $('#bf-parent-box').style.display = single ? 'none' : '';
  $('#bf-parent-lbl').style.display = single ? 'none' : '';
  $('#bf-kind-dirty').value = '1';
}
function bankKindReset() { $('#bf-kind-dirty').value = 'auto'; bankSave(); }
function bankShowForm() { BANK_SHOWFORM = true; BANK_EDIT = null; BANK_PREFILL = null; renderBankroll(); }
function bankCancel() { BANK_SHOWFORM = false; BANK_EDIT = null; BANK_PREFILL = null; renderBankroll(); }
function bankEdit(id) { BANK_EDIT = id; BANK_SHOWFORM = true; BANK_PREFILL = null; renderBankroll(); $('#main').scrollTop = 0; }
function bankPrefill(name, date, buyin) {
  BANK_PREFILL = {name, date, buyin}; BANK_SHOWFORM = true; BANK_EDIT = null; renderBankroll(); $('#main').scrollTop = 0;
}
async function bankSave() {
  const num = id => $('#bf-' + id).value === '' ? undefined : +$('#bf-' + id).value;
  const body = {
    id: $('#bf-id').value || undefined,
    date: $('#bf-date').value, name: $('#bf-name').value.trim(),
    buyin: num('buyin'), entries: num('entries'), cost: num('cost'),
    cash: num('cash') || 0, rank: $('#bf-rank').value, memo: $('#bf-memo').value,
  };
  if (!body.name) { toast('토너먼트명을 입력하세요'); return; }
  const kdirty = $('#bf-kind-dirty').value;
  if (kdirty === 'auto') body.kind = 'auto';
  else if (kdirty) {
    body.kind = $('#bf-kind').value;
    body.parent_id = body.kind === 'single' ? '' : $('#bf-parent').value;
    if (body.kind !== 'single' && !body.parent_id) { toast('상위 게임을 선택하세요'); return; }
  }
  const data = await fetch('/api/bankroll/entry', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  }).then(r => r.json());
  BANKROLL = data; BANK_SHOWFORM = false; BANK_EDIT = null; BANK_PREFILL = null;
  renderBankroll(); toast(body.id ? '수정됨' : '추가됨');
}
async function bankConfirm(id) {
  const data = await fetch('/api/bankroll/confirm', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('확인됨');
}
async function bankDelete(id) {
  if (!confirm('이 기록을 삭제할까요?')) return;
  const data = await fetch('/api/bankroll/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('삭제됨');
}
function bankSetFilter(f) { BANK_FILTER = f; BANK_PAGE = 0; renderBankroll(); }
function bankSetChart(m) { BANK_CHART = m; renderBankroll(); }

// 실제 잔고 스냅샷 입력 — 이후 토너 손익은 자동 추적, 리워드 등 차이는 재입력으로 보정
async function bankSetBalance() {
  const cur = (BANKROLL.balance && BANKROLL.balance.balance != null) ? BANKROLL.balance.balance : '';
  const v = prompt('현재 실제 사이트 잔고($)를 입력하세요.\n\n· 이후 토너 손익은 자동으로 더하고 뺍니다.\n· 레이크백·리더보드 등 토너 밖 수입으로 실제와 차이가 나면, 가끔 다시 입력해 보정하세요.', cur);
  if (v === null) return;
  const amount = parseFloat(v);
  if (isNaN(amount)) { toast('숫자를 입력하세요'); return; }
  const data = await fetch('/api/bankroll/balance', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({amount}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('잔고 저장됨');
}

// 입출금 기록 — 토너 손익과 별개 원장. 출금하면 잔고↓ → 바이인 추천도 낮아짐(의도된 동작).
// 출금 method: 'wallet'(내 지갑, $5 수수료) / 'transfer'(유저 송금, 수수료 없음).
async function bankAddCashflow(type, method) {
  const label = type === 'withdraw'
    ? (method === 'transfer' ? '유저 송금' : '지갑 출금') : '입금';
  let hint;
  if (type !== 'withdraw') hint = '입금분만큼 잔고가 늘어 추천 바이인에 반영됩니다.';
  else if (method === 'wallet') hint = '내 지갑으로 이체. 출금액에 네트워크 수수료 $5가 포함됩니다 — 잔고는 입력 금액만큼 줄고, 실수령은 (금액−$5)입니다.';
  else hint = '다른 유저에게 송금. 수수료 없이 입력 금액만큼 잔고가 줄어듭니다(대가는 앱 밖에서 직접 수령).';
  const v = prompt(`${label} 금액($, 계좌에서 빠지는 총액)을 입력하세요.\n\n· 토너 손익(ROI)에는 섞이지 않습니다.\n· ${hint}`, '');
  if (v === null) return;
  const amount = parseFloat(v);
  if (isNaN(amount) || amount <= 0) { toast('0보다 큰 숫자를 입력하세요'); return; }
  const date = prompt('날짜 (YYYY-MM-DD)', new Date().toISOString().slice(0,10));
  if (date === null) return;
  const note = prompt('메모 (선택)', '') || '';
  const data = await fetch('/api/bankroll/cashflow', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({type, method, amount, date, note}),
  }).then(r => r.json());
  if (data.error) { toast(data.error); return; }
  BANKROLL = data; renderBankroll(); toast(`${label} 기록됨`);
}
async function bankDelCashflow(id) {
  if (!confirm('이 입출금 기록을 삭제할까요?')) return;
  const data = await fetch('/api/bankroll/cashflow/delete', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}),
  }).then(r => r.json());
  BANKROLL = data; renderBankroll(); toast('삭제됨');
}

// 뱅크롤 행 클릭 → 그 토너 핸드 보기 (이미 있는 토너 뷰 재사용)
function openTournamentById(tid) {
  const i = DATA.tournaments.findIndex(t => t.id === tid);
  if (i >= 0) selectTourney(i); else toast('연결된 핸드가 없습니다');
}

// 캠페인 트리: 부모(본토너/최고단계)는 토글, 자식(세틀)은 기본 접힘. 각 행은 제 숫자만(#2 후자).
function bankRowTr(e, opts) {
  opts = opts || {};
  const badge = e.tournament_id
    ? `<span style="color:var(--accent);cursor:pointer" onclick="openTournamentById('${e.tournament_id}')">${e.hands}핸드 ›</span>`
    : `<span style="color:var(--gold)" title="연결된 핸드 없음">핸드없음</span>`;
  let name;
  if (opts.nKids) {
    const ks = e.children || [], nq = ks.filter(c => c.kind_eff === 'qualifier').length, ns = ks.length - nq;
    const cnt = [nq ? `Day1 ${nq}` : '', ns ? `세틀 ${ns}` : ''].filter(Boolean).join(' · ');
    name = `<span id="tw-${opts.campId}" onclick="bankToggle('${opts.campId}')" style="cursor:pointer;color:var(--dim);user-select:none;margin-right:5px">▶</span>${esc(e.name)}<span style="color:var(--dim);font-size:11px"> · ${cnt}</span>`;
  } else if (opts.isChild) {
    name = `<span style="color:var(--dim);margin-left:16px">└ ${esc(e.name)}</span>`;
  } else {
    name = esc(e.name);
  }
  const isQ = e.kind_eff === 'qualifier';
  const oc = (e.is_sat || isQ) && e.outcome ? (e.outcome === 'won'
    ? (isQ ? ` <span style="color:var(--green);font-size:11px" title="Day 1 생존 → 다음 날 진출(핸드 판정)">✅ 진출</span>`
           : ` <span style="color:var(--green);font-size:11px" title="세틀에서 살아남아 시트 획득(핸드 판정)">🎟 시트</span>`)
    : ` <span style="color:var(--dim);font-size:11px" title="버스트(핸드 판정)">버스트</span>`) : '';
  const kTag = e.kind_manual ? ` <span style="color:var(--dim);font-size:10px;border:1px solid var(--border);border-radius:4px;padding:0 4px"
      title="게임 타입 수동 지정됨">${{single: '단일', satellite: '세틀', qualifier: 'Day1'}[e.kind_eff] || ''}</span>` : '';
  const extra = `${e.entries>1?` <span style="color:var(--dim)">×${e.entries}</span>`:''}${e.rank?` <span style="color:var(--dim)">${esc(e.rank)}</span>`:''}`;
  const hide = opts.isChild ? 'display:none;background:rgba(0,0,0,.15);' : '';
  const pending = e.confirmed === false;        // 신규 자동등록 → 확인 대기
  // 맨 왼쪽: 상태 표시 전용 칸 (지금은 확인 대기 태그, 향후 다른 뱃지도 여기)
  const statusCell = `<td style="padding:6px 6px;white-space:nowrap">${pending?'<span style="color:var(--gold);font-size:11px">⚠ 확인 필요</span>':''}</td>`;
  const confirmBtn = pending
    ? `<a href="#" title="확인 (버스트 등 상금 입력 불필요)" style="color:var(--green);font-size:14px;margin-right:8px" onclick="event.preventDefault();bankConfirm('${e.id}')">✓</a>`
    : '';
  return `<tr class="${opts.isChild?('kid-'+opts.kidOf):''}" style="border-bottom:1px solid var(--border);${hide}">
    ${statusCell}
    <td style="padding:6px 8px;white-space:nowrap;color:var(--dim)">${esc(e.date || '')}</td>
    <td style="padding:6px 8px">${name}${oc}${kTag}${extra}</td>
    <td style="padding:6px 8px;text-align:right;color:var(--dim)">$${e.cost.toFixed(2)}</td>
    <td style="padding:6px 8px;text-align:right">${e.cash?('$'+e.cash.toFixed(2)):'<span style="color:var(--dim)">-</span>'}</td>
    <td style="padding:6px 8px;text-align:right;font-weight:600">${bankMoney(e.pnl, true)}</td>
    <td style="padding:6px 8px;text-align:right;font-size:12px">${badge}</td>
    <td style="padding:6px 8px;text-align:right;white-space:nowrap">
      ${confirmBtn}
      <a href="#" title="수정" style="font-size:14px;text-decoration:none" onclick="event.preventDefault();bankEdit('${e.id}')">✏️</a>
      <a href="#" title="삭제" style="font-size:14px;text-decoration:none;margin-left:8px" onclick="event.preventDefault();bankDelete('${e.id}')">🗑️</a>
    </td></tr>`;
}
function bankToggle(id) {
  const kids = document.querySelectorAll('.kid-' + id);
  const tw = document.getElementById('tw-' + id);
  const open = tw.textContent === '▼';
  kids.forEach(k => k.style.display = open ? 'none' : 'table-row');
  tw.textContent = open ? '▶' : '▼';
}

function bankSetTab(t) { BANK_TAB = t; renderBankroll(); $('#main').scrollTop = 0; }
function bankCfGotoPage(p) { BANK_CF_PAGE = p; renderBankroll(); $('#main').scrollTop = 0; }

// 뱅크롤 = 한 페이지 안의 2탭: 📊 토너 성적(플레이 결과) / 💸 입출금(실제 돈). 데이터는 /api/bankroll 한 방.
function renderBankroll() {
  const b = BANKROLL;
  const isCash = BANK_TAB === 'cash';
  const headBtn = isCash
    ? `<button class="primary" style="margin-left:auto" onclick="bankAddCashflow('deposit')">＋ 입금</button>`
    : `<button class="primary" style="margin-left:auto" onclick="bankShowForm()">➕ 결과 입력</button>`;
  $('#mainhead').innerHTML = `<h2 style="flex:0 0 auto">💰 뱅크롤</h2>${headBtn}`;
  const tab = (k, l) => `<button class="${BANK_TAB===k?'primary':''}" onclick="bankSetTab('${k}')">${l}</button>`;
  const tabBar = `<div style="display:flex;gap:6px;margin-bottom:14px;border-bottom:1px solid var(--border);padding-bottom:10px">
    ${tab('results','📊 토너 성적')} ${tab('cash','💸 입출금')}</div>`;
  $('#hands').innerHTML = tabBar + (isCash ? bankCashTab(b) : bankResultsTab(b));
}

// ── 탭 1: 토너 성적 (칩→돈 플레이 품질) ─────────────────────────────
function bankResultsTab(b) {
  const cards = `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px">
    ${statCard(bankMoney(b.profit, true), '순손익', `비용 $${b.total_cost} · 상금 $${b.total_cash}`)}
    ${statCard((b.roi>=0?'+':'') + b.roi + '%', 'ROI', '상금/비용')}
    ${statCard(b.itm_pct + '%', 'ITM', `상금권 ${b.n_paid}토너 중`)}
    ${statCard(b.n, '토너 수', `평균 바이인 $${b.avg_buyin}`)}
    ${statCard('$' + b.biggest_cash.toFixed(2), '최고 상금', '단일 토너')}
  </div>`;

  const fBtn = (k, l) => `<button class="${BANK_FILTER===k?'primary':''}" onclick="bankSetFilter('${k}')">${l}</button>`;
  // 페이징 대상: 트리는 루트, 필터는 평면 엔트리 (50개씩)
  const isTree = BANK_FILTER === 'all';
  let items = isTree ? b.tree : b.entries.slice().reverse();
  if (BANK_FILTER === 'unmatched') items = items.filter(e => !e.tournament_id);
  else if (BANK_FILTER === 'itm') items = items.filter(e => e.cash > 0);
  // 평면 뷰도 확인 대기(미확인)를 상단으로 (트리는 백엔드에서 이미 정렬). stable sort라 그 외 순서 유지
  if (!isTree) items = items.slice().sort((a, b) => (a.confirmed===false?0:1) - (b.confirmed===false?0:1));
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / BANK_PAGE_SIZE));
  if (BANK_PAGE >= pages) BANK_PAGE = pages - 1;
  if (BANK_PAGE < 0) BANK_PAGE = 0;
  const pageItems = items.slice(BANK_PAGE * BANK_PAGE_SIZE, (BANK_PAGE + 1) * BANK_PAGE_SIZE);
  const bodyRows = isTree
    ? pageItems.map(n => {
        const kids = n.children || [];
        return bankRowTr(n, kids.length ? {campId: n.id, nKids: kids.length} : {})
          + kids.map(c => bankRowTr(c, {isChild: true, kidOf: n.id})).join('');
      }).join('')
    : pageItems.map(e => bankRowTr(e, {})).join('');
  const countLabel = (isTree ? `${total}개 캠페인` : `${total}건`)
    + (pages > 1 ? ` · ${BANK_PAGE + 1}/${pages}p` : '');
  const table = `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">
    <tr style="color:var(--dim);text-align:left;border-bottom:1px solid var(--border)">
      <th style="padding:6px 6px"></th>
      <th style="padding:6px 8px">날짜</th><th style="padding:6px 8px">토너먼트</th>
      <th style="padding:6px 8px;text-align:right">비용</th><th style="padding:6px 8px;text-align:right">상금</th>
      <th style="padding:6px 8px;text-align:right">손익</th>
      <th style="padding:6px 8px;text-align:right">핸드</th><th></th></tr>
    ${bodyRows}</table></div>`;

  // 역방향: 핸드는 있는데 기록 없는 유료 토너 ($0 프리롤 제외).
  // 티켓 입장(세틀에서 올라옴)은 버스트면 기록 불필요(돈 누락 아님) → 별도 그룹으로, 바이인 0 프리필.
  const ulPaid = b.unlogged.filter(u => u.buyin > 0);
  const ulTicket = ulPaid.filter(u => u.ticket);
  const ulReal = ulPaid.filter(u => !u.ticket);
  const ulRow = (u, prefBuyin) => `
        <div style="display:flex;gap:10px;align-items:center;padding:4px 0;border-bottom:1px solid var(--border)">
          <span style="color:var(--dim);width:90px">${esc(u.start)}</span>
          <span style="flex:1">${esc(u.name)}</span>
          <span style="color:var(--dim)">${u.hands}핸드</span>
          <button onclick="bankPrefill('${esc(u.name).replace(/'/g,'')}','${u.start}',${prefBuyin})">+ 본토너 기록</button>
        </div>`;
  const ulTicketBlock = ulTicket.length ? `
    <details style="margin-top:18px"><summary style="cursor:pointer;color:var(--green)">🎟 티켓 입장 (세틀에서 올라옴 · 버스트면 기록 불필요) ${ulTicket.length}개</summary>
      <div style="margin-top:8px;font-size:12px;color:var(--dim)">바이인 0(티켓)으로 채워집니다. ITM 했던 것만 상금 입력해 누락분 보정하세요.</div>
      <div style="margin-top:6px;font-size:13px">${ulTicket.slice(0,60).map(u => ulRow(u, 0)).join('')}</div></details>` : '';
  const ulRealBlock = ulReal.length ? `
    <details style="margin-top:14px"><summary style="cursor:pointer;color:var(--gold)">⚠ 핸드는 있는데 기록 없는 현금 토너 ${ulReal.length}개 (점검)</summary>
      <div style="margin-top:8px;font-size:13px">${ulReal.slice(0,60).map(u => ulRow(u, u.buyin)).join('')}</div></details>` : '';
  const unlogged = ulTicketBlock + ulRealBlock;

  const unmatchedNote = b.unmatched.length
    ? `<div style="color:var(--dim);font-size:12px;margin:10px 0">매칭 안 된 ${b.unmatched.length}건은 '핸드없음'으로 표시 — 새틀라이트/PLO/미기록 핸드라 정상입니다. 손익 합계엔 모두 포함됩니다.</div>`
    : '';

  const form = BANK_SHOWFORM ? bankForm() : '';
  // 누적 손익 차트(왼쪽 절반) + 바이인 추천(오른쪽 남는 공간) 나란히 배치
  const chartCol = bankChartCol(b), rec = bankRecommend(b);
  const chartRow = (chartCol && rec)
    ? `<div style="display:flex;gap:14px;margin-bottom:14px;align-items:stretch">
         <div style="flex:1;min-width:0;display:flex;flex-direction:column">${chartCol}</div>
         <div style="flex:1;min-width:0;display:flex;flex-direction:column">${rec}</div></div>`
    : (chartCol || rec
        ? `<div style="margin-bottom:14px">${chartCol || rec}</div>`
        : '');
  return cards + chartRow + form
    + `<div style="display:flex;gap:6px;margin-bottom:8px"><span style="color:var(--dim);font-size:13px;align-self:center">보기:</span>
       ${fBtn('all','캠페인 트리')} ${fBtn('itm','ITM만')} ${fBtn('unmatched','미매칭만')}
       <span style="margin-left:auto;color:var(--dim);font-size:12px;align-self:center">${countLabel} 표시</span></div>`
    + unmatchedNote + table + bankPager(pages) + unlogged;
}

// ── 탭 2: 입출금 (실제 돈의 입/출, 잔고) ─────────────────────────────
function bankCashTab(b) {
  const bal = b.balance;
  const balVal = bal ? '$' + bal.balance.toFixed(2) : '<span style="color:var(--dim);font-size:18px">입력 →</span>';
  const cfStr = bal && bal.since_cashflow ? ` · 입출금 ${bal.since_cashflow>=0?'+':''}${bal.since_cashflow.toFixed(2)}` : '';
  // 앵커 시각이 그날 끝(23:59:59)이면 소급/구버전 스냅샷 → 날짜만, 아니면 분까지 표시
  const anchorLbl = bal ? (/23:59:59$/.test(bal.anchor_at||'') ? bal.anchor_date : (bal.anchor_at||'').slice(0,16)) : '';
  const balSub = bal
    ? `${anchorLbl} 기준 ${bal.since_pnl>=0?'+':''}${bal.since_pnl.toFixed(2)}${cfStr}`
    : '실제 사이트 잔고 클릭 입력';
  const balCard = `<div onclick="bankSetBalance()" style="cursor:pointer" title="실제 사이트 잔고 입력/보정 — 토너 손익·입출금은 자동 추적됩니다">${statCard(balVal, '💵 현재 잔고', balSub)}</div>`;
  // 순 회수 = 출금 − 입금 (내 주머니 관점: 출금은 +, 입금은 −). 양수면 넣은 것보다 더 빼낸 것.
  const net = (b.cf_withdraw - b.cf_deposit);

  // ── 총수익 히어로 배너 = 현재잔고 + 총출금 − 총입금 (진짜 현금 손익) ──
  const balNum = bal ? bal.balance : null;
  let hero;
  if (balNum == null) {
    hero = `<div style="border:1px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:14px;background:var(--panel);display:flex;align-items:center;gap:12px;cursor:pointer" onclick="bankSetBalance()">
      <span style="font-size:15px;color:var(--dim)">💰 총수익</span>
      <span style="margin-left:auto;color:var(--accent);font-size:14px">현재 잔고를 입력하면 계산됩니다 →</span></div>`;
  } else {
    const profit = balNum + b.cf_withdraw - b.cf_deposit;
    const pos = profit >= 0;
    const col = pos ? 'var(--green)' : 'var(--red)';
    const warn = (b.cf_deposit === 0)
      ? ` <span style="color:var(--gold);font-size:13px;cursor:help" title="입금 기록이 없어요. 처음부터의 입금을 모두 기록해야 총수익이 정확합니다.">⚠ 입금 기록 필요</span>` : '';
    hero = `<div style="border:1px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:14px;background:var(--panel)">
      <div style="display:flex;align-items:baseline;gap:14px;flex-wrap:wrap">
        <span style="font-size:15px;color:var(--dim)">💰 총수익</span>
        <span style="font-size:30px;font-weight:700;color:${col};font-variant-numeric:tabular-nums">${pos?'+':'−'}$${Math.abs(profit).toFixed(2)}</span>${warn}
      </div>
      <div style="margin-top:6px;color:var(--dim);font-size:13px">현재잔고 $${balNum.toFixed(2)} + 총출금 $${b.cf_withdraw.toFixed(2)} − 총입금 $${b.cf_deposit.toFixed(2)}</div>
    </div>`;
  }

  const cards = `<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:6px">
    ${balCard}
    ${statCard('$' + b.cf_deposit.toFixed(2), '총 입금', '계좌로 넣은 돈')}
    ${statCard('$' + b.cf_withdraw.toFixed(2), '총 출금', '계좌에서 뺀 돈')}
    ${statCard((net>=0?'+':'−') + '$' + Math.abs(net).toFixed(2), '순 회수', '출금 − 입금')}
    ${statCard('$' + (b.cf_fee||0).toFixed(2), '수수료 합계', '지갑 출금 네트워크비')}
  </div>`;
  const balHint = `<div style="color:var(--dim);font-size:12px;margin-bottom:14px">현재 잔고 카드를 눌러 실제 사이트 잔고를 보정할 수 있어요. 토너 손익·입출금은 그 기준 이후 자동 반영됩니다.</div>`;

  const addBtns = `<div style="margin:4px 0 6px;display:flex;gap:6px;flex-wrap:wrap">
      <button class="primary" onclick="bankAddCashflow('deposit')">＋ 입금</button>
      <button onclick="bankAddCashflow('withdraw','wallet')">－ 지갑 출금 ($5 수수료)</button>
      <button onclick="bankAddCashflow('withdraw','transfer')">－ 유저 송금 (수수료 없음)</button>
    </div>`;
  const note = `<div style="font-size:12px;color:var(--dim);margin-bottom:10px">출금하면 위험 자본이 줄어 추정 잔고·추천 바이인이 함께 내려갑니다 — 딴 돈을 지키는 정상 동작입니다. 지갑 출금은 입력 금액에 네트워크 수수료 $5가 포함됩니다(실수령 = 금액−$5).</div>`;

  const cfs = (b.cashflows || []).slice().reverse();
  const pages = Math.max(1, Math.ceil(cfs.length / BANK_PAGE_SIZE));
  if (BANK_CF_PAGE >= pages) BANK_CF_PAGE = pages - 1;
  if (BANK_CF_PAGE < 0) BANK_CF_PAGE = 0;
  const pageItems = cfs.slice(BANK_CF_PAGE * BANK_PAGE_SIZE, (BANK_CF_PAGE + 1) * BANK_PAGE_SIZE);
  const rows = pageItems.map(c => {
    const isW = c.type === 'withdraw';
    const sign = isW ? '−' : '+';
    const col = isW ? 'var(--gold)' : 'var(--green)';
    const kind = isW ? (c.method === 'transfer' ? '유저 송금' : '지갑 출금') : '입금';
    const sub = (isW && c.fee) ? `실수령 $${(c.amount-c.fee).toFixed(2)} · 수수료 $${c.fee.toFixed(2)}` : '';
    return `<tr style="border-bottom:1px solid var(--border)">
        <td style="padding:6px 8px;color:var(--dim)">${esc(c.date||'—')}</td>
        <td style="padding:6px 8px;color:${col}">${kind}</td>
        <td style="padding:6px 8px;text-align:right;color:${col};font-variant-numeric:tabular-nums">${sign}$${c.amount.toFixed(2)}</td>
        <td style="padding:6px 8px;color:var(--dim);font-size:12px">${sub}</td>
        <td style="padding:6px 8px;color:var(--dim)">${esc(c.note||'')}</td>
        <td style="padding:6px 8px;text-align:right"><span style="cursor:pointer;color:var(--dim)" title="삭제" onclick="bankDelCashflow('${c.id}')">🗑</span></td>
      </tr>`;
  }).join('');
  const table = cfs.length
    ? `<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="color:var(--dim);text-align:left;border-bottom:1px solid var(--border)">
          <th style="padding:6px 8px">날짜</th><th style="padding:6px 8px">구분</th>
          <th style="padding:6px 8px;text-align:right">금액</th><th style="padding:6px 8px">실수령/수수료</th>
          <th style="padding:6px 8px">메모</th><th></th></tr>
        ${rows}</table></div>`
    : `<div style="color:var(--dim);font-size:13px;padding:14px 0;text-align:center">아직 입출금 기록이 없습니다. 위 버튼으로 추가하세요.</div>`;
  const pager = pages > 1
    ? `<div style="display:flex;gap:6px;justify-content:center;margin-top:10px">
        <button ${BANK_CF_PAGE<=0?'disabled':''} onclick="bankCfGotoPage(${BANK_CF_PAGE-1})">‹</button>
        <span style="align-self:center;color:var(--dim);font-size:12px">${BANK_CF_PAGE+1}/${pages}p · ${cfs.length}건</span>
        <button ${BANK_CF_PAGE>=pages-1?'disabled':''} onclick="bankCfGotoPage(${BANK_CF_PAGE+1})">›</button></div>`
    : '';

  return hero + cards + balHint + addBtns + note + table + pager;
}

function tourneyMd() {
  // 필터가 켜져 있으면 표시 중인 핸드만 복사/다운로드
  return visibleHands().map(h => h.markdown).join('\n---\n\n');
}
function copyMd() {
  navigator.clipboard.writeText(tourneyMd()).then(() => toast('복사 완료 — AI에게 붙여넣으세요'));
}
function downloadMd() {
  const t = currentTourney();
  const blob = new Blob([tourneyMd()], {type: 'text/markdown'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `tournament_${t.id}.md`;
  a.click();
}

async function applyData(data) {
  DATA = data; SEL = 0;
  REPORT = data.report || null;
  ANALYZED_TOTAL = data.analyzed_total || 0;
  REVIEW_COUNT = data.review_count || 0;
  REVIEW_HANDS = null; STATS = null; LEAKS = null; GRID_CACHE = {}; GRID_POS = 'all'; GRID_STACK = 'all';  // 임포트 후 다시 로드되도록 초기화
  updateReportBtn();
  const params = new URLSearchParams(location.search);
  HIDE_FOLDS = params.has('hidefolds');
  $('#drop').style.display = 'none';
  $('#layout').classList.add('active');
  if (params.has('review')) await selectReview();
  else if (params.has('search')) selectSearch();
  else await selectStats();
}

async function importText(text) {
  const hero = 'Hero';   // CoinPoker는 본인을 항상 'Hero'로 익명화 (전 핸드 100% 확인)
  const res = await fetch('/api/import?hero=' + encodeURIComponent(hero), {
    method: 'POST', body: text,
  });
  const data = await res.json();
  if (data.error) { toast(data.error); return; }
  applyData(data);
  toast(`신규 ${data.added}개 추가 · 기존 ${data.skipped}개 스킵`
    + (data.bankroll_added ? ` · 뱅크롤 ${data.bankroll_added}토너 추가(상금 입력 필요)` : ''));
}

async function loadFiles(files) {
  let text = '';
  for (const f of files) text += await f.text() + '\n';
  importText(text);
}

// 드래그&드롭 / 파일 선택
const drop = $('#drop');
drop.onclick = () => $('#file').click();
$('#btnOpen').onclick = () => $('#file').click();
$('#file').onchange = e => loadFiles(e.target.files);
['dragover','dragenter'].forEach(ev => document.body.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.add('over');
}));
['dragleave','drop'].forEach(ev => document.body.addEventListener(ev, e => {
  e.preventDefault(); drop.classList.remove('over');
}));
document.body.addEventListener('drop', e => loadFiles(e.dataTransfer.files));

// --- 종합 리포트 ---
function updateReportBtn() {
  const b = $('#btnReport');
  b.style.display = '';
  b.textContent = `📊 종합 리포트${ANALYZED_TOTAL ? ` (${ANALYZED_TOTAL}핸드 분석됨)` : ''}`;
}
function openReport() { $('#report-overlay').classList.add('open'); renderReport(); }
function closeReport() { $('#report-overlay').classList.remove('open'); }

function renderReport(streamText) {
  const el = $('#report-body');
  if (REPORT_STREAMING) {
    el.innerHTML = mdToHtml(streamText || '') + '<span class="ai-cursor">▍</span>';
    return;
  }
  if (REPORT) {
    el.innerHTML = mdToHtml(REPORT.text) +
      `<div class="ai-meta">${esc(REPORT.created_at)} 생성 · 분석 핸드 ${REPORT.hand_count}개 기반 · ` +
      `<a href="#" style="color:var(--dim)" onclick="event.preventDefault(); generateReport()">다시 생성</a></div>`;
  } else {
    el.innerHTML = `
      <p style="color:var(--dim)">분석된 핸드들을 모아 반복되는 실수 패턴을 진단합니다.<br>
      현재 분석된 핸드: ${ANALYZED_TOTAL}개 (3개 이상 필요)</p>
      <button class="primary" style="margin-top:12px" onclick="generateReport()">리포트 생성</button>`;
  }
}

async function generateReport() {
  if (REPORT_STREAMING) return;
  REPORT_STREAMING = true;
  renderReport('');
  try {
    const res = await fetch('/api/report', {method: 'POST'});
    if (!res.ok) {
      const data = await res.json();
      REPORT_STREAMING = false;
      $('#report-body').innerHTML = `<div class="ai-error">${esc(data.error || 'HTTP ' + res.status)}</div>`;
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let text = '';
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      text += decoder.decode(value, {stream: true});
      renderReport(text);
    }
    text += decoder.decode();
    REPORT_STREAMING = false;
    if (text.trim()) {
      const now = new Date();
      REPORT = {text, created_at: now.toISOString().slice(0,16).replace('T',' '),
                hand_count: ANALYZED_TOTAL};
    }
    renderReport();
  } catch (e) {
    REPORT_STREAMING = false;
    $('#report-body').innerHTML = `<div class="ai-error">${esc(String(e))}</div>`;
  }
}

// ─────────────────────────────────────────────────────────────
// ⏱ 토너먼트 타이머 (SEL = -6)
// 핸드 DB와 완전히 독립된 도구 — 서버/hands_db.json을 일절 건드리지 않고
// 설정·진행상태를 localStorage에만 저장한다. 새로고침해도 경과시간이 이어지도록
// '경과 ms(base) + 재생 시작 시각(startedAt)'만 저장하고 화면에서 매 틱 계산한다.
// ─────────────────────────────────────────────────────────────
const TM_KEY = 'ahh_timer_v1';

// 블라인드 사다리 — 시작 SB에 곱하는 배수. 레벨당 증가율로 구조 성격이 갈린다.
const TM_LADDERS = {
  slow: [1,1.5,2,2.5,3,4,5,6,8,10,12,15,20,25,30,40,50,60,80,100,120,150,200,250,300,400,500,600,800,1000,1200,1500],
  med:  [1,1.5,2,3,4,5,6,8,10,12,15,20,25,30,40,50,60,80,100,125,150,200,250,300,400,500,600,800,1000,1250,1500,2000],
  fast: [1,1.5,2,3,4,6,8,10,15,20,30,40,60,80,100,150,200,300,400,600,800,1000,1500,2000,3000,4000,6000,8000,10000],
};
const TM_LADDER_LABEL = {slow: '완만 (×1.25)', med: '보통 (×1.33)', fast: '가파름 (×1.5)'};

const TM_PRESETS = {
  hyper:   {label: '⚡ 하이퍼', ladder: 'fast', levelMin: 3,  startStack: 5000,  startSb: 25, anteFrom: 2, breakEvery: 0,  breakMin: 5},
  turbo:   {label: '🚀 터보',   ladder: 'med',  levelMin: 5,  startStack: 10000, startSb: 25, anteFrom: 3, breakEvery: 12, breakMin: 5},
  classic: {label: '🎩 클래식', ladder: 'slow', levelMin: 10, startStack: 20000, startSb: 50, anteFrom: 4, breakEvery: 6,  breakMin: 5},
  deep:    {label: '🛡 딥스택', ladder: 'slow', levelMin: 15, startStack: 30000, startSb: 50, anteFrom: 5, breakEvery: 4,  breakMin: 10},
};

const TM_DEFAULT = {
  preset: 'turbo', ladder: 'med', levelMin: 5, startStack: 10000, startSb: 25,
  anteFrom: 3, breakEvery: 12, breakMin: 5,
  buyin: 10, prizeRate: 90, itmRate: 15, entrants: 100, remaining: 100, mute: false,
};

let TIMER = {cfg: Object.assign({}, TM_DEFAULT), run: {base: 0, startedAt: null, running: false}};
let TM_INT = null;        // 1초 틱 (재생 중에만 돎 — 다른 탭에 있어도 레벨업 알림은 울린다)
let TM_AC = null;         // WebAudio (첫 재생 클릭 때 생성 — 자동재생 정책)
let TM_SEG = -1;          // 마지막으로 렌더한 구간 인덱스 (레벨업 감지용)
let TM_WARNED = -1;       // 1분 전 알림을 이미 울린 구간
let TM_UI = {cfg: false, struct: false};   // 설정/구조 패널 펼침 상태 (재렌더 시 유지용)
const TM_TITLE = document.title;

function tmSave() {
  try { localStorage.setItem(TM_KEY, JSON.stringify(TIMER)); } catch (e) {}
}
function tmLoad() {
  try {
    const raw = localStorage.getItem(TM_KEY);
    if (!raw) return;
    const s = JSON.parse(raw);
    if (s && s.cfg) TIMER.cfg = Object.assign({}, TM_DEFAULT, s.cfg);
    if (s && s.run) TIMER.run = Object.assign({base: 0, startedAt: null, running: false}, s.run);
  } catch (e) {}
}

// 사다리를 곱하면 37.5 같은 값이 나온다 → 실제 토너에서 쓰는 '떨어지는 숫자'로 스냅
const TM_MANTISSA = [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7.5, 8, 10];
function tmNiceSb(v) {
  if (v < 10) return Math.max(1, Math.round(v));
  const dec = Math.pow(10, Math.floor(Math.log10(v)));
  const m = v / dec;
  let best = TM_MANTISSA[0];
  for (const s of TM_MANTISSA) if (Math.abs(s - m) < Math.abs(best - m)) best = s;
  return Math.round(best * dec);
}

// --- 스케줄: 레벨/브레이크를 하나의 구간 배열로 펼친다 (at = 시작 오프셋 ms) ---
function tmSchedule() {
  const c = TIMER.cfg;
  const lad = TM_LADDERS[c.ladder] || TM_LADDERS.med;
  const lvMs = Math.max(1, c.levelMin) * 60000;
  const segs = [];
  let t = 0, prevSb = 0;
  for (let i = 0; i < lad.length; i++) {
    const raw = Math.max(1, c.startSb) * lad[i];
    // 레벨 1은 사용자가 넣은 시작 SB를 그대로 (스냅하면 33 → 30 처럼 바뀌어버린다)
    let sb = i === 0 ? Math.max(1, Math.round(c.startSb)) : tmNiceSb(raw);
    if (sb <= prevSb) sb = Math.max(prevSb + 1, Math.round(raw));   // 항상 증가 보장
    prevSb = sb;
    const bb = sb * 2;
    const ante = (c.anteFrom > 0 && i + 1 >= c.anteFrom) ? bb : 0;   // BB 앤티 방식
    segs.push({kind: 'level', n: i + 1, sb, bb, ante, at: t, ms: lvMs});
    t += lvMs;
    if (c.breakEvery > 0 && c.breakMin > 0 && (i + 1) % c.breakEvery === 0 && i + 1 < lad.length) {
      const bMs = c.breakMin * 60000;
      segs.push({kind: 'break', at: t, ms: bMs});
      t += bMs;
    }
  }
  return segs;
}

function tmElapsed() {
  const r = TIMER.run;
  return r.base + (r.running && r.startedAt ? Date.now() - r.startedAt : 0);
}
// 경과시간을 특정 지점으로 이동 (재생 중이면 기준 시각도 다시 잡는다)
function tmSeek(ms) {
  const r = TIMER.run;
  r.base = Math.max(0, ms);
  if (r.running) r.startedAt = Date.now();
  TM_WARNED = -1;
  tmSave();
}

function tmSegAt(segs, ms) {
  for (let i = segs.length - 1; i >= 0; i--) if (ms >= segs[i].at) return i;
  return 0;
}
function tmFmt(ms) {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
}
function tmNum(n) { return Math.round(n).toLocaleString(); }

// --- 파생 계산 (실제 돈) ---
function tmDerived() {
  const c = TIMER.cfg;
  const entrants = Math.max(1, Math.round(c.entrants));
  const remaining = Math.min(entrants, Math.max(0, Math.round(c.remaining)));
  const pool = c.buyin * entrants * (c.prizeRate / 100);
  const itm = Math.min(entrants, Math.max(1, Math.ceil(entrants * (c.itmRate / 100))));
  return {
    entrants, remaining, pool, itm,
    toBubble: remaining - itm,                                   // 0 이하면 ITM 확정
    avg: remaining > 0 ? (c.startStack * entrants) / remaining : 0,
  };
}

// --- 알림음 ---
function tmBeep(times) {
  if (TIMER.cfg.mute) return;
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return;
    TM_AC = TM_AC || new AC();
    if (TM_AC.state === 'suspended') TM_AC.resume();
    for (let i = 0; i < times; i++) {
      const o = TM_AC.createOscillator(), g = TM_AC.createGain();
      const t0 = TM_AC.currentTime + i * 0.3;
      o.type = 'sine'; o.frequency.value = 880;
      g.gain.setValueAtTime(0.0001, t0);
      g.gain.exponentialRampToValueAtTime(0.3, t0 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.24);
      o.connect(g); g.connect(TM_AC.destination);
      o.start(t0); o.stop(t0 + 0.27);
    }
  } catch (e) {}
}

// --- 조작 ---
function tmToggle() {
  const r = TIMER.run;
  if (r.running) { r.base = tmElapsed(); r.startedAt = null; r.running = false; }
  else { r.startedAt = Date.now(); r.running = true; tmBeep(0); }   // 첫 클릭에서 AudioContext 확보
  tmSave(); tmLoop(); renderSidebar();
  if (SEL === -6) renderTimer();
}
function tmJump(dir) {
  const segs = tmSchedule();
  const i = tmSegAt(segs, tmElapsed());
  if (dir < 0) {
    // 구간 시작 후 3초 넘게 지났으면 '현재 구간 처음으로', 아니면 이전 구간으로
    const inSeg = tmElapsed() - segs[i].at;
    tmSeek(inSeg > 3000 || i === 0 ? segs[i].at : segs[i - 1].at);
  } else {
    tmSeek(i + 1 < segs.length ? segs[i + 1].at : segs[segs.length - 1].at + segs[segs.length - 1].ms);
  }
  TM_SEG = -1;
  if (SEL === -6) renderTimer();
}
function tmReset() {
  if (!confirm('타이머를 레벨 1 처음으로 되돌릴까요? (설정값은 그대로)')) return;
  TIMER.run = {base: 0, startedAt: null, running: false};
  TM_SEG = -1; TM_WARNED = -1;
  tmSave(); tmLoop(); renderSidebar();
  if (SEL === -6) renderTimer();
}
function tmMute() {
  TIMER.cfg.mute = !TIMER.cfg.mute; tmSave();
  if (SEL === -6) renderTimer();
}
function tmBust(d) {
  const c = TIMER.cfg;
  c.remaining = Math.min(Math.round(c.entrants), Math.max(0, Math.round(c.remaining) + d));
  tmSave(); tmRenderStats();
}

// 설정 변경. 구조(레벨 길이·블라인드)를 건드리면 프리셋은 '직접 설정'으로 바뀐다.
const TM_STRUCT_KEYS = ['ladder', 'levelMin', 'startSb', 'anteFrom', 'breakEvery', 'breakMin'];
function tmSet(key, v) {
  const c = TIMER.cfg;
  if (key === 'ladder') { c.ladder = TM_LADDERS[v] ? v : 'med'; }
  else {
    let n = parseFloat(v);
    if (!isFinite(n) || n < 0) n = 0;
    c[key] = n;
    if (key === 'entrants') c.remaining = Math.min(c.remaining, n);
    if (key === 'remaining') c.remaining = Math.min(n, c.entrants);
  }
  if (TM_STRUCT_KEYS.indexOf(key) >= 0) c.preset = 'custom';
  tmSave();
  if (TM_STRUCT_KEYS.indexOf(key) >= 0) { TM_SEG = -1; renderTimer(); }
  else tmRenderStats();
}
function tmApplyPreset(k) {
  const p = TM_PRESETS[k];
  if (!p) return;
  Object.assign(TIMER.cfg, {
    preset: k, ladder: p.ladder, levelMin: p.levelMin, startStack: p.startStack,
    startSb: p.startSb, anteFrom: p.anteFrom, breakEvery: p.breakEvery, breakMin: p.breakMin,
  });
  TM_SEG = -1; tmSave(); renderTimer();
}

// --- 루프 ---
function tmLoop() {
  if (TM_INT) { clearInterval(TM_INT); TM_INT = null; }
  if (TIMER.run.running) TM_INT = setInterval(tmTick, 250);
  else if (document.title !== TM_TITLE) document.title = TM_TITLE;
}

function tmTick() {
  const segs = tmSchedule();
  const el = tmElapsed();
  const end = segs[segs.length - 1].at + segs[segs.length - 1].ms;
  const i = tmSegAt(segs, el);
  const seg = segs[i];
  const left = Math.max(0, seg.at + seg.ms - el);

  // 레벨업 / 1분 전 알림 — 타이머 탭 밖에 있어도 울린다
  if (TIMER.run.running) {
    if (TM_SEG >= 0 && i !== TM_SEG) tmBeep(seg.kind === 'break' ? 3 : 2);
    if (left <= 60000 && left > 0 && TM_WARNED !== i && seg.kind === 'level') { tmBeep(1); TM_WARNED = i; }
    const lbl = seg.kind === 'break' ? '휴식' : 'L' + seg.n;
    document.title = (el >= end ? '종료' : tmFmt(left) + ' ' + lbl) + ' · ' + TM_TITLE;
  }
  const segChanged = i !== TM_SEG;
  TM_SEG = i;

  if (SEL !== -6) return;
  const card = $('#tm-clock-card');
  if (!card) return;
  card.innerHTML = tmClockHtml(segs, i, el, end);
  if (segChanged) { tmRenderStats(); tmMarkStruct(i); }
}

// --- 렌더 ---
function tmClockHtml(segs, i, el, end) {
  const seg = segs[i];
  const done = el >= end;
  const left = Math.max(0, seg.at + seg.ms - el);
  const pct = done ? 100 : Math.min(100, ((el - seg.at) / seg.ms) * 100);
  const brk = seg.kind === 'break';
  // 다음 레벨(브레이크는 건너뛰고 실제 블라인드를 보여준다)
  let nxt = null;
  for (let k = i + 1; k < segs.length; k++) if (segs[k].kind === 'level') { nxt = segs[k]; break; }

  const head = brk
    ? `<div class="tm-lv" style="color:#c084fc">BREAK</div><div class="tm-blinds" style="color:#c084fc">휴식 중</div>`
    : `<div class="tm-lv">LEVEL ${seg.n}</div>
       <div class="tm-blinds">${tmNum(seg.sb)} / ${tmNum(seg.bb)}${seg.ante ? ` <span style="font-size:14px;color:var(--dim)">ante ${tmNum(seg.ante)}</span>` : ''}</div>`;

  const cls = done ? 'done' : brk ? 'brk' : (left <= 60000 ? 'warn' : '');
  const clock = done ? '구조 종료' : tmFmt(left);
  const next = done ? '마지막 레벨까지 모두 지났습니다'
    : (nxt ? `다음 ${brk ? '레벨' : `레벨 ${nxt.n}`} · <b>${tmNum(nxt.sb)} / ${tmNum(nxt.bb)}</b>${nxt.ante ? ` (ante ${tmNum(nxt.ante)})` : ''}` : '마지막 레벨');

  const total = Math.floor(el / 60000);
  return `<div class="tm-top">${head}
      <div style="text-align:right;color:var(--dim);font-size:12px">경과 ${Math.floor(total/60)}시간 ${total%60}분<br>
        <span style="font-size:11px">${TM_PRESETS[TIMER.cfg.preset] ? TM_PRESETS[TIMER.cfg.preset].label : '직접 설정'} · ${TIMER.cfg.levelMin}분/레벨</span></div>
    </div>
    <div class="tm-clock ${cls}">${clock}</div>
    <div class="tm-next">${next}</div>
    <div class="tm-bar"><i class="${brk ? 'brk' : ''}" style="width:${pct.toFixed(1)}%"></i></div>`;
}

function tmStatsHtml() {
  const c = TIMER.cfg, d = tmDerived();
  const segs = tmSchedule();
  const seg = segs[tmSegAt(segs, tmElapsed())];
  const bb = seg.kind === 'level' ? seg.bb : (function () {   // 브레이크 중엔 직전 레벨의 BB
    for (let k = tmSegAt(segs, tmElapsed()); k >= 0; k--) if (segs[k].kind === 'level') return segs[k].bb;
    return c.startSb * 2;
  })();
  const bubble = d.toBubble > 0
    ? `<div class="tm-stat bubble"><span>버블까지</span><b>${d.toBubble}명</b><em>탈락하면 ITM</em></div>`
    : `<div class="tm-stat itm"><span>ITM</span><b>🎉 확정</b><em>${d.remaining}명 남음 · ${d.itm}위까지 인더머니</em></div>`;
  return `
    <div class="tm-stat"><span>상금풀</span><b>$${d.pool.toLocaleString(undefined, {maximumFractionDigits: 2})}</b>
      <em>$${c.buyin} × ${d.entrants}명 × ${c.prizeRate}%</em></div>
    <div class="tm-stat itm"><span>ITM 인원</span><b>${d.itm}위까지</b><em>상위 ${c.itmRate}%</em></div>
    ${bubble}
    <div class="tm-stat"><span>잔여 / 참가자
        <button class="tm-mini" onclick="tmBust(-1)" title="한 명 탈락">−1</button>
        <button class="tm-mini" onclick="tmBust(1)">+1</button></span>
      <b>${d.remaining} / ${d.entrants}</b><em>생존 ${(d.remaining / d.entrants * 100).toFixed(0)}%</em></div>
    <div class="tm-stat"><span>평균 스택</span><b>${tmNum(d.avg)}</b><em>${(d.avg / bb).toFixed(1)}bb · 시작 ${tmNum(c.startStack)}</em></div>`;
}
function tmRenderStats() {
  const el = $('#tm-stats');
  if (el) el.innerHTML = tmStatsHtml();
}

function tmStructHtml(segs, cur) {
  let rows = '';
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    const t = Math.floor(s.at / 60000);
    const at = `${Math.floor(t / 60)}:${String(t % 60).padStart(2, '0')}`;
    rows += s.kind === 'break'
      ? `<tr class="brk ${i === cur ? 'cur' : ''}" id="tmrow${i}"><td>☕ 휴식</td><td colspan="2">${TIMER.cfg.breakMin}분</td><td>—</td><td>${at}</td></tr>`
      : `<tr class="${i === cur ? 'cur' : ''}" id="tmrow${i}"><td>L${s.n}</td><td>${tmNum(s.sb)}</td><td>${tmNum(s.bb)}</td>
         <td>${s.ante ? tmNum(s.ante) : '—'}</td><td>${at}</td></tr>`;
  }
  return `<div style="max-height:340px;overflow-y:auto"><table class="tm-struct">
    <thead><tr><th>레벨</th><th>SB</th><th>BB</th><th>앤티</th><th>시작(경과)</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}
function tmMarkStruct(cur) {
  const box = $('#tm-struct');
  if (!box) return;
  const old = box.querySelector('tr.cur');
  if (old) old.classList.remove('cur');
  const row = $('#tmrow' + cur);
  if (row) row.classList.add('cur');
}

function tmField(key, label, hint, step) {
  return `<label class="tm-f"><span>${label}</span>
    <input type="number" min="0" step="${step || 1}" value="${TIMER.cfg[key]}"
           onchange="tmSet('${key}', this.value)">
    ${hint ? `<em>${hint}</em>` : ''}</label>`;
}

function tmSidebarMeta() {
  if (!TIMER.run.base && !TIMER.run.running) return '블라인드 카운트다운 · 상금풀/ITM';
  const segs = tmSchedule();
  const seg = segs[tmSegAt(segs, tmElapsed())];
  const d = tmDerived();
  return seg.kind === 'break'
    ? `휴식 중 · 잔여 ${d.remaining}명`
    : `L${seg.n} ${tmNum(seg.sb)}/${tmNum(seg.bb)} · 잔여 ${d.remaining}명`;
}

function selectTimer() {
  SEL = -6; renderSidebar();
  $('#mainhead').innerHTML = '<h2>⏱ 토너먼트 타이머</h2>';
  renderTimer();
  $('#main').scrollTop = 0;
}

function renderTimer() {
  if (SEL !== -6) return;
  const c = TIMER.cfg;
  const segs = tmSchedule();
  const el = tmElapsed();
  const end = segs[segs.length - 1].at + segs[segs.length - 1].ms;
  const cur = tmSegAt(segs, el);
  TM_SEG = cur;

  const presetBtns = Object.keys(TM_PRESETS).map(k =>
    `<button class="${c.preset === k ? 'primary' : ''}" onclick="tmApplyPreset('${k}')">${TM_PRESETS[k].label}</button>`
  ).join(' ') + (c.preset === 'custom'
    ? ` <span style="align-self:center;color:var(--gold);font-size:12px">✏️ 직접 설정됨</span>` : '');

  const ladderOpts = Object.keys(TM_LADDERS).map(k =>
    `<option value="${k}" ${c.ladder === k ? 'selected' : ''}>${TM_LADDER_LABEL[k]}</option>`).join('');

  const totalMin = Math.round(end / 60000);

  $('#hands').innerHTML = `<div class="tm-wrap">
    <div class="tm-card" id="tm-clock-card">${tmClockHtml(segs, cur, el, end)}</div>

    <div class="tm-ctrl">
      <button class="primary" onclick="tmToggle()" style="min-width:104px">${TIMER.run.running ? '⏸ 일시정지' : '▶ 시작'}</button>
      <button onclick="tmJump(-1)" title="현재 레벨 처음으로 / 이전 레벨">⏮ 이전</button>
      <button onclick="tmJump(1)">⏭ 다음 레벨</button>
      <button onclick="tmReset()">↺ 리셋</button>
      <span class="spacer"></span>
      <button onclick="tmMute()" title="레벨업 1분 전·레벨업 순간 알림음">${c.mute ? '🔇 알림 꺼짐' : '🔔 알림 켜짐'}</button>
    </div>

    <div class="tm-stats" id="tm-stats">${tmStatsHtml()}</div>

    <details class="tm-panel" ${TM_UI.cfg ? 'open' : ''} ontoggle="TM_UI.cfg=this.open">
      <summary>⚙️ 설정</summary>
      <div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">${presetBtns}</div>
        <div class="tm-form">
          <h4>💵 상금</h4>
          ${tmField('buyin', '바이인 ($)', '수수료 포함 실제 결제액', '0.01')}
          ${tmField('prizeRate', '프라이즈율 (%)', '바이인 중 상금풀로 가는 비율', '0.1')}
          ${tmField('itmRate', 'ITM 비율 (%)', '상위 몇 %가 인더머니인지', '0.1')}
          ${tmField('entrants', '참가자 수', '리엔트리 포함 총 엔트리')}
          ${tmField('remaining', '잔여 인원', '탈락할 때마다 −1 버튼으로')}
          <h4>⏱ 구조</h4>
          ${tmField('levelMin', '레벨당 시간 (분)')}
          ${tmField('startStack', '시작 스택', '평균 스택 계산에 사용')}
          ${tmField('startSb', '시작 SB', `BB = SB × 2 (현재 ${tmNum(c.startSb)}/${tmNum(c.startSb * 2)})`)}
          <label class="tm-f"><span>블라인드 증가율</span>
            <select onchange="tmSet('ladder', this.value)">${ladderOpts}</select>
            <em>레벨당 대략 몇 배씩 오르는지</em></label>
          ${tmField('anteFrom', '앤티 시작 레벨', '0 = 앤티 없음 · BB 앤티 방식')}
          ${tmField('breakEvery', '브레이크 주기 (레벨)', '0 = 브레이크 없음')}
          ${tmField('breakMin', '브레이크 길이 (분)')}
        </div>
      </div>
    </details>

    <details class="tm-panel" ${TM_UI.struct ? 'open' : ''} ontoggle="TM_UI.struct=this.open">
      <summary>📋 블라인드 구조 <span style="color:var(--dim);font-weight:400">— ${segs.filter(s => s.kind === 'level').length}레벨 · 총 ${Math.floor(totalMin / 60)}시간 ${totalMin % 60}분</span></summary>
      <div id="tm-struct">${tmStructHtml(segs, cur)}</div>
    </details>
  </div>`;
}

// ─────────────────────────────────────────────────────────────
// 🎯 문제 풀기 (SEL = -7)
// 서버가 내 리크 스팟을 뽑아 실제 핸드를 결정 지점에서 잘라 보내주고, 액션을 고르면
// AI가 채점한다. 미래 정보(실제 액션·결과)는 채점이 끝난 뒤에만 서버에서 따로 받아온다.
// ─────────────────────────────────────────────────────────────
let QUIZ = {
  mode: 'hand',     // hand(핸드 리뷰, AI 채점) | range(오픈 레인지, 로컬 채점)
  status: 'idle',   // idle | loading | ready | grading | graded | error
  spots: null,      // /api/quiz/spots 응답
  pos: [],          // 히어로 포지션 필터 (빈 배열 = 전체)
  stack: [],        // 시작 스택대 필터 (빈 배열 = 전체)
  street: [],       // 결정 스트릿 필터 (빈 배열 = 전체)
  q: null,          // 현재 문제
  picked: null,     // 고른 선택지
  text: '',         // 채점 텍스트 (스트리밍 중 누적)
  backend: '',
  reveal: null,     // 실제로는 어떻게 쳤는지 (채점 후 로드)
  err: '',
};

function qzSidebarMeta() {
  // 두 모드는 채점 방식이 달라 성적을 합치지 않는다 — 지금 보고 있는 모드 것만 보여준다
  const s = QUIZ.mode === 'range'
    ? (RANGE.state && RANGE.state.scoreboard)
    : (QUIZ.spots && QUIZ.spots.scoreboard);
  if (!s || !s.total) return QUIZ.mode === 'range'
    ? '포지션별 오픈 레인지 드릴' : '내 약점 스팟으로 AI가 문제 출제';
  return `${s.total}문제 풀이 · 정답률 ${s.ok_rate === null ? '—' : s.ok_rate + '%'}`;
}

function selectQuiz() {
  SEL = -7; renderSidebar();
  renderQuiz();
  $('#main').scrollTop = 0;
  if (QUIZ.mode === 'range') { if (!RANGE.state) rgLoadState(); }
  else if (!QUIZ.spots) qzLoadSpots();
}

function qzSetMode(m) {
  QUIZ.mode = m;
  selectQuiz();
}

// 필터를 쿼리스트링으로 (빈 배열이면 파라미터 자체를 안 붙인다)
function qzFilterQS() {
  const p = [];
  if (QUIZ.pos.length) p.push('pos=' + QUIZ.pos.join(','));
  if (QUIZ.stack.length) p.push('stack=' + QUIZ.stack.join(','));
  if (QUIZ.street.length) p.push('street=' + QUIZ.street.join(','));
  return p.length ? '?' + p.join('&') : '';
}

async function qzLoadSpots() {
  try {
    QUIZ.spots = await (await fetch('/api/quiz/spots' + qzFilterQS())).json();
  } catch (e) {
    QUIZ.spots = {spots: [], error: String(e)};
  }
  renderSidebar();
  if (SEL === -7) renderQuiz();
}

// 토글: 빈 문자열이면 '전체'(선택 해제). 같은 걸 다시 누르면 빠진다.
function qzToggleIn(arr, key) {
  if (!key) return [];
  const i = arr.indexOf(key);
  if (i >= 0) { arr.splice(i, 1); return arr; }
  arr.push(key);
  return arr;
}

function qzTogglePos(key)    { QUIZ.pos    = qzToggleIn(QUIZ.pos.slice(), key);    qzAfterToggle(); }
function qzToggleStack(key)  { QUIZ.stack  = qzToggleIn(QUIZ.stack.slice(), key);  qzAfterToggle(); }
function qzToggleStreet(key) { QUIZ.street = qzToggleIn(QUIZ.street.slice(), key); qzAfterToggle(); }

function qzAfterToggle() {
  renderQuiz();      // 토글 반응은 즉시, 스팟 수는 서버 응답 후 갱신
  qzLoadSpots();
}

// 다음 문제. 실제 핸드가 소진된 스팟이면 서버가 generate 지시를 주고, 그때만 AI로 만든다.
async function qzNext() {
  QUIZ.status = 'loading'; QUIZ.q = null; QUIZ.picked = null;
  QUIZ.text = ''; QUIZ.reveal = null; QUIZ.err = '';
  renderQuiz();
  try {
    let data = await (await fetch('/api/quiz/next' + qzFilterQS())).json();
    if (data.generate) {
      QUIZ.status = 'generating'; renderQuiz();
      const res = await fetch('/api/quiz/gen', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data.generate),
      });
      data = await res.json();
    }
    if (data.error) { QUIZ.status = 'error'; QUIZ.err = data.error; }
    else { QUIZ.q = data.question; QUIZ.status = 'ready'; }
  } catch (e) {
    QUIZ.status = 'error'; QUIZ.err = String(e);
  }
  renderQuiz();
}

async function qzAnswer(idx) {
  if (QUIZ.status !== 'ready' || !QUIZ.q) return;
  const q = QUIZ.q;
  QUIZ.picked = q.choices[idx];
  QUIZ.status = 'grading'; QUIZ.text = '';
  renderQuiz();
  try {
    const res = await fetch('/api/quiz/grade', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        situation: q.situation, choice_label: QUIZ.picked.label, choice_id: QUIZ.picked.id,
        hand_id: q.hand_id ?? null, didx: q.didx ?? null,
        spot: q.spot, street: q.street, source: q.source,
      }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      QUIZ.status = 'error'; QUIZ.err = d.error || ('HTTP ' + res.status);
      renderQuiz(); return;
    }
    QUIZ.backend = res.headers.get('X-AI-Backend') || 'AI';
    const reader = res.body.getReader(), dec = new TextDecoder();
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      QUIZ.text += dec.decode(value, {stream: true});
      qzRenderGrade();
    }
    QUIZ.text += dec.decode();
    QUIZ.status = 'graded';
    renderQuiz();
    qzLoadSpots();                     // 성적표/사이드바 갱신
    if (q.source === 'real') qzLoadReveal(q);
  } catch (e) {
    QUIZ.status = 'error'; QUIZ.err = String(e); renderQuiz();
  }
}

// 채점이 끝난 뒤에만 호출 — 그 전에 받아오면 정답이 새어나간다
async function qzLoadReveal(q) {
  try {
    QUIZ.reveal = await (await fetch(
      `/api/quiz/reveal?hand_id=${encodeURIComponent(q.hand_id)}&didx=${q.didx}`)).json();
  } catch (e) { QUIZ.reveal = null; }
  if (SEL === -7) renderQuiz();
}

function qzGrade() {
  const m = QUIZ.text.match(/\[(좋음|무난|의문|실수)\]/);
  return m ? m[1] : null;
}

// 토글 한 줄. 선택된 게 없으면 '전체'가 켜진 것으로 본다.
// o.n === null 이면 개수를 셀 수 없는 축(스트릿) — 흐리게 처리도 툴팁도 하지 않는다.
function qzToggleRow(label, opts, sel, fn) {
  const all = `<span class="qz-tg ${sel.length ? '' : 'on'}" onclick="${fn}('')">전체</span>`;
  const btns = opts.map(o => `
    <span class="qz-tg ${sel.includes(o.key) ? 'on' : ''} ${o.n === 0 ? 'empty' : ''}"
          onclick="${fn}('${o.key}')"${o.n === null ? '' : ` title="${o.n}개 스팟"`}
      >${esc(o.label)}</span>`).join('');
  return `<div class="qz-tgrow"><span class="qz-tglabel">${label}</span>${all}${btns}</div>`;
}

// 토글 행과 같은 자리에 쓰는 드롭다운 버전. 복수 선택이 아니라 '전체 또는 하나'다.
function qzSelectRow(rows) {
  const one = ([label, opts, sel, fn, allLabel]) => `
    <span class="qz-tglabel">${label}</span>
    <select class="qz-sel" onchange="${fn}(this.value)">
      <option value="" ${sel ? '' : 'selected'}>${allLabel || '전체'}</option>
      ${opts.map(o => `<option value="${esc(o.key)}" ${o.key === sel ? 'selected' : ''}
        ${o.n === 0 ? 'disabled' : ''}>${esc(o.label)}${o.n ? ` (${o.n})` : ''}</option>`).join('')}
    </select>`;
  return `<div class="qz-tgrow">${rows.map(one).join('')}</div>`;
}

function qzSpotsHtml() {
  const s = QUIZ.spots;
  if (!s) return '<div class="qz-note">약점 스팟을 찾는 중…</div>';

  const filters = s.options ? `
    ${qzToggleRow('포지션', s.options.positions, QUIZ.pos, 'qzTogglePos')}
    ${qzToggleRow('스택', s.options.stacks, QUIZ.stack, 'qzToggleStack')}
    ${qzToggleRow('스트릿', s.options.streets, QUIZ.street, 'qzToggleStreet')}` : '';

  const warn = s.freq_available ? '' : `
    <div class="qz-note">📊 통계 이탈 스팟(포지션별 오픈/디펜스 빈도)은 <code>python3 gui.py --rebuild</code>
      실행 후에 나타납니다 — 기존 핸드에 <code>pf_faced</code>·<code>stack_bb</code>가 없습니다.</div>`;

  const any = QUIZ.pos.length || QUIZ.stack.length || QUIZ.street.length;
  const status = !s.matched
    ? `<div class="qz-note">${any
        ? '선택한 조합에 해당하는 리크 스팟이 없습니다. 필터를 넓혀 보세요.'
        : `아직 출제할 리크 스팟이 없습니다. 핸드를 더 임포트해 보세요. (현재 ${s.total_hands.toLocaleString()}핸드)`}</div>`
    : `<div class="qz-note">${s.matched}개 리크 스팟에서 출제합니다${
        any ? '' : ' — 위 토글로 범위를 좁힐 수 있습니다'}.${
        QUIZ.street.length && !QUIZ.street.includes('preflop')
          ? ' 프리플랍을 빼면 통계 이탈(📊) 스팟은 프리플랍 지표라 제외됩니다.' : ''}</div>`;
  return `${filters}${warn}${status}`;
}

function qzQuestionHtml() {
  const q = QUIZ.q;
  if (QUIZ.status === 'idle') return '';
  if (QUIZ.status === 'loading') return '<div class="qz-card"><div class="ai-loading">문제를 고르는 중</div></div>';
  if (QUIZ.status === 'generating') return `<div class="qz-card">
    <div class="ai-loading">이 스팟의 실제 핸드를 다 풀었습니다 — AI가 새 문제를 만드는 중</div></div>`;
  if (QUIZ.status === 'error') return `<div class="qz-card">
    <div class="ai-error">${esc(QUIZ.err)}</div>
    <button style="margin-top:10px" onclick="qzNext()">다시 시도</button></div>`;
  if (!q) return '';

  const graded = QUIZ.status === 'graded' || QUIZ.status === 'grading';
  const keys = 'ABCD';
  const choices = q.choices.map((c, i) => `
    <button class="qz-choice ${QUIZ.picked && QUIZ.picked.id === c.id ? 'picked' : ''}"
            ${graded ? 'disabled' : ''} onclick="qzAnswer(${i})">
      <span class="k">${keys[i]}</span> ${esc(c.label)}</button>`).join('');

  return `<div class="qz-card">
    <div class="qz-head">
      <span class="qz-tag">${esc(q.spot_label || '')}</span>
      <span class="qz-tag street">${esc(q.street || '')}</span>
      ${q.source === 'ai' ? '<span class="qz-tag ai">AI 생성 문제</span>' : ''}
      ${q.cached ? '<span class="qz-tag cache">채점 이력 있음</span>' : ''}
    </div>
    <div class="qz-sit qz-md">${mdToHtml(stripHeader(q.situation))}</div>
    <div class="qz-choices">${choices}</div>
  </div>
  <div id="qz-grade">${qzGradeHtml()}</div>`;
}

function qzGradeHtml() {
  if (QUIZ.status !== 'grading' && QUIZ.status !== 'graded') return '';
  if (QUIZ.status === 'grading' && !QUIZ.text) return '<div class="qz-card"><div class="ai-loading">채점 중</div></div>';
  const g = qzGrade();
  const streaming = QUIZ.status === 'grading';
  return `<div class="qz-card">
    <div class="qz-verdict">
      <span>${g ? VERDICT_EMOJI[g] : '🤖'}</span>
      ${g ? `<span class="g qz-g-${g}">${g}</span>` : ''}
      <span style="font-size:13px;font-weight:400;color:var(--dim)">내 선택: ${esc(QUIZ.picked ? QUIZ.picked.label : '')}</span>
    </div>
    <div class="ai-result" style="margin-top:0">${mdToHtml(QUIZ.text)}${streaming ? '<span class="ai-cursor">▍</span>' : ''}</div>
    ${streaming ? '' : qzRevealHtml()}
    ${streaming ? '' : `<div style="margin-top:14px"><button class="primary" onclick="qzNext()">다음 문제 →</button>
      <span style="margin-left:10px;color:var(--dim);font-size:12px">채점: ${esc(QUIZ.backend)}</span></div>`}
  </div>`;
}

function qzRevealHtml() {
  const r = QUIZ.reveal;
  if (!QUIZ.q || QUIZ.q.source !== 'real') return '';
  if (!r) return '<div class="qz-reveal">실제 플레이를 불러오는 중…</div>';
  if (r.error) return '';
  const net = r.net_bb === null || r.net_bb === undefined ? '—'
    : `<span class="net ${r.net_bb >= 0 ? 'win' : 'lose'}">${r.net_bb >= 0 ? '+' : ''}${r.net_bb}bb</span>`;
  const same = QUIZ.picked && QUIZ.picked.label.startsWith(r.actual);
  return `<div class="qz-reveal">
    이 스팟에서 <b>실제로는 ${esc(r.actual)}${r.actual_amount ? ' ' + esc(r.actual_amount) : ''}</b>
    했고, 핸드 최종 손익은 ${net} 였습니다.
    ${same ? '(내 선택과 같음)' : '(내 선택과 다름)'}
    &nbsp;<a href="#" style="color:var(--accent)" onclick="event.preventDefault();qzToggleFull()">핸드 전체 보기</a>
    <div id="qz-full" style="display:none;margin-top:10px" class="qz-md">${mdToHtml(stripHeader(r.full))}</div>
  </div>`;
}

function qzToggleFull() {
  const el = $('#qz-full');
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

function qzScoreHtml() {
  const s = QUIZ.spots && QUIZ.spots.scoreboard;
  if (!s || !s.total) return '';
  const st = s.by_street.map(x =>
    `<div><span>${esc(x.street)}</span><b>${x.n ? Math.round(x.ok / x.n * 100) : 0}%</b>
      <span style="font-size:11px">${x.ok}/${x.n}</span></div>`).join('');
  return `<h3 style="margin:20px 0 10px;font-size:14px">성적</h3>
    <div class="qz-score">
      <div><span>푼 문제</span><b>${s.total}</b></div>
      <div><span>정답률</span><b>${s.ok_rate === null ? '—' : s.ok_rate + '%'}</b>
        <span style="font-size:11px">좋음+무난 기준</span></div>
      ${Object.entries(s.grades).map(([g, n]) =>
        `<div><span>${VERDICT_EMOJI[g]} ${g}</span><b>${n}</b></div>`).join('')}
    </div>
    ${st ? `<div class="qz-score" style="margin-top:10px">${st}</div>` : ''}`;
}

// 스트리밍 중엔 채점 영역만 다시 그린다 (문제 카드까지 새로 그리면 스크롤이 튄다)
function qzRenderGrade() {
  if (SEL !== -7) return;
  const el = $('#qz-grade');
  if (el) el.innerHTML = qzGradeHtml();
}

// ─────────────────────────────────────────────────────────────
// 📐 오픈 레인지 드릴 — 서버가 차트를 들고 있고 채점도 서버에서 즉시 끝난다.
// AI를 전혀 부르지 않으므로 스트리밍도, 캐시도, 생성 폴백도 없다.
// ─────────────────────────────────────────────────────────────
let RANGE = {
  status: 'idle',   // idle | loading | ready | graded | error
  state: null,      // /api/range/state (토글 선택지 + 성적표)
  pos: [], stack: [],
  spot: null,       // 리크 리포트에서 고정한 스팟 {pos, vs, bucket, label} — 있으면 드롭다운 대신 이것만
  q: null,          // 현재 문제
  picked: null,     // 고른 액션 id
  res: null,        // 채점 결과 (로컬이라 한 번에 온다)
  chart: null,      // 차트 그리드 (채점 후 '차트 보기'를 눌렀을 때만 받아온다)
  showChart: false,
  err: '',
  showImport: false,
  // vs: 방어 차트의 오프너 ('' = 오픈 차트). BB는 오픈이 없어 vs가 있어야 한다
  imp: {pos: '', vs: '', stack: '', text: '', source: '', busy: false, err: '', msg: ''},
  // 📊 레인지 차트 뷰어 (문제를 내지 않는 보기 전용 모드). vs '' = 오픈 차트
  view: {pos: '', vs: '', stack: '', chart: null, loading: false, err: '', rate: false, cache: {},
         max: rgvLoadMax(),    // 테이블 인원 (8/7/6맥스) — 화면에서 앞자리를 몇 개 빼고 보여줄지
         mode: 'chart', leaks: null},   // mode: 'chart' | 'leak' (리크 리포트), leaks: 인원별 리포트 캐시
};

function rgFilterQS() {
  const p = ['max=' + RANGE.view.max];        // 테이블 인원 — 차트 탭과 같은 설정을 쓴다
  const s = RANGE.spot;
  if (s)       // 리크 리포트에서 고정한 스팟 — 드롭다운 필터 대신 그 스팟(자리·상대·구간) 하나만
    return '?' + p.concat(['pos=' + encodeURIComponent(s.pos), 'stack=' + s.bucket,
                           'spot=' + encodeURIComponent(s.vs || 'open')]).join('&');
  if (RANGE.pos.length) p.push('pos=' + RANGE.pos.map(encodeURIComponent).join(','));
  if (RANGE.stack.length) p.push('stack=' + RANGE.stack.join(','));
  return '?' + p.join('&');
}

async function rgLoadState() {
  try { RANGE.state = await (await fetch('/api/range/state')).json(); }
  catch (e) { RANGE.state = {error: String(e)}; }
  RANGE.view.leaks = null;        // 차트가 새로 들어왔을 수 있다 — 리크 리포트는 다시 계산 (0.2초)
  renderSidebar();
  if (SEL === -7) renderQuiz();
  if (SEL === -8) renderRangeChart();
}

// 드롭다운은 '전체(빈 값) 또는 하나' — 쿼리스트링 형식은 그대로라 서버는 손댈 게 없다
function rgTogglePos(k)   { RANGE.pos   = k ? [k] : []; renderQuiz(); }
function rgToggleStack(k) { RANGE.stack = k ? [k] : []; renderQuiz(); }

// GTOWizard 등에서 복사한 레인지 텍스트를 (포지션, 스택버킷) 슬롯으로 가져온다 — 내장 차트를 덮어쓴다
function rgToggleImport() { RANGE.showImport = !RANGE.showImport; renderQuiz(); }

function rgImpSet(k, v) {
  const i = RANGE.imp;
  i[k] = v; i.err = ''; i.msg = '';
  // 포지션을 바꿔 상대가 더는 앞자리가 아니게 되면 비운다 (BTN 고른 채 UTG로 바꾸는 경우).
  // 숨은 선택칸에 값이 남아 엉뚱한 슬롯으로 저장되지 않게 하려는 것
  if (k === 'pos' && i.vs && RANGE.state) {
    const order = RANGE.state.import_positions.map(p => p.key);
    if (order.indexOf(rgVsParts(i.vs)[0]) >= order.indexOf(v)) i.vs = '';
  }
  renderQuiz();
}

async function rgImport() {
  const i = RANGE.imp;
  if (!i.pos || !i.stack) { i.err = '포지션과 스택을 먼저 선택하세요.'; renderQuiz(); return; }
  if (!i.text.trim()) { i.err = '레인지 텍스트를 붙여넣으세요.'; renderQuiz(); return; }
  i.busy = true; i.err = ''; i.msg = ''; renderQuiz();
  try {
    const res = await fetch('/api/range/import', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pos: i.pos, vs: i.vs, stack: i.stack, text: i.text, source: i.source}),
    });
    const d = await res.json();
    i.busy = false;
    if (d.error) {
      i.err = d.error + (d.warnings && d.warnings.length ? ` (읽지 못한 토큰: ${d.warnings.join(', ')})` : '');
    } else {
      i.msg = `${rgSpot(d.pos, d.vs)} · ${d.stack} 가져오기 완료 — ${d.n}콤보, 상위 ${d.pct}% (경계 ${d.mix_pct}%)` +
              (d.warnings && d.warnings.length ? `. 못 읽은 토큰: ${d.warnings.join(', ')}` : '');
      i.text = '';
      await rgLoadState();
    }
  } catch (e) { i.busy = false; i.err = String(e); }
  renderQuiz();
}

// 'BB vs BTN' / 'UTG' — 서버 ranges.spot_name과 같은 이름
function rgSpot(pos, vs) {
  const [op, kind] = rgVsParts(vs);
  return op ? `${pos} vs ${op}${kind === 'raise' ? '' : ' ' + RG_KIND[kind]}` : pos;
}
// 상대가 어떻게 들어왔나 — 'UTG' 레이즈 / 'UTG-allin' 올인 / 'SB-limp' 림프 (ranges.vs_parts와 같은 규칙)
const RG_KIND = {raise: '오픈', allin: '올인', limp: '림프'};
function rgVsParts(vs) {
  if (!vs) return ['', null];
  if (vs.endsWith('-allin')) return [vs.slice(0, -6), 'allin'];
  if (vs.endsWith('-limp')) return [vs.slice(0, -5), 'limp'];
  return [vs, 'raise'];
}

async function rgDeleteChart(pos, stack, vs) {
  try {
    await fetch('/api/range/delete-chart', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pos, stack, vs: vs || ''}),
    });
    await rgLoadState();
  } catch (e) {}
}

function rgImportHtml() {
  const st = RANGE.state;
  if (!st || !st.import_positions) return '';
  const i = RANGE.imp;
  const posOpts = st.import_positions.map(p =>
    `<option value="${p.key}" ${i.pos === p.key ? 'selected' : ''}>${esc(p.label)}</option>`).join('');
  // 상대는 나보다 먼저 액션하는 자리만 (BB면 UTG~SB 전부) — 서버 parse_vs와 같은 규칙.
  // 앞자리가 없는 UTG(와 포지션 미선택)에는 상대 선택칸 자체를 띄우지 않는다
  const order = st.import_positions.map(p => p.key);
  const vsList = (st.import_vs || []).filter(p => order.indexOf(rgVsParts(p.key)[0]) < order.indexOf(i.pos));
  const vsOpts = vsList
    .map(p => `<option value="${p.key}" ${i.vs === p.key ? 'selected' : ''}>${esc(p.label)}</option>`).join('');
  const stackOpts = st.stacks.map(s =>
    `<option value="${s.key}" ${i.stack === s.key ? 'selected' : ''}>${esc(s.label)}</option>`).join('');
  const customRows = (st.custom || []).map(c => `
    <div class="rg-imp-row">
      <span>${esc(rgSpot(c.pos, c.vs))} · ${esc(c.stack_label)}</span>
      <span>${c.pct}% (경계 ${c.mix_pct}%) · ${c.n}콤보${c.source ? ' · ' + esc(c.source) : ''}</span>
      <button onclick="rgDeleteChart('${c.pos}','${c.stack}','${c.vs || ''}')">${c.vs ? '삭제' : '삭제 (내장 차트로)'}</button>
    </div>`).join('');
  return `<div class="qz-card rg-imp" style="margin-bottom:14px">
    <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer"
         onclick="rgToggleImport()">
      <b style="font-size:14px">📥 레인지 가져오기 (GTOWizard 등)</b>
      <span style="color:var(--dim);font-size:12px">${RANGE.showImport ? '접기 ▲' : '펼치기 ▼'}</span>
    </div>
    ${RANGE.showImport ? `
      <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
        <select onchange="rgImpSet('pos', this.value)">
          <option value="">포지션</option>${posOpts}
        </select>
        ${i.pos && vsList.length ? `<select onchange="rgImpSet('vs', this.value)"
                 title="오픈을 받은 방어 차트면 오프너를 고르세요">
          <option value="">${i.pos === 'BB' ? '상대 (BB는 필수)' : '오픈 차트 (상대 없음)'}</option>${vsOpts}
        </select>` : ''}
        <select onchange="rgImpSet('stack', this.value)">
          <option value="">스택</option>${stackOpts}
        </select>
        <input type="text" placeholder="출처 메모 (선택, 예: GTOWizard 40bb ICM)" value="${esc(i.source)}"
               oninput="rgImpSet('source', this.value)" style="flex:1;min-width:180px">
      </div>
      <textarea rows="4" placeholder="GTOWizard에서 복사한 레인지 텍스트를 붙여넣으세요 (예: AA:100,AKs:87.5,... 또는 22+, ATs+, KQo)"
                style="width:100%;margin-top:8px;box-sizing:border-box"
                oninput="rgImpSet('text', this.value)">${esc(i.text)}</textarea>
      <div style="display:flex;gap:8px;margin-top:8px;align-items:center">
        <button class="primary" ${i.busy ? 'disabled' : ''} onclick="rgImport()">가져오기</button>
        ${i.busy ? '<span class="ai-loading" style="margin:0">가져오는 중</span>' : ''}
      </div>
      ${i.err ? `<div style="color:var(--red);margin-top:8px;font-size:13px">${esc(i.err)}</div>` : ''}
      ${i.msg ? `<div style="color:var(--green);margin-top:8px;font-size:13px">${esc(i.msg)}</div>` : ''}
      ${customRows ? `<div style="margin-top:14px">
        <div style="font-size:12px;color:var(--dim);margin-bottom:2px">가져온 차트 (내장 차트를 덮어씀)</div>
        ${customRows}
      </div>` : ''}
    ` : ''}
  </div>`;
}

async function rgNext() {
  RANGE.status = 'loading'; RANGE.q = null; RANGE.picked = null;
  RANGE.res = null; RANGE.chart = null; RANGE.showChart = false; RANGE.err = '';
  renderQuiz();
  try {
    const d = await (await fetch('/api/range/next' + rgFilterQS())).json();
    if (d.error) { RANGE.status = 'error'; RANGE.err = d.error; }
    else { RANGE.q = d.question; RANGE.status = 'ready'; }
  } catch (e) { RANGE.status = 'error'; RANGE.err = String(e); }
  renderQuiz();
}

async function rgAnswer(choice) {
  if (RANGE.status !== 'ready' || !RANGE.q) return;
  const q = RANGE.q;
  RANGE.picked = choice;
  try {
    const res = await fetch('/api/range/grade', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pos: q.pos, vs: q.vs || '', stack: q.stack, combo: q.combo, choice}),
    });
    const d = await res.json();
    if (d.error) { RANGE.status = 'error'; RANGE.err = d.error; }
    else {
      RANGE.res = d; RANGE.status = 'graded';
      if (RANGE.state) RANGE.state.scoreboard = d.scoreboard;   // 성적표 즉시 갱신
      renderSidebar();
    }
  } catch (e) { RANGE.status = 'error'; RANGE.err = String(e); }
  renderQuiz();
}

// 차트는 채점이 끝난 뒤에만 — 먼저 보여주면 정답이 그대로 새어나간다
async function rgToggleChart() {
  RANGE.showChart = !RANGE.showChart;
  const q = RANGE.q;
  if (RANGE.showChart && q && !RANGE.chart) {
    renderQuiz();
    try {
      RANGE.chart = await (await fetch(
        `/api/range/chart?pos=${encodeURIComponent(q.pos)}&stack=${q.stack}` +
        (q.vs ? `&vs=${encodeURIComponent(q.vs)}` : ''))).json();
    } catch (e) { RANGE.chart = {error: String(e)}; }
  }
  renderQuiz();
}

function rgCardHtml(card) {
  const g = {s: '♠', h: '♥', d: '♦', c: '♣'}[card[1]];
  const red = card[1] === 'h' || card[1] === 'd';
  return `<div class="rg-c${red ? ' red' : ''}">${card[0]}<span>${g}</span></div>`;
}

function rgQuestionHtml() {
  if (RANGE.status === 'loading') return '<div class="qz-card"><div class="ai-loading">문제 뽑는 중</div></div>';
  if (RANGE.status === 'error') return `<div class="qz-card">
    <div style="color:#ff6b7d;margin-bottom:10px">${esc(RANGE.err)}</div>
    <button class="primary" onclick="rgNext()">다시 시도</button></div>`;
  const q = RANGE.q;
  if (!q) return '';
  const graded = RANGE.status === 'graded';
  // 콜(초록)이 있는 차트만 서버가 call 선택지를 넣어 보낸다 → 3지선다
  const callChoice = q.choices.find(c => c.id === 'call');
  const hasCall = !!callChoice;
  const btn = (id, label, key) => `
    <button class="${RANGE.picked === id ? 'picked ' : ''}${!graded && id === 'open' ? 'primary' : ''}"
            ${graded ? 'disabled' : ''} onclick="rgAnswer('${id}')">
      ${esc(label)}<span class="rg-key">${key}</span></button>`;
  return `<div class="qz-card">
    <div class="qz-head" style="justify-content:center">
      <span class="qz-tag">${esc(q.pos_label)}</span>
      ${q.vs_label ? `<span class="qz-tag">${esc(q.vs_label)}</span>` : ''}
      <span class="qz-tag street">${esc(q.stack_label)}</span>
    </div>
    <div class="rg-ctx">${esc(q.prompt)}<b>${hasCall ? '어떻게 할까요?' : esc(q.verb) + '할까요?'}</b></div>
    <div class="rg-hero">${q.cards.map(rgCardHtml).join('')}</div>
    <div class="rg-combo">${esc(q.combo)}</div>
    <div class="rg-acts">
      ${q.choices.map(c => btn(c.id, c.label, {open: 'O', call: 'C', fold: 'F'}[c.id])).join('')}
    </div>
    ${graded ? rgResultHtml() : ''}
  </div>`;
}

function rgResultHtml() {
  const r = RANGE.res, q = RANGE.q;
  if (!r) return '';
  return `<div class="rg-res">
    <div class="qz-verdict" style="margin-bottom:8px">
      <span>${VERDICT_EMOJI[r.grade] || ''}</span>
      <span class="g qz-g-${r.grade}">${r.grade}</span>
      <span style="font-size:13px;font-weight:500;color:var(--dim)">
        차트 정답: ${r.correct === 'open' ? r.verb : r.correct === 'call' ? r.call_name
          : (r.correct === 'mix' ? '혼합(경계)' : esc(r.fold_name || '폴드'))}</span>
    </div>
    <div class="why">${mdToHtml(r.text)}</div>
    <div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">
      <button class="primary" onclick="rgNext()">다음 문제 →<span class="rg-key">Enter</span></button>
      <button onclick="rgToggleChart()">${RANGE.showChart ? '차트 접기' : `${esc(rgSpot(q.pos, q.vs))} · ${esc(q.stack_label)} 차트 보기`}</button>
    </div>
    ${RANGE.showChart ? rgChartHtml() : ''}
  </div>`;
}

function rgChartHtml() {
  const c = RANGE.chart;
  if (!c) return '<div class="ai-loading" style="margin-top:12px">차트 여는 중</div>';
  if (c.error) return `<div class="qz-note" style="margin-top:12px">${esc(c.error)}</div>`;
  const BG = {open: 'rgba(77,163,255,.55)', call: 'rgba(79,154,92,.55)',
              mix: 'rgba(230,180,80,.35)', fold: 'var(--panel2)'};
  let rows = '<tr><th></th>' + GRID_RANKS.map(r => `<th>${r}</th>`).join('') + '</tr>';
  for (let i = 0; i < 13; i++) {
    let tds = `<th>${GRID_RANKS[i]}</th>`;
    for (let j = 0; j < 13; j++) {
      const combo = comboLabel(i, j);
      const d = c.cells[combo] || {v: 'fold'};
      const cur = RANGE.q && RANGE.q.combo === combo ? ' rg-cur' : '';
      // 내 실전 비율이 차트와 크게 어긋나는 칸에 빨간 테두리 — 여기가 리크다 (판정은 서버 dev)
      const dev = d.dev ? ' rg-dev' : '';
      const t = `${combo} · 차트 ${d.v === 'open' ? c.verb : d.v === 'call' ? '콜'
          : (d.v === 'mix' ? '혼합' : '폴드')}` + (d.call ? ` (콜 ${Math.round(d.call * 100)}%)` : '') +
        (d.rate === undefined ? ' · 실전 기회 없음' : ` · ${rgHeroTip(c, d)}`);
      tds += `<td><div class="hc${i === j ? ' pair' : ''}${cur}${dev}" style="background:${BG[d.v]}" title="${esc(t)}">
        <div class="lab">${combo}</div>${d.rate === undefined ? '' : `<div class="val">${d.rate}%</div>`}</div></td>`;
    }
    rows += `<tr>${tds}</tr>`;
  }
  return `<div style="margin-top:14px">
    <div class="rg-legend">
      ${c.allin ? '' : `<span><i style="background:${BG.open}"></i>${esc(c.verb)} ${c.has_call
        ? Math.round((c.pct - c.call_pct) * 10) / 10 : c.pct}%</span>`}
      ${c.has_call ? `<span><i style="background:${BG.call}"></i>${esc(c.call_name)} ${c.call_pct}%</span>` : ''}
      <span><i style="background:${BG.mix}"></i>경계(혼합) ${c.mix_pct}%</span>
      <span><i style="background:var(--panel2)"></i>${esc(c.fold_name || '폴드')}</span>
      <span><i style="box-shadow:inset 0 0 0 1px #ff6b7d"></i>내 실전 기록이 차트와 어긋난 칸</span>
    </div>
    <div class="grid-wrap"><table class="hgrid">${rows}</table></div>
    <div class="qz-note" style="margin-top:8px">${rgHeroNote(c)}</div>
  </div>`;
}

// 실전 기록 문구 — 오픈 차트는 '오픈 비율', 방어 차트는 '방어(콜+3벳) 비율'이다
function rgHeroTip(c, d) {
  if (c.allin || c.limp) return `실전 ${d.opps}회 중 ${d.opens}회 ${c.verb} (${d.rate}%)`;
  return c.vs
    ? `실전 ${d.opps}회 중 ${d.opens}회 방어 (${d.rate}% · ${c.verb} ${d.raises}회)`
    : `실전 ${d.opps}회 중 ${d.opens}회 오픈 (${d.rate}%)`;
}
function rgHeroNote(c) {
  const op = esc(rgVsParts(c.vs)[0]);
  if (c.allin) return `숫자는 ${op}의 오픈 올인만 받았을 때(콜러 없음) 내가 실제로 콜한 비율입니다.`;
  if (c.limp) return `숫자는 ${op} 혼자 림프했을 때 내가 실제로 레이즈(아이솔)한 비율입니다.`;
  return c.vs
    ? `숫자는 ${op}의 오픈 한 번만 받았을 때(림프·콜러 없음) 내가 실제로 방어(콜+${esc(c.verb)})한 비율입니다.`
    : '숫자는 이 스팟에서 내가 실제로 오픈한 비율입니다 (폴드 투 히어로 상황 기준).';
}

function rgScoreHtml() {
  const s = RANGE.state && RANGE.state.scoreboard;
  if (!s || !s.total) return '';
  const bp = s.by_pos.map(x =>
    `<div><span>${esc(x.pos)}</span><b>${x.n ? Math.round(x.ok / x.n * 100) : 0}%</b>
      <span style="font-size:11px">${x.ok}/${x.n}</span></div>`).join('');
  return `<h3 style="margin:20px 0 10px;font-size:14px">성적</h3>
    <div class="qz-score">
      <div><span>푼 문제</span><b>${s.total}</b></div>
      <div><span>정답률</span><b>${s.ok_rate === null ? '—' : s.ok_rate + '%'}</b>
        <span style="font-size:11px">좋음+무난 기준</span></div>
      ${Object.entries(s.grades).map(([g, n]) =>
        `<div><span>${VERDICT_EMOJI[g]} ${g}</span><b>${n}</b></div>`).join('')}
    </div>
    ${bp ? `<div class="qz-score" style="margin-top:10px">${bp}</div>` : ''}`;
}

// ─────────────────────────────────────────────────────────────
// 📊 레인지 차트 (SEL = -8) — 문제를 내지 않고 차트만 본다 (가져온 슬롯 전용).
// 드릴에서 차트를 채점 전에 보면 정답이 새지만, 여기는 출제 자체가 없으므로 그 제약이 없다.
// 대신 드릴에 풀던 문제가 떠 있는 채로 넘어오면 그 문제의 답이 보이므로 qzSetMode에서 비운다.
// ─────────────────────────────────────────────────────────────
// 스택 슬라이더를 끌면 한 번에 여러 장을 지나가므로, 받은 차트는 캐시해 두고 다시
// 요청하지 않는다. 캐시에 있으면 로딩 표시 없이 그 자리에서 다시 그린다.
function rgvSidebarMeta() {
  const n = ((RANGE.state && RANGE.state.custom) || []).length;
  return n ? `가져온 차트 ${n}장 · 포지션 × 스택` : 'GTO 차트를 가져와서 펼쳐 보기';
}

function selectRangeChart() {
  // 드릴에 문제가 떠 있는 채로 넘어오면 그 문제의 답이 차트에 그대로 보인다 — 비우고 간다
  if (RANGE.status === 'ready') {
    RANGE.status = 'idle'; RANGE.q = null; RANGE.picked = null;
    RANGE.res = null; RANGE.chart = null; RANGE.showChart = false;
  }
  SEL = -8; renderSidebar();
  renderRangeChart();
  $('#main').scrollTop = 0;
  rgLoadState();                 // 밖에서(grab_chart.py) 넣은 차트도 탭을 누르면 바로 뜨게
}

function renderRangeChart() {
  if (SEL !== -8) return;
  const mb = (k, l) => `<button class="${RANGE.view.mode === k ? 'primary' : ''}" onclick="rgvSetMode('${k}')">${l}</button>`;
  $('#mainhead').innerHTML = `<h2 style="flex:0 0 auto">📊 레인지 차트</h2>${mb('chart', '차트')}${mb('leak', '리크 리포트')}`;
  if (RANGE.view.mode === 'leak') renderLeakView(); else renderRangeView();
}

function rgvSetMode(m) { RANGE.view.mode = m; renderRangeChart(); $('#main').scrollTop = 0; }

// ── 리크 리포트 — 가져온 차트 vs 내 실전, '차트와 다르게 친 결정 수' 순 (서버 ranges.leak_report) ──
// 인원 설정은 차트·드릴과 같은 것을 쓴다. 결과는 인원별로 캐시하고, 차트를 새로 가져오면(state 갱신) 비운다.
async function loadLeaks() {
  const v = RANGE.view, key = String(v.max);
  if (v.leaks && v.leaks.key === key) return;
  v.leaks = {key, loading: true};
  renderRangeChart();
  try {
    const d = await (await fetch('/api/range/leaks?max=' + v.max)).json();
    if (v.leaks.key === key) v.leaks = {key, data: d};
  } catch (e) { v.leaks = {key, err: String(e)}; }
  renderRangeChart();
}

function leakSetMax(n) {
  RANGE.view.max = +n;
  try { localStorage.setItem('ahh_rgv_max', String(n)); } catch (e) {}
  renderRangeChart();
}

// 행 → 그 스팟의 차트를 '내 실전 기록 겹쳐 보기'로 연다 (어긋난 칸에 테두리)
function leakOpenChart(i) {
  const r = RANGE.view.leaks.data.rows[i], v = RANGE.view;
  v.mode = 'chart'; v.rate = true;
  // 리포트는 구간(15–25bb 등)으로 비교하므로 그 구간에서 실제로 쓴 차트의 bb로 연다
  v.stack = r.bb === null || r.bb === undefined ? r.stack : String(r.bb);
  rgvGo(r.pos, r.vs || '');
  renderRangeChart();
}

// 조합 칩 → 그 스팟에서 그 조합을 받은 실제 핸드들 (그리드 드릴다운과 같은 핸드 목록 화면)
async function leakOpenHands(i, j) {
  const r = RANGE.view.leaks.data.rows[i], c = r.combos[j];
  const q = new URLSearchParams({pos: r.pos, vs: r.vs || '', bucket: r.bucket, combo: c.combo});
  SEL = -4; DRILL = null; renderSidebar();
  const name = `📊 ${r.label} · ${r.stack_label} · ${c.combo}`;
  $('#mainhead').innerHTML = `<button onclick="leakBack()">← 리크 리포트로</button><h2>${esc(name)}</h2>`;
  $('#hands').innerHTML = '<div class="ai-loading">핸드 불러오는 중</div>';
  const data = await fetch('/api/range/hands?' + q).then(x => x.json());
  if (SEL !== -4) return;
  for (const h of data.hands)
    if (h.analysis && !AI_CACHE[h.hand_id])
      AI_CACHE[h.hand_id] = {status: 'done', text: h.analysis, backend: '저장됨'};
  DRILL = {id: 'drill', name, hand_count: data.hands.length, hands: data.hands,
           back: ['leakBack()', '← 리크 리포트로']};
  renderMain(); $('#main').scrollTop = 0;
}
function leakBack() { RANGE.view.mode = 'leak'; selectRangeChart(); }

// 행의 '🎯 드릴' → 📐 드릴을 그 스팟 하나로 고정해 바로 시작 (해제하면 원래 필터로)
function leakDrill(i) {
  const r = RANGE.view.leaks.data.rows[i];
  RANGE.spot = {pos: r.pos, vs: r.vs || '', bucket: r.bucket, label: `${r.label} · ${r.stack_label} (${r.act_name})`};
  RANGE.status = 'idle'; RANGE.q = null; RANGE.res = null; RANGE.chart = null; RANGE.showChart = false;
  QUIZ.mode = 'range';
  selectQuiz();
  rgNext();
}
function rgUnpin() {
  RANGE.spot = null;
  RANGE.status = 'idle'; RANGE.q = null; RANGE.res = null; RANGE.chart = null; RANGE.showChart = false;
  renderQuiz();
}

function renderLeakView() {
  const v = RANGE.view, st = RANGE.state;
  if (!st) { $('#hands').innerHTML = '<div class="qz-wrap"><div class="ai-loading">불러오는 중</div></div>'; return; }
  if (!v.leaks || v.leaks.key !== String(v.max)) { loadLeaks(); return; }
  const maxSel = `<span class="qz-tglabel">인원</span>
    <select class="qz-sel" onchange="leakSetMax(this.value)">${[8, 7, 6, 5, 4, 3].map(n =>
      `<option value="${n}" ${n === v.max ? 'selected' : ''}>${rgvMaxName(n)}</option>`).join('')}</select>`;
  const L = v.leaks;
  let body;
  if (L.loading) body = '<div class="ai-loading">내 기록과 차트를 맞대어 보는 중</div>';
  else if (L.err) body = `<div class="qz-note">${esc(L.err)}</div>`;
  else if (L.data.personalized === false) body = `<div class="qz-note">DB가 <code>--rebuild</code> 전이라
      오픈 기회를 판정할 수 없습니다 — <code>python3 gui.py --rebuild</code> 후 다시 열어 주세요.</div>`;
  else if (!L.data.rows.length) body = `<div class="qz-note">비교할 스팟이 없습니다 — 가져온 차트가 있는 구간에서
      기회가 ${L.data.min_n}번 이상인 스팟만 싣습니다.</div>`;
  else body = `<table class="lk">
      <tr><th>스팟</th><th>기회</th><th>실제 vs 차트</th><th title="조합마다 |실제 횟수 − 기회 × 차트 빈도|의 합">다르게 친 결정</th><th>대표 조합 (실제 / 기회 · 차트)</th></tr>
      ${L.data.rows.map((r, i) => `<tr onclick="leakOpenChart(${i})" title="${esc(r.chart_label)} 차트에 내 기록을 겹쳐 봅니다">
        <td><b>${esc(r.label)}</b><br><span class="lk-sub">${esc(r.stack_label)} · ${esc(r.act_name)}</span>
          <br><button class="lk-drill" onclick="event.stopPropagation(); leakDrill(${i})"
            title="이 스팟만 📐 드릴로 연습">🎯 드릴</button></td>
        <td class="num">${r.n.toLocaleString()}</td>
        <td class="num">${r.actual}% <span class="lk-sub">/ ${r.expected}%</span><br>
          <span class="lk-diff ${r.diff > 0 ? 'up' : 'down'}">${r.diff > 0 ? '+' : ''}${r.diff}p ${
            Math.abs(r.diff) < 2 ? '' : r.diff > 0 ? '많이 ' + esc(r.act_name) : '적게 ' + esc(r.act_name)}</span></td>
        <td class="num"><b>${Math.round(r.wrong)}</b></td>
        <td>${r.combos.map((c, j) => `<span class="lk-chip ${c.acts / c.opps > c.target / 100 ? 'up' : 'down'}"
            onclick="event.stopPropagation(); leakOpenHands(${i}, ${j})"
            title="이 조합을 받은 실제 핸드 ${c.opps}개 보기">${c.combo} ${c.acts}/${c.opps} · ${c.target}%</span>`).join('')}</td>
      </tr>`).join('')}
    </table>`;
  $('#hands').innerHTML = `<div class="qz-wrap" style="max-width:1100px">
    <div class="qz-tgrow">${maxSel}</div>
    <div class="qz-note">가져온 차트와 내 실전 기록을 스팟마다 맞대어 봅니다. 기대치는 <b>내가 실제로 받은 조합</b>으로
      계산하고, <b>차트와 다르게 친 결정 수</b>(빈도 차이 × 표본) 순으로 정렬합니다. 그 구간에 가져온 차트가 있고
      기회가 ${L.data ? L.data.min_n : 15}번 이상인 스팟만 싣습니다. 행을 누르면 그 차트에 내 기록을 겹쳐 보고,
      조합을 누르면 실제 핸드로 갑니다.</div>
    ${body}</div>`;
}

// 상황(vs)은 '' = 오픈 차트, 그 밖엔 오프너 이름. 슬롯은 (포지션, 상황, 스택)으로 갈린다.
async function rgvOpen(pos, stack, vs) {
  const slots = (RANGE.state && RANGE.state.custom) || [];
  const v = RANGE.view;
  if (vs === undefined) {                          // 상황을 안 골랐으면 보던 상황 유지
    const scen = rgvScenarios(pos);
    if (!scen.length) return;
    vs = scen.includes(v.vs) ? v.vs : scen[0];
  }
  if (stack === undefined) {                       // 포지션·상황만 고른 경우
    const list = rgvStacks(pos, vs);
    if (!list.length) return;
    // 보던 스택을 그대로 유지한다 — 포지션끼리 같은 스택을 비교하는 게 이 화면의 쓸모다.
    // 그 포지션에 그 스택이 없으면 가장 가까운 bb로 붙인다 (맨 처음으로 튀지 않게).
    const cur = String(v.stack);
    const same = list.find(s => String(s.stack) === cur);
    const bbOf = s => (s && s.bb !== null && s.bb !== undefined ? s.bb : -1);
    const curBb = bbOf(slots.find(s => s.pos === v.pos && (s.vs || '') === v.vs &&
                                       String(s.stack) === cur));
    stack = same ? same.stack
      : list.reduce((a, b) =>
          Math.abs(bbOf(b) - curBb) < Math.abs(bbOf(a) - curBb) ? b : a).stack;
  }
  const slot = slots.find(x => x.pos === pos && (x.vs || '') === vs &&
                               String(x.stack) === String(stack));
  // 캐시 키에 그 슬롯의 저장 시각을 넣는다 — 같은 칸을 다시 가져오면 ts가 바뀌어 저절로 미스
  const key = [pos, vs, stack, (slot && slot.ts) || ''].join('|');
  v.pos = pos; v.vs = vs; v.stack = stack; v.err = ''; v.miss = false;
  if (v.cache[key]) { v.chart = v.cache[key]; v.loading = false; renderRangeChart(); return; }
  v.chart = null; v.loading = true;
  renderRangeChart();
  let got, err = '';
  try {
    got = await (await fetch(
      `/api/range/chart?pos=${encodeURIComponent(pos)}&stack=${stack}` +
      (vs ? `&vs=${encodeURIComponent(vs)}` : ''))).json();
  } catch (e) { err = String(e); }
  // 끄는 동안 응답이 뒤섞일 수 있다 — 그 사이 다른 칸으로 옮겼으면 버린다
  if (v.pos !== pos || v.vs !== vs || String(v.stack) !== String(stack)) return;
  // 실패한 칸은 기억해 둔다 — renderRangeView의 '안 받았으면 받아 온다'가 무한 재요청하지 않게
  v.failKey = (!got || got.error || err) ? [pos, vs, stack].join('|') : '';
  if (got && !got.error) v.cache[key] = got;
  v.chart = got; v.err = err; v.loading = false;
  renderRangeChart();
}

// 그 포지션·상황의 스택 슬롯을 **작은 것부터** (슬라이더 축 순서). bb 없는 구형 슬롯은 맨 앞.
function rgvStacks(pos, vs) {
  return ((RANGE.state && RANGE.state.custom) || [])
    .filter(s => s.pos === pos && (s.vs || '') === (vs || ''))
    .slice().sort((a, b) => (a.bb === null ? -1 : a.bb) - (b.bb === null ? -1 : b.bb));
}

// 그 포지션에 가져온 상황들 — 오픈('')이 먼저, 그다음 오프너 (서버 custom_slots가 이미 그 순서다)
function rgvScenarios(pos) {
  const out = [];
  for (const s of (RANGE.state && RANGE.state.custom) || [])
    if (s.pos === pos && !out.includes(s.vs || '')) out.push(s.vs || '');
  return out;
}

// 슬라이더 축 = 가져온 **모든** 차트의 스택을 합친 것 (작은 것부터). 지금 상황에 없는 스택도
// 흐린 눈금으로 남겨 고를 수 있게 한다 — 한 장뿐인 BB 방어 차트에도 슬라이더가 생기고, 그 자리에
// 무엇을 더 캡처해야 하는지가 축에서 바로 보인다. 축이 포지션마다 같아서 눈금 위치도 흔들리지 않는다.
function rgvAxis() {
  const seen = new Map();
  for (const s of (RANGE.state && RANGE.state.custom) || [])
    if (!seen.has(String(s.stack)))
      seen.set(String(s.stack), {stack: s.stack, bb: s.bb, stack_label: s.stack_label});
  return [...seen.values()].sort((a, b) => (a.bb === null ? -1 : a.bb) - (b.bb === null ? -1 : b.bb));
}

function rgvSlide(i) {
  const v = RANGE.view, axis = rgvAxis();
  const a = axis[Math.max(0, Math.min(axis.length - 1, +i))];
  if (!a || String(a.stack) === String(v.stack)) return;
  if (rgvStacks(v.pos, v.vs).some(s => String(s.stack) === String(a.stack))) {
    rgvOpen(v.pos, a.stack, v.vs); return;
  }
  // 이 상황엔 없는 스택 — 빈 칸으로 보여준다 (넣는 커맨드와 함께)
  v.stack = a.stack; v.chart = null; v.loading = false; v.err = ''; v.miss = true;
  renderRangeChart();
}

// ── 테이블 인원 (8/7/6맥스, 파이널 테이블용 5/4/3명) ──
// 차트는 전부 8맥스 이름으로 저장돼 있다. 오픈 레인지를 정하는 건 '뒤에 남은 인원 수'라 인원이 줄면
// **앞자리부터 하나씩 빠질 뿐** 나머지 자리의 차트는 그대로 맞는다 (7맥스 UTG = 8맥스 UTG1,
// 6맥스 UTG = 8맥스 LJ — ranges._pos_8max와 같은 규칙). 그래서 데이터는 손대지 않고 카드만 줄인다.
// 고른 값은 이 브라우저에만 기억한다 (보기 설정일 뿐이라 DB·클라우드에 넣지 않는다).
// 키는 상수로 빼지 않는다 — rgvLoadMax는 RANGE를 만들 때(이 줄보다 먼저) 불려서 const면 아직 없다.
// 고른 적이 없으면 7맥스 — 주로 치는 온라인 테이블이 7맥스라서다 (차트 데이터는 8맥스 이름 그대로)
function rgvLoadMax() {
  try { const n = +localStorage.getItem('ahh_rgv_max'); return [3, 4, 5, 6, 7, 8].includes(n) ? n : 7; }
  catch (e) { return 7; }
}
function rgvSeats() {
  const all = RANGE.state.import_positions.map(p => p.key);   // UTG UTG1 LJ HJ CO BTN SB BB
  return all.slice(all.length - RANGE.view.max);
}
// 6명 이상은 'N맥스'(테이블 포맷), 5명 이하는 'N명'(파이널 테이블 등에서 인원이 줄어든 상태)
function rgvMaxName(n) { return n >= 6 ? `${n}맥스` : `${n}명`; }

function rgvSetMax(n) {
  RANGE.view.max = n;
  try { localStorage.setItem('ahh_rgv_max', String(n)); } catch (e) {}
  renderRangeChart();     // 지금 자리·오프너가 빠졌으면 renderRangeView가 첫 자리로 옮긴다
}

// ── 액션 카드 바 (GTO 툴처럼 위에서 액션을 눌러 트리를 따라간다) ──
// 상태는 여전히 (pos, vs, stack) 하나다 — 카드 바는 그걸 '앞자리들이 무엇을 했나'로 펼쳐 보여줄 뿐.
//   vs 없음 = pos 앞이 전부 폴드 → pos의 오픈 차트
//   vs 있음 = vs가 레이즈, 그 사이는 폴드 → pos의 방어 차트
// 데이터는 오픈과 '오픈 한 번에 대한 방어'까지라, 3벳·콜 이후(멀티웨이)로 가는 버튼은 막아 둔다.
// 스택은 슬라이더 값을 그대로 유지한다 — 그 스택에 차트가 없으면 빈 칸과 캡처 커맨드를 보여준다.
function rgvGo(pos, vs) {
  const v = RANGE.view;
  if (rgvStacks(pos, vs).some(s => String(s.stack) === String(v.stack))) { rgvOpen(pos, v.stack, vs); return; }
  v.pos = pos; v.vs = vs; v.chart = null; v.loading = false; v.err = ''; v.miss = true;
  renderRangeChart();
}

function rgvBarHtml(c) {
  const v = RANGE.view, st = RANGE.state;
  const order = rgvSeats();                                    // 인원에 맞춰 앞자리를 뺀 자리들
  const last = order.length - 1;
  const [opName, opKind] = rgVsParts(v.vs);
  const opAllin = opKind === 'allin', opLimp = opKind === 'limp';
  const cur = order.indexOf(v.pos), op = opName ? order.indexOf(opName) : -1;
  const has = (p, vs) => rgvStacks(p, vs).some(s => String(s.stack) === String(v.stack));
  // 그 자리의 오픈 차트(이 스택)에 올인이 있나 — 있어야 '올인'으로 여는 갈래가 존재한다
  const jams = p => rgvStacks(p, '').some(s => String(s.stack) === String(v.stack) && s.has_jam && s.jam_pct > 0.05);
  const go = (p, vs) => `rgvGo('${p}','${vs}')`;
  const NA = '아직 지원하지 않는 상황 (3벳·콜 이후 / 멀티웨이)';
  const WALK = 'BB 워크 — 결정할 게 없습니다';
  const chip = (label, cls, {sel = false, click = '', off = '', pct = null} = {}) =>
    `<span class="rgv-chip ${cls}${sel ? ' sel' : ''}${off ? ' off' : ''}"${off ? ` title="${off}"` : ''}${
      click && !off ? ` onclick="event.stopPropagation();${click}"` : ''}>${label}${
      pct === null ? '' : `<b>${pct}%</b>`}</span>`;
  const r1 = x => Math.round(x);
  return `<div class="rgv-bar">${order.map((p, i) => {
    let cls = '', chips = '', target = null, note = '';
    const next = i < last ? order[i + 1] : null;
    // 이 자리가 '여는' 칩들 (레이즈 / 올인). 고르면 다음 자리가 그 오픈을 받는다
    const openChips = (sel, pcts = {}) =>
      chip('레이즈', 'raise', {sel: sel === 'raise', pct: pcts.raise ?? null,
                               click: sel === 'raise' ? '' : go(next, p)})
      + (sel === 'allin' || pcts.jam !== undefined || jams(p)
          ? chip('올인', 'jam', {sel: sel === 'allin', pct: pcts.jam ?? null,
                                click: sel === 'allin' ? '' : go(next, p + '-allin')}) : '');
    if (i < cur) {                                          // 이미 액션한 자리
      cls = 'done';
      const afterOpen = op >= 0 && i > op;                   // 오픈을 받고 폴드한 자리
      target = afterOpen ? [p, v.vs] : [p, ''];              // 누르면 '이 자리의 결정'으로 돌아간다
      if (i === op) {
        chips = openChips(opLimp ? null : opAllin ? 'allin' : 'raise')
              + (opLimp ? chip('림프', 'call', {sel: true}) : '')
              + chip('폴드', 'fold', {click: next && next !== 'BB' ? go(next, '') : '',
                                     off: next === 'BB' ? WALK : ''});
      } else if (afterOpen) {                                // 오픈 뒤의 레이즈·콜 = 3벳·콜드콜 — 데이터 없음
        chips = (opAllin ? chip('콜', 'call', {off: NA}) : chip('3벳', 'raise', {off: NA}))
              + chip('폴드', 'fold', {sel: true});
      } else {
        chips = openChips(null) + chip('폴드', 'fold', {sel: true});
      }
    } else if (i === cur) {                                 // 지금 차례 — 차트 전체의 액션 비율
      cls = 'cur';
      const pct = c ? c.pct : null, jam = c ? c.jam_pct : 0, call = c ? c.call_pct : 0;
      const raise = c ? pct - jam - call : null;
      const foldChip = (click, off) => chip('폴드', 'fold', {pct: c ? r1(100 - pct) : null, click, off});
      if (!v.vs) {
        chips = (next ? openChips(null, {raise: c ? r1(raise) : null,
                                          ...(jam > 0.05 ? {jam: r1(jam)} : {})})
                      : chip('레이즈', 'raise', {off: '마지막 자리'}))
              // SB 림프 → BB 차트로 간다 (다른 자리의 오픈 림프는 MTT 트리에 없다)
              + (call > 0.05 ? chip('림프', 'call', {pct: r1(call),
                  click: p === 'SB' ? go('BB', 'SB-limp') : '', off: p === 'SB' ? '' : NA}) : '')
              + foldChip(next && next !== 'BB' ? go(next, '') : '', next === 'BB' ? WALK : '');
      } else if (opLimp) {                                   // SB 림프를 받은 BB — 레이즈/체크 (그 뒤는 없다)
        chips = chip('레이즈', 'raise', {pct: c ? r1(pct - jam) : null, off: NA})
              + (jam > 0.05 ? chip('올인', 'jam', {pct: r1(jam), off: NA}) : '')
              + chip('체크', 'fold', {pct: c ? r1(100 - pct) : null, off: NA});
      } else if (opAllin) {                                  // 올인을 받았다 — 콜/폴드뿐
        chips = chip('콜', 'call', {pct: c ? r1(call) : null, off: NA})
              + foldChip(next ? go(next, v.vs) : '', next ? '' : '마지막 자리');
      } else {
        chips = chip(c ? c.verb : '3벳', 'raise', {pct: c ? r1(raise) : null, off: NA})
              + (jam > 0.05 ? chip('올인', 'jam', {pct: r1(jam), off: NA}) : '')
              + chip('콜', 'call', {pct: c ? r1(call) : null, off: NA})
              + foldChip(next ? go(next, v.vs) : '', next ? '' : '마지막 자리');
      }
      if (!c) note = '<div class="rgv-cnote">차트 없음</div>';
    } else {                                                // 아직 차례가 안 온 자리 — 누르면 그 사이는 폴드로
      cls = 'later';
      if (v.vs) target = [p, v.vs];
      else if (p !== 'BB') target = [p, ''];
      chips = target ? '' : '<div class="rgv-cnote">워크</div>';
    }
    if (target && !has(target[0], target[1])) { cls += ' none'; if (cls.includes('later')) note = '<div class="rgv-cnote">차트 없음</div>'; }
    return `<div class="rgv-card ${cls}"${target ? ` onclick="${go(target[0], target[1])}"` : ''}
      title="${target ? esc(rgSpot(target[0], target[1])) + (has(target[0], target[1]) ? '' : ' (이 스택 차트 없음)') : ''}">
      <div class="pn">${p}${i === op ? `<span class="op">${RG_KIND[opKind]}</span>` : ''}${
        i === 0 && v.max < 8 && p !== 'BTN' ? `<span class="fmt">${rgvMaxName(v.max)} UTG</span>` : ''}</div>${chips}${note}</div>`;
  }).join('')}</div>`;
}

function rgvToggleRate() { RANGE.view.rate = !RANGE.view.rate; renderRangeChart(); }

// 빨강(밝은 톤)의 이름 — 오픈 차트는 레이즈, 방어 차트는 3벳
function rgvRaiseName(c) { return c.vs ? c.verb : '레이즈'; }

function rgvGridHtml(c) {
  const showRate = RANGE.view.rate;
  let cells = '';
  for (let i = 0; i < 13; i++) {
    for (let j = 0; j < 13; j++) {
      const combo = comboLabel(i, j);
      const d = c.cells[combo] || {v: 'fold', w: 0};
      const w = Math.round((d.w || 0) * 100);
      const jm = Math.round((d.jam || 0) * 100);      // 올인 몫 (레이즈 몫 = w - jm - cl)
      const cl = Math.round((d.call || 0) * 100);     // 콜(림프) 몫 — 빨강 오른쪽에 초록으로
      // 내 실전 비율이 차트와 어긋난 칸 — 겹쳐 보기를 켰을 때만 표시 (판정은 서버 dev)
      const dev = showRate && d.dev;
      const num = showRate
        ? (d.rate === undefined ? '' : d.rate + '%')
        : (w > 0 && w < 100 ? w + '%' : '');
      const act = (jm > 0 || cl > 0)
        ? [`${rgvRaiseName(c)} ${w - jm - cl}%`, jm > 0 ? `올인 ${jm}%` : '', cl > 0 ? `콜 ${cl}%` : '']
            .filter(Boolean).join(' · ')
        : `${w}% ${c.verb}`;
      const t = `${combo} · 차트 ${act}` +
        (d.rate === undefined ? ' · 실전 기회 없음' : ` · ${rgHeroTip(c, d)}`);
      cells += `<div class="rgv-c${i === j ? ' pair' : ''}${dev ? ' dev' : ''}" title="${esc(t)}">
        ${w - cl > 0 ? `<div class="fill" style="width:${w - cl}%"></div>` : ''}
        ${jm > 0 ? `<div class="jamfill" style="width:${jm}%"></div>` : ''}
        ${cl > 0 ? `<div class="callfill" style="left:${w - cl}%;width:${cl}%"></div>` : ''}
        <span class="t">${combo}</span>${num ? `<span class="pc">${num}</span>` : ''}</div>`;
    }
  }
  return `<div class="rgv-grid">${cells}</div>`;
}

function renderRangeView() {
  const st = RANGE.state;
  const slots = (st && st.custom) || [];
  if (!st) { $('#hands').innerHTML = '<div class="qz-wrap"><div class="ai-loading">불러오는 중</div></div>'; return; }
  if (!slots.length) {
    $('#hands').innerHTML = `<div class="qz-wrap">
      <div class="qz-card" style="text-align:center;padding:34px 22px">
        <div style="font-size:15px;margin-bottom:6px">아직 가져온 레인지가 없습니다</div>
        <div style="color:var(--dim);font-size:13px;line-height:1.6">
          이 화면은 <b>내가 가져온 차트</b>만 보여줍니다.<br>
          📐 오픈 레인지 탭의 <b>레인지 가져오기</b>로 붙여넣거나,
          화면 캡처로 넣으려면 <code>python3 grab_chart.py UTG 20</code></div>
      </div></div>`;
    return;
  }
  const v = RANGE.view;
  const axis = rgvAxis();
  // 처음 열 때는 GTO 툴처럼 첫 액션(UTG 오픈)부터. 스택은 UTG 차트가 있는 가장 작은 것
  const seats = rgvSeats();
  if (!v.pos) {
    v.pos = seats[0]; v.vs = '';
    v.stack = ((rgvStacks(seats[0], '')[0]) || axis[0]).stack;
  }
  // 인원을 줄여서 지금 자리나 오프너가 테이블에서 빠졌으면 첫 자리의 오픈으로 돌아간다
  if (!seats.includes(v.pos) || (v.vs && !seats.includes(rgVsParts(v.vs)[0]))) {
    rgvGo(seats[0], ''); return;
  }
  if (!axis.some(a => String(a.stack) === String(v.stack))) v.stack = axis[0].stack;
  const mine = rgvStacks(v.pos, v.vs);
  const slotOf = stack => mine.find(m => String(m.stack) === String(stack));
  const idx = axis.findIndex(a => String(a.stack) === String(v.stack));
  const cur = axis[idx], curSlot = slotOf(v.stack);
  // 고른 칸에 차트가 있는데 아직 안 받았으면 받아 온다 (카드·슬라이더 이동은 상태만 바꾸고 여기서 연다)
  const ch = v.chart;
  if (curSlot && !v.loading && v.failKey !== [v.pos, v.vs, v.stack].join('|')
      && !(ch && !ch.error && ch.pos === v.pos && (ch.vs || '') === v.vs
           && String(ch.stack) === String(v.stack))) {
    rgvOpen(v.pos, v.stack, v.vs); return;
  }
  // 스택은 순서가 있는 축이라 슬라이더로 — 끌면 레인지가 변하는 게 그대로 보인다.
  // 눈금 간격은 bb 값이 아니라 **인덱스** 기준이다 (13·15·20…35는 간격이 들쭉날쭉하다).
  const ticks = axis.length < 2 ? '' : axis.map((a, i) => `
    <span style="left:${i / (axis.length - 1) * 100}%"
          class="${i === idx ? 'on' : ''}${slotOf(a.stack) ? '' : ' miss'}" onclick="rgvSlide(${i})"
          ${slotOf(a.stack) ? '' : 'title="이 상황엔 아직 없는 스택"'}
      >${a.bb === null ? esc(a.stack_label) : a.bb}</span>`).join('');
  const maxBtn = n => `<button class="${v.max === n ? 'primary' : ''}" onclick="rgvSetMax(${n})">${rgvMaxName(n)}</button>`;
  const picker = `<div class="rgv-max">${[8, 7, 6, 5, 4, 3].map(maxBtn).join('')}
      <span>${v.max === 3 ? '앞자리 5개를 빼고 봅니다 — BTN부터'
        : v.max < 8 ? `앞자리 ${8 - v.max}개를 빼고 봅니다 — ${rgvMaxName(v.max)} UTG = 8맥스 ${seats[0]} 차트` : ''}</span></div>
    ${v.max <= 5 ? `<div class="qz-note rgv-icm">⚠️ 이 차트들은 <b>칩EV</b> 기준입니다. 이 인원은 주로 <b>파이널 테이블</b>인데,
      거기선 ICM(상금 구조) 때문에 레인지가 훨씬 타이트해야 합니다 — 특히 <b>올인에 대한 콜</b>과 중간 스택의 오픈.
      버블 근처도 마찬가지입니다. 이 화면대로 치면 너무 느슨합니다.</div>` : ''}
    ${rgvBarHtml(curSlot && ch && !ch.error ? ch : null)}
  <div class="qz-tgrow" style="align-items:flex-end">
    ${axis.length < 2 ? `<span class="qz-tglabel">스택</span><b class="rgv-bb">${esc(cur.stack_label)}</b>` : `
      <div class="rgv-stack">
        <div class="rgv-stack-top"><span class="qz-tglabel">스택</span>
          <b class="rgv-bb">${esc(cur.stack_label)}</b></div>
        <input type="range" class="rgv-range" min="0" max="${axis.length - 1}" step="1"
               value="${idx}" oninput="rgvSlide(this.value)"
               title="좌우 방향키로도 이동합니다">
        <div class="rgv-ticks">${ticks}</div>
      </div>`}
    <span style="color:var(--dim);font-size:12px;padding-bottom:2px">${esc(rgSpot(v.pos, v.vs))} ${mine.length}장${
      curSlot && curSlot.ts ? ' · 이 차트 ' + esc(curSlot.ts) : ''}</span>
  </div>`;
  const c = v.chart;
  let body;
  if (!curSlot) body = `<div class="qz-card" style="text-align:center;padding:30px 20px">
      <div style="font-size:15px;margin-bottom:6px">${esc(rgSpot(v.pos, v.vs))} · ${esc(cur.stack_label)} 차트가 아직 없습니다</div>
      <div style="color:var(--dim);font-size:13px">화면 캡처로 넣으려면
        <code>python3 grab_chart.py ${esc(v.pos)} ${cur.bb === null ? esc(String(cur.stack)) : cur.bb}${
          v.vs ? ' --vs ' + esc(v.vs) : ''}</code></div></div>`;
  else if (v.loading) body = '<div class="ai-loading">차트 여는 중</div>';
  else if (v.err || (c && c.error)) body = `<div class="qz-note">${esc(v.err || c.error)}</div>`;
  else if (!c) body = '';
  else body = `
    <div class="rgv-head">
      <h3>${esc(c.label)}</h3>
      <span class="pct">${c.allin ? '콜' : c.has_jam || c.has_call ? '액션' : esc(c.verb)} ${c.pct}%</span>
      ${(c.has_jam || c.has_call) && !c.allin ? `<span style="font-size:12px">${rgvRaiseName(c)} ${
        Math.round((c.pct - c.jam_pct - c.call_pct) * 10) / 10}%${
        c.has_jam ? ` · <b style="color:#b8463a">올인 ${c.jam_pct}%</b>` : ''}${
        c.has_call ? ` · <b style="color:#4f9a5c">콜 ${c.call_pct}%</b>` : ''}</span>` : ''}
      <span style="color:var(--dim);font-size:12px">경계 ${c.mix_pct}%${
        c.source ? ' · 출처 ' + esc(c.source) : ''}</span>
      <span style="flex:1"></span>
      <button onclick="rgvToggleRate()">${v.rate ? '차트 빈도 보기' : '내 실전 기록 겹쳐 보기'}</button>
    </div>
    ${rgvGridHtml(c)}
    <div class="rg-legend" style="margin-top:10px">
      ${c.has_jam ? '<span><i style="background:#72271f"></i>올인</span>' : ''}
      ${c.allin ? '' : `<span><i style="background:#dd4c45"></i>${c.has_jam || c.has_call ? rgvRaiseName(c) : esc(c.verb)}</span>`}
      ${c.has_call ? `<span><i style="background:#4f9a5c"></i>${esc(c.call_name)}</span>` : ''}
      <span><i style="background:#4d7bb3"></i>${esc(c.fold_name || '폴드')}</span>
      <span>칸이 가로로 채워진 비율 = 그 조합의 액션 빈도</span>
      ${v.rate ? '<span><i style="box-shadow:inset 0 0 0 2px #ffdd57"></i>내 실전 기록이 차트와 어긋난 칸</span>' : ''}
    </div>
    <div class="qz-note" style="margin-top:8px">${v.rate
      ? rgHeroNote(c) + (c.vs && st.vs_personalized === false
          ? ' 지금 DB엔 오프너 기록이 없어 비어 보입니다 — <code>python3 gui.py --rebuild</code> 후 채워집니다.' : '')
      : '숫자는 혼합 빈도입니다 (100%·0%인 칸은 생략).'}</div>`;
  $('#hands').innerHTML = `<div class="qz-wrap">${picker}${body}</div>`;
}

// 드릴의 테이블 인원 — 📊 차트 탭과 **같은 설정**(RANGE.view.max, 브라우저 기억)을 쓴다.
// 빠진 앞자리가 포지션 필터에 골라져 있으면 비운다 (그 자리 문제는 더 안 나오므로)
function rgDrillSetMax(n) {
  RANGE.view.max = +n;
  try { localStorage.setItem('ahh_rgv_max', String(n)); } catch (e) {}
  const off = RANGE.state ? RANGE.state.import_positions.map(p => p.key).slice(0, 8 - RANGE.view.max) : [];
  if (RANGE.pos.length && off.includes(RANGE.pos[0])) RANGE.pos = [];
  renderQuiz();
}

function renderRangeQuiz() {
  const st = RANGE.state;
  const off = st && st.import_positions ? st.import_positions.map(p => p.key).slice(0, 8 - RANGE.view.max) : [];
  const maxSel = `<span class="qz-tglabel">인원</span>
    <select class="qz-sel" onchange="rgDrillSetMax(this.value)">${[8, 7, 6, 5, 4, 3].map(n =>
      `<option value="${n}" ${n === RANGE.view.max ? 'selected' : ''}>${rgvMaxName(n)}</option>`).join('')}</select>`;
  const filters = RANGE.spot ? `<div class="qz-note rg-pin">📌 리크 리포트에서 고정한 스팟:
      <b>${esc(RANGE.spot.label)}</b> — 이 스팟만 냅니다
      <button onclick="rgUnpin()">해제</button>
      <button onclick="leakBack()">← 리크 리포트로</button></div>`
    : st && st.positions ? qzSelectRow([
    ['포지션', st.positions.filter(p => !off.includes(p.key)), RANGE.pos[0] || '', 'rgTogglePos'],
    ['스택', st.stacks, RANGE.stack[0] || '', 'rgToggleStack'],
  ]).replace('<div class="qz-tgrow">', '<div class="qz-tgrow">' + maxSel) : '';
  const note = st && st.personalized === false ? `
    <div class="qz-note">📊 <code>python3 gui.py --rebuild</code> 를 돌리면 내가 실제로 차트와
      어긋나게 친 조합이 우선 출제됩니다 — 지금은 균등 무작위로 냅니다.</div>` : `
    <div class="qz-note">내가 실제로 차트와 어긋나게 친 조합이 더 자주 나옵니다.
      경계 핸드는 어느 쪽을 골라도 <b>무난</b>입니다.${
      st && st.vs_personalized === false && (st.custom || []).some(c => c.vs)
        ? ' 방어 차트(vs 오픈)는 <code>--rebuild</code> 후부터 내 기록이 반영됩니다.' : ''}</div>`;
  $('#hands').innerHTML = `<div class="qz-wrap">
    ${rgImportHtml()}
    ${filters}${note}
    ${RANGE.status === 'idle'
      ? `<div class="qz-card" style="text-align:center;padding:34px 22px">
           <div style="font-size:15px;margin-bottom:6px">폴드로 돌아왔을 때 이 핸드를 여나?</div>
           <div style="color:var(--dim);font-size:13px;margin-bottom:18px">
             포지션·스택별 오픈 레인지 차트와 대조해 <b>즉시 채점</b>합니다 (AI 호출 없음).</div>
           <button class="primary" onclick="rgNext()">드릴 시작 →</button></div>`
      : rgQuestionHtml()}
    ${rgScoreHtml()}
  </div>`;
}

// 속사 드릴이라 키보드로 넘길 수 있게 — 오픈 레인지 모드에서만 반응한다
document.addEventListener('keydown', e => {
  if (SEL !== -7 || QUIZ.mode !== 'range') return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  const k = e.key.toLowerCase();
  // 오픈 올인을 받은 문제엔 레이즈 선택지가 없다 — 없는 선택지는 키로도 못 고른다
  if (RANGE.status === 'ready' && (k === 'o' || k === 'r') && RANGE.q &&
      RANGE.q.choices.some(c => c.id === 'open')) { e.preventDefault(); rgAnswer('open'); }
  else if (RANGE.status === 'ready' && k === 'f') { e.preventDefault(); rgAnswer('fold'); }
  else if (RANGE.status === 'ready' && k === 'c' && RANGE.q &&
           RANGE.q.choices.some(c => c.id === 'call')) { e.preventDefault(); rgAnswer('call'); }
  else if ((RANGE.status === 'graded' || RANGE.status === 'idle') &&
           (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); rgNext(); }
});

// ─────────────────────────────────────────────────────────────
// 💬 AI 코치 (SEL = -9) — 내 플레이 기록을 근거로 AI와 대화한다.
// 컨텍스트(내 플레이 요약 · #번호로 짚은 핸드 · 최근 대화)는 서버 coach.build_prompt가 매번
// 조립한다 — 여기는 주고받기만 한다. 스트리밍 중엔 메시지 영역만 다시 그린다: 입력창까지
// 통째로 다시 그리면 답을 기다리며 쓰던 다음 질문과 포커스가 날아간다.
// ─────────────────────────────────────────────────────────────
let COACH = {
  chats: null,      // /api/coach/chats — 저장된 대화 목록 (본문 없음)
  id: '',           // 지금 대화 id ('' = 새 대화 — 첫 답의 X-Chat-Id로 정해진다)
  messages: [],     // [{role: 'user'|'coach', text, hands?, missing?, streaming?, failed?, error?}]
  input: '',
  busy: false,
};
const COACH_EXAMPLES = [
  '최근 플레이가 전체와 비교해 어떻게 달라졌어?',
  '오픈 레인지에서 차트와 가장 어긋나는 곳은 어디야?',
  'BB 방어에서 가장 먼저 고쳐야 할 건?',
  '내 약점 스팟들의 우선순위를 정해줘',
];

function coSidebarMeta() {
  const n = COACH.chats ? COACH.chats.length : 0;
  return n ? `저장된 대화 ${n}개 · 내 기록 근거로 질문` : '내 기록을 근거로 AI와 대화';
}

async function coLoadChats() {
  try { COACH.chats = await (await fetch('/api/coach/chats')).json(); }
  catch (e) { COACH.chats = COACH.chats || []; }
  renderSidebar();
  coRenderBar();
}

function selectCoach() {
  SEL = -9; renderSidebar();
  $('#mainhead').innerHTML = '<h2>💬 AI 코치</h2>';
  renderCoach();
  $('#main').scrollTop = $('#main').scrollHeight;
  coLoadChats();
}

// 핸드 뷰어의 '이 핸드로 대화' — 새 대화를 열고 입력창에 #번호를 미리 넣어 둔다
function coachFromHand(hid) {
  if (COACH.busy) { toast('지금 답변을 받는 중입니다 — 끝난 뒤 다시 눌러 주세요'); return; }
  COACH.id = ''; COACH.messages = [];
  COACH.input = `#${hid} `;
  selectCoach();
}

function renderCoach() {
  if (SEL !== -9) return;
  $('#hands').innerHTML = `<div class="co-wrap">
    <div class="co-bar" id="co-bar"></div>
    <div class="co-msgs" id="co-msgs"></div>
    <div class="co-input">
      <textarea id="co-text" placeholder="내 플레이에 대해 물어보세요 — 예: #300001 이 핸드 리버 콜 괜찮았어?"
                oninput="COACH.input = this.value" onkeydown="coKey(event)">${esc(COACH.input)}</textarea>
      <button class="primary" id="co-send" onclick="coSend()"></button>
    </div>
    <div class="co-hint">Enter 보내기 · Shift+Enter 줄바꿈 · <b>#핸드번호</b>로 핸드를 짚으면 원문을 같이 봅니다
      (목록에 보이는 끝 6자리도 됨) · 매 질문마다 내 플레이 요약이 함께 전달됩니다</div>
  </div>`;
  coRenderBar(); coRenderMsgs(); coRenderBusy();
  const ta = $('#co-text');
  if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
}

function coRenderBar() {
  const el = document.getElementById('co-bar');
  if (!el || SEL !== -9) return;
  const chats = COACH.chats || [];
  el.innerHTML = `
    <button onclick="coNew()" ${COACH.busy ? 'disabled' : ''}>＋ 새 대화</button>
    <select class="qz-sel" onchange="coOpen(this.value)" ${COACH.busy ? 'disabled' : ''}>
      <option value="">${chats.length ? '저장된 대화 열기…' : '저장된 대화 없음'}</option>
      ${chats.map(c => `<option value="${c.id}" ${c.id === COACH.id ? 'selected' : ''}>${
        esc(c.title)} · ${esc(c.updated.slice(5, 16))}</option>`).join('')}
    </select>
    ${COACH.id && chats.some(c => c.id === COACH.id)
      ? `<button onclick="coDelete()" ${COACH.busy ? 'disabled' : ''}>🗑 이 대화 삭제</button>` : ''}
    <span style="color:var(--dim);font-size:12px">최근 대화 ${chats.length}/20개 저장</span>`;
}

function coMsgHtml(m, i) {
  if (m.role === 'user') {
    const refs = (m.hands || []).map(h => '#' + h.slice(-6)).join(' ');
    const miss = (m.missing || []).map(h => '#' + h).join(' ');
    return `<div class="co-msg user">${esc(m.text)}${refs || miss ? `<div class="co-refs">${
      refs ? '📎 같이 보낸 핸드 ' + esc(refs) : ''}${miss ? ' · 못 찾은 번호 ' + esc(miss) : ''}</div>` : ''}</div>`;
  }
  if (m.streaming && !m.text) return '<div class="co-msg coach"><div class="ai-loading">내 기록을 읽고 답하는 중</div></div>';
  return `<div class="co-msg coach">${mdToHtml(m.text || '')}${m.streaming ? '<span class="ai-cursor">▍</span>' : ''}${
    m.failed ? `<div class="co-fail">⚠️ ${esc(m.error || '답이 끝까지 오지 않아 저장되지 않았습니다')}
      <button onclick="coRetry(${i})" style="margin-left:6px">다시 보내기</button></div>` : ''}</div>`;
}

function coRenderMsgs() {
  const el = document.getElementById('co-msgs');
  if (!el || SEL !== -9) return;
  const main = $('#main');
  const atBottom = main.scrollHeight - main.scrollTop - main.clientHeight < 80;
  el.innerHTML = COACH.messages.length
    ? COACH.messages.map(coMsgHtml).join('')
    : `<div class="qz-card" style="text-align:center;padding:28px 20px">
        <div style="font-size:15px;margin-bottom:6px">내 플레이 기록을 근거로 AI 코치와 대화합니다</div>
        <div style="color:var(--dim);font-size:13px;margin-bottom:16px">
          VPIP/PFR · 포지션별 칩 EV · 차트 대비 오픈/방어율 · 약점 스팟 · AI 분석 결과가
          매 질문에 같이 전달됩니다.</div>
        <div class="co-ex">${COACH_EXAMPLES.map((q, i) =>
          `<button onclick="coSend(COACH_EXAMPLES[${i}])">${esc(q)}</button>`).join('')}</div>
      </div>`;
  if (atBottom) main.scrollTop = main.scrollHeight;    // 위로 올려 읽는 중이면 끌어내리지 않는다
}

function coRenderBusy() {
  const b = document.getElementById('co-send');
  if (b) { b.disabled = COACH.busy; b.textContent = COACH.busy ? '답변 중…' : '보내기'; }
  coRenderBar();
}

function coKey(e) {
  // 한글 조합 중 Enter는 글자 확정이지 전송이 아니다
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); coSend(); }
}

function coNew() {
  if (COACH.busy) return;
  COACH.id = ''; COACH.messages = [];
  renderCoach();
}

async function coOpen(id) {
  if (COACH.busy) return;
  if (!id) { coNew(); return; }
  try {
    const d = await (await fetch('/api/coach/chat?id=' + encodeURIComponent(id))).json();
    if (d.error) { toast(d.error); return; }
    COACH.id = d.id; COACH.messages = d.messages;
  } catch (e) { toast(String(e)); return; }
  renderCoach();
  $('#main').scrollTop = $('#main').scrollHeight;
}

async function coDelete() {
  if (COACH.busy || !COACH.id || !confirm('이 대화를 삭제할까요?')) return;
  await fetch('/api/coach/delete', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({id: COACH.id})}).catch(() => {});
  COACH.id = ''; COACH.messages = [];
  renderCoach();
  coLoadChats();
}

// 저장되지 않은 질문을 다시 보낸다 — 실패한 질문·답 한 쌍을 화면에서 걷어내고 같은 글로 재전송
function coRetry(i) {
  const q = COACH.messages[i - 1];
  if (COACH.busy || !q) return;
  COACH.messages.splice(i - 1, 2);
  coSend(q.text);
}

async function coSend(text) {
  if (COACH.busy) return;
  const fromInput = text === undefined;
  text = (fromInput ? COACH.input : text).trim();
  if (!text) return;
  if (fromInput) { COACH.input = ''; const ta = $('#co-text'); if (ta) ta.value = ''; }
  const um = {role: 'user', text, hands: []};
  const cm = {role: 'coach', text: '', streaming: true};
  COACH.messages.push(um, cm);
  COACH.busy = true;
  renderSidebar(); coRenderMsgs(); coRenderBusy();
  const main = $('#main'); main.scrollTop = main.scrollHeight;
  try {
    const res = await fetch('/api/coach/send', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({chat_id: COACH.id, text}),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.error || 'HTTP ' + res.status);
    }
    COACH.id = res.headers.get('X-Chat-Id') || COACH.id;
    um.hands = (res.headers.get('X-Coach-Refs') || '').split(',').filter(Boolean);
    um.missing = (res.headers.get('X-Coach-Missing') || '').split(',').filter(Boolean);
    const reader = res.body.getReader(), dec = new TextDecoder();
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      cm.text += dec.decode(value, {stream: true});
      coRenderMsgs();
    }
    cm.text += dec.decode();
    cm.streaming = false;
    // 서버는 답이 끝까지 왔을 때만 저장한다 — 저장본의 마지막 답이 지금 받은 것과 같은지 확인
    const saved = await (await fetch('/api/coach/chat?id=' + encodeURIComponent(COACH.id))).json()
      .catch(() => null);
    const last = saved && saved.messages && saved.messages[saved.messages.length - 1];
    if (!last || last.role !== 'coach' || last.text !== cm.text.trim()) cm.failed = true;
  } catch (e) {
    cm.streaming = false; cm.failed = true; cm.error = String(e.message || e);
  }
  COACH.busy = false;
  renderSidebar(); coRenderMsgs(); coRenderBusy();
  coLoadChats();
}

function renderQuiz() {
  if (SEL !== -7) return;
  const mb = (k, l) => `<button class="${QUIZ.mode === k ? 'primary' : ''}" onclick="qzSetMode('${k}')">${l}</button>`;
  $('#mainhead').innerHTML = `<h2 style="flex:0 0 auto">🎯 문제 풀기</h2>
    ${mb('hand', '🃏 핸드 리뷰')}${mb('range', '📐 오픈 레인지')}`;
  if (QUIZ.mode === 'range') return renderRangeQuiz();
  $('#hands').innerHTML = `<div class="qz-wrap">
    ${qzSpotsHtml()}
    ${QUIZ.status === 'idle'
      ? `<div class="qz-card" style="text-align:center;padding:34px 22px">
           <div style="font-size:15px;margin-bottom:6px">내가 자주 실수하는 스팟에서 문제를 냅니다</div>
           <div style="color:var(--dim);font-size:13px;margin-bottom:18px">
             실제 내 핸드를 결정 직전에서 끊어 보여주고, 고른 액션을 AI가 채점합니다.</div>
           <button class="primary" onclick="qzNext()">문제 시작 →</button></div>`
      : qzQuestionHtml()}
    ${qzScoreHtml()}
  </div>`;
}

tmLoad();
tmLoop();

// 저장된 DB가 있으면 시작하자마자 표시
fetch('/api/db').then(r => r.json()).then(d => {
  if (d.tournaments && d.tournaments.length) applyData(d);
});
