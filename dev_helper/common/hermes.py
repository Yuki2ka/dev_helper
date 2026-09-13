"""
Hermes Agent (Nous Research, MIT) self-installing bootstrap, shared between
``LLM_chat_hermes.py`` and ``new_file_from_LLM.py``.

Importing this module does NOT touch the network or create venvs - the heavy
bootstrap only happens when ``ensure_hermes()`` is called. That matters because
``ensure_hermes()`` may ``os.execv`` into a freshly-built venv, which replaces
the process: any state computed before the call is lost. Callers must therefore
resolve their settings (HERMES / PROVIDER / ONLINE) *before* bootstrapping.
"""

from __future__ import annotations

import os
import sys
import pathlib
import subprocess


def _hermes_importable(python=None) -> bool:
    """True if ``run_agent`` (Hermes) can be imported.

    With no argument, checks the *current* interpreter. Pass a path to probe a
    different Python (e.g. the venv) in a subprocess.
    """
    if python is None or os.path.samefile(python, sys.executable):
        import importlib.util
        return importlib.util.find_spec("run_agent") is not None
    return subprocess.run(
        # this script also needs `requests`, so require both to be importable
        [str(python), "-c", "import run_agent, requests"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _external_hermes_pythons():
    """Interpreters from known external Hermes installs (e.g. the desktop app).

    The official installer drops a full, working venv under the Hermes home -
    ``%LOCALAPPDATA%\\hermes`` on Windows, ``~/.hermes`` on POSIX (Linux/macOS),
    overridable via the ``HERMES_HOME`` env var. Reusing it skips cloning /
    building entirely - a big win on a RAM disk where git checkouts are
    unreliable.
    """
    homes = []
    env_home = os.environ.get("HERMES_HOME", "").strip()
    if env_home:
        homes.append(pathlib.Path(env_home))
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            homes.append(pathlib.Path(base) / "hermes")
        homes.append(pathlib.Path.home() / "AppData" / "Local" / "hermes")
    else:
        homes.append(pathlib.Path.home() / ".hermes")

    cands = []
    for home in homes:
        venv = home / "hermes-agent" / "venv"
        cands.append(venv / "Scripts" / "python.exe")  # Windows layout
        cands.append(venv / "bin" / "python")          # POSIX layout
    # de-dupe while preserving order (normcase respects case-sensitivity)
    seen, unique = set(), []
    for c in cands:
        key = os.path.normcase(str(c))
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _ensure_hermes(script_file: str) -> type:
    """Return the AIAgent class, preferring existing installs before building a venv.

    Resolution order:
      1. Global / current interpreter: if Hermes is importable in the Python
         that launched this script, use it directly (no venv, no downloads).
      2. External install: reuse the official Hermes desktop-app venv
         (%LOCALAPPDATA%\\hermes\\...) if present - re-exec into it.
      3. Local venv at <script_dir>/.venv-hermes: create / repair it and re-exec.

    ``script_file`` is the path of the *calling* script. It is used as the
    re-exec target so the caller resumes inside the venv with the same argv.
    """
    # 1) Prefer an existing Hermes install in the current interpreter.
    if _hermes_importable():
        from run_agent import AIAgent
        return AIAgent

    # 2) Reuse an existing official Hermes install (desktop app) if present.
    for cand in _external_hermes_pythons():
        if cand.exists() and not os.path.samefile(cand, sys.executable) \
                and _hermes_importable(cand):
            print(f"[bootstrap] reusing existing Hermes install {cand}", flush=True)
            os.execv(str(cand), [str(cand), script_file, *sys.argv[1:]])

    repo = pathlib.Path(script_file).resolve().parent
    venv = repo / ".venv-hermes"
    vpy = (venv / "Scripts" / "python.exe") if os.name == "nt" else (venv / "bin" / "python")

    # If we're already running inside the venv and the import above still
    # failed, the install is genuinely broken - don't loop re-execing.
    if vpy.exists() and os.path.samefile(sys.executable, vpy):
        raise RuntimeError(
            "Hermes (run_agent) still not importable from the venv at "
            f"{venv}. The install likely failed - check the pip output above."
        )

    print("[bootstrap] Hermes not installed globally - falling back to local venv")

    # Create the venv only if it doesn't exist yet; otherwise reuse it. We
    # (re)install into it below regardless, so a venv left behind by a
    # previously-failed install gets repaired instead of causing an endless
    # re-exec loop.
    if not vpy.exists():
        print(f"[bootstrap] Hermes not found - creating venv at {venv}")
        if subprocess.run([sys.executable, "-m", "uv", "venv", str(venv)]).returncode == 0:
            installer = [sys.executable, "-m", "uv", "pip", "install", "--python", str(vpy)]
        else:
            subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
            subprocess.run([str(vpy), "-m", "pip", "install", "--upgrade", "pip"], check=True)
            installer = [str(vpy), "-m", "pip", "install"]
    else:
        # Venv exists: if Hermes is already installed in it, just re-exec;
        # otherwise repair the install.
        if _hermes_importable(vpy):
            # flush: os.execv replaces the process without flushing stdio
            print(f"[bootstrap] re-executing with existing venv {vpy}", flush=True)
            os.execv(str(vpy), [str(vpy), script_file, *sys.argv[1:]])
        print(f"[bootstrap] reusing venv at {venv} (repairing install)")
        if subprocess.run([sys.executable, "-m", "uv", "--version"]).returncode == 0:
            installer = [sys.executable, "-m", "uv", "pip", "install", "--python", str(vpy)]
        else:
            installer = [str(vpy), "-m", "pip", "install"]

    # pip's `git+https://` clone fails to check out on some filesystems
    # (e.g. RAM disks / mapped Z: drives) with "must be run in a work
    # tree". Clone locally and install from the path instead.
    #
    # On such filesystems git's *automatic* work-tree discovery is broken:
    # a normal `git clone` (or a later `git -C <dir> checkout`) either
    # aborts with "fatal: this operation must be run in a work tree" or
    # silently checks out nothing, leaving an empty working tree that git
    # still reports as "clean". Passing GIT_DIR and GIT_WORK_TREE
    # explicitly bypasses the broken discovery and forces a real checkout
    # onto disk, so this works without an SSD / regular drive.
    src = repo / ".hermes-src"
    if not (src / ".git").exists():
        print(f"[bootstrap] cloning hermes-agent into {src} ...")
        subprocess.run(
            ["git", "clone", "--no-checkout",
             "https://github.com/NousResearch/hermes-agent.git", str(src)],
            check=True,
        )

    git_env = {
        **os.environ,
        "GIT_DIR": str(src / ".git"),
        "GIT_WORK_TREE": str(src),
    }
    print("[bootstrap] checking out hermes-agent working tree ...")
    subprocess.run(
        ["git", "checkout", "-f", "origin/HEAD"],
        check=True, env=git_env,
    )
    print("[bootstrap] installing NousResearch/hermes-agent ...")
    subprocess.run([*installer, str(src)], check=True)

    print(f"[bootstrap] re-executing with {vpy}", flush=True)
    os.execv(str(vpy), [str(vpy), script_file, *sys.argv[1:]])


def ensure_hermes(script_file: "str | None" = None) -> type:
    """Lazy bootstrap entry point. Returns the AIAgent class.

    May ``os.execv`` into a venv first (replacing the process). After that the
    caller resumes at this same call with Hermes importable.

    ``script_file``: path of the calling script, used as the re-exec target.
    Defaults to ``sys.argv[0]`` (the originally-invoked script), which is the
    right target because ``os.execv`` preserves argv.
    """
    return _ensure_hermes(script_file or sys.argv[0])
