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
  and reported version, checks the main binary against the
  [command-line rules](#how-command-line-tools-behave), runs the installers
  against it, installs the Homebrew formula from a scratch tap, and dry-runs
  the registry upload. A .NET tool is packed for NuGet on a Mac, so its macOS
  packages can carry Mac-only binaries, then installed from those packages and
  held to the same checks as the main binary.
- **Merging it publishes:** a draft release gets every file, `SHA256SUMS` and a
  signed provenance record, then is published, which creates the tag. Releases
  are locked once published, so a broken one is fixed by shipping the next
  patch. Then the channels update:
  - the Homebrew tap: `brew install leduftw/tap/<name>`
  - WinGet: `winget install leduftw.<name>`
  - one-line installers:
    `curl -fsSL https://github.com/leduftw/<name>/releases/latest/download/<name>-installer.sh | sh`
    and `irm https://github.com/leduftw/<name>/releases/latest/download/<name>-installer.ps1 | iex`
  - the language registry through trusted publishing, so no registry token is
    stored anywhere: crates.io (`cargo install <name> --locked`) or NuGet
    (`dotnet tool install -g <package id>`)
- **A failed channel is retried without a new release:** once the cause is
  fixed (an expired token, say), `gh workflow run playbook --ref main` updates
  the channels of the latest release from its published files. Each channel
  runs on its own, so one failing never holds back the others.
- **Topics say what it is, so strangers can find it:** 5–10 of them covering
  what the program is, its language, and the platforms it runs on. Platform
  topics (`macos`, `windows`, `linux`) are all of the release's platforms or
  none, never a subset, which would read as "only runs here". Local repos need
  no topics: nobody searches for them.
- **Release files have stable names:** `<name>-<os>-<arch>.zip` (macOS, Windows)
  or `.tar.gz` (Linux), such as `dbird-macos-arm64.zip`. The version is in the
  release's address, not the file name, so `releases/latest/download/...`
  always points at the newest one.

## How command-line tools behave

A command-line tool in any of these repos behaves like the others, whatever its
language. The general approach comes from the
[Command Line Interface Guidelines](https://clig.dev), so read them before
designing a tool's commands and options. These rules settle what they leave
open:

- **Help:** `-h` and `--help` print the help to stdout and exit 0. A tool with
  subcommands also takes `help` and `help <command>`. The help describes the
  tool with the repo's GitHub description, word for word.
- **Version:** `-V` and `--version` print exactly `<name> <version>`, such as
  `dbird 1.3.0`, and exit 0. A tool with subcommands also takes `version`. `-v`
  is left for `--verbose`.
- **Mistakes:** an unknown command or option, or a missing or wrong value,
  prints a message to stderr and exits 2. A command that ran and failed exits 1.
  That tells a script its own mistake (2: retrying never helps) from a failure
  (1: retrying might).
- **No arguments:** a tool started with no arguments at all does its default
  job when it can, such as a game starting a round. If something the job needs
  isn't set up yet, such as a token or a terminal, it prints the help plus one
  line saying what's missing, and exits 0. WinGet's validation starts every new
  version this way. An explicit command that can't run (`monad run` without a
  token) still exits 1, and a config file that exists but can't be read is an
  error either way.
- **Colors:** the palette of Python 3.14's `argparse`: the `usage:` label and
  headings in bold blue, the program's name, subcommand included
  (`playbook start`), in bold magenta, commands, arguments and short options
  (`-h`) in bold green, long options (`--help`) in bold cyan, and option values
  in bold yellow. Inside the usage line, arguments, options and values take the
  same colors without bold. These are the terminal's basic colors, so they
  follow its theme. Colors appear only when the output goes to a terminal, and
  `NO_COLOR`, `TERM=dumb` or `--no-color` (anywhere on the command line) turn
  them off.
- **Words and dashes:** actions are subcommands, without dashes (`update`,
  `run`); settings are options, with dashes (`--mute`, `--token`). Only help and
  version come in both forms. A tool whose arguments are data, such as a file
  to read, takes no `help` or `version` word, because `subtitler help` must be
  free to mean a file called `help`.
- **Parsing:** each language's standard parser, never a hand-written one, so
  the help comes from the same definitions the parser uses: `argparse` in
  Python, `clap` in Rust and `System.CommandLine` in .NET. In .NET the library
  only parses: the tool prints its own help, version and errors, because the
  library's defaults break these rules.

A published tool also has `update`, with `upgrade` as an alias. It asks GitHub
for the latest release and, if that's newer than itself, updates through the
channel that installed it. It finds the channel from where its own file is,
after following links: `/opt/homebrew/bin/<name>` is only Homebrew's link into
its `Cellar`.

| Installed with | Recognized by | `update` runs |
| --- | --- | --- |
| Homebrew | `/Cellar/<name>/` in the path | `brew upgrade leduftw/tap/<name>` |
| WinGet | `\Microsoft\WinGet\` in the path | `winget upgrade --id leduftw.<name>` |
| crates.io | Cargo's `bin` folder | `cargo install <name> --locked` |
| NuGet | `.store/<package id>/` in the path | `dotnet tool update --tool-path <folder> <package id>`, for the folder holding that `.store` |
| A local build | `target/` or `bin/Debug` in the path | nothing: it refuses |
| The one-line installer | anywhere else | the release's installer, into the same folder |

If it's already the latest version, `update` says so and exits 0. A release
reaches WinGet only after Microsoft reviews it, which can take days, and NuGet
once it has checked and indexed the packages, which takes minutes. When the
newest version isn't there yet, `update` says that instead of calling itself
current. Windows can't replace a running program, so there `update` starts
`winget`, `dotnet tool update` or the installer and exits.

Every release PR checks a published tool's behavior against these rules, on
all six platforms, with its real binary: `-h`, `--help` and `help` print the
same help to stdout; `-V`, `--version` and `version` print exactly
`<name> <version>`; an unknown option exits 2 with a message on stderr only;
`update --help` and `upgrade --help` show the same command; and a bare run with
no terminal and no credentials exits 0, as WinGet's validation does. None of it
may print color codes, even with `FORCE_COLOR`, `CLICOLOR_FORCE` or
`PYTHON_COLORS` set, since the output goes to a pipe. The layout of the help is
the tool's own.

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

Put `bin/` on `PATH` (macbook-setup does this). Every command except `help` and
`version` needs `gh` signed in. `playbook`, `playbook help` and `--help` list
the commands, `playbook help <command>` shows one command's options, and
`playbook version` or `-V` prints the version.

| Command | What it does |
| --- | --- |
| `playbook start <issue>` | Creates the worktree and branch for an issue |
| `playbook finish` | Waits for checks, squash-merges, cleans up |
| `playbook release patch\|minor\|major` | Opens the PR that releases the next version |
| `playbook new <name> --published\|--local --stack <stack> --description "..." [--topics "..."]` | Starts a repo that follows the playbook |
| `playbook sync [--here \| <repo>... \| --all]` | Brings the playbook's files in a repo up to date |
| `playbook settings [<repo>... \| --all]` | Applies the shared GitHub settings |
| `playbook audit [--here \| <repo>... \| --all]` | Reports anything that differs from the playbook, including missing or misleading topics, README badges that point at removed workflows, and Dependabot PRs waiting more than a week |

## Starting a repo

The first question for any new repo is whether it's **published** (other people
install it) or **local** (it stays on my machines). `playbook new` won't guess:

```sh
playbook new mytool --published --stack rust --description "a tool that does one thing well" \
  --topics "cli,rust,macos,windows,linux"
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
  WinGet's validation starts the program with no arguments, which the
  [command-line rules](#how-command-line-tools-behave) make exit 0 and every
  release PR checks, so no submission needs a comment explaining how to run it.

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
uvx ruff@0.17.0 check . && uvx ruff@0.17.0 format --check .
actionlint
```

The playbook follows itself: its own changes go through issues, worktrees and
PRs, and its own checks run through `pipeline.yml` from the same commit.
`playbook release` bumps `VERSION`; merging that PR publishes the release and
moves the major tag.
