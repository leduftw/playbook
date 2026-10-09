# playbook

One standard for how every one of my repos is worked on, checked and released.
Repos differ in what they do. Everything around the code is the same, it lives
here, and changing it here changes it everywhere.

A repo follows the playbook when it has `.github/playbook.toml`: a short file of
facts about the repo (its name, how its code is checked, and for a published
repo what a release contains). Everything else comes from this repo.

## What every repo gets

- **`main` is the default branch, and nothing lands on it except through a PR.**
  A GitHub ruleset requires a PR whose checks pass, and the repo's git hooks
  refuse commits and pushes to `main` locally.
- **One worktree per change.** `playbook start <issue>` creates
  `~/Developer/.worktrees/<repo>/<issue>-<slug>` on branch
  `dev/leduftw/<issue>-<slug>`. The primary checkout stays on `main`.
- **Every change starts from an issue** and lands as a PR that links it.
- **`playbook finish` lands it:** waits for the checks, merges `main` in if it
  moved, squash-merges, deletes the worktree and branch, pulls `main`, and
  confirms the issue closed.
- **Squash is the only merge method.** The PR title becomes the commit on
  `main`, written in one style: a lowercase verb first, plain words, no
  `feat:`-style prefix, no full stop (`fix Windows installer replacement`).
  The `playbook / title` check enforces it.
- **The same checks everywhere:** formatting, lint with warnings as errors, and
  tests. Published repos run them on macOS, Linux and Windows; local repos on
  Linux unless their code depends on the OS. A step can only be skipped with a
  written reason in `playbook.toml`.
- **`AGENTS.md` and `CLAUDE.md` are identical**, so Codex and Claude Code read
  the same instructions. Part of them is the playbook's section, kept in sync
  from here; the rest belongs to the repo.
- **Dependabot** opens grouped updates weekly; minor and patch updates merge
  themselves once green, major ones wait for an agent session. Actions are
  pinned to exact commits.

## What published repos also get

A published repo is one other people install. Today that's
[dbird](https://github.com/leduftw/dbird) and [monad](https://github.com/leduftw/monad).

- **Releasing is one command.** `playbook release patch|minor|major` opens a PR
  that only bumps the version. Nobody types a version number: the first release
  is always 1.0.0, and each later one must be exactly the next patch, minor or
  major. The check refuses anything else, so a project can't start at 2.0.0 or
  skip from 1.2.0 to 1.4.0.
- **The release PR proves the release** before anything is published: it builds
  every file for six platforms (macOS, Linux and Windows, each on Apple/ARM and
  Intel/AMD chips), opens each archive and checks its files, CPU type, linking
  and reported version, runs the installers against it, installs the Homebrew
  formula from a scratch tap, and dry-runs the registry upload.
- **Merging it publishes:** a draft release gets every file, `SHA256SUMS` and a
  signed provenance record, then is published, which creates the tag. Releases
  are locked once published, so a broken one is fixed by shipping the next
  patch. Then the channels update:
  - the Homebrew tap: `brew install leduftw/tap/<name>`
  - WinGet: `winget install leduftw.<name>`
  - one-line installers:
    `curl -fsSL https://github.com/leduftw/<name>/releases/latest/download/<name>-installer.sh | sh`
    and `irm https://github.com/leduftw/<name>/releases/latest/download/<name>-installer.ps1 | iex`
  - the language registry (crates.io or NuGet) through trusted publishing, so
    no registry token is stored anywhere
- **Release files have stable names:** `<name>-<os>-<arch>.zip` (macOS, Windows)
  or `.tar.gz` (Linux), such as `dbird-macos-arm64.zip`. The version is in the
  release's address, not the file name, so `releases/latest/download/...`
  always points at the newest one.

## How a change here reaches every repo

Each repo's workflow is a few lines that call this repo's shared pipeline,
pinned to a major version:

```yaml
uses: leduftw/playbook/.github/workflows/pipeline.yml@v1
```

`v1` is a tag that always points at the newest 1.x release of the playbook.
A fix or a compatible improvement is released as 1.x and reaches every repo on
its next run, without touching the repo. A change that needs repos to change is
released as 2.0.0; repos keep running v1 until a PR moves each one to `v2`.

Files that have to live inside each repo (the hooks, the two small workflow
files, the Dependabot settings and the playbook's section of `AGENTS.md`) are
rendered from `templates/` and kept current by `playbook sync`, which proposes
the update to each repo as a PR that merges itself once its checks pass.
`playbook audit` reports any repo that has drifted.

## Commands

Put `bin/` on `PATH` (macbook-setup does this). Every command needs `gh`
signed in.

| Command | What it does |
| --- | --- |
| `playbook start <issue>` | Creates the worktree and branch for an issue |
| `playbook finish` | Waits for checks, squash-merges, cleans up |
| `playbook release patch\|minor\|major` | Opens the PR that releases the next version |
| `playbook new <name> --published\|--local --stack <stack> --description "..."` | Starts a repo that follows the playbook |
| `playbook sync [--here \| <repo>... \| --all]` | Brings the playbook's files in a repo up to date |
| `playbook settings [<repo>... \| --all]` | Applies the shared GitHub settings |
| `playbook audit [--here \| <repo>... \| --all]` | Reports anything that differs from the playbook |

## Starting a repo

The first question for any new repo is whether it's **published** (other people
install it) or **local** (it stays on my machines). `playbook new` won't guess:

```sh
playbook new mytool --published --stack rust --description "a tool that does one thing well"
playbook new notes --local --private --stack python --description "scripts for my notes"
```

It creates the project, the playbook's files, the GitHub repo with its
settings, and lists what's left for a human. A local repo can be published
later by adding `published = true` and a `[release]` table to its
`playbook.toml`. Publishing rules exist for command-line tools in Rust and .NET;
rules for desktop apps and services get written the first time one ships.

## One-time steps that need a human

- **GitHub Pro** for private repos: GitHub Free can't protect `main` in a
  private repo, so until then those repos rely on their local hooks.
- **Registry trusted publishing**, once per published repo: on crates.io or
  NuGet, add a trusted publisher with repository `leduftw/<name>`, workflow
  `playbook.yml` and environment `release`.
- **WinGet**: one classic GitHub token with the `public_repo` scope, stored in
  each published repo's release environment
  (`gh secret set WINGET_TOKEN --env release --repo leduftw/<name>`), and a
  manual first submission of each new package (`wingetcreate new <url>`).

`playbook settings` does everything else, including each published repo's own
deploy key for the Homebrew tap.

## Checking a download

Each release lists the SHA-256 fingerprint of every file in `SHA256SUMS`, and
the installers, Homebrew and WinGet check it automatically. Each file also has a
provenance record signed by GitHub during the build. Because the playbook's
pipeline does the building, verify it like this:

```sh
gh attestation verify dbird-macos-arm64.zip --repo leduftw/dbird --signer-repo leduftw/playbook
```

## Working on the playbook

It's plain Python 3.11+ with no dependencies, plus shell and PowerShell
templates.

```sh
python3 -m unittest discover -s tests -t .
uvx ruff check . && uvx ruff format --check .
actionlint
```

The playbook follows itself: its own changes go through issues, worktrees and
PRs, and its own checks run through `pipeline.yml` from the same commit.
`playbook release` bumps `VERSION`; merging that PR publishes the release and
moves the major tag.
