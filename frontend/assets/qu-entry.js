/* Question-up entry: one school Google sign-in for students and faculty.
 * First sign-in picks a role once; afterwards the stored role sends each person to their page.
 * Anonymous join by class code and the faculty trial stay under "로그인 없이 이용하기". */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const show = (node, on) => { node.hidden = !on; };

  /* ---------- 로그인 없이: 수업 코드 ---------- */
  const form = $('join-form');
  const input = $('join-code');
  const error = $('join-error');
  const clean = (v) => v.toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 6);
  input.addEventListener('input', () => { input.value = clean(input.value); error.hidden = true; input.classList.remove('invalid'); });
  form.addEventListener('submit', (e) => {
    const code = clean(input.value);
    if (code.length !== 6) {
      e.preventDefault();
      error.hidden = false;
      input.classList.add('invalid');
      input.focus();
      return;
    }
    input.value = code;
  });

  /* ---------- 학교 Google 로그인 ---------- */
  const { client } = window.QU;
  // No token and no 401 redirect: a failed exchange only shows a message here.
  const google = window.QU.createGoogleLogin({ api: client(() => null) });
  const button = $('google-start');
  const roleButtons = () => $('google-role').querySelectorAll('[data-role]');

  function setError(msg) {
    $('login-error').textContent = msg ? (msg.message || String(msg)) : '';
    show($('login-error'), !!msg);
  }

  function busy(on, label) {
    button.disabled = on;
    button.textContent = on ? label : '학교 Google 계정으로 로그인';
    roleButtons().forEach((b) => { b.disabled = on; });
  }

  function route(result) {
    if (!result) return;
    if (result.needsRole) {
      $('google-role-email').textContent = google.roleIntro(result);
      show($('google-role'), true);
      show(button, false);
      show($('google-note'), false);
      roleButtons()[0].focus();
      return;
    }
    // The role page signs in again from the same Neon session and keeps its token in memory.
    google.goTo(result.role === 'faculty' ? 'teacher.html' : 'student.html', result.role);
  }

  async function run(task, label) {
    setError(null);
    busy(true, label);
    try { route(await task()); } catch (err) { setError(err); } finally { busy(false); }
  }

  button.addEventListener('click', () => run(async () => {
    if (await google.session()) return google.exchange();
    await google.start();
    return null;
  }, 'Google로 이동 중…'));

  $('google-role').addEventListener('click', (event) => {
    const card = event.target.closest('[data-role]');
    if (card) run(() => google.exchange(card.dataset.role), '이동 중…');
  });

  $('google-cancel').addEventListener('click', async () => {
    show($('google-role'), false);
    show(button, true);
    show($('google-note'), true);
    await google.signOut();
    button.focus();
  });

  // Back from Google: classify right away.
  if (google.returning()) run(() => google.exchange(), '로그인 확인 중…');
})();
