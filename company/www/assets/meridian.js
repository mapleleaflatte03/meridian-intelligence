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
  var intakeForm = document.querySelector('[data-pilot-intake-form]');
  var checkoutForm = document.querySelector('[data-checkout-capture-form]');
  if (!intakeForm && !checkoutForm) {
    return;
  }

  var intakeStatus = intakeForm ? intakeForm.querySelector('[data-pilot-intake-status]') : null;
  var intakeSubmitButton = intakeForm ? intakeForm.querySelector('button[type="submit"]') : null;
  var intakeEndpoint = intakeForm ? (intakeForm.getAttribute('action') || '/api/pilot/intake') : '';

  var checkoutShell = document.querySelector('[data-checkout-shell]');
  var checkoutStatus = checkoutForm ? checkoutForm.querySelector('[data-checkout-status]') : null;
  var checkoutSubmitButton = checkoutForm ? checkoutForm.querySelector('button[type="submit"]') : null;
  var checkoutEndpoint = checkoutForm ? (checkoutForm.getAttribute('action') || '/api/subscriptions/checkout-capture') : '';
  var checkoutPreviewId = checkoutForm ? checkoutForm.querySelector('[name="preview_id"]') : null;
  var checkoutPlan = checkoutForm ? checkoutForm.querySelector('[name="plan"]') : null;
  var checkoutPrice = checkoutForm ? checkoutForm.querySelector('[name="amount_usd"]') : null;
  var checkoutTelegramId = checkoutForm ? checkoutForm.querySelector('[name="telegram_id"]') : null;
  var checkoutTxHash = checkoutForm ? checkoutForm.querySelector('[name="tx_hash"]') : null;
  var checkoutPayerWallet = checkoutForm ? checkoutForm.querySelector('[name="payer_wallet"]') : null;

  function setStatus(node, message, isError) {
    if (!node) {
      return;
    }
    node.textContent = message;
    node.style.color = isError ? '#ff9b9b' : '';
  }

  function setText(selector, value) {
    var node = document.querySelector(selector);
    if (node) {
      node.textContent = value || '';
    }
  }

  function showCheckout(data) {
    var preview = data && data.subscription_preview ? data.subscription_preview : {};
    var checkout = data && data.checkout ? data.checkout : {};
    var offer = checkout.offer || {};
    var instructions = offer.payment_instructions || {};
    if (checkoutShell) {
      checkoutShell.hidden = false;
    }
    if (checkoutPreviewId) {
      checkoutPreviewId.value = preview.preview_id || '';
    }
    if (checkoutPlan) {
      checkoutPlan.value = offer.plan || '';
    }
    if (checkoutPrice) {
      checkoutPrice.value = String(instructions.amount_usd || offer.price_usd || '');
    }
    setText('[data-checkout-preview-id]', preview.preview_id || '');
    setText('[data-checkout-plan-name]', offer.plan || '');
    setText('[data-checkout-amount]', '$' + String(instructions.amount_usd || offer.price_usd || ''));
    setText('[data-checkout-wallet]', instructions.recipient_wallet || '');
    setText('[data-checkout-chain]', instructions.network ? instructions.network + ' (Chain ID ' + String(instructions.chain_id || '') + ')' : '');
    setText('[data-checkout-asset]', instructions.asset || '');
    setText('[data-checkout-note]', instructions.note || '');
    setStatus(checkoutStatus, 'Quote published. Pay the exact amount, then paste the Base transaction hash below to activate delivery.', false);
    if (checkoutTxHash) {
      checkoutTxHash.focus();
    }
  }

  if (intakeForm) {
    intakeForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var payload = {};
      new FormData(intakeForm).forEach(function (value, key) {
        payload[key] = value;
      });
      payload.publish_preview = true;
      payload.requested_offer = payload.requested_offer || 'founder-pilot-week';
      if (intakeSubmitButton) {
        intakeSubmitButton.disabled = true;
      }
      setStatus(intakeStatus, 'Publishing quote and checkout instructions...', false);
      try {
        var intakeResponse = await fetch(intakeEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        var intakeData = await intakeResponse.json();
        if (!intakeResponse.ok) {
          throw new Error(intakeData && intakeData.error ? intakeData.error : 'Pilot intake submission failed');
        }
        setStatus(intakeStatus, 'Quote ready. Preview ' + intakeData.subscription_preview.preview_id + ' is now live for checkout capture.', false);
        showCheckout(intakeData);
      } catch (error) {
        setStatus(intakeStatus, error.message || 'Pilot intake submission failed', true);
      } finally {
        if (intakeSubmitButton) {
          intakeSubmitButton.disabled = false;
        }
      }
    });
  }

  if (checkoutForm) {
    checkoutForm.addEventListener('submit', async function (event) {
      event.preventDefault();
      var txHash = checkoutTxHash ? checkoutTxHash.value.trim() : '';
      var payload = {
        preview_id: checkoutPreviewId ? checkoutPreviewId.value : '',
        plan: checkoutPlan ? checkoutPlan.value : '',
        payment_method: 'base_usdc',
        payment_ref: txHash,
        payment_evidence: {
          tx_hash: txHash,
          payment_ref: txHash,
          amount_usd: checkoutPrice ? Number(checkoutPrice.value || 0) : 0,
        },
      };
      if (checkoutTelegramId && checkoutTelegramId.value.trim()) {
        payload.telegram_id = checkoutTelegramId.value.trim();
      }
      if (checkoutPayerWallet && checkoutPayerWallet.value.trim()) {
        payload.payment_evidence.payer_wallet = checkoutPayerWallet.value.trim();
      }
      if (checkoutSubmitButton) {
        checkoutSubmitButton.disabled = true;
      }
      setStatus(checkoutStatus, 'Verifying Base USDC payment and activating delivery...', false);
      try {
        var checkoutResponse = await fetch(checkoutEndpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        var checkoutData = await checkoutResponse.json();
        if (!checkoutResponse.ok) {
          throw new Error(checkoutData && checkoutData.error ? checkoutData.error : 'Checkout capture failed');
        }
        var result = checkoutData.result || {};
        var deliveryRun = result.delivery_run || {};
        var channel = deliveryRun.delivery_channel || '';
        var target = deliveryRun.delivery_target || '';
        var summary = 'Activation complete. Subscription ' + ((result.subscription && result.subscription.id) || '') + ' is active.';
        if (deliveryRun.delivered) {
          summary += ' Delivery sent via ' + channel + (target ? ' to ' + target : '') + '.';
        } else if (deliveryRun.delivery_status) {
          summary += ' Delivery status: ' + deliveryRun.delivery_status + '.';
        }
        setStatus(checkoutStatus, summary, false);
      } catch (error) {
        setStatus(checkoutStatus, error.message || 'Checkout capture failed', true);
      } finally {
        if (checkoutSubmitButton) {
          checkoutSubmitButton.disabled = false;
        }
      }
    });
  }
})();

