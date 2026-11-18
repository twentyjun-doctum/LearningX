(() => {
  'use strict';
  const {el, LEVEL} = window.QU;
  const pdfBase = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@5.4.624/';
  let pdfLibrary;
  function loadPdf(bytes) {
    pdfLibrary ??= import(pdfBase + 'build/pdf.min.mjs').then(lib => {
      lib.GlobalWorkerOptions.workerSrc = pdfBase + 'build/pdf.worker.min.mjs';
      return lib;
    }).catch(err => {
      pdfLibrary = null;
      throw err;
    });
    return pdfLibrary.then(async lib => ({
      lib,
      doc: await lib.getDocument({
        data: bytes, isEvalSupported: false, useWasm: false, enableXfa: false,
        cMapUrl: pdfBase + 'cmaps/', cMapPacked: true, standardFontDataUrl: pdfBase + 'standard_fonts/'
      }).promise,
    }));
  }

  async function renderPage({lib, doc}, number, width) {
    const page = await doc.getPage(number);
    const natural = page.getViewport({scale: 1});
    const scale = Math.min(width / natural.width, innerHeight * .75 / natural.height);
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

  window.QU.createLiveView = ({api, session, panel, faculty = false, onState, onQuestion}) => {
    const page = panel.querySelector('[data-live-page]');
    const content = panel.querySelector('[data-live-content]');
    const index = panel.querySelector('[data-live-index]');
    const status = panel.querySelector('[data-live-status]');
    const path = '/v1/sessions/' + encodeURIComponent(session) + '/live';
    const frames = new Map();
    let deck = null;
    let slide = null;
    let selected = null;
    let displayed = null;
    let stopped = false;
    let timer;
    let indexKey = '';
    let stamp;
    let items = [];
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

    const width = () => Math.max(180, Math.floor(content.clientWidth || panel.clientWidth || 320));

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
      let frame;
      try {
        if (deck) {
          frame = await cached('deck:' + target.page + ':' + w, () => renderPage(deck, target.page, w));
        } else if (target.pdf) {
          frame = await cached('page:' + target.page + ':' + w, async () => {
            const res = await api(path + '/page/' + encodeURIComponent(target.version), {raw: true, timeout: 12000});
            const pdf = await loadPdf(await res.arrayBuffer());
            try {
              return await renderPage(pdf, 1, w);
            } finally {
              await pdf.doc.destroy();
            }
          });
        }
      } catch (err) {
        if (err.status === 409) return;
      }
      if (stopped || id !== renderId) return;
      if (target.image) frame ??= el('img', {src: 'data:image/jpeg;base64,' + target.image, alt: target.page + '쪽 교안'});
      frame ??= el('p', {class: 'live-slide-text', text: target.text || '표시할 텍스트가 없습니다.'});
      displayed = target.page;
      page.textContent = target.page + ' / ' + target.total;
      content.replaceChildren(frame);
      paintIndex();
      if (deck) {
        for (const number of [displayed + 1, displayed - 1]) {
          if (number > 0 && number <= deck.doc.numPages) {
            cached('deck:' + number + ':' + w, () => renderPage(deck, number, w)).catch(() => {});
          }
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
        if (changed) slide = data.slide;
        items = data.index;
        if (changed && (selected === null || data.status === 'CLOSED')) display(slide);
        if (selected === slide?.page) selected = null;
        paintIndex();
        status.textContent = slide ? '동기화 중' : '';
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
      if (!deck || stopped) return;
      const number = selected ?? slide?.page ?? displayed;
      if (number) display({page: number, total: deck.doc.numPages});
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
      display({page: number, total: deck.doc.numPages});
    }

    panel.hidden = false;
    page.textContent = '송출 대기';
    content.replaceChildren();
    index.replaceChildren();
    tick();
    if (faculty) {
      api(path + '/document', {raw: true}).then(res => res.arrayBuffer()).then(loadPdf).then(pdf => {
        if (stopped) return pdf.doc.destroy();
        deck = pdf;
        select(slide?.page || 1);
        if (!slide) selected = null;
        onState({slide, total: deck.doc.numPages});
      }).catch(() => {});
    }
    return {
      stop, select, page: () => displayed,
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
