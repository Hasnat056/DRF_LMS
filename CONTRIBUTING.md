# Branching workflow

This repo follows a Git Flow-style branching model, mapped to three
environments:

| Environment   | Branch                     | How it's reached |
|---------------|-----------------------------|-------------------|
| Development   | any `feature/*` branch      | Run locally via `docker compose up --build` — no cloud deploy |
| Testing / QA  | (gate on the PR itself)     | Automated: the `Run Tests` GitHub Actions workflow runs the full pytest suite on every PR into `main` or `develop`. A PR can't merge until it's green |
| Production    | `main`                      | Auto-deployed by `Deploy NexusAPI` on every push to `main` (which only happens via a merged PR) |

## Branches

- **`main`** — production. Protected: no direct pushes, PRs only, must pass
  the test workflow to merge. Every push here deploys to the live Azure VM.
- **`develop`** — integration branch. Protected the same way as `main`.
  Feature branches merge here first; this is where work accumulates between
  releases.
- **`feature/<short-name>`** — one branch per piece of work, branched from
  `develop`. PR back into `develop` when done. Delete after merge.
- **`hotfix/<short-name>`** — urgent production fixes, branched from `main`
  (not `develop`, since production may be ahead of what's currently in
  `develop`). PR into `main`. After merging, also PR (or merge) the same fix
  into `develop` so the next regular release doesn't reintroduce the bug.
- **`release/<version>`** (optional) — cut from `develop` when preparing a
  release, for last-mile stabilization (no new features, just fixes) before
  PRing into `main`. Skip this for small changes; use it when a release
  needs its own soak time separate from `develop`'s ongoing churn.

## Typical flow

```
feature/x  --PR-->  develop  --PR-->  release/x (optional)  --PR-->  main
                                                                        |
                                                                   auto-deploy
main  --PR (hotfix branch)-->  main  --merge back-->  develop
```

## Rules enforced on GitHub

Both `main` and `develop` have branch protection:
- Pull request required — no direct pushes, including from admins.
- The `Run Tests` check must pass before a PR can merge.

## Local testing before opening a PR

```bash
docker compose up --build
docker compose exec backend pytest -v --tb=short
```

The `Run Tests` workflow mirrors this (MySQL + Redis service containers,
same `pytest` invocation) so a PR that passes locally should pass in CI too.
