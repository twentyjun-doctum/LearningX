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

  window.QU.createLiveView = ({api, session, panel, faculty = false, initial, localFile, onState, onQuestion, onAnchor}) => {
    const page = panel.querySelector('[data-live-page]');
    const content = panel.querySelector('[data-live-content]');
    const index = panel.querySelector('[data-live-index]');
    const status = panel.querySelector('[data-live-status]');
    const path = '/v1/sessions/' + encodeURIComponent(session) + '/live';
    const frames = new Map();
    const pageFiles = new Map();
    const downloads = new Map();
    const pageCacheLimit = 24 * 1024 * 1024;
    let retainedBytes = 0;
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
    let indexVersion;
    let items = initial?.index ?? [];
    let renderId = 0;

    function cached(key, make) {
      if (frames.has(key)) {
        const existing = frames.get(key);
        frames.delete(key);
        frames.set(key, existing);
        return existing;
      }
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

    const feedback = panel.querySelector('[data-live-feedback]');
    const confusedButton = panel.querySelector('[data-live-confused]');
    const askButton = panel.querySelector('[data-live-ask]');
    const confusionSummary = panel.querySelector('[data-live-confusion]');
    const strip = panel.querySelector('[data-live-strip]');
    let confusion;
    let picked;
    let marksKey = '';
    let open = initial?.status === 'OPEN';
    let sendingConfusion = false;

    function pickText() {
      const selection = window.getSelection();
      const frame = content.querySelector('.live-pdf');
      if (!selection || selection.isCollapsed || !selection.rangeCount || !frame) return;
      const range = selection.getRangeAt(0);
      if (!frame.contains(range.startContainer) || !frame.contains(range.endContainer)) return;
      const area = frame.getBoundingClientRect();
      const hit = range.getBoundingClientRect();
      if (!area.width || !area.height) return;
      const clamp = value => Math.max(0, Math.min(1, value));
      picked = {x: clamp((hit.left + hit.width / 2 - area.left) / area.width),
        y: clamp((hit.top + hit.height / 2 - area.top) / area.height),
        quote: selection.toString().trim().slice(0, 240)};
      if (feedback) feedback.textContent = picked.quote ? '선택: “' + picked.quote + '”' : '';
      paintIndex();
    }

    function pickPoint(event) {
      if (window.getSelection()?.toString().trim()) return;
      const frame = event.target.closest('.live-pdf');
      if (!frame) return;
      const area = frame.getBoundingClientRect();
      picked = {x: (event.clientX - area.left) / area.width, y: (event.clientY - area.top) / area.height};
      if (picked.x < 0 || picked.x > 1 || picked.y < 0 || picked.y > 1) {
        picked = null;
        return;
      }
      feedback.textContent = '교안에서 위치를 선택했어요.';
      paintIndex();
    }

    function sendConfusion() {
      pickText();
      const target = displayed;
      if (!target || !open || sendingConfusion) return;
      sendingConfusion = true;
      confusedButton.disabled = true;
      api(path + '/confusion', {method: 'PUT', body: {page: target, anchor: picked}}).then(() => {
        if (displayed === target) feedback.textContent = '헷갈리는 부분을 전달했어요.';
      }).catch(err => {
        if (displayed === target) feedback.textContent = err.status === 409 ? '페이지가 바뀌었어요. 다시 선택해 주세요.' : err.message;
      }).finally(() => { sendingConfusion = false; confusedButton.disabled = !open || !displayed; });
    }

    function askHere() {
      pickText();
      if (!displayed || !open) return;
      if (!onAnchor?.({page: displayed, anchor: picked})) {
        feedback.textContent = '지금 작성 중인 질문을 먼저 마무리해 주세요.';
        return;
      }
      feedback.textContent = displayed + '쪽에 질문을 붙여요.';
    }

    if (!faculty) {
      document.addEventListener('selectionchange', pickText);
      content.addEventListener('click', pickPoint);
      confusedButton?.addEventListener('click', sendConfusion);
      askButton?.addEventListener('click', askHere);
    }

    function paintIndex() {
      const visible = items.filter(item => item.page === displayed);
      const key = JSON.stringify([displayed, visible, strip ? items.map(({id, level, page}) => [id, level, page]) : null]);
      if (key !== indexKey) {
        indexKey = key;
        index.replaceChildren(...visible.map(item => el('button', {
          type: 'button', class: 'live-tab ' + item.level,
          'aria-label': LEVEL[item.level] + ' 질문: ' + (item.text || ''),
          onclick: () => onQuestion(item.id)
        }, el('span', {class: 'live-tab-label', text: LEVEL[item.level]}),
        el('span', {class: 'live-tab-preview'},
          item.anchor?.quote ? el('small', {text: '“' + item.anchor.quote + '”'}) : null,
          el('span', {text: item.text || LEVEL[item.level] + ' 질문'})))));
        if (strip) {
          const groups = new Map();
          for (const item of items) {
            if (!groups.has(item.page)) groups.set(item.page, []);
            groups.get(item.page).push(item);
          }
          strip.replaceChildren(...Array.from(groups).sort(([a], [b]) => a - b).map(([number, questions]) =>
            el('div', {class: 'live-strip-page'}, el('span', {text: number + '쪽'}),
              ...questions.map(item => el('button', {type: 'button', class: 'live-tab ' + item.level,
                'aria-label': number + '쪽 ' + LEVEL[item.level] + ' 질문', onclick: () => onQuestion(item.id)})))));
        }
      }
      const current = displayed === slide?.page ? confusion : null;
      const markState = JSON.stringify([displayed, current, picked, visible.map(item => [item.id, item.level, item.anchor])]);
      const frame = content.querySelector('.live-pdf');
      if (confusionSummary) confusionSummary.textContent = displayed ? '헷갈려요 · ' + (current?.count ?? 0) + '명' : '';
      if (confusedButton) {
        confusedButton.textContent = current?.mine ? '여기가 헷갈려요 ✓' : '여기가 헷갈려요';
        confusedButton.disabled = !displayed || !open || sendingConfusion;
        askButton.disabled = !displayed || !open;
      }
      if (!frame || markState === marksKey) return;
      marksKey = markState;
      frame.querySelector('.live-marks')?.remove();
      const layer = el('div', {class: 'live-marks', 'aria-hidden': 'true'});
      const pins = [...(current?.spots ?? []).map(spot => ({...spot, level: 'confused'})),
        ...visible.filter(item => item.anchor).map(item => ({...item.anchor, level: item.level}))];
      if (picked) pins.push({...picked, level: 'picked'});
      for (const pin of pins) {
        const marker = el('span', {class: 'live-mark ' + pin.level, text: pin.count ?? ''});
        marker.style.left = (pin.x * 100) + '%';
        marker.style.top = (pin.y * 100) + '%';
        layer.append(marker);
      }
      frame.append(layer);
    }

    function pageBytes(target, number) {
      const retained = pageFiles.get(number);
      if (retained) {
        pageFiles.delete(number);
        pageFiles.set(number, retained);
        return Promise.resolve(retained);
      }
      if (downloads.has(number)) return downloads.get(number);
      const url = path + '/page/' + encodeURIComponent(target.version) + '?page=' + number;
      const pending = api(url, {raw: true, timeout: 12000}).then(res => res.arrayBuffer()).then(bytes => {
        if (!stopped && bytes.byteLength <= pageCacheLimit) {
          while (retainedBytes + bytes.byteLength > pageCacheLimit || pageFiles.size >= 8) {
            const oldest = pageFiles.keys().next().value;
            retainedBytes -= pageFiles.get(oldest).byteLength;
            pageFiles.delete(oldest);
          }
          pageFiles.set(number, bytes);
          retainedBytes += bytes.byteLength;
        }
        return bytes;
      }).finally(() => downloads.delete(number));
      downloads.set(number, pending);
      return pending;
    }

    function pageFrame(target, number, w, h, onBytes) {
      return cached('page:' + number + ':' + w + ':' + h, async () => {
        const bytes = await pageBytes(target, number);
        onBytes?.();
        // PDF.js transfers the input to its worker. Keep the cached bytes intact.
        const pdf = await loadPdf(bytes.slice(0));
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
      const preloadNext = () => {
        if (!faculty && !stopped && id === renderId && target.after) {
          pageBytes(target, target.after).catch(() => {});
        }
      };
      try {
        if (deck) {
          frame = await cached('deck:' + target.page + ':' + w + ':' + h, () => renderPage(deck, target.page, w, h));
        } else if (target.pdf && (!faculty || !localFile)) {
          try {
            frame = await pageFrame(target, target.page, w, h, preloadNext);
          } catch (err) {
            if (err.status !== 409 || stopped || id !== renderId) throw err;
            // An in-flight preload may have used the previous slide version.
            frame = await pageFrame(target, target.page, w, h, preloadNext);
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
      if (displayed !== target.page) {
        picked = null;
        if (feedback) feedback.textContent = '';
      }
      displayed = target.page;
      page.textContent = target.page + ' / ' + target.total;
      status.textContent = '동기화 중';
      content.replaceChildren(frame);
      marksKey = '';
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
        if (indexVersion) params.set('index_version', indexVersion);
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
        indexVersion = data.index_version;
        if (data.index) items = data.index;
        open = data.status === 'OPEN';
        confusion = data.confusion;
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
      document.removeEventListener('selectionchange', pickText);
      content.removeEventListener('click', pickPoint);
      confusedButton?.removeEventListener('click', sendConfusion);
      askButton?.removeEventListener('click', askHere);
      window.removeEventListener('resize', resize);
      document.removeEventListener('fullscreenchange', resize);
      ++renderId;
      deck?.doc.destroy();
      frames.clear();
      pageFiles.clear();
      downloads.clear();
      retainedBytes = 0;
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
