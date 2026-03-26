#!/usr/bin/env python3
"""Tracked subscription service helpers for the founding live workspace."""
import contextlib
import datetime
import fcntl
import importlib.util
import json
import os
import tempfile
import uuid

from capsule import (
    ensure_subscription_aliases,
    subscriptions_path,
    subscriptions_backup_path,
    subscriptions_lock_path,
)


TRIAL_DAYS = 7
PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
ECONOMY_DIR = os.path.join(WORKSPACE, 'economy')
REVENUE_PY = os.path.join(ECONOMY_DIR, 'revenue.py')

_revenue_spec = importlib.util.spec_from_file_location('subscription_service_revenue', REVENUE_PY)
_revenue_mod = importlib.util.module_from_spec(_revenue_spec)
_revenue_spec.loader.exec_module(_revenue_mod)

PLANS = {
    'premium-brief-monthly': {'price_usd': 9.99, 'duration_days': 30, 'type': 'recurring'},
    'premium-brief-weekly':  {'price_usd': 2.99, 'duration_days': 7,  'type': 'recurring'},
    'deep-dive-single':      {'price_usd': 9.99, 'duration_days': 0,  'type': 'one-time'},
    'trial':                 {'price_usd': 0.00, 'duration_days': TRIAL_DAYS,  'type': 'trial'},
}


def now_ts():
    return datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def now_dt():
    return datetime.datetime.utcnow()


def _default_subscriptions(org_id=None):
    return {
        'subscribers': {},
        'draft_subscriptions': {},
        'loom_delivery_jobs': {},
        'delivery_log': [],
        'updatedAt': now_ts(),
        '_meta': {
            'service_scope': 'institution_owned_subscription_service',
            'boundary_name': 'subscriptions',
            'identity_model': 'session',
            'storage_model': 'capsule_canonical_with_legacy_symlink',
            'bound_org_id': org_id or '',
            'internal_test_ids': [],
        },
    }


def _normalize_draft_subscription(draft, org_id=None, existing=None):
    draft = dict(draft or {})
    existing = dict(existing or {})
    draft_id = (draft.get('draft_id') or existing.get('draft_id') or '').strip()
    if not draft_id:
        draft_id = f"draft_{uuid.uuid4().hex[:12]}"

    record = dict(existing)
    record['draft_id'] = draft_id
    record['preview_id'] = (draft.get('preview_id') or existing.get('preview_id') or '').strip()
    record['pilot_request_id'] = (draft.get('pilot_request_id') or existing.get('pilot_request_id') or '').strip()
    record['source_preview_state'] = (
        draft.get('source_preview_state')
        or existing.get('source_preview_state')
        or ''
    ).strip()
    record['source_preview_truth_source'] = (
        draft.get('source_preview_truth_source')
        or existing.get('source_preview_truth_source')
        or ''
    ).strip()
    record['requested_offer'] = (draft.get('requested_offer') or existing.get('requested_offer') or '').strip()
    record['requested_cadence'] = (draft.get('requested_cadence') or existing.get('requested_cadence') or '').strip()
    record['name'] = (draft.get('name') or existing.get('name') or '').strip()
    record['company'] = (draft.get('company') or existing.get('company') or '').strip()
    record['email'] = (draft.get('email') or existing.get('email') or '').strip()
    record['telegram_handle'] = (draft.get('telegram_handle') or existing.get('telegram_handle') or '').strip()
    record['plan_options'] = list(draft.get('plan_options') or existing.get('plan_options') or [])
    record['plan'] = (draft.get('plan') or existing.get('plan') or '').strip()
    record['price_usd'] = float(draft.get('price_usd', existing.get('price_usd', 0.0)) or 0.0)
    record['status'] = (draft.get('status') or existing.get('status') or 'draft').strip() or 'draft'
    record['drafted_at'] = (draft.get('drafted_at') or existing.get('drafted_at') or '').strip()
    record['drafted_by'] = (draft.get('drafted_by') or existing.get('drafted_by') or '').strip()
    record['draft_note'] = (draft.get('draft_note') or existing.get('draft_note') or '').strip()
    record['subscription_id'] = (draft.get('subscription_id') or existing.get('subscription_id') or '').strip()
    record['subscription_source'] = (
        draft.get('subscription_source')
        or existing.get('subscription_source')
        or 'subscription_preview_queue'
    ).strip()
    record['subscription_status'] = (draft.get('subscription_status') or existing.get('subscription_status') or 'draft').strip() or 'draft'
    record['payment_method'] = (draft.get('payment_method') or existing.get('payment_method') or 'draft').strip()
    record['payment_ref'] = (draft.get('payment_ref') or existing.get('payment_ref') or '').strip()
    record['payment_verified'] = bool(draft.get('payment_verified', existing.get('payment_verified', False)))
    record['payment_verified_at'] = (draft.get('payment_verified_at') or existing.get('payment_verified_at') or '').strip()
    record['payment_evidence'] = dict(draft.get('payment_evidence') or existing.get('payment_evidence') or {})
    record['created_at'] = (draft.get('created_at') or existing.get('created_at') or now_ts()).strip()
    record['updated_at'] = now_ts()
    return record


