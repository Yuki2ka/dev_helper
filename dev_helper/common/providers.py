"""
Shared LLM provider detection & selection.

Supports local OpenAI-compatible servers (LM Studio / Ollama / llama.cpp) and
the online Kilo Gateway (free models). Used by new_file_from_LLM.py and
LLM_chat_hermes.py so the provider logic lives in exactly one place.

Build note: build.py (via stickytape) inlines ``dev_helper.common.*`` into each
standalone bundle (add_python_paths includes the project root), so importing
from here works in both the dev tree and the single-file bundle.
"""

from __future__ import annotations

import json
import concurrent.futures
import urllib.request
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

try:
    import requests
except ImportError:
    requests = None

DEFAULT_PROVIDERS: List[Tuple[str, str, str]] = [
    ("lmstudio", "http://127.0.0.1:1234", "/v1/models"),
    ("ollama", "http://127.0.0.1:11434", "/api/tags"),
    ("llamacpp", "http://127.0.0.1:8080", "/v1/models"),
]
DEFAULT_KILO_GATEWAY_URL = "https://api.kilo.ai/api/gateway"
DEFAULT_KILO_HEADERS = {"HTTP-Referer": "https://kilo.ai/", "X-Title": "Kilo Code"}

# Module-level cache so the gateway is only queried once per process.
_online_models_cache: Optional[List[str]] = None


@dataclass
class ProviderConfig:
    """Single source of truth for provider selection.

    Scripts keep their own SETTINGS (PROVIDER, ONLINE, KILO_*, ...) and pass
    them in here. ``online`` is normalized to a bool on first resolution (the
    "ask" prompt), so there is never a separate duplicate decision flag.
    """

    providers: List[Tuple[str, str, str]] = field(
        default_factory=lambda: list(DEFAULT_PROVIDERS)
    )
    endpoint_timeout: float = 0.5
    provider: str = "ask"          # concrete name | "auto" | "online" | "ask"
    online: object = "ask"         # true | false | "ask" (normalized to bool)
    kilo_gateway_url: str = DEFAULT_KILO_GATEWAY_URL
    kilo_headers: dict = field(default_factory=lambda: dict(DEFAULT_KILO_HEADERS))
    kilo_api_key: str = "anonymous"

    def resolve_online(self) -> bool:
        """Decide whether to include online; normalizes ``self.online`` to bool."""
        setting = self.online
        if setting is True or (isinstance(setting, str) and str(setting).lower() == "true"):
            self.online = True
            return True
        if setting is False or (isinstance(setting, str) and str(setting).lower() == "false"):
            self.online = False
            return False
        print("\n[?] Include online providers (Kilo Gateway)?")
        print("  [*] Yes")
        print("  [2] No")
        resp = input("Select: ").strip()
        self.online = (resp != "2")
        return self.online


