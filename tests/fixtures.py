"""Configs shaped like the real repos, for tests."""

from __future__ import annotations

import tomllib

from playbook.config import Config, parse

DBIRD = """
name = "dbird"
published = true

[[check]]
stack = "rust"
apt = ["libasound2-dev"]

[[check]]
stack = "node"
dir = "leaderboard"
test = ["npm test"]
skip.format = "no formatter configured"
skip.lint = "no linter configured"

[release]
stack = "rust"
apt = ["libasound2-dev"]
channels = ["homebrew", "winget", "installers", "crates-io"]

[release.homebrew]
desc = "Fully playable terminal recreation of the classic Flappy Bird game"
"""

MONAD = """
name = "monad"
published = true

[[check]]
stack = "dotnet"
solution = "Monad.slnx"

[release]
stack = "dotnet"
project = "Monad/Monad.csproj"
macos-binaries = ["monad-audiotap"]
files = ["LICENSE", "README.md", "THIRD-PARTY-NOTICES.md", "monad.example.json"]
version-command = ["version"]
version-output = "monad {version}"
macos-minimum = "14.2"
apt = ["pulseaudio-utils"]
channels = ["homebrew", "winget", "installers", "nuget"]
package-id = "leduftw.monad"
macos-publish-properties = ["-p:RequireAudioTapSidecar=true", "-p:SkipAudioTapSidecar=false"]
other-publish-properties = ["-p:SkipAudioTapSidecar=true"]

[release.smoke]
all = ["{bin} --config {dir}/monad.example.json help"]
macos = ["{dir}/monad-audiotap help"]

[release.homebrew]
desc = "Watches what your machine is playing and logs it as a timeline"
linux-dependencies = ["pulseaudio"]
install = ['pkgshare.install "monad.example.json"']

[release.installer]
notes = ["Set AUDD_API_TOKEN, then run: monad"]
linux-notes = ["Linux capture also needs parec (pulseaudio-utils) or ffmpeg."]
"""

LOCAL = """
name = "media-library"

[[check]]
stack = "python"
"""


def config(text: str) -> Config:
    return parse(tomllib.loads(text))