def _normalize_subscriptions(data, org_id=None):
    if not isinstance(data, dict):
        return _default_subscriptions(org_id)
    payload = dict(data)
    payload.setdefault('subscribers', {})
    payload.setdefault('draft_subscriptions', {})
    payload.setdefault('loom_delivery_jobs', {})
    payload.setdefault('delivery_log', [])
    payload.setdefault('updatedAt', now_ts())
    payload.setdefault('_meta', {})
    payload['_meta']['service_scope'] = 'institution_owned_subscription_service'
    payload['_meta']['boundary_name'] = 'subscriptions'
    payload['_meta']['identity_model'] = 'session'
    payload['_meta']['storage_model'] = 'capsule_canonical_with_legacy_symlink'
    payload['_meta']['bound_org_id'] = org_id or payload['_meta'].get('bound_org_id', '')
    payload['_meta'].setdefault('internal_test_ids', [])
    raw_drafts = payload.get('draft_subscriptions', {})
    drafts = {}
    if isinstance(raw_drafts, list):
        for item in raw_drafts:
            if isinstance(item, dict):
                draft_id = (item.get('draft_id') or '').strip()
                if draft_id:
                    drafts[draft_id] = dict(item)
    elif isinstance(raw_drafts, dict):
        for draft_id, item in raw_drafts.items():
            if isinstance(item, dict):
                record = dict(item)
                record['draft_id'] = record.get('draft_id') or draft_id
                drafts[record['draft_id']] = record
    for draft_id, record in list(drafts.items()):
        drafts[draft_id] = _normalize_draft_subscription(record, org_id, existing=record)
    payload['draft_subscriptions'] = drafts

    raw_jobs = payload.get('loom_delivery_jobs', {})
    jobs = {}
    if isinstance(raw_jobs, list):
        for item in raw_jobs:
            if isinstance(item, dict):
                job_id = (item.get('job_id') or '').strip()
                if job_id:
                    jobs[job_id] = dict(item)
    elif isinstance(raw_jobs, dict):
        for job_id, item in raw_jobs.items():
            if isinstance(item, dict):
                record = dict(item)
                record['job_id'] = record.get('job_id') or job_id
                jobs[record['job_id']] = record
    payload['loom_delivery_jobs'] = {
        job_id: _normalize_loom_delivery_job(record, org_id, existing=record)
        for job_id, record in jobs.items()
    }
    return payload


def _storage_paths(org_id=None):
    ensure_subscription_aliases(org_id)
    return subscriptions_path(org_id), subscriptions_backup_path(org_id)


def _write_json_atomic(path, data):
    directory = os.path.dirname(path) or '.'
    fd, tmp_path = tempfile.mkstemp(
        prefix=os.path.basename(path) + '.',
        suffix='.tmp',
        dir=directory,
    )
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@contextlib.contextmanager
def _subscriptions_lock(org_id=None):
    ensure_subscription_aliases(org_id)
    with open(subscriptions_lock_path(org_id), 'a+') as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def load_subscriptions(org_id=None):
    primary_path, backup_path = _storage_paths(org_id)
    for path in (primary_path, backup_path):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            with open(path) as f:
                return _normalize_subscriptions(json.load(f), org_id)
    return _default_subscriptions(org_id)


