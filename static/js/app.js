/* ============================================================
   POKER DECISION WEAPON — Frontend Logic
   ============================================================ */

'use strict';

/* ── Constants ──────────────────────────────────────────────── */
const SUIT_SYMBOLS = { h: '\u2665', d: '\u2666', c: '\u2663', s: '\u2660' };
const SUIT_NAMES   = { h: 'Hearts \u2665', d: 'Diamonds \u2666', c: 'Clubs \u2663', s: 'Spades \u2660' };
const SUIT_CLASS   = { h: 'heart', d: 'diamond', c: 'club', s: 'spade' };
const RANKS        = ['A','K','Q','J','T','9','8','7','6','5','4','3','2'];
const SUITS        = ['h','d','c','s'];

const TAG_ICONS = {
  VALUE:       '💰',
  THIN_VALUE:  '🪙',
  BLUFF:       '🎭',
  PROTECTION:  '🛡',
  TRAP:        '🕸',
  BLUFF_CATCH: '🎣',
  FOLD:        '✋',
  NEUTRAL:     '◦',
};

const REASONING_ICONS = [
  '🃏', '📊', '🌊', '📏', '🛡', '📈', '🎯', '👥', '🎰', '⚡',
];

/* ── State ──────────────────────────────────────────────────── */
let APP_MODE               = 'fast';  // 'fast' | 'full' — initialised in initPlanUI
let pickerTargetField      = null;
let pickerMaxCards         = 0;
let pickerAllowFlex        = false;
let pickerSelection        = [];

// Inline dropdown picker state
const dropState = { hand: [], board: [] };

/* ── Helpers ────────────────────────────────────────────────── */
const $  = id => document.getElementById(id);
const qs = sel => document.querySelector(sel);

function suitColor(suit) { return (suit === 'h' || suit === 'd') ? 'red' : 'black'; }

function parseCards(text) {
  if (!text || !text.trim()) return [];
  return text.split(',').map(x => x.trim()).filter(Boolean);
}

function fmt(val, digits = 2) {
  if (val === undefined || val === null || val === '') return '\u2014';
  if (typeof val === 'number') return val.toFixed(digits);
  const n = Number(val);
  return Number.isNaN(n) ? String(val) : n.toFixed(digits);
}

function show(id) {
  const el = $(id);
  if (el) { el.style.display = ''; el.style.removeProperty('display'); }
}
function hide(id) {
  const el = $(id);
  if (el) el.style.display = 'none';
}

/* ── Segment control helpers ────────────────────────────────── */
function initSegment(groupClass, onChange) {
  const btns = document.querySelectorAll(`.${groupClass} .seg-btn`);
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      btns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      onChange(btn.dataset.value);
    });
  });
}

function getSegmentValue(groupClass) {
  const active = qs(`.${groupClass} .seg-btn.active`);
  return active ? active.dataset.value : null;
}

/* ── Card rendering ─────────────────────────────────────────── */
function renderPlayCard(rank, suit, extraClass = '') {
  const div = document.createElement('div');
  div.className = `play-card ${suitColor(suit)} ${extraClass}`.trim();
  div.dataset.suit = suit;   // needed for card Theme 2 CSS (suit-colored)
  div.innerHTML =
    `<div class="c-top">${rank}${SUIT_SYMBOLS[suit]}</div>` +
    `<div class="c-bot">${rank}${SUIT_SYMBOLS[suit]}</div>`;
  return div;
}

function renderCards(cards, targetId, small = false) {
  const el = $(targetId);
  if (!el) return;
  el.innerHTML = '';
  cards.forEach(card => {
    if (card.length !== 2) return;
    el.appendChild(renderPlayCard(card[0].toUpperCase(), card[1].toLowerCase(), small ? 'small' : ''));
  });
}

/* ── Action styling ─────────────────────────────────────────── */
function applyActionStyle(action) {
  const pill = $('actionPill');
  const text = $('heroText');
  if (!pill) return;

  const upper = (action || '').toUpperCase();
  pill.className = 'action-pill';
  text.className = 'hero-text';

  if (upper.startsWith('RAISE')) {
    pill.classList.add('pill-raise');
    pill.textContent = 'Raise';
  } else if (upper.startsWith('CALL')) {
    pill.classList.add('pill-call');
    pill.textContent = 'Call';
  } else if (upper.startsWith('FOLD')) {
    pill.classList.add('pill-fold');
    pill.textContent = 'Fold';
  } else if (upper.startsWith('BLUFF')) {
    pill.classList.add('pill-bluff');
    pill.textContent = 'Bluff';
  } else {
    pill.classList.add('pill-idle');
    pill.textContent = 'Idle';
  }
}

