#!/usr/bin/python3
"""PreToolUse guard denying git commits whose comments carry a measurement or a narrative.
A comment holds what the code does and the invariant behind it; a measured number
belongs in docs and the story of how the code got here belongs in the commit
message. Given paths instead of a hook payload, it surveys them and reports."""

import argparse
import json
import pathlib
import re
import sys

from pygments import lexers, token, util

import commits

PROSE_SUFFIXES = frozenset({"md", "markdown", "rst", "txt"})
# Preprocessor lines and shebangs are code that pygments happens to tag as comment.
NOT_PROSE_TOKENS = (token.Comment.Preproc, token.Comment.PreprocFile, token.Comment.Hashbang)

DIRECTIVE = (
    "Blocked: this commit would record a comment that goes stale. Per directive, a comment "
    "states what the code does and the invariant behind it -- never a measured number, a "
    "date, nor the story of how the code got here. Move a measurement to docs, put the "
    "change story in the commit message, keep the comment to the durable fact, then commit."
)

UNIT = (
    r"(?:ns|µs|us|ms|sec|secs|seconds?|minutes?|hours?|[KMGT]i?B|kB|[KMG]?B/s|"
    r"[kKMG]?bps|[kKMG]bit/s|[mkKMG]?W|J|fps|[kKMG]?sps|iops|qps|rps|ops/s|req/s)"
)
QUANTITY = re.compile(rf"(?<![\w.])\d[\d,]*(?:\.\d+)?\s*{UNIT}(?![\w/])")
RATIO = re.compile(r"(?<![\w.])\d+(?:\.\d+)?\s*(?:[x×](?!\w)|times\b)", re.I)
# A ratio is a claim only beside what it is a ratio of; a bare `1x` names a standard.
COMPARATIVE = re.compile(
    r"\b(?:faster|slower|better|worse|cheaper|quicker|smaller|larger|more|less|fewer|"
    r"speedup|improvement|reduction|overhead|gain|win|throughput|latency)\b",
    re.I,
)
PERCENT = re.compile(
    r"(?<![\w.])\d+(?:\.\d+)?\s*%\s*(?:faster|slower|better|worse|improvement|reduction|"
    r"fewer|less|more|overhead|speedup|gain|win)\b"
    r"|\b(?:faster|slower|better|worse|improved|reduced|cheaper|smaller|larger)\s+by\s+"
    r"\d+(?:\.\d+)?\s*%",
    re.I,
)
DATED = re.compile(
    r"\b(?:19|20)\d\d-[01]\d-[0-3]\d\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?,?\s+(?:19|20)\d\d\b"
    r"|\bQ[1-4]\s+(?:19|20)\d\d\b",
    re.I,
)
VERSIONED = re.compile(
    r"\b(?:the |our |this )?(?:old|previous|earlier|original|legacy)\s+"
    r"(?:code|version|implementation|approach|behaviou?r|way|design|logic)\b"
    # The purpose sense follows a form of "be"; the story sense does not.
    r"|(?<!be )(?<!been )(?<!being )(?<!is )(?<!are )(?<!was )(?<!were )"
    r"\bused to (?:be|use|call|return|do|have|hold|store|live|work|take|handle|mean)\b"
    r"|\binstead of (?:the )?(?:old|previous|earlier|original)\b"
    r"|\b(?:this|it) (?:replaces|replaced|supersedes|superseded)\b"
    r"|\b(?:rewritten|refactored|migrated|switched|moved|changed)\s+(?:back\s+)?"
    r"(?:from|away from|out of)\b",
    re.I,
)
DIARY = re.compile(
    r"\bturn(?:s|ed) out\b"
    r"|\bit seems\b|\bseems? to work\b"
    r"|\b(?:i|we) (?:tried|found|discovered|noticed|realis|realiz|spent|struggled)"
    r"|\bafter (?:trying|much|several|a few|hours|some)\b"
    r"|\b(?:first|second|third|another|my|our) attempt\b"
    r"|\bfor some reason\b"
    r"|\b(?:unfortunately|surprisingly|oddly|curiously|interestingly|annoyingly|sadly)\b"
    r"|\bnote to self\b"
    r"|\b(?:much|significantly|dramatically|massively|way)\s+"
    r"(?:faster|slower|better|worse|simpler|cheaper|quicker)\b"
    r"|\border(?:s)? of magnitude\b"
    r"|\b(?:huge|big|nice|massive) (?:win|improvement|speedup)\b"
    r"|\bas of\b"
    r"|\bfor now\b"
    r"|\btemporarily (?:disabled|removed|commented|reverted|dropped)\b",
    re.I,
)

# A reporting word beside a number means the number came from a run, whatever its unit.
REPORTED = re.compile(
    r"\b(?:measured|measurement|benchmark(?:ed|s)?|profiled|profiling|observed|took|"
    r"takes (?:about|roughly|around)|spent|latency|throughput|wall[- ]clock|wall time|"
    r"runtime of|on average|averaged?|p50|p90|p95|p99|percentile|timed at|tested at|"
    r"speedup|regression of)\b",
    re.I,
)
# A history word beside a word for change means the comment is telling the story.
HISTORY = re.compile(
    r"\b(?:previously|originally|formerly|historically|until recently|up to now|"
    r"in the past|before this (?:change|commit|patch|fix|version|refactor))\b",
    re.I,
)
CHANGE = re.compile(
    r"\b(?:was|were|had|used|did|now|instead|replaced|removed|added|changed|switched|"
    r"rewritten|refactored|version|implementation|approach|code)\b",
    re.I,
)
NUMBER = re.compile(r"(?<![\w.#])\d+(?:\.\d+)?(?![\w%])")

