<!-- playbook:begin -->
## How work lands here

> Managed by [leduftw/playbook](https://github.com/leduftw/playbook) @@major@@. Change it there, not here; `playbook sync` brings this section back in line.

The facts about this repo live in `.github/playbook.toml`. Everything in this section is the same in every repo that follows the playbook.

### Every change

1. **Start from an issue.** Reuse the issue that describes the change, or create one assigned to `leduftw` with at least one label.
2. **Work in your own worktree.** Run `playbook start <issue>`: it creates `~/Developer/.worktrees/@@name@@/<issue>-<slug>` on branch `dev/leduftw/<issue>-<slug>`, based on the latest `main`. Work only there. Another session may be working at the same time, so never edit the primary checkout; it stays on `main` and only moves with `git pull`.
3. **Commit and push** each finished slice to that branch, without pausing for confirmation.
4. **Open the PR** with `gh pr create --base main`. Its title becomes the commit on `main`, so it follows the commit style below. Put `Closes #<issue>` in the body when the PR fully resolves the issue.
5. **Land it with `playbook finish`.** It waits for the required checks, merges `main` into the branch if `main` has moved on, squash-merges, removes the worktree and the branch, pulls `main`, and confirms the issue is closed. If a check fails, fix it on the branch and run `playbook finish` again.

Sessions that only read, plan or answer questions need no issue and no worktree.

**Never** commit or push to `main` (hooks and a GitHub ruleset refuse it), rebase a branch that's already pushed, or force-push. To catch up with `main`, merge it into your branch.

**Commit style:** start with a lowercase verb that says what the change does, then plain words, with no `feat:`-style prefix and no full stop. Names keep their capitals: `fix Windows installer replacement`, `add opt-in global leaderboard`. The `playbook / title` check rejects PR titles that break this.

**Without the `playbook` command** (a cloud session, a fresh machine), do the same by hand:

- start: `git fetch origin`, then `git worktree add -b dev/leduftw/<issue>-<slug> ~/Developer/.worktrees/@@name@@/<issue>-<slug> origin/main` (a cloud session is already isolated, so a branch from `origin/main` is enough)
- finish: `gh pr checks --watch --required`, then `gh pr merge --squash`; once the PR shows `MERGED`, `git worktree remove <path>`, `git branch -D <branch>` (`-d` refuses, because squash commits aren't ancestors of `main`) and `git pull --ff-only` in the primary checkout

@@#published@@
### Releasing

This repo is published: other people install it.

- `playbook release patch|minor|major` opens a PR that only bumps the version; nobody types a version number. The first release is always 1.0.0, and every later one is exactly the next patch, minor or major.
- That PR builds and smoke-tests every release file on all six platforms. Landing it with `playbook finish` tags the release, publishes it on GitHub Releases and updates @@channels@@.
- A published release is locked. A broken one is never fixed in place; ship the next patch.
@@/published@@
@@#local@@
### Publishing

This repo is local: nothing is released. If that changes, the playbook adds the release setup (`published = true` and a `[release]` table in `.github/playbook.toml`).
@@/local@@
@@#self@@
### Releasing the playbook

Repos pin the playbook as `v1`, so a release is what carries a change to them.

- `playbook release patch|minor|major` opens a PR that only bumps `VERSION`. Use patch for fixes, minor for anything repos can take without changing, and major when repos must change too; a new major reaches each repo through its own PR.
- Landing that PR publishes `vX.Y.Z` and moves `vX`, so every repo on that major picks the change up on its next run.
@@/self@@
<!-- playbook:end -->
