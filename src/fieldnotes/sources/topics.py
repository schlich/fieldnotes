"""Published topics on the Tangled knot that have not landed on main.

`ci dispatch` publishes each topic as a `jj-*` branch, which waits there
until `ci land` delivers it.
"""

from __future__ import annotations

import functools
import json
import os
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

from rich.text import Text

from fieldnotes.model import Detail, Item, Section, SourceError, ago, now, parse_time, stamp
from fieldnotes.sources.base import Source

# A topic that has waited this long to land is highlighted.
STALE_DAYS = 2
# `ci dispatch` names a branch `jj-<title slug>-<short change ID>`.
CHANGE_ID = re.compile(r"-([k-z]{8})$")
CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
)


@functools.cache
def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    if not context.cert_store_stats()["x509_ca"]:
        # A standalone Python, such as one uv installs, may not know where
        # this system keeps its certificates (NixOS, for one).
        for bundle in CA_BUNDLES:
            if os.path.exists(bundle):
                context.load_verify_locations(bundle)
                break
    return context


@dataclass
class Topic:
    branch: str
    title: str
    message: str
    commit: str
    updated: datetime | None
    authored: datetime | None

    def stale(self, at: datetime) -> bool:
        return self.updated is not None and (at - self.updated).days >= STALE_DAYS

    @property
    def change_id(self) -> str:
        match = CHANGE_ID.search(self.branch)
        return match.group(1) if match else ""


def pending_topics(branches: list[dict], main_log: list[dict]) -> list[Topic]:
    """Published topics that have not landed, longest-waiting first."""
    # `ci land` rebases a topic before it lands, so a landed topic's branch may
    # still name the pre-rebase commit; match its title as well.
    landed_hashes = {bytes(commit.get("hash") or []).hex() for commit in main_log}
    landed_titles = {
        (commit.get("message") or "").splitlines()[0]
        for commit in main_log
        if commit.get("message")
    }
    topics = []
    for branch in branches:
        reference = branch.get("reference") or {}
        name = reference.get("name", "")
        if not name.startswith("jj-"):
            continue
        commit = branch.get("commit") or {}
        message = commit.get("Message") or name
        title = message.splitlines()[0]
        if reference.get("hash") in landed_hashes or title in landed_titles:
            continue
        topics.append(
            Topic(
                branch=name,
                title=title,
                message=message.strip(),
                commit=reference.get("hash") or "",
                updated=parse_time((commit.get("Committer") or {}).get("When")),
                authored=parse_time((commit.get("Author") or {}).get("When")),
            )
        )
    at = now()
    topics.sort(key=lambda topic: topic.updated or at)
    return topics


class TopicsSource(Source):
    name = "topics"
    title = "Waiting to land"
    empty = "Every published topic has landed."

    def knot(self, method: str, params: dict) -> dict:
        url = f"{self.settings.tangled_knot}/xrpc/{method}?{urllib.parse.urlencode(params)}"
        # The knot refuses urllib's default User-Agent.
        request = urllib.request.Request(url, headers={"User-Agent": "fieldnotes"})
        with urllib.request.urlopen(request, timeout=12, context=tls_context()) as response:
            return json.load(response)

    def fetch(self) -> list[Topic]:
        repo = self.settings.tangled_repo_did
        try:
            branches = self.knot("sh.tangled.repo.branches", {"repo": repo}).get("branches", [])
            log = self.knot("sh.tangled.repo.log", {"repo": repo, "ref": "main", "limit": 200}).get(
                "commits", []
            )
        except (OSError, ValueError) as error:
            raise SourceError(f"Tangled knot unreachable: {error}") from None
        return pending_topics(branches, log)

    def compare_url(self, topic: Topic) -> str:
        return f"{self.settings.tangled_repo_url}/compare/main...{topic.branch}"

    def heading(self, data: list[Topic]) -> str:
        return f"{self.title} / {len(data)}" if data else self.title

    def items(self, data: list[Topic]) -> list[Item]:
        at = now()
        rows = []
        for topic in data:
            stale = topic.stale(at)
            rows.append(
                Item(
                    key=topic.branch,
                    title=topic.title,
                    state="stale" if stale else "waiting",
                    status=ago(topic.updated, at),
                    lines=(topic.branch,),
                    url=self.compare_url(topic),
                    value=topic.branch,
                    value_label="branch",
                    data=topic,
                )
            )
        return rows

    def summary(self, data: list[Topic]) -> tuple[str, str] | None:
        if not data:
            return None
        at = now()
        stale = sum(topic.stale(at) for topic in data)
        text = f"{len(data)} waiting"
        if stale:
            return f"{text}, {stale} over {STALE_DAYS}d", "stale"
        return text, "waiting"

    def detail(self, item: Item) -> Detail:
        topic: Topic = item.data
        at = now()
        fields = [
            ("Branch", topic.branch),
            ("Change", topic.change_id or "—"),
            ("Commit", topic.commit[:12] or "—"),
            ("Updated", stamp(topic.updated, at)),
            ("Authored", stamp(topic.authored, at)),
            ("Waiting", f"over {STALE_DAYS} days" if topic.stale(at) else "—"),
            ("Compare", self.compare_url(topic)),
            ("Tree", f"{self.settings.tangled_repo_url}/tree/{topic.branch}"),
        ]
        return Detail(topic.title, fields, [Section("Description", Text(topic.message))])