def save_subscriptions(data, org_id=None):
    payload = _normalize_subscriptions(data, org_id)
    payload['updatedAt'] = now_ts()
    primary_path, backup_path = _storage_paths(org_id)
    with _subscriptions_lock(org_id):
        _write_json_atomic(primary_path, payload)
        _write_json_atomic(backup_path, payload)


def _subscription_delivery_eligible(sub, *, org_id=None, now=None):
    now = now or now_dt()
    if sub.get('status') != 'active':
        return False
    if sub.get('plan') not in ('premium-brief-monthly', 'premium-brief-weekly', 'trial'):
        return False
    expires_at = (sub.get('expires_at') or '').strip()
    if expires_at:
        expires = datetime.datetime.strptime(expires_at, '%Y-%m-%dT%H:%M:%SZ')
        if expires < now:
            return False
    if sub.get('plan') != 'trial' and (
        not sub.get('payment_verified', False) or not _payment_evidence_ok(sub, org_id=org_id)
    ):
        return False
    return True


def _payment_evidence(sub, *, org_id=None):
    if sub.get('plan') == 'trial':
        return {'type': 'trial', 'payment_ref': ''}
    payment_ref = (sub.get('payment_ref') or '').strip()
    if not payment_ref:
        return None
    return _revenue_mod.find_customer_payment_evidence(
        payment_ref=payment_ref,
        min_amount_usd=float(sub.get('price_usd', 0.0) or 0.0),
    )


def _payment_evidence_ok(sub, *, org_id=None):
    if sub.get('plan') == 'trial':
        return True
    evidence = _payment_evidence(sub, org_id=org_id)
    if not evidence:
        return False
    bound = sub.get('payment_evidence', {})
    if bound:
        if bound.get('order_id') and evidence.get('order_id') != bound.get('order_id'):
            return False
        if bound.get('payment_key') and evidence.get('payment_key') != bound.get('payment_key'):
            return False
        if bound.get('tx_hash') and evidence.get('tx_hash') != bound.get('tx_hash'):
            return False
    return True


def _require_payment_evidence(payment_ref, amount_usd, *, org_id=None):
    payment_ref = (payment_ref or '').strip()
    if not payment_ref:
        raise ValueError('payment_ref is required for paid subscription verification')
    evidence = _revenue_mod.find_customer_payment_evidence(
        payment_ref=payment_ref,
        min_amount_usd=float(amount_usd or 0.0),
    )
    if not evidence:
        raise ValueError(
            f'no customer_payment evidence found for payment_ref={payment_ref} amount>={float(amount_usd or 0.0):.2f}'
        )
    return evidence


def _bind_payment_evidence(subscription, payment_ref=None, *, org_id=None):
    ref = (payment_ref if payment_ref is not None else subscription.get('payment_ref', '')) or ''
    evidence = _require_payment_evidence(ref, subscription.get('price_usd', 0.0), org_id=org_id)
    subscription['payment_ref'] = ref
    subscription['payment_verified'] = True
    subscription['payment_verified_at'] = now_ts()
    subscription['payment_evidence'] = {
        'order_id': evidence.get('order_id', ''),
        'payment_key': evidence.get('payment_key', ''),
        'payment_ref': evidence.get('payment_ref', ref),
        'tx_hash': evidence.get('tx_hash', ''),
        'amount_usd': float(evidence.get('amount', 0.0) or 0.0),
    }
    return evidence


