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
