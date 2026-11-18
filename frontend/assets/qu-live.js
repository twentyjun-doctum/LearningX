(() => {
  'use strict';
  const {el, LEVEL} = window.QU;
  window.QU.createLiveView = ({api, session, panel, onState, onQuestion}) => {
    const page = panel.querySelector('[data-live-page]');
    const content = panel.querySelector('[data-live-content]');
    const index = panel.querySelector('[data-live-index]');
    const status = panel.querySelector('[data-live-status]');
    let slide = null;
    let stopped = false;
    let timer;
    let indexKey = '';

    async function tick() {
      try {
        const version = slide?.version || '';
        const data = await api('/v1/sessions/' + encodeURIComponent(session) + '/live?version=' + encodeURIComponent(version), {timeout: 8000});
        if (stopped) return;
        if (data.slide?.version !== slide?.version) {
          const snapshot = data.slide;
          content.replaceChildren();
          if (snapshot?.image) content.append(el('img', {src: 'data:image/jpeg;base64,' + snapshot.image, alt: snapshot.page + '쪽 교안'}));
          else if (snapshot) content.append(el('p', {class: 'live-slide-text', text: snapshot.text || '표시할 텍스트가 없습니다.'}));
          page.textContent = snapshot ? snapshot.page + ' / ' + snapshot.total : '송출 대기';
        }
        slide = data.slide;
        const items = data.index.filter(item => item.page === slide?.page);
        const key = JSON.stringify(items);
        if (key !== indexKey) {
          indexKey = key;
          index.replaceChildren(...items.map(item => el('button', {
            type: 'button', class: 'live-tab ' + item.level,
            'aria-label': LEVEL[item.level] + ' 질문', title: LEVEL[item.level] + ' 질문',
            onclick: () => onQuestion(item.id)
          }, LEVEL[item.level])));
        }
        status.textContent = slide ? '동기화 중' : '';
        onState(data);
        if (data.status === 'CLOSED') stop();
      } catch (err) {
        if (stopped) return;
        status.textContent = '연결을 다시 확인하는 중입니다.';
      } finally {
        if (!stopped) timer = setTimeout(tick, 2000);
      }
    }
    function stop() {
      stopped = true;
      clearTimeout(timer);
    }
    panel.hidden = false;
    page.textContent = '송출 대기';
    content.replaceChildren();
    index.replaceChildren();
    tick();
    return {stop, page: () => slide?.page || null};
  };
})();
