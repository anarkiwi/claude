#!/usr/bin/env python3
"""PreToolUse guard blocking writes of self-admitted guessing code.

Enforces the global directive: do not tune constants, brute-force, or write
guessing code instead of reading source that is already available. A hook
cannot prove code is algorithmic, but agents reliably confess guessing in
comments ("magic number", "tuned until tests pass", "empirically chosen") and
in tune-to-green idioms. Those admissions are high-precision tells; this guard
denies the write and tells the agent to derive/justify the value from source.

Reads the Claude Code PreToolUse hook payload on stdin and, when a tell is
found in the *added* text of a code file, emits a deny decision on stdout.
Silence (exit 0, no output) allows the write.
"""

import json
import re
import sys

# Files whose added text is scanned. Prose (md/rst/txt) and data (json/yaml)
# are excluded: admission phrases legitimately appear there as content.
CODE_SUFFIXES = frozenset("""py pyx pyi c h cc cpp cxx hpp js jsx mjs cjs ts tsx go rs java kt kts
    scala swift rb sh bash zsh sql jl lua r php pl pm m mm""".split())

# Inherently admissions: safe to match anywhere, they do not occur in real
# identifiers. Each names guessing, constant-fiddling, or tuning to green.
STRONG = [
    r"magic\s+(?:number|constant|value)",
    r"fudge\s*factor",
    r"trial[\s-]and[\s-]error",
    r"finger[s]?\s+crossed",
    r"guess[\s-]?timate",
    r"\bno\s+idea\s+why\b",
    r"\bnot\s+sure\s+why\b",
    r"random(?:ly)?\s+(?:chosen|picked|guess)",
    r"arbitrar(?:y|ily)\s+(?:chosen|value|number|constant|threshold)",
    r"empirical(?:ly)?\s+(?:tuned|determined|derived|chosen|found|set)",
    r"tuned?\s+(?:this\s+|the\s+)?(?:constant|value|threshold|param(?:eter)?s?|weight)",
    r"(?:tweak|adjust|bump|nudge)(?:ed|ing)?\s+(?:this|the|until)",
    r"until\s+(?:it|the\s+test[s]?|they|tests?)\s+(?:work|pass|green)",
    r"(?:to\s+)?make\s+(?:the\s+)?test[s]?\s+pass",
    r"hack(?:y|ish)?\s+(?:value|constant|threshold|fix|number)",
]

# Weaker tells: only credible inside a comment, else they collide with real
# code ("guess_count", a docstring narrating a heuristic, etc.).
COMMENT_ONLY = [
    r"\bguess(?:ed|ing|work)?\b",
    r"\bseems?\s+to\s+work\b",
    r"\bshould\s+(?:be\s+enough|work|do\s+it)\b",
    r"\bgood\s+enough\b",
    r"\bclose\s+enough\b",
]

STRONG_RE = re.compile("|".join(STRONG), re.IGNORECASE)
COMMENT_ONLY_RE = re.compile("|".join(COMMENT_ONLY), re.IGNORECASE)
# Text after a line/inline comment opener in the common languages scanned.
COMMENT_RE = re.compile(r"(?:#|//|/\*|<!--)\s*(.*)$")

DIRECTIVE = (
    "Blocked: this write admits guessing (tuning constants / brute-forcing / "
    "writing code you hope works) instead of reading source you already have. "
    "Per directive, 97% correct means 0% correct algorithmically. Stop, read "
    "the code that defines the required behavior, and derive the value. If a "
    "constant is genuinely required, name it and document its derivation "
    "(cite the source), removing the guessing language."
)


def added_texts(tool_name, tool_input):
    """Yield (label, text) pairs for the text this call would add."""
    if tool_name == "Write":
        yield tool_input.get("file_path", ""), tool_input.get("content", "")
    elif tool_name == "Edit":
        yield tool_input.get("file_path", ""), tool_input.get("new_string", "")
    elif tool_name == "MultiEdit":
        path = tool_input.get("file_path", "")
        for i, edit in enumerate(tool_input.get("edits", [])):
            yield f"{path}#{i}", edit.get("new_string", "")
    elif tool_name == "NotebookEdit":
        yield tool_input.get("notebook_path", ""), tool_input.get("new_source", "")


def is_code(path):
    suffix = path.rsplit("#", 1)[0].rsplit(".", 1)
    return path.endswith(".ipynb") or (len(suffix) == 2 and suffix[1].lower() in CODE_SUFFIXES)


def find_tells(text):
    """Return [(lineno, matched_phrase)] for guessing admissions in text."""
    tells = []
    for lineno, line in enumerate(text.splitlines(), 1):
        strong = STRONG_RE.search(line)
        if strong:
            tells.append((lineno, strong.group(0).strip()))
            continue
        comment = COMMENT_RE.search(line)
        if comment:
            weak = COMMENT_ONLY_RE.search(comment.group(1))
            if weak:
                tells.append((lineno, weak.group(0).strip()))
    return tells


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    found = []
    for label, text in added_texts(tool_name, tool_input):
        if is_code(label):
            found += [(label, ln, phrase) for ln, phrase in find_tells(text)]
    if not found:
        return 0

    detail = "; ".join(f"{lbl} line {ln}: '{ph}'" for lbl, ln, ph in found[:8])
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"{DIRECTIVE}\nTells: {detail}",
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