def _normalize_loom_delivery_job(job, org_id=None, existing=None):
    job = dict(job or {})
    existing = dict(existing or {})
    subscription_id = (job.get('subscription_id') or existing.get('subscription_id') or '').strip()
    preview_id = (job.get('preview_id') or existing.get('preview_id') or '').strip()
    telegram_id = (job.get('telegram_id') or existing.get('telegram_id') or '').strip()
    job_id = (job.get('job_id') or existing.get('job_id') or '').strip()
    if not job_id:
        if subscription_id:
            job_id = f'loom_{subscription_id}'
        elif preview_id:
            job_id = f'loom_{preview_id}'
        else:
            job_id = f'loom_{uuid.uuid4().hex[:12]}'

    record = dict(existing)
    record['job_id'] = job_id
    record['subscription_id'] = subscription_id
    record['preview_id'] = preview_id
    record['telegram_id'] = telegram_id
    record['plan'] = (job.get('plan') or existing.get('plan') or '').strip()
    record['delivery_runtime'] = (job.get('delivery_runtime') or existing.get('delivery_runtime') or 'loom').strip() or 'loom'
    record['delivery_channel'] = (job.get('delivery_channel') or existing.get('delivery_channel') or 'loom').strip() or 'loom'
    record['job_type'] = (job.get('job_type') or existing.get('job_type') or 'subscription_loom_delivery').strip() or 'subscription_loom_delivery'
    record['state'] = (job.get('state') or existing.get('state') or 'queued').strip() or 'queued'
    record['queued_at'] = (job.get('queued_at') or existing.get('queued_at') or now_ts()).strip()
    record['queued_by'] = (job.get('queued_by') or existing.get('queued_by') or '').strip()
    record['claimed_at'] = (job.get('claimed_at') or existing.get('claimed_at') or '').strip()
    record['claimed_by'] = (job.get('claimed_by') or existing.get('claimed_by') or '').strip()
    record['completed_at'] = (job.get('completed_at') or existing.get('completed_at') or '').strip()
    record['completed_by'] = (job.get('completed_by') or existing.get('completed_by') or '').strip()
    record['delivery_ref'] = (job.get('delivery_ref') or existing.get('delivery_ref') or '').strip()
    record['notes'] = (job.get('notes') or existing.get('notes') or '').strip()
    record['subscription_status'] = (job.get('subscription_status') or existing.get('subscription_status') or '').strip()
    record['payment_verified'] = bool(job.get('payment_verified', existing.get('payment_verified', False)))
    record['payment_ref'] = (job.get('payment_ref') or existing.get('payment_ref') or '').strip()
    record['created_at'] = (job.get('created_at') or existing.get('created_at') or now_ts()).strip()
    record['updated_at'] = now_ts()
    return record


def queue_loom_delivery(subscription, *, preview=None, org_id=None, actor=''):
    subscription = dict(subscription or {})
    preview = dict(preview or {})
    subscription_id = (subscription.get('id') or '').strip()
    if not subscription_id:
        raise ValueError('subscription_id is required')

    payload = load_subscriptions(org_id)
    jobs = payload.setdefault('loom_delivery_jobs', {})
    job_id = f'loom_{subscription_id}'
    existing = jobs.get(job_id)
    job = _normalize_loom_delivery_job({
        'job_id': job_id,
        'subscription_id': subscription_id,
        'preview_id': (preview.get('preview_id') or subscription.get('activated_from_preview_id') or '').strip(),
        'telegram_id': (subscription.get('telegram_id') or subscription.get('subscriber_id') or subscription.get('created_for') or '').strip(),
        'plan': subscription.get('plan', ''),
        'delivery_runtime': 'loom',
        'delivery_channel': 'loom',
        'job_type': 'subscription_loom_delivery',
        'state': 'queued',
        'queued_at': now_ts(),
        'queued_by': actor or '',
        'payment_verified': bool(subscription.get('payment_verified', False)),
        'payment_ref': subscription.get('payment_ref', ''),
        'subscription_status': subscription.get('status', ''),
        'notes': 'Queued for Loom delivery after subscription activation',
    }, org_id, existing=existing)
    jobs[job_id] = job
    save_subscriptions(payload, org_id)
    return {
        'job_id': job_id,
        'delivery_job': job,
    }