LICENCE = re.compile(
    r"\bcopyright\b|\bSPDX-License-Identifier\b|\blicen[cs]ed under\b"
    r"|\b(?:GPL|LGPL|MIT|BSD|Apache|MPL)[- ]?\d",
    re.I,
)
BACKTICKED = re.compile(r"`[^`\n]*`")
# Spans that name something rather than measure it, blanked before the rules run.
NAMING = (
    re.compile(r"\bhttps?://\S+"),
    re.compile(r"(?<![\w.])#\d+\b"),
    re.compile(r"\bv\d+(?:\.\d+)*\b|\b\d+\.\d+\.\d+\b"),
    re.compile(r"\b(?:RFC|ISO|IEEE|ANSI|ETSI|MIL-STD|EN|POSIX)[- ]?\d+\b", re.I),
)

RULES = (
    (QUANTITY, "a measured quantity"),
    (PERCENT, "a percentage claim"),
    (DATED, "a date"),
    (VERSIONED, "the story of an earlier version"),
    (DIARY, "a note about the author's process"),
)
PAIRED = (
    (RATIO, COMPARATIVE, "a speedup or ratio"),
    (REPORTED, NUMBER, "a number reported as an observation"),
    (HISTORY, CHANGE, "the story of an earlier version"),
)


def _blank(match):
    return " " * len(match.group(0))


def _keep_quantities(match):
    """Leave a backticked span alone unless it hides a quantity behind the quoting."""
    span = match.group(0)
    return span if QUANTITY.search(span) or RATIO.search(span) else _blank(match)


def mask(text):
    """Blank the spans that name a symbol or a reference, keeping every offset."""
    out = BACKTICKED.sub(_keep_quantities, text)
    for pattern in NAMING:
        out = pattern.sub(_blank, out)
    return out


def offences(text):
    """Every (offset, why) at which this comment records something that will go stale."""
    if LICENCE.search(text):
        return []
    bare = mask(text)
    found = [(m.start(), why) for pattern, why in RULES for m in pattern.finditer(bare)]
    for trigger, companion, why in PAIRED:
        hit = trigger.search(bare)
        if hit and companion.search(bare):
            found.append((hit.start(), why))
    return sorted(found)


def is_prose(kind):
    """True for a token that carries prose, not preprocessor or shebang text."""
    if not (kind in token.Comment or kind in token.String.Doc):
        return False
    return not any(kind in skipped for skipped in NOT_PROSE_TOKENS)


def lexer_for(path):
    """The pygments lexer for a source path, or None for prose and the unrecognised."""
    if path.rsplit(".", 1)[-1].lower() in PROSE_SUFFIXES:
        return None
    for name in (path, path.lower()):
        try:
            return lexers.get_lexer_for_filename(name, stripnl=False)
        except util.ClassNotFound:
            continue
    return None


def spans(path, text):
    """Yield (offset, text) for every comment and docstring in a source file."""
    lexer = lexer_for(path)
    if lexer is None:
        return
    for start, kind, value in lexer.get_tokens_unprocessed(text):
        if is_prose(kind):
            yield start, value


def scan(path, text):
    """Return one (line, why, source) per offending line, in file order."""
    lines = text.splitlines()
    found = {}
    for start, comment in spans(path, text):
        for at, why in offences(comment):
            line = text.count("\n", 0, start + at) + 1
            if line <= len(lines):
                found.setdefault(line, (line, why, lines[line - 1].strip()))
    return [found[key] for key in sorted(found)]


def wanted(path):
    """True for a path worth lexing: a source file pygments recognises."""
    return lexer_for(path) is not None


def findings(contents):
    """Return [(path, line, why, source)] for every offending comment in {path: bytes}."""
    found = []
    for path, blob in sorted(contents.items()):
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue
        found += [(path, line, why, source) for line, why, source in scan(path, text)]
    return found


def reason(found):
    """Name the offending comments and what to do with each."""
    detail = "; ".join(f"{path}:{line}: {why}: {source}" for path, line, why, source in found[:8])
    more = "" if len(found) <= 8 else f" (and {len(found) - 8} more)"
    return f"{DIRECTIVE}\nComments: {detail}{more}"


def hook():
    """Read a hook payload on stdin and deny a commit carrying a stale comment."""
    found = []
    for _, contents in commits.committed(commits.payload(), wanted):
        found += findings(contents)
    if found:
        print(json.dumps(commits.deny(reason(found))))
    return 0


def survey(paths, listing):
    """Report the offending comments in paths, failing unless listing."""
    hits = 0
    for name in paths:
        path = pathlib.Path(name)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line, why, source in scan(name, text):
            print(f"{name}:{line}: {why}: {source}")
            hits += 1
    if not hits:
        print("no measurement, date or narrative in a comment")
        return 0
    print(f"\n{hits} comment(s) will go stale: move each out of the code.")
    return 0 if listing else 1


def main(argv=None):
    par = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    par.add_argument("files", nargs="*", help="survey these paths; default: read a hook payload")
    par.add_argument("--list", action="store_true", help="report and exit 0")
    args = par.parse_args(argv)
    return survey(args.files, args.list) if args.files else hook()


if __name__ == "__main__":
    sys.exit(main())
