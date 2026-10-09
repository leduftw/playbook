# playbook

The standard every one of my repos follows, and the tooling that applies it. Read
`README.md` first: it is the standard, written for people.

## Layout

- `bin/playbook`: the command. Plain Python 3.11+, standard library only; it runs on
  this Mac and on every GitHub runner, so it must stay dependency-free.
- `playbook/`: the code. `config.py` reads a repo's `.github/playbook.toml`,
  `stacks.py` holds what each language stack means, `render.py` produces the files
  the playbook owns in every repo, and `ci/` holds the steps the shared pipeline runs.
- `templates/`: the source of every file synced into repos (hooks, the two small
  workflow files, release-note settings, the shared `AGENTS.md` section).
- `installers/`: the one-line installer templates attached to every release.
- `.github/workflows/pipeline.yml`: the shared pipeline repos call as `@v1`.
  `title.yml` is the PR-title check; `self-release.yml` releases the playbook.
- `.github/actions/registry/`: the registry upload, which runs inside each repo's
  own workflow because registries match trusted publishing against that file.

## Checks

```sh
python3 -m unittest discover -s tests -t .
uvx ruff check . && uvx ruff format --check .
actionlint
```

## Things that are the way they are for a reason

- **Repos pin `@v1`, and the pipeline is told its own version.** A reusable
  workflow can't discover which playbook commit it runs from, so every repo's
  managed workflow passes `playbook-ref: v1` next to `@v1`. Both come from one
  template, so they can't disagree. This repo calls `./.github/workflows/pipeline.yml`
  with its own commit, so a PR here tests the pipeline it changes.
- **Pipeline scripts are fetched into `$RUNNER_TEMP`, not checked out into the
  workspace.** A copy inside the workspace would be linted and tested as part of the
  calling repo.
- **The registry upload is a job in each repo's own `playbook.yml`.** crates.io and
  NuGet match trusted publishing against the workflow file that publishes; neither
  documents support for a workflow in another repo.
- **A change to anything that renders into repos ships as a release.** Merged
  changes reach repos only when `VERSION` moves. A change that needs repos to change
  is a new major.
- **The installers keep their whole body in `main`, called on the last line.**
  People pipe them into `sh`, and a shell reading a pipe runs each command as it
  arrives, so a dropped connection must not run half the script.

<!-- playbook:begin -->
## How work lands here

> Managed by [leduftw/playbook](https://github.com/leduftw/playbook) v1. Change it there, not here; `playbook sync` brings this section back in line.

The facts about this repo live in `.github/playbook.toml`. Everything in this section is the same in every repo that follows the playbook.

### Every change

1. **Start from an issue.** Reuse the issue that describes the change, or create one assigned to `leduftw` with at least one label.
2. **Work in your own worktree.** Run `playbook start <issue>`: it creates `~/Developer/.worktrees/playbook/<issue>-<slug>` on branch `dev/leduftw/<issue>-<slug>`, based on the latest `main`. Work only there. Another session may be working at the same time, so never edit the primary checkout; it stays on `main` and only moves with `git pull`.
3. **Commit and push** each finished slice to that branch, without pausing for confirmation.
4. **Open the PR** with `gh pr create --base main`. Its title becomes the commit on `main`, so it follows the commit style below. Put `Closes #<issue>` in the body when the PR fully resolves the issue.
5. **Land it with `playbook finish`.** It waits for the required checks, merges `main` into the branch if `main` has moved on, squash-merges, removes the worktree and the branch, pulls `main`, and confirms the issue is closed. If a check fails, fix it on the branch and run `playbook finish` again.

Sessions that only read, plan or answer questions need no issue and no worktree.

**Never** commit or push to `main` (hooks and a GitHub ruleset refuse it), rebase a branch that's already pushed, or force-push. To catch up with `main`, merge it into your branch.

**Commit style:** start with a lowercase verb that says what the change does, then plain words, with no `feat:`-style prefix and no full stop. Names keep their capitals: `fix Windows installer replacement`, `add opt-in global leaderboard`. The `playbook / title` check rejects PR titles that break this.

**Without the `playbook` command** (a cloud session, a fresh machine), do the same by hand:

- start: `git fetch origin`, then `git worktree add -b dev/leduftw/<issue>-<slug> ~/Developer/.worktrees/playbook/<issue>-<slug> origin/main` (a cloud session is already isolated, so a branch from `origin/main` is enough)
- finish: `gh pr checks --watch --required`, then `gh pr merge --squash`; once the PR shows `MERGED`, `git worktree remove <path>`, `git branch -D <branch>` (`-d` refuses, because squash commits aren't ancestors of `main`) and `git pull --ff-only` in the primary checkout

### Releasing the playbook

Repos pin the playbook as `v1`, so a release is what carries a change to them.

- `playbook release patch|minor|major` opens a PR that only bumps `VERSION`. Use patch for fixes, minor for anything repos can take without changing, and major when repos must change too; a new major reaches each repo through its own PR.
- Landing that PR publishes `vX.Y.Z` and moves `vX`, so every repo on that major picks the change up on its next run.
<!-- playbook:end -->
