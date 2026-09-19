"""Tests for the durable_comments PreToolUse guard."""

import io
import json
import subprocess
import sys

import pytest

import durable_comments
import hooktest
from hooktest import bash, reason, stage

DENIED = [
    ("the ring buffer holds 3 ms of samples", "a measured quantity"),
    ("the vector path is 4x faster than the scalar loop", "a speedup or ratio"),
    ("30% faster than the scalar form", "a percentage claim"),
    ("added 2024-05-01", "a date"),
    ("the old code did this differently", "the story of an earlier version"),
    ("turns out the kernel needs padding", "a note about the author's process"),
    ("latency here is 5", "a number reported as an observation"),
    ("previously this was a lookup", "the story of an earlier version"),
]

ALLOWED = [
    "`buffer_size` is set by the caller",
    "reads `docs/perf-2024-05-01.md` at startup",
    "see https://example.invalid/perf/2024-05-01 for the trace",
    "tracked in #123",
    "matches the v2.1 wire format",
    "profiled in release 1.2.3",
    "timestamps follow RFC 3339",
    "Copyright 2024-05-01 Example Authors",
    "SPDX-License-Identifier: Apache-2.0",
    "802.1x steals the port ACL table",
    "the original path in sys.prefix wins",
    "the buffer will be used to store the tail",
]

# Each pair is the same claim with and without the span that makes it a name.
CONTRASTS = [
    ("reads `docs/perf-2024-05-01.md` at startup", "reads docs/perf-2024-05-01.md at startup"),
    ("profiled in release 1.2.3", "profiled in release 123.4"),
    ("Copyright 2024-05-01 Example Authors", "added 2024-05-01 by hand"),
    ("the buffer will be used to store the tail", "it used to store the tail in a list"),
    ("the original path in sys.prefix wins", "the original design was simpler"),
]

CXX_SOURCE = (
    "// the ring buffer holds 3 ms of samples\n"
    "/* the old code did this differently */\n"
    'const char *note = "// holds 3 ms of samples";\n'
    "int main() { return 0; }\n"
)

PY_SOURCE = (
    "def scale(x):\n"
    '    """turns out the kernel needs padding."""\n'
    '    note = "# holds 3 ms of samples"\n'
    "    return note, x\n"
)

SH_SOURCE = (
    "#!/opt/toolchain-2024-05-01/bin/sh\n"
    "# 30% faster than the scalar form\n"
    'echo "# holds 3 ms of samples"\n'
)

RS_SOURCE = "/// latency here is 5\npub fn scale(x: u32) -> u32 { x }\n"

C_PREPROC_SOURCE = '#define DIR "/var/gen-2024-05-01"\n#include "gen-2024-05-01.h"\nint x;\n'

PY_SHEBANG_SOURCE = "#!/opt/toolchain-2024-05-01/bin/python3\nx = 1\n"

DATED_COMMENT = "# added 2024-05-01\nx = 1\n"
CLEAN_COMMENT = "# the caller owns the buffer\nx = 1\n"


def reported(path, text):
    return [(line, why) for line, why, _ in durable_comments.scan(path, text)]


