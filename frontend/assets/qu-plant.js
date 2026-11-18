/* 콩나무: 질문 수준(seed · shallow · mid · deep)에 따라 자라는 SVG.
   디자인 캔버스 StudentLadder / StudentDeskLadder 보드의 도형을 그대로 옮겼습니다. */
(() => {
  'use strict';
  const NS = 'http://www.w3.org/2000/svg';
  const LEAF = 'M0 0 C 10 -12, 32 -12, 44 0 C 32 9, 10 9, 0 0 Z';
  const ST1 = 'M100 368 C 118 345.56, 82 323.12, 100 300';
  const ST2 = ' C 82 267.0, 118 234.0, 100 200';
  const ST3 = ' C 118 165.35, 82 130.7, 100 95';
  const FONT = "'QU Sans', Freesentation, 'Noto Sans KR', sans-serif";

  function s(tag, attrs, ...kids) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, String(v));
    kids.forEach((k) => n.append(k));
    return n;
  }
  const pair = (y1, y2, sc1 = 1, sc2 = 0.9) => [
    s('path', { d: LEAF, fill: '#6FD6A0', transform: `translate(104 ${y1}) rotate(-25) scale(${sc1})` }),
    s('path', { d: LEAF, fill: '#4CC08F', transform: `translate(96 ${y2}) rotate(205) scale(${sc2})` }),
  ];
  const bud = (y, sc) => [
    s('path', { d: LEAF, fill: '#6FD6A0', transform: `translate(103 ${y}) rotate(-25) scale(${sc})` }),
    s('path', { d: LEAF, fill: '#4CC08F', transform: `translate(97 ${y}) rotate(205) scale(${sc})` }),
  ];

  const STAGES = {
    seed: { stem: null, leaves: () => [] },
    shallow: { stem: ST1, leaves: () => [...pair(342, 324), ...bud(302, 0.55)] },
    mid: { stem: ST1 + ST2, leaves: () => [...pair(342, 324), ...pair(258, 240), ...bud(202, 0.55)] },
    deep: { stem: ST1 + ST2 + ST3, leaves: () => [...pair(342, 324), ...pair(258, 240), ...pair(155.5, 137.5), ...bud(99, 0.7)] },
  };

  /** @param {'seed'|'shallow'|'mid'|'deep'} stage  @param {{panel?:boolean,label?:string}} opt */
  function plant(stage, opt = {}) {
    const st = STAGES[stage] || STAGES.seed;
    const panel = !!opt.panel;
    const svg = s('svg', panel
      ? { viewBox: '0 0 200 420', role: 'img', 'aria-label': opt.label || '' }
      : { viewBox: '40 40 120 360', 'aria-hidden': 'true' });
    const cloud = stage === 'deep' && panel ? '#E3EDFB' : '#EEF3FA';
    svg.append(s('g', { 'aria-hidden': 'true' },
      s('ellipse', { cx: 100, cy: 62, rx: 56, ry: 18, fill: cloud }),
      s('ellipse', { cx: 74, cy: 54, rx: 26, ry: 16, fill: cloud }),
      s('ellipse', { cx: 124, cy: 50, rx: 30, ry: 20, fill: cloud })));
    if (panel) {
      for (const [y, t] of [[300, '얕은'], [200, '중간'], [95, '깊은']]) {
        svg.append(s('line', { x1: 4, y1: y, x2: 58, y2: y, stroke: '#127A66', 'stroke-width': 1, 'stroke-dasharray': '3 4' }));
        const label = s('text', { x: 4, y: y - 6, 'font-size': 12, 'font-weight': 700, fill: '#0D5C4C', 'font-family': FONT });
        label.textContent = t;
        svg.append(label);
      }
    }
    svg.append(
      s('path', { d: 'M0 372 Q 100 362 200 372 L 200 420 L 0 420 Z', fill: '#E7DFD2' }),
      s('path', { d: 'M0 372 Q 100 362 200 372', fill: 'none', stroke: '#CDBFA8', 'stroke-width': 1.5 }));
    if (!st.stem) {
      svg.append(s('ellipse', { cx: 100, cy: 366, rx: 10, ry: 7, fill: '#B98A55' }),
        s('path', { d: 'M100 360 C 99 354, 101 350, 100 346', fill: 'none', stroke: '#2FB07E', 'stroke-width': 4, 'stroke-linecap': 'round' }));
      return svg;
    }
    svg.append(
      s('path', { d: st.stem, fill: 'none', stroke: '#2FB07E', 'stroke-width': 7, 'stroke-linecap': 'round' }),
      s('path', { d: st.stem, fill: 'none', stroke: '#1E9A72', 'stroke-width': 1.5, 'stroke-linecap': 'round', opacity: 0.5 }),
      ...st.leaves());
    if (stage === 'deep') {
      for (const r of [0, 72, 144, 216, 288]) {
        svg.append(s('ellipse', { cx: 100, cy: 79, rx: 6, ry: 11, fill: '#FFB98F', transform: `rotate(${r} 100 87)` }));
      }
      svg.append(s('circle', { cx: 100, cy: 87, r: 6, fill: '#F0604A' }));
    }
    return svg;
  }

  window.QU = Object.assign(window.QU || {}, { plant });
})();
