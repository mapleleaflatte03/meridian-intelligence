# Meridian Intelligence Mirror Archive Policy

This repository is a **mirror** of the canonical module at:

- https://github.com/mapleleaflatte03/meridian/tree/main/intelligence

## Policy

- Treat this repo as **read-only for product evolution**.
- Open all new issues in monorepo:
  - https://github.com/mapleleaflatte03/meridian/issues
- Open all new PRs against monorepo path `intelligence/`.
- Follow monorepo security policy for vulnerabilities:
  - https://github.com/mapleleaflatte03/meridian/security/policy

## Allowed Mirror Updates

- Monorepo -> mirror sync commits
- Emergency metadata redirect fixes

## Not Allowed Here

- New feature development
- Divergent fixes that skip monorepo
- New roadmap planning

## Maintainer Checklist

1. Land change in monorepo first.
2. Sync mirror with canonical commit hash reference.
3. Keep issue/PR templates redirecting to monorepo.
4. Keep README canonical banner + policy link visible.
