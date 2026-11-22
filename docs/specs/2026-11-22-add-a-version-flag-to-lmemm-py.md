# `--version` flag for lmemm.py

**Date:** 22 November 2026 · **Branch:** `factory/issue-70` · **Request:** #70

## Goal

`python3 lmemm.py --version` prints the app name and version, `lmemm.py 0.1.0`, and exits with status 0.
The version string is defined once in the code. This is a small change that touches real code and tests, and it
serves as the first end-to-end run of the software factory.

## Behaviour

- `python3 lmemm.py --version` prints exactly one line, `lmemm.py 0.1.0`, to stdout. Nothing goes to stderr and the
  exit status is 0.
- The flag only prints. It doesn't start the tracker, run first-run setup, open the menu, touch the data directory or
  talk to a running tracker.
- The flag only counts as the **first** argument. Anything after it is ignored, so `lmemm.py --version --every 5`
  still just prints the version.
- Nothing else changes. `lmemm.py` with no arguments still opens the menu. Other leading flags (`--every 5`,
  `--no-widget`) still go to `start`. An unknown command still exits with `USAGE`. `start --version` and similar
  forms are not handled specially and behave as they do today.
- This change adds no empty or error states: the version is a constant, so the flag can't fail.

## Design

All of the change is in `lmemm.py`, which is the one command and already holds `USAGE` and the dispatch.

- Add a module-level constant `__version__ = "0.1.0"` just below the imports, next to `USAGE`. This is the only
  place the version number is written.
- In `main()`, add a check right after the `if not args:` block and **before**
  `cmd = args[0] if not args[0].startswith("-") else "start"`:
  `if args[0] == "--version": print(f"lmemm.py {__version__}"); return`.
  The check has to come before that line because the dispatch sends every leading `-…` argument to `cmd_start`, and
  `cmd_start` can trigger first-run setup and the tracker. Returning normally from `main()` gives exit status 0 under
  `if __name__ == "__main__":`.
- Add `--version` to the usage text:
  - the module docstring: a line such as `lmemm.py --version              print the version and exit`
  - `USAGE`: add ` | --version` to the string.
- `README.md`: add one row to the command table, `` | `lmemm.py --version` | print the version and exit | ``.

## Acceptance criteria

1. `python3 lmemm.py --version`, run from the repo root, prints `lmemm.py 0.1.0` followed by a newline, and nothing
   else, to stdout. `echo $?` then prints `0`.
2. The version number `0.1.0` appears exactly once in the code, as `__version__` in `lmemm.py`. The printed line is
   built from that constant: changing the constant changes the output (the test patches it to check this).
3. `--version` doesn't call `cmd_start`, `cmd_setup`, `interactive_menu` or anything in `tracker`.
4. `--version` followed by other arguments still prints only the version line.
5. Existing dispatch is unchanged. A leading flag such as `--every 5` still goes to `cmd_start`, and an unknown
   command still raises `SystemExit` with `USAGE`.
6. The `lmemm.py` docstring, `USAGE` and the README command table all mention `--version`.
7. `./scripts/test.sh` passes, including the new tests.

## Tests

New file `tests/test_lmemm_version.py`. It follows the CLI pattern in `tests/test_context.py`
(`patch("sys.argv", …)`, `contextlib.redirect_stdout`, then `lmemm.main()`):

- `test_version_prints_name_and_version`: with argv `["lmemm.py", "--version"]`, stdout is exactly
  `f"lmemm.py {lmemm.__version__}\n"` and `main()` returns without raising, or raises `SystemExit` with code 0 or
  `None`.
- `test_version_is_built_from_the_constant`: patch `lmemm.__version__` to `"9.9.9"`, and the output is
  `lmemm.py 9.9.9\n`.
- `test_version_constant_value`: `lmemm.__version__ == "0.1.0"`.
- `test_version_does_not_start_anything`: patch `lmemm.cmd_start`, `lmemm.cmd_setup` and `lmemm.interactive_menu`
  with mocks, run `--version` and `--version --every 5`, and assert that none of the mocks were called.
- `test_other_leading_flags_still_go_to_start`: patch `lmemm.cmd_start`, and with argv
  `["lmemm.py", "--every", "5"]` it is called once with `["--every", "5"]`.
- `test_usage_mentions_version`: `"--version"` appears in `lmemm.USAGE` and in `lmemm.__doc__`.

No existing tests change.

## Out of scope

- A short `-V` alias, or `--version` on subcommands (`start --version`, `trail --version`).
- `--version` for `trail_cli.py`, `run.sh` or other scripts.
- Packaging metadata (`pyproject.toml`, `setup.py`), reading the version from git tags, or a release or bump process.
- Showing the version in the menu, the window or `status`.

## Assumptions

- The version is `0.1.0`, as in the request's example. The repo has no version anywhere else.
- The printed name is the literal `lmemm.py`, as in the request, not `LMemM` and not `sys.argv[0]`. This keeps the
  output the same however the script is launched.
- The flag is a plain check in `main()`, not argparse's `action="version"`. `main()` dispatches by hand, and argparse
  only appears inside the subcommands.
- Exit status 0 comes from returning normally from `main()`. An explicit `sys.exit(0)` would also be fine, and the
  tests accept either.
