#!/usr/bin/env python3
"""Phase 6 demo harness for payment-evidence to Loom brief generation.

This script is intentionally truthful:
- it writes explicit local demo payment evidence into an isolated temp journal
- it exercises the real subscription activation path used by checkout capture
- it never claims any blockchain transfer happened
- outbound delivery is disabled by default for demo safety
- it only prints a generated brief if the configured Loom runtime actually returns one
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
import tempfile
import uuid

WORKSPACE = os.path.dirname(os.path.abspath(__file__))
PLATFORM_DIR = os.path.join(WORKSPACE, 'company', 'meridian_platform')

if PLATFORM_DIR not in sys.path:
    sys.path.insert(0, PLATFORM_DIR)

import subscription_service


DEFAULT_PLAN = 'premium-brief-weekly'
DEFAULT_TELEGRAM_ID = 'phase6-demo-sink'
SCRIPT_ACTOR = 'script:run_live_demo_phase6'


@contextlib.contextmanager
def _temporary_environment(overrides):
    saved = {}
    try:
        for key, value in (overrides or {}).items():
            saved[key] = os.environ.get(key)
            if value in (None, ''):
                os.environ.pop(key, None)
            else:
                os.environ[key] = str(value)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextlib.contextmanager
def isolated_demo_environment(*, disable_outbound_dispatch=True, keep_state=False):
    tmpdir = tempfile.mkdtemp(prefix='phase6-live-demo-')
    subscriptions_file = os.path.join(tmpdir, 'subscriptions.json')
    subscriptions_backup = os.path.join(tmpdir, 'subscriptions.json.bak')
    subscriptions_lock = os.path.join(tmpdir, '.subscriptions.lock')
    transactions_file = os.path.join(tmpdir, 'transactions.jsonl')

    with open(subscriptions_file, 'w') as f:
        json.dump(subscription_service._default_subscriptions('org_demo_phase6'), f, indent=2)
    with open(subscriptions_backup, 'w') as f:
        json.dump(subscription_service._default_subscriptions('org_demo_phase6'), f, indent=2)
    with open(subscriptions_lock, 'w') as f:
        f.write('')
    with open(transactions_file, 'w') as f:
        f.write('')

    originals = {
        'subscriptions_path': subscription_service.subscriptions_path,
        'subscriptions_backup_path': subscription_service.subscriptions_backup_path,
        'subscriptions_lock_path': subscription_service.subscriptions_lock_path,
        'ensure_subscription_aliases': subscription_service.ensure_subscription_aliases,
        'load_transactions': subscription_service._revenue_mod.load_transactions,
        'dispatch_telegram': subscription_service._dispatch_telegram_delivery,
        'dispatch_email': subscription_service._dispatch_email_delivery,
    }

    def _load_transactions():
        with open(transactions_file) as f:
            entries = []
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entries.append(json.loads(line))
            return entries

    try:
        subscription_service.subscriptions_path = lambda org_id=None: subscriptions_file
        subscription_service.subscriptions_backup_path = lambda org_id=None: subscriptions_backup
        subscription_service.subscriptions_lock_path = lambda org_id=None: subscriptions_lock
        subscription_service.ensure_subscription_aliases = lambda org_id=None: None
        subscription_service._revenue_mod.load_transactions = _load_transactions
        if disable_outbound_dispatch:
            subscription_service._dispatch_telegram_delivery = lambda *args, **kwargs: None
            subscription_service._dispatch_email_delivery = lambda *args, **kwargs: None
        yield {
            'tmpdir': tmpdir,
            'subscriptions_file': subscriptions_file,
            'transactions_file': transactions_file,
            'outbound_dispatch_disabled': disable_outbound_dispatch,
        }
    finally:
        subscription_service.subscriptions_path = originals['subscriptions_path']
        subscription_service.subscriptions_backup_path = originals['subscriptions_backup_path']
        subscription_service.subscriptions_lock_path = originals['subscriptions_lock_path']
        subscription_service.ensure_subscription_aliases = originals['ensure_subscription_aliases']
        subscription_service._revenue_mod.load_transactions = originals['load_transactions']
        subscription_service._dispatch_telegram_delivery = originals['dispatch_telegram']
        subscription_service._dispatch_email_delivery = originals['dispatch_email']
        if not keep_state:
            shutil.rmtree(tmpdir, ignore_errors=True)


def _plan_option(plan_name):
    plan = subscription_service.PLANS[plan_name]
    return {
        'plan': plan_name,
        'price_usd': float(plan['price_usd']),
        'duration_days': int(plan['duration_days']),
        'billing_type': plan['type'],
    }


def build_demo_preview(*, telegram_id, email, plan):
    return {
        'preview_id': 'quote_phase6_live_demo',
        'pilot_request_id': 'pir_phase6_live_demo',
        'name': 'Phase 6 Demo Operator',
        'company': 'Meridian Demo Sink',
        'email': (email or '').strip(),
        'telegram_handle': (telegram_id or '').strip(),
        'requested_cadence': 'Weekly intelligence brief',
        'requested_offer': 'manual_pilot',
        'review_note': 'Local phase 6 demo harness using explicit local payment evidence only',
        'preview_truth_source': 'local_demo_harness_with_explicit_customer_payment_evidence_only',
        'state': 'reviewed',
        'plan_options': [_plan_option(plan)],
        'topics': ['pricing', 'provider launches', 'agent infrastructure'],
        'competitors': ['OpenAI', 'Anthropic'],
    }


def _write_demo_payment_evidence(transactions_file, *, plan, payment_ref):
    amount = float(subscription_service.PLANS[plan]['price_usd'])
    suffix = uuid.uuid4().hex[:10]
    entry = {
        'type': 'customer_payment',
        'order_id': f'ord_phase6_{suffix}',
        'amount': amount,
        'client': 'phase6-demo-client',
        'product': f'demo-{plan}',
        'payment_key': f'ref:{payment_ref}',
        'payment_ref': payment_ref,
        'tx_hash': f'phase6-demo-tx-{suffix}',
        'note': 'Local phase 6 demo evidence only. No blockchain settlement is claimed by this script.',
        'ts': subscription_service.now_ts(),
    }
    with open(transactions_file, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    return entry


def runtime_snapshot():
    capability_name = (subscription_service._loom_delivery_capability() or '').strip()
    loom_bin = subscription_service._loom_bin()
    loom_bin_exists = os.path.exists(loom_bin)
    preflight = {
        'ok': False,
        'runtime': 'loom',
        'capability_name': capability_name,
        'errors': [],
    }
    if not capability_name:
        preflight['errors'].append('No Loom delivery capability is configured')
    if not loom_bin_exists:
        preflight['errors'].append(f'Loom binary is missing at {loom_bin}')
    if capability_name and loom_bin_exists:
        preflight = subscription_service._loom_delivery_preflight(capability_name)

    runtime_lane = ((preflight.get('capability') or {}).get('runtime_lane') or '').strip()
    wasm_io_available = 'wasm' in runtime_lane.lower()
    runtime_path = 'loom_wasm_io' if wasm_io_available else 'current_truthful_subscription_loom_submit_path'
    return {
        'capability_name': capability_name,
        'loom_bin': loom_bin,
        'loom_bin_exists': loom_bin_exists,
        'runtime_lane': runtime_lane,
        'wasm_io_available': wasm_io_available,
        'runtime_path': runtime_path,
        'preflight': preflight,
    }


def run_demo(*, plan=DEFAULT_PLAN, telegram_id=DEFAULT_TELEGRAM_ID, email='', timeout=20,
             disable_outbound_dispatch=True, keep_state=False, capability_name=''):
    env_overrides = {}
    if capability_name:
        env_overrides['MERIDIAN_LOOM_SUBSCRIPTION_DELIVERY_CAPABILITY'] = capability_name

    with _temporary_environment(env_overrides):
        runtime = runtime_snapshot()
        payment_ref = f'phase6-demo-{uuid.uuid4().hex[:8]}'
        payment_evidence = {
            'payment_key': f'ref:{payment_ref}',
            'payment_ref': payment_ref,
        }

        with isolated_demo_environment(
            disable_outbound_dispatch=disable_outbound_dispatch,
            keep_state=keep_state,
        ) as demo_env:
            payment_entry = _write_demo_payment_evidence(
                demo_env['transactions_file'],
                plan=plan,
                payment_ref=payment_ref,
            )
            payment_evidence.update({
                'order_id': payment_entry['order_id'],
                'tx_hash': payment_entry['tx_hash'],
                'amount_usd': float(payment_entry['amount']),
            })
            preview = build_demo_preview(
                telegram_id=telegram_id,
                email=email,
                plan=plan,
            )
            capture_result = subscription_service.capture_subscription_from_preview(
                preview,
                telegram_id=telegram_id,
                plan=plan,
                payment_method='captured',
                payment_ref=payment_ref,
                payment_evidence=payment_evidence,
                org_id='org_demo_phase6',
                actor=SCRIPT_ACTOR,
                timeout=int(timeout),
            )
            state = subscription_service.load_subscriptions('org_demo_phase6')
            return {
                'runtime': runtime,
                'demo_state_dir': demo_env['tmpdir'],
                'outbound_dispatch_disabled': demo_env['outbound_dispatch_disabled'],
                'payment_entry': payment_entry,
                'payment_evidence': payment_evidence,
                'preview': preview,
                'capture_result': capture_result,
                'subscriptions_state': state,
            }


def _print_result(result):
    runtime = result['runtime']
    capture_result = result['capture_result']
    subscription = capture_result['subscription']
    run = capture_result.get('delivery_run', {})
    execution = capture_result.get('delivery_execution', {})
    artifact = capture_result.get('delivery_artifact', {})

    print('Phase 6 Live Demo')
    print(f"workspace: {WORKSPACE}")
    print(f"demo_state_dir: {result['demo_state_dir']}")
    print(f"outbound_dispatch_disabled: {str(result['outbound_dispatch_disabled']).lower()}")
    print('blockchain_transfer_claimed: false')
    print(f"runtime_path: {runtime['runtime_path']}")
    print(f"wasm_io_available: {str(runtime['wasm_io_available']).lower()}")
    print(f"loom_bin: {runtime['loom_bin']}")
    print(f"loom_bin_exists: {str(runtime['loom_bin_exists']).lower()}")
    print(f"capability_name: {runtime['capability_name'] or '<unset>'}")
    print(f"preflight_ok: {str(bool(runtime['preflight'].get('ok'))).lower()}")
    if runtime['preflight'].get('errors'):
        print('preflight_errors:')
        for error in runtime['preflight']['errors']:
            print(f'  - {error}')
    print(f"payment_ref: {result['payment_entry']['payment_ref']}")
    print(f"payment_order_id: {result['payment_entry']['order_id']}")
    print(f"payment_tx_hash: {result['payment_entry']['tx_hash']}")
    print(f"subscription_id: {subscription.get('id', '')}")
    print(f"subscription_status: {subscription.get('status', '')}")
    print(f"payment_verified: {str(bool(subscription.get('payment_verified'))).lower()}")
    print(f"delivery_run_state: {run.get('state', '')}")
    print(f"delivery_status: {run.get('delivery_status', '')}")
    print(f"delivered: {str(bool(run.get('delivered'))).lower()}")
    print(f"delivery_ref: {run.get('delivery_ref', '') or '<none>'}")
    print(f"execution_ok: {str(bool(execution.get('ok'))).lower()}")
    if execution.get('error'):
        print(f"execution_error: {execution['error']}")
    if artifact.get('result_path'):
        print(f"brief_result_path: {artifact['result_path']}")
    else:
        print('brief_result_path: <none>')
    print('brief_text:')
    brief_text = (artifact.get('brief_text') or '').strip()
    if brief_text:
        print(brief_text)
    else:
        print('<none>')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run the phase 6 subscription activation + Loom brief demo harness')
    parser.add_argument('--plan', default=DEFAULT_PLAN, choices=sorted(subscription_service.PLANS.keys()))
    parser.add_argument('--telegram-id', default=DEFAULT_TELEGRAM_ID, help='Demo subscriber identifier used for activation')
    parser.add_argument('--email', default='', help='Optional demo email address')
    parser.add_argument('--timeout', type=int, default=20, help='Loom execution timeout in seconds')
    parser.add_argument('--capability-name', default='', help='Optional Loom delivery capability override')
    parser.add_argument('--enable-outbound-dispatch', action='store_true', help='Allow real outbound dispatch to configured channels')
    parser.add_argument('--keep-state', action='store_true', help='Keep isolated temp state for inspection after the run')
    args = parser.parse_args(argv)

    try:
        result = run_demo(
            plan=args.plan,
            telegram_id=args.telegram_id,
            email=args.email,
            timeout=args.timeout,
            disable_outbound_dispatch=not args.enable_outbound_dispatch,
            keep_state=args.keep_state,
            capability_name=args.capability_name,
        )
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

    _print_result(result)
    artifact = result['capture_result'].get('delivery_artifact', {})
    return 0 if (artifact.get('brief_text') or '').strip() else 1


if __name__ == '__main__':
    raise SystemExit(main())
