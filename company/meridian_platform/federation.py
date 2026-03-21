#!/usr/bin/env python3
"""
Federation gateway primitives for Meridian live runtime.

The live deployment still defaults to a founding-only institution host.
This module keeps the federation contract honest so workspace/status can
surface whether host-service federation is enabled without pretending the
deployment already routes multiple institutions.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import uuid


TRUST_STATES = (
    'trusted',
    'suspended',
    'revoked',
)

DEFAULT_TTL_SECONDS = 300
MAX_TTL_SECONDS = 3600


def _now():
    return datetime.datetime.utcnow()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


def _b64url_decode(s: str) -> bytes:
    padding = 4 - (len(s) % 4)
    if padding != 4:
        s += '=' * padding
    return base64.urlsafe_b64decode(s)


def _canonical_payload_bytes(payload) -> bytes:
    if payload is None:
        return b''
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode('utf-8')
    return json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _payload_hash(payload) -> str:
    return hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()


class FederationError(RuntimeError):
    """Base class for federation gateway failures."""


class FederationUnavailable(FederationError):
    """The host has not enabled federation for this boundary."""


class FederationValidationError(FederationError):
    """Envelope or peer identity failed validation."""


class FederationReplayError(FederationValidationError):
    """Envelope nonce was already consumed."""


class FederationPeer:
    __slots__ = (
        'host_id',
        'label',
        'transport',
        'trust_state',
        'shared_secret',
        'admitted_org_ids',
    )

    def __init__(self, host_id, label='', transport='https', trust_state='trusted',
                 shared_secret='', admitted_org_ids=None):
        if trust_state not in TRUST_STATES:
            raise ValueError(
                f'Unknown trust_state {trust_state!r}. Must be one of {TRUST_STATES}'
            )
        self.host_id = host_id
        self.label = label or host_id
        self.transport = transport or 'https'
        self.trust_state = trust_state
        self.shared_secret = shared_secret or ''
        self.admitted_org_ids = list(admitted_org_ids or [])

    def to_dict(self, redact_secret=True):
        data = {
            'host_id': self.host_id,
            'label': self.label,
            'transport': self.transport,
            'trust_state': self.trust_state,
            'admitted_org_ids': list(self.admitted_org_ids),
        }
        if not redact_secret:
            data['shared_secret'] = self.shared_secret
        return data


class ReplayStore:
    """Replay protection store for federation envelope nonces."""

    def __init__(self, file_path=None):
        self.file_path = file_path
        self._seen = set()
        if self.file_path and os.path.exists(self.file_path):
            with open(self.file_path) as f:
                for line in f:
                    key = line.strip()
                    if key:
                        self._seen.add(key)

    @property
    def mode(self):
        return 'file_backed' if self.file_path else 'memory_only'

    def has(self, key):
        return key in self._seen

    def record(self, key):
        if key in self._seen:
            return False
        self._seen.add(key)
        if self.file_path:
            with open(self.file_path, 'a') as f:
                f.write(key + '\n')
        return True

    def snapshot(self):
        return {
            'mode': self.mode,
            'entries': len(self._seen),
        }


class FederationEnvelopeClaims:
    __slots__ = (
        'envelope_id',
        'source_host_id',
        'source_institution_id',
        'target_host_id',
        'target_institution_id',
        'actor_type',
        'actor_id',
        'session_id',
        'boundary_name',
        'identity_model',
        'message_type',
        'payload_hash',
        'issued_at',
        'expires_at',
        'nonce',
        'algorithm',
        'warrant_id',
        'commitment_id',
    )

    def __init__(self, **kwargs):
        for name in self.__slots__:
            setattr(self, name, kwargs.get(name, ''))

    def to_dict(self):
        return {name: getattr(self, name) for name in self.__slots__}

    @property
    def replay_key(self):
        return f'{self.source_host_id}:{self.nonce}'

    @property
    def is_expired(self):
        try:
            expires = datetime.datetime.strptime(self.expires_at, '%Y-%m-%dT%H:%M:%SZ')
        except (ValueError, TypeError):
            return True
        return _now() >= expires


def load_peer_registry(file_path, *, host_identity=None):
    registry = {
        'host_id': getattr(host_identity, 'host_id', '') or '',
        'source': 'none',
        'peers': {},
        'trusted_peer_ids': [],
    }
    if not file_path or not os.path.exists(file_path):
        return registry

    with open(file_path) as f:
        raw = json.load(f)

    registry['source'] = 'file'
    registry['host_id'] = (raw.get('host_id') or registry['host_id'] or '').strip()
    if host_identity and registry['host_id'] and registry['host_id'] != host_identity.host_id:
        raise RuntimeError(
            f"Peer registry host_id '{registry['host_id']}' does not match runtime host "
            f"'{host_identity.host_id}'"
        )

    peers = raw.get('peers', {})
    if isinstance(peers, list):
        peers = {entry.get('host_id', ''): entry for entry in peers}
    if not isinstance(peers, dict):
        raise RuntimeError('Federation peer registry must contain a peers dict or list')

    for host_id, data in peers.items():
        host_id = (host_id or data.get('host_id') or '').strip()
        if not host_id:
            continue
        entry = FederationPeer(
            host_id=host_id,
            label=(data.get('label') or data.get('name') or host_id).strip(),
            transport=(data.get('transport') or 'https').strip(),
            trust_state=(data.get('trust_state') or 'trusted').strip(),
            shared_secret=(data.get('shared_secret') or '').strip(),
            admitted_org_ids=data.get('admitted_org_ids', []),
        )
        if entry.trust_state == 'trusted' and not entry.shared_secret:
            raise RuntimeError(
                f"Trusted peer '{host_id}' must declare shared_secret for HMAC verification"
            )
        registry['peers'][host_id] = entry
        if entry.trust_state == 'trusted':
            registry['trusted_peer_ids'].append(host_id)
    return registry


class FederationAuthority:
    """Host-service signer / verifier for federation envelopes."""

    def __init__(self, host_identity, *, signing_secret=None,
                 peer_registry=None, replay_store=None):
        self.host_identity = host_identity
        self._signing_secret = (
            signing_secret.encode('utf-8')
            if isinstance(signing_secret, str) else
            signing_secret
        )
        self.peer_registry = peer_registry or {
            'source': 'none',
            'host_id': getattr(host_identity, 'host_id', '') or '',
            'peers': {},
            'trusted_peer_ids': [],
        }
        self.replay_store = replay_store or ReplayStore()

    def _enabled_state(self):
        if not getattr(self.host_identity, 'federation_enabled', False):
            return False, 'host_federation_disabled'
        if not self._signing_secret:
            return False, 'signing_secret_missing'
        if (getattr(self.host_identity, 'peer_transport', '') or 'none') == 'none':
            return False, 'peer_transport_none'
        return True, ''

    def ensure_enabled(self):
        enabled, reason = self._enabled_state()
        if not enabled:
            raise FederationUnavailable(
                f'Federation gateway is disabled on host {self.host_identity.host_id} '
                f'({reason})'
            )
        return True

    def _sign(self, payload_bytes):
        return hmac.new(self._signing_secret, payload_bytes, hashlib.sha256).digest()

    def _verification_secret_for_source(self, source_host_id):
        if source_host_id == self.host_identity.host_id:
            if not self._signing_secret:
                raise FederationValidationError('Local federation signing secret is not configured')
            return self._signing_secret
        peer = self.peer_registry.get('peers', {}).get(source_host_id)
        if not peer:
            raise FederationValidationError(f"Source host '{source_host_id}' is not in peer registry")
        if peer.trust_state != 'trusted':
            raise FederationValidationError(
                f"Source host '{source_host_id}' is not trusted (state={peer.trust_state})"
            )
        if not peer.shared_secret:
            raise FederationValidationError(
                f"Trusted peer '{source_host_id}' does not declare a verification secret"
            )
        return peer.shared_secret.encode('utf-8')

    def issue(self, source_institution_id, target_host_id, target_institution_id,
              message_type, payload=None, *, actor_type='host_service',
              actor_id='', session_id='', boundary_name='federation_gateway',
              ttl_seconds=None, warrant_id='', commitment_id=''):
        self.ensure_enabled()
        if not source_institution_id:
            raise ValueError('source_institution_id is required')
        if not target_host_id:
            raise ValueError('target_host_id is required')
        if not target_institution_id:
            raise ValueError('target_institution_id is required')
        if not message_type:
            raise ValueError('message_type is required')

        ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_SECONDS
        if ttl > MAX_TTL_SECONDS:
            ttl = MAX_TTL_SECONDS
        if ttl <= 0:
            raise ValueError('ttl_seconds must be positive')

        now = _now()
        payload_obj = {
            'envelope_id': f'fed_{uuid.uuid4().hex[:12]}',
            'source_host_id': self.host_identity.host_id,
            'source_institution_id': source_institution_id,
            'target_host_id': target_host_id,
            'target_institution_id': target_institution_id,
            'actor_type': actor_type or 'host_service',
            'actor_id': actor_id or f'host_service:{self.host_identity.host_id}',
            'session_id': session_id or '',
            'boundary_name': boundary_name or 'federation_gateway',
            'identity_model': 'signed_host_service',
            'message_type': message_type,
            'payload_hash': _payload_hash(payload),
            'issued_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
            'expires_at': (now + datetime.timedelta(seconds=ttl)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'nonce': f'nonce_{uuid.uuid4().hex[:12]}',
            'algorithm': 'hmac_sha256',
            'warrant_id': warrant_id or '',
            'commitment_id': commitment_id or '',
        }
        payload_bytes = json.dumps(payload_obj, separators=(',', ':')).encode('utf-8')
        signature = self._sign(payload_bytes)
        return _b64url_encode(payload_bytes) + '.' + _b64url_encode(signature)

    def validate(self, envelope, *, payload=None, expected_target_host_id=None,
                 expected_target_org_id=None, expected_boundary_name=None,
                 reject_replay=False):
        if not envelope or '.' not in envelope:
            raise FederationValidationError('Invalid federation envelope format')

        body_b64, signature_b64 = envelope.split('.', 1)
        try:
            payload_bytes = _b64url_decode(body_b64)
            signature = _b64url_decode(signature_b64)
            body = json.loads(payload_bytes)
        except Exception as exc:
            raise FederationValidationError(f'Envelope decode failed: {exc}') from exc

        required = (
            'envelope_id',
            'source_host_id',
            'source_institution_id',
            'target_host_id',
            'target_institution_id',
            'actor_type',
            'actor_id',
            'boundary_name',
            'identity_model',
            'message_type',
            'payload_hash',
            'issued_at',
            'expires_at',
            'nonce',
            'algorithm',
        )
        missing = [field for field in required if not body.get(field)]
        if missing:
            raise FederationValidationError(
                f'Envelope payload missing required fields: {", ".join(missing)}'
            )
        if body.get('identity_model') != 'signed_host_service':
            raise FederationValidationError('Envelope identity_model must be signed_host_service')
        if body.get('algorithm') != 'hmac_sha256':
            raise FederationValidationError('Envelope algorithm must be hmac_sha256')

        secret = self._verification_secret_for_source(body['source_host_id'])
        expected_sig = hmac.new(secret, payload_bytes, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_sig):
            raise FederationValidationError('Envelope signature verification failed')

        claims = FederationEnvelopeClaims(**body)
        if claims.is_expired:
            raise FederationValidationError('Envelope is expired')
        if expected_target_host_id and claims.target_host_id != expected_target_host_id:
            raise FederationValidationError(
                f"Envelope targets host '{claims.target_host_id}', not '{expected_target_host_id}'"
            )
        if expected_target_org_id and claims.target_institution_id != expected_target_org_id:
            raise FederationValidationError(
                f"Envelope targets institution '{claims.target_institution_id}', not "
                f"'{expected_target_org_id}'"
            )
        if expected_boundary_name and claims.boundary_name != expected_boundary_name:
            raise FederationValidationError(
                f"Envelope boundary '{claims.boundary_name}' does not match expected "
                f"'{expected_boundary_name}'"
            )
        if payload is not None and claims.payload_hash != _payload_hash(payload):
            raise FederationValidationError('Envelope payload hash does not match supplied payload')

        source_peer = self.peer_registry.get('peers', {}).get(claims.source_host_id)
        if source_peer and source_peer.admitted_org_ids:
            if claims.source_institution_id not in source_peer.admitted_org_ids:
                raise FederationValidationError(
                    f"Envelope source institution '{claims.source_institution_id}' is not admitted "
                    f"for peer '{claims.source_host_id}'"
                )

        if reject_replay and self.replay_store.has(claims.replay_key):
            raise FederationReplayError(
                f"Envelope nonce already consumed for source host '{claims.source_host_id}'"
            )
        return claims

    def accept(self, envelope, *, payload=None, expected_target_host_id=None,
               expected_target_org_id=None, expected_boundary_name='federation_gateway'):
        self.ensure_enabled()
        claims = self.validate(
            envelope,
            payload=payload,
            expected_target_host_id=expected_target_host_id,
            expected_target_org_id=expected_target_org_id,
            expected_boundary_name=expected_boundary_name,
            reject_replay=True,
        )
        self.replay_store.record(claims.replay_key)
        return claims

    def snapshot(self, *, bound_org_id='', admission_registry=None):
        enabled, reason = self._enabled_state()
        peers = self.peer_registry.get('peers', {})
        trusted_peers = [
            peer.to_dict()
            for peer in peers.values()
            if peer.trust_state == 'trusted'
        ]
        return {
            'enabled': enabled,
            'disabled_reason': '' if enabled else reason,
            'host_id': self.host_identity.host_id,
            'bound_org_id': bound_org_id,
            'identity_model': 'signed_host_service',
            'boundary_name': 'federation_gateway',
            'peer_transport': self.host_identity.peer_transport,
            'registry_source': self.peer_registry.get('source', 'none'),
            'peer_count': len(trusted_peers),
            'trusted_peer_ids': list(self.peer_registry.get('trusted_peer_ids', [])),
            'trusted_peers': trusted_peers,
            'admitted_org_ids': list((admission_registry or {}).get('admitted_org_ids', [])),
            'replay_protection': self.replay_store.snapshot(),
            'signing': {
                'algorithm': 'hmac_sha256',
                'configured': bool(self._signing_secret),
            },
        }
