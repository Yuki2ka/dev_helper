"""
any free Kilo model, the way Kilo calls it: Kilo Gateway
OpenAI-compatible endpoint with the same headers Kilo sets, via the official
OpenAI Python SDK, plus a live selector that lists every free model currently
available in Kilo.

Kilo builds its model catalog from GET https://api.kilo.ai/api/gateway/models
(packages/core/src/models-dev.ts) and flags free models with `isFree`. We reuse
that exact endpoint to discover the free models, then stream from the chosen one.

From packages/core/src/plugin/provider/kilo.ts:
  api.url  = KILO_OPENROUTER_BASE        -> https://api.kilo.ai/api/gateway
  request.headers["HTTP-Referer"] = "https://kilo.ai/"
  request.headers["X-Title"]      = "Kilo Code"
  auth = KILO_API_KEY / kilocodeToken   (Bearer; free tier works anonymously)

Requires: pip install openai requests   ( only for the /models selector)

Run:
    no arg =  interactive selector
    just print free models: --list
    KILO_MODEL=tencent/hy3:free   # skip selector
"""

import os
import sys
import requests
from openai import OpenAI

BASE_URL = os.environ.get("KILO_GATEWAY_URL", "https://api.kilo.ai/api/gateway")
HEADERS = {"HTTP-Referer": "https://kilo.ai/", "X-Title": "Kilo Code"}


def free_models() -> list[str]:
    """Fetch the live list of free models from the Kilo Gateway (no auth needed)."""
    resp = requests.get(f"{BASE_URL.rstrip('/')}/models", headers=HEADERS)
    resp.raise_for_status()
    return [m["id"] for m in resp.json()["data"] if m.get("isFree")]


def choose_model() -> str:
    if os.environ.get("KILO_MODEL"):
        return os.environ["KILO_MODEL"]
    if "--list" in sys.argv:
        for mid in free_models():
            print(mid)
        sys.exit(0)
    models = free_models()
    print("Free models available in Kilo:")
    for i, mid in enumerate(models):
        print(f"  [{i}] {mid}")
    pick = input("Select a model number (default 0): ").strip()
    idx = int(pick) if pick else 0
    return models[idx]


def stream(prompt: str, model: str) -> None:
    client = OpenAI(
        api_key=os.environ.get("KILO_API_KEY", "anonymous"),
        base_url=BASE_URL,
        default_headers=HEADERS,
    )
    chunks = client.chat.completions.create(
        model=model,
        stream=True,
        messages=[{"role": "user", "content": prompt}],
    )
    for chunk in chunks:
        tok = chunk.choices[0].delta.content
        if tok:
            print(tok, end="", flush=True)
    print()


if __name__ == "__main__":
    stream("what is your model name?", choose_model())
