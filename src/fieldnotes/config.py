"""Settings, read from `FIELDNOTES_*` variables and overridden by flags."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

from fieldnotes import __version__


def state_home(environ: Mapping[str, str]) -> Path:
    return Path(environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state"))


def default_tasks_dir(environ: Mapping[str, str]) -> Path:
    return state_home(environ) / "fieldnotes" / "agent-tasks"


def default_jev_log(environ: Mapping[str, str]) -> Path:
    return state_home(environ) / "jev" / "usage.jsonl"


@dataclass(frozen=True)
class Settings:
    ci_repo: str = field(
        default="schlich/dotfiles",
        metadata={"help": "GitHub repository whose Actions runs the CI panel shows"},
    )
    tangled_knot: str = field(
        default="https://knot1.tangled.sh",
        metadata={"help": "Tangled knot that hosts the published topic branches"},
    )
    tangled_repo_did: str = field(
        default="did:plc:3ta3pjip7mu36b7dnznhoyri",
        metadata={"help": "DID of the repository on that knot"},
    )
    tangled_repo_url: str = field(
        default="https://tangled.org/@schlich.tngl.sh/dotfiles",
        metadata={"help": "web page of the same repository, for opening a topic"},
    )
    tasks_dir: Path = field(
        default_factory=lambda: default_tasks_dir(os.environ),
        metadata={"help": "background task records: this host's own and other hosts' reports"},
    )
    jev_log: Path = field(
        default_factory=lambda: default_jev_log(os.environ),
        metadata={"help": "ledger of Jev responses whose token usage the provider panel sums"},
    )
    iwe_root: Path = field(
        default_factory=lambda: Path("~/kb").expanduser(),
        metadata={"help": "IWE knowledge base the librarian panel reads"},
    )
    poll_seconds: float = field(
        default=30.0,
        metadata={"help": "how often the remote sources refresh (at least 5)"},
    )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> Settings:
        values: dict[str, object] = {
            "tasks_dir": default_tasks_dir(environ),
            "jev_log": default_jev_log(environ),
        }
        for item in fields(cls):
            raw = environ.get(f"FIELDNOTES_{item.name.upper()}")
            if raw:
                values[item.name] = convert(item.name, raw)
        return cls(**values)


def convert(name: str, raw: str) -> object:
    if name.endswith(("_dir", "_root", "_log")):
        return Path(raw).expanduser()
    if name == "poll_seconds":
        return max(5.0, float(raw))
    return raw


def parse_args(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] = os.environ
) -> Settings:
    parser = argparse.ArgumentParser(
        prog="fieldnotes",
        description=(
            "A terminal dashboard for coding-agent usage, CI, topics waiting "
            "to land, and background tasks."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    for item in fields(Settings):
        parser.add_argument(
            f"--{item.name.replace('_', '-')}",
            dest=item.name,
            metavar=item.name.split("_")[-1].upper(),
            help=f"{item.metadata['help']}; ${'FIELDNOTES_' + item.name.upper()}",
        )
    args = parser.parse_args(argv)
    settings = Settings.from_env(environ)
    overrides = {
        name: convert(name, value) for name, value in vars(args).items() if value is not None
    }
    return replace(settings, **overrides)
