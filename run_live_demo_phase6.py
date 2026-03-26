#!/usr/bin/env python3
"""Phase 6 demo harness for payment-evidence to Loom brief generation.

This script is intentionally truthful:
- it writes explicit local demo payment evidence into an isolated temp journal
- it exercises the real subscription activation path used by checkout capture
- it only claims a blockchain transfer if a real broadcast tx hash exists; offline
  signed raw hex is surfaced separately as a non-broadcast artifact
- outbound delivery is disabled by default for demo safety
- it only prints a generated brief if the configured Loom runtime actually returns one
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
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
DIRECT_LOOM_BIN = '/home/ubuntu/.local/share/meridian-loom/current/bin/loom'
DIRECT_LOOM_ROOT = '/home/ubuntu/.local/share/meridian-loom/runtime/default'
DIRECT_LOOM_ORG_ID = 'org_51fcd87f'
DIRECT_LOOM_AGENT_ID = 'agent_atlas'
DIRECT_LOOM_CAPABILITY = 'loom.browser.navigate.v1'
DIRECT_LOOM_URL = 'http://example.com/'
DIRECT_X402_WALLET_ID = 'automated_loom_settlement_v1'
DIRECT_X402_SOURCE_ACCOUNT_ID = 'automated_loom_settlement'
DIRECT_X402_SETTLEMENT_ADAPTER = 'base_usdc_x402'
DIRECT_X402_PROOF_TYPE = 'signed_raw_transaction_offline'
DIRECT_X402_TOKEN_CONTRACT = '0x036CbD53842c5426634e7929541eC2318f3dCF7e'
DIRECT_X402_RECIPIENT = '0x2222222222222222222222222222222222222222'
DIRECT_X402_AMOUNT_USDC = '1.00'
DIRECT_X402_CHAIN_ID = 84532
DIRECT_X402_NONCE = 0
DIRECT_X402_GAS_LIMIT = 65000
DIRECT_X402_GAS_PRICE_WEI = 1_000_000_000
KERNEL_TREASURY_PATH = '/tmp/meridian-kernel/kernel/treasury.py'
KERNEL_CAPSULE_PATH = '/tmp/meridian-kernel/kernel/capsule.py'
HOT_WALLET_SECRET_PATH = '/tmp/meridian-kernel/.hot-wallet-secrets/automated_loom_settlement_v1.json'
_KERNEL_TREASURY_MODULE = None


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



def _loom_cli_prefix(loom_bin):
    prefix = []
    if os.geteuid() == 0 and shutil.which('sudo'):
        prefix.extend(['sudo', '-u', 'ubuntu', '-H'])
    prefix.append(loom_bin)
    return prefix


def _run_loom_json(args, *, loom_bin, timeout):
    command = _loom_cli_prefix(loom_bin) + list(args)
    try:
        completed = subprocess.run(
            command,
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            timeout=int(timeout),
        )
    except subprocess.TimeoutExpired:
        return {
            'ok': False,
            'payload': {},
            'stdout': '',
            'stderr': '',
            'error': f'loom command timed out after {int(timeout)}s',
            'command': command,
            'returncode': -1,
        }
    output = (completed.stdout or '').strip()
    payload = {}
    if output:
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = {}
    error = ((completed.stderr or '').strip() or payload.get('error') or output or '').strip()
    return {
        'ok': completed.returncode == 0,
        'payload': payload,
        'stdout': output,
        'stderr': (completed.stderr or '').strip(),
        'error': error,
        'command': command,
        'returncode': completed.returncode,
    }


def _load_worker_result(path):
    candidate = (path or '').strip()
    if not candidate or not os.path.exists(candidate):
        return {}
    with open(candidate) as f:
        return json.load(f)


def _host_response_brief_text(worker_result):
    host_response = (worker_result or {}).get('host_response_json')
    if isinstance(host_response, str):
        try:
            host_response = json.loads(host_response)
        except Exception:
            host_response = {}
    if not isinstance(host_response, dict):
        return ''
    title = str(host_response.get('title') or '').strip()
    excerpt = str(host_response.get('body_excerpt_utf8') or '').strip()
    parts = [part for part in (title, excerpt) if part]
    return '\n\n'.join(parts).strip()




def _load_kernel_treasury_module():
    global _KERNEL_TREASURY_MODULE
    if _KERNEL_TREASURY_MODULE is not None:
        return _KERNEL_TREASURY_MODULE
    if not os.path.exists(KERNEL_TREASURY_PATH):
        return None
    kernel_dir = os.path.dirname(KERNEL_TREASURY_PATH)
    if kernel_dir in sys.path:
        sys.path.remove(kernel_dir)
    sys.path.insert(0, kernel_dir)
    if os.path.exists(KERNEL_CAPSULE_PATH):
        capsule_spec = importlib.util.spec_from_file_location('capsule', KERNEL_CAPSULE_PATH)
        capsule_module = importlib.util.module_from_spec(capsule_spec)
        sys.modules['capsule'] = capsule_module
        capsule_spec.loader.exec_module(capsule_module)
    spec = importlib.util.spec_from_file_location('meridian_kernel_treasury_phase6', KERNEL_TREASURY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _KERNEL_TREASURY_MODULE = module
    return module




def _kernel_signer_org_id(treasury=None):
    treasury = treasury or _load_kernel_treasury_module()
    if treasury is not None:
        resolver = getattr(treasury, '_default_org_id', None)
        if callable(resolver):
            resolved = str(resolver() or '').strip()
            if resolved:
                return resolved
    return DIRECT_LOOM_ORG_ID


def _load_hot_wallet_secret():
    if not os.path.exists(HOT_WALLET_SECRET_PATH):
        return {}
    with open(HOT_WALLET_SECRET_PATH) as f:
        return json.load(f)



def _offline_x402_signature(timeout):
    treasury = _load_kernel_treasury_module()
    if treasury is None:
        return {
            'ok': False,
            'error': f'Kernel treasury module is missing at {KERNEL_TREASURY_PATH}',
            'signing': {},
        }
    secret = _load_hot_wallet_secret()
    private_key = str(secret.get('private_key_hex') or '').strip()
    if not private_key:
        return {
            'ok': False,
            'error': f'Hot wallet secret is missing a private key at {HOT_WALLET_SECRET_PATH}',
            'signing': {},
        }
    try:
        signing = treasury.sign_x402_transfer_from_wallet(
            SCRIPT_ACTOR,
            org_id=_kernel_signer_org_id(treasury),
            source_account_id=DIRECT_X402_SOURCE_ACCOUNT_ID,
            sender_wallet_id=DIRECT_X402_WALLET_ID,
            recipient_address=DIRECT_X402_RECIPIENT,
            amount_usdc=DIRECT_X402_AMOUNT_USDC,
            token_contract_address=DIRECT_X402_TOKEN_CONTRACT,
            private_key=private_key,
            chain_id=DIRECT_X402_CHAIN_ID,
            nonce=DIRECT_X402_NONCE,
            gas_limit=DIRECT_X402_GAS_LIMIT,
            gas_price_wei=DIRECT_X402_GAS_PRICE_WEI,
            host_supported_adapters=[DIRECT_X402_SETTLEMENT_ADAPTER],
            timeout_seconds=max(1, min(int(timeout), 10)),
        )
    except Exception as exc:
        return {
            'ok': False,
            'error': str(exc),
            'signing': {},
        }
    signed_transaction = dict(signing.get('signed_transaction') or {})
    raw_hex = str(signed_transaction.get('raw_transaction_hex') or '').strip()
    return {
        'ok': bool(signing.get('signing_performed') and raw_hex),
        'error': '' if (signing.get('signing_performed') and raw_hex) else 'offline x402 signing did not produce raw_transaction_hex',
        'signing': signing,
        'raw_hex': raw_hex,
    }



def _attach_offline_x402_signature(result, *, timeout):
    runtime = dict(result.get('runtime') or {})
    capture_result = dict(result.get('capture_result') or {})
    delivery_artifact = dict(capture_result.get('delivery_artifact') or {})
    if not runtime.get('loom_bin_exists'):
        return result
    if not (delivery_artifact.get('brief_text') or '').strip():
        return result

    signing_result = _offline_x402_signature(timeout)
    result['offline_x402_signing'] = signing_result.get('signing', {})
    if not signing_result.get('ok'):
        return result

    signing = dict(signing_result.get('signing') or {})
    signed_transaction = dict(signing.get('signed_transaction') or {})
    execution_refs = dict((capture_result.get('delivery_run') or {}).get('execution_refs') or {})
    execution_refs.update({
        'settlement_adapter': DIRECT_X402_SETTLEMENT_ADAPTER,
        'proof_type': DIRECT_X402_PROOF_TYPE,
        'proof': {
            'signed_raw_hex': signing_result.get('raw_hex', ''),
            'signed_tx_hash': signed_transaction.get('signed_tx_hash', ''),
            'sender_address': signed_transaction.get('sender_address', ''),
            'wallet_id': DIRECT_X402_WALLET_ID,
            'source_account_id': DIRECT_X402_SOURCE_ACCOUNT_ID,
            'broadcast_requested': bool((signing.get('broadcast') or {}).get('requested')),
            'broadcast_attempted': bool((signing.get('broadcast') or {}).get('attempted')),
            'truth_boundary': signing.get('truth_boundary', ''),
            'actual_transfer_blockers': list(signing.get('actual_transfer_blockers') or []),
        },
    })
    delivery_run = dict(capture_result.get('delivery_run') or {})
    delivery_run['execution_refs'] = execution_refs
    capture_result['delivery_run'] = delivery_run

    delivery_execution = dict(capture_result.get('delivery_execution') or {})
    delivery_execution['execution_refs'] = execution_refs
    capture_result['delivery_execution'] = delivery_execution
    result['capture_result'] = capture_result
    return result


def _builtin_browser_capability_ready(loom_bin, *, timeout=10):
    capability_result = _run_loom_json(
        [
            'capability',
            'show',
            '--root',
            DIRECT_LOOM_ROOT,
            '--name',
            DIRECT_LOOM_CAPABILITY,
            '--format',
            'json',
        ],
        loom_bin=loom_bin,
        timeout=timeout,
    )
    payload = dict(capability_result.get('payload') or {})
    ready = (
        capability_result.get('ok')
        and payload.get('name') == DIRECT_LOOM_CAPABILITY
        and bool(payload.get('enabled'))
    )
    return {
        'ok': bool(ready),
        'capability': payload,
        'error': capability_result.get('error', ''),
        'command': capability_result.get('command', []),
    }


def runtime_snapshot():
    configured_capability = (subscription_service._loom_delivery_capability() or '').strip()
    capability_name = configured_capability
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
    elif loom_bin_exists:
        direct_preflight = _builtin_browser_capability_ready(loom_bin, timeout=10)
        if direct_preflight['ok']:
            capability_name = DIRECT_LOOM_CAPABILITY
            preflight = {
                'ok': True,
                'runtime': 'loom_direct_action_execute',
                'capability_name': capability_name,
                'errors': [],
                'capability': direct_preflight.get('capability', {}),
            }

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


def _delivery_blockchain_artifact(payload, *, source=''):
    payload = dict(payload or {})
    if not payload:
        return {}

    settlement_adapter = (payload.get('settlement_adapter') or '').strip()
    proof_type = (payload.get('proof_type') or '').strip()
    for field in ('signed_raw_hex', 'signed_tx_hex', 'raw_hex'):
        value = (payload.get(field) or '').strip()
        if value:
            return {
                'artifact_type': field,
                'artifact': value,
                'artifact_source': source,
                'settlement_adapter': settlement_adapter,
                'proof_type': proof_type,
            }

    tx_hash = (payload.get('tx_hash') or '').strip()
    if tx_hash:
        return {
            'artifact_type': 'tx_hash',
            'artifact': tx_hash,
            'artifact_source': source,
            'settlement_adapter': settlement_adapter,
            'proof_type': proof_type,
        }

    for nested_key in ('proof', 'settlement_proof', 'execution_refs'):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            artifact = _delivery_blockchain_artifact(
                nested,
                source=f'{source}.{nested_key}' if source else nested_key,
            )
            if artifact:
                if not artifact.get('settlement_adapter'):
                    artifact['settlement_adapter'] = settlement_adapter
                if not artifact.get('proof_type'):
                    artifact['proof_type'] = proof_type
                return artifact

    return {}


def delivery_blockchain_artifact(result):
    capture_result = dict((result or {}).get('capture_result') or {})
    delivery_execution = dict(capture_result.get('delivery_execution') or {})
    delivery_run = dict(capture_result.get('delivery_run') or {})
    delivery_job = dict(capture_result.get('delivery_job') or {})
    delivery_artifact = dict(capture_result.get('delivery_artifact') or {})

    candidates = [
        ('delivery_execution.worker_result', delivery_execution.get('worker_result')),
        ('delivery_execution.execution_refs', delivery_execution.get('execution_refs')),
        ('delivery_run.execution_refs', delivery_run.get('execution_refs')),
        ('delivery_job.execution_refs', delivery_job.get('execution_refs')),
        ('delivery_artifact', delivery_artifact),
    ]
    for source, payload in candidates:
        artifact = _delivery_blockchain_artifact(payload, source=source)
        if artifact:
            return artifact
    return {
        'artifact_type': '',
        'artifact': '',
        'artifact_source': '',
        'settlement_adapter': '',
        'proof_type': '',
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
            result = {
                'runtime': runtime,
                'demo_state_dir': demo_env['tmpdir'],
                'outbound_dispatch_disabled': demo_env['outbound_dispatch_disabled'],
                'payment_entry': payment_entry,
                'payment_evidence': payment_evidence,
                'preview': preview,
                'capture_result': capture_result,
                'subscriptions_state': state,
            }
            result = _direct_browser_fallback_capture(result, timeout=int(timeout))
            return _attach_offline_x402_signature(result, timeout=int(timeout))



def _direct_browser_fallback_capture(result, *, timeout):
    runtime = dict(result.get('runtime') or {})
    capture_result = dict(result.get('capture_result') or {})
    delivery_artifact = dict(capture_result.get('delivery_artifact') or {})
    if (delivery_artifact.get('brief_text') or '').strip():
        return result
    if not runtime.get('loom_bin_exists'):
        return result
    if (runtime.get('capability_name') or '').strip() != DIRECT_LOOM_CAPABILITY:
        return result

    execute_result = _run_loom_json(
        [
            'action',
            'execute',
            '--root',
            DIRECT_LOOM_ROOT,
            '--agent-id',
            DIRECT_LOOM_AGENT_ID,
            '--capability',
            DIRECT_LOOM_CAPABILITY,
            '--payload-json',
            json.dumps({'url': DIRECT_LOOM_URL}),
            '--format',
            'json',
        ],
        loom_bin=runtime['loom_bin'],
        timeout=timeout,
    )
    payload = dict(execute_result.get('payload') or {})
    result_path = (payload.get('worker_result_path') or payload.get('result_path') or '').strip()
    worker_result = _load_worker_result(result_path)
    brief_text = _host_response_brief_text(worker_result)

    fallback_execution = {
        'ok': (
            execute_result.get('ok', False)
            and (payload.get('runtime_outcome') == 'worker_executed' or payload.get('worker_status') == 'completed')
        ),
        'runtime': 'loom_direct_action_execute',
        'capability_name': DIRECT_LOOM_CAPABILITY,
        'org_id': DIRECT_LOOM_ORG_ID,
        'agent_id': DIRECT_LOOM_AGENT_ID,
        'command': execute_result.get('command', []),
        'submit': payload,
        'result_path': result_path,
        'worker_result': worker_result,
        'error': '',
    }
    if not fallback_execution['ok']:
        fallback_execution['error'] = (
            execute_result.get('error')
            or payload.get('worker_note')
            or payload.get('runtime_outcome')
            or 'direct loom action execute failed'
        )

    capture_result['delivery_execution'] = fallback_execution
    if brief_text:
        capture_result['delivery_run'] = {
            'state': 'executed',
            'delivery_status': 'artifact_ready',
            'delivered': False,
            'delivery_ref': '',
            'execution_refs': payload,
        }
        capture_result['delivery_artifact'] = {
            'artifact_type': 'subscription_brief_v1',
            'content_type': 'text/plain',
            'subscription_id': capture_result.get('subscription', {}).get('id', ''),
            'preview_id': result.get('preview', {}).get('preview_id', ''),
            'job_id': payload.get('job_id', ''),
            'delivery_title': 'Phase 6 direct Loom browser demo brief',
            'brief_date': subscription_service.now_dt().date().isoformat(),
            'topic': DIRECT_LOOM_URL,
            'result_path': result_path,
            'source_key': 'host_response_json.title+body_excerpt_utf8',
            'brief_text': brief_text,
            'brief_preview': brief_text[: subscription_service.BRIEF_PREVIEW_LIMIT].strip(),
            'raw_worker_result': worker_result,
        }
    elif not fallback_execution['error']:
        fallback_execution['error'] = 'direct loom browser execution returned no deliverable brief text'
    result['capture_result'] = capture_result
    return result


def _print_result(result):
    runtime = result['runtime']
    capture_result = result['capture_result']
    subscription = capture_result['subscription']
    run = capture_result.get('delivery_run', {})
    execution = capture_result.get('delivery_execution', {})
    artifact = capture_result.get('delivery_artifact', {})
    blockchain = delivery_blockchain_artifact(result)

    print('Phase 6 Live Demo')
    print(f"workspace: {WORKSPACE}")
    print(f"demo_state_dir: {result['demo_state_dir']}")
    print(f"outbound_dispatch_disabled: {str(result['outbound_dispatch_disabled']).lower()}")
    print(f"blockchain_transfer_claimed: {str((blockchain.get('artifact_type') or '') == 'tx_hash').lower()}")
    print(f"blockchain_artifact_type: {blockchain.get('artifact_type') or '<none>'}")
    print(f"blockchain_artifact_source: {blockchain.get('artifact_source') or '<none>'}")
    if blockchain.get('settlement_adapter'):
        print(f"blockchain_settlement_adapter: {blockchain['settlement_adapter']}")
    if blockchain.get('proof_type'):
        print(f"blockchain_proof_type: {blockchain['proof_type']}")
    print(f"blockchain_artifact: {blockchain.get('artifact') or '<none>'}")
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
    print(f"payment_evidence_tx_hash: {result['payment_entry']['tx_hash']}")
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
