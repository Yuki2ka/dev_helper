"""
Hermes Agent (Nous Research, MIT) driving a free Kilo model - or any local
OpenAI-compatible server (LM Studio / Ollama / llama.cpp).

Hermes Agent is a model-agnostic autonomous agent. We point its `AIAgent` core
at the chosen backend (OpenAI-compatible) so the agent runs on a local model or
a free model such as `tencent/hy3:free`, then drop into an interactive console
chat.

Provider selection uses dev_helper.common.providers (shared with
new_file_from_LLM.py):
  - Offline providers are probed in parallel (LM Studio / Ollama / llama.cpp).
  - Online providers come from the Kilo Gateway (OpenAI-compatible, free models,
    no auth needed) and are listed under a "------ online ------" separator.
  - Settings: PROVIDER, ONLINE, STREAM, etc. (see SETTINGS START/END below).

Run:
    no arg =  interactive provider selector
    --list  =  print free online models and exit
    PROVIDER=lmstudio            # skip selector, use this local provider
    KILO_MODEL=tencent/hy3:free  # skip selector, use this online model
"""

import os
import json
import sys
import pathlib

from openai import OpenAI

# === SETTINGS START ===
# Stream tokens to the console (Hermes streams internally; this only affects
# whether we forward its streamed output verbatim). Kept for parity with the
# other LLM scripts.
STREAM = True

# Startup provider selection
# - a concrete name ("lmstudio", "ollama", "llamacpp") -> probe & use it; on
#   failure, fall back to the interactive selector
# - "online" -> use the Kilo Gateway (first free model)
# - "auto"   -> first responder in AVAILABLE_PROVIDERS_CONFIG order, else online
# - "ask" / None / any other value -> show the unified selector
PROVIDER = "ask"

# Online providers (Kilo Gateway, OpenAI-compatible)
# - true   -> include online providers (no prompt)
# - false  -> never make any internet connection
# - "ask" / None / any other value -> ask the user once
ONLINE = "ask"
KILO_GATEWAY_URL = "https://api.kilo.ai/api/gateway"
KILO_HEADERS = {"HTTP-Referer": "https://kilo.ai/", "X-Title": "Kilo Code"}
# KILO_API_KEY is assigned after the import section (needs os.environ)

HERMES = "ask" # "ask" True False

# Hermes agent tuning
SKIP_MEMORY = False        # False => memory/session DB ON (naming is inverted)
MAX_ITERATIONS = 24
QUIET_MODE = True          # we print the reply ourselves
# === SETTINGS END ===


# ==================================================
#  Imports and shared setup
#  (Hermes bootstrap happens in main() after HERMES is resolved)
# ==================================================

try:
    import requests
except ImportError:
    requests = None

# Make `dev_helper` importable when run directly from the dev tree (the
# standalone build inlines dev_helper.common.* via stickytape, so this is only
# needed for in-tree execution).
_resolved = pathlib.Path(__file__).resolve()
_PROJECT_ROOT = _resolved.parents[2] if len(_resolved.parents) >= 3 else None
if _PROJECT_ROOT is not None and str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# KILO_API_KEY depends on os.environ, which is only available after imports
KILO_API_KEY = os.environ.get("KILO_API_KEY", "anonymous")

from dev_helper.common.hermes import ensure_hermes


# ==================================================
#  Provider detection / selection
#  (shared implementation: dev_helper.common.providers)
# ==================================================
from dev_helper.common.providers import (
    ProviderConfig,
    resolve_provider as _resolve_provider,
    get_online_models,
)

# Bridge the script's SETTINGS into the shared config (single source of truth).
_PROVIDER_CFG = ProviderConfig(
    provider=PROVIDER,
    online=ONLINE,
    kilo_gateway_url=KILO_GATEWAY_URL,
    kilo_headers=KILO_HEADERS,
    kilo_api_key=KILO_API_KEY,
)


def resolve_provider(cli_provider=None):
    """Thin wrapper so the rest of this module keeps calling resolve_provider()."""
    return _resolve_provider(_PROVIDER_CFG, cli_provider)


def _send_to_llm(messages, base_url, api_key, model):
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=KILO_HEADERS,
    )
    chunks = client.chat.completions.create(
        model=model,
        stream=True,
        messages=messages,
    )
    full = []
    for chunk in chunks:
        tok = chunk.choices[0].delta.content
        if tok:
            print(tok, end="", flush=True)
            full.append(tok)
    print()
    return {"role": "assistant", "content": "".join(full)}


# ==================================================
#  Chat loop
# ==================================================
def main() -> None:
    global HERMES
    if "--list" in sys.argv:
        for mid in get_online_models(_PROVIDER_CFG):
            print(mid)
        return

    # Decide Hermes BEFORE any heavy work. ensure_hermes() may os.execv,
    # which replaces the process and discards state computed before it - so
    # the provider/Hermes decisions must be made first and survive the
    # re-exec.
    if HERMES is True or (isinstance(HERMES, str) and HERMES.lower() == "true"):
        use_hermes = True
    elif HERMES is False or (isinstance(HERMES, str) and HERMES.lower() == "false"):
        use_hermes = False
    else:
        cached = os.environ.get("DEV_HELPER_HERMES")
        if cached is not None:
            use_hermes = cached.lower() in ("1", "true", "yes")
        else:
            print("\n[?] Route this run through the Hermes autonomous agent?")
            print("  [*] Yes (Hermes AIAgent drives the provider)")
            print("  [2] No  (direct HTTP to the provider, as usual)")
            resp = input("Select: ").strip()
            use_hermes = resp != "2"

    HERMES = use_hermes

    # KILO_MODEL env (or analogous arg) selects an explicit online model
    selected = resolve_provider(os.environ.get("KILO_MODEL"))
    if not selected:
        print("[-] No provider selected. Exiting.")
        return
    ptype, base, model = selected
    if not model:
        print("[-] Selected provider has no model loaded. Exiting.")
        return

    # OpenAI-compatible endpoint
    hermes_base = base.rstrip("/") + "/v1"
    api_key = KILO_API_KEY if ptype == "online" else "lm-studio"

    if use_hermes:
        AIAgent = ensure_hermes(script_file=__file__)
        agent = AIAgent(
            base_url=hermes_base,
            api_key=api_key,
            model=model,
            skip_memory=SKIP_MEMORY,
            max_iterations=MAX_ITERATIONS,
            quiet_mode=QUIET_MODE,
        )
        print(f"\nHermes agent ready on `{model}` ({ptype}). Type 'exit' or 'quit' to leave.\n")
        history = None
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user or user.lower() in ("exit", "quit"):
                break
            result = agent.run_conversation(user, conversation_history=history)
            history = result.get("messages")
            print(f"\nhermes> {result.get('final_response', '')}")
    else:
        print(f"\nDirect HTTP mode on `{model}` ({ptype}). Type 'exit' or 'quit' to leave.\n")
        history = None
        while True:
            try:
                user = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not user or user.lower() in ("exit", "quit"):
                break
            if history is None:
                messages = [{"role": "user", "content": user}]
            else:
                messages = history + [{"role": "user", "content": user}]
            reply = _send_to_llm(messages, hermes_base, api_key, model)
            history = messages + [reply]
            print(f"\n> {reply.get('content', '')}")


if __name__ == "__main__":
    main()
