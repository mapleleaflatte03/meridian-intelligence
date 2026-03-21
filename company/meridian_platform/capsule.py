#!/usr/bin/env python3
"""
Live institution state capsule helpers.

The live system is still operationally single-org, but runtime governance state
should already live behind an institution-owned boundary instead of ad hoc files
in meridian_platform/.
"""
import json
import os


PLATFORM_DIR = os.path.dirname(os.path.abspath(__file__))
WORKSPACE = os.path.dirname(os.path.dirname(PLATFORM_DIR))
CAPSULES_DIR = os.path.join(WORKSPACE, 'economy', 'capsules')
ORGS_FILE = os.path.join(PLATFORM_DIR, 'organizations.json')


def _load_orgs():
    if not os.path.exists(ORGS_FILE):
        return {}
    with open(ORGS_FILE) as f:
        return json.load(f).get('organizations', {})


def default_org_id():
    for oid, org in _load_orgs().items():
        if org.get('slug') == 'meridian':
            return oid
    return None


def resolve_org_id(org_id=None):
    founding_org_id = default_org_id()
    if org_id and founding_org_id and org_id != founding_org_id:
        raise ValueError(
            f'Live capsule only supports founding org {founding_org_id}, got {org_id}'
        )
    return org_id or founding_org_id


def capsule_dir(org_id=None):
    resolved_org_id = resolve_org_id(org_id)
    if not resolved_org_id:
        raise ValueError('Founding org is not initialized')
    return os.path.join(CAPSULES_DIR, resolved_org_id)


def ensure_capsule(org_id=None):
    target = capsule_dir(org_id)
    os.makedirs(target, exist_ok=True)
    return target


def capsule_path(org_id, filename):
    return os.path.join(capsule_dir(org_id), filename)