def activate_subscription_from_preview(preview, *, telegram_id=None, plan=None,
                                       payment_method='captured', payment_ref=None,
                                       org_id=None, actor=''):
    preview = dict(preview or {})
    preview_id = (preview.get('preview_id') or '').strip()
    if not preview_id:
        raise ValueError('preview_id is required')
    plan_name = (plan or '').strip()
    if not plan_name:
        raise ValueError('plan is required to activate a subscription from preview')
    if plan_name not in PLANS:
        raise ValueError(f"unknown plan '{plan_name}'. Available: {', '.join(sorted(PLANS.keys()))}")

    subscriber_key = (telegram_id or preview.get('telegram_id') or preview.get('telegram_handle') or '').strip()
    if not subscriber_key:
        raise ValueError('telegram_id is required to activate a subscription from preview')

    payment_ref = (payment_ref or '').strip()
    confirm_payment = plan_name != 'trial'
    if confirm_payment and not payment_ref:
        raise ValueError('payment_ref is required to activate a paid subscription from preview')

    result = create_subscription(
        subscriber_key,
        plan=plan_name,
        payment_method=payment_method or ('trial' if plan_name == 'trial' else 'captured'),
        payment_ref=payment_ref,
        confirm_payment=confirm_payment,
        trial=(plan_name == 'trial'),
        email=preview.get('email') or None,
        org_id=org_id,
        actor=actor,
    )

    payload = load_subscriptions(org_id)
    subscription = result['subscription']
    records = payload.get('subscribers', {}).get(subscriber_key, [])
    for index, record in enumerate(records):
        if record.get('id') == subscription['id']:
            record['activated_from_preview_id'] = preview_id
            record['activation_state'] = 'captured'
            record['activation_source'] = 'subscription_preview_queue'
            record['subscription_source'] = 'subscription_preview_queue'
            record['telegram_id'] = subscriber_key
            record['subscriber_id'] = subscriber_key
            record['created_for'] = subscriber_key
            record['preview_id'] = preview_id
            record['created_from_preview'] = True
            record['activated_at'] = now_ts()
            record['activated_by'] = actor or ''
            records[index] = record
            subscription = record
            result['subscription'] = record
            break
    payload['subscribers'][subscriber_key] = records
    save_subscriptions(payload, org_id)

    delivery_job = queue_loom_delivery(subscription, preview=preview, org_id=org_id, actor=actor)
    return {
        'preview_id': preview_id,
        'telegram_id': subscriber_key,
        'subscription': subscription,
        'delivery_job': delivery_job['delivery_job'],
    }


def create_draft_subscription_from_preview(preview, *, org_id=None, actor=''):
    preview = dict(preview or {})
    preview_id = (preview.get('preview_id') or '').strip()
    if not preview_id:
        raise ValueError('preview_id is required')

    subscription = {
        'draft_id': f"draft_{preview_id}",
        'preview_id': preview_id,
        'pilot_request_id': (preview.get('pilot_request_id') or '').strip(),
        'source_preview_state': (preview.get('state') or preview.get('preview_state') or '').strip(),
        'source_preview_truth_source': (preview.get('preview_truth_source') or '').strip(),
        'requested_offer': (preview.get('requested_offer') or '').strip(),
        'requested_cadence': (preview.get('requested_cadence') or '').strip(),
        'name': (preview.get('name') or '').strip(),
        'company': (preview.get('company') or '').strip(),
        'email': (preview.get('email') or '').strip(),
        'telegram_handle': (preview.get('telegram_handle') or '').strip(),
        'plan_options': list(preview.get('plan_options') or []),
        'plan': '',
        'price_usd': 0.0,
        'status': 'draft',
        'drafted_at': now_ts(),
        'drafted_by': actor or '',
        'draft_note': (preview.get('review_note') or '').strip(),
        'subscription_source': 'subscription_preview_queue',
        'subscription_status': 'draft',
        'payment_method': 'draft',
        'payment_ref': '',
        'payment_verified': False,
        'payment_verified_at': '',
        'payment_evidence': {},
        'created_at': now_ts(),
        'updated_at': now_ts(),
    }

    payload = load_subscriptions(org_id)
    drafts = payload.setdefault('draft_subscriptions', {})
    drafts[subscription['draft_id']] = _normalize_draft_subscription(
        subscription,
        org_id,
        existing=drafts.get(subscription['draft_id'], {}),
    )
    save_subscriptions(payload, org_id)
    return {
        'preview_id': preview_id,
        'draft_subscription': drafts[subscription['draft_id']],
    }


