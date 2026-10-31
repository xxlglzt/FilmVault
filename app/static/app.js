/* FilmVault 单页前端，无构建依赖 */

const app = document.getElementById('app');
const playerRoot = document.getElementById('player-root');
const directoryRoot = document.getElementById('directory-root');
const scanRoot = document.getElementById('scan-root');
const localRoot = document.getElementById('local-root');
const STATUS_LABEL = { none: '未标记', want: '想看', watching: '在看', watched: '看过', on_hold: '搁置', dropped: '弃坑' };
const TYPE_LABEL = { movie: '电影', tv: '电视剧' };
const PLAY_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7z"></path></svg>';
const FOLDER_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7h7l2 2h9v10H3z"></path></svg>';

async function api(path, options = {}) {
  const opts = { headers: {}, ...options };
  if (opts.body && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
  }
  const resp = await fetch(path, opts);
  if (resp.status === 204) return null;
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const msg = data.detail ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : `HTTP ${resp.status}`;
    throw new Error(msg);
  }
  return data;
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function flash(message, isError = false) {
  const root = document.getElementById('toast-root');
  root.innerHTML = `<div class="flash${isError ? ' error' : ''}">${esc(message)}</div>`;
  setTimeout(() => { root.innerHTML = ''; }, 3600);
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

function starsHtml(rating, cls = '') {
  const pct = rating == null ? 0 : rating * 10;
  return `<span class="stars ${cls}">★★★★★<span class="fill" style="width:${pct}%">★★★★★</span></span>`;
}

function interactiveStars(el, current, onRate) {
  function render(rating, hover) {
    const value = hover ?? rating;
    el.innerHTML = starsHtml(value, 'lg interactive') + `<span class="muted">${value == null ? '点击评分' : value + ' 分'}</span>`;
    const stars = el.querySelector('.stars');
    const width = stars.offsetWidth || 180;
    for (let half = 1; half <= 10; half++) {
      const hit = document.createElement('i');
      hit.className = 'hit';
      hit.style.left = ((half - 1) * width / 10) + 'px';
      hit.style.width = (width / 10) + 'px';
      hit.onmouseenter = () => render(rating, half);
      hit.onclick = () => onRate(half);
      stars.appendChild(hit);
    }
    stars.onmouseleave = () => render(rating, null);
  }
  render(current, null);
}

function workCard(w) {
  const rating = w.my_rating ?? w.douban_rating;
  return `<article class="work-card">
    <a href="#/work/${w.id}" aria-label="${esc(w.title)}">
      <div class="poster-wrap">
        <img src="/api/works/${w.id}/poster" loading="lazy" alt="${esc(w.title)}">
        ${w.status !== 'none' ? `<span class="card-status">${STATUS_LABEL[w.status]}</span>` : ''}
        <span class="card-play">${PLAY_ICON}</span>
      </div>
      <div class="card-info">
        <div class="card-title" title="${esc(w.title)}">${esc(w.title)}</div>
        <div class="card-sub"><span>${w.year ?? ''} · ${TYPE_LABEL[w.type]}</span>${rating != null ? `<span class="card-rating">★ ${rating}</span>` : ''}</div>
      </div>
    </a>
  </article>`;
}

function shelf(id, title, items) {
  if (!items.length) return '';
  return `<section class="shelf">
    <div class="shelf-heading"><h2>${esc(title)}</h2><div class="shelf-controls">
      <button data-scroll="${id}" data-dir="-1" aria-label="向左滚动">‹</button>
      <button data-scroll="${id}" data-dir="1" aria-label="向右滚动">›</button>
    </div></div>
    <div class="poster-row" id="shelf-${id}">${items.map(workCard).join('')}</div>
  </section>`;
}

function bindShelves() {
  document.querySelectorAll('[data-scroll]').forEach(button => {
    button.onclick = () => {
      const row = document.getElementById('shelf-' + button.dataset.scroll);
      row.scrollBy({ left: Number(button.dataset.dir) * row.clientWidth * .8, behavior: 'smooth' });
    };
  });
}

async function renderHome() {
  document.body.classList.add('home-route');
  const data = await api('/api/works?sort=created');
  if (!data.items.length) {
    app.innerHTML = `<div class="empty-home"><div class="empty-content">
      <div class="logo" style="font-size:34px;margin-bottom:25px">FILMVAULT</div>
      <h1>你的私人影院，从一个目录开始</h1>
      <p>选择存放电影或电视剧的目录，FilmVault 会自动识别片名、下载海报与豆瓣评分，并整理成分季分集的媒体库。</p>
      <button class="btn play" onclick="openDirectoryPicker()">${FOLDER_ICON} 选择媒体目录</button>
    </div></div>`;
    return;
  }
  const featured = [...data.items].sort((a, b) => (b.douban_rating || 0) - (a.douban_rating || 0))[0];
  const movies = data.items.filter(w => w.type === 'movie');
  const tv = data.items.filter(w => w.type === 'tv');
  const highRated = [...data.items].filter(w => w.douban_rating != null).sort((a, b) => b.douban_rating - a.douban_rating);
  const watching = data.items.filter(w => w.status === 'watching');
  app.innerHTML = `<div class="home-page">
    <section class="hero">
      <img class="hero-backdrop" src="/api/works/${featured.id}/poster" alt="">
      <img class="hero-poster" src="/api/works/${featured.id}/poster" alt="${esc(featured.title)}">
      <div class="hero-content">
        <div class="hero-kicker"><b>F</b> 今日精选</div>
        <h1>${esc(featured.title)}</h1>
        <div class="hero-meta"><span>${featured.year ?? ''}</span><span>${TYPE_LABEL[featured.type]}</span>${featured.douban_rating != null ? `<span class="rating">豆瓣 ${featured.douban_rating}</span>` : ''}</div>
        <p class="hero-summary">${esc(featured.summary || '已收藏到你的私人媒体库。')}</p>
        <div class="hero-actions">
          ${featured.first_file_id ? `<button class="btn play" onclick="openPlayer(${featured.first_file_id}, ${featured.first_episode_id ?? 'null'}, ${featured.first_progress_seconds || 0})">${PLAY_ICON} ${featured.first_progress_seconds ? '继续播放' : '播放'}</button><button class="btn ghost" onclick="playLocal(${featured.first_file_id})">本地播放</button>` : ''}
          <a class="btn secondary" href="#/work/${featured.id}">查看详情</a>
        </div>
      </div>
    </section>
    <div class="shelves">
      ${watching.length ? shelf('watching', '继续观看', watching) : ''}
      ${shelf('recent', '最近添加', data.items.slice(0, 20))}
      ${shelf('movies', '电影', movies.slice(0, 20))}
      ${shelf('tv', '电视剧', tv.slice(0, 20))}
      ${shelf('rated', '高分作品', highRated.slice(0, 20))}
    </div>
  </div>`;
  bindShelves();
}

async function renderCollection(params, forcedType = '') {
  const state = {
    type: forcedType || params.get('type') || '', status: params.get('status') || '',
    tag: params.get('tag') || '', q: params.get('q') || '', sort: params.get('sort') || 'created',
  };
  const query = new URLSearchParams();
  Object.entries(state).forEach(([key, value]) => value && query.set(key, value));
  const [data, tags] = await Promise.all([api('/api/works?' + query), api('/api/tags')]);
  const title = state.q ? `“${state.q}”的搜索结果` : state.tag ? state.tag : state.status ? STATUS_LABEL[state.status] : TYPE_LABEL[state.type] || '全部作品';
  app.innerHTML = `<div class="page">
    <div class="list-header"><div><h1>${esc(title)}</h1><p>共 ${data.total} 部作品</p></div>
      <div class="filters">
        <select id="f-status"><option value="">全部状态</option>${Object.entries(STATUS_LABEL).filter(([k]) => k !== 'none').map(([k, v]) => `<option value="${k}" ${state.status === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
        <select id="f-tag"><option value="">全部标签</option>${tags.items.map(t => `<option value="${esc(t.name)}" ${state.tag === t.name ? 'selected' : ''}>${esc(t.name)} (${t.count})</option>`).join('')}</select>
        <select id="f-sort">${[['created', '最近添加'], ['my_rating', '我的评分'], ['douban_rating', '豆瓣评分'], ['year', '年份'], ['title', '标题']].map(([k, v]) => `<option value="${k}" ${state.sort === k ? 'selected' : ''}>按${v}</option>`).join('')}</select>
      </div>
    </div>
    ${data.items.length ? `<div class="grid">${data.items.map(workCard).join('')}</div>` : '<div class="empty">这里还没有作品</div>'}
  </div>`;
  const reload = () => {
    const p = new URLSearchParams();
    const status = document.getElementById('f-status').value;
    const tag = document.getElementById('f-tag').value;
    const sort = document.getElementById('f-sort').value;
    if (status) p.set('status', status);
    if (tag) p.set('tag', tag);
    if (sort !== 'created') p.set('sort', sort);
    if (state.q) p.set('q', state.q);
    location.hash = (state.type ? (state.type === 'movie' ? '#/movies' : '#/tv') : '#/collection') + (p.toString() ? '?' + p : '');
  };
  document.getElementById('f-status').onchange = reload;
  document.getElementById('f-tag').onchange = reload;
  document.getElementById('f-sort').onchange = reload;
}

async function renderWork(id) {
  const w = await api(`/api/works/${id}`);
  const firstFile = w.files[0];
  const info = [
    ['原名', esc(w.original_title)], ['地区', esc(w.region)],
    ['类型', w.genres.map(g => `<a href="#/tag/${encodeURIComponent(g)}">${esc(g)}</a>`).join(' / ')],
    ['导演', w.directors.map(esc).join(' / ')], ['主演', w.cast.map(esc).join(' / ')],
    ['IMDb', w.imdb_id ? `<a href="https://www.imdb.com/title/${w.imdb_id}/" target="_blank">${esc(w.imdb_id)}</a>` : ''],
  ];
  const episodes = w.seasons.map(s => `<section class="season-block">
    <h3>第 ${s.season_number} 季 <span class="muted">${s.episodes.filter(e => e.watched).length}/${s.episodes.length} 已看</span></h3>
    <div class="episode-list">${s.episodes.map(e => {
      const pct = e.duration_seconds ? Math.min(100, Math.round(e.progress_seconds / e.duration_seconds * 100)) : 0;
      return `<div class="episode"><span class="episode-number">${e.episode_number}</span>
        <div><div class="episode-title">${esc(e.title || `第 ${e.episode_number} 集`)}</div>
          <div class="episode-meta">${e.air_date ? `<span>${e.air_date}</span>` : ''}${pct ? `<span class="progress-track"><i style="width:${pct}%"></i></span><span>${pct}%</span>` : ''}<label><input type="checkbox" ${e.watched ? 'checked' : ''} onchange="toggleWatched(${e.id}, this.checked)"> 已看</label></div>
        </div>
        <div>${e.file_id ? `<button class="btn small play" onclick="openPlayer(${e.file_id}, ${e.id}, ${e.progress_seconds})">${PLAY_ICON} 播放</button> <button class="btn small ghost" onclick="playLocal(${e.file_id})">本地播放</button>` : '<span class="muted">暂无文件</span>'}</div>
      </div>`;
    }).join('')}</div>
  </section>`).join('');
  const reviews = w.reviews.map(r => `<div class="review"><div>${starsHtml(r.rating)} <b class="card-rating">${r.rating ?? ''}</b></div><p>${esc(r.content)}</p><div class="meta">${r.created_at}<a href="javascript:void 0" onclick="deleteReview(${r.id}, ${w.id})">删除</a></div></div>`).join('');
  const otherFiles = w.files.filter(f => !f.episode_id);
  app.innerHTML = `
    <section class="detail-hero">
      <img class="detail-backdrop" src="/api/works/${w.id}/poster" alt="">
      <div class="detail-main">
        <img class="detail-poster" src="/api/works/${w.id}/poster" alt="${esc(w.title)}">
        <div class="detail-copy">
          <h1>${esc(w.title)}</h1><div class="original-title">${esc(w.original_title)}</div>
          <div class="detail-meta"><span>${w.year ?? ''}</span><span>${TYPE_LABEL[w.type]}</span>${w.douban_rating != null ? `<a class="douban" href="https://movie.douban.com/subject/${w.douban_id}/" target="_blank">豆瓣 ${w.douban_rating}</a>` : ''}</div>
          <p class="detail-summary">${esc(w.summary || '暂无简介')}</p>
          <div class="detail-actions">
            ${firstFile ? `<button class="btn play" onclick="openPlayer(${firstFile.id}, ${firstFile.episode_id ?? 'null'}, ${firstFile.progress_seconds || 0})">${PLAY_ICON} ${firstFile.progress_seconds ? '继续播放' : '播放'}</button><button class="btn ghost" onclick="playLocal(${firstFile.id})">本地播放</button>` : ''}
            <button class="btn secondary" onclick="location.hash='#/edit/${w.id}'">编辑</button>
            <button class="btn ghost" onclick="rescrape(${w.id}, '${w.type}')">刷新资料</button>
          </div>
        </div>
      </div>
    </section>
    <div class="detail-content">
      <section class="detail-section"><div class="info-grid">${info.filter(([, value]) => value).map(([key, value]) => `<div><span class="key">${key}</span>${value}</div>`).join('')}</div></section>
      <section class="detail-section rate-panel">
        <div class="rating-box"><h3>我的评分</h3><div class="rating-line" id="my-stars"></div>
          <div class="tags-row">${w.tags.map(t => `<span class="tag-chip">${esc(t)}</span>`).join('')}</div>
          <div class="tags-row"><input id="tag-input" value="${esc(w.tags.join(', '))}" placeholder="标签，用逗号分隔"><button class="btn small ghost" onclick="saveTags(${w.id})">保存标签</button></div>
        </div>
        <div class="rating-box"><h3>观看状态</h3><div class="status-btns">${Object.entries(STATUS_LABEL).filter(([k]) => k !== 'none').map(([k, v]) => `<button class="btn small ${w.status === k ? 'on' : 'ghost'}" onclick="setStatus(${w.id}, '${k}')">${v}</button>`).join('')}</div></div>
      </section>
      ${w.type === 'tv' ? `<section class="detail-section"><h2>剧集</h2>${episodes || '<div class="empty">暂无季集信息</div>'}</section>` : ''}
      ${otherFiles.length ? `<section class="detail-section"><h2>可播放文件</h2><div class="episode-list">${otherFiles.map(f => `<div class="episode"><span>${PLAY_ICON}</span><div class="episode-title">${esc(f.path.split(/[\\/]/).pop())}<div class="episode-meta">${f.progress_seconds ? `已观看 ${Math.floor(f.progress_seconds / 60)} 分钟` : ''}</div></div><div><button class="btn small play" onclick="openPlayer(${f.id}, null, ${f.progress_seconds || 0})">播放</button> <button class="btn small ghost" onclick="playLocal(${f.id})">本地播放</button></div></div>`).join('')}</div></section>` : ''}
      <section class="detail-section"><h2>短评</h2>${reviews || '<p class="muted">还没有短评</p>'}
        <div class="review-form"><div class="rating-line" id="review-stars"></div><textarea id="review-content" placeholder="写下你的观影感受…"></textarea><button class="btn" onclick="addReview(${w.id})">发表短评</button></div>
      </section>
      <section class="detail-section"><button class="btn small danger" onclick="deleteWork(${w.id})">从媒体库删除此作品</button></section>
    </div>`;
  interactiveStars(document.getElementById('my-stars'), w.my_rating, async value => {
    await api(`/api/works/${w.id}/state`, { method: 'PATCH', body: { my_rating: value } });
    flash(`已评 ${value} 分`);
    renderWork(id);
  });
  let reviewRating = null;
  interactiveStars(document.getElementById('review-stars'), null, value => {
    reviewRating = value;
    document.getElementById('review-stars').dataset.rating = value;
  });
  document.getElementById('review-stars').dataset.rating = '';
}

async function setStatus(id, status) {
  await api(`/api/works/${id}/state`, { method: 'PATCH', body: { status } });
  flash(`已标记为“${STATUS_LABEL[status]}”`);
  renderWork(id);
}

async function saveTags(id) {
  const tags = document.getElementById('tag-input').value.split(/[,，]/).map(s => s.trim()).filter(Boolean);
  await api(`/api/works/${id}/tags`, { method: 'PUT', body: { tags } });
  flash('标签已保存');
  renderWork(id);
}

async function addReview(workId) {
  const content = document.getElementById('review-content').value.trim();
  if (!content) return flash('短评内容不能为空', true);
  const value = document.getElementById('review-stars').dataset.rating;
  await api(`/api/works/${workId}/reviews`, { method: 'POST', body: { content, rating: value ? Number(value) : null } });
  flash('短评已发表');
  renderWork(workId);
}

async function deleteReview(id, workId) {
  if (!confirm('删除这条短评？')) return;
  await api(`/api/reviews/${id}`, { method: 'DELETE' });
  renderWork(workId);
}

async function deleteWork(id) {
  if (!confirm('从媒体库删除此作品？本地视频文件不会被删除。')) return;
  await api(`/api/works/${id}`, { method: 'DELETE' });
  location.hash = '#/';
}

async function toggleWatched(episodeId, watched) {
  await api(`/api/episodes/${episodeId}`, { method: 'PATCH', body: { watched } });
}

async function rescrape(workId, type) {
  const source = prompt('使用哪个数据源？输入 douban 或 tmdb', 'douban');
  if (!source) return;
  const externalId = prompt(`请输入该作品在 ${source} 的 ID：`);
  if (!externalId) return;
  try {
    await api(`/api/works/${workId}/rescrape`, { method: 'POST', body: { source, external_id: externalId, type } });
    flash('资料已刷新');
    renderWork(workId);
  } catch (e) { flash(e.message, true); }
}

async function renderAdd() {
  app.innerHTML = `<div class="page"><div class="list-header"><div><h1>手动添加</h1><p>自动扫描之外，也可以按源站条目或手工资料添加</p></div></div>
    <div class="add-tabs"><button class="btn" id="tab-scrape" onclick="switchTab('scrape')">搜索源站</button><button class="btn ghost" id="tab-manual" onclick="switchTab('manual')">手工录入</button></div>
    <div id="tab-scrape-body"><div class="search-import"><select id="scrape-source"><option value="douban">豆瓣</option><option value="tmdb">TMDB</option></select><input id="scrape-q" placeholder="输入电影或电视剧名称"><button class="btn" onclick="doScrapeSearch()">搜索</button></div><div id="scrape-results"></div></div>
    <div id="tab-manual-body" class="hidden"><div class="panel form-grid">
      <label>类型</label><select id="m-type"><option value="movie">电影</option><option value="tv">电视剧</option></select>
      <label>片名 *</label><input id="m-title"><label>原名</label><input id="m-orig"><label>年份</label><input id="m-year" type="number">
      <label>地区</label><input id="m-region"><label>类型标签</label><input id="m-genres" placeholder="剧情, 悬疑"><label>导演</label><input id="m-directors"><label>主演</label><input id="m-cast">
      <label>豆瓣评分</label><input id="m-douban" type="number" step="0.1"><label>简介</label><textarea id="m-summary"></textarea><span></span><button class="btn" onclick="createManual()">创建作品</button>
    </div></div>
  </div>`;
  document.getElementById('scrape-q').onkeydown = e => { if (e.key === 'Enter') doScrapeSearch(); };
}

function switchTab(which) {
  document.getElementById('tab-scrape-body').classList.toggle('hidden', which !== 'scrape');
  document.getElementById('tab-manual-body').classList.toggle('hidden', which !== 'manual');
  document.getElementById('tab-scrape').className = which === 'scrape' ? 'btn' : 'btn ghost';
  document.getElementById('tab-manual').className = which === 'manual' ? 'btn' : 'btn ghost';
}

async function doScrapeSearch() {
  const source = document.getElementById('scrape-source').value;
  const q = document.getElementById('scrape-q').value.trim();
  if (!q) return;
  const box = document.getElementById('scrape-results');
  box.innerHTML = '<div class="empty">正在搜索…</div>';
  try {
    const data = await api(`/api/scrape/search?source=${source}&q=${encodeURIComponent(q)}`);
    box.innerHTML = data.items.length ? data.items.map((it, i) => `<div class="result-item"><img src="${esc(it.poster_url)}" onerror="this.style.visibility='hidden'" alt=""><div><h3>${esc(it.title)} <span class="muted">${esc(it.original_title)}</span></h3><div class="muted">${it.year ?? ''} ${it.rating != null ? '· ★ ' + it.rating : ''}</div><p>${esc((it.summary || '').slice(0, 180))}</p><select id="imp-type-${i}"><option value="movie">电影</option><option value="tv">电视剧</option></select> <button class="btn small" onclick="doImport('${source}', '${esc(it.external_id)}', document.getElementById('imp-type-${i}').value)">添加</button></div></div>`).join('') : '<div class="empty">没有找到相关作品</div>';
  } catch (e) { box.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

async function doImport(source, externalId, type) {
  try {
    const result = await api('/api/scrape/import', { method: 'POST', body: { source, external_id: externalId, type } });
    location.hash = `#/work/${result.id}`;
  } catch (e) { flash(e.message, true); }
}

function manualBody() {
  const split = s => s.split(/[,，/、]+/).map(x => x.trim()).filter(Boolean);
  return {
    type: document.getElementById('m-type').value, title: document.getElementById('m-title').value.trim(),
    original_title: document.getElementById('m-orig').value.trim(), year: Number(document.getElementById('m-year').value) || null,
    region: document.getElementById('m-region').value.trim(), genres: split(document.getElementById('m-genres').value),
    directors: split(document.getElementById('m-directors').value), cast: split(document.getElementById('m-cast').value),
    summary: document.getElementById('m-summary').value.trim(), douban_rating: Number(document.getElementById('m-douban').value) || null,
  };
}

async function createManual() {
  const body = manualBody();
  if (!body.title) return flash('片名必填', true);
  const result = await api('/api/works', { method: 'POST', body });
  location.hash = `#/work/${result.id}`;
}

async function renderEdit(id) {
  const w = await api(`/api/works/${id}`);
  app.innerHTML = `<div class="page"><div class="list-header"><div><h1>编辑《${esc(w.title)}》</h1></div></div><div class="panel form-grid">
    <label>类型</label><select id="m-type"><option value="movie" ${w.type === 'movie' ? 'selected' : ''}>电影</option><option value="tv" ${w.type === 'tv' ? 'selected' : ''}>电视剧</option></select>
    <label>片名</label><input id="m-title" value="${esc(w.title)}"><label>原名</label><input id="m-orig" value="${esc(w.original_title)}"><label>年份</label><input id="m-year" type="number" value="${w.year ?? ''}">
    <label>地区</label><input id="m-region" value="${esc(w.region)}"><label>类型标签</label><input id="m-genres" value="${esc(w.genres.join(', '))}"><label>导演</label><input id="m-directors" value="${esc(w.directors.join(', '))}"><label>主演</label><input id="m-cast" value="${esc(w.cast.join(', '))}">
    <label>豆瓣评分</label><input id="m-douban" type="number" step="0.1" value="${w.douban_rating ?? ''}"><label>简介</label><textarea id="m-summary">${esc(w.summary)}</textarea>
    <label>我的评分</label><input id="m-myrating" type="number" step="1" min="0" max="10" value="${w.my_rating ?? ''}"><label>状态</label><select id="m-status">${Object.entries(STATUS_LABEL).map(([k, v]) => `<option value="${k}" ${w.status === k ? 'selected' : ''}>${v}</option>`).join('')}</select>
    <span></span><div><button class="btn" onclick="saveEdit(${w.id})">保存</button> <a class="btn ghost" href="#/work/${w.id}">取消</a></div>
  </div></div>`;
}

async function saveEdit(id) {
  const body = manualBody();
  body.my_rating = Number(document.getElementById('m-myrating').value) || null;
  body.status = document.getElementById('m-status').value;
  if (!body.title) return flash('片名必填', true);
  await api(`/api/works/${id}`, { method: 'PUT', body });
  location.hash = `#/work/${id}`;
}

async function renderStats() {
  const s = await api('/api/stats');
  const maxYear = Math.max(1, ...s.by_year.map(r => r.c));
  app.innerHTML = `<div class="page"><div class="list-header"><div><h1>观影统计</h1><p>你的收藏与观看记录</p></div></div>
    <div class="stat-cards"><div class="stat-card"><div class="num">${s.total}</div><div class="label">收录作品</div></div><div class="stat-card"><div class="num">${s.by_type.movie || 0}</div><div class="label">电影</div></div><div class="stat-card"><div class="num">${s.by_type.tv || 0}</div><div class="label">电视剧</div></div><div class="stat-card"><div class="num">${s.watched_episodes}/${s.total_episodes}</div><div class="label">已看剧集</div></div><div class="stat-card"><div class="num">${s.avg_my_rating ?? '—'}</div><div class="label">我的平均评分</div></div></div>
    <div class="panel"><h2>年份分布</h2>${s.by_year.map(r => `<div class="bar-row"><span class="name">${r.year}</span><span class="bar" style="width:${r.c / maxYear * 65}%"></span><span>${r.c}</span></div>`).join('') || '<span class="muted">暂无数据</span>'}</div>
    <div class="panel"><h2>我的高分榜</h2><div class="top-list">${s.top_rated.map((w, i) => `<div><span class="rank">${i + 1}</span><a href="#/work/${w.id}">${esc(w.title)}</a> <b class="card-rating">★ ${w.my_rating}</b></div>`).join('') || '<span class="muted">还没有评分</span>'}</div></div>
    <div class="panel"><h2>最新短评</h2>${s.recent_reviews.map(r => `<div class="review"><a href="#/work/${r.work_id}">《${esc(r.title)}》</a> ${starsHtml(r.rating)}<p>${esc(r.content)}</p><div class="meta">${r.created_at}</div></div>`).join('') || '<span class="muted">还没有短评</span>'}</div>
  </div>`;
}

async function renderSettings() {
  const s = await api('/api/settings');
  app.innerHTML = `<div class="page"><div class="list-header"><div><h1>设置</h1><p>数据源、本地播放器与存储</p></div></div>
    <div class="panel form-grid"><label>TMDB API Key</label><div><input id="s-tmdb" value="${esc(s.tmdb_api_key)}" placeholder="可选，用于增强国外影视识别"><p class="muted" style="margin-top:8px;line-height:1.7">自动扫描默认可使用豆瓣。填写 TMDB 免费 Key 后会增加一个识别来源，并补全电视剧季集资料。</p></div><span></span><button class="btn" onclick="saveSettings()">保存设置</button></div>
    <div class="panel form-grid"><label>本地播放器程序</label><div><input id="s-player" value="${esc(s.local_player_path)}" placeholder="C:/Program Files/VideoLAN/VLC/vlc.exe"><p class="muted" style="margin-top:8px;line-height:1.7">填写 PotPlayer、VLC 等播放器的完整程序路径。VLC 可实时同步实际播放位置；其他播放器在关闭后按本次播放时长同步。观看进度达到 90% 会自动标记为已看。</p></div><span></span><button class="btn" onclick="saveSettings()">保存播放器</button></div>
    <div class="panel"><h2 style="margin-bottom:12px">数据说明</h2><p class="muted" style="line-height:2">全部数据保存在项目 <code>data/</code> 目录，备份该目录即可。扫描只读取视频路径，不会修改或删除本地文件。移动文件后重新扫描即可更新媒体库。</p></div>
  </div>`;
}

async function saveSettings() {
  try {
    await api('/api/settings', { method: 'PUT', body: { tmdb_api_key: document.getElementById('s-tmdb').value, local_player_path: document.getElementById('s-player').value } });
    flash('设置已保存');
  } catch (e) { flash(e.message, true); }
}

let directoryPath = '';

async function openDirectoryPicker() {
  directoryRoot.innerHTML = `<div class="modal-mask" id="directory-mask"><div class="modal">
    <div class="modal-header"><div><h2>选择媒体目录</h2><p class="muted" style="margin-top:5px">选择后将自动识别并生成媒体库</p></div><button class="modal-close" onclick="closeDirectoryPicker()">×</button></div>
    <div class="modal-body"><div id="saved-folders"></div><div class="current-folder" id="current-folder">选择磁盘或位置</div><div class="directory-list" id="directory-list"></div></div>
    <div class="modal-footer"><span class="muted">单击文件夹进入，可包含电影和电视剧</span><button class="btn" id="choose-folder" disabled onclick="startSelectedScan()">选择此目录并开始</button></div>
  </div></div>`;
  document.body.classList.add('modal-open');
  document.getElementById('directory-mask').onclick = e => { if (e.target === e.currentTarget) closeDirectoryPicker(); };
  await Promise.all([loadDirectories(''), loadSavedFolders()]);
}

function closeDirectoryPicker() {
  directoryRoot.innerHTML = '';
  if (!playerRoot.innerHTML) document.body.classList.remove('modal-open');
}

async function loadSavedFolders() {
  const box = document.getElementById('saved-folders');
  if (!box) return;
  const data = await api('/api/library/folders');
  if (!data.items.length) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="saved-folders"><h3>常用目录</h3>${data.items.map(folder => `<div class="saved-folder"><span title="${esc(folder)}">${esc(folder)}</span><button class="btn small ghost" data-folder="${esc(folder)}">重新扫描</button></div>`).join('')}</div>`;
  box.querySelectorAll('[data-folder]').forEach(button => { button.onclick = () => startScan(button.dataset.folder); });
}

async function loadDirectories(path) {
  const list = document.getElementById('directory-list');
  if (!list) return;
  list.innerHTML = '<div class="empty">正在读取…</div>';
  try {
    const data = await api('/api/system/directories?path=' + encodeURIComponent(path));
    if (!document.getElementById('directory-list')) return;
    directoryPath = data.current;
    document.getElementById('current-folder').textContent = data.current || '此电脑';
    document.getElementById('choose-folder').disabled = !data.current;
    const back = data.parent !== null ? `<button class="directory-item" id="directory-back">${FOLDER_ICON}<span>返回上一级</span></button>` : '';
    list.innerHTML = back + (data.items.map(item => `<button class="directory-item" data-path="${esc(item.path)}">${FOLDER_ICON}<span>${esc(item.name)}</span></button>`).join('') || '<div class="empty">此位置没有子目录</div>');
    const backButton = document.getElementById('directory-back');
    if (backButton) backButton.onclick = () => loadDirectories(data.parent || '');
    list.querySelectorAll('[data-path]').forEach(button => { button.onclick = () => loadDirectories(button.dataset.path); });
  } catch (e) { list.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
}

function startSelectedScan() {
  if (directoryPath) startScan(directoryPath);
}

let scanPollTimer = null;
let scanMinimized = false;

async function startScan(folder) {
  closeDirectoryPicker();
  clearTimeout(scanPollTimer);
  scanMinimized = false;
  scanRoot.innerHTML = `<div class="scan-widget"><div class="scan-head"><strong>正在准备扫描</strong></div><div class="scan-body"><div class="scan-current">${esc(folder)}</div></div></div>`;
  try {
    const job = await api('/api/library/scan', { method: 'POST', body: { folder } });
    localStorage.setItem('filmvault_scan_job', job.id);
    renderScanJob(job);
    pollScan(job.id);
  } catch (e) {
    scanRoot.innerHTML = '';
    flash(e.message, true);
  }
}

async function pollScan(jobId) {
  clearTimeout(scanPollTimer);
  try {
    const job = await api(`/api/library/scan/${jobId}`);
    renderScanJob(job);
    if (job.status === 'running') {
      scanPollTimer = setTimeout(() => pollScan(jobId), 900);
    } else {
      localStorage.removeItem('filmvault_scan_job');
      if (job.status === 'done') flash(`扫描完成：新增 ${job.added} 部作品，更新 ${job.updated} 个文件`);
      else flash('扫描中断，请查看进度面板中的原因', true);
      route();
    }
  } catch (e) {
    localStorage.removeItem('filmvault_scan_job');
    scanRoot.innerHTML = '';
    flash(e.message, true);
  }
}

function renderScanJob(job) {
  const pct = job.total ? Math.round(job.processed / job.total * 100) : 3;
  const running = job.status === 'running';
  scanRoot.innerHTML = `<div class="scan-widget${scanMinimized ? ' minimized' : ''}" id="scan-widget">
    <div class="scan-head"><strong>${running ? '正在整理媒体库' : job.status === 'done' ? '媒体库已更新' : '扫描中断'}</strong><button onclick="toggleScanWidget()" title="${scanMinimized ? '展开' : '收起'}">${scanMinimized ? '□' : '—'}</button></div>
    <div class="scan-body"><div class="scan-phase"><span>${esc(job.phase)}</span><span>${job.processed}/${job.total}</span></div><div class="scan-progress"><i style="width:${pct}%"></i></div>
      <div class="scan-current">${esc(job.current || job.folder)}</div><div class="scan-counts"><span>新增 ${job.added}</span><span>更新 ${job.updated}</span><span>已存在 ${job.skipped}</span><span>待识别 ${job.failed}</span></div>
      ${job.errors.length ? `<details class="scan-errors"><summary>查看 ${job.errors.length} 个未识别文件</summary>${job.errors.map(item => `<div class="scan-error"><code title="${esc(item.path)}">${esc(item.path)}</code>${esc(item.reason)}</div>`).join('')}</details>` : ''}
      ${!running ? '<button class="btn small ghost" style="margin-top:13px" onclick="dismissScanJob()">关闭</button>' : ''}
    </div>
  </div>`;
}

function toggleScanWidget() {
  scanMinimized = !scanMinimized;
  document.getElementById('scan-widget')?.classList.toggle('minimized', scanMinimized);
}

function dismissScanJob() {
  clearTimeout(scanPollTimer);
  scanRoot.innerHTML = '';
}

function openPlayer(fileId, episodeId, startAt = 0) {
  playerRoot.innerHTML = `<div class="modal-mask player-modal"><button class="player-close" onclick="closePlayer()">×</button><video id="player" controls autoplay src="/api/files/${fileId}/stream#t=${startAt || 0}"></video></div>`;
  document.body.classList.add('modal-open');
  const video = document.getElementById('player');
  const save = debounce(() => {
    api(`/api/files/${fileId}/progress`, { method: 'POST', body: { position_seconds: Math.floor(video.currentTime), duration_seconds: Math.floor(video.duration || 0) } }).catch(() => {});
  }, 2500);
  video.ontimeupdate = save;
  video.onpause = save;
  video.onended = () => {
    api(`/api/files/${fileId}/progress`, { method: 'POST', body: { position_seconds: Math.floor(video.currentTime), duration_seconds: Math.floor(video.duration || 0), watched: true } }).catch(() => {});
  };
}

function closePlayer() {
  const video = document.getElementById('player');
  if (video) video.onpause = null;
  playerRoot.innerHTML = '';
  if (!directoryRoot.innerHTML) document.body.classList.remove('modal-open');
  if (location.hash.startsWith('#/work/')) renderWork(Number(location.hash.split('/')[2])).catch(showPageError);
}

let localPollTimer = null;

async function playLocal(fileId) {
  try {
    const state = await api(`/api/files/${fileId}/local-play`, { method: 'POST' });
    renderLocalStatus(state);
    flash('已启动本地播放器，观看记录将自动同步');
    clearTimeout(localPollTimer);
    localPollTimer = setTimeout(pollLocalStatus, 1500);
  } catch (e) { flash(e.message, true); }
}

async function pollLocalStatus() {
  clearTimeout(localPollTimer);
  try {
    const state = await api('/api/player/local');
    renderLocalStatus(state);
    if (state.active) {
      localPollTimer = setTimeout(pollLocalStatus, 2000);
    } else {
      route();
      localPollTimer = setTimeout(() => { localRoot.innerHTML = ''; }, 8000);
    }
  } catch (e) { flash(e.message, true); }
}

function formatPlayTime(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  return `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`;
}

function renderLocalStatus(state) {
  if (!state.file_id) { localRoot.innerHTML = ''; return; }
  const pct = state.duration_seconds ? Math.min(100, Math.round(state.position_seconds / state.duration_seconds * 100)) : 0;
  localRoot.innerHTML = `<div class="scan-widget local-widget"><div class="scan-head"><strong>${state.active ? '本地播放器运行中' : '观看记录已同步'}</strong>${!state.active ? '<button onclick="dismissLocalStatus()">×</button>' : ''}</div>
    <div class="scan-body"><div class="scan-phase"><span>${esc(state.title || '')}</span><span>${formatPlayTime(state.position_seconds)}${state.duration_seconds ? ' / ' + formatPlayTime(state.duration_seconds) : ''}</span></div>
      <div class="scan-progress"><i style="width:${pct}%"></i></div><div class="scan-current">${state.active ? (state.sync_mode === 'realtime' ? '正在实时同步 VLC 播放位置' : '关闭播放器后同步本次观看时长') : '进度与已看状态已更新'}</div>
    </div></div>`;
}

function dismissLocalStatus() {
  clearTimeout(localPollTimer);
  localRoot.innerHTML = '';
}

function showPageError(error) {
  app.innerHTML = `<div class="page"><div class="empty">${esc(error.message)}</div></div>`;
}

function setActiveNav(routeName) {
  document.querySelectorAll('#nav a').forEach(a => a.classList.toggle('active', a.dataset.route === routeName));
}

function route() {
  const hash = location.hash.slice(1) || '/';
  const [path, qs] = hash.split('?');
  const params = new URLSearchParams(qs || '');
  const seg = path.split('/').filter(Boolean);
  document.body.classList.remove('home-route');
  document.getElementById('more-dropdown').classList.remove('open');
  let task;
  if (!seg.length) { setActiveNav('home'); task = renderHome(); }
  else if (seg[0] === 'movies') { setActiveNav('movies'); task = renderCollection(params, 'movie'); }
  else if (seg[0] === 'tv') { setActiveNav('tv'); task = renderCollection(params, 'tv'); }
  else if (seg[0] === 'search' && seg[1]) { params.set('q', decodeURIComponent(seg[1])); setActiveNav(''); task = renderCollection(params); }
  else if (seg[0] === 'tag' && seg[1]) { params.set('tag', decodeURIComponent(seg[1])); setActiveNav(''); task = renderCollection(params); }
  else if (seg[0] === 'collection') { setActiveNav(''); task = renderCollection(params); }
  else if (seg[0] === 'status' && seg[1]) { params.set('status', seg[1]); setActiveNav('status'); task = renderCollection(params); }
  else if (seg[0] === 'work' && seg[1]) { setActiveNav(''); task = renderWork(Number(seg[1])); }
  else if (seg[0] === 'edit' && seg[1]) { setActiveNav(''); task = renderEdit(Number(seg[1])); }
  else if (seg[0] === 'add') { setActiveNav(''); task = renderAdd(); }
  else if (seg[0] === 'stats') { setActiveNav(''); task = renderStats(); }
  else if (seg[0] === 'settings') { setActiveNav(''); task = renderSettings(); }
  else if (seg[0] === 'scan') { openDirectoryPicker(); return; }
  else { setActiveNav('home'); task = renderHome(); }
  task.catch(showPageError);
  window.scrollTo(0, 0);
}

document.getElementById('search-toggle').onclick = () => {
  document.getElementById('search-panel').classList.add('open');
  document.getElementById('global-q').focus();
};
document.getElementById('search-close').onclick = () => document.getElementById('search-panel').classList.remove('open');
document.getElementById('global-q').onkeydown = e => {
  if (e.key === 'Enter' && e.currentTarget.value.trim()) {
    document.getElementById('search-panel').classList.remove('open');
    location.hash = '#/search/' + encodeURIComponent(e.currentTarget.value.trim());
  }
};
document.getElementById('more-toggle').onclick = e => {
  e.stopPropagation();
  document.getElementById('more-dropdown').classList.toggle('open');
};
document.addEventListener('click', e => { if (!e.target.closest('.more-menu')) document.getElementById('more-dropdown').classList.remove('open'); });
window.addEventListener('scroll', () => document.getElementById('topbar').classList.toggle('solid', window.scrollY > 30));
window.addEventListener('hashchange', route);

const previousJob = localStorage.getItem('filmvault_scan_job');
if (previousJob) pollScan(previousJob);
api('/api/player/local').then(state => {
  if (state.active) { renderLocalStatus(state); pollLocalStatus(); }
}).catch(() => {});
route();
