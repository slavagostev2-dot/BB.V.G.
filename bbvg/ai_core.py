from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import requests

FEATURE_HEALTH_INSPECTOR = "health_inspector"
FEATURE_SUSPICIOUS_POST_ANALYSIS = "suspicious_post_analysis"
FEATURE_SOURCE_DISCOVERY = "source_discovery"
FEATURE_NATURAL_LANGUAGE_ADMIN = "natural_language_admin"
KNOWN_FEATURES = frozenset(
    {
        FEATURE_HEALTH_INSPECTOR,
        FEATURE_SUSPICIOUS_POST_ANALYSIS,
        FEATURE_SOURCE_DISCOVERY,
        FEATURE_NATURAL_LANGUAGE_ADMIN,
    }
)

STATE_VERSION = 1
DEFAULT_STATE_PATH = Path(__file__).resolve().parents[1] / "ai_runtime_state.json"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
SUPPORTED_PROVIDERS = frozenset({"openai", "gemini"})
GEMINI_MODEL_ALIASES = {
    "gemini-2.5-flash-lite": "gemini-3.1-flash-lite",
}


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, "") or default)
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


def _feature_env(feature: str) -> str:
    token = re.sub(r"[^A-Z0-9]+", "_", feature.upper()).strip("_")
    return f"BBVG_AI_FEATURE_{token}"


def _provider_api_key(provider: str) -> str:
    if provider == "gemini":
        return os.getenv("GEMINI_API_KEY", "").strip()
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "").strip()
    return ""


def _normalized_model(provider: str, model: str) -> str:
    value = str(model or "").strip()
    if provider == "gemini":
        return GEMINI_MODEL_ALIASES.get(value, value)
    return value


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = False
    enabled_features: frozenset[str] = frozenset()
    provider: str = "openai"
    model: str = ""
    timeout_seconds: int = 20
    max_calls_per_minute: int = 10
    cache_ttl_seconds: int = 900
    max_decisions: int = 500
    state_path: Path = DEFAULT_STATE_PATH
    api_key: str = field(default="", repr=False, compare=False)

    @classmethod
    def from_env(cls) -> "AIConfig":
        features = {
            item.strip()
            for item in os.getenv("BBVG_AI_FEATURES", "").split(",")
            if item.strip()
        }
        if "*" in features:
            features = set(KNOWN_FEATURES)
        for feature in KNOWN_FEATURES:
            name = _feature_env(feature)
            if name in os.environ:
                if _bool_env(name):
                    features.add(feature)
                else:
                    features.discard(feature)

        provider = os.getenv("BBVG_AI_PROVIDER", "openai").strip().casefold() or "openai"
        model = _normalized_model(provider, os.getenv("BBVG_AI_MODEL", ""))
        return cls(
            enabled=_bool_env("BBVG_AI_ENABLED"),
            enabled_features=frozenset(features & set(KNOWN_FEATURES)),
            provider=provider,
            model=model,
            timeout_seconds=_int_env("BBVG_AI_TIMEOUT_SECONDS", 20, 3, 120),
            max_calls_per_minute=_int_env("BBVG_AI_MAX_CALLS_PER_MINUTE", 10, 1, 120),
            cache_ttl_seconds=_int_env("BBVG_AI_CACHE_TTL_SECONDS", 900, 0, 86400),
            max_decisions=_int_env("BBVG_AI_MAX_DECISIONS", 500, 50, 5000),
            state_path=Path(os.getenv("BBVG_AI_STATE_PATH", str(DEFAULT_STATE_PATH))),
            api_key=_provider_api_key(provider),
        )

    def feature_enabled(self, feature: str) -> bool:
        return self.enabled and feature in self.enabled_features

    def provider_configured(self) -> bool:
        return self.provider in SUPPORTED_PROVIDERS and bool(self.api_key and self.model)


@dataclass(frozen=True)
class AIResult:
    ok: bool
    status: str
    text: str = ""
    data: dict[str, Any] | None = None
    provider: str = ""
    model: str = ""
    cached: bool = False
    used_fallback: bool = False
    error: str = ""
    decision_id: str = ""


Transport = Callable[[AIConfig, str], str]


class AIStateStore:
    def __init__(self, path: Path, max_decisions: int) -> None:
        self.path = path
        self.max_decisions = max_decisions

    @staticmethod
    def empty() -> dict[str, Any]:
        return {"version": STATE_VERSION, "decisions": [], "cache": {}, "rate_calls": []}

    def load(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return self.empty()
        if not isinstance(value, dict):
            return self.empty()
        value.setdefault("decisions", [])
        value.setdefault("cache", {})
        value.setdefault("rate_calls", [])
        value["version"] = STATE_VERSION
        return value

    def save(self, state: dict[str, Any]) -> None:
        decisions = state.get("decisions")
        if isinstance(decisions, list) and len(decisions) > self.max_decisions:
            state["decisions"] = decisions[-self.max_decisions :]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)