def active_delivery_targets(org_id=None, *, external_only=False):
    payload = load_subscriptions(org_id)
    internal_ids = {
        str(value) for value in payload.get('_meta', {}).get('internal_test_ids', [])
    }
    targets = set()
    for telegram_id, records in payload.get('subscribers', {}).items():
        tid = str(telegram_id)
        if external_only and tid in internal_ids:
            continue
        for record in records:
            if _subscription_delivery_eligible(record, org_id=org_id):
                targets.add(tid)
                break
    return sorted(targets)


def start_trial(telegram_id, *, email='', org_id=None, actor=''):
    return create_subscription(
        telegram_id,
        plan='trial',
        trial=True,
        email=email,
        org_id=org_id,
        actor=actor,
    )['subscription']


def add_subscription(telegram_id, plan='trial', *, duration_days=None,
                     payment_method=None, payment_ref=None,
                     confirm_payment=False, trial=False, email=None,
                     org_id=None, actor=''):
    payload = load_subscriptions(org_id)
    tid = str(telegram_id or '').strip()
    if not tid:
        raise ValueError('telegram_id is required')
    plan_name = 'trial' if trial else (plan or 'trial')
    if plan_name not in PLANS:
        raise ValueError(
            f"unknown plan '{plan_name}'. Available: {', '.join(sorted(PLANS.keys()))}"
        )

    if plan_name == 'trial':
        existing = payload.get('subscribers', {}).get(tid, [])
        for record in existing:
            if record.get('plan') == 'trial':
                raise ValueError(f'telegram:{tid} already used a trial subscription')

    plan_info = PLANS[plan_name]
    duration = duration_days if duration_days is not None else plan_info['duration_days']
    expires = None
    if duration > 0:
        expires = (now_dt() + datetime.timedelta(days=duration)).strftime('%Y-%m-%dT%H:%M:%SZ')

    payment_ref = (payment_ref or '').strip()
    payment_verified = plan_name == 'trial'
    payment_verified_at = now_ts() if plan_name == 'trial' else ''
    payment_evidence = {'type': 'trial'} if plan_name == 'trial' else {}
    if plan_name != 'trial' and bool(confirm_payment):
        evidence = _require_payment_evidence(payment_ref, plan_info['price_usd'], org_id=org_id)
        payment_verified = True
        payment_verified_at = now_ts()
        payment_evidence = {
            'order_id': evidence.get('order_id', ''),
            'payment_key': evidence.get('payment_key', ''),
            'payment_ref': evidence.get('payment_ref', payment_ref),
            'tx_hash': evidence.get('tx_hash', ''),
            'amount_usd': float(evidence.get('amount', 0.0) or 0.0),
        }

    subscription = {
        'id': str(uuid.uuid4())[:8],
        'plan': plan_name,
        'price_usd': plan_info['price_usd'],
        'started_at': now_ts(),
        'expires_at': expires,
        'status': 'active',
        'payment_method': payment_method or ('trial' if plan_name == 'trial' else 'manual'),
        'payment_ref': payment_ref,
        'payment_verified': payment_verified,
        'payment_verified_at': payment_verified_at,
        'payment_evidence': payment_evidence,
        'email': email or '',
        'created_by': actor or '',
    }
    payload.setdefault('subscribers', {}).setdefault(tid, []).append(subscription)
    save_subscriptions(payload, org_id)
    return {
        'telegram_id': tid,
        'subscription': subscription,
    }


def create_subscription(telegram_id, plan='trial', *, duration_days=None,
                        payment_method=None, payment_ref=None,
                        confirm_payment=False, trial=False, email=None,
                        org_id=None, actor=''):
    return add_subscription(
        telegram_id,
        plan=plan,
        duration_days=duration_days,
        payment_method=payment_method,
        payment_ref=payment_ref,
        confirm_payment=confirm_payment,
        trial=trial,
        email=email,
        org_id=org_id,
        actor=actor,
    )