/* ── Render: core stats ─────────────────────────────────────── */
function renderCoreStats(json) {
  $('statWinRate').textContent =
    json.win_rate != null ? (json.win_rate * 100).toFixed(1) + '%' : '—';
  $('statCallEv').textContent =
    json.ev_call  != null ? (json.ev_call  >= 0 ? '+' : '') + fmt(json.ev_call)  : '—';
  $('statRaiseEv').textContent =
    json.ev_raise != null ? (json.ev_raise >= 0 ? '+' : '') + fmt(json.ev_raise) : '—';
  $('statConfidence').textContent =
    json.confidence != null ? (json.confidence * 100).toFixed(0) + '%' : '—';
}

/* ── Render: key metrics box ────────────────────────────────── */
function renderKeyMetrics(json) {
  const metrics = [
    ['Fold Equity',     json.fold_equity     != null ? (json.fold_equity * 100).toFixed(1) + '%' : '—'],
    ['SPR',             json.spr             != null ? fmt(json.spr) : '—'],
    ['Hand Class',      json.hand_class      || '—'],
    ['Range Advantage', json.range_advantage != null ? fmt(json.range_advantage, 3) : '—'],
    ['Pot Odds',        json.pot_odds        != null ? (json.pot_odds * 100).toFixed(1) + '%' : '—'],
  ];
  $('metricsBox').innerHTML = metrics.map(
    ([k, v]) => `<div class="metric-item"><span>${k}</span><span>${v}</span></div>`
  ).join('');
}

/* ── Render: decision tags ──────────────────────────────────── */
function renderTags(tags) {
  if (!tags || !tags.length) { hide('tagsSection'); return; }

  const row = $('tagsRow');
  row.innerHTML = '';
  tags.forEach((tag, i) => {
    const span = document.createElement('span');
    span.className = `tag tag-${tag}`;
    span.style.animationDelay = `${i * 0.06}s`;
    span.textContent = (TAG_ICONS[tag] || '') + ' ' + tag.replace('_', ' ');
    row.appendChild(span);
  });
  show('tagsSection');
}

/* ── Render: reasoning bullets ──────────────────────────────── */
function renderReasoning(bullets) {
  if (!bullets || !bullets.length) { hide('reasoningSection'); return; }

  const ul = $('reasoningList');
  ul.innerHTML = '';
  bullets.forEach((text, i) => {
    const li = document.createElement('li');
    li.className = 'reasoning-item';
    li.style.animationDelay = `${i * 0.05}s`;
    const icon = REASONING_ICONS[i % REASONING_ICONS.length];
    li.innerHTML =
      `<span class="reasoning-icon">${icon}</span>` +
      `<span>${text}</span>`;
    ul.appendChild(li);
  });
  show('reasoningSection');
}

/* ── Render: UX signals ─────────────────────────────────────── */
function renderUxSignals(signals, popAdj) {
  // Guard: missing, null, or empty object (Beginner plan gets {})
  if (!signals || Object.keys(signals).length === 0) {
    hide('metricsSection');
    return;
  }

  // Progress bars (delay for transition to trigger after paint)
  setTimeout(() => {
    const conf = $('barConfidence');
    const agg  = $('barAggression');
    if (conf) conf.style.width = ((signals.confidence_score || 0) * 100).toFixed(1) + '%';
    if (agg)  agg.style.width  = ((signals.aggression_score || 0) * 100).toFixed(1) + '%';
  }, 40);

  const confVal = $('metricConfidenceVal');
  if (confVal) {
    confVal.textContent =
      signals.confidence_score != null ? (signals.confidence_score * 100).toFixed(0) + '%' : '—';
  }

  const aggVal = $('metricAggressionVal');
  if (aggVal) {
    aggVal.textContent =
      signals.aggression_score != null ? (signals.aggression_score * 100).toFixed(0) + '%' : '—';
  }

  // Risk badge
  const risk   = (signals.risk_level || 'none').toLowerCase();
  const riskEl = $('riskBadge');
  if (riskEl) {
    riskEl.textContent = risk.toUpperCase();
    riskEl.className   = 'risk-badge risk-' + risk;
  }

  // Population adjustment
  const popEl = $('popAdjVal');
  if (popEl) {
    popEl.textContent = popAdj != null ? popAdj.toFixed(3) : '—';
    popEl.className   = 'pop-factor';
  }

  show('metricsSection');
}

/* ── Render: board texture ──────────────────────────────────── */
function renderBoardTexture(texture) {
  const box = $('textureBox');
  if (!box || !texture) return;

  const badges = [];
  if (texture.dry_board)    badges.push({ label: '🏜 Dry',       cls: 'dry'    });
  if (texture.flush_draw)   badges.push({ label: '♣ Flush Draw',  cls: ''       });
  if (texture.straight_draw)badges.push({ label: '↔ Str. Draw',   cls: ''       });
  if (texture.paired)       badges.push({ label: '♊ Paired',      cls: 'paired' });
  if (texture.monotone)     badges.push({ label: '🌊 Monotone',    cls: 'wet'    });
  if (texture.wetness >= 0.6 && !texture.dry_board)
    badges.push({ label: '💧 Wet',  cls: 'wet' });

  box.innerHTML = badges.map(
    b => `<span class="texture-badge ${b.cls}">${b.label}</span>`
  ).join('') || '<span class="texture-badge" style="opacity:.5">—</span>';
}

