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
  // 45s, not 12s: the backend's own Claude call (vision/analyzer.py) is
  // configured for up to CLAUDE_VISION_TIMEOUT=60s (default), plus image
  // decode + decision-engine time. A client timeout shorter than the
  // server's own budget means the browser routinely aborts requests the
  // backend is still correctly working on — that WAS the bug (root-caused
  // 2026-08-20): the client gave up before Claude Vision, which can
  // legitimately take longer than a few seconds, had a chance to respond.
  // 45s stays under the server's 60s ceiling (so a genuinely-stuck request
  // still surfaces as a clear client-side timeout instead of hanging
  // forever) while covering realistic response times with real margin.
  const REQUEST_TIMEOUT_MS = 45_000;
  const MAX_BYTES = 20 * 1024 * 1024; // matches utils/image_utils.py's MAX_IMAGE_BYTES default

  // Client-side pre-upload resize (2026-08-21 latency investigation). Real
  // production evidence: a 3.0MB 1206x2622 iPhone screenshot took ~6.9s to
  // reach Flask over the network and another ~4.9s of server-side
  // decode/resize/encode — both dwarfing Claude's own ~4s and the decision
  // engine's ~0.1s. Shrinking the upload BEFORE it leaves the phone attacks
  // the actual measured bottleneck. Mirrors the server's own thresholds so
  // the two stay consistent and the server's "already within target,
  // skip re-encode" fast path (utils/image_utils.py) gets hit normally.
  const CLIENT_MAX_DIMENSION = 2048; // matches utils/image_utils.py's MAX_IMAGE_DIMENSION default
  const CLIENT_JPEG_QUALITY = 0.92;  // matches utils/image_utils.py's existing JPEG quality=92

  const ANALYZING_MESSAGES = ['Reading table…', 'Analyzing hand…', 'Calculating decision…'];

  /** @type {'idle'|'analyzing'|'result'|'error'} */
  let state = 'idle';
  let messageTimer = null;

  // Latency-audit instrumentation only (2026-08-20) — does not affect
  // behavior. performance.now() is high-resolution and monotonic (unlike
  // Date.now(), which can jump with system clock adjustments), so it's
  // what every [PERF][WEB] duration below is measured with. perfPrev is
  // reset at the start of each new analysis attempt (perfReset(), called
  // from button_clicked) so "elapsed=" always means "since the previous
  // stage of THIS attempt", not since page load or a prior attempt.
  let perfPrev = null;

  function perfReset() {
    perfPrev = null;
  }

  function perfMark(stage) {
    const t = performance.now();
    const elapsed = perfPrev === null ? 0 : t - perfPrev;
    perfPrev = t;
    console.log(`[PERF][WEB] ${stage} t=${t.toFixed(2)}ms elapsed=${elapsed.toFixed(2)}ms`);
  }

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
    if (perfPrev !== null) perfMark('result_rendered');
  }

  function openPicker() {
    console.log(`${TAG} button_clicked`);
    if (state === 'analyzing') {
      console.warn(`${TAG} tap ignored, already analyzing`);
      return;
    }
    perfReset();
    perfMark('button_clicked');
    // Reset so picking the SAME photo twice in a row still fires 'change'.
    fileInput.value = '';
    fileInput.click();
  }

  /**
   * Resize+re-encode the selected screenshot to CLIENT_MAX_DIMENSION before
   * upload, ONLY if it's actually larger than that — otherwise returns the
   * original file untouched (no quality loss, no wasted CPU for the common
   * case of an already-reasonably-sized screenshot).
   *
   * Every failure path (createImageBitmap unsupported/throws, no 2D canvas
   * context, toBlob returning null — all realistic on iOS Safari under
   * memory pressure or for HEIC/unusual inputs createImageBitmap can't
   * decode) falls back to the ORIGINAL file, unchanged. This is a pure
   * performance optimization: the backend (utils/image_utils.py)
   * independently re-validates MIME/dimensions/corruption regardless of
   * what the client sends, so a fallback to the original file is always
   * safe, never a correctness or security issue — it just forgoes the
   * speedup for that one upload.
   *
   * JPEG at quality 0.92 (matching utils/image_utils.py's own existing
   * JPEG quality for its resize path — not a new, unvalidated threshold):
   * benchmarked against PNG on a same-content-profile proxy (flat UI
   * regions + sharp card/text edges) at the server's actual target
   * dimensions, JPEG q92 was ~35% smaller than PNG with no visible
   * artifacting at this quality level. See the session report for the
   * real numbers and their caveats (synthetic proxy, not a real ClubGG
   * screenshot — recommend validating decision accuracy against a real
   * screenshot before treating this as final).
   */
  async function preprocessImage(file) {
    if (typeof createImageBitmap !== 'function') {
      console.warn(`${TAG} createImageBitmap unavailable, using original file`);
      return { blob: file, width: null, height: null, skipped: true };
    }

    let bitmap = null;
    try {
      bitmap = await createImageBitmap(file);
      const { width, height } = bitmap;
      console.log(`[PERF][WEB] original_width=${width}`);
      console.log(`[PERF][WEB] original_height=${height}`);

      if (width <= CLIENT_MAX_DIMENSION && height <= CLIENT_MAX_DIMENSION) {
        return { blob: file, width, height, skipped: true };
      }

      const scale = Math.min(CLIENT_MAX_DIMENSION / width, CLIENT_MAX_DIMENSION / height);
      const targetW = Math.max(1, Math.round(width * scale));
      const targetH = Math.max(1, Math.round(height * scale));

      // Plain <canvas>, not OffscreenCanvas — broader/more reliable iOS
      // Safari support across the versions this PWA needs to run on.
      const canvas = document.createElement('canvas');
      canvas.width = targetW;
      canvas.height = targetH;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('2D canvas context unavailable');
      ctx.drawImage(bitmap, 0, 0, targetW, targetH);

      const blob = await new Promise((resolve, reject) => {
        canvas.toBlob(
          (b) => (b ? resolve(b) : reject(new Error('canvas.toBlob returned null'))),
          'image/jpeg',
          CLIENT_JPEG_QUALITY
        );
      });

      return { blob, width: targetW, height: targetH, skipped: false };
    } catch (err) {
      console.warn(`${TAG} client-side preprocessing failed, using original file: ${err && err.message}`);
      return { blob: file, width: null, height: null, skipped: true };
    } finally {
      if (bitmap) bitmap.close();
    }
  }

  async function onFileSelected(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    console.log(`${TAG} file_selected`);
    console.log(`${TAG} file_name=${file.name}`);
    console.log(`${TAG} file_size=${file.size}`);
    console.log(`[PERF][WEB] file_selected`);
    console.log(`[PERF][WEB] original_bytes=${file.size}`);
    perfMark('file_selected');

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

    const totalStart = performance.now();
    const preprocessStart = performance.now();
    const { blob: uploadBlob, width, height, skipped } = await preprocessImage(file);
    const preprocessMs = performance.now() - preprocessStart;

    console.log(`[PERF][WEB] preprocess_ms=${preprocessMs.toFixed(2)} skipped=${skipped}`);
    console.log(`[PERF][WEB] final_bytes=${uploadBlob.size}`);
    if (width != null) console.log(`[PERF][WEB] final_width=${width}`);
    if (height != null) console.log(`[PERF][WEB] final_height=${height}`);

    // JPEG when preprocessing actually ran (canvas output), otherwise the
    // original file's own name/type pass through unchanged.
    const uploadName = skipped ? (file.name || 'screenshot.png') : 'screenshot.jpg';

    const form = new FormData();
    // Field name "image" — must match request.files["image"] in routes/mobile.py.
    form.append('image', uploadBlob, uploadName);

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

    console.log(`${TAG} upload_started`);
    perfMark('upload_started');
    let response;
    try {
      response = await fetch(ENDPOINT, { method: 'POST', body: form, signal: controller.signal });
    } catch (err) {
      clearTimeout(timer);
      if (controller.signal.aborted) {
        console.error(`${TAG}[ERROR] stage=upload timed out after ${REQUEST_TIMEOUT_MS}ms`);
        resetToIdle('Analysis timed out. Try again.', true);
      } else {
        console.error(`${TAG}[ERROR] stage=upload network error: ${err && err.message}`);
        resetToIdle('Could not reach the server. Check your connection and try again.', true);
      }
      return;
    }
    clearTimeout(timer);
    // With the plain Fetch API (no XHR upload-progress tracking, deliberately
    // avoided to keep this dependency-free), the promise resolving IS the
    // signal that both the upload and the response headers have arrived —
    // there's no separate lower-level event for "upload done" vs "response
    // headers in" to log distinctly.
    console.log(`${TAG} upload_completed`);
    console.log(`${TAG} response_received`);
    console.log(`${TAG} response_status=${response.status}`);
    console.log(`[PERF][WEB] upload_completed`);
    console.log(`[PERF][WEB] total_ms=${(performance.now() - totalStart).toFixed(2)}`);
    perfMark('upload_completed'); // fetch() resolving covers both upload + response headers, see comment above
    perfMark('response_received');

    let body;
    try {
      perfMark('json_parse_started');
      body = await response.json();
      perfMark('json_parse_done');
    } catch (err) {
      console.error(`${TAG}[ERROR] stage=parse_response response was not valid JSON: ${err && err.message}`);
      resetToIdle('Analysis failed. Try again.', true);
      return;
    }

    if (!response.ok) {
      const message = (body && typeof body.error === 'string' && body.error) || 'Analysis failed. Try again.';
      console.error(`${TAG}[ERROR] stage=backend_error status=${response.status} message=${message}`);
      resetToIdle(message, true);
      return;
    }

    const VALID_ACTIONS = ['FOLD', 'CHECK', 'CALL', 'BET', 'RAISE', 'ALL-IN'];
    if (typeof body.winrate !== 'number' || !VALID_ACTIONS.includes(body.action)) {
      console.error(`${TAG}[ERROR] stage=invalid_response shape=${JSON.stringify(body)}`);
      resetToIdle('Analysis failed. Try again.', true);
      return;
    }

    console.log(`${TAG} result_received`);
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
    perfMark('result_rendered');
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
