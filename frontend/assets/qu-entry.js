(() => {
  'use strict';
  const form = document.getElementById('join-form');
  const input = document.getElementById('join-code');
  const error = document.getElementById('join-error');
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
})();
