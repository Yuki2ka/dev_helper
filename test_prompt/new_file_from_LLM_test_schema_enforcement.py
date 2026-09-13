#!/usr/bin/env python3
"""Quick test: verify if server honors response_format: json_schema."""

import json
import re
import urllib.request

_THINKING_TAG_PATTERNS = [
    (r"<thinking>(.*?)</thinking>", ""),
    (r"<Thought>(.*?)</Thought>", ""),
]

def strip_thinking(text: str) -> str:
    """Remove thinking/reasoning text from response."""
    if not text:
        return text
    for pattern, repl in _THINKING_TAG_PATTERNS:
        text = re.sub(pattern, repl, text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def test_schema_enforcement(base_url, model, provider="lmstudio"):
    """Send test request; return True if JSON is enforced."""
    print(f"[INFO] Model '{model}' on {provider} - testing JSON mode support")
    
    if provider == "ollama":
        url = f"{base_url}/api/chat"
    else:
        url = f"{base_url}/v1/chat/completions"
    
    # Test 1: baseline (no constraint)
    baseline = {
        "model": model,
        "messages": [{"role": "user", "content": "Return {\"hello\": \"world\"} exactly."}],
        "stream": False,
        "max_tokens": 64,
    }
    if provider == "ollama":
        del baseline["max_tokens"]
    else:
        baseline["reasoning_effort"] = "none"  # Disable thinking for consistent output
    
    print("\n[TEST 1] Baseline (no format constraint):")
    _run_test(url, baseline, provider)
    
    # Test 2: with json_schema
    schema_payload = {
        "model": model,
        "messages": [{"role": "user", "content": "Return {\"hello\": \"world\"} exactly."}],
        "stream": False,
        "max_tokens": 64,
    }
    if provider == "ollama":
        del schema_payload["max_tokens"]
        schema_payload["format"] = "json"
    else:
        schema_payload["reasoning_effort"] = "none"
        schema_payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "test_check",
                "schema": {
                    "type": "object",
                    "properties": {"hello": {"type": "string"}},
                    "required": ["hello"]
                }
            }
        }
    
    print("\n[TEST 2] With JSON constraint:")
    _run_test(url, schema_payload, provider)


def _run_test(url, payload, provider):
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            result = json.loads(raw)
            
            msg = {}
            if "choices" in result:
                msg = result["choices"][0].get("message", {})
                print(f"[FINISH] {result['choices'][0].get('finish_reason', '')}")
            elif "message" in result:
                msg = result.get("message", {})
            
            content = msg.get("content", "")
            reasoning = msg.get("reasoning_content", "") if provider != "ollama" else msg.get("thinking", "")
            
            # If content empty, try to extract from reasoning
            if not content and reasoning:
                content = strip_thinking(reasoning)
                print("[INFO] Extracted from reasoning_content")
            
            try:
                parsed = json.loads(content)
                if parsed == {"hello": "world"}:
                    print(f"[OK] Schema enforced: {parsed}")
                else:
                    print(f"[WARN] Got JSON but: {parsed}")
            except json.JSONDecodeError:
                print(f"[FAIL] Not valid JSON: {content[:100]}")
                
    except Exception as e:
        print(f"[ERROR] {e}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--provider", choices=["lmstudio", "llamacpp", "ollama"], default="lmstudio")
    parser.add_argument("--url", help="Override base URL")
    parser.add_argument("--model", help="Override model name")
    args = parser.parse_args()
    
    base = args.url or {"lmstudio": "http://127.0.0.1:1234", "ollama": "http://127.0.0.1:11434", "llamacpp": "http://127.0.0.1:8080"}[args.provider]
    model = args.model
    
    if not model:
        try:
            check = f"{base}/api/tags" if args.provider == "ollama" else f"{base}/v1/models"
            with urllib.request.urlopen(urllib.request.Request(check, method="GET"), timeout=3) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                models = result.get("models" if args.provider == "ollama" else "data", [])
                model = models[0].get("name" if args.provider == "ollama" else "id", "default")
        except Exception:
            model = "default"
    
    print(f"[PROVIDER] {args.provider} @ {base}, model: {model}")
    test_schema_enforcement(base, model, args.provider)