/* ── Render: what-if engine ─────────────────────────────────── */
function renderWhatIf(whatIf) {
  const section = $('whatifSection');
  const grid    = $('whatifGrid');
  if (!grid) return;

  if (!whatIf || Object.keys(whatIf).length === 0) {
    hide('whatifSection');
    return;
  }

  grid.innerHTML = '';

  // 1) If opponent raises
  if (whatIf.if_opponent_raises) {
    const d = whatIf.if_opponent_raises;
    const col = d.recommended_action && d.recommended_action.toUpperCase().startsWith('FOLD')
      ? 'whatif-action-red' : 'whatif-action-blue';
    grid.appendChild(buildWhatIfCard({
      wrapClass: 'whatif-opponent',
      icon:      '🃏',
      title:     'If Opponent Raises',
      action:    d.recommended_action || '—',
      actionCls: col,
      details: [
        d.break_even_equity != null
          ? `Break-even equity: ${(d.break_even_equity * 100).toFixed(0)}%`
          : null,
        d.hero_equity != null
          ? `Hero equity: ${(d.hero_equity * 100).toFixed(0)}%`
          : null,
        d.estimated_ev != null
          ? `EV: ${d.estimated_ev >= 0 ? '+' : ''}${d.estimated_ev.toFixed(1)} chips`
          : null,
      ],
      coaching: null,
    }));
  }

  // 2) If favorable card
  if (whatIf.if_favorable_card) {
    const d = whatIf.if_favorable_card;
    grid.appendChild(buildWhatIfCard({
      wrapClass: 'whatif-good',
      icon:      '✨',
      title:     'If Favorable Card',
      action:    d.scenario || 'Good Card',
      actionCls: 'whatif-action-green',
      details: [
        d.new_equity_est != null
          ? `New equity: ${(d.new_equity_est * 100).toFixed(0)}%`
          : null,
        d.ev_delta != null
          ? `EV delta: ${d.ev_delta >= 0 ? '+' : ''}${d.ev_delta.toFixed(1)} chips`
          : null,
      ],
      coaching: d.coaching || null,
    }));
  }

  // 3) If unfavorable card
  if (whatIf.if_unfavorable_card) {
    const d = whatIf.if_unfavorable_card;
    grid.appendChild(buildWhatIfCard({
      wrapClass: 'whatif-bad',
      icon:      '⚠️',
      title:     'If Bad Card',
      action:    d.scenario || 'Bad Card',
      actionCls: 'whatif-action-red',
      details: [
        d.new_equity_est != null
          ? `New equity: ${(d.new_equity_est * 100).toFixed(0)}%`
          : null,
        d.ev_delta != null
          ? `EV delta: ${d.ev_delta >= 0 ? '+' : ''}${d.ev_delta.toFixed(1)} chips`
          : null,
      ],
      coaching: d.coaching || null,
    }));
  }

  if (grid.children.length > 0) show('whatifSection');
  else hide('whatifSection');
}

function buildWhatIfCard({ wrapClass, icon, title, action, actionCls, details, coaching }) {
  const card = document.createElement('div');
  card.className = `whatif-card ${wrapClass}`;
  const detailHTML = details
    .filter(Boolean)
    .map(d => `<div class="whatif-detail">${d}</div>`)
    .join('');
  const coachHTML = coaching
    ? `<div class="whatif-coaching">${coaching}</div>`
    : '';
  card.innerHTML =
    `<div class="whatif-header">` +
      `<span class="whatif-icon">${icon}</span>` +
      `<span class="whatif-title">${title}</span>` +
    `</div>` +
    `<div class="whatif-action ${actionCls}">${action}</div>` +
    detailHTML +
    coachHTML;
  return card;
}

/* ── Real-time credits update ───────────────────────────────── */
function updateCreditsDisplay(count) {
  // Navbar chip (base.html)
  const navCount = document.getElementById('navCreditsCount');
  const navChip  = document.getElementById('navCreditsChip');
  if (navCount) navCount.textContent = count;
  if (navChip)  navChip.style.display = count > 0 ? '' : 'none';

  // Header sub-text (app.html)
  const headerCount = document.getElementById('headerCreditsCount');
  if (headerCount) headerCount.textContent = count;

  // Inline warning box (app.html, credits users only)
  const inlineCount = document.getElementById('inlineCreditsCount');
  if (inlineCount) inlineCount.textContent = count;

  // If credits hit zero, update the warning box message
  if (count <= 0) {
    const warningBox = document.getElementById('creditsWarningBox');
    if (warningBox) {
      warningBox.innerHTML =
        '⚡ <b>No decisions remaining.</b> ' +
        '<a href="/pricing" style="color:var(--accent);font-weight:600;">Buy more →</a>';
    }
  }
}

