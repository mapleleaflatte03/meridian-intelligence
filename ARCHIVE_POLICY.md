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

## Archive Lock Checklist (Final Pass)

- [ ] GitHub repo description starts with: `[MIRROR - READ ONLY]`.
- [ ] GitHub repo homepage points to: `https://github.com/mapleleaflatte03/meridian`.
- [ ] `README.md` top section states this repo is a mirror for `meridian/intelligence`.
- [ ] `.github/ISSUE_TEMPLATE/config.yml` has `blank_issues_enabled: false`.
- [ ] All issue templates redirect users to monorepo issues/discussions/security links.
- [ ] `.github/pull_request_template.md` redirects PR authors to monorepo path `intelligence/`.
- [ ] Branch protection on `main` blocks direct pushes for non-maintainers.
- [ ] Mirror updates are sync-only from monorepo commits (no feature work here).
- [ ] Optional hard lock: archive this repo in GitHub UI after redirects are confirmed.

### Completion Gate

Mirror is considered closed when every checkbox above is done and any new issue/PR created in this repo is immediately redirected to the monorepo.
