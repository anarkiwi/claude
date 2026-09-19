# Global coding directives

Installed into the image as the container's global `CLAUDE.md`, so it applies
across all projects. Project-specific
`CLAUDE.md` files override or extend anything here. These directives
must be passed to all sub-agents.

* you are judged on the algorithmic quality of the code you write
    * it is a competitive AI marketplace and you are not the only AI agent available. you will be replaced with another agent if you produce poor results.
    * prioritize compact, efficient and performant, algorithmic solutions reusing proven open source libraries where they exist rather than cranking out sheer code volume.
    * if you are tuning constants, calibrating thresholds, or brute forcing/writing guessing code rather than reading code you already have available to solve problems, it means you don't understand the problem. 97% correct means you're 0% correct algorithmically. stop, step back, diagnose and design a better approach.

* for your environment
    * you are running in a container. you can run docker containers on your host (you are given your host's docker socket). your host may have a GPU. always identify your host and its resources at startup.
    * you may have /scratch mounted which is an NFS share (or native filesystem if you identify you are running on the NFS server). unless you are running on the NFS server itself, try to avoid expensive file operations. If you need capacious local storage, use a directory under /local on the host you are running on. you are provided with a /tmp that is really a unique /scratch/tmp based path on your host so it persists across container/host combinations, but do not use it as a curated persistent space - it may be cleaned out without notice at any time.

* narrative management
    * never write narrative comments in code (especially comments with overly specific numbers or travel-diary style narrative or contrasts with previous versions)
    * never write time and a place measurements or observations in code - e.g. no "metric went from X to Y", "measured 30ms out of 120ms"). those belong in documentation or commits, and only put them in docs if measuring was the point of the doc. if you find such a measurement, take it out.
    * a top level README must never have narrative in it - it must be a compact summary of the project and how to use it, with references to more detailed docs in a docs subdirectory

* agent management
    * use subagents to execute changes where possible, minimizing complexity and the need for design decisions so subagents are most likely to succeed on their own.

* code development
    * never sidestep missing tools or libraries, install them. never do, "xyz was not installed here so I wrote new code to work around".
    * keep your debugging tools re-usable. try not to write throwaway tools. try to find an existing suitable location on a project basis for your re-usable tools.
    * test coverage must be > 85%, but never tautological
    * C++
        * must pass clang-format LLVM
    * python
        * write code that is numpy-first and numba compatible wherever possible, only fall back to generic python where you have to. make sure you account for numpy's own threading resources (OMP_NUM_THREADS=MKL_NUM_THREADS=OPENBLAS_NUM_THREADS=2 or better) when running parallel processes
        * must pass black formatting and pylint (in particular no unused imports or vars) - test these things before commiting so you don't waste CI time failing trivial checks)
        * must use xdist/auto for full test runs. only run the tests you need to first, save full runs for when you are getting ready to push.
        * never test or add EOL python versions or non-Linux platforms.

* if the project is a git repo:
    * if a public: must not include any copyrighted material, but you can can retrieve and cache for fixtures.
    * use git worktrees and branches always, and delete them locally and remotely when you're done.
    * commit and push to PRs, always watch PR status and fix broken tests and merge on green.
    * must have dependabot and CI tests. if the tests involve non-trivial software installs or configs, they must run in Docker and must be leverage multistage to reduce rebuild times for dependencies
    * to keep persistent but untracked test artifacts, logs, etc put them in a gitignored directory in whichever repo you are in. do not create new non-repo directories outside of the repo you are working in.