/* ── Main API call ──────────────────────────────────────────── */
async function sendDecision() {
  const hand  = parseCards($('hand').value);
  const board = parseCards($('board').value);

  // Loading state
  $('heroText').textContent = 'Calculating...';
  $('heroText').className   = 'hero-text calculating';
  $('actionPill').className = 'action-pill pill-idle';
  $('actionPill').textContent = '...';
  $('explanationBox').textContent = 'Running Monte Carlo simulations and evaluating EV...';
  $('submitBtn').disabled = true;
  // Update spinner text to match mode
  const spinnerSub = $('spinnerSub');
  if (spinnerSub) {
    spinnerSub.textContent = APP_MODE === 'fast'
      ? 'Quick Equity · Fast Sizing'
      : 'Monte Carlo · EV · Coach Layer';
  }
  $('loadingOverlay').style.display = 'flex';

  // Hide optional sections while loading
  ['tagsSection','reasoningSection','metricsSection','whatifSection','fastSizingBadge']
    .forEach(id => hide(id));

  const payload = APP_MODE === 'fast' ? buildFastPayload(hand, board) : buildFullPayload(hand, board);

  try {
    const res  = await fetch('/decision', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    const json = await res.json();

    if (!res.ok) {
      if (res.status === 401) {
        // Not logged in — redirect to login
        window.location.href = '/login?next=/app';
        return;
      }
      if (res.status === 403 && json.upgrade_required) {
        // No plan and no credits
        $('heroText').textContent   = 'Access Blocked';
        $('heroText').className     = 'hero-text';
        $('actionPill').className   = 'action-pill pill-fold';
        $('actionPill').textContent = 'Blocked';
        $('explanationBox').innerHTML =
          (json.error || 'Access denied.') +
          ' <a href="/pricing" style="color:var(--accent);font-weight:600;">View Plans →</a>';
        return;
      }
      showError(json.error || 'Server returned an error.');
      return;
    }

    // ── Core display ──────────────────────────────────────────
    $('heroText').textContent = json.action || 'No action';
    $('heroText').className   = 'hero-text';
    $('explanationBox').textContent = json.explanation || 'Decision calculated.';
    applyActionStyle(json.action || '');

    // ── Mode-specific rendering ───────────────────────────────
    if (APP_MODE === 'fast') {
      renderFastDecision(json);
    } else {
      renderFullDecision(json);
    }

    // ── Real-time credits update ──────────────────────────────
    if (json.credits_remaining !== undefined) {
      updateCreditsDisplay(json.credits_remaining);
    }

  } catch (err) {
    showError('Could not reach the server. Is the engine running?');
  } finally {
    $('submitBtn').disabled = false;
    $('loadingOverlay').style.display = 'none';
  }
}

function showError(msg) {
  $('heroText').textContent = 'Error';
  $('heroText').className   = 'hero-text';
  $('actionPill').className = 'action-pill pill-fold';
  $('actionPill').textContent = 'Error';
  $('explanationBox').textContent = msg;
  renderCoreStats({});
  renderKeyMetrics({});
}

/* ── Reset ──────────────────────────────────────────────────── */
function resetForm() {
  $('hand').value        = 'Ah,Kd';
  $('board').value       = 'Qs,Jh,2c';
  $('players').value     = 2;
  $('pot').value         = 100;
  $('bet').value         = 20;
  $('stack').value       = 500;
  $('position').value    = localStorage.getItem('dw-default-position') || 'BTN';
  $('simulations').value = 3000;
  $('line').value        = 'none';

  // Close any open inline card pickers and reset their state
  ['hand', 'board'].forEach(field => {
    const dd = $(field + 'Dropdown');
    if (dd) dd.classList.remove('open');
  });
  dropState.hand  = parseCards('Ah,Kd');
  dropState.board = parseCards('Qs,Jh,2c');

  $('heroText').textContent   = 'Waiting for input';
  $('heroText').className     = 'hero-text';
  $('actionPill').className   = 'action-pill pill-idle';
  $('actionPill').textContent = 'Idle';
  $('explanationBox').textContent =
    'Fill in the hand details and click Get Decision to see the recommendation and EV analysis.';

  renderCards(parseCards($('hand').value), 'heroCards', true);
  // boardCards / stage handled by updateLiveBoardStrip() called below
  renderCoreStats({});
  renderKeyMetrics({});

  ['tagsSection','reasoningSection','metricsSection','whatifSection']
    .forEach(id => hide(id));

  // Reset progress bars
  const bConf = $('barConfidence');
  const bAgg  = $('barAggression');
  if (bConf) bConf.style.width = '0%';
  if (bAgg)  bAgg.style.width  = '0%';

  // Reset facing action to default (small)
  document.querySelectorAll('.facing-btn').forEach(b => b.classList.remove('active'));
  const defaultFacing = qs('.facing-btn[data-value="small"]');
  if (defaultFacing) defaultFacing.classList.add('active');

  // Hide sizing badge
  hide('fastSizingBadge');

  forceCloseCardPicker();  // close the full-deck modal picker if open
  updateLiveBoardStrip();
}

/* ── Card picker ────────────────────────────────────────────── */
function getUsedCards(excludeField = null) {
  const used = new Set();
  ['hand','board'].forEach(fid => {
    if (fid === excludeField) return;
    parseCards($(fid).value).forEach(c => used.add(c));
  });
  return used;
}

function openCardPicker(targetField, maxCards, allowFlex = false) {
  pickerTargetField = targetField;
  pickerMaxCards    = maxCards;
  pickerAllowFlex   = allowFlex;
  pickerSelection   = parseCards($(targetField).value);

  $('pickerTitle').textContent = targetField === 'hand'
    ? 'Select Hero Hand' : 'Select Board Cards';
  $('pickerSubtitle').textContent = targetField === 'hand'
    ? 'Choose exactly 2 hole cards from the deck.'
    : 'Board can be empty or contain 3, 4, or 5 community cards.';

  renderPickerPreview();
  renderPickerGrid();
  $('cardPickerOverlay').classList.add('open');
}

function closeCardPicker(e) {
  if (e.target.id === 'cardPickerOverlay') forceCloseCardPicker();
}
function forceCloseCardPicker() {
  $('cardPickerOverlay').classList.remove('open');
}

function clearPickerSelection() {
  pickerSelection = [];
  renderPickerPreview();
  renderPickerGrid();
}

function renderPickerPreview() {
  const box = $('pickerPreview');
  box.innerHTML = '';
  if (!pickerSelection.length) {
    box.innerHTML = '<div class="picker-empty">No cards selected yet.</div>';
    return;
  }
  pickerSelection.forEach(card => {
    const r = card[0].toUpperCase(), s = card[1].toLowerCase();
    box.appendChild(renderPlayCard(r, s, 'small'));
  });
}

function toggleCardSelection(card) {
  const idx = pickerSelection.indexOf(card);
  if (idx >= 0) {
    // Already selected → deselect
    pickerSelection.splice(idx, 1);
  } else if (pickerTargetField === 'hand') {
    // Hero hand: clicking a new card when 2 are already chosen auto-resets
    if (pickerSelection.length >= pickerMaxCards) {
      pickerSelection = [card];   // start fresh with the new card
    } else {
      pickerSelection.push(card);
    }
  } else {
    // Board: just cap at max
    if (pickerSelection.length >= pickerMaxCards) return;
    pickerSelection.push(card);
  }
  renderPickerPreview();
  renderPickerGrid();
}

function isValidBoardLen(n) { return n === 0 || n === 3 || n === 4 || n === 5; }

function applyCardSelection() {
  if (pickerTargetField === 'hand' && pickerSelection.length !== 2) {
    alert('Hero hand must contain exactly 2 cards.');
    return;
  }
  if (pickerTargetField === 'board' && !isValidBoardLen(pickerSelection.length)) {
    alert('Board must contain 0, 3, 4, or 5 cards.');
    return;
  }
  $(pickerTargetField).value = pickerSelection.join(',');
  if (pickerTargetField === 'hand')  renderCards(pickerSelection, 'heroCards', true);
  if (pickerTargetField === 'board') updateLiveBoardStrip();
  forceCloseCardPicker();
}

function renderPickerGrid() {
  const grid   = $('pickerGrid');
  const usedEx = getUsedCards(pickerTargetField);
  grid.innerHTML = '';

  SUITS.forEach(suit => {
    const section  = document.createElement('div');
    section.className = 'picker-suit';
    section.innerHTML =
      `<h4><span class="${SUIT_CLASS[suit]}">${SUIT_NAMES[suit]}</span>` +
      `<span style="color:var(--muted);font-size:12px">${suit.toUpperCase()}</span></h4>`;

    const group = document.createElement('div');
    group.className = 'suit-group';

    RANKS.forEach(rank => {
      const card     = `${rank}${suit}`;
      const selected = pickerSelection.includes(card);
      const disabled = usedEx.has(card);

      const div = document.createElement('div');
      div.className =
        `play-card small clickable ${suitColor(suit)}` +
        (selected ? ' selected' : '') +
        (disabled ? ' disabled' : '');
      div.innerHTML =
        `<div class="c-top">${rank}${SUIT_SYMBOLS[suit]}</div>` +
        `<div class="c-bot">${rank}${SUIT_SYMBOLS[suit]}</div>`;

      if (!disabled) div.addEventListener('click', () => toggleCardSelection(card));
      group.appendChild(div);
    });

    section.appendChild(group);
    grid.appendChild(section);
  });
}

/* ── Game state bar update ──────────────────────────────────── */
function updateLiveBoardStrip() {
  const container = $('boardCards');
  const stageEl   = $('lbsStage');
  if (!container) return;

  const board = parseCards($('board').value);
  container.innerHTML = '';

  // Render filled card slots
  board.forEach(card => {
    if (card.length === 2) {
      container.appendChild(
        renderPlayCard(card[0].toUpperCase(), card[1].toLowerCase(), 'small')
      );
    }
  });

  // Render empty placeholder slots for visual context
  for (let i = board.length; i < 5; i++) {
    const ph = document.createElement('div');
    ph.className = 'gsb-slot-empty';
    container.appendChild(ph);
  }

  // Update street label
  if (stageEl) {
    const labels = ['Pre-flop', 'Pre-flop', 'Pre-flop', 'Flop', 'Turn', 'River'];
    stageEl.textContent = labels[board.length] || 'River';
  }
}

/* ── Mode change handler ────────────────────────────────────── */
function onModeChange(mode) {
  const simField = $('simsField');
  if (simField) simField.style.display = mode === 'quick' ? 'none' : '';
}

/* ── Plan-based UI init ─────────────────────────────────────── */
function initPlanUI() {
  const featureTier = window.FEATURE_TIER || 'beginner';
  const plan        = window.USER_PLAN    || 'none';

  // Apply saved default mode (Pro only — Fast is locked for non-Pro)
  const savedMode = localStorage.getItem('dw-default-mode') || 'full';
  if (featureTier === 'pro' && savedMode === 'fast') {
    setAppMode('fast');
  } else {
    APP_MODE = 'full';
    // (Jinja already set the correct initial DOM state)
  }

  // Non-pro: hide simulations field (quick mode forced server-side)
  if (featureTier !== 'pro') {
    const simsField = $('simsField');
    if (simsField) simsField.style.display = 'none';
  }

  // Show credits warning in explanation box if credits are low
  const credits = window.USER_CREDITS || 0;
  if (plan === 'credits' && credits <= 2) {
    const expBox = $('explanationBox');
    if (expBox && credits === 0) {
      expBox.innerHTML =
        'You have no credits remaining. ' +
        '<a href="/pricing" style="color:var(--accent);font-weight:600;">Upgrade your plan →</a>';
    }
  }
}

/* ── Payload helpers for plan gating ─────────────────────────── */
function getPlanAwarePayload(basePayload) {
  const featureTier = window.FEATURE_TIER || 'beginner';
  if (featureTier !== 'pro') {
    // Server enforces these, but set client-side too for clarity
    return { ...basePayload, mode: 'quick', player_profile: 'reg' };
  }
  return basePayload;
}

/* ── Mode toggle ────────────────────────────────────────────── */
function setAppMode(mode) {
  APP_MODE = mode;

  // Update toggle button active state
  document.querySelectorAll('.mode-toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.mode === mode);
  });

  // Show/hide field containers
  const fastFields = $('fastModeFields');
  const fullFields = $('fullModeFields');
  if (fastFields) fastFields.style.display = mode === 'fast' ? '' : 'none';
  if (fullFields) fullFields.style.display = mode === 'full' ? '' : 'none';

  // Update section badge
  const badge = $('modeBadge');
  if (badge) badge.textContent = mode === 'fast' ? 'Fast' : 'Full';

  // Update spinner sub-text
  const sub = $('spinnerSub');
  if (sub) {
    sub.textContent = mode === 'fast'
      ? 'Quick Equity · Fast Sizing'
      : 'Monte Carlo · EV · Coach Layer';
  }

  // Hide sizing badge on mode switch
  hide('fastSizingBadge');
}

