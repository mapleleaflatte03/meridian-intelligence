/* meridian.js — Scroll-reveal observer. No dependencies. */
(function () {
  'use strict';
  var els = document.querySelectorAll('.reveal');
  if (!els.length || !('IntersectionObserver' in window)) {
    els.forEach(function (el) { el.classList.add('visible'); });
    return;
  }
  var io = new IntersectionObserver(function (entries) {
    entries.forEach(function (e) {
      if (e.isIntersecting) {
        e.target.classList.add('visible');
        io.unobserve(e.target);
      }
    });
  }, { threshold: 0.12 });
  els.forEach(function (el) { io.observe(el); });
})();

(function () {
  'use strict';
  var form = document.querySelector('[data-pilot-intake-form]');
  if (!form) {
    return;
  }
  var status = form.querySelector('[data-pilot-intake-status]');
  var submitButton = form.querySelector('button[type="submit"]');
  var endpoint = form.getAttribute('action') || '/api/pilot/intake';

  function setStatus(message, isError) {
    if (!status) {
      return;
    }
    status.textContent = message;
    status.style.color = isError ? '#ff9b9b' : '';
  }

  form.addEventListener('submit', async function (event) {
    event.preventDefault();
    var payload = {};
    new FormData(form).forEach(function (value, key) {
      payload[key] = value;
    });
    if (submitButton) {
      submitButton.disabled = true;
    }
    setStatus('Submitting request into the pilot queue...', false);
    try {
      var response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      var data = await response.json();
      if (!response.ok) {
        throw new Error(data && data.error ? data.error : 'Pilot intake submission failed');
      }
      form.reset();
      setStatus('Request recorded. Queue ID ' + data.request.request_id + ' is now inspectable in the workspace.', false);
    } catch (error) {
      setStatus(error.message || 'Pilot intake submission failed', true);
    } finally {
      if (submitButton) {
        submitButton.disabled = false;
      }
    }
  });
})();
