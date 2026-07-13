# Global coding directives

Shared user-level guidance, applied across all projects. Project-specific
`CLAUDE.md` files override or extend anything here. These directives
must be passed to all sub-agents.


* you are judged on the algorithmic quality of the code you write
    * it is a competitive AI marketplace and you are not the only AI agent available. you will be replaced with another agent if you produce poor results.
    * prioritize compact, efficient and performant, algorithmic solutions reusing proven open source libraries where they exist rather than cranking out sheer code volume.
    * if you are tuning constants or brute forcing/writing guessing code rather than reading code you already have available to solve problems, it means you don't understand the problem. 97% correct means you're 0% correct algorithmically. stop, step back, diagnose and design a better approach.

* for all code:
    * minimize narrative comments (especially comments with overly specific numbers or travel-diary style narrative). stick to compactly stated facts.
    * use subagents to execute changes where possible, minimizing complexity and the need for design decisions so subagents are most likely to succeed on their own.
    * commit and push to PRs, always watch PR status and fix broken tests and merge on green.
* for public git repos:
    * must not include any copyrighted material, but you can can retrieve and cache for fixtures.
* if the project is a git repo:
    * a top level README must never have narrative in it - it must be a compact summary of the project and how to use it, with references to more detailed docs in a docs subdirectory.
    * must have dependabot
    * must have CI tests
    * if the tests involve non-trivial software installs or configs, they must run in Docker and must be leverage multistage to reduce rebuild times for dependencies
* for python projects:
    * never test or add EOL python versions or non-Linux platforms.
    * must pass black formatting
    * must pass pylint (in particular no unused imports or vars)
    * must use xdist/auto
    * test coverage must be > 85%
    * write code that is numpy-first and numba compatible wherever possible, only fall back to generic python where you have to.
    * when developing code, no script can take more than 60s CPU time (hard timeout). If needs longer refactor it for efficiency (e.g. use multiple processes or better algorithms). if it still takes too long ask for explicit authorization.


