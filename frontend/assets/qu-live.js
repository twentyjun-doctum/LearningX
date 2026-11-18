(() => {
  'use strict';
  const {el, LEVEL} = window.QU;
  const pdfBase = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.624/';
  let pdfLibrary;
  function warmPdf() {
    pdfLibrary ??= import(pdfBase + 'build/pdf.min.mjs').then(lib => {
      lib.GlobalWorkerOptions.workerSrc = pdfBase + 'build/pdf.worker.min.mjs';
      return lib;
    }).catch(err => {
      pdfLibrary = null;
      throw err;
    });
    return pdfLibrary;
  }

  function loadPdf(bytes) {
    return warmPdf().then(async lib => ({
      lib,
      doc: await lib.getDocument({
        data: bytes, isEvalSupported: false, useWasm: false, enableXfa: false,
        cMapUrl: pdfBase + 'cmaps/', cMapPacked: true, standardFontDataUrl: pdfBase + 'standard_fonts/'
      }).promise,
    }));
  }

  async function renderPage({lib, doc}, number, width, height) {
    const page = await doc.getPage(number);
    const natural = page.getViewport({scale: 1});
    const scale = Math.min(width / natural.width, height / natural.height);
    const viewport = page.getViewport({scale});
    const ratio = Math.min(devicePixelRatio || 1, 2);
    const canvas = el('canvas', {width: Math.ceil(viewport.width * ratio), height: Math.ceil(viewport.height * ratio),
      role: 'img', 'aria-label': number + '쪽 교안'});
    const frame = el('div', {class: 'live-pdf'}, canvas);
    frame.style.width = viewport.width + 'px';
    frame.style.setProperty('--total-scale-factor', String(viewport.scale * (viewport.userUnit || 1)));
    const text = el('div', {class: 'live-pdf-text textLayer'});
    frame.append(text);
    await Promise.all([
      page.render({canvas, viewport, transform: [ratio, 0, 0, ratio, 0, 0]}).promise,
      new lib.TextLayer({textContentSource: page.streamTextContent(), container: text, viewport}).render(),
    ]);
    return frame;
  }

  // Start loading the renderer during entry/review so opening a slide can overlap
  // authentication and network I/O with module loading.
  warmPdf().catch(() => {});

  window.QU.createLiveView = ({api, session, panel, faculty = false, initial, localFile, onState, onQuestion}) => {
    const page = panel.querySelector('[data-live-page]');
    const content = panel.querySelector('[data-live-content]');
    const index = panel.querySelector('[data-live-index]');
    const status = panel.querySelector('[data-live-status]');
    const path = '/v1/sessions/' + encodeURIComponent(session) + '/live';
    const frames = new Map();
    let deck = null;
    let slide = initial?.slide ?? null;
    let selected = null;
    let navigation = null;
    let visiblePages = slide?.visible_pages ?? [];
    let displayed = null;
    let stopped = false;
    let timer;
    let indexKey = '';
    let stamp;
    let items = initial?.index ?? [];
    let renderId = 0;

    function cached(key, make) {
      if (frames.has(key)) return frames.get(key);
      const frame = make().catch(err => {
        if (frames.get(key) === frame) frames.delete(key);
        throw err;
      });
      frames.set(key, frame);
      if (frames.size > 8) frames.delete(frames.keys().next().value);
      return frame;
    }

    const width = () => Math.max(1, Math.floor(content.clientWidth || panel.clientWidth || 320));
    const height = () => Math.floor(document.fullscreenElement === panel ? innerHeight : innerHeight * .75);

    function paintIndex() {
      const visible = items.filter(item => item.page === displayed);
      const key = JSON.stringify(visible);
      if (key === indexKey) return;
      indexKey = key;
      index.replaceChildren(...visible.map(item => el('button', {
        type: 'button', class: 'live-tab ' + item.level,
        'aria-label': LEVEL[item.level] + ' 질문', title: LEVEL[item.level] + ' 질문',
        onclick: () => onQuestion(item.id)
      }, LEVEL[item.level])));
    }

    function pageFrame(target, number, w, h) {
      return cached('page:' + number + ':' + w + ':' + h, async () => {
        const url = path + '/page/' + encodeURIComponent(target.version) + '?page=' + number;
        const res = await api(url, {raw: true, timeout: 12000});
        const pdf = await loadPdf(await res.arrayBuffer());
        try {
          return await renderPage(pdf, 1, w, h);
        } finally {
          await pdf.doc.destroy();
        }
      });
    }

    async function display(target) {
      const id = ++renderId;
      if (!target) {
        displayed = null;
        page.textContent = '송출 대기';
        content.replaceChildren();
        paintIndex();
        return;
      }
      const w = width();
      const h = height();
      let frame;
      try {
        if (deck) {
          frame = await cached('deck:' + target.page + ':' + w + ':' + h, () => renderPage(deck, target.page, w, h));
        } else if (target.pdf && (!faculty || !localFile)) {
          try {
            frame = await pageFrame(target, target.page, w, h);
          } catch (err) {
            if (err.status !== 409 || stopped || id !== renderId) throw err;
            // An in-flight preload may have used the previous slide version.
            frame = await pageFrame(target, target.page, w, h);
          }
        }
      } catch (err) {
        if (stopped || id !== renderId || err.status === 409) return;
        status.textContent = '교안 화면을 불러오지 못했습니다.';
        return;
      }
      if (stopped || id !== renderId) return;
      if (faculty && !frame) return;
      if (target.image) frame ??= el('img', {src: 'data:image/jpeg;base64,' + target.image, alt: target.page + '쪽 교안'});
      frame ??= el('p', {class: 'live-slide-text', text: target.text || '교안 화면을 준비하는 중입니다.'});
      displayed = target.page;
      page.textContent = target.page + ' / ' + target.total;
      status.textContent = '동기화 중';
      content.replaceChildren(frame);
      paintIndex();
      if (deck) {
        for (const number of [target.after ?? displayed + 1, target.before ?? displayed - 1]) {
          if (number > 0 && number <= deck.doc.numPages) {
            cached('deck:' + number + ':' + w + ':' + h, () => renderPage(deck, number, w, h)).catch(() => {});
          }
        }
      } else if (!faculty && target.pdf) {
        for (const number of [target.after, target.before]) {
          if (number) pageFrame(target, number, w, h).catch(() => {});
        }
      }
    }

    async function tick() {
      let delay = 0;
      try {
        const params = new URLSearchParams({version: slide?.version || ''});
        if (stamp !== undefined) params.set('after', stamp);
        const data = await api(path + '?' + params, {timeout: 10000});
        if (stopped) return;
        stamp = data.revision;
        if (stamp === undefined) delay = 2000;
        const changed = slide?.version !== data.slide?.version;
        if (changed) {
          slide = data.slide;
          if (slide?.visible_pages) visiblePages = slide.visible_pages;
          if (selected === null || slide?.page === selected) navigation = slide;
        }
        items = data.index;
        if (changed && (selected === null || data.status === 'CLOSED')) display(slide);
        if (selected === slide?.page) selected = null;
        paintIndex();
        if (!displayed && status.textContent === '') status.textContent = '교안 화면을 준비하는 중입니다.';
        onState(data);
        if (data.status === 'CLOSED') stop();
      } catch (err) {
        if (stopped) return;
        status.textContent = '연결을 다시 확인하는 중입니다.';
        delay = 1200;
      } finally {
        if (!stopped) timer = setTimeout(tick, delay);
      }
    }

    function resize() {
      if (stopped || !slide) return;
      if (deck && selected) display({page: selected, total: deck.doc.numPages});
      else display(slide);
    }

    if (faculty) {
      window.addEventListener('resize', resize);
      document.addEventListener('fullscreenchange', resize);
    }

    function stop() {
      stopped = true;
      clearTimeout(timer);
      window.removeEventListener('resize', resize);
      document.removeEventListener('fullscreenchange', resize);
      ++renderId;
      deck?.doc.destroy();
      frames.clear();
    }

    function select(number) {
      if (!deck) return;
      selected = number;
      const position = visiblePages.indexOf(number);
      display({page: number, total: deck.doc.numPages,
        before: visiblePages[position - 1], after: visiblePages[position + 1]});
    }

    panel.hidden = false;
    page.textContent = '송출 대기';
    content.replaceChildren();
    index.replaceChildren();
    status.textContent = '교안 화면을 준비하는 중입니다.';
    if (slide && (!faculty || !localFile)) display(slide);
    tick();
    if (faculty) {
      const remote = () => api(path + '/document', {raw: true}).then(res => res.arrayBuffer()).then(loadPdf);
      const loading = localFile ? localFile.arrayBuffer().then(loadPdf).catch(remote) : remote();
      loading.then(pdf => {
        if (stopped) return pdf.doc.destroy();
        deck = pdf;
        if (slide && displayed !== slide.page) display(slide);
        onState({slide, total: deck.doc.numPages});
      }).catch(() => { if (!stopped) status.textContent = '원본 교안 화면을 불러오지 못했습니다. 수업에 다시 접속해 주세요.'; });
    }
    return {
      stop, select, page: () => displayed,
      acknowledge: result => { if (!stopped && (selected === null || selected === result.page)) navigation = result; },
      neighbor: delta => {
        if (!deck) return null;
        const current = selected ?? slide?.page ?? displayed;
        if (current === null) return null;
        if (visiblePages.length) return visiblePages[visiblePages.indexOf(current) + delta];
        if (navigation?.page === current && navigation.pdf) return delta < 0 ? navigation.before : navigation.after;
        const next = current + delta;
        return next >= 1 && next <= deck.doc.numPages ? next : null;
      },
      target: () => selected ?? slide?.page ?? displayed,
      total: () => deck?.doc.numPages ?? slide?.total,
      reconcile: () => {
        if (!stopped) {
          selected = null;
          display(slide || (deck && {page: 1, total: deck.doc.numPages}));
        }
      },
    };
  };
})();
