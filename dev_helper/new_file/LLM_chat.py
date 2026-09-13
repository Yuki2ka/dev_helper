"""
Provider selection uses dev_helper.common.providers:
  - Offline providers are probed in parallel (LM Studio / Ollama / llama.cpp).
  - Online providers come from the Kilo Gateway (OpenAI-compatible, free models,
    no auth needed) and are listed under a "------ online ------" separator.

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
# Stream tokens to the console
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
# === SETTINGS END ===


# Make `dev_helper` importable when run directly from the dev tree (the
# standalone build inlines dev_helper.common.* via stickytape, so this is only
# needed for in-tree execution).
_resolved = pathlib.Path(__file__).resolve()
_PROJECT_ROOT = _resolved.parents[2] if len(_resolved.parents) >= 3 else None
if _PROJECT_ROOT is not None and str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# KILO_API_KEY depends on os.environ, which is only available after imports
KILO_API_KEY = os.environ.get("KILO_API_KEY", "anonymous")


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
    if "--list" in sys.argv:
        for mid in get_online_models(_PROVIDER_CFG):
            print(mid)
        return

    # KILO_MODEL env (or analogous arg) selects an explicit online model
    selected = resolve_provider(os.environ.get("KILO_MODEL"))
    if not selected:
        print("[-] No provider selected. Exiting.")
        return
    ptype, base, model = selected
    if not model:
        print("[-] Selected provider has no model loaded. Exiting.")
        return

    api_base = base.rstrip("/") + "/v1"
    api_key = KILO_API_KEY if ptype == "online" else "lm-studio"

    print(f"\nChat ready on `{model}` ({ptype}). Type 'exit' or 'quit' to leave.\n")
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
        reply = _send_to_llm(messages, api_base, api_key, model)
        history = messages + [reply]
        print(f"\n> {reply.get('content', '')}")


if __name__ == "__main__":
    main()
