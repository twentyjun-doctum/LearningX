/* Question-up unit learning objective panel (faculty review/approval step).
 * Order follows backward design: objective -> evidence to observe -> coaching.
 * Pure helpers live on window.QU.objectiveRules so they can be tested alone. */
(() => {
  'use strict';
  const { el } = window.QU;

  const PROCESSES = [
    ['remember', '기억', '핵심 사실이나 용어를 확인한다.', '모형의 구성 요소 두 가지를 식별한다.'],
    ['understand', '이해', '의미나 관계를 자신의 말로 설명한다.', '두 개념을 예시로 구분한다.'],
    ['apply', '적용', '개념이나 절차를 주어진 사례에 사용한다.', '사례에 적용할 공식을 고르고 계산한다.'],
    ['analyze', '분석', '부분·관계·가정을 구분한다.', '두 자료의 차이를 찾아 가능한 설명을 나눈다.'],
    ['evaluate', '평가', '명시한 기준으로 주장이나 방법을 판단한다.', '신뢰성 기준을 제시하고 자료로 판단을 정당화한다.'],
    ['create', '창안', '목적과 제약에 맞는 새 방안이나 산출물을 구성한다.', '대안을 제안하고 제약을 어떻게 충족하는지 설명한다.'],
  ];
  const SUPPORT = [['minimal', '힌트 최소'], ['directional', '방향 제시'], ['stepwise', '단계별 안내']];
  const KNOWLEDGE = [['', '선택 안 함'], ['factual', '사실'], ['conceptual', '개념'], ['procedural', '절차'], ['metacognitive', '메타인지']];
  const CONTEXT = [['', '선택 안 함'], ['within_lesson', '교안 내부'], ['similar_case', '유사 사례'], ['new_situation', '새로운 상황']];
  const KIND = { goal: '추가 목표', evidence: '관찰 증거', guideline: '코칭 지침' };
  const REASON = {
    answer_disclosure: '정답·해설 공개 요청은 적용하지 않아요.',
    grading: '성적·점수 관련 지시는 적용하지 않아요.',
    permission_change: '권한 변경은 적용하지 않아요.',
    unapproved_material: '승인되지 않은 자료 사용은 적용하지 않아요.',
    evidence_unclear: '기타 입력에서 학생이 무엇을 어떻게 보여줄지 적고 다시 해석해 주세요. 예: 두 자료의 차이를 근거 하나로 설명한다.',
  };
  const UNASSESSED_REASON = { model_not_configured: 'AI 모델이 설정되지 않음', guest_model_disabled: '체험 수업은 AI 모델을 쓰지 않음',
    model_timeout: 'AI 응답 시간 초과', model_validation_or_transport_failed: 'AI 응답 검증 실패',
    no_matching_approved_evidence: '일치하는 승인 근거 없음', no_matching_approved_evidence_on_page: '그 쪽에 일치하는 승인 근거 없음',
    explicit_page_not_approved: '승인되지 않은 쪽 지정' };
  const RELEVANCE = { direct: '목표에 직접 연결', prerequisite: '선행 이해에 도움', out_of_unit: '단원 밖',
    insufficient_context: '맥락 부족' };
  const WARNING = { objective_not_observable: '학습목표가 관찰하기 어려운 표현이에요. 학생이 말하거나 수행할 행동으로 바꿔 보세요.',
    evidence_not_observable: '수행 증거가 관찰하기 어려운 표현이에요.' };
  const label = (table, key) => (table.find(([k]) => k === key) || [])[1] || '';

  /* ---------- pure rules (tested in smoke.mjs) ---------- */
  const rules = {
    PROCESSES, SUPPORT, REASON, RELEVANCE, UNASSESSED_REASON,
    emptyDraft: () => ({ objective: '', process: null, evidence: '', support: 'directional',
      knowledge_type: '', application_context: '', other_raw: '', other_items: [] }),
    fromProfile(profile) {
      if (!profile) return rules.emptyDraft();
      return { objective: profile.objective, process: profile.process, evidence: profile.evidence,
        support: profile.support, knowledge_type: profile.knowledge_type || '',
        application_context: profile.application_context || '', other_raw: profile.other_raw || '',
        other_items: (profile.other_items || []).map(({ kind, text }) => ({ kind, text })) };
    },
    // Request body for POST /objective-profiles; optional choices are omitted when unset.
    payload(draft) {
      const body = { objective: draft.objective.trim(), process: draft.process, evidence: draft.evidence.trim(),
        support: draft.support, other_items: draft.other_items.map(({ kind, text }) => ({ kind, text: text.trim() }))
          .filter((item) => item.text) };
      if (draft.knowledge_type) body.knowledge_type = draft.knowledge_type;
      if (draft.application_context) body.application_context = draft.application_context;
      if (draft.other_raw.trim()) body.other_raw = draft.other_raw.trim();
      return body;
    },
    // Spec 2.1: setting a profile needs objective, process and evidence together.
    missing(draft) {
      const missing = [];
      if (draft.objective.trim().length < 5) missing.push('학습목표');
      if (!draft.process) missing.push('목표 사고 과정');
      if (draft.evidence.trim().length < 5) missing.push('확인할 수행 증거');
      return missing;
    },
    same: (a, b) => JSON.stringify(rules.payload(a)) === JSON.stringify(rules.payload(b)),
    summary(profile) {
      if (!profile) return '미설정 · 기존 코칭 흐름을 사용합니다';
      return `v${profile.version} · ${label(PROCESSES, profile.process)} · ${label(SUPPORT, profile.support)} · ${profile.objective}`;
    },
  };

  /* ---------- panel ---------- */
  function createObjectivePanel({ api, panel, onChange }) {
    const body = panel.querySelector('[data-objective-body]');
    const summary = panel.querySelector('[data-objective-summary]');
    const status = panel.querySelector('[data-objective-status]');
    let materialId = null;
    let saved = null;
    let draft = rules.emptyDraft();
    let interpretation = null;
    let preview = null;
    let previewQuestion = '';
    let busy = false;
    let token = 0;

    const say = (text) => { status.textContent = text || ''; };
    const changed = () => { render(); onChange?.(); };
    const set = (patch) => { Object.assign(draft, patch); changed(); };
    const uid = (name) => `obj-${name}`;

    function radioGroup(name, legend, options, current, onPick, describe) {
      return el('fieldset', { class: 'obj-steps', 'aria-describedby': describe ? uid(name + '-desc') : undefined },
        el('legend', { text: legend }),
        el('div', { class: 'obj-step-row' }, ...options.map(([value, text]) => el('label', { class: 'obj-step' + (current === value ? ' is-on' : '') },
          el('input', { type: 'radio', id: uid(`${name}-${value}`), name: uid(name), value, checked: current === value,
            onchange: () => onPick(value) }),
          el('span', { text: (current === value ? '✓ ' : '') + text })))),
        el('p', { class: 'obj-current small', text: `현재: ${current ? label(options, current) : '미설정'}` }));
    }

    function field(id, text, value, onInput, hint, rows = 2) {
      return el('label', { class: 'field', for: uid(id) }, el('span', { class: 'k', text }),
        el('textarea', { id: uid(id), rows, maxlength: id === 'other' ? 1000 : 300, text: value,
          oninput: (e) => { draft[onInput] = e.target.value; onChange?.(); refreshControls(); } }),
        hint ? el('span', { class: 'muted small', text: hint }) : null);
    }

    function selectField(id, text, options, value, key) {
      return el('label', { class: 'field', for: uid(id) }, el('span', { class: 'k', text }),
        el('select', { id: uid(id), onchange: (e) => set({ [key]: e.target.value }) },
          ...options.map(([v, t]) => el('option', { value: v, text: t, selected: v === value }))));
    }

    function otherSection() {
      const items = draft.other_items.map((item, index) => el('div', { class: 'obj-item' },
        el('span', { class: 'obj-kind', text: KIND[item.kind] }),
        el('input', { class: 'input', type: 'text', maxlength: 200, value: item.text, 'aria-label': `${KIND[item.kind]} 문장`,
          oninput: (e) => { item.text = e.target.value; onChange?.(); refreshControls(); } }),
        el('button', { class: 'btn sm', type: 'button', text: '빼기', onclick: () => { draft.other_items.splice(index, 1); changed(); } })));
      const proposals = interpretation ? [
        ...interpretation.applicable.filter((p) => !draft.other_items.some((i) => i.kind === p.kind && i.text === p.text))
          .map((p) => el('div', { class: 'obj-proposal' },
            el('span', { class: 'obj-kind', text: KIND[p.kind] }), el('span', { class: 'grow', text: p.text }),
            el('button', { class: 'btn sm', type: 'button', text: '적용', onclick: () => { draft.other_items.push({ kind: p.kind, text: p.text }); changed(); } }))),
        ...interpretation.held.map((p) => el('div', { class: 'obj-proposal held' },
          el('span', { class: 'obj-kind', text: p.status === 'rejected' ? '적용 안 함' : '보류' }),
          el('span', { class: 'grow', text: p.text }), el('span', { class: 'muted small', text: REASON[p.reason] || '' }))),
        ...interpretation.conflicts.map((c) => el('div', { class: 'obj-proposal conflict' },
          el('span', { class: 'grow', text: c.field === 'process'
            ? `“${c.item}”은(는) ${label(PROCESSES, c.suggested)} 과정에 가까워요. 현재 선택은 ${label(PROCESSES, c.current)}입니다.`
            : `“${c.item}”은(는) ${label(SUPPORT, c.suggested)}에 가까워요. 현재 선택은 ${label(SUPPORT, c.current)}입니다.` }),
          el('button', { class: 'btn sm', type: 'button', text: '제안 값 적용', onclick: () => {
            interpretation.conflicts = interpretation.conflicts.filter((x) => x !== c); set({ [c.field]: c.suggested }); } }),
          el('button', { class: 'btn sm', type: 'button', text: '현재 값 유지', onclick: () => {
            interpretation.conflicts = interpretation.conflicts.filter((x) => x !== c); changed(); } }))),
        el('p', { class: 'muted small', text: interpretation.source === 'model'
          ? 'AI가 나눈 초안입니다. 적용할 항목만 고르고 문장을 다듬어 주세요.'
          : '문장 단위로 나눈 초안입니다. 적용할 항목만 고르고 문장을 다듬어 주세요.' }),
      ] : [];
      const manualKind = el('select', { 'aria-label': '직접 추가할 항목 종류' },
        ...Object.entries(KIND).map(([k, t]) => el('option', { value: k, text: t })));
      return el('div', { class: 'stack obj-other' },
        field('other', '기타 학습목표 (선택)', draft.other_raw, 'other_raw',
          '예: 목표: 두 사료의 한계를 구분한다. 관찰 증거: 사료별 근거를 하나씩 제시한다. 코칭 지침: 한 번에 하나씩 되묻는다.', 3),
        el('div', { class: 'row' },
          el('button', { class: 'btn sm', type: 'button', id: uid('interpret'), text: '해석 미리보기', onclick: interpret }),
          manualKind,
          el('button', { class: 'btn sm', type: 'button', text: '항목 직접 추가', onclick: () => { draft.other_items.push({ kind: manualKind.value, text: '' }); changed(); } })),
        ...proposals,
        draft.other_items.length ? el('b', { class: 'small', text: '적용할 항목' }) : null,
        ...items);
    }

    function previewSection() {
      const question = el('input', { class: 'input', id: uid('preview-q'), type: 'text', maxlength: 2000,
        placeholder: '예: 평균이 커진 건 왜예요?', value: previewQuestion,
        oninput: (e) => { previewQuestion = e.target.value; } });
      const result = preview ? el('dl', { class: 'obj-preview' },
        el('dt', { text: '질문 진단' }), el('dd', { text: preview.diagnosis.level === 'unassessed'
          ? `판정 보류${preview.reason ? ` · ${UNASSESSED_REASON[preview.reason] || '판정하지 못함'}` : ''}` : `${preview.diagnosis.level_label} 질문` }),
        el('dt', { text: '목표 관련성' }), el('dd', { text: RELEVANCE[preview.goal_relevance] || '판정 보류' }),
        el('dt', { text: '다음 역질문' }), el('dd', { text: preview.follow_up_question || '—' }),
        preview.ignored_items.length ? el('dt', { text: '반영되지 않은 항목' }) : null,
        preview.ignored_items.length ? el('dd', { text: preview.ignored_items.map((i) => i.text).join(' / ') }) : null) : null;
      return el('div', { class: 'stack obj-preview-box' },
        el('label', { class: 'field', for: uid('preview-q') }, el('span', { class: 'k', text: '샘플 학생 질문으로 미리보기' }), question),
        el('div', { class: 'row' }, el('button', { class: 'btn sm', type: 'button', text: '미리보기', disabled: busy,
          onclick: () => runPreview(question.value) })),
        result);
    }

    function render() {
      // Re-rendering replaces the controls; keep keyboard focus (radio arrow keys) where it was.
      const focused = document.activeElement && body.contains(document.activeElement) ? document.activeElement.id : null;
      build();
      if (focused) document.getElementById(focused)?.focus();
    }

    function build() {
      const process = PROCESSES.find(([k]) => k === draft.process);
      const missing = rules.missing(draft);
      body.replaceChildren(
        el('p', { class: 'muted small', text: '목표 → 확인할 증거 → 코칭 방식 순서로 정해 주세요. 미설정이면 기존 코칭 흐름을 그대로 씁니다.' }),
        field('objective', '단원 학습목표', draft.objective, 'objective',
          '“학생은 [조건·자료]를 사용해 [대상]을 [관찰 가능한 행동]할 수 있다.”'),
        radioGroup('process', '목표 사고 과정', PROCESSES.map(([k, t]) => [k, t]), draft.process,
          (value) => set({ process: value }), true),
        el('p', { class: 'muted small', id: uid('process-desc'), text: process ? `${process[1]} — ${process[2]}` : '대표 과정 1개를 골라 주세요.' }),
        field('evidence', '확인할 수행 증거', draft.evidence, 'evidence', process ? `예시: ${process[3]}` : '목표별 1개를 권장해요.'),
        process ? el('div', { class: 'row' }, el('button', { class: 'btn sm', type: 'button', text: '예시 넣기',
          onclick: () => set({ evidence: process[3] }) })) : null,
        radioGroup('support', '코칭 지원 정도', SUPPORT, draft.support, (value) => set({ support: value })),
        el('details', { class: 'obj-optional' }, el('summary', { text: '선택 항목: 지식 유형 · 적용 맥락' }),
          el('div', { class: 'stack' },
            selectField('knowledge', '지식 유형', KNOWLEDGE, draft.knowledge_type, 'knowledge_type'),
            selectField('context', '적용 맥락', CONTEXT, draft.application_context, 'application_context'))),
        otherSection(),
        previewSection(),
        el('div', { class: 'row obj-actions' },
          el('button', { class: 'btn primary', type: 'button', id: uid('confirm'), text: saved ? '새 버전으로 확정' : '학습목표 확정', onclick: save }),
          el('button', { class: 'btn', type: 'button', id: uid('cancel'), text: '변경 취소',
            onclick: () => { draft = rules.fromProfile(saved); interpretation = null; preview = null; say(''); changed(); } }),
          el('span', { class: 'muted small', id: uid('missing') })));
      refreshControls();
    }

    // Text inputs do not re-render (that would move the cursor), so derived control
    // states are refreshed here on every keystroke as well as after each render.
    function refreshControls() {
      const missing = rules.missing(draft);
      const byId = (name) => document.getElementById(uid(name));
      if (byId('interpret')) byId('interpret').disabled = busy || draft.other_raw.trim().length < 2;
      if (byId('confirm')) byId('confirm').disabled = busy || !!missing.length || !dirty();
      if (byId('cancel')) byId('cancel').disabled = busy || !dirty();
      if (byId('missing')) byId('missing').textContent = missing.length && dirty() ? `${missing.join(', ')}을(를) 함께 정해 주세요.` : '';
      summaryRender();
    }

    function summaryRender() {
      summary.textContent = rules.summary(saved) + (dirty() ? ' · 저장하지 않은 변경 있음' : '');
    }

    function dirty() {
      return !rules.same(draft, rules.fromProfile(saved));
    }

    async function interpret() {
      busy = true; render(); say('해석하는 중…');
      try {
        interpretation = await api(`/v1/materials/${materialId}/objective-profiles/interpret`, { method: 'POST',
          body: { text: draft.other_raw.trim(), ...(draft.process ? { process: draft.process } : {}), support: draft.support } });
        say(interpretation.items.length ? '' : '나눌 항목을 찾지 못했어요. 직접 추가해 주세요.');
      } catch (err) {
        interpretation = null;
        say('해석하지 못했어요. 항목을 직접 추가해 주세요.');
      } finally { busy = false; render(); }
    }

    async function runPreview(question) {
      if (question.trim().length < 10) return say('샘플 질문을 10자 이상 입력해 주세요.');
      const missing = rules.missing(draft);
      if (missing.length) return say(`${missing.join(', ')}을(를) 먼저 정해 주세요.`);
      busy = true; render(); say('미리보는 중…');
      try {
        preview = await api(`/v1/materials/${materialId}/objective-profiles/preview`, { method: 'POST', timeout: 60000,
          body: { profile: rules.payload(draft), question: question.trim() } });
        say('');
      } catch (err) {
        preview = null;
        say(err.status === 409 ? '교안 근거를 승인하고 AI 코칭에 동의한 뒤 미리볼 수 있어요.' : err.message);
      } finally { busy = false; render(); }
    }

    async function save() {
      busy = true; render(); say('확정하는 중…');
      try {
        const result = await api(`/v1/materials/${materialId}/objective-profiles`, { method: 'POST', body: rules.payload(draft) });
        saved = result;
        draft = rules.fromProfile(saved);
        interpretation = null;
        say([`v${result.version}을(를) 확정했어요. 이후 여는 수업부터 이 설정을 씁니다.`,
          ...(result.warnings || []).map((w) => WARNING[w])].join(' '));
      } catch (err) {
        const detail = err.detail && typeof err.detail === 'object' ? err.detail : null;
        say(detail ? `${KIND[detail.kind]} 중 적용할 수 없는 항목이 있어요. ${REASON[detail.reason] || ''}` : err.message);
      } finally { busy = false; changed(); }
    }

    async function load(id) {
      const mine = ++token;
      materialId = id; saved = null; draft = rules.emptyDraft(); interpretation = null; preview = null;
      say('불러오는 중…'); render();
      try {
        const res = await api(`/v1/materials/${id}/objective-profiles`);
        if (mine !== token) return;
        saved = res.latest; draft = rules.fromProfile(saved); say('');
      } catch (err) {
        if (mine !== token) return;
        say('학습목표를 불러오지 못했어요. 미설정 상태로 진행할 수 있습니다.');
      }
      changed();
    }

    return { load, dirty, focus: () => { panel.open = true; panel.querySelector('summary').focus(); } };
  }

  window.QU = Object.assign(window.QU || {}, { createObjectivePanel, objectiveRules: rules });
})();
