// 랭킹 페이지
(async function initAuth() {
  try {
    const r = await fetch('/api/me');
    if (r.ok) {
      const me = await r.json();
      const area = document.getElementById('authArea');
      if (me && me.id && area) {
        area.innerHTML = `
          <div class="user-info">
            ${me.profile_image ? `<img src="${me.profile_image}" class="profile-img" style="width:28px;height:28px;border-radius:50%;vertical-align:middle"/>` : ''}
            <span class="user-name">${me.nickname || me.name || ''}</span>
            <a href="/auth/logout" class="logout-btn">로그아웃</a>
          </div>`;
      }
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

async function loadLeaderboard(metric, btn) {
  document.querySelectorAll('.lb-tab').forEach(t => t.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const listEl = document.getElementById('lbList');
  const metaEl = document.getElementById('lbMeta');
  listEl.innerHTML = '<div class="empty-state">로딩 중...</div>';

  try {
    const r = await fetch(`/api/leaderboard?metric=${metric}`);
    if (!r.ok) throw new Error('failed');
    const data = await r.json();
    const ranks = data.ranks || [];
    const metricLabel = { total: '전체 누적', '30d': '최근 30일', '7d': '최근 7일' }[metric] || metric;
    metaEl.textContent = `${metricLabel} 수익률 기준 · ${data.total_participants}명 참여`;

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
      const img = r.profile_image
        ? `<img src="${r.profile_image}" class="lb-avatar" onerror="this.style.display='none'"/>`
        : `<div class="lb-avatar lb-avatar-fallback">${(r.nickname || '?')[0].toUpperCase()}</div>`;
      return `
        <div class="lb-row${meCls}">
          ${rankBadge}
          ${img}
          <div class="lb-name">
            ${r.nickname}${r.is_me ? ' <span class="lb-me-tag">나</span>' : ''}
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
loadLeaderboard('total', document.querySelector('.lb-tab[data-metric="total"]'));