/* ── Fast lock toast (non-Pro clicked Fast Decision) ────────── */
let _toastTimer = null;
function showFastLockToast() {
  const toast = $('upgradeToast');
  if (!toast) return;
  toast.classList.add('visible');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => toast.classList.remove('visible'), 3600);
}

/* ── Facing action helpers ──────────────────────────────────── */
function getActiveFacingAction() {
  const active = qs('.facing-btn.active');
  return active ? active.dataset.value : 'small';
}

/* ── Payload builders ───────────────────────────────────────── */
function buildFastPayload(hand, board) {
  return {
    mode:          'fast',
    hand,
    board,
    position:      $('position').value,
    stack_depth:   getSegmentValue('stack-depth-group') || 'medium',
    facing_action: getActiveFacingAction(),
  };
}

function buildFullPayload(hand, board) {
  const mode    = getSegmentValue('mode-group')    || 'full';
  const profile = getSegmentValue('profile-group') || 'reg';
  return getPlanAwarePayload({
    hand,
    board,
    players:        parseInt($('players').value)     || 2,
    pot:            parseFloat($('pot').value)        || 100,
    bet:            parseFloat($('bet').value)        || 0,
    stack:          parseFloat($('stack').value)      || 500,
    position:       $('position').value,
    line:           $('line').value,
    player_profile: profile,
    mode,
    simulations:    parseInt($('simulations').value) || 3000,
  });
}