(function () {
  'use strict';
  var shell = document.querySelector('[data-trust-ops-shell]');
  if (!shell) {
    return;
  }

  var filterForm = shell.querySelector('[data-trust-ops-filter-form]');
  var statusNode = shell.querySelector('[data-trust-ops-status]');
  var queueBody = shell.querySelector('[data-trust-ops-queue-body]');
  var questionnaireDetail = shell.querySelector('[data-trust-ops-questionnaire-detail]');
  var questionnaireSelect = shell.querySelector('[name="questionnaire_id"]');
  var summaryActionable = shell.querySelector('[data-summary-actionable]');
  var summaryPending = shell.querySelector('[data-summary-pending]');
  var summaryReady = shell.querySelector('[data-summary-ready]');
  var summaryEvidence = shell.querySelector('[data-summary-evidence]');
  var currentSnapshot = null;

  function setOperatorStatus(message, isError) {
    if (!statusNode) {
      return;
    }
    statusNode.textContent = message;
    statusNode.style.color = isError ? '#ff9b9b' : '';
  }

  function safeText(value) {
    return value == null ? '' : String(value);
  }

  function escapeHtml(value) {
    return safeText(value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatTimestamp(value) {
    if (!value) {
      return 'Not reviewed yet';
    }
    var date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return safeText(value);
    }
    return date.toLocaleString();
  }

  function badgeClass(bucket) {
    if (bucket === 'pending' || bucket === 'stale') {
      return 'status-warn';
    }
    if (bucket === 'revoked') {
      return 'status-bad';
    }
    if (bucket === 'approved') {
      return 'status-good';
    }
    return 'status-neutral';
  }

  function renderQuestionnaireOptions(operator) {
    if (!questionnaireSelect) {
      return;
    }
    var selected = operator && operator.filters ? operator.filters.questionnaire_id || '' : '';
    var options = ['<option value="">All questionnaires</option>'];
    (operator && operator.questionnaires ? operator.questionnaires : []).forEach(function (item) {
      var qid = safeText(item.questionnaire_id);
      var selectedAttr = qid === selected ? ' selected' : '';
      options.push('<option value="' + escapeHtml(qid) + '"' + selectedAttr + '>' + escapeHtml(qid) + '</option>');
    });
    questionnaireSelect.innerHTML = options.join('');
  }

  function renderSummary(operator) {
    var summary = operator && operator.summary ? operator.summary : {};
    var counts = operator && operator.counts ? operator.counts : {};
    var evidenceCounts = summary && summary.evidence && summary.evidence.counts ? summary.evidence.counts : {};
    if (summaryActionable) {
      summaryActionable.textContent = String(counts.actionable || 0);
    }
    if (summaryPending) {
      summaryPending.textContent = String(counts.pending || summary.pending_queue_count || 0);
    }
    if (summaryReady) {
      summaryReady.textContent = String(summary.ready_questionnaire_count || 0);
    }
    if (summaryEvidence) {
      summaryEvidence.textContent = [
        evidenceCounts.approved || 0,
        evidenceCounts.draft || 0,
        evidenceCounts.stale || 0,
        evidenceCounts.revoked || 0
      ].join(' / ');
    }
  }

  function renderQueue(operator) {
    var rows = operator && operator.queue ? operator.queue : [];
    if (!queueBody) {
      return;
    }
    if (!rows.length) {
      queueBody.innerHTML = '<tr><td colspan="6">No queue items match the current filter.</td></tr>';
      return;
    }
    queueBody.innerHTML = rows.map(function (item) {
      var bucket = safeText(item.bucket);
      var reviewMeta = item.reviewed_at
        ? '<div class="operator-meta">Reviewed by ' + escapeHtml(item.reviewed_by || 'operator') + ' · ' + escapeHtml(formatTimestamp(item.reviewed_at)) + '</div>'
        : '<div class="operator-meta">No explicit review recorded yet.</div>';
      var noteMeta = item.review_note
        ? '<div class="operator-meta">' + escapeHtml(item.review_note) + '</div>'
        : '';
      var questionMeta = [
        item.critical ? '<span class="operator-chip operator-chip-critical">Critical</span>' : '<span class="operator-chip">Standard</span>',
        item.approval_required ? '<span class="operator-chip operator-chip-action">Approval required</span>' : '<span class="operator-chip">Delivery allowed</span>'
      ].join(' ');
      var actionButtons = ['approve', 'stale', 'revoke', 'unresolved'].map(function (decision) {
        return '<button type="button" class="operator-action" data-queue-id="' + escapeHtml(item.queue_id) + '" data-decision="' + decision + '">' + escapeHtml(decision) + '</button>';
      }).join('');
      return '<tr>' +
        '<td data-label="Question"><strong>' + escapeHtml(item.question_text || item.question_id) + '</strong><div class="operator-meta">' + questionMeta + '</div>' + noteMeta + '</td>' +
        '<td data-label="State"><span class="' + badgeClass(bucket) + '">' + escapeHtml(bucket) + '</span><div class="operator-meta">Gate: ' + escapeHtml(item.approval_gate_status || 'unknown') + '</div></td>' +
        '<td data-label="Evidence"><code>' + escapeHtml(item.evidence_key || 'none') + '</code><div class="operator-meta">Status: ' + escapeHtml(item.evidence_status || 'none') + '</div></td>' +
        '<td data-label="Owner"><strong>' + escapeHtml(item.origin_agent || 'main') + '</strong>' + reviewMeta + '</td>' +
        '<td data-label="Questionnaire"><button type="button" class="operator-link" data-select-questionnaire="' + escapeHtml(item.questionnaire_id) + '">' + escapeHtml(item.questionnaire_id) + '</button><div class="operator-meta">' + escapeHtml(item.source_session_key || '') + '</div></td>' +
        '<td data-label="Action"><div class="operator-actions">' + actionButtons + '</div></td>' +
      '</tr>';
    }).join('');
  }

  function renderQuestionnaireDetail(operator) {
    if (!questionnaireDetail) {
      return;
    }
    var selected = operator ? operator.selected_questionnaire : null;
    if (!selected) {
      questionnaireDetail.innerHTML = '<p class="dim">No questionnaire selected yet.</p>';
      return;
    }
    var questions = selected.questions || [];
    var questionItems = questions.map(function (item) {
      var bucket = item.approval_required ? 'pending' : (item.answer_state || 'unknown');
      var evidenceLine = item.evidence_key
        ? '<div class="operator-meta">Evidence: <code>' + escapeHtml(item.evidence_key) + '</code> · ' + escapeHtml(item.evidence_status || 'unknown') + '</div>'
        : '<div class="operator-meta">No evidence key linked.</div>';
      var reviewLine = item.last_reviewed_at
        ? '<div class="operator-meta">Last review: ' + escapeHtml(item.last_review_decision || 'reviewed') + ' · ' + escapeHtml(formatTimestamp(item.last_reviewed_at)) + '</div>'
        : '';
      var resolutionLine = item.resolution_note
        ? '<div class="operator-meta">' + escapeHtml(item.resolution_note) + '</div>'
        : '';
      return '<article class="operator-question-card">' +
        '<div class="operator-question-head"><strong>' + escapeHtml(item.text || item.question_id) + '</strong><span class="' + badgeClass(bucket) + '">' + escapeHtml(bucket) + '</span></div>' +
        '<div class="operator-meta">Owner: ' + escapeHtml(item.origin_agent || 'main') + (item.critical ? ' · Critical' : '') + '</div>' +
        evidenceLine + reviewLine + resolutionLine +
      '</article>';
    }).join('');
    questionnaireDetail.innerHTML =
      '<div class="operator-detail-head">' +
        '<div><strong>' + escapeHtml(selected.questionnaire_id) + '</strong><div class="operator-meta">' + escapeHtml(selected.source_session_key || '') + '</div></div>' +
        '<div class="operator-detail-metrics">' +
          '<span class="' + badgeClass(selected.pending_approval_count ? 'pending' : 'approved') + '">' + escapeHtml(selected.approval_gate_status || 'unknown') + '</span>' +
          '<span class="operator-chip">' + escapeHtml(String(selected.pending_approval_count || 0)) + ' pending</span>' +
          '<span class="operator-chip">' + escapeHtml(String(selected.critical_count || 0)) + ' critical</span>' +
        '</div>' +
      '</div>' +
      '<div class="operator-question-grid">' + questionItems + '</div>';
  }

  function buildOperatorStatusMessage(operator) {
    var filters = operator && operator.filters ? operator.filters : {};
    var queue = operator && operator.queue ? operator.queue : [];
    var questionnaires = operator && operator.questionnaires ? operator.questionnaires : [];
    var selectedQuestionnaire = filters.questionnaire_id || '';
    var filterLabelMap = {
      actionable: 'actionable items',
      all: 'all visible items',
      pending: 'pending approvals',
      approved: 'approved items',
      stale: 'stale items',
      revoked: 'revoked items',
      unresolved: 'unresolved items',
      draft: 'draft items'
    };
    var filterLabel = filterLabelMap[filters.status] || 'queue items';
    var parts = [
      'Trust Ops queue loaded.',
      'Showing ' + String(queue.length) + ' ' + filterLabel + '.'
    ];
    if (selectedQuestionnaire) {
      parts.push('Focused on ' + selectedQuestionnaire + '.');
    } else if (questionnaires.length) {
      parts.push('Tracking ' + String(questionnaires.length) + ' questionnaires.');
    }
    if (filters.include_cleared) {
      parts.push('Cleared items included.');
    }
    return parts.join(' ');
  }

  async function loadOperatorSnapshot() {
    var params = new URLSearchParams();
    var formData = new FormData(filterForm);
    formData.forEach(function (value, key) {
      if (key === 'include_cleared') {
        return;
      }
      if (value) {
        params.set(key, value);
      }
    });
    var includeCleared = filterForm.querySelector('[name="include_cleared"]');
    if (includeCleared && includeCleared.checked) {
      params.set('include_cleared', 'true');
    }
    setOperatorStatus('Refreshing Trust Ops queue…', false);
    try {
      var response = await fetch('/api/trust-ops/queue?' + params.toString());
      var payload = await response.json();
      if (!response.ok) {
        throw new Error(payload && payload.output ? payload.output : 'Failed to load Trust Ops queue');
      }
      currentSnapshot = payload.operator || null;
      renderQuestionnaireOptions(currentSnapshot);
      renderSummary(currentSnapshot);
      renderQueue(currentSnapshot);
      renderQuestionnaireDetail(currentSnapshot);
      setOperatorStatus(buildOperatorStatusMessage(currentSnapshot), false);
    } catch (error) {
      setOperatorStatus(error.message || 'Failed to load Trust Ops queue', true);
      if (queueBody) {
        queueBody.innerHTML = '<tr><td colspan="6">Could not load queue.</td></tr>';
      }
      if (questionnaireDetail) {
        questionnaireDetail.innerHTML = '<p class="dim">Could not load questionnaire detail.</p>';
      }
    }
  }

  async function reviewQueueItem(queueId, decision) {
    var note = window.prompt('Review note for ' + decision + ' on ' + queueId + ':', '');
    if (note === null) {
      return;
    }
    setOperatorStatus('Submitting ' + decision + ' review for ' + queueId + '…', false);
    try {
      var response = await fetch('/api/trust-ops/queue/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          queue_id: queueId,
          decision: decision,
          note: note,
          actor: 'web-operator'
        })
      });
      var payload = await response.json();
      if (!response.ok) {
        throw new Error(payload && payload.output ? payload.output : 'Queue review failed');
      }
      setOperatorStatus('Queue item ' + queueId + ' reviewed as ' + decision + '.', false);
      await loadOperatorSnapshot();
    } catch (error) {
      setOperatorStatus(error.message || 'Queue review failed', true);
    }
  }

  filterForm.addEventListener('submit', function (event) {
    event.preventDefault();
    loadOperatorSnapshot();
  });

  shell.addEventListener('click', function (event) {
    var selectButton = event.target.closest('[data-select-questionnaire]');
    if (selectButton && questionnaireSelect) {
      questionnaireSelect.value = selectButton.getAttribute('data-select-questionnaire') || '';
      loadOperatorSnapshot();
      return;
    }
    var actionButton = event.target.closest('[data-queue-id][data-decision]');
    if (actionButton) {
      reviewQueueItem(
        actionButton.getAttribute('data-queue-id') || '',
        actionButton.getAttribute('data-decision') || ''
      );
    }
  });

  loadOperatorSnapshot();
})();
