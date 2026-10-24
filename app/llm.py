
from __future__ import annotations

import time

import requests

import config

PROVIDERS = {
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b", "key_env": None},
    "groq": {"base_url": "https://api.groq.com/openai/v1", "model": "openai/gpt-oss-120b", "key_env": "GROQ_API_KEY"},
}


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, provider: str | None = None, model: str | None = None, base_url: str | None = None,
                 timeout: int = 300):
        self.provider = (provider or config.get("LLM_PROVIDER", "ollama")).lower()
        if self.provider not in PROVIDERS:
            raise LLMError(f"LLM_PROVIDER must be one of {', '.join(PROVIDERS)}; got {self.provider!r}")
        spec = PROVIDERS[self.provider]
        self.model = model or config.get("LLM_MODEL") or spec["model"]
        self.base_url = (base_url or config.get("LLM_BASE_URL") or spec["base_url"]).rstrip("/")
        self.api_key = config.get(spec["key_env"]) if spec["key_env"] else ""
        self.timeout = timeout
        if spec["key_env"] and not self.api_key:
            raise LLMError(f"Set {spec['key_env']} in .env to use the {self.provider} provider.")

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    def chat(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 700) -> str:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if "gpt-oss" in self.model:
            # Reasoning models spend part of max_tokens thinking; keep that short and leave room for the answer.
            body["reasoning_effort"] = "low"
            body["max_tokens"] = max(max_tokens, 2000)
        last = "no response"
        for attempt in range(5):
            try:
                resp = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=body, timeout=self.timeout)
            except requests.ConnectionError as exc:
                hint = " Is Ollama running? Start it with: ollama serve" if self.provider == "ollama" else ""
                raise LLMError(f"Could not reach {self.base_url}.{hint}") from exc
            except requests.Timeout as exc:
                raise LLMError("The model took too long to answer.") from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"{resp.status_code} {resp.text[:300]}"
                if "per day" in resp.text.lower():       # a daily quota will not clear in seconds
                    raise LLMError(f"{self.label} daily limit reached. Try again later or use another model. Details: {last}")
                try:
                    wait = float(resp.headers.get("retry-after", ""))
                except ValueError:
                    wait = 5 * (attempt + 1)
                time.sleep(min(wait, 30))
                continue
            if resp.status_code != 200:
                raise LLMError(f"{self.label} returned {resp.status_code}: {resp.text[:300]}")
            try:
                return resp.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError(f"Unexpected response from the model: {resp.text[:200]}") from exc
        raise LLMError(f"The model service kept failing ({last}).")