/* ── Render: fast sizing badge ──────────────────────────────── */
function renderFastSizingBadge(sizingCategory) {
  const badge = $('fastSizingBadge');
  const tag   = $('fastSizingCategory');
  if (!badge || !tag) return;

  if (!sizingCategory) {
    badge.style.display = 'none';
    return;
  }

  tag.textContent = sizingCategory.toUpperCase();
  tag.className   = `fast-sizing-tag size-${sizingCategory}`;
  badge.style.display = '';
}

/* ── Render: fast decision (minimal output) ─────────────────── */
function renderFastDecision(json) {
  renderCoreStats(json);
  renderKeyMetrics(json);
  renderBoardTexture(json.board_texture);
  renderFastSizingBadge(json.sizing_category);

  // Coaching sections are stripped server-side; hide them in the UI too
  ['tagsSection', 'reasoningSection', 'metricsSection', 'whatifSection']
    .forEach(id => hide(id));
}

/* ── Render: full analysis (coaching + what-if) ─────────────── */
function renderFullDecision(json) {
  renderCoreStats(json);
  renderKeyMetrics(json);
  renderBoardTexture(json.board_texture);
  renderFastSizingBadge(null);  // ensure sizing badge is hidden in full mode

  renderTags(json.decision_tags);
  renderReasoning(json.reasoning);
  renderUxSignals(json.ux_signals, json.population_adjustment);
  renderWhatIf(json.what_if);
}

