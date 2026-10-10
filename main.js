/**
 * KAMUCL 插件 · 头像替换 (avatar-swap)
 *
 * 作者：spirit1132 (https://github.com/spirit1132)
 * 许可：MIT
 *
 * 把主界面左上角的头像（img.brand-head）替换为指定皮肤的头像。
 *
 * 两种模式：
 *   - follow : 跟随当前账号皮肤，自动从皮肤贴图裁出头部（含帽子/头发叠加层）
 *   - custom : 导入本地 PNG，皮肤尺寸则裁头部，其他尺寸整图使用
 *
 * 约束说明：启动动画（splash）的头像编译在 app.asar 内、运行在独立窗口，
 * 插件运行在主窗口渲染进程，无法触及。本插件只影响主界面头像。
 */

(function () {
  'use strict';

  const STORE_KEY = 'kamucl.avatar-swap.v1';
  const DEFAULT_FACE_SRC = null; // 首次运行时记录原图，用于"恢复默认"

  const bridge = window.kamucl;
  const api = window.kamuclPlugin;

  if (!bridge || !api) {
    console.warn('[avatar-swap] KAMUCL 插件 API 不可用，插件退出');
    return;
  }

  /* ---------------------------------------------------------------- 状态 */

  const state = {
    mode: 'follow',      // 'follow' | 'custom'
    customDataUrl: '',   // custom 模式下的头像 dataURL
    cachedFace: '',      // follow 模式最近一次取到的头像
    originalSrc: '',     // 原始头像（用于恢复）
    lastApplied: '',
    busy: false,
    error: ''
  };

  function loadState() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved && typeof saved === 'object') {
        if (saved.mode === 'follow' || saved.mode === 'custom') state.mode = saved.mode;
        if (typeof saved.customDataUrl === 'string') state.customDataUrl = saved.customDataUrl;
        if (typeof saved.cachedFace === 'string') state.cachedFace = saved.cachedFace;
      }
    } catch (err) {
      console.warn('[avatar-swap] 读取配置失败', err);
    }
  }

  function saveState() {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify({
        mode: state.mode,
        customDataUrl: state.customDataUrl,
        cachedFace: state.cachedFace
      }));
    } catch (err) {
      console.warn('[avatar-swap] 保存配置失败', err);
    }
  }

  /* ------------------------------------------------------- 皮肤头部裁切 */

  /** 皮肤贴图常见尺寸（宽 x 高） */
  const SKIN_SIZES = [
    [64, 32],   // 旧版
    [64, 64],   // 现代
    [128, 64],  // 高清旧版
    [128, 128], // 高清现代
    [256, 128],
    [256, 256],
    [512, 256],
    [512, 512],
    [1024, 512],
    [1024, 1024]
  ];

  function isSkinSize(w, h) {
    return SKIN_SIZES.some(([sw, sh]) => sw === w && sh === h);
  }

  function loadImage(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error('图片无法解码'));
      img.src = src;
    });
  }

  /**
   * 从皮肤贴图裁出头部。
   * 基准布局是 64x64：头部正面在 (8,8) 8x8，帽子叠加层在 (40,8) 8x8。
   * 旧版 64x32 没有帽子层。高清皮肤按比例放大。
   */
  function cropHeadFromSkin(img) {
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    if (!w || !h) throw new Error('皮肤尺寸无效');

    const unit = w / 64;                  // 每个像素单位对应的实际像素
    const modern = h >= w / 2 * 2 && h / unit >= 64; // 高度够 64 单位则有帽子层

    const OUT = 256;                      // 输出 256x256，与原头像规格一致
    const canvas = document.createElement('canvas');
    canvas.width = OUT;
    canvas.height = OUT;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;

    const sx = 8 * unit;
    const sy = 8 * unit;
    const size = 8 * unit;

    // 头部底色
    ctx.drawImage(img, sx, sy, size, size, 0, 0, OUT, OUT);

    // 帽子 / 头发叠加层（现代皮肤才有）
    if (modern) {
      ctx.drawImage(img, 40 * unit, 8 * unit, size, size, 0, 0, OUT, OUT);
    }

    return canvas.toDataURL('image/png');
  }

  /**
   * 把任意导入图转成头像。
   * 是皮肤尺寸就裁头部，否则整图缩放使用（保持像素风格）。
   */
  async function toAvatarDataUrl(dataUrl) {
    const img = await loadImage(dataUrl);
    const w = img.naturalWidth || img.width;
    const h = img.naturalHeight || img.height;
    if (isSkinSize(w, h)) return cropHeadFromSkin(img);

    const OUT = 256;
    const canvas = document.createElement('canvas');
    canvas.width = OUT;
    canvas.height = OUT;
    const ctx = canvas.getContext('2d');
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(img, 0, 0, OUT, OUT);
    return canvas.toDataURL('image/png');
  }

  /* ------------------------------------------------------------ DOM 操作 */

  function findAvatar() {
    return document.querySelector('img.brand-head');
  }

  function applyToDom(dataUrl) {
    const img = findAvatar();
    if (!img) return false;
    if (!state.originalSrc) state.originalSrc = img.getAttribute('src') || '';
    if (img.getAttribute('src') !== dataUrl) img.setAttribute('src', dataUrl);
    state.lastApplied = dataUrl;
    return true;
  }

  function restoreDom() {
    const img = findAvatar();
    if (!img) return;
    if (state.originalSrc) img.setAttribute('src', state.originalSrc);
    state.lastApplied = '';
  }

  /** 按当前模式把头像写到界面上 */
  function render() {
    if (state.mode === 'custom' && state.customDataUrl) {
      applyToDom(state.customDataUrl);
      return;
    }
    if (state.cachedFace) {
      applyToDom(state.cachedFace);
      return;
    }
    restoreDom();
  }

  /* ------------------------------------------------- 跟随账号皮肤拉取 */

  async function fetchAccountFace() {
    state.busy = true;
    state.error = '';
    paintPanel();
    try {
      const dataUrl = await bridge.invoke('skin:avatar');
      if (!dataUrl || typeof dataUrl !== 'string') {
        state.error = '当前账号没有可用皮肤（离线账号需先设置离线皮肤）';
        state.cachedFace = '';
      } else {
        state.cachedFace = await toAvatarDataUrl(dataUrl);
        state.error = '';
      }
      saveState();
    } catch (err) {
      state.error = '获取皮肤失败：' + (err && err.message ? err.message : String(err));
    } finally {
      state.busy = false;
      render();
      paintPanel();
    }
  }

  /* ---------------------------------------------------------- 面板界面 */

  let panel = null;
  let panelOpen = false;

  function el(tag, style, text) {
    const node = document.createElement(tag);
    if (style) Object.assign(node.style, style);
    if (text != null) node.textContent = text;
    return node;
  }

  const S = {
    btn: {
      position: 'fixed', zIndex: '2147483000', width: '26px', height: '26px',
      borderRadius: '8px', border: '1px solid rgba(255,255,255,.28)',
      background: 'rgba(23,32,33,.86)', color: '#f5f8f6', cursor: 'pointer',
      fontSize: '13px', lineHeight: '1', padding: '0', display: 'flex',
      alignItems: 'center', justifyContent: 'center', boxShadow: '0 3px 12px rgba(0,0,0,.28)',
      backdropFilter: 'blur(6px)'
    },
    panel: {
      position: 'fixed', zIndex: '2147483001', width: '268px', padding: '14px',
      borderRadius: '12px', border: '1px solid rgba(255,255,255,.2)',
      background: 'rgba(23,32,33,.94)', color: '#f5f8f6',
      font: '13px/1.5 "Segoe UI","Microsoft YaHei",sans-serif',
      boxShadow: '0 10px 34px rgba(0,0,0,.42)', backdropFilter: 'blur(10px)'
    },
    row: { display: 'flex', gap: '8px', marginTop: '10px' },
    action: {
      flex: '1', padding: '7px 8px', borderRadius: '8px',
      border: '1px solid rgba(255,255,255,.22)', background: 'rgba(255,255,255,.08)',
      color: '#f5f8f6', cursor: 'pointer', fontSize: '12px',
      fontFamily: 'inherit'
    },
    actionPrimary: { background: 'var(--accent, #2563eb)', borderColor: 'transparent', color: '#fff' },
    hint: { marginTop: '9px', fontSize: '11.5px', color: '#a9b6b8', lineHeight: '1.55' },
    status: { marginTop: '9px', fontSize: '11.5px', color: '#8fd3a8', minHeight: '16px' },
    statusErr: { color: '#ff9d9d' },
    title: { fontSize: '13px', fontWeight: '700', letterSpacing: '.2px' },
    chip: {
      display: 'inline-block', padding: '1px 7px', borderRadius: '999px',
      fontSize: '11px', marginLeft: '6px', background: 'rgba(255,255,255,.14)'
    }
  };

  function makeButton(label, primary) {
    const b = el('button', Object.assign({}, S.action, primary ? S.actionPrimary : {}), label);
    b.type = 'button';
    b.addEventListener('mouseenter', () => { b.style.filter = 'brightness(1.12)'; });
    b.addEventListener('mouseleave', () => { b.style.filter = ''; });
    return b;
  }

  function paintPanel() {
    if (!panel) return;
    const body = panel.querySelector('[data-body]');
    if (!body) return;
    body.textContent = '';

    const title = el('div', S.title);
    title.appendChild(document.createTextNode('头像替换'));
    const chip = el('span', S.chip, state.mode === 'follow' ? '跟随皮肤' : '自定义');
    title.appendChild(chip);
    body.appendChild(title);

    const hint = el('div', S.hint);
    hint.textContent = state.mode === 'follow'
      ? '自动使用当前账号皮肤裁出的头像（含帽子层）。'
      : '正在使用导入的图片作为头像。';
    body.appendChild(hint);

    const status = el('div', state.error ? Object.assign({}, S.status, S.statusErr) : S.status);
    if (state.busy) status.textContent = '正在获取皮肤…';
    else if (state.error) status.textContent = state.error;
    else if (state.mode === 'follow' && state.cachedFace) status.textContent = '✓ 已应用账号皮肤头像';
    else if (state.mode === 'custom' && state.customDataUrl) status.textContent = '✓ 已应用导入图片';
    else status.textContent = '';
    body.appendChild(status);

    const row1 = el('div', S.row);
    const bFollow = makeButton('跟随账号皮肤', state.mode === 'follow');
    bFollow.disabled = state.busy;
    bFollow.addEventListener('click', async () => {
      state.mode = 'follow';
      saveState();
      await fetchAccountFace();
    });
    row1.appendChild(bFollow);
    body.appendChild(row1);

    const row2 = el('div', S.row);
    const bImport = makeButton('导入 PNG', state.mode === 'custom');
    bImport.addEventListener('click', pickFile);
    row2.appendChild(bImport);

    const bRefresh = makeButton('刷新');
    bRefresh.disabled = state.busy;
    bRefresh.addEventListener('click', () => {
      if (state.mode === 'follow') fetchAccountFace();
      else { render(); paintPanel(); }
    });
    row2.appendChild(bRefresh);
    body.appendChild(row2);

    const row3 = el('div', S.row);
    const bReset = makeButton('恢复默认');
    bReset.addEventListener('click', () => {
      state.mode = 'follow';
      state.customDataUrl = '';
      state.cachedFace = '';
      state.error = '';
      saveState();
      restoreDom();
      paintPanel();
      if (api.toast) api.toast('已恢复默认头像', 'success');
    });
    row3.appendChild(bReset);
    body.appendChild(row3);

    const tip = el('div', S.hint);
    tip.textContent = '提示：启动动画的头像在 app.asar 内，插件无法修改；如需替换请使用配套的补丁工具。';
    body.appendChild(tip);
  }

  function pickFile() {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/*';
    input.style.display = 'none';
    input.addEventListener('change', async () => {
      const file = input.files && input.files[0];
      if (!file) return;
      try {
        const dataUrl = await new Promise((resolve, reject) => {
          const fr = new FileReader();
          fr.onload = () => resolve(String(fr.result));
          fr.onerror = () => reject(new Error('读取文件失败'));
          fr.readAsDataURL(file);
        });
        const avatar = await toAvatarDataUrl(dataUrl);
        state.mode = 'custom';
        state.customDataUrl = avatar;
        state.error = '';
        saveState();
        render();
        paintPanel();
        if (api.toast) api.toast('头像已替换', 'success');
      } catch (err) {
        state.error = '导入失败：' + (err && err.message ? err.message : String(err));
        paintPanel();
      }
    });
    document.body.appendChild(input);
    input.click();
    setTimeout(() => input.remove(), 1000);
  }

  function ensureToggleButton() {
    if (panel) return;
    const btn = el('button', S.btn, '◧');
    btn.type = 'button';
    btn.title = '头像替换设置';
    btn.setAttribute('data-avatar-swap', 'toggle');
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      panelOpen = !panelOpen;
      if (panel) panel.style.display = panelOpen ? 'block' : 'none';
      if (panelOpen) positionPanel();
    });
    document.body.appendChild(btn);

    panel = el('div', S.panel);
    panel.setAttribute('data-avatar-swap', 'panel');
    panel.style.display = 'none';
    const body = el('div');
    body.setAttribute('data-body', '');
    panel.appendChild(body);
    panel.addEventListener('click', (ev) => ev.stopPropagation());
    document.body.appendChild(panel);

    positionToggle();
    paintPanel();

    window.addEventListener('resize', () => { positionToggle(); if (panelOpen) positionPanel(); });
  }

  /** 把小按钮贴到头像右侧 */
  function positionToggle() {
    const btn = document.querySelector('[data-avatar-swap="toggle"]');
    const img = findAvatar();
    if (!btn) return;
    if (!img) {
      btn.style.left = '16px';
      btn.style.top = '16px';
      return;
    }
    const r = img.getBoundingClientRect();
    if (!r.width) {
      btn.style.left = '16px';
      btn.style.top = '16px';
      return;
    }
    btn.style.left = Math.round(r.right + 6) + 'px';
    btn.style.top = Math.round(r.top + (r.height - 26) / 2) + 'px';
  }

  function positionPanel() {
    if (!panel) return;
    const btn = document.querySelector('[data-avatar-swap="toggle"]');
    const anchor = btn || findAvatar();
    if (!anchor) return;
    const r = anchor.getBoundingClientRect();
    const top = Math.min(window.innerHeight - 300, Math.max(8, r.bottom + 8));
    const left = Math.min(window.innerWidth - 284, Math.max(8, r.left - 8));
    panel.style.left = Math.round(left) + 'px';
    panel.style.top = Math.round(top) + 'px';
  }

  /* ------------------------------------------------ 维持替换不被覆盖 */

  /**
   * Vue 重渲染会把 img 的 src 还原，这里持续把替换后的头像贴回去。
   * 同时跟随头像位置调整小按钮。
   */
  function guard() {
    if (state.lastApplied) {
      const img = findAvatar();
      if (img && img.getAttribute('src') !== state.lastApplied) {
        img.setAttribute('src', state.lastApplied);
      }
    }
    positionToggle();
    if (panelOpen) positionPanel();
  }

  /* ------------------------------------------------------------ 启动 */

  async function boot() {
    loadState();
    ensureToggleButton();

    // 等主界面头像出现
    let tries = 0;
    const waitAvatar = setInterval(() => {
      tries += 1;
      const img = findAvatar();
      if (img) {
        clearInterval(waitAvatar);
        if (!state.originalSrc) state.originalSrc = img.getAttribute('src') || '';
        render();
        paintPanel();
      } else if (tries > 100) {
        clearInterval(waitAvatar);
      }
    }, 100);

    // 首次跟随模式且没有缓存时自动拉取一次
    if (state.mode === 'follow' && !state.cachedFace) {
      setTimeout(() => { void fetchAccountFace(); }, 600);
    }

    setInterval(guard, 500);
    console.info('[avatar-swap] 头像替换插件已加载');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    void boot();
  }
})();
