/**
 * EquiEdgeAI ClubGG Analyzer — thin PWA client.
 *
 * No poker logic lives here. This file's entire job: get a screenshot from
 * the user with the fewest possible taps, POST it to the existing
 * /mobile/analyze endpoint (same contract Android's AnalyzeApi.kt uses —
 * multipart field name "image", response {winrate, action} | {error}), and
 * render whatever comes back. Same-origin fetch (this page is served BY
 * Flask), so no API base URL configuration and no CORS are needed at all.
 */
(() => {
  'use strict';

  const TAG = '[WEB-ANALYZE]';
  const ENDPOINT = '/mobile/analyze';
  const REQUEST_TIMEOUT_MS = 12_000; // matches Android AnalyzeApi.kt's TIMEOUT_MS
  const MAX_BYTES = 20 * 1024 * 1024; // matches utils/image_utils.py's MAX_IMAGE_BYTES default

  const ANALYZING_MESSAGES = ['Reading table…', 'Analyzing hand…', 'Calculating decision…'];

  /** @type {'idle'|'analyzing'|'result'|'error'} */
  let state = 'idle';
  let messageTimer = null;

  const fileInput = document.getElementById('fileInput');
  const analyzeBtn = document.getElementById('analyzeBtn');
  const statusLine = document.getElementById('statusLine');
  const resultCard = document.getElementById('resultCard');
  const resultAction = document.getElementById('resultAction');
  const resultWinrate = document.getElementById('resultWinrate');

  function setState(next) {
    console.log(`${TAG} state=${next}`);
    state = next;
  }

  function render({ busy, buttonLabel, status, isError, showResult }) {
    analyzeBtn.disabled = busy;
    analyzeBtn.textContent = buttonLabel;
    statusLine.textContent = status;
    statusLine.classList.toggle('is-error', !!isError);
    resultCard.classList.toggle('visible', !!showResult);
  }

  function cycleAnalyzingMessages() {
    let i = 0;
    statusLine.textContent = ANALYZING_MESSAGES[0];
    messageTimer = setInterval(() => {
      i = (i + 1) % ANALYZING_MESSAGES.length;
      statusLine.textContent = ANALYZING_MESSAGES[i];
    }, 1400);
  }

  function stopCyclingMessages() {
    if (messageTimer) {
      clearInterval(messageTimer);
      messageTimer = null;
    }
  }

  // Only ever called on failure paths (or once at page load) — a
  // successful analysis goes through showResult() instead, which sets its
  // own 'visible' state directly. So resetToIdle always hides any stale
  // result card rather than trying to preserve one.
  function resetToIdle(status = 'Ready', isError = false) {
    stopCyclingMessages();
    setState(isError ? 'error' : 'idle');
    render({ busy: false, buttonLabel: 'ANALYZE SCREENSHOT', status, isError, showResult: false });
  }

  function openPicker() {
    if (state === 'analyzing') {
      console.warn(`${TAG} tap ignored, already analyzing`);
      return;
    }
    // Reset so picking the SAME photo twice in a row still fires 'change'.
    fileInput.value = '';
    fileInput.click();
  }

  async function onFileSelected(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    console.log(`${TAG} image selected name=${file.name} type=${file.type} size=${file.size}`);

    if (!file.type.startsWith('image/')) {
      resetToIdle("That doesn't look like an image. Try again.", true);
      return;
    }
    if (file.size === 0) {
      resetToIdle('Selected file is empty. Try again.', true);
      return;
    }
    if (file.size > MAX_BYTES) {
      resetToIdle(
        `Image is too large (${(file.size / (1024 * 1024)).toFixed(1)} MB, max ${MAX_BYTES / (1024 * 1024)} MB).`,
        true
      );
      return;
    }

    await analyze(file);
  }

  async function analyze(file) {
    setState('analyzing');
    resultCard.classList.remove('visible');
    render({ busy: true, buttonLabel: 'ANALYZING…', status: '', isError: false, showResult: false });
    cycleAnalyzingMessages();

    const form = new FormData();
    // Field name "image" — must match request.files["image"] in routes/mobile.py.
    form.append('image', file, file.name || 'screenshot.png');

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    console.log(`${TAG} upload started`);
    let response;
    try {
      response = await fetch(ENDPOINT, { method: 'POST', body: form, signal: controller.signal });
    } catch (err) {
      clearTimeout(timer);
      if (controller.signal.aborted) {
        console.error(`${TAG}[ERROR] timed out after ${REQUEST_TIMEOUT_MS}ms`);
        resetToIdle('Analysis timed out. Try again.', true);
      } else {
        console.error(`${TAG}[ERROR] network error: ${err && err.message}`);
        resetToIdle('Could not reach the server. Check your connection and try again.', true);
      }
      return;
    }
    clearTimeout(timer);
    console.log(`${TAG} response received status=${response.status}`);

    let body;
    try {
      body = await response.json();
    } catch (err) {
      console.error(`${TAG}[ERROR] response was not valid JSON: ${err && err.message}`);
      resetToIdle('Analysis failed. Try again.', true);
      return;
    }

    if (!response.ok) {
      const message = (body && typeof body.error === 'string' && body.error) || 'Analysis failed. Try again.';
      console.error(`${TAG}[ERROR] server error status=${response.status} message=${message}`);
      resetToIdle(message, true);
      return;
    }

    const VALID_ACTIONS = ['FOLD', 'CHECK', 'CALL', 'BET', 'RAISE', 'ALL-IN'];
    if (typeof body.winrate !== 'number' || !VALID_ACTIONS.includes(body.action)) {
      console.error(`${TAG}[ERROR] unexpected response shape: ${JSON.stringify(body)}`);
      resetToIdle('Analysis failed. Try again.', true);
      return;
    }

    console.log(`${TAG} result=${body.action} winrate=${body.winrate}`);
    showResult(body.action, body.winrate);
  }

  function showResult(action, winrate) {
    stopCyclingMessages();
    resultAction.textContent = action;
    resultAction.className = 'result-action action-' + action.toLowerCase().replace('-', '');
    resultWinrate.innerHTML = `Winrate: <b>${winrate.toFixed(1)}%</b>`;
    resultCard.classList.add('visible');
    setState('result');
    render({ busy: false, buttonLabel: 'ANALYZE ANOTHER', status: 'Ready', isError: false, showResult: true });
  }

  analyzeBtn.addEventListener('click', openPicker);
  fileInput.addEventListener('change', onFileSelected);

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/mobile' }).catch((err) => {
        console.warn(`${TAG} service worker registration failed: ${err && err.message}`);
      });
    });
  }

  resetToIdle('Ready');
})();