def check_endpoint(url: str, timeout: float = 0.5) -> bool:
    """Check if an endpoint is reachable (any HTTP response counts)."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status in (200, 201, 404, 405)
    except Exception:
        return False


def get_lmstudio_model(base: str) -> Optional[str]:
    """First already-loaded model from an OpenAI-compatible server."""
    try:
        url = f"{base}/v1/models"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
            models = result.get("data", [])
            if models:
                return models[0]["id"]
    except Exception:
        pass
    return None


def get_ollama_model(base: str) -> Optional[str]:
    """First model tag from an Ollama server."""
    try:
        url = f"{base}/api/tags"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            result = json.loads(response.read().decode("utf-8"))
            models = result.get("models", [])
            if models:
                return models[0]["name"]
    except Exception:
        pass
    return None


def get_online_models(cfg: ProviderConfig, force: bool = False) -> List[str]:
    """Free model ids from the Kilo Gateway (no auth). Cached. [] on failure."""
    global _online_models_cache
    if _online_models_cache is not None and not force:
        return _online_models_cache
    if requests is None:
        _online_models_cache = []
        return _online_models_cache
    try:
        resp = requests.get(
            f"{cfg.kilo_gateway_url.rstrip('/')}/models",
            headers=cfg.kilo_headers,
            timeout=10,
        )
        resp.raise_for_status()
        _online_models_cache = [
            m["id"] for m in resp.json().get("data", []) if m.get("isFree")
        ]
    except Exception:
        _online_models_cache = []
    return _online_models_cache


def get_model_for_provider(
    cfg: ProviderConfig, provider_type: str, base: str
) -> Optional[str]:
    if provider_type == "lmstudio":
        return get_lmstudio_model(base)
    elif provider_type == "ollama":
        return get_ollama_model(base)
    elif provider_type == "llamacpp":
        return get_lmstudio_model(base)
    elif provider_type == "online":
        models = get_online_models(cfg)
        return models[0] if models else None
    return None


def get_local_providers(cfg: ProviderConfig) -> List[Tuple[str, str]]:
    """Probe local providers in parallel; return reachable ones (config order)."""
    probes = [(name, base, f"{base}{path}") for name, base, path in cfg.providers]
    local_found: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max(1, len(probes))
    ) as executor:
        future_to_probe = {
            executor.submit(check_endpoint, url, cfg.endpoint_timeout): (name, base)
            for name, base, url in probes
        }
        for future in concurrent.futures.as_completed(future_to_probe):
            name, base = future_to_probe[future]
            try:
                if future.result():
                    local_found[name] = base
            except Exception:
                continue
    return [(name, base) for name, base, _ in cfg.providers if name in local_found]


def get_online_provider_items(cfg: ProviderConfig) -> List[Tuple[str, str, str]]:
    """Each free Kilo Gateway model as its own selectable entry."""
    if not cfg.resolve_online():
        return []
    return [("online", cfg.kilo_gateway_url, mid) for mid in get_online_models(cfg)]


def _provider_base(cfg: ProviderConfig, name: str) -> Optional[str]:
    """Configured base URL for a provider name, or None."""
    return next((base for n, base, _ in cfg.providers if n == name), None)


_VALID_SELECTORS = {"auto", "online", "ask"}


def select_provider(cfg: ProviderConfig) -> Optional[Tuple[str, str, Optional[str]]]:
    """Unified provider chooser.

    - local providers first (config order), each with its loaded model
    - a "------ online ------" separator
    - each free Kilo Gateway model as its own entry

    Auto-selects when there is exactly one usable choice.
    """
    local = [
        (name, base, get_model_for_provider(cfg, name, base))
        for name, base in get_local_providers(cfg)
    ]
    online = get_online_provider_items(cfg)
    enriched = list(local) + list(online)

    if not enriched:
        return None

    with_models = [it for it in enriched if it[2]]
    if len(enriched) == 1:
        return enriched[0]
    if len(with_models) == 1:
        return with_models[0]

    print("\n[?] Choose provider:")
    for i, (ptype, base, model) in enumerate(enriched, 1):
        if ptype == "online" and (i == 1 or enriched[i - 2][0] != "online"):
            print("  ------ online ------")
        marker = "[*]" if i == 1 else f"[{i}]"
        if ptype == "online":
            label = f"{model}"
        else:
            extra = f" — model: {model}" if model else " — (no model loaded)"
            label = f"{ptype.upper()} ({base}){extra}"
        print(f"  {marker} {label}")

    while True:
        resp = input("Select: ").strip()
        idx = 0 if (resp == "" or resp == "1") else int(resp) - 1
        if 0 <= idx < len(enriched):
            return enriched[idx]
        print(f"[-] Enter 1-{len(enriched)}")


def resolve_provider(
    cfg: ProviderConfig, cli_provider: Optional[str] = None
) -> Optional[Tuple[str, str, Optional[str]]]:
    """Resolve (ptype, base, model) per the PROVIDER/ONLINE settings.

    - An explicit model id (e.g. KILO_MODEL=tencent/hy3:free) -> use it online.
    - A concrete name from cfg.providers: probe & use it; on failure, selector.
    - "auto": first responder in config order (local first, then online).
    - "ask" / None / any other value: show the unified selector.
    """
    desired = cli_provider or cfg.provider
    if desired is None:
        desired = "ask"
    desired_l = desired.lower() if isinstance(desired, str) else "ask"

    known = _VALID_SELECTORS | {n for n, _, _ in cfg.providers}

    # An explicit model id (not a provider name) -> online model
    if cli_provider and desired_l not in known:
        if cfg.resolve_online():
            return ("online", cfg.kilo_gateway_url, cli_provider)
        print("[*] Online disabled by the ONLINE setting; ignoring explicit online model.")
        desired_l = cfg.provider.lower() if isinstance(cfg.provider, str) else "ask"

    _no_providers_msg = (
        "[-] No LLM providers detected.",
        "   Checked: "
        + ", ".join(f"{name.capitalize()} ({base})" for name, base, _ in cfg.providers)
        + (", Online (Kilo Gateway)" if cfg.resolve_online() else ""),
    )

    # Explicit online request
    if desired_l == "online":
        if not cfg.resolve_online():
            print("[*] Online provider is disabled by the ONLINE setting.")
        else:
            models = get_online_models(cfg)
            if models:
                return ("online", cfg.kilo_gateway_url, models[0])
            print("[-] Online provider not reachable. Falling back to selector.")

    # "auto": first responder in configured order (local first, then online)
    if desired_l == "auto":
        for name, base in get_local_providers(cfg):
            return (name, base, get_model_for_provider(cfg, name, base))
        online_items = get_online_provider_items(cfg)
        if online_items:
            return online_items[0]
        print(_no_providers_msg[0])
        print(_no_providers_msg[1])
        return None

    # Concrete local provider name: probe it, fall back to selector on failure
    if desired_l in (known - _VALID_SELECTORS):
        base = _provider_base(cfg, desired_l)
        check_url = f"{base}/api/tags" if desired_l == "ollama" else f"{base}/v1/models"
        if base and check_endpoint(check_url, cfg.endpoint_timeout):
            return (desired_l, base, get_model_for_provider(cfg, desired_l, base))
        print(f"[-] Provider '{desired_l}' not responding at {base}. Falling back to selector.")

    # ask / None / unknown / fallback after failed concrete probe -> unified selector
    return select_provider(cfg)