def convert_trial_subscription(telegram_id, plan, *, payment_method=None,
                               payment_ref=None, confirm_payment=False,
                               email=None, org_id=None, actor=''):
    tid = str(telegram_id or '').strip()
    if not tid:
        raise ValueError('telegram_id is required')
    if plan not in PLANS or plan == 'trial':
        raise ValueError(f"invalid conversion plan '{plan}'. Use a paid plan.")

    payload = load_subscriptions(org_id)
    if tid not in payload.get('subscribers', {}):
        raise LookupError(f'No subscriptions for telegram:{tid}')

    had_trial = False
    for sub in payload['subscribers'][tid]:
        if sub.get('plan') == 'trial' and sub.get('status') == 'active':
            had_trial = True
            sub['status'] = 'converted'
            sub['converted_at'] = now_ts()
            sub['converted_by'] = actor or ''
            break
    if not had_trial:
        raise LookupError(f'No active trial found for telegram:{tid}')

    save_subscriptions(payload, org_id)
    result = add_subscription(
        tid,
        plan=plan,
        payment_method=payment_method,
        payment_ref=payment_ref,
        confirm_payment=confirm_payment,
        email=email,
        org_id=org_id,
        actor=actor,
    )
    payload = load_subscriptions(org_id)
    for idx, record in enumerate(payload.get('subscribers', {}).get(tid, [])):
        if record.get('id') == result['subscription']['id']:
            record['converted_from_trial'] = True
            result['subscription'] = record
            payload['subscribers'][tid][idx] = record
            break
    save_subscriptions(payload, org_id)
    return {
        'telegram_id': tid,
        'subscription': result['subscription'],
    }


def check_subscription(telegram_id, *, org_id=None):
    tid = str(telegram_id or '').strip()
    if not tid:
        raise ValueError('telegram_id is required')
    payload = load_subscriptions(org_id)
    records = list(payload.get('subscribers', {}).get(tid, []))
    active = [record for record in records if record.get('status') == 'active']
    latest = records[-1] if records else None
    return {
        'telegram_id': tid,
        'found': bool(records),
        'active': bool(active),
        'eligible_for_delivery': any(
            _subscription_delivery_eligible(record, org_id=org_id)
            for record in active
        ),
        'subscription_count': len(records),
        'active_count': len(active),
        'latest_subscription': latest,
    }


def verify_subscription_payment(telegram_id, *, subscription_id=None,
                                payment_ref=None, org_id=None, actor=''):
    payload = load_subscriptions(org_id)
    tid = str(telegram_id or '').strip()
    if not tid:
        raise ValueError('telegram_id is required')
    if tid not in payload.get('subscribers', {}) or not payload['subscribers'][tid]:
        raise LookupError(f'No subscriptions for telegram:{tid}')

    candidates = [
        sub for sub in payload['subscribers'][tid]
        if sub.get('status') == 'active' and sub.get('plan') != 'trial'
    ]
    if subscription_id:
        candidates = [sub for sub in candidates if sub.get('id') == subscription_id]
    if not candidates:
        raise LookupError(f'No active paid subscription found for telegram:{tid}')

    target = candidates[-1]
    if payment_ref:
        target['payment_ref'] = payment_ref
    _bind_payment_evidence(target, payment_ref=target.get('payment_ref'))
    target['payment_verified_by'] = actor or ''
    save_subscriptions(payload, org_id)
    return {
        'telegram_id': tid,
        'subscription': target,
    }


def set_email(telegram_id, email, *, org_id=None, actor=''):
    payload = load_subscriptions(org_id)
    tid = str(telegram_id).strip()
    if tid not in payload.get('subscribers', {}) or not payload['subscribers'][tid]:
        raise LookupError(f'No subscriptions for telegram:{tid}')
    candidates = [sub for sub in payload['subscribers'][tid] if sub.get('status') == 'active']
    target = candidates[-1] if candidates else payload['subscribers'][tid][-1]
    target['email'] = email
    target['email_updated_at'] = now_ts()
    target['email_updated_by'] = actor or ''
    save_subscriptions(payload, org_id)
    return target


