"""Find remote git URLs for easy sharing. They can be printed with .gitsource_().

This module makes at least the following assumptions:
- You want to use the remote called "origin"
- The remote supports URLs that look like Github's and Gitlab's
- The remote supports HTTPS
It also has trouble with code that was merged from another remote.
"""

from __future__ import print_function

import os
import re
import subprocess

from odoo_repl.imports import t, urlparse, odoo, BaseModel
from odoo_repl import color
from odoo_repl import config
from odoo_repl import sources
from odoo_repl import util

PAT_URL = re.compile(r"\w+://.*")


class GitSourceError(RuntimeError):
    pass


class GitProcessError(GitSourceError):
    pass


def git(path, *args):
    # type: (t.Text, t.Text) -> t.Text
    """Execute a git command in the context of a file."""
    if not os.path.isdir(path):
        path = os.path.dirname(path)
    argv = ["git", "-C", path]
    argv.extend(args)
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stdin=subprocess.PIPE, universal_newlines=True
    )
    assert proc.stdin
    assert proc.stdout
    proc.stdin.close()
    output = proc.stdout.read()
    status = proc.wait()
    if status:
        raise GitProcessError(
            "{!r} failed with status {}".format(" ".join(argv), status)
        )
    if output.endswith("\n"):
        output = output[:-1]
    return output


def get_config(path, key):
    # type: (t.Text, t.Text) -> t.Text
    return git(path, "config", "--", key)


def root(path):
    # type: (t.Text) -> t.Text
    """Get the root directory of a git repository."""
    try:
        return git(path, "rev-parse", "--show-toplevel")
    except GitProcessError:
        raise GitSourceError("File {!r} is not in a repository!".format(path))


def abbreviate(path, commit):
    # type: (t.Text, t.Text) -> t.Text
    """Find a suitable short yet unique version of a commit hash."""
    return git(path, "rev-parse", "--short", commit)


def remote_base(path):
    # type: (t.Text) -> t.Text
    """Get the base HTTPS URL for a repository's origin remote."""
    origin_url = get_config(path, "remote.origin.url")
    if PAT_URL.match(origin_url):
        url = urlparse(origin_url)
        base = "https://{}{}".format(url.hostname, url.path)
    else:
        if "@" in origin_url:
            _, origin_url = origin_url.split("@", 1)
        if ":" not in origin_url:
            raise GitSourceError("Can't parse remote URL {!r}!".format(origin_url))
        hostname, path = origin_url.split(":", 1)
        hostname = hostname.strip("/")
        path = path.strip("/")
        base = "https://{}/{}".format(hostname, path)
    if base.endswith("/"):
        base = base[:-1]
    if base.endswith(".git"):
        base = base[:-4]
    return base


def branches_containing_commit(path, commit):
    # type: (t.Text, t.Text) -> t.List[t.Text]
    """Return refs of remote branches that contain a commit."""
    return git(
        path, "branch", "-r", "--format", "%(refname)", "--contains", commit
    ).split("\n")


def commit_for_file(path, abbrev=True):
    # type: (t.Text, bool) -> t.Text
    """Return an appropriate commit or branch for a file."""
    commit = git(path, "rev-list", "-1", "HEAD", "--", path)
    if not commit:
        raise GitSourceError("File {!r} hasn't been committed!".format(path))

    containing = branches_containing_commit(path, commit)
    if not any(branch.startswith("refs/remotes/origin/") for branch in containing):
        # Latest commit doesn't exist on remote, fall back to branch
        # The line number is likely to be off
        return odoo.release.version

    if abbrev:
        commit = abbreviate(path, commit)

    return commit


def to_url(path):
    # type: (t.Text) -> t.Text
    """Turn a file path into a shareable URL."""
    path = os.path.realpath(path)  # For symlinks
    rootdir = root(path)
    base = remote_base(path)
    trail = os.path.relpath(path, rootdir)
    mode = "tree" if os.path.isdir(path) else "blob"
    commit = commit_for_file(path)
    return "{}/{}/{}/{}".format(base, mode, commit, trail)


def format_source(source):
    # type: (sources.Source) -> t.Text
    module, fname, lnum = source
    fname = to_url(fname)
    if lnum is not None:
        fname += "#L{}".format(lnum)
    if config.clickable_filenames:
        fname = color.linkify(fname, fname)
    return "{}: {}".format(color.module(module), fname)


def format_sources(sourcelist):
    # type: (t.Iterable[sources.Source]) -> t.List[t.Text]
    return [format_source(source) for source in sourcelist]


def gitsource(thing):
    # type: (sources.Sourceable) -> None
    for source in sources.find_source(thing):
        try:
            fmt = format_source(source)
        except GitSourceError as exc:
            fmt = "{}: {}".format(color.module(source.module), color.missing(str(exc)))
        print(fmt)


util.patch(BaseModel, "gitsource_", gitsource)