/* ══════════════════════════════════════════════════════════════
   INLINE DROPDOWN CARD PICKER
   — Panels render directly below the "Pick Cards" button inside
     the Hand Setup form. No floating/fixed positioning.
═══════════════════════════════════════════════════════════════ */

function _getUsedByOther(field) {
  const other = field === 'hand' ? 'board' : 'hand';
  return new Set(parseCards($(other).value));
}

function toggleInlinePicker(field) {
  const dd = $(field + 'Dropdown');
  if (!dd) return;

  if (dd.classList.contains('open')) {
    _closeInlinePicker(field);
  } else {
    // Close the other field's picker first
    const other = field === 'hand' ? 'board' : 'hand';
    _closeInlinePicker(other);

    // Sync state from current input value
    dropState[field] = parseCards($(field).value);
    _renderDropdown(field);
    dd.classList.add('open');
  }
}

function _closeInlinePicker(field) {
  const dd = $(field + 'Dropdown');
  if (!dd || !dd.classList.contains('open')) return;
  dd.classList.remove('open');

  // Board: revert to empty if selection count is invalid (1 or 2 cards)
  if (field === 'board') {
    const n = dropState.board.length;
    if (n === 1 || n === 2) {
      dropState.board = [];
      $('board').value = '';
      updateLiveBoardStrip();
    }
  }
}

function _renderDropdown(field) {
  const dd = $(field + 'Dropdown');
  if (!dd) return;

  const sel    = dropState[field];
  const used   = _getUsedByOther(field);
  const isHand = field === 'hand';
  const status = isHand
    ? `${sel.length} / 2`
    : `${sel.length} card${sel.length !== 1 ? 's' : ''} (0,3,4,5)`;

  let html = `
    <div class="cdd-header">
      <span class="cdd-title">${isHand ? 'Hero Hand' : 'Board'}</span>
      <span class="cdd-status">${status}</span>
      <button class="cdd-clear" onclick="clearInlinePicker('${field}')">Clear</button>
    </div>
    <div class="cdd-grid">`;

  SUITS.forEach(suit => {
    const color = (suit === 'h' || suit === 'd') ? 'red' : 'black';
    html += `<div class="cdd-suit-col">`;
    html += `<div class="cdd-suit-label ${SUIT_CLASS[suit]}">${SUIT_SYMBOLS[suit]}</div>`;
    RANKS.forEach(rank => {
      const card    = `${rank}${suit}`;
      const isSel   = sel.includes(card);
      const isUsed  = used.has(card);
      let cls = `cdd-card cdd-${color}`;
      if (isSel)  cls += ' cdd-selected';
      if (isUsed) cls += ' cdd-disabled';
      const handler = isUsed ? '' : `onclick="toggleDropCard('${field}','${card}')"`;
      html += `<div class="${cls}" data-suit="${suit}" ${handler}>${rank}</div>`;
    });
    html += `</div>`;
  });

  html += `</div>`;
  dd.innerHTML = html;
}

function toggleDropCard(field, card) {
  const sel    = dropState[field];
  const idx    = sel.indexOf(card);
  const isHand = field === 'hand';

  if (idx >= 0) {
    sel.splice(idx, 1);
  } else if (isHand) {
    // Hero hand: clicking 3rd card resets selection (same behaviour as modal picker)
    if (sel.length >= 2) dropState[field] = [card];
    else sel.push(card);
  } else {
    if (sel.length >= 5) return;
    sel.push(card);
  }

  // Apply live
  $(field).value = dropState[field].join(',');
  if (field === 'hand') renderCards(dropState.hand, 'heroCards', true);
  else                  updateLiveBoardStrip();

  // Auto-close hero hand when exactly 2 cards are chosen
  // (respects the dw-autoclose-hand setting; defaults to true)
  const autoClose = localStorage.getItem('dw-autoclose-hand') !== 'false';
  if (isHand && dropState.hand.length === 2 && autoClose) {
    _closeInlinePicker('hand');
    return;
  }

  _renderDropdown(field);
}

