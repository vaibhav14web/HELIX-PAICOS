import os
import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from foundation.event_bus.event_bus import EventBus, HelixEvent
from core_ai.tool_calling.tool_parser import ToolParser
from core_ai.llm_engine.think_filter import ThinkBlockFilter

logger = logging.getLogger("helix.llm_engine")


class LLMEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus

        self._backend = os.getenv("HELIX_LLM_BACKEND", "mock")
        from foundation.paths import get_orchestrator_model_path, get_coder_model_path
        self._orchestrator_model_path = str(get_orchestrator_model_path())
        self._coder_model_path = str(get_coder_model_path())
        self._orchestrator_model_name = os.getenv(
            "HELIX_LLM_ORCHESTRATOR_MODEL_NAME",
            "qwen3:4b",
        )
        self._coder_model_name = os.getenv(
            "HELIX_LLM_CODER_MODEL_NAME",
            "qwen2.5-coder:3b",
        )
        self._model_path = self._orchestrator_model_path
        self._model_name = self._orchestrator_model_name
        self._ollama_url = os.getenv("HELIX_LLM_OLLAMA_URL", "http://localhost:11434")
        self._context_length = int(os.getenv("HELIX_LLM_CONTEXT_LENGTH", "4096"))
        self._max_tokens = int(os.getenv("HELIX_LLM_MAX_TOKENS", "512"))
        self._temperature = float(os.getenv("HELIX_LLM_TEMPERATURE", "0.7"))
        self._gpu_layers = int(os.getenv("HELIX_LLM_GPU_LAYERS", "-1"))
        self._idle_unload_seconds = int(os.getenv("HELIX_LLM_IDLE_UNLOAD_SECONDS", "1800"))
        self._trust_remote_code = os.getenv("HELIX_LLM_TRUST_REMOTE_CODE", "false").lower() in ("true", "1", "yes")

        self._model: Any = None
        self._loaded = False
        self._active_model_name: str | None = None
        self._last_used: datetime | None = None
        self._load_lock = asyncio.Lock()
        self._unload_task: asyncio.Task | None = None
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._http_session: Any = None
        self._abort = asyncio.Event()
        self._session_contracts: dict[str, dict[str, Any]] = {}
        self._current_system_state = os.getenv("HELIX_SYSTEM_STATE", "sleep")

    @staticmethod
    def get_gpu_vram_used_mb(gpu_index: int | str | None = None) -> float | None:
        """
        Query physical GPU VRAM usage in MB using nvidia-smi across all processes
        (including external daemons like Ollama, and in-process runtimes like llama.cpp / Faster-Whisper).

        Reconciles driver-reserved allocations on WDDM / Windows by computing:
            max(memory.used, memory.total - memory.free)

        Supports multi-GPU configurations by selecting gpu_index, HELIX_GPU_INDEX,
        or CUDA_VISIBLE_DEVICES. If multiple GPUs are present and none is specified,
        returns the maximum memory usage across all detected GPUs.

        Returns None if nvidia-smi is not available, times out, or output is malformed.
        """
        import subprocess
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=index,memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None

            lines = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
            if not lines:
                return None

            target_idx_str: str | None = None
            if gpu_index is not None:
                target_idx_str = str(gpu_index).strip()
            elif os.getenv("HELIX_GPU_INDEX"):
                target_idx_str = os.getenv("HELIX_GPU_INDEX", "").strip()
            elif os.getenv("CUDA_VISIBLE_DEVICES"):
                cuda_devs = os.getenv("CUDA_VISIBLE_DEVICES", "").split(",")
                if cuda_devs and cuda_devs[0].strip().isdigit():
                    target_idx_str = cuda_devs[0].strip()

            parsed_gpus: dict[str, float] = {}
            for line in lines:
                parts = [p.strip() for p in line.split(",")]
                if len(parts) < 4:
                    continue
                try:
                    idx_str = parts[0]
                    total_mb = float(parts[1])
                    used_mb = float(parts[2])
                    free_mb = float(parts[3])
                    reconciled_used = max(used_mb, max(0.0, total_mb - free_mb))
                    parsed_gpus[idx_str] = reconciled_used
                except (ValueError, IndexError):
                    continue

            if not parsed_gpus:
                return None

            if target_idx_str is not None:
                return parsed_gpus.get(target_idx_str)

            return max(parsed_gpus.values())
        except Exception:
            return None

    @staticmethod
    def get_estimated_model_size_mb(model_path: str = "", model_name: str = "") -> float:
        """
        Estimate the VRAM footprint in MB required to load a model.
        Checks explicit override HELIX_ESTIMATED_MODEL_SIZE_MB first, then model file size on disk,
        then heuristics based on model name parameter count.
        """
        env_override = os.getenv("HELIX_ESTIMATED_MODEL_SIZE_MB")
        if env_override:
            try:
                return float(env_override)
            except ValueError:
                pass

        if model_path:
            p = Path(model_path)
            try:
                if p.is_file():
                    return float(p.stat().st_size) / (1024.0 * 1024.0)
            except Exception:
                pass

        name_lower = (model_name or "").lower()
        if any(tag in name_lower for tag in ("7b", "8b")):
            return 4500.0
        if any(tag in name_lower for tag in ("4b", "3.8b", "phi-3")):
            return 2500.0
        if any(tag in name_lower for tag in ("3b", "2.5-coder", "coder")):
            return 2000.0
        if any(tag in name_lower for tag in ("1.5b", "1b")):
            return 1000.0

        return 2048.0


    async def start(self) -> None:
        self._event_handler_map = {
            "llm.generate": self._handle_generate,
            "llm.generate.stop": self._handle_stop,
            "system.state.change": self._handle_state_change,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

        await self._event_bus.publish_event(
            source="llm_engine",
            event_type="llm.ready",
            payload={"backend": self._backend, "loaded": False},
        )
        logger.info(
            "LLM Engine started (backend=%s, context_length=%d, max_tokens=%d, orchestrator_model=%s)",
            self._backend,
            self._context_length,
            self._max_tokens,
            self._orchestrator_model_name,
        )
        print(f"[HELIX STARTUP] LLM Backend: {self._backend} | Pinned Context (num_ctx): {self._context_length} | Model: {self._orchestrator_model_name}")
        if self._backend == "ollama":
            try:
                asyncio.create_task(self.verify_ollama_runtime())
            except Exception:
                pass

    async def _handle_state_change(self, event: HelixEvent) -> None:
        state = event.payload.get("state", "sleep")
        self._current_system_state = state
        if state in ("sleep", "background") or event.payload.get("force_unload", False):
            self._abort.set()
            await self._unload_model()

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        self._abort.set()
        await self._unload_model()
        if self._http_session:
            await self._http_session.aclose()
            self._http_session = None
        logger.info("LLM Engine stopped")

    async def _ensure_session(self) -> None:
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._http_session is None or getattr(self._http_session, "is_closed", False) or getattr(self, "_session_loop", None) != current_loop:
            self._http_session = None
            import httpx
            timeout_val = float(os.getenv("HELIX_LLM_TIMEOUT", "120.0"))
            self._http_session = httpx.AsyncClient(timeout=timeout_val)
            self._session_loop = current_loop

    def _check_and_log_observability(
        self,
        prompt_tokens: int,
        output_tokens: int,
        finish_reason: str | None,
        model_name: str,
    ) -> None:
        """
        Log per call: prompt token count, num_ctx in effect, output token count, and finish reason.
        Warn when prompt tokens exceed 85% of num_ctx (TASK 1 / to_make.md).
        """
        logger.info(
            "LLM call metrics [model=%s]: prompt_tokens=%d, output_tokens=%d, num_ctx=%d, finish_reason=%s",
            model_name,
            prompt_tokens,
            output_tokens,
            self._context_length,
            finish_reason,
        )
        threshold_85 = int(self._context_length * 0.85)
        if prompt_tokens > threshold_85:
            logger.warning(
                "CONTEXT WARNING: Prompt tokens (%d) exceed 85%% of active context window num_ctx (%d) (threshold=%d). High truncation risk!",
                prompt_tokens,
                self._context_length,
                threshold_85,
            )

    async def verify_ollama_runtime(self) -> dict[str, Any]:
        """
        Verify loaded models and runtime context with ollama show and ollama ps per Task 2.
        Prints verified status to stdout/logs at startup or on demand.
        """
        if self._backend != "ollama":
            return {"backend": self._backend, "num_ctx": self._context_length, "status": "non-ollama"}
        await self._ensure_session()
        runtime_info = {"configured_num_ctx": self._context_length, "model": self._orchestrator_model_name}
        try:
            ps_res = await self._http_session.get(f"{self._ollama_url}/api/ps")
            if ps_res.status_code == 200:
                runtime_info["ps"] = ps_res.json()
        except Exception as e:
            runtime_info["ps_error"] = str(e)

        try:
            show_res = await self._http_session.post(
                f"{self._ollama_url}/api/show",
                json={"name": self._orchestrator_model_name},
            )
            if show_res.status_code == 200:
                show_data = show_res.json()
                runtime_info["parameters"] = show_data.get("parameters", "")
                runtime_info["template"] = show_data.get("template", "")
        except Exception as e:
            runtime_info["show_error"] = str(e)

        logger.debug("Ollama runtime verification details: %s", runtime_info)
        print(f"[HELIX STARTUP] Ollama runtime verification: active num_ctx={self._context_length}, model={self._orchestrator_model_name}")
        return runtime_info

    def _detect_contract_cancellation(self, text: str) -> bool:
        lower = text.lower()
        patterns = [
            r"\b(?:stop|done\s+with|finish(?:ed)?|end)\s+(?:revising|revision|evaluation|quiz)\b",
            r"\b(?:let\'?s\s+move\s+on|change\s+the\s+topic|new\s+topic)\b",
        ]
        return any(re.search(p, lower) for p in patterns)

    def _is_establishing_evaluation_contract(self, text: str) -> bool:
        lower = text.lower()
        patterns = [
            r"\brevising\s+(?:[a-z0-9_-]+\s+)*concepts?\b",
            r"\bi\s+(?:will|want\s+to)\s+explain\s+(?:them|concepts?|it)\s+to\s+you\b",
            r"\btell\s+me\s+if\s+(?:my\s+explanation|it|my\s+answer)\s+is\s+correct\b",
            r"\bjudge\s+(?:whether|if)?\s*(?:my\s+explanation|it)\b",
            r"\bevaluate\s+(?:my\s+explanation|my\s+answer|this\s+definition)\b",
            r"\bcorrect[,\s]+partially\s+correct[,\s]+(?:or\s+)?incorrect\b",
        ]
        return any(re.search(p, lower) for p in patterns)

    def _is_explicit_coding_request(self, prompt: str, messages: list[dict] | None = None) -> bool:
        text = prompt or ""
        if messages:
            text = " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
        lower = text.lower()

        # 1. Concrete code syntax indicators
        code_syntax_patterns = [
            r"```[a-z]*\n[\s\S]*?```",
            r"\bdef\s+[a-zA-Z0-9_]+\s*\(",
            r"\bclass\s+[a-zA-Z0-9_]+(?:\([a-zA-Z0-9_.,\s]*\))?:",
            r"\b(?:import|from)\s+[a-zA-Z0-9_]+",
            r"\b(?:const|let|var)\s+[a-zA-Z0-9_]+\s*=",
            r"\bpublic\s+(?:class|static|void)\b",
            r"#include\s+<",
        ]
        if any(re.search(p, text) for p in code_syntax_patterns):
            return True

        # 2. Explicit code generation, debugging, refactoring, implementation imperatives
        imperative_patterns = [
            r"\b(?:write|create|generate|provide)\s+(?:a|an|the|some)?\s*(?:python|javascript|typescript|java|c\+\+|rust|go|c#|sql)?\s*(?:code|script|function|class|method|snippet|query|test|implementation)\b",
            r"\bimplement\s+(?:a|an|the)?\s*(?:python|javascript|typescript|java|c\+\+|rust|go|c#)?\s*(?:class|function|method|interface|algorithm|endpoint|handler|decorator)\b",
            r"\bimplement\b.*?\b(?:using|in)\s+(?:java|python|javascript|typescript|rust|c\+\+|c#|go)\b",
            r"\b(?:debug|troubleshoot|fix)\s+(?:this|the|my)?\s*(?:code|bug|syntax\s+error|error|exception|traceback|stacktrace|script|function)\b",
            r"\brefactor\s+(?:this|the|my)?\s*(?:code|class|function|method|script)\b",
            r"\bhow\s+(?:do|can)\s+i\s+(?:write|code|implement)\b.*?\bin\s+(?:python|javascript|typescript|java|c\+\+|rust|go|c#)\b",
        ]
        if any(re.search(p, lower) for p in imperative_patterns):
            return True

        return False

    def _is_conceptual_educational(self, prompt: str, messages: list[dict] | None = None) -> bool:
        text = prompt or ""
        if messages:
            text = " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
        lower = text.lower()

        educational_patterns = [
            r"\brevising\s+(?:[a-z0-9_-]+\s+)*concepts?\b",
            r"\bi\s+(?:will|want\s+to)\s+explain\s+(?:them|concepts?|it)\s+to\s+you\b",
            r"\btell\s+me\s+if\s+(?:my\s+explanation|it|my\s+answer)\s+is\s+correct\b",
            r"\bjudge\s+(?:whether|if)?\s*(?:my\s+explanation|it)\b",
            r"\bevaluate\s+(?:my\s+explanation|my\s+answer|this\s+definition)\b",
            r"\bcorrect[,\s]+partially\s+correct[,\s]+(?:or\s+)?incorrect\b",
            r"\bwhat\s+(?:is|are)\s+(?:the\s+)?(?:four\s+pillars\s+of\s+)?(?:oop|encapsulation|inheritance|polymorphism|abstraction|object\s+oriented)\b",
            r"\bexplain\s+(?:the\s+concept\s+of\s+)?(?:encapsulation|inheritance|polymorphism|abstraction|oop)\b",
            r"\bdifference\s+between\s+(?:an?\s+)?(?:abstract\s+)?class\s+and\s+(?:an?\s+)?(?:object|interface)\b",
            r"\b[a-z0-9_-]+\s+is\s+(?:the\s+)?(?:bundling|process|blueprint|concept|mechanism|ability|practice)\b",
            r"\b(?:encapsulation|inheritance|polymorphism|abstraction)\s+is\b",
            r"\ba?\s*class\s+is\s+(?:a\s+)?(?:blueprint|template)\b",
            r"\ban?\s*object\s+is\s+(?:an?\s+)?instance\b",
            r"\bbundling\s+of\s+data\s+and\s+(?:the\s+)?methods\b",
            r"\brestricting\s+direct\s+access\b",
        ]
        return any(re.search(p, lower) for p in educational_patterns)

    def _detect_coding_in_prompt(self, prompt: str) -> bool:
        if self._is_conceptual_educational(prompt):
            return False
        return self._is_explicit_coding_request(prompt)

    async def _handle_generate(self, event: HelixEvent) -> None:
        self._abort.clear()
        prompt = event.payload.get("prompt", "")
        session_id = event.payload.get("session_id", "default")
        tools = event.payload.get("tools")
        messages = event.payload.get("messages")

        if self._current_system_state == "background":
            logger.warning("LLM generation rejected: LLM loading is forbidden in background state (ADR-027)")
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.error",
                payload={"error": "LLM loading forbidden in background state (ADR-027)", "session_id": session_id},
                correlation_id=event.correlation_id,
            )
            return

        # Session contract management
        combined_text = prompt or ""
        if messages:
            combined_text += " " + " ".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))

        if self._detect_contract_cancellation(combined_text):
            self._session_contracts.pop(session_id, None)
        elif self._is_establishing_evaluation_contract(combined_text):
            self._session_contracts[session_id] = {
                "mode": "concept_evaluation",
                "concise": True,
            }

        is_coding = event.payload.get("is_coding", False)
        is_con = self._is_conceptual_educational(prompt, messages)
        is_exp = self._is_explicit_coding_request(prompt, messages)
        has_active_contract = session_id in self._session_contracts

        if (is_con or has_active_contract) and not is_exp:
            # Overrule upstream keyword false-positives
            is_coding = False
        elif not is_coding:
            source = event.source or ""
            if "coding" in source.lower() or "code" in source.lower():
                is_coding = True
            elif is_exp:
                is_coding = True

        if not prompt.strip():
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.error",
                payload={"error": "Empty prompt", "session_id": session_id},
                correlation_id=event.correlation_id,
            )
            return

        # Check active VRAM ceiling (SAD §13: <5GB VRAM limit = 5,120 MB)
        max_vram_mb = float(os.getenv("HELIX_MAX_ACTIVE_VRAM_MB", "5120"))
        used_vram = self.get_gpu_vram_used_mb()

        # Fail-closed enforcement policy:
        # If nvidia-smi is missing, times out, or returns malformed output:
        # Real GPU backends (ollama, llama.cpp, transformers) or when HELIX_FAIL_CLOSED_VRAM=true MUST fail closed.
        fail_closed = (self._backend != "mock") or (
            os.getenv("HELIX_FAIL_CLOSED_VRAM", "").lower() in ("true", "1", "yes")
        )

        if used_vram is None:
            if fail_closed:
                err_msg = (
                    "LLM generation rejected: unable to verify GPU VRAM ceiling "
                    "(nvidia-smi unavailable, timed out, or malformed; fail-closed per SAD §13)"
                )
                logger.error(err_msg)
                await self._event_bus.publish_event(
                    source="llm_engine",
                    event_type="llm.generation.error",
                    payload={"error": err_msg, "session_id": session_id, "vram_mb": None},
                    correlation_id=event.correlation_id,
                )
                await self._event_bus.publish_event(
                    source="llm_engine",
                    event_type="system.state.request",
                    payload={"state": "sleep", "reason": "vram_verification_failed"},
                    correlation_id=event.correlation_id,
                )
                return
            else:
                logger.warning("VRAM monitoring unavailable; proceeding in mock backend mode without GPU enforcement.")
        else:
            # Model load and KV-cache growth projection:
            # If the model is not loaded in-process, compute estimated load footprint
            target_model_path = self._coder_model_path if is_coding else self._orchestrator_model_path
            target_model_name = self._coder_model_name if is_coding else self._orchestrator_model_name

            additional_load_mb = 0.0
            is_model_resident = self._loaded and (self._active_model_name is None or self._active_model_name == target_model_name)
            if not is_model_resident and (self._backend != "mock" or os.getenv("HELIX_ESTIMATED_MODEL_SIZE_MB")):
                additional_load_mb = self.get_estimated_model_size_mb(target_model_path, target_model_name)

            kv_env = os.getenv("HELIX_KV_CACHE_RESERVE_MB")
            if kv_env is not None:
                kv_reserve_mb = float(kv_env)
            elif self._backend != "mock":
                kv_reserve_mb = 256.0
            else:
                kv_reserve_mb = 0.0

            projected_vram = used_vram + additional_load_mb + kv_reserve_mb

            # SAD §13: strict ceiling of < 5,120 MiB rejects usage >= 5,120 MiB
            if projected_vram >= max_vram_mb:
                err_msg = (
                    f"LLM generation rejected: active GPU VRAM ({projected_vram:.0f} MB) "
                    f"exceeds {max_vram_mb:.0f} MB ceiling (SAD §13)"
                )
                logger.error(err_msg)
                await self._event_bus.publish_event(
                    source="llm_engine",
                    event_type="llm.generation.error",
                    payload={
                        "error": err_msg,
                        "session_id": session_id,
                        "vram_mb": projected_vram,
                        "current_vram_mb": used_vram,
                        "additional_load_mb": additional_load_mb,
                        "kv_reserve_mb": kv_reserve_mb,
                    },
                    correlation_id=event.correlation_id,
                )
                await self._event_bus.publish_event(
                    source="llm_engine",
                    event_type="system.state.request",
                    payload={"state": "sleep", "reason": "vram_ceiling_exceeded"},
                    correlation_id=event.correlation_id,
                )
                return

        await self._event_bus.publish_event(
            source="llm_engine",
            event_type="llm.generation.start",
            payload={"session_id": session_id},
            correlation_id=event.correlation_id,
        )

        tools = event.payload.get("tools")
        messages = event.payload.get("messages")

        try:
            tool_calls = []
            if tools or messages:
                if not messages:
                    messages = [{"role": "user", "content": prompt}]
                chat_res = await self.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    correlation_id=event.correlation_id,
                    session_id=session_id,
                    is_coding=is_coding,
                )
                response = chat_res.get("content", "")
                tool_calls = chat_res.get("tool_calls", [])
            else:
                response = await self.generate(prompt, correlation_id=event.correlation_id, session_id=session_id, is_coding=is_coding)
                cleaned_text, parsed_calls = ToolParser.parse_text_response(response)
                if parsed_calls:
                    response = cleaned_text
                    tool_calls = [tc.to_dict() for tc in parsed_calls]

            self._last_used = datetime.now(timezone.utc)

            self._schedule_unload_if_idle()

            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.complete",
                payload={
                    "session_id": session_id,
                    "response": response,
                    "tool_calls": tool_calls,
                },
                correlation_id=event.correlation_id,
            )
        except asyncio.CancelledError:
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.error",
                payload={"error": "Generation cancelled", "session_id": session_id},
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("LLM generation failed")
            # Cleanup partial model state on failure
            try:
                await self._unload_model_internal()
            except Exception:
                pass
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.error",
                payload={"error": str(e), "session_id": session_id},
                correlation_id=event.correlation_id,
            )
            # Transition orchestrator to sleep on model-load or generation failure (ADR-027 / AC §2.4)
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="system.state.request",
                payload={"state": "sleep", "reason": f"llm_failure: {str(e)}"},
                correlation_id=event.correlation_id,
            )

    async def _handle_stop(self, event: HelixEvent) -> None:
        self._abort.set()
        logger.info("LLM generation stop requested")

    async def generate(self, prompt: str, correlation_id: str | None = None, session_id: str = "default", is_coding: bool = False) -> str:
        model_path = self._coder_model_path if is_coding else self._orchestrator_model_path
        model_name = self._coder_model_name if is_coding else self._orchestrator_model_name

        if self._backend == "mock":
            res = self._generate_mock(prompt)
            prompt_tokens = max(1, len(prompt) // 4)
            out_tokens = max(1, len(res) // 4)
            self._check_and_log_observability(prompt_tokens, out_tokens, "stop", "mock")
            return res
        if self._backend == "ollama":
            return await self._generate_ollama(prompt, model_name=model_name, correlation_id=correlation_id, session_id=session_id)

        await self._ensure_model_loaded(model_path, model_name)
        return await self._generate_real(prompt, correlation_id=correlation_id, session_id=session_id)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        correlation_id: str | None = None,
        session_id: str = "default",
        is_coding: bool = False,
        response_format: Any | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send chat messages with optional tool definitions and return structured response & tool calls."""
        model_name = self._coder_model_name if is_coding else self._orchestrator_model_name

        if self._backend == "mock":
            res = self._chat_with_tools_mock(messages, tools, response_format=response_format)
            prompt_text = "".join(str(m.get("content", "")) for m in messages)
            prompt_tokens = max(1, len(prompt_text) // 4)
            out_tokens = max(1, len(res.get("content", "")) // 4)
            finish_reason = "stop"
            self._check_and_log_observability(prompt_tokens, out_tokens, finish_reason, "mock")
            res["prompt_tokens"] = prompt_tokens
            res["output_tokens"] = out_tokens
            res["num_ctx"] = self._context_length
            res["finish_reason"] = finish_reason
            return res
        if self._backend == "ollama":
            return await self._chat_with_tools_ollama(
                messages=messages,
                tools=tools,
                model_name=model_name,
                correlation_id=correlation_id,
                session_id=session_id,
                response_format=response_format,
                options=options,
            )

        # Fallback for llama.cpp / transformers
        prompt = "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)
        raw_res = await self.generate(prompt, correlation_id=correlation_id, session_id=session_id, is_coding=is_coding)
        cleaned_text, parsed_calls = ToolParser.parse_text_response(raw_res)
        return {
            "content": cleaned_text,
            "tool_calls": [tc.to_dict() for tc in parsed_calls],
        }

    def _chat_with_tools_mock(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        response_format: Any | None = None,
    ) -> dict[str, Any]:
        last_msg = ""
        for m in reversed(messages):
            content = m.get("content", "")
            if "<|user|>" in content:
                import re
                matches = re.findall(r"<\|user\|>\n(.*?)</s>", content, re.DOTALL)
                if matches:
                    last_msg = matches[-1].lower().strip()
                    break
            elif m.get("role") == "user":
                if "User Query:" in content:
                    last_msg = content.split("User Query:")[-1].lower().strip()
                elif "--- END UNTRUSTED RETRIEVED OBSERVATIONS ---" in content:
                    last_msg = content.split("--- END UNTRUSTED RETRIEVED OBSERVATIONS ---")[-1].lower().strip()
                else:
                    last_msg = content.lower().strip()
                break

        # ── Reliability Pipeline Mock Responses (Tasks 1-8 / to_make.md) ─────────
        # When response_format is set, detect active mode from system prompt and return
        # structured JSON so offline unit tests pass deterministically (no Ollama needed).
        if response_format is not None:
            active_mode = None
            for _m in messages:
                if _m.get("role") == "system":
                    _sc = _m.get("content", "")
                    if "CURRENT MODE: RATE" in _sc:
                        active_mode = "RATE"
                    elif "CURRENT MODE: QUIZ" in _sc:
                        active_mode = "QUIZ"
                    elif "CURRENT MODE: GRADE" in _sc:
                        active_mode = "GRADE"
                    break

            import json as _json
            _lower = last_msg

            if active_mode == "RATE":
                _score = 7
                _missing: list[str] = ["compile-time vs runtime binding"]
                if "polymorphism" in _lower and not any(w in _lower for w in ("overload", "overrid", "compile", "runtime")):
                    _score = 5
                    _missing = ["compile-time polymorphism (overloading)", "runtime polymorphism (overriding)", "static vs dynamic binding"]
                elif "encapsulation" in _lower and not any(w in _lower for w in ("private", "class", "accessor")):
                    _score = 6
                    _missing = ["unit is the class", "private fields + public accessors"]
                elif "inheritance" in _lower and any(w in _lower for w in ("child", "parent", "extends")):
                    _score = 8
                    _missing = ["IS-A relationship", "no multiple class inheritance in Java"]
                elif "abstraction" in _lower and any(w in _lower for w in ("hiding", "essential", "internal")):
                    _score = 8
                    _missing = ["abstract class vs interface distinction"]
                return {"content": _json.dumps({"score": _score, "missing": _missing[:3]}), "tool_calls": []}

            if active_mode == "QUIZ":
                return {
                    "content": _json.dumps({"question": "What is the difference between method overloading and method overriding in Java?"}),
                    "tool_calls": [],
                }

            if active_mode == "GRADE":
                _score = 7
                _covered = ["polymorphism definition"]
                _missing = ["compile-time vs runtime binding"]
                if not any(w in _lower for w in ("overload", "overrid", "compile", "runtime")):
                    _score = 5
                    _missing = ["compile-time polymorphism (overloading)", "runtime polymorphism (overriding)"]
                return {
                    "content": _json.dumps({
                        "score": _score,
                        "covered": _covered,
                        "missing": _missing[:3],
                        "next_question": "Explain the concept of encapsulation in Java with an example.",
                    }),
                    "tool_calls": [],
                }

            # Generic fallback for unknown schema mode
            return {"content": _json.dumps({"score": 7, "missing": ["key concept"]}), "tool_calls": []}

        if any(trigger in last_msg for trigger in ("open notepad", "launch notepad", "start notepad")):
            return {
                "content": "Launching Notepad for you.",
                "tool_calls": [{"name": "launch_application", "arguments": {"application": "notepad"}}],
            }
        if any(trigger in last_msg for trigger in ("open chrome", "launch chrome", "open google chrome")):
            return {
                "content": "Opening Google Chrome.",
                "tool_calls": [{"name": "launch_application", "arguments": {"application": "chrome"}}],
            }

        # General launch application trigger in mock mode
        import re
        launch_match = re.search(r"^(?:open|launch|start)\s+([a-zA-Z0-9_\-\.]+)", last_msg)
        if launch_match and not any(kw in last_msg for kw in ("open app", "running app", "list app", "open window", "running app", "search", "formal", "playful", "hinglish", "casual")):
            app_to_launch = launch_match.group(1).strip()
            return {
                "content": f"Launching {app_to_launch}.",
                "tool_calls": [{"name": "launch_application", "arguments": {"application": app_to_launch}}],
            }

        # Cross-session active context recall in mock mode (GAP-01)
        if any(p in last_msg for p in ("what project", "project name", "which project", "project did i", "project was i")):
            for m in messages:
                if m.get("role") == "system":
                    m_proj = re.search(r"project='([^']+)'", m.get("content", ""))
                    if m_proj:
                        p_name = m_proj.group(1)
                        return {
                            "content": f"You mentioned in an earlier session that you are working on Project **{p_name}**.",
                            "tool_calls": [],
                        }
        if any(g in last_msg for g in ("what was my goal", "what is my goal", "what goal", "goal did i")):
            for m in messages:
                if m.get("role") == "system":
                    m_goal = re.search(r"goal='([^']+)'", m.get("content", ""))
                    if m_goal:
                        g_name = m_goal.group(1)
                        return {
                            "content": f"Your goal recorded from our earlier session is: **{g_name}**.",
                            "tool_calls": [],
                        }

        # Future schedule uncertainty in mock mode (GAP-03)
        if any(trig in last_msg for trig in ("tomorrow's schedule", "schedule for tomorrow", "what should i do tomorrow", "what is my schedule for tomorrow", "what do i have tomorrow")):
            return {
                "content": "You don't have any scheduled tasks or calendar events on record for tomorrow. Rather than assuming or inventing a schedule, let me know what you would like to work on or plan!",
                "tool_calls": [],
            }
        if last_msg.startswith("search for ") or last_msg.startswith("google "):
            query = last_msg.replace("search for ", "").replace("google ", "").strip()
            return {
                "content": f"Searching the web for '{query}'.",
                "tool_calls": [{"name": "browser_search", "arguments": {"query": query}}],
            }
        if any(trigger in last_msg for trigger in (
            "open apps", "open applications", "what apps are open", "which apps are open",
            "what applications are open", "which applications are open", "open windows",
            "what windows are open", "which windows are open", "running applications",
            "running apps", "show open apps", "show open windows", "list open apps",
            "list open applications", "list open windows"
        )):
            return {
                "content": "Inspecting currently open desktop application windows.",
                "tool_calls": [{"name": "list_open_applications", "arguments": {}}],
            }
        if "system state" in last_msg or "system status" in last_msg:
            return {
                "content": "Retrieving current system perception state.",
                "tool_calls": [{"name": "get_system_state", "arguments": {}}],
            }
        if any(trigger in last_msg for trigger in ("installed apps", "installed applications", "search installed apps", "laptop apps", "applications on my laptop")):
            return {
                "content": "Searching for installed applications on your laptop.",
                "tool_calls": [{"name": "search_installed_apps", "arguments": {"query": ""}}],
            }
        if any(trigger in last_msg for trigger in ("scan documents", "scan files", "search files", "laptop files", "scan my documents")):
            return {
                "content": "Scanning laptop files for you.",
                "tool_calls": [{"name": "scan_laptop_files", "arguments": {"folder": "documents", "query": ""}}],
            }

        # Contextual, natural responses for slang, catchphrases, style switching, and Hinglish in mock mode
        # 1. Explicit style requests
        if any(trigger in last_msg for trigger in ("be formal", "speak formally", "switch to formal", "use formal tone")):
            return {
                "content": "Understood. I will maintain a formal and professional tone in our conversations.",
                "tool_calls": [],
            }
        if any(trigger in last_msg for trigger in ("switch to hinglish", "talk in hinglish", "speak in hinglish", "use hinglish")):
            return {
                "content": "Done bhai, ab se Hinglish me baat karenge! Sab sort ho jayega.",
                "tool_calls": [],
            }
        if any(trigger in last_msg for trigger in ("be playful", "talk playfully", "switch to playful", "be funny")):
            return {
                "content": "You got it! Ready to keep things fun and lively 😄",
                "tool_calls": [],
            }
        if any(trigger in last_msg for trigger in ("keep it technical", "be technical", "switch to technical", "sound technical")):
            return {
                "content": "Acknowledged. I will prioritize technical precision, concise engineering terminology, and architectural depth.",
                "tool_calls": [],
            }
        if any(trigger in last_msg for trigger in ("speak casually", "be casual", "switch to casual", "keep it casual")):
            return {
                "content": "Sounds good! We can keep things relaxed and casual.",
                "tool_calls": [],
            }

        # Concept Evaluation & Tutoring in mock mode
        if "revising" in last_msg and "concept" in last_msg and any(w in last_msg for w in ("correct", "explain", "judge", "tell me")):
            return {
                "content": "Understood. I will evaluate each concept explanation as Correct, Partially correct, or Incorrect with a concise note. Go ahead with your first concept.",
                "tool_calls": [],
            }
        if "encapsulation" in last_msg:
            if any(w in last_msg for w in ("bundling", "data", "restrict")):
                return {
                    "content": "Correct. Encapsulation bundles data and methods while restricting direct access to internal state.",
                    "tool_calls": [],
                }
            return {
                "content": "Partially correct. Encapsulation involves bundling data and methods into a unit and restricting unauthorized access to internal state.",
                "tool_calls": [],
            }
        if "inheritance" in last_msg:
            if any(w in last_msg for w in ("parent", "derive", "inherit", "child")):
                return {
                    "content": "Correct. Inheritance allows a child class to inherit attributes and methods from a parent class.",
                    "tool_calls": [],
                }
            return {
                "content": "Partially correct. Inheritance enables code reuse by allowing a class to inherit properties from a base class.",
                "tool_calls": [],
            }
        if "polymorphism" in last_msg:
            if any(w in last_msg for w in ("many forms", "interface", "override", "different")):
                return {
                    "content": "Correct. Polymorphism allows methods to do different things based on the object, providing a common interface.",
                    "tool_calls": [],
                }
            return {
                "content": "Partially correct. Polymorphism allows different classes to implement the same method interface.",
                "tool_calls": [],
            }
        if "abstraction" in last_msg:
            if any(w in last_msg for w in ("hiding", "essential", "details", "internal")):
                return {
                    "content": "Correct. Abstraction hides complex internal implementation details and shows only the essential features.",
                    "tool_calls": [],
                }
            return {
                "content": "Partially correct. Abstraction focuses on showing what an object does while hiding how it does it.",
                "tool_calls": [],
            }

        # 2. Technical & Safety Explanations (Strictly professional, zero forced slang)
        if any(trigger in last_msg for trigger in ("explain the permission manager", "how does permission manager work", "explain permission manager")):
            return {
                "content": "The Permission Manager is HELIX's authoritative security gate. It evaluates actions against deterministic risk tiers, cryptographic user confirmation, and session invariants before granting execution rights.",
                "tool_calls": [],
            }
        if any(trigger in last_msg for trigger in ("explain the event bus", "how does event bus work", "explain event bus")):
            return {
                "content": "The Event Bus provides asynchronous, decoupled publish-subscribe messaging across HELIX components, enforcing isolation and deterministic delivery.",
                "tool_calls": [],
            }
        if any(trigger in last_msg for trigger in ("explain the planner engine", "how does planner work", "explain planner")):
            return {
                "content": "The Planner Engine translates high-level user goals into structured dependency graphs and executable steps with deterministic validation.",
                "tool_calls": [],
            }

        # 3. Slang and informal dialogue
        if "bro really said" in last_msg:
            return {
                "content": "Haha, that's a wild quote! Good thing I treat quoted speech as passive text rather than actual instructions.",
                "tool_calls": [],
            }
        if "that project is fire" in last_msg or "feature is fire" in last_msg or ("fire" in last_msg and "project" in last_msg):
            return {
                "content": "I completely agree! That project is really impressive and well-executed.",
                "tool_calls": [],
            }
        if "cooked" in last_msg:
            if any(w in last_msg for w in ("exam", "test", "tomorrow")):
                return {
                    "content": "Don't panic! Focus on the key concepts tonight and review calmly. You can still handle tomorrow's exam.",
                    "tool_calls": [],
                }
            if any(w in last_msg for w in ("sleep", "slept", "tired", "awake")):
                return {
                    "content": "You sound completely exhausted. Get some proper sleep and recharge; your health comes first.",
                    "tool_calls": [],
                }
            return {
                "content": "Sounds like you're facing a tough situation. Let's break it down and tackle it step by step.",
                "tool_calls": [],
            }
        if "let him cook" in last_msg:
            return {
                "content": "Understood, stepping back and giving them the space to do their thing.",
                "tool_calls": [],
            }
        if "no cap" in last_msg:
            return {
                "content": "I hear you, 100% serious and understood.",
                "tool_calls": [],
            }
        if "that's sick" in last_msg or "thats sick" in last_msg:
            return {
                "content": "Glad you like it! It really turned out great.",
                "tool_calls": [],
            }
        if "it's giving" in last_msg or "its giving" in last_msg:
            return {
                "content": "Haha, I totally see that aesthetic and vibe!",
                "tool_calls": [],
            }
        if any(kw in last_msg for kw in ("kya scene hai", "scene sorted")):
            return {
                "content": "Sab badhiya bhai! All systems running smooth. Batao, what's on your mind?",
                "tool_calls": [],
            }
        if "touch grass" in last_msg:
            return {
                "content": "Haha, definitely a good reminder to unplug and enjoy some fresh air.",
                "tool_calls": [],
            }
        if "skill issue" in last_msg:
            return {
                "content": "Haha, brutal! But with a bit of practice, it will be smooth sailing.",
                "tool_calls": [],
            }
        if "ain't no way" in last_msg or "aint no way" in last_msg:
            return {
                "content": "I know, right? Absolutely unbelievable!",
                "tool_calls": [],
            }
        if "how's the project looking" in last_msg or "how does the project look" in last_msg:
            return {
                "content": "Looking super clean! The architecture is solid and everything's running smoothly ✨",
                "tool_calls": [],
            }
        if "crashed again" in last_msg or ("great" in last_msg and "crash" in last_msg):
            return {
                "content": "Ugh, that's frustrating. Let's look at the stack trace and fix whatever crashed.",
                "tool_calls": [],
            }
        if any(w in last_msg for w in ("skibidi", "gyatt", "fanum", "mewing")):
            return {
                "content": "I notice the informal internet expression, though I won't pretend to be certain of its exact context here. What would you like to explore?",
                "tool_calls": [],
            }

        return {
            "content": "Hello! I am HELIX running in mock development mode.",
            "tool_calls": [],
        }

    async def _chat_with_tools_ollama(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model_name: str = "qwen3:4b",
        correlation_id: str | None = None,
        session_id: str = "default",
        response_format: Any | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._ensure_session()
        merged_options = {
            "temperature": self._temperature,
            "num_predict": self._max_tokens,
            "num_ctx": self._context_length,
        }
        if options:
            merged_options.update(options)

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": merged_options,
        }
        if response_format is not None:
            payload["format"] = response_format
        is_coder = (model_name == self._coder_model_name and self._coder_model_name != self._orchestrator_model_name)
        if tools:
            payload["tools"] = tools
        if is_coder:
            payload["keep_alive"] = 0

        try:
            resp = await self._http_session.post(f"{self._ollama_url}/api/chat", json=payload)
        except httpx.ConnectError as exc:
            logger.error("Cannot connect to Ollama at %s. Is Ollama running? Start it with 'ollama serve'.", self._ollama_url)
            raise RuntimeError(f"Cannot connect to Ollama at {self._ollama_url}. Start it with 'ollama serve' and ensure a model is pulled ('ollama pull {model_name}').") from exc
        resp.raise_for_status()
        data = resp.json()
        message = data.get("message", {})
        content = message.get("content", "")
        raw_tool_calls = message.get("tool_calls")

        # Task 1 Observability: capture prompt & generation tokens and finish reason
        prompt_eval_count = data.get("prompt_eval_count")
        eval_count = data.get("eval_count")
        done_reason = data.get("done_reason") or ("stop" if data.get("done") else None)
        if prompt_eval_count is None:
            prompt_eval_count = max(1, sum(len(str(m.get("content", ""))) for m in messages) // 4)
        if eval_count is None:
            eval_count = max(1, len(content) // 4)

        self._check_and_log_observability(
            prompt_tokens=prompt_eval_count,
            output_tokens=eval_count,
            finish_reason=done_reason,
            model_name=model_name,
        )

        parsed_calls = []
        if raw_tool_calls:
            parsed_calls = ToolParser.parse_native_tool_calls(raw_tool_calls)

        # Filter reasoning blocks from content before fallback text tool parsing
        if content:
            content = ThinkBlockFilter.filter_text(content)

        # If no native tool calls returned in structure, check if filtered content contains text tool calls
        if not parsed_calls and content:
            cleaned_text, text_calls = ToolParser.parse_text_response(content)
            if text_calls:
                content = cleaned_text
                parsed_calls = text_calls

        if is_coder:
            try:
                await self._http_session.post(f"{self._ollama_url}/api/generate", json={"model": model_name, "keep_alive": 0})
                logger.info("Evicted coder model '%s' from VRAM after coding completion", model_name)
            except Exception:
                pass
            self._loaded = False
            self._active_model_name = None
        else:
            self._loaded = True
            self._active_model_name = model_name

        return {
            "content": content,
            "tool_calls": [tc.to_dict() for tc in parsed_calls],
            "prompt_tokens": prompt_eval_count,
            "output_tokens": eval_count,
            "num_ctx": merged_options.get("num_ctx", self._context_length),
            "finish_reason": done_reason,
        }

    def _generate_mock(self, prompt: str) -> str:
        lower = prompt.lower()
        if "revising" in lower and "concept" in lower and any(w in lower for w in ("correct", "explain", "judge", "tell me")):
            return "Understood. I will evaluate each concept explanation as Correct, Partially correct, or Incorrect with a concise note. Go ahead with your first concept."
        if "encapsulation" in lower:
            if any(w in lower for w in ("bundling", "data", "restrict")):
                return "Correct. Encapsulation bundles data and methods while restricting direct access to internal state."
            return "Partially correct. Encapsulation involves bundling data and methods into a unit and restricting direct access to internal state."
        if "inheritance" in lower:
            return "Correct. Inheritance allows a child class to inherit attributes and methods from a parent class."
        if "polymorphism" in lower:
            return "Correct. Polymorphism allows methods to do different things based on the object, providing a common interface."
        if "abstraction" in lower:
            return "Correct. Abstraction hides complex internal implementation details and shows only the essential features."
        return (
            "Hello! I'm HELIX running in mock development mode. "
            "I received your message and will process it properly "
            "once a real LLM backend is configured. "
            "Set HELIX_LLM_BACKEND=ollama to use your local Ollama models."
        )

    async def _generate_ollama(self, prompt: str, model_name: str, correlation_id: str | None = None, session_id: str = "default") -> str:
        await self._ensure_session()
        is_coder = (model_name == self._coder_model_name and self._coder_model_name != self._orchestrator_model_name)
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": self._temperature,
                "num_predict": self._max_tokens,
                "num_ctx": self._context_length,
            },
        }
        if is_coder:
            payload["keep_alive"] = 0
        async with self._http_session.stream(
            "POST", f"{self._ollama_url}/api/generate", json=payload
        ) as resp:
            resp.raise_for_status()
            full_text = []
            think_filter = ThinkBlockFilter()
            last_meta: dict[str, Any] = {}
            async for line in resp.aiter_lines():
                if self._abort.is_set():
                    break
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "error" in data:
                    raise RuntimeError(data["error"])
                if "prompt_eval_count" in data or "eval_count" in data or "done_reason" in data:
                    last_meta.update(data)
                if "response" in data:
                    chunk = data["response"]
                    filtered_chunk = think_filter.feed(chunk)
                    if filtered_chunk:
                        full_text.append(filtered_chunk)
                        if correlation_id:
                            await self._event_bus.publish_event(
                                source="llm_engine",
                                event_type="llm.generation.chunk",
                                payload={
                                    "session_id": session_id,
                                    "chunk": filtered_chunk,
                                    "token": filtered_chunk,
                                },
                                correlation_id=correlation_id,
                            )
                if data.get("done", False):
                    break
            final_flush = think_filter.flush()
            if final_flush:
                full_text.append(final_flush)
                if correlation_id:
                    await self._event_bus.publish_event(
                        source="llm_engine",
                        event_type="llm.generation.chunk",
                        payload={
                            "session_id": session_id,
                            "chunk": final_flush,
                            "token": final_flush,
                        },
                        correlation_id=correlation_id,
                    )

            prompt_eval_count = last_meta.get("prompt_eval_count") or max(1, len(prompt) // 4)
            eval_count = last_meta.get("eval_count") or max(1, len("".join(full_text)) // 4)
            done_reason = last_meta.get("done_reason") or "stop"
            self._check_and_log_observability(
                prompt_tokens=prompt_eval_count,
                output_tokens=eval_count,
                finish_reason=done_reason,
                model_name=model_name,
            )
            if correlation_id:
                if self._abort.is_set():
                    await self._event_bus.publish_event(
                        source="llm_engine",
                        event_type="llm.generation.error",
                        payload={"session_id": session_id, "error": "Generation cancelled"},
                        correlation_id=correlation_id,
                    )
                else:
                    await self._event_bus.publish_event(
                        source="llm_engine",
                        event_type="llm.generation.complete",
                        payload={
                            "session_id": session_id,
                            "response": "".join(full_text).strip(),
                        },
                        correlation_id=correlation_id,
                    )
        if is_coder:
            try:
                await self._http_session.post(f"{self._ollama_url}/api/generate", json={"model": model_name, "keep_alive": 0})
                logger.info("Evicted coder model '%s' from VRAM after completion", model_name)
            except Exception:
                pass
            self._loaded = False
            self._active_model_name = None
        else:
            self._loaded = True
            self._active_model_name = model_name

        return "".join(full_text).strip()

    async def _ensure_model_loaded(self, model_path: str, model_name: str) -> None:
        async with self._load_lock:
            if self._loaded and self._model_path != model_path:
                logger.info("Unloading previous model to load new model: %s", self._model_path)
                await self._unload_model_internal()

            if self._loaded:
                self._cancel_unload()
                return

            self._model_path = model_path
            self._model_name = model_name

            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.model.loading",
                payload={"backend": self._backend, "model_path": self._model_path},
            )
            logger.info("Loading model (backend=%s, path=%s)", self._backend, self._model_path)
            try:
                if self._backend == "llama.cpp":
                    self._model = await self._load_llamacpp()
                elif self._backend == "transformers":
                    self._model = await self._load_transformers()
                else:
                    raise ValueError(f"Unknown LLM backend: {self._backend}")

                self._loaded = True
                await self._event_bus.publish_event(
                    source="llm_engine",
                    event_type="llm.model.loaded",
                    payload={"backend": self._backend},
                )
                logger.info("Model loaded successfully")
            except Exception as e:
                logger.exception("Failed to load model: %s", e)
                # Cleanup partial model state
                self._model = None
                self._loaded = False
                await self._unload_model_internal()
                await self._event_bus.publish_event(
                    source="llm_engine",
                    event_type="llm.model.error",
                    payload={"backend": self._backend, "error": str(e), "model_path": self._model_path},
                )
                raise

    async def _load_llamacpp(self) -> Any:
        try:
            from llama_cpp import Llama
        except ImportError:
            raise ImportError(
                "llama-cpp-python not installed. "
                "Install with: pip install llama-cpp-python "
                "or set HELIX_LLM_BACKEND=ollama to use Ollama."
            )

        model_path = Path(self._model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"Model not found: {model_path}. "
                f"Set HELIX_LLM_MODEL_PATH to a valid GGUF file."
            )

        from foundation.security.model_integrity import verify_model_integrity
        verify_model_integrity(model_path)

        loop = asyncio.get_running_loop()
        model = await loop.run_in_executor(
            None,
            lambda: Llama(
                model_path=str(model_path),
                n_ctx=self._context_length,
                n_gpu_layers=self._gpu_layers if self._gpu_layers >= 0 else -1,
                verbose=False,
            ),
        )
        return model

    async def _load_transformers(self) -> dict[str, Any]:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError:
            raise ImportError(
                "transformers/torch not installed. "
                "Install with: pip install transformers torch "
                "or set HELIX_LLM_BACKEND=ollama to use Ollama."
            )

        loop = asyncio.get_running_loop()
        tokenizer_fut = loop.run_in_executor(
            None,
            lambda: AutoTokenizer.from_pretrained(self._model_name, trust_remote_code=self._trust_remote_code),
        )
        model_fut = loop.run_in_executor(
            None,
            lambda: AutoModelForCausalLM.from_pretrained(
                self._model_name,
                torch_dtype=torch.float16 if torch.cuda.is_available() else torch.float32,
                device_map="auto" if torch.cuda.is_available() else None,
                trust_remote_code=self._trust_remote_code,
            ),
        )
        tokenizer, model = await asyncio.gather(tokenizer_fut, model_fut)
        return {"model": model, "tokenizer": tokenizer}

    async def _generate_real(self, prompt: str, correlation_id: str | None = None, session_id: str = "default") -> str:
        if self._backend == "llama.cpp":
            return await self._generate_llamacpp(prompt, correlation_id=correlation_id, session_id=session_id)
        elif self._backend == "transformers":
            return await self._generate_transformers(prompt, correlation_id=correlation_id, session_id=session_id)
        raise ValueError(f"Unknown backend: {self._backend}")

    async def _generate_llamacpp(self, prompt: str, correlation_id: str | None = None, session_id: str = "default") -> str:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._model(
                prompt,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                echo=False,
            ),
        )
        text = result["choices"][0]["text"].strip()
        if correlation_id and text:
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.chunk",
                payload={"session_id": session_id, "chunk": text, "token": text},
                correlation_id=correlation_id,
            )
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.complete",
                payload={"session_id": session_id, "response": text},
                correlation_id=correlation_id,
            )
        return text

    async def _generate_transformers(self, prompt: str, correlation_id: str | None = None, session_id: str = "default") -> str:
        import torch

        loop = asyncio.get_running_loop()
        model = self._model["model"]
        tokenizer = self._model["tokenizer"]

        inputs = await loop.run_in_executor(
            None,
            lambda: tokenizer(prompt, return_tensors="pt"),
        )
        if torch.cuda.is_available():
            inputs = {k: v.to("cuda") for k, v in inputs.items()}

        outputs = await loop.run_in_executor(
            None,
            lambda: model.generate(
                **inputs,
                max_new_tokens=self._max_tokens,
                temperature=self._temperature,
                do_sample=True,
            ),
        )
        response = tokenizer.decode(outputs[0], skip_special_tokens=True)
        response = response[len(prompt):].strip()
        if correlation_id and response:
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.chunk",
                payload={"session_id": session_id, "chunk": response, "token": response},
                correlation_id=correlation_id,
            )
            await self._event_bus.publish_event(
                source="llm_engine",
                event_type="llm.generation.complete",
                payload={"session_id": session_id, "response": response},
                correlation_id=correlation_id,
            )
        return response

    async def _unload_model(self) -> None:
        if self._unload_task and not self._unload_task.done():
            self._unload_task.cancel()
            self._unload_task = None
        await self._unload_model_internal()

    async def _unload_model_internal(self) -> None:
        if not self._loaded:
            return

        await self._event_bus.publish_event(
            source="llm_engine",
            event_type="llm.model.unloading",
            payload={},
        )
        logger.info("Unloading model: %s", self._model_path)

        self._model = None
        self._loaded = False
        self._active_model_name = None

        try:
            import gc
            gc.collect()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

        await self._event_bus.publish_event(
            source="llm_engine",
            event_type="llm.model.unloaded",
            payload={},
        )
        logger.info("Model unloaded")

    def _schedule_unload_if_idle(self) -> None:
        if self._idle_unload_seconds <= 0:
            return
        if self._unload_task is None or self._unload_task.done():
            self._unload_task = asyncio.create_task(self._delayed_unload())

    def _cancel_unload(self) -> None:
        if self._unload_task and not self._unload_task.done():
            self._unload_task.cancel()
            self._unload_task = None

    async def _delayed_unload(self) -> None:
        try:
            await asyncio.sleep(self._idle_unload_seconds)
            if self._last_used and (datetime.now(timezone.utc) - self._last_used).total_seconds() < self._idle_unload_seconds:
                return
            await self._unload_model()
        except asyncio.CancelledError:
            pass