def cancel_active(telegram_id, *, org_id=None, actor=''):
    payload = load_subscriptions(org_id)
    tid = str(telegram_id).strip()
    if tid not in payload.get('subscribers', {}):
        raise LookupError(f'No subscriptions for telegram:{tid}')
    cancelled = 0
    for sub in payload['subscribers'][tid]:
        if sub.get('status') == 'active':
            sub['status'] = 'cancelled'
            sub['cancelled_at'] = now_ts()
            sub['cancelled_by'] = actor or ''
            cancelled += 1
    if cancelled == 0:
        raise ValueError(f'No active subscriptions for telegram:{tid}')
    save_subscriptions(payload, org_id)
    return {'telegram_id': tid, 'cancelled_count': cancelled}


def record_delivery(telegram_id, product, *, brief_date='', org_id=None, actor=''):
    payload = load_subscriptions(org_id)
    entry = {
        'telegram_id': str(telegram_id).strip(),
        'product': (product or '').strip(),
        'delivered_at': now_ts(),
        'recorded_by': actor or '',
    }
    if not entry['telegram_id']:
        raise ValueError('telegram_id is required')
    if not entry['product']:
        raise ValueError('product is required')
    if brief_date:
        entry['brief_date'] = brief_date
    payload.setdefault('delivery_log', []).append(entry)
    if len(payload['delivery_log']) > 500:
        payload['delivery_log'] = payload['delivery_log'][-500:]
    save_subscriptions(payload, org_id)
    return entry


def loom_delivery_queue_snapshot(org_id=None, *, limit=50):
    payload = load_subscriptions(org_id)
    jobs = list(payload.get('loom_delivery_jobs', {}).values())
    jobs = sorted(
        [
            _normalize_loom_delivery_job(job, org_id, existing=job)
            for job in jobs
        ],
        key=lambda job: (
            job.get('queued_at', ''),
            job.get('updated_at', ''),
            job.get('job_id', ''),
        ),
        reverse=True,
    )
    try:
        limit = max(int(limit), 0)
    except (TypeError, ValueError):
        limit = 50
    limited_jobs = jobs[:limit] if limit else []
    state_counts = {
        'queued': 0,
        'claimed': 0,
        'completed': 0,
        'blocked': 0,
    }
    for job in jobs:
        state = job.get('state', 'queued')
        state_counts[state] = state_counts.get(state, 0) + 1
    return {
        'bound_org_id': (org_id or '').strip(),
        'management_mode': 'institution_owned_service',
        'service_scope': 'institution_owned_subscription_service',
        'boundary_name': 'subscriptions',
        'queue_paths': {
            'inspect': '/api/subscriptions/loom-delivery-jobs',
            'activation': '/api/subscriptions/activate-from-preview',
        },
        'summary': {
            'total_jobs': len(jobs),
            'queued_count': state_counts.get('queued', 0),
            'claimed_count': state_counts.get('claimed', 0),
            'completed_count': state_counts.get('completed', 0),
            'blocked_count': state_counts.get('blocked', 0),
        },
        'delivery_jobs': limited_jobs,
        'meta': payload.get('_meta', {}),
    }


def subscription_summary(org_id=None):
    payload = load_subscriptions(org_id)
    rows = list(payload.get('subscribers', {}).values())
    all_subs = [sub for records in rows for sub in records]
    active = [sub for sub in all_subs if sub.get('status') == 'active']
    draft_subscriptions = list(payload.get('draft_subscriptions', {}).values())
    loom_delivery_jobs = list(payload.get('loom_delivery_jobs', {}).values())
    verified = [
        sub for sub in active
        if sub.get('plan') != 'trial' and sub.get('payment_verified')
    ]
    return {
        'subscriber_count': len(payload.get('subscribers', {})),
        'subscription_count': len(all_subs),
        'active_subscription_count': len(active),
        'draft_subscription_count': len(draft_subscriptions),
        'verified_paid_subscription_count': len(verified),
        'delivery_log_count': len(payload.get('delivery_log', [])),
        'loom_delivery_job_count': len(loom_delivery_jobs),
        'queued_loom_delivery_job_count': sum(
            1 for job in loom_delivery_jobs if (job.get('state') or 'queued') == 'queued'
        ),
        'internal_test_id_count': len(payload.get('_meta', {}).get('internal_test_ids', [])),
        'external_target_count': len(active_delivery_targets(org_id, external_only=True)),
    }