class AIClient:
    """Optional AI layer. Core monitoring must keep working when this layer fails."""

    def __init__(
        self,
        config: AIConfig | None = None,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config or AIConfig.from_env()
        self.store = AIStateStore(self.config.state_path, self.config.max_decisions)
        self.transport = transport
        self.clock = clock

    def feature_enabled(self, feature: str) -> bool:
        return self.config.feature_enabled(feature)

    def status_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "provider": self.config.provider,
            "model": self.config.model,
            "provider_configured": bool(self.transport or self.config.provider_configured()),
            "enabled_features": sorted(self.config.enabled_features),
            "max_calls_per_minute": self.config.max_calls_per_minute,
            "cache_ttl_seconds": self.config.cache_ttl_seconds,
        }

    def ask_text(
        self,
        feature: str,
        *,
        system_prompt: str,
        user_input: str,
        fallback_text: str = "",
    ) -> AIResult:
        base = {
            "text": fallback_text,
            "provider": self.config.provider,
            "model": self.config.model,
            "used_fallback": True,
        }
        if feature not in KNOWN_FEATURES:
            return AIResult(False, "unknown_feature", error=f"unknown feature: {feature}", **base)
        if not self.config.feature_enabled(feature):
            return AIResult(False, "disabled", **base)
        if self.transport is None and not self.config.provider_configured():
            return AIResult(False, "not_configured", error="AI provider is not configured", **base)

        prompt = f"SYSTEM INSTRUCTIONS:\n{system_prompt.strip()}\n\nINPUT:\n{user_input.strip()}"
        cache_key = self._cache_key(feature, prompt)
        state = self.store.load()
        now = self.clock()
        cached = self._cache_get(state, cache_key, now)
        if cached is not None:
            decision_id = self._record(state, feature, "cache_hit", cached, now, cached=True)
            self.store.save(state)
            return AIResult(
                True,
                "ok",
                cached,
                provider=self.config.provider,
                model=self.config.model,
                cached=True,
                decision_id=decision_id,
            )

        if not self._take_rate_slot(state, now):
            decision_id = self._record(state, feature, "rate_limited", fallback_text, now)
            self.store.save(state)
            return AIResult(False, "rate_limited", decision_id=decision_id, **base)

        try:
            text = self._call_provider(prompt).strip()
            if not text:
                raise ValueError("AI provider returned empty text")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            decision_id = self._record(
                state,
                feature,
                "provider_error",
                fallback_text,
                now,
                error=error,
            )
            self.store.save(state)
            return AIResult(
                False,
                "provider_error",
                error=error,
                decision_id=decision_id,
                **base,
            )

        if self.config.cache_ttl_seconds:
            state["cache"][cache_key] = {
                "text": text,
                "expires_at": now + self.config.cache_ttl_seconds,
            }
        decision_id = self._record(state, feature, "ok", text, now)
        self._prune_cache(state, now)
        self.store.save(state)
        return AIResult(
            True,
            "ok",
            text,
            provider=self.config.provider,
            model=self.config.model,
            decision_id=decision_id,
        )

    def ask_json(
        self,
        feature: str,
        *,
        system_prompt: str,
        user_input: str,
        fallback_data: dict[str, Any] | None = None,
    ) -> AIResult:
        fallback = dict(fallback_data or {})
        result = self.ask_text(
            feature,
            system_prompt=system_prompt.rstrip()
            + "\nReturn only one valid JSON object without Markdown fences.",
            user_input=user_input,
            fallback_text=json.dumps(fallback, ensure_ascii=False, sort_keys=True),
        )
        if not result.ok:
            return AIResult(
                False,
                result.status,
                result.text,
                fallback,
                result.provider,
                result.model,
                result.cached,
                True,
                result.error,
                result.decision_id,
            )
        try:
            data = self._parse_json(result.text)
        except (ValueError, json.JSONDecodeError) as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            state = self.store.load()
            decision_id = self._record(
                state,
                feature,
                "invalid_json",
                result.text,
                self.clock(),
                error=error,
            )
            self.store.save(state)
            return AIResult(
                False,
                "invalid_json",
                json.dumps(fallback, ensure_ascii=False, sort_keys=True),
                fallback,
                result.provider,
                result.model,
                result.cached,
                True,
                error,
                decision_id,
            )
        self._annotate(result.decision_id, data)
        return AIResult(
            True,
            "ok",
            result.text,
            data,
            result.provider,
            result.model,
            result.cached,
            False,
            "",
            result.decision_id,
        )

    def _cache_key(self, feature: str, prompt: str) -> str:
        raw = json.dumps(
            {
                "feature": feature,
                "provider": self.config.provider,
                "model": self.config.model,
                "prompt": prompt,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _cache_get(state: dict[str, Any], key: str, now: float) -> str | None:
        entry = state.get("cache", {}).get(key)
        if not isinstance(entry, dict) or float(entry.get("expires_at", 0) or 0) <= now:
            state.get("cache", {}).pop(key, None)
            return None
        return entry.get("text") if isinstance(entry.get("text"), str) else None

    def _take_rate_slot(self, state: dict[str, Any], now: float) -> bool:
        calls = []
        for value in state.get("rate_calls", []):
            try:
                timestamp = float(value)
            except (TypeError, ValueError):
                continue
            if now - timestamp < 60:
                calls.append(timestamp)
        if len(calls) >= self.config.max_calls_per_minute:
            state["rate_calls"] = calls
            return False
        state["rate_calls"] = calls + [now]
        return True

    @staticmethod
    def _prune_cache(state: dict[str, Any], now: float) -> None:
        cache = state.get("cache")
        if not isinstance(cache, dict):
            state["cache"] = {}
            return
        for key in list(cache):
            entry = cache.get(key)
            if not isinstance(entry, dict) or float(entry.get("expires_at", 0) or 0) <= now:
                cache.pop(key, None)

    def _record(
        self,
        state: dict[str, Any],
        feature: str,
        status: str,
        text: str,
        now: float,
        *,
        cached: bool = False,
        error: str = "",
    ) -> str:
        decision_id = uuid.uuid4().hex
        row = {
            "id": decision_id,
            "created_at": now,
            "feature": feature,
            "status": status,
            "provider": self.config.provider,
            "model": self.config.model,
            "cached": cached,
            "output_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else "",
        }
        if error:
            row["error"] = error
        state.setdefault("decisions", []).append(row)
        return decision_id

    def _annotate(self, decision_id: str, data: dict[str, Any]) -> None:
        state = self.store.load()
        for row in reversed(state.get("decisions", [])):
            if not isinstance(row, dict) or row.get("id") != decision_id:
                continue
            for key in ("decision", "classification", "action", "reason"):
                value = data.get(key)
                if isinstance(value, (str, int, float, bool)):
                    row[key] = str(value)[:500]
            confidence = data.get("confidence")
            if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                row["confidence"] = max(0.0, min(1.0, float(confidence)))
            self.store.save(state)
            return

    def _call_provider(self, prompt: str) -> str:
        if self.transport:
            return self.transport(self.config, prompt)
        if self.config.provider == "openai":
            return self._call_openai(prompt)
        if self.config.provider == "gemini":
            return self._call_gemini(prompt)
        raise RuntimeError(f"unsupported AI provider: {self.config.provider}")

    def _call_openai(self, prompt: str) -> str:
        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            json={"model": self.config.model, "input": prompt},
            timeout=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"OpenAI HTTP {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("OpenAI returned invalid JSON") from exc
        return self._openai_output_text(payload)

    def _call_gemini(self, prompt: str) -> str:
        model = quote(self.config.model, safe="-._")
        url = f"{GEMINI_MODELS_URL}/{model}:generateContent"
        generation_config: dict[str, Any] = {"temperature": 0.1}
        if "Return only one valid JSON object without Markdown fences." in prompt:
            generation_config["responseMimeType"] = "application/json"
        response = requests.post(
            url,
            headers={
                "x-goog-api-key": self.config.api_key,
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            },
            timeout=self.config.timeout_seconds,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Gemini HTTP {response.status_code}: {response.text[:300]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Gemini returned invalid JSON") from exc
        return self._gemini_output_text(payload)

    @staticmethod
    def _openai_output_text(payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
            return payload["output_text"]
        chunks = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue
            for part in item.get("content", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])
        if not chunks:
            raise RuntimeError("OpenAI response contains no output text")
        return "\n".join(chunks)

    @staticmethod
    def _gemini_output_text(payload: dict[str, Any]) -> str:
        chunks: list[str] = []
        candidates = payload.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                content = candidate.get("content")
                if not isinstance(content, dict):
                    continue
                parts = content.get("parts")
                if not isinstance(parts, list):
                    continue
                for part in parts:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chunks.append(part["text"])
        if chunks:
            return "\n".join(chunks)
        block_reason = ""
        prompt_feedback = payload.get("promptFeedback")
        if isinstance(prompt_feedback, dict):
            block_reason = str(prompt_feedback.get("blockReason") or "")
        suffix = f": {block_reason}" if block_reason else ""
        raise RuntimeError(f"Gemini response contains no output text{suffix}")

    # Backward-compatible name used by older tests and callers.
    _output_text = _openai_output_text

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        candidate = text.strip()
        if candidate.startswith("```"):
            candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
            candidate = re.sub(r"\s*```$", "", candidate)
        value = json.loads(candidate)
        if not isinstance(value, dict):
            raise ValueError("AI response must be a JSON object")
        return value


def client_from_env() -> AIClient:
    return AIClient(AIConfig.from_env())


def feature_enabled(feature: str) -> bool:
    return AIConfig.from_env().feature_enabled(feature)
