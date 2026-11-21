// 랭킹 페이지
(async function initAuth() {
  try {
    const r = await fetch('/api/me');
    if (!r.ok) return;
    const data = await r.json();
    const u = data && data.user;        // /api/me 응답은 { user: {...} }
    const area = document.getElementById('authArea');
    if (u && u.id && area) {
      if (window.SAProfile) area.innerHTML = SAProfile.html(u);
    }
  } catch (e) {}
})();

function _fmtKrw(n) {
  if (n == null) return '—';
  if (n >= 1e8)  return (n / 1e8).toFixed(2) + '억';
  if (n >= 1e4)  return Math.round(n / 1e4).toLocaleString() + '만';
  return Math.round(n).toLocaleString();
}

function _fmtPct(p) {
  if (p == null || isNaN(p)) return '—';
  const sign = p > 0 ? '+' : '';
  return sign + p.toFixed(2) + '%';
}

function _rankMedal(rank) {
  if (rank === 1) return '🥇';
  if (rank === 2) return '🥈';
  if (rank === 3) return '🥉';
  return rank;
}

let _lbMetric = 'total';
let _lbBots = true;
try { _lbBots = localStorage.getItem('lbBots') !== '0'; } catch (e) {}

function setBotFilter(on) {
  _lbBots = !!on;
  try { localStorage.setItem('lbBots', on ? '1' : '0'); } catch (e) {}
  _paintBotFilter();
  loadLeaderboard(_lbMetric, document.querySelector(`.lb-tab[data-metric="${_lbMetric}"]`));
}
function _paintBotFilter() {
  document.querySelectorAll('.lb-bot-btn').forEach(b => {
    const on = (b.dataset.bots === '1') === _lbBots;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}

async function loadLeaderboard(metric, btn) {
  _lbMetric = metric;
  document.querySelectorAll('.lb-tab').forEach(t => t.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const listEl = document.getElementById('lbList');
  const metaEl = document.getElementById('lbMeta');
  listEl.innerHTML = '<div class="empty-state">로딩 중...</div>';

  try {
    const r = await fetch(`/api/leaderboard?metric=${metric}&bots=${_lbBots ? 1 : 0}`);
    if (!r.ok) throw new Error('failed');
    const data = await r.json();
    const ranks = data.ranks || [];
    const metricLabel = { total: '전체 누적', '30d': '최근 30일', '7d': '최근 7일' }[metric] || metric;
    const botN = ranks.filter(r => r.is_bot).length;
    metaEl.textContent = `${metricLabel} 수익률 기준 · ${data.total_participants - botN}명 참여` + (botN ? ` · AI 봇 ${botN}개` : '');

    if (!ranks.length) {
      listEl.innerHTML = `<div class="empty-state">아직 ${metricLabel} 랭킹 데이터가 부족합니다. 며칠 후 다시 확인해주세요.</div>`;
      return;
    }

    listEl.innerHTML = ranks.map(r => {
      const pct = r.return_pct;
      const cls = pct > 0 ? 'pos' : (pct < 0 ? 'neg' : '');
      const rankBadge = r.rank <= 3
        ? `<span class="lb-rank lb-rank-${r.rank}">${_rankMedal(r.rank)}</span>`
        : `<span class="lb-rank">${r.rank}</span>`;
      const meCls = r.is_me ? ' lb-me' : '';
      const img = r.is_bot
        ? `<div class="lb-avatar lb-avatar-fallback lb-avatar-bot" aria-hidden="true">🤖</div>`
        : r.profile_image
        ? `<img src="${_escName(r.profile_image)}" class="lb-avatar" onerror="this.style.display='none'"/>`
        : `<div class="lb-avatar lb-avatar-fallback">${_escName((r.nickname || '?')[0].toUpperCase())}</div>`;
      const botTag = r.is_bot ? ' <span class="lb-bot-tag" title="알고리즘 검증용 가상 봇 계좌">AI BOT</span>' : '';
      const nameHtml = r.is_bot
        ? `<a class="lb-bot-link" href="/ai-bots#${encodeURIComponent(r.bot_key || '')}">${_escName(r.nickname)}</a>`
        : _escName(r.nickname);
      const resetTag = r.reset_count > 0
        ? ` <span class="lb-reset-tag" title="1억 초기화 ${r.reset_count}회">🔄${r.reset_count}</span>`
        : '';
      return `
        <div class="lb-row${meCls}${r.is_bot ? ' lb-bot' : ''}">
          ${rankBadge}
          ${img}
          <div class="lb-name">
            ${nameHtml}${botTag}${r.is_me ? ' <span class="lb-me-tag">나</span>' : ''}${resetTag}
          </div>
          <div class="lb-pct ${cls}">${_fmtPct(pct)}</div>
          <div class="lb-assets">${_fmtKrw(r.total_assets_krw)}원</div>
        </div>`;
    }).join('');
  } catch (e) {
    console.error(e);
    listEl.innerHTML = '<div class="empty-state">랭킹을 불러오지 못했습니다.</div>';
  }
}

// 초기 로드
_paintBotFilter();
loadLeaderboard('total', document.querySelector('.lb-tab[data-metric="total"]'));

// 외부(OAuth) 이름을 HTML 에 넣기 전 이스케이프
function _escName(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