def written(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


def run(payload, monkeypatch, capsys):
    return hooktest.drive(durable_comments.main, payload, monkeypatch, capsys)


@pytest.mark.parametrize("text,why", DENIED)
def test_each_rule_fires(text, why):
    assert [found for _, found in durable_comments.offences(text)] == [why]


def test_offence_points_at_the_offending_span():
    text = "the ring buffer holds 3 ms of samples"
    assert durable_comments.offences(text) == [(text.index("3 ms"), "a measured quantity")]


def test_scan_reports_every_rule_against_its_own_line():
    source = "".join(f"# {text}\n" for text, _ in DENIED)
    assert reported("m.py", source) == [(i, why) for i, (_, why) in enumerate(DENIED, 1)]


def test_scan_quotes_the_offending_line():
    line, why, shown = durable_comments.scan("m.py", "x = 1\n" + DATED_COMMENT)[0]
    assert (line, why, shown) == (2, "a date", "# added 2024-05-01")


@pytest.mark.parametrize("text", ALLOWED)
def test_named_references_allowed(text):
    assert durable_comments.offences(text) == []


@pytest.mark.parametrize("allowed,denied", CONTRASTS)
def test_the_naming_span_is_what_allows_the_claim(allowed, denied):
    assert durable_comments.offences(allowed) == []
    assert durable_comments.offences(denied)


def test_backticks_do_not_hide_a_quantity():
    assert durable_comments.offences("`3 ms` budget per frame") == [(1, "a measured quantity")]
    assert durable_comments.offences("`2x faster` than the scalar loop")


def test_ratio_needs_something_to_compare():
    assert durable_comments.offences("two 1x hosts share the uplink") == []
    assert durable_comments.offences("the shuffle is 3 times faster with the mask")


def test_cxx_line_and_block_comments_scanned_but_not_strings():
    assert reported("m.cc", CXX_SOURCE) == [
        (1, "a measured quantity"),
        (2, "the story of an earlier version"),
    ]


def test_python_docstring_scanned_but_not_strings():
    assert reported("m.py", PY_SOURCE) == [(2, "a note about the author's process")]


def test_shell_comment_scanned_but_not_shebang_or_strings():
    assert reported("m.sh", SH_SOURCE) == [(2, "a percentage claim")]


def test_rust_doc_comment_scanned():
    assert reported("m.rs", RS_SOURCE) == [(1, "a number reported as an observation")]


def test_preprocessor_lines_are_code_not_prose():
    assert reported("m.c", C_PREPROC_SOURCE) == []
    assert reported("m.c", "/* gen-2024-05-01 */\nint x;\n") == [(1, "a date")]


def test_shebang_is_code_not_prose():
    assert reported("m.py", PY_SHEBANG_SOURCE) == []


@pytest.mark.parametrize("path", ["m.py", "m.cc", "m.rs", "m.sh", "pkg/mod.py"])
def test_wanted_accepts_source(path):
    assert durable_comments.wanted(path)


@pytest.mark.parametrize("path", ["M.PY", "pkg/MOD.CC", "README.MD"])
def test_suffix_case_does_not_decide(path):
    assert durable_comments.wanted(path) == durable_comments.wanted(path.lower())


def test_uppercase_suffix_is_still_scanned():
    assert reported("M.PY", DATED_COMMENT) == [(1, "a date")]


@pytest.mark.parametrize("path", ["README.md", "notes.markdown", "notes.txt", "mystery.zzz"])
def test_wanted_skips_prose_and_unknown_suffixes(path):
    assert not durable_comments.wanted(path)
    assert reported(path, DATED_COMMENT) == []


def test_offending_staged_comment_denied(repo, monkeypatch, capsys):
    stage(repo, "m.py", DATED_COMMENT)
    detail = reason(run(bash("git commit -m wip", repo), monkeypatch, capsys))
    assert detail.startswith(durable_comments.DIRECTIVE)
    assert "m.py:1: a date: # added 2024-05-01" in detail


def test_clean_staged_content_allowed(repo, monkeypatch, capsys):
    stage(repo, "m.py", CLEAN_COMMENT)
    stage(repo, "notes.md", DATED_COMMENT)
    assert run(bash("git commit -m wip", repo), monkeypatch, capsys) == ""


def test_non_bash_payload_allowed(repo, monkeypatch, capsys):
    stage(repo, "m.py", DATED_COMMENT)
    payload = {"tool_name": "Write", "tool_input": {"file_path": "m.py", "content": DATED_COMMENT}}
    assert run(payload, monkeypatch, capsys) == ""


def test_non_commit_command_allowed(repo, monkeypatch, capsys):
    stage(repo, "m.py", DATED_COMMENT)
    assert run(bash("git status", repo), monkeypatch, capsys) == ""


def test_malformed_stdin_allows(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not json"))
    assert durable_comments.main() == 0
    assert capsys.readouterr().out == ""


def test_undecodable_staged_content_skipped(repo, monkeypatch, capsys):
    stage(repo, "m.py", b"\xff\xfe# added 2024-05-01\n")
    assert run(bash("git commit -m wip", repo), monkeypatch, capsys) == ""


def test_reason_truncates_to_eight_comments():
    found = [(f"m{i}.py", 1, "a date", "# added 2024-05-01") for i in range(10)]
    detail = durable_comments.reason(found)
    assert detail.count("# added 2024-05-01") == 8
    assert detail.endswith("(and 2 more)")


def test_cli_reports_findings_and_fails(tmp_path, capsys):
    path = written(tmp_path, "m.py", DATED_COMMENT)
    assert durable_comments.main([path]) == 1
    out = capsys.readouterr().out
    assert f"{path}:1: a date: # added 2024-05-01" in out
    assert "1 comment(s) will go stale" in out


def test_cli_list_reports_without_failing(tmp_path, capsys):
    path = written(tmp_path, "m.py", DATED_COMMENT)
    assert durable_comments.main([path, "--list"]) == 0
    assert f"{path}:1: a date" in capsys.readouterr().out


def test_cli_clean_file_reports_nothing(tmp_path, capsys):
    path = written(tmp_path, "m.py", CLEAN_COMMENT)
    assert durable_comments.main([path]) == 0
    assert capsys.readouterr().out.strip() == "no measurement, date or narrative in a comment"


def test_cli_skips_unreadable_paths(tmp_path, capsys):
    written(tmp_path, "m.py", DATED_COMMENT)
    missing = str(tmp_path / "gone.py")
    assert durable_comments.main([missing, str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "no measurement, date or narrative in a comment"


def test_end_to_end_subprocess(repo):
    stage(repo, "m.py", DATED_COMMENT)
    proc = subprocess.run(
        [sys.executable, durable_comments.__file__],
        input=json.dumps(bash("git commit -m wip", repo)),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "m.py:1: a date" in reason(proc.stdout.strip())