function clearInlinePicker(field) {
  dropState[field] = [];
  $(field).value = '';
  if (field === 'hand') renderCards([], 'heroCards', true);
  else                  updateLiveBoardStrip();
  // Re-render dropdown contents if it is currently open
  const dd = $(field + 'Dropdown');
  if (dd && dd.classList.contains('open')) _renderDropdown(field);
}

/* ══════════════════════════════════════════════════════════════
   CARD THEME — applied from localStorage (managed in /settings)
═══════════════════════════════════════════════════════════════ */

function setCardTheme(n) {
  document.body.setAttribute('data-card-theme', String(n));
  // No in-engine buttons to sync — theme is controlled from /settings
}

/* ══════════════════════════════════════════════════════════════
   HEBREW / LANGUAGE SUPPORT (i18n)
═══════════════════════════════════════════════════════════════ */

const I18N = {
  en: {
    'label-hero-hand':      'Hero Hand',
    'label-board':          'Board',
    'label-position':       'Position',
    'label-stack-depth':    'Stack Depth',
    'label-facing-action':  'Facing Action',
    'label-players':        'Players at Table',
    'label-pot':            'Pot',
    'label-bet':            'Bet to Call',
    'label-stack':          'Hero Stack',
    'label-simulations':    'Simulations',
    'label-hero-line':      'Hero Line',
    'label-hand-setup':     'Hand Setup',
    'label-decision-output':'Decision Output',
    'label-rec-action':     'Recommended Action',
    'label-cards':          'Cards',
    'help-hand':            'Select exactly 2 hole cards.',
    'help-board':           'Leave empty (pre-flop) or pick 3, 4, or 5 community cards.',
  },
  he: {
    'label-hero-hand':      'יד הגיבור',
    'label-board':          'לוח',
    'label-position':       'מיקום',
    'label-stack-depth':    'עומק מחסנית',
    'label-facing-action':  'פעולה מולה',
    'label-players':        'שחקנים בשולחן',
    'label-pot':            'סיר',
    'label-bet':            'הימור לקריאה',
    'label-stack':          'מחסנית גיבור',
    'label-simulations':    'סימולציות',
    'label-hero-line':      'קו גיבור',
    'label-hand-setup':     'הגדרת יד',
    'label-decision-output':'תוצאת החלטה',
    'label-rec-action':     'פעולה מומלצת',
    'label-cards':          'קלפים',
    'help-hand':            'בחר בדיוק 2 קלפים.',
    'help-board':           'השאר ריק (pre-flop) או בחר 3, 4 או 5 קלפי קהילה.',
  },
};

function applyTranslations(lang) {
  const dict = I18N[lang] || I18N.en;
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key] !== undefined) el.textContent = dict[key];
  });
}

function setLanguage(lang) {
  document.body.setAttribute('data-lang', lang);
  document.documentElement.setAttribute('dir', lang === 'he' ? 'rtl' : 'ltr');
  // No in-engine buttons to sync — language is controlled from /settings
  applyTranslations(lang);
}

/* ── Init ───────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  // Segment controls (only if elements exist — locked for non-pro)
  if (document.querySelector('.mode-group'))        initSegment('mode-group',        onModeChange);
  if (document.querySelector('.profile-group'))     initSegment('profile-group',     () => {});
  if (document.querySelector('.stack-depth-group')) initSegment('stack-depth-group', () => {});

  // Facing action buttons (fast mode)
  document.querySelectorAll('.facing-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.facing-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });

  // Close inline pickers when clicking outside the dropdown panel or its toggle button
  document.addEventListener('click', e => {
    ['hand', 'board'].forEach(field => {
      const dd = $(field + 'Dropdown');
      if (!dd || !dd.classList.contains('open')) return;
      if (dd.contains(e.target)) return;  // click inside the panel — ignore
      // If the click was on the "Pick Cards" toggle button for this field, let
      // toggleInlinePicker handle open/close rather than closing and reopening
      if (e.target.closest(`[data-picker-toggle="${field}"]`)) return;
      _closeInlinePicker(field);
    });
  });

  // Apply saved card theme (from /settings)
  const savedTheme = parseInt(localStorage.getItem('dw-card-theme') || '1', 10);
  setCardTheme(savedTheme);

  // Apply saved language (from /settings)
  const savedLang = localStorage.getItem('dw-lang') || 'en';
  setLanguage(savedLang);

  // Honor "Show Card Notation Guide" setting
  const showLegend = localStorage.getItem('dw-show-legend') !== 'false';
  const legendEl = document.querySelector('.legend');
  if (legendEl) legendEl.style.display = showLegend ? '' : 'none';

  // Plan-based UI adjustments
  initPlanUI();

  // Initial render (uses saved default position & mode from /settings)
  resetForm();
});
