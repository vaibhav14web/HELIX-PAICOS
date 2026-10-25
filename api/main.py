import os
import json
import base64
import asyncio
import time
from datetime import datetime, timezone
import re
import inspect
from contextlib import asynccontextmanager
from typing import Any, Callable

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from pathlib import Path
import uuid
from uuid import UUID

load_dotenv(Path(__file__).parent.parent / ".env")

from foundation.event_bus.event_bus import EventBus, HelixEvent
from foundation.config_manager.config_manager import ConfigManager
from foundation.storage_manager.storage_manager import StorageManager
from foundation.logger.logger import HelixLogger
from foundation.permission_manager.permission_manager import PermissionManager
from foundation.auth import (
    authenticate_token,
    is_allowed_origin,
    get_allowed_origins,
    AuthenticatedPrincipal,
    get_identity_service,
)
from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
from foundation.storage_manager.backup_restore import (
    get_backup_restore_manager,
    BackupError,
    BackupAuthorizationError,
    BackupIntegrityError,
)
from memory.conversation_memory.conversation_memory import ConversationMemory
from memory.preference_memory.preference_memory import PreferenceMemory
from memory.work_memory.work_memory import WorkMemory
from memory.explainability_engine.explainability_engine import ExplainabilityEngine
from memory.warm_memory.warm_memory import WarmMemory
from memory.warm_memory.models import WarmIndexRequest, WarmMemoryEntriesResponse, WarmSearchRequest, WarmSearchResponse
from core_ai.llm_engine.llm_engine import LLMEngine
from core_ai.conversation_engine.conversation_engine import ConversationEngine
from core_ai.user_model.user_model_engine import UserModelEngine, UserModel
from memory.episodic_memory.episodic_memory import EpisodicMemory, Episode
from memory.procedural_memory.procedural_memory import ProceduralMemory, ProceduralWorkflow, ProceduralStep
from core_ai.reflection_engine.reflection_engine import ReflectionEngine
from core_ai.voice_engine.voice_engine import VoiceEngine
from core_ai.wake_word_engine.wake_word_engine import WakeWordEngine
from orchestrator.orchestrator import Orchestrator

from context.context_aggregator.context_aggregator import ContextAggregator
from context.browser_engine.browser_engine import BrowserEngine
from context.file_intelligence_engine.file_intelligence_engine import FileIntelligenceEngine
from context.project_engine.project_engine import ProjectEngine
from context.system_monitor_engine.system_monitor_engine import SystemMonitorEngine

from action.planner_engine.planner_engine import PlannerEngine
from action.automation_engine.automation_engine import AutomationEngine
from action.action_executor.action_executor import ActionExecutor
from action.productivity_engine.productivity_engine import ProductivityEngine
from action.timetable_scheduler.timetable_scheduler import TimetableScheduler
from companion.coding_companion import CodingCompanion
from companion.learning_companion import LearningCompanion
from companion.productivity_companion import ProductivityCompanion
from core_ai.self_model.self_model_engine import SelfModelEngine
from foundation.world_model.world_model_engine import WorldModelEngine
from action.capability_registry.capability_registry import CapabilityRegistry
from action.application_discovery.app_discovery_engine import ApplicationDiscoveryEngine
from foundation.project_registry.project_registry import ProjectRegistry
from action.action_verification.action_verification import ActionVerificationEngine
from core_ai.goal_manager.goal_manager import GoalManager
from foundation.observability.observability_engine import ObservabilityEngine
from orchestrator.ecs.executive_control_system import ExecutiveControlSystem, ExecutiveState


class SessionStartRequest(BaseModel):
    session_id: str


class ProjectRequest(BaseModel):
    session_id: str
    project: str


class ConversationStoreRequest(BaseModel):
    session_id: str
    role: str
    content: str
    companion_id: str | None = None


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    request_id: str | None = None


class PreferenceStoreRequest(BaseModel):
    key: str
    value: Any
    category: str | None = None
    source: str | None = None


class TaskRequest(BaseModel):
    session_id: str
    task: str


class ContextUpdateRequest(BaseModel):
    session_id: str
    updates: dict[str, Any]


class ExplainLogRequest(BaseModel):
    action: str
    reasoning: str
    benefits: list[str] | None = None
    risks: list[str] | None = None
    alternatives: list[str] | None = None
    rationale: str | None = None
    source_module: str | None = None
    outcome: str | None = None


class ExplainQueryRequest(BaseModel):
    decision_id: str | None = None
    action: str | None = None
    limit: int | None = 10


class TraitObserveRequest(BaseModel):
    category: str
    trait: str
    value: Any
    confidence: float | None = None
    source: str = "api"


class UserContextUpdateRequest(BaseModel):
    current_goal: str | None = None
    active_project: str | None = None
    recent_topic: str | None = None


class EpisodeCreateRequest(BaseModel):
    intent: str
    actions_taken: list[Any] = []
    outcome: str = "success"
    user_feedback: str | None = None
    reflection_notes: str = ""
    tags: list[str] = []


class WorkflowCreateRequest(BaseModel):
    workflow_id: str | None = None
    name: str = "custom_workflow"
    trigger_intent: str | None = None
    intent_pattern: str | None = None
    keywords: list[str] = []
    steps: list[dict[str, Any]] = []
    project_scope: str | None = None
    confidence: float = 0.6
    user_verified: bool = False


class ReflectionTriggerRequest(BaseModel):
    session_id: str = "default"
    focus: str | None = None
    notes: str | None = None


event_bus = EventBus()
config_manager = ConfigManager()
storage_manager = StorageManager()
logger = HelixLogger()
perm_manager: PermissionManager | None = None
conv_mem: ConversationMemory | None = None
pref_mem: PreferenceMemory | None = None
work_mem: WorkMemory | None = None
expl_engine: ExplainabilityEngine | None = None
warm_mem: WarmMemory | None = None
user_model_engine: UserModelEngine | None = None
episodic_mem: EpisodicMemory | None = None
procedural_mem: ProceduralMemory | None = None
reflection_engine: ReflectionEngine | None = None
llm_engine: LLMEngine | None = None
conv_engine: ConversationEngine | None = None
voice_engine: VoiceEngine | None = None
wake_word_engine: WakeWordEngine | None = None
orch: Orchestrator | None = None
context_aggregator: ContextAggregator | None = None
planner_engine: PlannerEngine | None = None
automation_engine: AutomationEngine | None = None
action_executor: ActionExecutor | None = None
productivity_engine: ProductivityEngine | None = None
timetable_scheduler: TimetableScheduler | None = None
coding_companion: CodingCompanion | None = None
learning_companion: LearningCompanion | None = None
productivity_companion: ProductivityCompanion | None = None
self_model_engine: SelfModelEngine | None = None
world_model_engine: WorldModelEngine | None = None
capability_registry: CapabilityRegistry | None = None
app_discovery_engine: ApplicationDiscoveryEngine | None = None
project_registry: ProjectRegistry | None = None
action_verifier: ActionVerificationEngine | None = None
goal_manager: GoalManager | None = None
observability_engine: ObservabilityEngine | None = None
ecs_engine: ExecutiveControlSystem | None = None


class LifespanManager:
    """
    Manages sequential startup tracking and reverse rollback/shutdown cleanup
    for HELIX lifespan initialization, ensuring resilience to partial failures.
    """

    def __init__(self, logger: Any = None):
        self._logger = logger
        self.started_cleanups: list[tuple[str, Callable[[], Any]]] = []
        self.cleanup_done: bool = False
        self.cleanup_errors: list[tuple[str, BaseException]] = []

    def _log_info(self, msg: str) -> None:
        try:
            if self._logger and hasattr(self._logger, "info"):
                self._logger.info(msg, module="system")
        except Exception:
            pass

    def _log_warning(self, msg: str) -> None:
        try:
            if self._logger and hasattr(self._logger, "warning"):
                self._logger.warning(msg, module="system")
        except Exception:
            pass

    def _log_error(self, msg: str, exc_info: bool = False) -> None:
        try:
            if self._logger and hasattr(self._logger, "error"):
                self._logger.error(msg, module="system", exc_info=exc_info)
        except Exception:
            pass

    def _log_debug(self, msg: str) -> None:
        try:
            if self._logger and hasattr(self._logger, "debug"):
                self._logger.debug(msg, module="system")
        except Exception:
            pass

    def register_cleanup(self, name: str, cleanup_fn: Callable[[], Any]) -> None:
        """Register a cleanup action for a successfully started component or subscription."""
        self.started_cleanups.append((name, cleanup_fn))

    async def start_component(
        self,
        name: str,
        comp: Any,
        custom_stop: Callable[[], Any] | None = None,
    ) -> None:
        """
        Start a component and, only upon successful completion of start(),
        register its cleanup callback to guarantee reverse rollback on failure.
        """
        if comp is None:
            return
        if hasattr(comp, "start") and callable(getattr(comp, "start")):
            res = comp.start()
            if inspect.isawaitable(res):
                await res
        # Only register cleanup if start succeeded without raising
        if custom_stop is not None:
            self.register_cleanup(name, custom_stop)
        elif hasattr(comp, "stop") and callable(getattr(comp, "stop")):
            self.register_cleanup(name, comp.stop)

    async def execute_cleanup(
        self,
        reason: str = "shutdown",
        reraise_cancellation: bool = True,
    ) -> list[tuple[str, BaseException]]:
        """
        Clean up all started components in reverse order.
        Idempotent: subsequent calls return immediately without duplicate work.
        Does not allow one cleanup error to prevent remaining cleanups.
        Preserves cancellation semantics if CancelledError is raised during cleanup.
        """
        if self.cleanup_done:
            return list(self.cleanup_errors)
        self.cleanup_done = True

        self._log_info(f"Executing lifespan cleanup ({reason})...")
        cancelled_exc: asyncio.CancelledError | None = None

        while self.started_cleanups:
            name, cleanup_fn = self.started_cleanups.pop()
            try:
                res = cleanup_fn()
                if inspect.isawaitable(res):
                    await res
                self._log_debug(f"Cleaned up: {name}")
            except asyncio.CancelledError as ce:
                self._log_warning(f"Cleanup of {name} was cancelled")
                self.cleanup_errors.append((name, ce))
                cancelled_exc = ce
            except Exception as exc:
                self._log_error(f"Error during cleanup of {name}: {exc}", exc_info=True)
                self.cleanup_errors.append((name, exc))
            except BaseException as be:
                if isinstance(be, (KeyboardInterrupt, SystemExit)):
                    raise
                self._log_error(f"BaseException during cleanup of {name}: {be}", exc_info=True)
                self.cleanup_errors.append((name, be))

        if self.cleanup_errors:
            self._log_error(
                f"Lifespan cleanup finished with {len(self.cleanup_errors)} error(s): "
                + ", ".join(f"{n}: {e}" for n, e in self.cleanup_errors)
            )
        else:
            self._log_info("Lifespan cleanup completed successfully")

        if cancelled_exc is not None and reraise_cancellation:
            raise cancelled_exc

        return list(self.cleanup_errors)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global conv_mem, pref_mem, work_mem, expl_engine, warm_mem, llm_engine, conv_engine, perm_manager
    global voice_engine, wake_word_engine, orch, context_aggregator
    global planner_engine, automation_engine, action_executor, productivity_engine, timetable_scheduler
    global coding_companion, learning_companion, productivity_companion
    global self_model_engine, world_model_engine, capability_registry, app_discovery_engine
    global project_registry, action_verifier, goal_manager, observability_engine, ecs_engine
    global user_model_engine, episodic_mem, procedural_mem, reflection_engine

    lifecycle = LifespanManager(logger=logger)
    app.state.lifecycle = lifecycle

    # 0. Fail-Closed Startup Policy Validation
    from foundation.auth.auth import validate_complete_startup_policy
    api_host = os.getenv("HELIX_API_HOST", "127.0.0.1")
    allow_remote = os.getenv("HELIX_ALLOW_REMOTE_ACCESS", "").lower() in ("true", "1")
    auth_token = os.getenv("HELIX_AUTH_TOKEN")
    allowed_hosts = os.getenv("HELIX_ALLOWED_HOSTS")
    trusted_proxies = os.getenv("HELIX_TRUSTED_PROXIES")

    policy_valid, policy_reason = validate_complete_startup_policy(
        host=api_host,
        allow_remote=allow_remote,
        token=auth_token,
        allowed_hosts_raw=allowed_hosts,
        trusted_proxies_raw=trusted_proxies,
    )
    if not policy_valid:
        logger.error(f"Startup policy validation failed: {policy_reason}")
        raise RuntimeError(f"Startup policy violation: {policy_reason}")

    def _sub(event_type: str, handler: Any) -> None:
        event_bus.subscribe(event_type, handler)
        lifecycle.register_cleanup(
            f"sub:{event_type}",
            lambda et=event_type, h=handler: event_bus.unsubscribe(et, h),
        )

    started_ok = False
    try:
        await lifecycle.start_component("config_manager", config_manager)
        await lifecycle.start_component("storage_manager", storage_manager)
        await lifecycle.start_component("logger", logger)

        mem_path = storage_manager.runtime("memory")
        user_model_path = storage_manager.runtime("user_model")

        perm_manager = PermissionManager(event_bus)
        conv_mem = ConversationMemory(event_bus, storage_path=str(mem_path))
        pref_mem = PreferenceMemory(event_bus, storage_path=str(mem_path))
        work_mem = WorkMemory(event_bus, storage_path=str(mem_path))
        expl_engine = ExplainabilityEngine(event_bus, storage_path=str(mem_path))
        warm_mem = WarmMemory(event_bus, storage_path=str(mem_path))
        user_model_engine = UserModelEngine(event_bus, storage_path=str(user_model_path))
        episodic_mem = EpisodicMemory(event_bus, storage_path=str(mem_path))
        procedural_mem = ProceduralMemory(event_bus, storage_path=str(mem_path))
        reflection_engine = ReflectionEngine(event_bus)
        llm_engine = LLMEngine(event_bus)
        conv_engine = ConversationEngine(
            event_bus,
            user_model_engine=user_model_engine,
            episodic_memory=episodic_mem,
        )
        voice_engine = VoiceEngine(event_bus)
        wake_word_engine = WakeWordEngine(event_bus, ring_buffer=voice_engine.ring_buffer)
        ecs_engine = ExecutiveControlSystem(event_bus)
        orch = Orchestrator(event_bus, ecs=ecs_engine)

        # Context modules with real implementation
        context_aggregator = ContextAggregator(event_bus)
        browser_engine = BrowserEngine(event_bus)
        file_intelligence_engine = FileIntelligenceEngine(event_bus)
        project_engine = ProjectEngine(event_bus)
        system_monitor_engine = SystemMonitorEngine(event_bus)

        # Action modules
        planner_engine = PlannerEngine(
            event_bus,
            procedural_memory=procedural_mem,
        )
        automation_engine = AutomationEngine(event_bus)
        action_executor = ActionExecutor(event_bus)
        productivity_engine = ProductivityEngine(event_bus)
        timetable_scheduler = TimetableScheduler(event_bus)

        await lifecycle.start_component("perm_manager", perm_manager)
        await lifecycle.start_component("conv_mem", conv_mem)
        await lifecycle.start_component("pref_mem", pref_mem)
        await lifecycle.start_component("work_mem", work_mem)
        await lifecycle.start_component("expl_engine", expl_engine)
        await lifecycle.start_component("warm_mem", warm_mem)
        await lifecycle.start_component("user_model_engine", user_model_engine)
        await lifecycle.start_component("episodic_mem", episodic_mem)
        await lifecycle.start_component("procedural_mem", procedural_mem)
        await lifecycle.start_component("reflection_engine", reflection_engine)
        await lifecycle.start_component("llm_engine", llm_engine)
        await lifecycle.start_component("conv_engine", conv_engine)
        await lifecycle.start_component("voice_engine", voice_engine)
        await lifecycle.start_component("wake_word_engine", wake_word_engine)
        await lifecycle.start_component("ecs_engine", ecs_engine)
        await lifecycle.start_component("orch", orch)

        await lifecycle.start_component("context_aggregator", context_aggregator)
        await lifecycle.start_component("browser_engine", browser_engine)
        await lifecycle.start_component("file_intelligence_engine", file_intelligence_engine)
        await lifecycle.start_component("project_engine", project_engine)
        await lifecycle.start_component("system_monitor_engine", system_monitor_engine)

        await lifecycle.start_component("planner_engine", planner_engine)
        await lifecycle.start_component("automation_engine", automation_engine)
        await lifecycle.start_component("action_executor", action_executor)
        await lifecycle.start_component("productivity_engine", productivity_engine)
        await lifecycle.start_component("timetable_scheduler", timetable_scheduler)

        self_model_engine = SelfModelEngine(event_bus)
        world_model_engine = WorldModelEngine(event_bus)
        capability_registry = CapabilityRegistry()
        app_discovery_engine = ApplicationDiscoveryEngine()
        project_registry = ProjectRegistry()
        action_verifier = ActionVerificationEngine(event_bus)
        goal_manager = GoalManager(event_bus)
        if conv_engine:
            if hasattr(conv_engine, "set_goal_manager"):
                conv_engine.set_goal_manager(goal_manager)
            if hasattr(conv_engine, "set_work_memory"):
                conv_engine.set_work_memory(work_mem)
            if hasattr(conv_engine, "set_productivity_engine"):
                conv_engine.set_productivity_engine(productivity_engine)
        observability_engine = ObservabilityEngine(event_bus)

        await lifecycle.start_component("goal_manager", goal_manager)
        await lifecycle.start_component("self_model_engine", self_model_engine)
        await lifecycle.start_component("world_model_engine", world_model_engine)

        def _stop_observability() -> None:
            if hasattr(observability_engine, "stop") and callable(getattr(observability_engine, "stop")):
                return observability_engine.stop()
            if hasattr(observability_engine, "_handle_action_metric"):
                event_bus.unsubscribe("action.verified", observability_engine._handle_action_metric)
            if hasattr(observability_engine, "_handle_tts_metric"):
                event_bus.unsubscribe("voice.tts.complete", observability_engine._handle_tts_metric)
            return None

        await lifecycle.start_component("observability_engine", observability_engine, custom_stop=_stop_observability)

        coding_companion = CodingCompanion(event_bus)
        learning_companion = LearningCompanion(event_bus)
        productivity_companion = ProductivityCompanion(event_bus)

        await lifecycle.start_component("coding_companion", coding_companion)
        await lifecycle.start_component("learning_companion", learning_companion)
        await lifecycle.start_component("productivity_companion", productivity_companion)

        # Wire real-time event → WebSocket bridge for chat UI
        async def _broadcast_listen_complete(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "transcription",
                text=event.payload.get("text", ""),
                session_id=event.payload.get("session_id", "default"),
            )

        async def _broadcast_tts(event: HelixEvent) -> None:
            corr_id = event.correlation_id
            req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
            await _event_broadcaster.broadcast(
                "response",
                text=event.payload.get("text", ""),
                session_id=event.payload.get("session_id", "default"),
                correlation_id=corr_id,
                request_id=req_id,
                audio=event.payload.get("audio"),
                audio_id=event.payload.get("audio_id"),
            )

        async def _broadcast_llm_chunk(event: HelixEvent) -> None:
            corr_id = event.correlation_id
            req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
            await _event_broadcaster.broadcast(
                "llm_chunk",
                text=event.payload.get("chunk", "") or event.payload.get("token", ""),
                session_id=event.payload.get("session_id", "default"),
                correlation_id=corr_id,
                request_id=req_id,
            )

        async def _broadcast_permission_needed(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "permission_needed",
                **event.payload,
                session_id=event.payload.get("session_id", "default"),
            )

        async def _broadcast_automation_completed(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "automation_completed",
                **event.payload,
                session_id=event.payload.get("session_id", "default"),
            )

        async def _broadcast_automation_failed(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "automation_failed",
                error=event.payload.get("error", ""),
                session_id=event.payload.get("session_id", "default"),
            )

        async def _broadcast_permission_granted(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "permission_granted",
                id=event.payload.get("id", ""),
                session_id=event.payload.get("session_id", "default"),
            )

        async def _broadcast_permission_denied(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "permission_denied",
                id=event.payload.get("id", ""),
                session_id=event.payload.get("session_id", "default"),
            )

        async def _broadcast_tts_start(event: HelixEvent) -> None:
            corr_id = event.correlation_id
            req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
            await _event_broadcaster.broadcast(
                "voice_speaking_start",
                session_id=event.payload.get("session_id", "default"),
                correlation_id=corr_id,
                request_id=req_id,
            )

        async def _broadcast_tts_complete(event: HelixEvent) -> None:
            corr_id = event.correlation_id
            req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
            await _event_broadcaster.broadcast(
                "voice_speaking_stop",
                session_id=event.payload.get("session_id", "default"),
                correlation_id=corr_id,
                request_id=req_id,
            )

        async def _broadcast_goal_created(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "goal_created",
                **event.payload,
            )

        async def _broadcast_goal_updated(event: HelixEvent) -> None:
            await _event_broadcaster.broadcast(
                "goal_updated",
                **event.payload,
            )

        _sub("voice.listen.complete", _broadcast_listen_complete)
        _sub("voice.tts", _broadcast_tts)
        _sub("voice.tts.start", _broadcast_tts_start)
        _sub("voice.tts.complete", _broadcast_tts_complete)
        _sub("llm.generation.chunk", _broadcast_llm_chunk)
        _sub("automation.permission_needed", _broadcast_permission_needed)
        _sub("automation.completed", _broadcast_automation_completed)
        _sub("automation.failed", _broadcast_automation_failed)
        _sub("permission.granted", _broadcast_permission_granted)
        _sub("goal.created", _broadcast_goal_created)
        _sub("goal.updated", _broadcast_goal_updated)
        # Ensure legacy single-user conversation and preference data is migrated to the default owner partition
        try:
            from foundation.auth.principal import get_default_owner_principal
            from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
            default_owner = get_default_owner_principal()
            tsm = get_tenant_storage_manager()
            tsm.migrate_legacy_data(target_user_id=default_owner.user_id)
        except Exception as e:
            logger.warning("Single-user legacy data migration skipped or failed: %s", e)

        logger.info("HELIX fully initialized", module="system")
        started_ok = True

        yield
    except BaseException as exc:
        reason = f"runtime failure: {exc}" if started_ok else f"startup failure: {exc}"
        await lifecycle.execute_cleanup(reason, reraise_cancellation=False)
        raise
    finally:
        await lifecycle.execute_cleanup("normal shutdown", reraise_cancellation=True)


PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/auth/login", "/auth/register"}


class SecurityAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Allow CORS preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # 2. Strict Origin validation: Reject cross-origin browser abuse and RFC 6454 syntax errors
        origin = request.headers.get("origin")
        if origin:
            org = origin.strip()
            # RFC 6454 syntax check: no userinfo '@', query '?', fragment '#', or path '/'
            if "@" in org or "?" in org or "#" in org:
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Bad Request: Malformed Origin header"},
                )
            if "://" in org:
                scheme, rest = org.split("://", 1)
                if scheme.lower() not in ("http", "https"):
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Bad Request: Unsupported scheme in Origin"},
                    )
                if "/" in rest:
                    return JSONResponse(
                        status_code=400,
                        content={"detail": "Bad Request: Path prohibited in Origin header"},
                    )
            elif org.lower() != "null":
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Bad Request: Malformed Origin header"},
                )

            if not is_allowed_origin(org):
                logger.warning(f"Rejected cross-origin request from unauthorized origin: {origin}")
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Forbidden: Cross-origin request rejected"},
                )
        else:
            # Origin absent: check Referer fallback
            referer = request.headers.get("referer")
            if referer:
                import urllib.parse
                try:
                    parsed_ref = urllib.parse.urlsplit(referer)
                    if not parsed_ref.scheme or not parsed_ref.netloc:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Forbidden: Untrusted Referer header"},
                        )
                    ref_origin = f"{parsed_ref.scheme}://{parsed_ref.netloc}".lower()
                    if not is_allowed_origin(ref_origin):
                        logger.warning(f"Rejected request from unauthorized referer origin: {ref_origin}")
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Forbidden: Untrusted Referer origin"},
                        )
                except Exception:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Forbidden: Malformed Referer header"},
                    )
            else:
                # Both Origin and Referer absent: require anti-CSRF header on state mutations
                if request.method in ("POST", "PUT", "DELETE", "PATCH"):
                    path = request.url.path
                    if path not in PUBLIC_PATHS:
                        has_csrf_header = (
                            "x-helix-token" in request.headers
                            or "x-requested-with" in request.headers
                            or "x-csrf-token" in request.headers
                            or "x-test-no-auth" in request.headers
                        )
                        if not has_csrf_header:
                            return JSONResponse(
                                status_code=403,
                                content={"detail": "Forbidden: State mutations require anti-CSRF header"},
                            )

        # 3. Allow public non-sensitive endpoints (health check)
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)

        # 4. Extract token from Authorization header or X-Helix-Token header (query-string credentials prohibited on HTTP)
        auth_header = request.headers.get("authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "x-helix-token" in request.headers:
            token = request.headers.get("x-helix-token", "").strip()

        # 5. Authenticate token and resolve principal (Loopback is NOT exempt)
        principal = authenticate_token(token)
        if not principal:
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing token"},
            )

        # 6. Attach authenticated principal to request state
        request.state.principal = principal
        return await call_next(request)


# Backwards compatibility alias
LocalAuthMiddleware = SecurityAuthMiddleware


def get_current_principal(request: Request) -> AuthenticatedPrincipal:
    """FastAPI dependency to retrieve the verified AuthenticatedPrincipal."""
    principal = getattr(request.state, "principal", None)
    if not principal or not principal.is_authenticated:
        raise HTTPException(status_code=401, detail="Authentication required")
    return principal


from starlette.types import ASGIApp, Scope, Receive, Send


class CatchAllOptionsMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "OPTIONS":
            headers = dict(scope.get("headers", []))
            has_origin = b"origin" in headers
            has_cors_method = b"access-control-request-method" in headers
            if has_origin and has_cors_method:
                await self.app(scope, receive, send)
                return
            response = Response(status_code=200)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class HostValidationMiddleware:
    """
    Pure ASGI middleware validating Host and Authority headers across both HTTP
    and WebSocket protocols before request routing or handshake acceptance.
    """
    def __init__(
        self,
        app: ASGIApp,
        allowed_hosts: list[str] | set[str] | None = None,
        trusted_proxies: list[str] | set[str] | None = None,
    ):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.trusted_proxies = trusted_proxies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers", [])
        client = scope.get("client")
        client_host = client[0] if client else None

        from foundation.auth.auth import evaluate_host_authority
        is_valid, reason, _ = evaluate_host_authority(
            headers,
            client_ip=client_host,
            allowed_hosts=self.allowed_hosts,
            trusted_proxies_raw=self.trusted_proxies,
        )

        if not is_valid:
            if scope["type"] == "http":
                response = JSONResponse(
                    status_code=400,
                    content={"detail": f"Invalid Host authority: {reason}"},
                )
                await response(scope, receive, send)
                return
            elif scope["type"] == "websocket":
                # WebSocket handshake policy violation rejection before acceptance
                await send({
                    "type": "websocket.close",
                    "code": 1008,
                    "reason": f"Forbidden: {reason}",
                })
                return

        await self.app(scope, receive, send)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Outermost security response header injection middleware with fail-closed
    exception interception to guarantee security headers across all responses.
    """
    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(f"Unhandled exception caught in SecurityHeadersMiddleware: {exc}", exc_info=True)
            response = JSONResponse(
                status_code=500,
                content={"detail": "Internal Server Error"},
            )
        from foundation.auth.auth import get_csp_header
        response.headers["x-content-type-options"] = "nosniff"
        response.headers["x-frame-options"] = "DENY"
        response.headers["content-security-policy"] = get_csp_header()
        return response


app = FastAPI(
    title="HELIX API",
    description="REST interface for HELIX",
    version="2.0.0",
    lifespan=lifespan,
)

cors_origins = get_allowed_origins()

# Registration order (outermost executes first on ingress, last on egress):
# 1. Innermost: Plain OPTIONS 200 synthesizer
app.add_middleware(CatchAllOptionsMiddleware)

# 2. Middle-Inner: Handles CORS preflights and injects CORS response headers
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-Helix-Token", "X-Request-ID", "X-Session-ID", "X-Correlation-ID"],
)

# 3. Middle: Origin Validation, Bearer Authentication & Anti-CSRF Protection
app.add_middleware(SecurityAuthMiddleware)

# 4. Middle-Outer: Host Header Authority & DNS-Rebinding Protection (Pure ASGI)
app.add_middleware(HostValidationMiddleware)

# 5. Outermost: Global Security Response Headers (CSP, nosniff, frame-options) & Fail-Closed Error Wrapping
app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    logger.error(f"Critical exception in ServerErrorMiddleware fallback: {exc}", exc_info=True)
    from foundation.auth.auth import get_csp_header
    response = JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error"},
    )
    response.headers["x-content-type-options"] = "nosniff"
    response.headers["x-frame-options"] = "DENY"
    response.headers["content-security-policy"] = get_csp_header()
    return response


# ── Event-to-WebSocket bridge for real-time chat updates ──


class EventBroadcaster:
    def __init__(self):
        # Maps WebSocket connection -> AuthenticatedPrincipal
        self._connections: dict[WebSocket, AuthenticatedPrincipal] = {}

    def connect(self, ws: WebSocket, principal: AuthenticatedPrincipal) -> None:
        self._connections[ws] = principal

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.pop(ws, None)

    async def broadcast(self, event_type: str, user_id: str | None = None, **payload: Any) -> None:
        dead: set[WebSocket] = set()
        effective_user = user_id or payload.get("user_id")
        msg = {"type": event_type, **payload}
        for ws, principal in list(self._connections.items()):
            # Filter user-specific events unless user is owner or system admin
            if effective_user and effective_user not in ("global", "default_owner"):
                if principal.user_id != effective_user and not (principal.is_owner() or principal.is_admin()):
                    continue
            try:
                await ws.send_json(msg)
            except Exception:
                dead.add(ws)
        for ws in dead:
            self._connections.pop(ws, None)


_event_broadcaster = EventBroadcaster()


async def _broadcast_tts(event: HelixEvent) -> None:
    corr_id = event.correlation_id
    req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
    await _event_broadcaster.broadcast(
        "response",
        text=event.payload.get("text", ""),
        session_id=event.payload.get("session_id", "default"),
        correlation_id=corr_id,
        request_id=req_id,
        audio=event.payload.get("audio"),
        audio_id=event.payload.get("audio_id"),
    )


async def _broadcast_llm_chunk(event: HelixEvent) -> None:
    corr_id = event.correlation_id
    req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
    await _event_broadcaster.broadcast(
        "llm_chunk",
        text=event.payload.get("chunk", "") or event.payload.get("token", ""),
        session_id=event.payload.get("session_id", "default"),
        correlation_id=corr_id,
        request_id=req_id,
    )


async def _broadcast_tts_start(event: HelixEvent) -> None:
    corr_id = event.correlation_id
    req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
    await _event_broadcaster.broadcast(
        "voice_speaking_start",
        session_id=event.payload.get("session_id", "default"),
        correlation_id=corr_id,
        request_id=req_id,
    )


async def _broadcast_tts_complete(event: HelixEvent) -> None:
    corr_id = event.correlation_id
    req_id = corr_id.split("::gen::")[0] if corr_id and "::gen::" in corr_id else event.payload.get("request_id")
    await _event_broadcaster.broadcast(
        "voice_speaking_stop",
        session_id=event.payload.get("session_id", "default"),
        correlation_id=corr_id,
        request_id=req_id,
    )


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    # 1. Validate Origin if passed by browser (handshake rejection before accept)
    origin = websocket.headers.get("origin")
    if origin and not is_allowed_origin(origin):
        logger.warning(f"Rejected WebSocket connection from unauthorized origin: {origin}")
        await websocket.close(code=1008, reason="Forbidden: Unauthorized origin")
        return

    # 2. Evaluate Query-String Token Precedence
    query_token = websocket.query_params.get("token")
    if query_token:
        # Precedence 1: Query token provided
        principal = authenticate_token(query_token)
        if not principal:
            # Precedence 2: Invalid query token -> reject immediately before accept; NO quarantine
            logger.warning("Rejected WebSocket connection: invalid query token")
            await websocket.close(code=1008, reason="Unauthorized: Invalid token")
            return

        # Valid query token -> accept directly into authenticated state
        await websocket.accept()
        _event_broadcaster.connect(websocket, principal)
    else:
        # Precedence 3: Missing query token -> accept into Quarantine State
        await websocket.accept()
        # Enforce 5.0s quarantine timeout
        try:
            raw_frame = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("WebSocket quarantine timeout: client sent no auth frame within 5.0s")
            await websocket.close(code=1008, reason="Authentication timeout")
            return
        except WebSocketDisconnect:
            return
        except Exception:
            await websocket.close(code=1008, reason="Quarantine error")
            return

        # Check frame size (max 64KB)
        if len(raw_frame.encode("utf-8")) > 65536:
            await websocket.close(code=1009, reason="Message too large")
            return

        # Check JSON decoding
        try:
            auth_data = json.loads(raw_frame)
        except Exception:
            await websocket.close(code=1008, reason="Malformed frame: JSON decode error")
            return

        if not isinstance(auth_data, dict) or auth_data.get("type") != "auth":
            await websocket.close(code=1008, reason="Protocol breach: expected auth frame in quarantine")
            return

        inband_token = auth_data.get("token")
        principal = authenticate_token(inband_token) if inband_token else None
        if not principal:
            await websocket.close(code=1008, reason="Unauthorized: Invalid token")
            return

        # Authenticated! Promote socket and send confirmation
        await websocket.send_json({"type": "auth_ok"})
        _event_broadcaster.connect(websocket, principal)

    # 3. Authenticated Socket Message Loop
    try:
        while True:
            text = await websocket.receive_text()
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("type") == "auth":
                    # Duplicate auth frame safely ignored (WS-10)
                    await websocket.send_json({"type": "error", "message": "ignored"})
            except Exception:
                pass
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _event_broadcaster.disconnect(websocket)


# ── Multi-User Identity & Desktop Concurrency Lock ──

class RegisterRequest(BaseModel):
    username: str
    password: str
    display_name: str | None = None
    setup_token: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class DesktopLockRequest(BaseModel):
    timeout_seconds: float = 30.0


class DesktopAutomationLock:
    """
    Host-level concurrency lock protecting shared OS desktop resources.
    Serializes interactive GUI automation across independent tenants.
    """
    def __init__(self):
        self._lock = asyncio.Lock()
        self._holder: str | None = None
        self._acquired_at: float | None = None

    @property
    def is_locked(self) -> bool:
        return self._lock.locked()

    @property
    def holder(self) -> str | None:
        return self._holder

    async def acquire(self, user_id: str, timeout: float = 10.0) -> bool:
        try:
            await asyncio.wait_for(self._lock.acquire(), timeout=timeout)
            self._holder = user_id
            self._acquired_at = time.time()
            return True
        except asyncio.TimeoutError:
            return False

    def release(self, user_id: str) -> bool:
        if self._holder == user_id or user_id in ("global", "system", "default_owner"):
            self._holder = None
            self._acquired_at = None
            if self._lock.locked():
                self._lock.release()
            return True
        return False


_desktop_lock = DesktopAutomationLock()


@app.post("/auth/register", status_code=201)
async def auth_register(req: RegisterRequest, request: Request):
    svc = get_identity_service()
    setup_token = req.setup_token or request.headers.get("x-helix-setup-token")

    if svc.is_owner_unclaimed():
        if not setup_token:
            raise HTTPException(
                status_code=403,
                detail="Invalid or missing setup token. Unauthorized claim attempt.",
            )
        try:
            user = svc.claim_owner_bootstrap(
                username=req.username,
                password=req.password,
                setup_token=setup_token,
                display_name=req.display_name,
            )
        except PermissionError as pe:
            raise HTTPException(status_code=403, detail=str(pe))
        except ValueError as ve:
            raise HTTPException(status_code=400, detail=str(ve))
        except Exception as e:
            logger.error("Owner claim persistence failure: %s", e)
            raise HTTPException(status_code=500, detail="Internal Server Error")

        token, session = svc.create_session(user)
        return {
            "status": "registered",
            "user_id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "token": token,
            "expires_at": session.expires_at,
        }

    # Owner already claimed:
    if setup_token:
        raise HTTPException(status_code=403, detail="Owner has already been claimed.")

    allow_public = os.getenv("HELIX_ALLOW_PUBLIC_REGISTRATION", "").lower() in ("true", "1")
    if not allow_public:
        auth_header = request.headers.get("authorization", "")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else request.headers.get("x-helix-token", "").strip()
        principal = authenticate_token(token) if token else None
        if not principal or not (principal.is_owner() or principal.is_admin()):
            raise HTTPException(
                status_code=403,
                detail="Registration is closed. An authenticated owner session is required to create additional users on this HELIX instance.",
            )

    try:
        user = svc.register_user(req.username, req.password, display_name=req.display_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    token, session = svc.create_session(user)
    return {
        "status": "registered",
        "user_id": user.user_id,
        "username": user.username,
        "display_name": user.display_name,
        "token": token,
        "expires_at": session.expires_at,
    }


@app.post("/auth/login")
async def auth_login(req: LoginRequest, request: Request):
    svc = get_identity_service()
    client_id = getattr(request.client, "host", None)
    user, msg = svc.authenticate_user(req.username, req.password, client_identifier=client_id)
    if not user:
        if "locked" in msg.lower():
            raise HTTPException(status_code=429, detail=msg)
        raise HTTPException(status_code=401, detail=msg)

    token, session = svc.create_session(user)
    return {
        "status": "authenticated",
        "token": token,
        "expires_at": session.expires_at,
        "user": {
            "user_id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "roles": list(user.roles),
            "is_owner": user.is_owner,
            "is_admin": user.is_admin,
        }
    }


@app.post("/auth/logout")
async def auth_logout(request: Request):
    principal = getattr(request.state, "principal", None)
    if principal and principal.token:
        get_identity_service().revoke_session(principal.token)
    return {"status": "logged_out"}


@app.get("/auth/me")
async def auth_me(request: Request):
    principal = getattr(request.state, "principal", None)
    if not principal:
        raise HTTPException(status_code=401, detail="Unauthorized")
    user = get_identity_service().get_user(principal.user_id)
    return {
        "principal": {
            "user_id": principal.user_id,
            "username": principal.username,
            "roles": principal.roles,
            "is_owner": principal.is_owner(),
            "is_admin": principal.is_admin(),
        },
        "user": user.to_dict() if user else None,
    }


@app.post("/desktop/lock/acquire")
async def acquire_desktop_lock(req: DesktopLockRequest, request: Request):
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "anonymous"
    acquired = await _desktop_lock.acquire(user_id, timeout=req.timeout_seconds)
    if not acquired:
        raise HTTPException(status_code=409, detail=f"Desktop automation is currently locked by user '{_desktop_lock.holder}'")
    return {"status": "acquired", "user_id": user_id}


@app.post("/desktop/lock/release")
async def release_desktop_lock(request: Request):
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "anonymous"
    released = _desktop_lock.release(user_id)
    return {"status": "released" if released else "not_holder", "is_locked": _desktop_lock.is_locked}


@app.get("/health")
async def health():
    modules = [
        "config_manager",
        "storage_manager",
        "logger",
        "permission_manager",
        "event_bus",
        "conversation_memory",
        "preference_memory",
        "work_memory",
        "explainability_engine",
        "llm_engine",
        "conversation_engine",
        "voice_engine",
        "wake_word_engine",
        "orchestrator",
        "planner_engine",
        "automation_engine",
        "action_executor",
        "productivity_engine",
        "user_model_engine",
        "episodic_memory",
        "procedural_memory",
        "reflection_engine",
    ]
    state = orch.current_state if orch else "unknown"
    return {"status": "ok", "modules": modules, "state": state}


# ── Session ──


@app.post("/session/start")
async def session_start(req: SessionStartRequest):
    await event_bus.publish_event(
        source="api",
        event_type="session.start",
        payload={"session_id": req.session_id},
    )
    return {"session_id": req.session_id}


@app.post("/session/end")
async def session_end(req: SessionStartRequest):
    await event_bus.publish_event(
        source="api",
        event_type="session.end",
        payload={"session_id": req.session_id},
    )
    return {"session_id": req.session_id}


# ── Chat ──


@app.post("/chat")
@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    if conv_engine is None:
        return {"response": "OK", "session_id": req.session_id, "request_id": req.request_id}
    response = await conv_engine.chat(req.message, req.session_id, user_id=user_id, request_id=req.request_id)

    audio_b64 = None
    audio_id = None
    if response.strip() and voice_engine is not None:
        try:
            if getattr(voice_engine, "_tts_backend", "") != "mock":
                audio_bytes = await voice_engine.synthesize(response, session_id=req.session_id)
                if audio_bytes:
                    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                    audio_id = f"aud_{uuid.uuid4().hex[:12]}"
                    await _event_broadcaster.broadcast(
                        "chat_audio",
                        audio_id=audio_id,
                        audio=audio_b64,
                        text=response,
                        session_id=req.session_id,
                        request_id=req.request_id,
                    )
        except Exception:
            logger.exception("Failed to synthesize voice response")

    return {
        "response": response,
        "session_id": req.session_id,
        "request_id": req.request_id,
        "audio": audio_b64,
        "audio_id": audio_id,
    }


# ── Voice ──


class SpeakRequest(BaseModel):
    text: str
    session_id: str = "default"


@app.post("/speak")
async def speak(req: SpeakRequest):
    if voice_engine is None:
        raise HTTPException(status_code=503, detail="Voice engine not initialized")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    audio_b64 = None
    audio_id = None
    try:
        if getattr(voice_engine, "_tts_backend", "") != "mock":
            audio_bytes = await voice_engine.synthesize(req.text, session_id=req.session_id)
            if audio_bytes:
                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                audio_id = f"aud_{uuid.uuid4().hex[:12]}"
    except Exception as e:
        logger.exception("Failed to synthesize voice response")
        
    return {
        "status": "success" if audio_b64 else "unavailable",
        "session_id": req.session_id,
        "audio": audio_b64,
        "audio_id": audio_id,
    }


# ── Voice Profiles & Preview ──


@app.get("/voice/profiles")
async def get_voice_profiles(session_id: str = "default"):
    if voice_engine is None:
        raise HTTPException(status_code=503, detail="Voice engine not initialized")
    profiles = voice_engine.list_profiles(session_id=session_id)
    active_profile = voice_engine.get_profile(session_id=session_id)
    return {
        "status": "success",
        "profiles": profiles,
        "active_personality_id": active_profile.id,
        "active_profile": active_profile.id,
        "active_profile_id": active_profile.id,
        "tts_backend": getattr(voice_engine, "_tts_backend", "mock"),
    }


class VoiceProfileSelectRequest(BaseModel):
    profile_id: str
    session_id: str = "default"


@app.post("/voice/profile/select")
async def select_voice_profile(req: VoiceProfileSelectRequest, request: Request):
    if voice_engine is None:
        raise HTTPException(status_code=503, detail="Voice engine not initialized")
    clean_id = req.profile_id.strip().lower()
    if not voice_engine.profile_registry.is_valid_profile_id(clean_id):
        available = [p["id"] for p in voice_engine.list_profiles()]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid voice profile ID: '{req.profile_id}'. Available: {available}",
        )

    try:
        profile = voice_engine.set_active_profile(clean_id, session_id=req.session_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Persist in UserModelEngine if available
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    if user_model_engine is not None:
        try:
            user_model_engine.set_explicit_trait(
                "communication_style",
                "voice_profile",
                clean_id,
                confidence=1.0,
                user_id=user_id,
            )
        except Exception as e:
            logger.debug("Failed saving voice profile trait to user model: %s", e)

    # Persist in config.json
    try:
        from foundation.paths import get_config_dir
        config_path = get_config_dir() / "config.json"
        config_data = {}
        if config_path.is_file():
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        if "ui_settings" not in config_data or not isinstance(config_data["ui_settings"], dict):
            config_data["ui_settings"] = {}
        config_data["ui_settings"]["voice_profile"] = clean_id
        config_data["voice_profile"] = clean_id
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        logger.warning("Failed persisting voice profile to config.json: %s", e)

    return {
        "status": "success",
        "active_personality_id": profile.id,
        "active_profile": profile.id,
        "active_profile_id": profile.id,
        "profile": profile.to_dict(),
        "session_id": req.session_id,
    }


class VoicePreviewRequest(BaseModel):
    profile_id: str


@app.post("/voice/preview")
async def preview_voice_profile(req: VoicePreviewRequest):
    if voice_engine is None:
        raise HTTPException(status_code=503, detail="Voice engine not initialized")
    clean_id = req.profile_id.strip().lower()
    if not voice_engine.profile_registry.is_valid_profile_id(clean_id):
        available = [p["id"] for p in voice_engine.list_profiles()]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid voice profile ID: '{req.profile_id}'. Available: {available}",
        )

    profile = voice_engine.get_profile(clean_id)
    audio_b64 = None
    audio_id = None
    try:
        audio_bytes = await voice_engine.preview_voice(clean_id)
        if audio_bytes:
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
            audio_id = f"prev_{uuid.uuid4().hex[:12]}"
    except Exception as e:
        logger.exception("Failed to synthesize voice preview")
        raise HTTPException(status_code=500, detail=f"Preview synthesis failed: {e}")

    return {
        "status": "success",
        "profile_id": clean_id,
        "text": profile.preview_text,
        "audio": audio_b64,
        "audio_id": audio_id,
    }


@app.post("/voice/command")
async def voice_command(request: Request, file: UploadFile = File(...), session_id: str = Form("default")):
    if voice_engine is None or conv_engine is None:
        raise HTTPException(status_code=503, detail="Required engine not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    MAX_AUDIO_SIZE = 10 * 1024 * 1024
    audio_data = await file.read()
    if len(audio_data) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=413, detail="Audio file too large")
    await file.close()
    try:
        text = await voice_engine.transcribe(audio_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not text.strip():
        return {"text": "", "response": "", "audio": None}

    # Broadcast user transcription so connected clients know what user spoke
    await _event_broadcaster.broadcast(
        "transcription",
        text=text,
        session_id=session_id,
    )

    response = await conv_engine.chat(text, session_id, user_id=user_id)
    audio_b64 = None
    audio_id = None
    if response.strip():
        try:
            if getattr(voice_engine, "_tts_backend", "") != "mock":
                try:
                    audio_bytes = await voice_engine.synthesize(response, session_id=session_id)
                except TypeError:
                    audio_bytes = await voice_engine.synthesize(response)
                if audio_bytes:
                    audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                    audio_id = f"aud_{uuid.uuid4().hex[:12]}"
                    await _event_broadcaster.broadcast(
                        "chat_audio",
                        audio_id=audio_id,
                        audio=audio_b64,
                        text=response,
                        session_id=session_id,
                    )
        except Exception as e:
            logger.exception("Failed to synthesize voice response")

        if not audio_b64:
            await _event_broadcaster.broadcast(
                "response",
                text=response,
                session_id=session_id,
            )

    return {"text": text, "response": response, "session_id": session_id, "audio": audio_b64, "audio_id": audio_id}


@app.post("/voice/transcribe")
async def voice_transcribe(file: UploadFile = File(...), session_id: str = Form("default")):
    if voice_engine is None:
        raise HTTPException(status_code=503, detail="Voice engine not initialized")
    MAX_AUDIO_SIZE = 10 * 1024 * 1024
    audio_data = await file.read()
    if len(audio_data) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=413, detail="Audio file too large")
    await file.close()
    try:
        text = await voice_engine.transcribe(audio_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"text": text, "session_id": session_id}


@app.post("/voice/interrupt")
async def voice_interrupt():
    if voice_engine is None:
        raise HTTPException(status_code=503, detail="Voice engine not initialized")
    await event_bus.publish_event(
        source="api",
        event_type="voice.interrupt",
        payload={},
    )
    return {"status": "interrupted"}


class ChatStopRequest(BaseModel):
    session_id: str = "default"
    request_id: str | None = None


@app.post("/chat/stop")
async def chat_stop(req: ChatStopRequest):
    await event_bus.publish_event(
        source="api",
        event_type="llm.generate.stop",
        payload={"session_id": req.session_id, "request_id": req.request_id},
    )
    await event_bus.publish_event(
        source="api",
        event_type="voice.interrupt",
        payload={"session_id": req.session_id, "request_id": req.request_id},
    )
    return {"status": "stopped", "session_id": req.session_id, "request_id": req.request_id}


@app.websocket("/ws/voice")
async def voice_websocket(websocket: WebSocket):
    # 1. Validate Origin if passed by browser
    origin = websocket.headers.get("origin")
    if origin and not is_allowed_origin(origin):
        logger.warning(f"Rejected Voice WebSocket connection from unauthorized origin: {origin}")
        await websocket.close(code=1008, reason="Forbidden: Unauthorized origin")
        return

    # 2. Extract token from query params or headers
    token = websocket.query_params.get("token") or websocket.headers.get("x-helix-token")
    if not token:
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "sec-websocket-protocol" in websocket.headers:
            for proto in websocket.headers["sec-websocket-protocol"].split(","):
                p = proto.strip()
                if p.startswith("token."):
                    token = p[6:]

    principal = authenticate_token(token)
    if not principal:
        logger.warning("Rejected Voice WebSocket connection: missing or invalid authentication token")
        await websocket.close(code=1008, reason="Unauthorized: Invalid token")
        return

    await websocket.accept()
    session_id = websocket.query_params.get("session_id", "default")
    try:
        while True:
            data = await websocket.receive_bytes()
            if voice_engine is None or conv_engine is None:
                await websocket.send_json({"error": "Engine not ready"})
                continue
            text = await voice_engine.transcribe(data)
            if not text.strip():
                await websocket.send_json({"text": ""})
                continue
            await event_bus.publish_event(
                source="api",
                event_type="voice.listen.complete",
                payload={"session_id": session_id, "text": text},
            )
            await websocket.send_json({"text": text, "status": "processing"})
            response = await conv_engine.chat(text, session_id, user_id=principal.user_id)
            if response.strip():
                await voice_engine.speak(response, session_id)
            await websocket.send_json({"response": response})
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Voice WebSocket error")


# ── Project ──


@app.post("/project/set")
async def set_project(req: ProjectRequest):
    await event_bus.publish_event(
        source="api",
        event_type="memory.work.set_project",
        payload={"session_id": req.session_id, "project": req.project},
    )
    return {"session_id": req.session_id, "project": req.project}


# ── Conversation Memory ──


@app.post("/conversation/store")
async def conversation_store(req: ConversationStoreRequest, request: Request):
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    await event_bus.publish_event(
        source="api",
        event_type="memory.conversation.store",
        payload={
            "session_id": req.session_id,
            "user_id": user_id,
            "role": req.role,
            "content": req.content,
            "companion_id": req.companion_id or "default",
        },
    )
    return {"session_id": req.session_id, "role": req.role}


@app.get("/conversation/sessions")
async def conversation_sessions(request: Request):
    if conv_mem is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    sessions = await conv_mem.get_all_sessions(user_id=user_id)
    return {"sessions": sessions}


@app.get("/conversation/{session_id}")
async def conversation_get(session_id: str, request: Request, limit: int = 50):
    if conv_mem is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    entries = await conv_mem.get_session_history(session_id, limit, user_id=user_id)
    return {
        "session_id": session_id,
        "entries": [e.model_dump() for e in entries],
    }


@app.get("/conversation/search")
async def conversation_search(query: str, request: Request, limit: int = 10):
    if conv_mem is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    entries = await conv_mem.search_entries(query, limit, user_id=user_id)
    return {"query": query, "results": [e.model_dump() for e in entries]}


# ── Preferences ──


@app.post("/preference/store")
async def preference_store(req: PreferenceStoreRequest, request: Request):
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    await event_bus.publish_event(
        source="api",
        event_type="memory.preference.store",
        payload={
            "key": req.key,
            "user_id": user_id,
            "value": req.value,
            "category": req.category or "general",
            "source": req.source or "user",
        },
    )
    return {"key": req.key}


@app.get("/preference/{key}")
async def preference_get(key: str, request: Request):
    if pref_mem is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    entry = pref_mem.get(key, user_id=user_id)
    return {"key": key, "entry": entry.model_dump() if entry else None}


@app.get("/preferences")
async def preferences_get_all(request: Request, category: str | None = None):
    if pref_mem is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    if category:
        entries = pref_mem.get_all(category, user_id=user_id)
    else:
        entries = pref_mem.get_all(user_id=user_id)
    return {"category": category, "entries": [e.model_dump() for e in entries]}


# ── Work ──


@app.post("/work/task/add")
async def work_add_task(req: TaskRequest, request: Request):
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    await event_bus.publish_event(
        source="api",
        event_type="memory.work.add_task",
        payload={"session_id": req.session_id, "task": req.task, "user_id": user_id},
    )
    return {"session_id": req.session_id, "task": req.task}


@app.get("/work/context/{session_id}")
async def work_get_context(session_id: str, request: Request):
    if work_mem is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    session = work_mem.get_session(session_id, user_id=user_id)
    return {"session_id": session_id, "context": session.model_dump() if session else None}


@app.post("/work/context/update")
async def work_update_context(req: ContextUpdateRequest, request: Request):
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    await event_bus.publish_event(
        source="api",
        event_type="memory.work.update_context",
        payload={"session_id": req.session_id, "updates": req.updates, "user_id": user_id},
    )
    return {"session_id": req.session_id}


# ── Aggregated Context ──


@app.get("/context/aggregated/{session_id}")
async def get_aggregated_context(session_id: str, force: bool = False):
    if context_aggregator is None:
        raise HTTPException(status_code=503, detail="Context aggregator not initialized")
    
    # Check if cache needs refreshing
    now = time.time()
    if force or not context_aggregator.is_cache_fresh(now):
        await event_bus.publish_event(
            source="api",
            event_type="context.request",
            payload={"session_id": session_id, "force": force},
        )
        # Give engines a brief window to respond and ContextAggregator to update cache
        await asyncio.sleep(0.15)
        
    return {
        "session_id": session_id,
        "context": context_aggregator.get_consolidated_context(session_id),
    }


# ── Explainability ──


@app.post("/memory/explain/log")
async def explain_log(req: ExplainLogRequest):
    payload = {
        "action": req.action,
        "reasoning": req.reasoning,
        "benefits": req.benefits or [],
        "risks": req.risks or [],
        "alternatives": req.alternatives or [],
        "rationale": req.rationale or "",
        "source_module": req.source_module or "unknown",
        "outcome": req.outcome or "pending",
    }
    await event_bus.publish_event(
        source="api",
        event_type="memory.explain.log",
        payload=payload,
    )
    return {"action": req.action}


@app.post("/memory/explain/query")
async def explain_query(req: ExplainQueryRequest):
    if expl_engine is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    if req.decision_id:
        record = expl_engine.find_by_id(req.decision_id)
        return {"decision_id": req.decision_id, "record": record.model_dump() if record else None}
    if req.action:
        records = expl_engine.find_by_action(req.action, req.limit or 10)
    else:
        records = expl_engine.get_recent(req.limit or 10)
    return {"records": [r.model_dump() for r in records]}


# ── Warm Memory (Vector / RAG) ──


@app.post("/memory/warm/index")
async def memory_warm_index(req: WarmIndexRequest, request: Request):
    if warm_mem is None:
        raise HTTPException(status_code=503, detail="Warm memory not initialized")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    entry = await warm_mem.index_text(
        text=req.text,
        category=req.category,
        metadata=req.metadata,
        entry_id=req.entry_id,
        user_id=user_id,
    )
    return {"status": "indexed", "id": entry.id, "category": entry.category}


@app.post("/memory/warm/search", response_model=WarmSearchResponse)
async def memory_warm_search(req: WarmSearchRequest, request: Request):
    if warm_mem is None:
        raise HTTPException(status_code=503, detail="Warm memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    t0 = time.perf_counter()
    results = await warm_mem.search(
        query=req.query,
        limit=req.limit,
        min_score=req.min_score,
        category=req.category,
        user_id=user_id,
    )
    dur_ms = (time.perf_counter() - t0) * 1000
    return WarmSearchResponse(
        query=req.query,
        results=results,
        count=len(results),
        duration_ms=round(dur_ms, 2),
    )


@app.get("/memory/warm/stats")
async def memory_warm_stats(request: Request):
    if warm_mem is None:
        raise HTTPException(status_code=503, detail="Warm memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    return await warm_mem.get_stats(user_id=user_id)


@app.get("/memory/warm/entries", response_model=WarmMemoryEntriesResponse)
async def memory_warm_entries(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=64),
    category: str | None = Query(default=None, min_length=1, max_length=64),
):
    if warm_mem is None:
        raise HTTPException(status_code=503, detail="Memory not initialized")
    if cursor and not cursor.isdecimal():
        try:
            UUID(cursor)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid memory page cursor")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    entries, next_cursor = await warm_mem.list_entries(
        user_id=user_id,
        limit=limit,
        cursor=cursor,
        category=category,
    )
    total_count = await warm_mem.count_entries(user_id=user_id, category=category)
    return WarmMemoryEntriesResponse(
        entries=entries,
        total_count=total_count,
        next_cursor=next_cursor,
    )


@app.delete("/memory/warm/{entry_id}")
async def memory_warm_delete(entry_id: str, request: Request):
    if warm_mem is None:
        raise HTTPException(status_code=503, detail="Warm memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    success = await warm_mem.delete(entry_id, user_id=user_id)
    return {"entry_id": entry_id, "deleted": success}


@app.post("/memory/warm/rollover")
async def memory_warm_rollover(request: Request, min_age_days: int = 7, max_age_days: int = 30):
    if warm_mem is None:
        raise HTTPException(status_code=503, detail="Warm memory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    count = await warm_mem.rollover_conversations(min_age_days=min_age_days, max_age_days=max_age_days, user_id=user_id)
    return {"status": "rollover_complete", "indexed_count": count}


# ── Continuous Learning & Cognitive Telemetry (UserModel, Episodic, Procedural, Reflection) ──


@app.get("/user-model")
async def get_user_model(request: Request):
    """Inspect what HELIX has learned about the user."""
    if user_model_engine is None:
        raise HTTPException(status_code=503, detail="UserModelEngine not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    model = user_model_engine.get_model(user_id)
    return {
        "model": model.model_dump(),
        "prompt_context": user_model_engine.to_prompt_context(user_id=user_id),
    }


@app.post("/user-model/trait")
async def update_user_trait(req: TraitObserveRequest, request: Request):
    """Observe or explicitly set a trait in the user cognitive profile."""
    if user_model_engine is None:
        raise HTTPException(status_code=503, detail="UserModelEngine not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    if req.confidence is not None:
        trait_evidence = user_model_engine.set_explicit_trait(
            category=req.category,
            trait_name=req.trait,
            value=req.value,
            confidence=req.confidence,
            user_id=user_id,
        )
    else:
        trait_evidence = user_model_engine.observe_trait(
            category=req.category,
            trait_name=req.trait,
            value=req.value,
            source=req.source,
            user_id=user_id,
        )
    return {
        "status": "updated",
        "category": req.category,
        "trait": req.trait,
        "evidence": trait_evidence.model_dump(),
    }


@app.post("/user-model/context")
async def update_user_context(req: UserContextUpdateRequest, request: Request):
    """Update active user working context (current goal, project, topic)."""
    if user_model_engine is None:
        raise HTTPException(status_code=503, detail="UserModelEngine not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    ctx = user_model_engine.update_active_context(
        current_goal=req.current_goal,
        active_project=req.active_project,
        topic=req.recent_topic,
        user_id=user_id,
    )
    return {"status": "updated", "active_context": ctx.model_dump()}


@app.post("/user-model/decay")
async def decay_user_model(request: Request, hours_elapsed: float = 24.0):
    """Apply time-based confidence decay to observed traits."""
    if user_model_engine is None:
        raise HTTPException(status_code=503, detail="UserModelEngine not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    user_model_engine.decay_confidence(hours_elapsed=hours_elapsed, user_id=user_id)
    return {"status": "decay_applied", "hours_elapsed": hours_elapsed}


@app.get("/memory/episodes")
async def get_episodes(
    request: Request,
    query: str | None = None,
    limit: int = 20,
    outcome: str | None = None,
    tag: str | None = None,
):
    """Inspect episodic memory timeline or search episodes."""
    if episodic_mem is None:
        raise HTTPException(status_code=503, detail="EpisodicMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    if query:
        episodes = episodic_mem.search_episodes(query=query, limit=limit, outcome=outcome, tag=tag, user_id=user_id)
    else:
        episodes = episodic_mem.get_recent(limit=limit, outcome=outcome, user_id=user_id)
    return {
        "count": len(episodes),
        "episodes": [e.model_dump() for e in episodes],
    }


@app.get("/memory/episodes/mistakes")
async def get_episode_mistakes(intent: str, request: Request):
    """Retrieve past mistakes and lessons learned for a given intent."""
    if episodic_mem is None:
        raise HTTPException(status_code=503, detail="EpisodicMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    mistakes = episodic_mem.get_past_mistakes_for_intent(intent, user_id=user_id)
    return {
        "intent": intent,
        "mistakes": [m.model_dump() for m in mistakes],
    }


@app.get("/memory/episodes/{episode_id}")
async def get_episode(episode_id: str, request: Request):
    """Retrieve a specific interaction episode by ID."""
    if episodic_mem is None:
        raise HTTPException(status_code=503, detail="EpisodicMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    episode = episodic_mem.get_episode(episode_id, user_id=user_id)
    if not episode:
        raise HTTPException(status_code=404, detail="Episode not found")
    return {"episode": episode.model_dump()}


@app.post("/memory/episodes")
async def record_episode(req: EpisodeCreateRequest, request: Request):
    """Manually record an interaction episode into episodic memory."""
    if episodic_mem is None:
        raise HTTPException(status_code=503, detail="EpisodicMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    normalized_actions = []
    for a in req.actions_taken:
        if isinstance(a, str):
            normalized_actions.append({"action": a})
        elif isinstance(a, dict):
            normalized_actions.append(a)
        else:
            normalized_actions.append({"action": str(a)})
    ep = Episode(
        user_id=user_id,
        intent=req.intent,
        actions_taken=normalized_actions,
        outcome=req.outcome,
        reflection_notes=req.reflection_notes or (req.user_feedback or ""),
        tags=req.tags,
    )
    saved = episodic_mem.record_episode(ep)
    return {"status": "recorded", "episode": saved.model_dump()}


@app.get("/memory/workflows")
async def get_workflows(request: Request, project_scope: str | None = None, intent: str | None = None):
    """Inspect learned procedural workflows, optionally matching an intent."""
    if procedural_mem is None:
        raise HTTPException(status_code=503, detail="ProceduralMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    if intent:
        matched = procedural_mem.find_matching_workflow(intent=intent, project_scope=project_scope, user_id=user_id)
        return {
            "matched": matched.model_dump() if matched else None,
        }
    workflows = procedural_mem.get_all(project_scope=project_scope, user_id=user_id)
    return {
        "count": len(workflows),
        "workflows": [w.model_dump() for w in workflows],
    }


@app.get("/memory/workflows/{workflow_id}")
async def get_workflow(workflow_id: str, request: Request):
    """Retrieve a specific procedural workflow by ID."""
    if procedural_mem is None:
        raise HTTPException(status_code=503, detail="ProceduralMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    wf = procedural_mem.get_workflow(workflow_id, user_id=user_id)
    if not wf:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"workflow": wf.model_dump()}


@app.post("/memory/workflows")
async def save_workflow(req: WorkflowCreateRequest, request: Request):
    """Create or update a learned procedural workflow."""
    if procedural_mem is None:
        raise HTTPException(status_code=503, detail="ProceduralMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    parsed_steps = []
    for idx, s in enumerate(req.steps):
        action_name = s.get("action") or s.get("action_type") or s.get("description") or "execute"
        target_name = s.get("target") or s.get("tool")
        parsed_steps.append(ProceduralStep(
            step_index=s.get("step_index", idx),
            action=action_name,
            target=target_name,
            params=s.get("params", {}),
            depends_on=s.get("depends_on", []),
            notes=s.get("notes") or s.get("description", ""),
        ))
    trigger = req.trigger_intent or req.intent_pattern or req.name
    kwargs: dict[str, Any] = {
        "user_id": user_id,
        "name": req.name,
        "trigger_intent": trigger,
        "keywords": req.keywords,
        "steps": parsed_steps,
        "project_scope": req.project_scope,
        "confidence": req.confidence,
        "user_verified": req.user_verified,
    }
    if req.workflow_id:
        kwargs["workflow_id"] = req.workflow_id
    wf = ProceduralWorkflow(**kwargs)
    saved = procedural_mem.save_workflow(wf)
    return {"status": "saved", "workflow": saved.model_dump()}


@app.delete("/memory/workflows/{workflow_id}")
async def delete_workflow(workflow_id: str, request: Request):
    """Delete a learned procedural workflow."""
    if procedural_mem is None:
        raise HTTPException(status_code=503, detail="ProceduralMemory not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    removed = procedural_mem.delete_workflow(workflow_id, user_id=user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"status": "deleted", "workflow_id": workflow_id}


@app.post("/reflection/trigger")
async def trigger_reflection(req: ReflectionTriggerRequest, request: Request):
    """Trigger cognitive reflection and learning cycle."""
    if reflection_engine is None:
        raise HTTPException(status_code=503, detail="ReflectionEngine not initialized")
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    await event_bus.publish_event(
        source="api",
        event_type="reflection.trigger",
        payload={
            "user_id": user_id,
            "session_id": req.session_id,
            "focus": req.focus,
            "notes": req.notes,
        },
    )
    return {"status": "reflection_triggered", "session_id": req.session_id}


# ── Profile / System ──


@app.get("/system/profile")
async def system_profile():
    import os as os_mod
    import getpass
    try:
        username = getpass.getuser()
    except Exception:
        username = os_mod.environ.get("USERNAME", "user")
    hostname = os_mod.environ.get("COMPUTERNAME", os_mod.uname().nodename) if hasattr(os_mod, 'uname') else os_mod.environ.get("COMPUTERNAME", "localhost")
    return {"username": username, "hostname": hostname}


@app.get("/stats/system")
async def system_stats(request: Request):
    global conv_mem, pref_mem, expl_engine, perm_manager
    global user_model_engine, episodic_mem, procedural_mem
    principal = getattr(request.state, "principal", None)
    user_id = principal.user_id if principal else "default_owner"
    conv_count = 0
    pref_count = 0
    expl_count = 0
    perm_count = 0
    user_traits_count = 0
    episodes_count = 0
    workflows_count = 0

    if conv_mem is not None:
        try:
            entries = await conv_mem.get_session_history("default", 1000, user_id=user_id)
            conv_count = len(entries)
        except Exception:
            pass

    if pref_mem is not None:
        pref_count = len(pref_mem.get_all(user_id=user_id))

    if expl_engine is not None:
        try:
            records = expl_engine.get_recent(1000)
            expl_count = len(records)
        except Exception:
            pass

    if perm_manager is not None:
        perm_count = len(perm_manager.history)

    if user_model_engine is not None:
        model = user_model_engine.get_model(user_id)
        user_traits_count = (
            len(model.skills)
            + 3  # communication_style fields
            + 3  # working_style fields
            + 3  # decision_patterns fields
        )

    if episodic_mem is not None:
        episodes_count = len(episodic_mem._episodes.get(user_id, []))

    if procedural_mem is not None:
        workflows_count = len(procedural_mem._workflows.get(user_id, {}))

    return {
        "conversations": conv_count,
        "preferences": pref_count,
        "explanations": expl_count,
        "permissions_historical": perm_count,
        "pending_permissions": len(perm_manager.pending_requests) if perm_manager else 0,
        "user_traits": user_traits_count,
        "episodes": episodes_count,
        "workflows": workflows_count,
    }


# ── Browser Subsystem: Proxy, Content Extraction, AI & Bookmarks ──


class BrowserOpenRequest(BaseModel):
    url: str


class BrowserExtractRequest(BaseModel):
    url: str


class BrowserSummarizeRequest(BaseModel):
    url: str
    question: str | None = None
    save_to_memory: bool = False


class BookmarkCreateRequest(BaseModel):
    id: str | None = None
    label: str
    url: str
    icon: str = "🌐"


def _load_browser_bookmarks() -> list[dict[str, Any]]:
    from foundation.paths import get_config_dir
    path = get_config_dir() / "browser_bookmarks.json"
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return [
        {"id": "bm-1", "label": "GitHub", "icon": "💻", "url": "https://github.com"},
        {"id": "bm-2", "label": "Docs", "icon": "📄", "url": "https://opencode.ai/docs"},
        {"id": "bm-3", "label": "AI Hub", "icon": "🤖", "url": "https://huggingface.co"},
        {"id": "bm-4", "label": "Analytics", "icon": "📊", "url": "https://analytics.google.com"},
    ]


def _save_browser_bookmarks(bms: list[dict[str, Any]]) -> None:
    from foundation.paths import get_config_dir
    path = get_config_dir() / "browser_bookmarks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bms, f, indent=2)


def _load_browser_quicklinks() -> list[dict[str, Any]]:
    from foundation.paths import get_config_dir
    path = get_config_dir() / "browser_quicklinks.json"
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return [
        {"id": "ql-1", "label": "HELIX Home", "icon": "🏠", "url": "https://helix.ai", "desc": "AI platform dashboard", "color": "text-zinc-300"},
        {"id": "ql-2", "label": "Research", "icon": "🔬", "url": "https://scholar.google.com", "desc": "Academic search", "color": "text-zinc-300"},
        {"id": "ql-3", "label": "Models", "icon": "🧠", "url": "https://huggingface.co/models", "desc": "Model hub", "color": "text-zinc-300"},
        {"id": "ql-4", "label": "Code", "icon": "📝", "url": "https://github.com", "desc": "Source control", "color": "text-zinc-300"},
        {"id": "ql-5", "label": "News", "icon": "📰", "url": "https://news.ycombinator.com", "desc": "Tech news", "color": "text-zinc-300"},
        {"id": "ql-6", "label": "MCP", "icon": "🔌", "url": "https://opencode.ai/mcp", "desc": "Plugin registry", "color": "text-zinc-300"},
        {"id": "ql-7", "label": "Alerts", "icon": "🔔", "url": "https://status.helix.ai", "desc": "System status", "color": "text-zinc-300"},
        {"id": "ql-8", "label": "Chat", "icon": "💬", "url": "https://chat.helix.ai", "desc": "AI assistant", "color": "text-zinc-300"},
    ]


@app.post("/browser/open")
async def browser_open(req: BrowserOpenRequest):
    import webbrowser
    from context.browser_engine.browser_service import validate_and_normalize_url
    is_valid, normalized_url, err = validate_and_normalize_url(req.url)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err or "Invalid URL")
    try:
        webbrowser.open(normalized_url)
        return {"status": "opened", "url": normalized_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/browser/proxy")
async def browser_proxy(url: str):
    from context.browser_engine.browser_service import proxy_fetch_url
    status_code, body, headers, _ = await proxy_fetch_url(url)
    return Response(content=body, status_code=status_code, headers=headers)


@app.post("/browser/extract")
async def browser_extract(req: BrowserExtractRequest):
    from context.browser_engine.browser_service import extract_browser_page
    res = await extract_browser_page(req.url)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to extract page content"))
    return res


@app.post("/browser/summarize")
async def browser_summarize(req: BrowserSummarizeRequest, request: Request):
    from context.browser_engine.browser_service import summarize_page_content
    res = await summarize_page_content(req.url, question=req.question, conv_engine=conv_engine)
    if res.get("status") == "error":
        raise HTTPException(status_code=400, detail=res.get("error", "Summarization failed"))

    saved = False
    if req.save_to_memory and warm_mem is not None and res.get("summary"):
        try:
            principal = getattr(request.state, "principal", None)
            user_id = principal.user_id if principal else "default_owner"
            await warm_mem.index_text(
                text=f"Research on '{res.get('title', req.url)}':\n{res['summary']}",
                category="browser_research",
                metadata={"url": res.get("url", req.url), "title": res.get("title", "")},
                user_id=user_id,
            )
            saved = True
        except Exception as e:
            logger.warning("Could not save browser summary to warm memory: %s", e)

    res["saved_to_memory"] = saved
    return res


@app.get("/browser/bookmarks")
async def browser_get_bookmarks():
    return {"bookmarks": _load_browser_bookmarks()}


@app.post("/browser/bookmarks")
async def browser_add_bookmark(req: BookmarkCreateRequest):
    from context.browser_engine.browser_service import validate_and_normalize_url
    is_valid, normalized_url, err = validate_and_normalize_url(req.url)
    if not is_valid:
        raise HTTPException(status_code=400, detail=err or "Invalid URL")

    bms = _load_browser_bookmarks()
    bm_id = req.id or f"bm-{int(time.time() * 1000)}"
    existing = next((b for b in bms if b["url"] == normalized_url or b["id"] == bm_id), None)
    if existing:
        existing["label"] = req.label
        existing["url"] = normalized_url
        existing["icon"] = req.icon
    else:
        bms.append({"id": bm_id, "label": req.label, "url": normalized_url, "icon": req.icon})
    _save_browser_bookmarks(bms)
    return {"status": "saved", "bookmark": {"id": bm_id, "label": req.label, "url": normalized_url, "icon": req.icon}}


@app.delete("/browser/bookmarks/{bookmark_id}")
async def browser_delete_bookmark(bookmark_id: str):
    bms = _load_browser_bookmarks()
    filtered = [b for b in bms if b.get("id") != bookmark_id and b.get("url") != bookmark_id]
    _save_browser_bookmarks(filtered)
    return {"status": "deleted", "id": bookmark_id}


@app.get("/browser/quick-links")
async def browser_get_quick_links():
    return {"quick_links": _load_browser_quicklinks()}



# ── Work / Tasks / Scratchpad ──


class ScratchpadSaveRequest(BaseModel):
    session_id: str
    content: str


@app.post("/work/scratchpad/save")
async def work_scratchpad_save(req: ScratchpadSaveRequest):
    await event_bus.publish_event(
        source="api",
        event_type="memory.work.update_context",
        payload={"session_id": req.session_id, "updates": {"scratchpad": req.content}},
    )
    return {"session_id": req.session_id, "saved": True}


@app.get("/work/scratchpad/{session_id}")
async def work_scratchpad_get(session_id: str):
    if work_mem is None:
        raise HTTPException(status_code=503, detail="Work memory not initialized")
    session = work_mem.get_session(session_id)
    scratchpad = ""
    if session and session.current_context:
        scratchpad = session.current_context.get("scratchpad", "")
    return {"session_id": session_id, "content": scratchpad}


@app.get("/work/tasks/{session_id}")
async def work_tasks_get(session_id: str):
    if work_mem is None:
        raise HTTPException(status_code=503, detail="Work memory not initialized")
    session = work_mem.get_session(session_id)
    tasks = session.open_tasks if session else []
    return {"session_id": session_id, "tasks": tasks}


@app.get("/work/sessions")
async def work_sessions_get():
    if work_mem is None:
        raise HTTPException(status_code=503, detail="Work memory not initialized")
    if hasattr(work_mem, "get_all_sessions"):
        sessions = work_mem.get_all_sessions()
    else:
        seen_sids: set[str] = set()
        sessions = []
        for s in list(work_mem._sessions.values()):
            sid = getattr(s, "session_id", None)
            if sid and sid not in seen_sids:
                seen_sids.add(sid)
                sessions.append(s)
    return {"sessions": [s.model_dump() for s in sessions if hasattr(s, "model_dump")]}


# ── Productivity & Logs ──


LOG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \[([A-Z]+)\] ([^:]+): (.*)$")


async def read_parsed_logs(limit: int = 100, level: str | None = None) -> list[dict[str, Any]]:
    if not logger or not logger._log_path:
        return []
    log_file = logger._log_path / "helix.log"
    if not log_file.exists():
        return []
        
    try:
        loop = asyncio.get_running_loop()
        def _read():
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                return f.readlines()
        lines = await loop.run_in_executor(None, _read)
    except Exception:
        return []
        
    parsed_entries = []
    current_entry = None
    
    for line in lines:
        line_str = line.strip("\n")
        match = LOG_PATTERN.match(line_str)
        if match:
            if current_entry:
                parsed_entries.append(current_entry)
            raw_ts = match.group(1).replace(",", ".")
            if " " in raw_ts and "T" not in raw_ts:
                raw_ts = raw_ts.replace(" ", "T")
            current_entry = {
                "timestamp": raw_ts,
                "level": match.group(2),
                "module": match.group(3),
                "message": match.group(4)
            }
        else:
            if current_entry:
                current_entry["message"] += "\n" + line_str
            else:
                current_entry = {
                    "timestamp": None,
                    "level": "INFO",
                    "module": "system",
                    "message": line_str
                }
    if current_entry:
        parsed_entries.append(current_entry)
        
    if level:
        level_upper = level.upper()
        parsed_entries = [e for e in parsed_entries if e["level"] == level_upper]
        
    parsed_entries.reverse()
    return parsed_entries[:limit]


@app.get("/productivity/patterns")
async def productivity_patterns_get():
    if productivity_engine is None:
        raise HTTPException(status_code=503, detail="Productivity engine not initialized")
    patterns = productivity_engine.get_patterns()
    return {"patterns": [p.to_dict() for p in patterns]}


@app.get("/productivity/suggestions")
async def productivity_suggestions_get(status: str | None = None):
    if productivity_engine is None:
        raise HTTPException(status_code=503, detail="Productivity engine not initialized")
    suggestions = productivity_engine.get_suggestions(status=status)
    return {"suggestions": [s.to_dict() for s in suggestions]}


class SuggestionActionRequest(BaseModel):
    suggestion_id: str


@app.post("/productivity/suggestion/accept")
async def productivity_suggestion_accept(req: SuggestionActionRequest):
    await event_bus.publish_event(
        source="api",
        event_type="productivity.suggestion.accept",
        payload={"suggestion_id": req.suggestion_id},
    )
    return {"status": "accepted", "suggestion_id": req.suggestion_id}


@app.post("/productivity/suggestion/dismiss")
async def productivity_suggestion_dismiss(req: SuggestionActionRequest):
    await event_bus.publish_event(
        source="api",
        event_type="productivity.suggestion.dismiss",
        payload={"suggestion_id": req.suggestion_id},
    )
    return {"status": "dismissed", "suggestion_id": req.suggestion_id}


@app.get("/productivity/insights")
async def productivity_insights_get():
    all_sessions = []
    if work_mem is not None and hasattr(work_mem, "get_all_sessions"):
        all_sessions = work_mem.get_all_sessions()
    elif work_mem is not None and hasattr(work_mem, "_sessions"):
        seen_sids: set[str] = set()
        for s in list(work_mem._sessions.values()):
            sid = getattr(s, "session_id", None)
            if sid and sid not in seen_sids:
                seen_sids.add(sid)
                all_sessions.append(s)

    project_durations: dict[str, float] = {}
    total_minutes = 0.0
    for s in all_sessions:
        proj = getattr(s, "active_project", None) or "General"
        started_str = getattr(s, "started_at", "")
        updated_str = getattr(s, "last_updated", "") or started_str
        try:
            start_dt = datetime.fromisoformat(started_str.replace("Z", "+00:00")) if started_str else None
            end_dt = datetime.fromisoformat(updated_str.replace("Z", "+00:00")) if updated_str else None
            if start_dt and end_dt:
                diff_min = max(1.0, (end_dt - start_dt).total_seconds() / 60.0)
            else:
                diff_min = 1.0
        except Exception:
            diff_min = 1.0
        project_durations[proj] = project_durations.get(proj, 0.0) + diff_min
        total_minutes += diff_min

    total_focus_hours = round(total_minutes / 60.0, 1)

    colors = ["#00d4ff", "#7c3aed", "#22c55e", "#f59e0b", "#ec4899", "#8b5cf6"]
    categories = []
    for i, (p_name, p_mins) in enumerate(project_durations.items()):
        pct = round((p_mins / total_minutes) * 100.0, 1) if total_minutes > 0 else 0.0
        categories.append({
            "name": p_name,
            "percentage": pct,
            "hours": round(p_mins / 60.0, 1),
            "color": colors[i % len(colors)],
        })

    distinct_projects = list(project_durations.keys())
    context_switches = max(0, len(distinct_projects) - 1) if distinct_projects else 0
    focus_score = min(100.0, round(50.0 + (total_minutes / 12.0), 1)) if total_minutes > 0 else 0.0

    return {
        "focus_score": focus_score,
        "total_focus_hours": total_focus_hours,
        "context_switches": context_switches,
        "categories": categories,
        "timeline": [],
        "status": "nominal" if total_minutes > 0 else "no_data",
    }


@app.get("/logs")
async def logs_get(level: str | None = None, limit: int = 100):
    if logger is None or not hasattr(logger, "_log_path"):
        raise HTTPException(status_code=503, detail="Logger not initialized")
    try:
        entries = await read_parsed_logs(limit=limit, level=level)
        return {"logs": entries}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Permissions ──


class PermissionRequestPayload(BaseModel):
    action: str
    reasoning: str
    benefits: list[str] | None = None
    risks: list[str] | None = None
    alternatives: list[str] | None = None
    source_module: str = "unknown"
    resources: list[str] | None = None
    params: dict[str, Any] | None = None
    target: str | None = None
    scope: str | None = None
    level: str | None = None
    user_id: str | None = None


class PermissionDecisionPayload(BaseModel):
    id: str


class AutoApproveAddRequest(BaseModel):
    pattern: str


@app.post("/permission/request")
async def permission_request(req: PermissionRequestPayload, request: Request):
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    principal = get_current_principal(request)
    target_user_id = req.user_id or principal.user_id
    if not principal.is_owner() and target_user_id != principal.user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot create permission request for another user")
    p = await perm_manager.request(
        action=req.action,
        reasoning=req.reasoning,
        benefits=req.benefits,
        risks=req.risks,
        alternatives=req.alternatives,
        source_module=req.source_module,
        resources=req.resources,
        params=req.params,
        target=req.target,
        scope=req.scope,
        ttl_seconds=300,
        user_id=target_user_id,
    )
    return {
        "id": p.id,
        "user_id": p.user_id,
        "action": p.action,
        "status": p.status,
        "reasoning": p.reasoning,
        "benefits": p.benefits,
        "risks": p.risks,
        "alternatives": p.alternatives,
        "resources": p.resources,
        "params": p.params,
        "target": p.target,
        "params_hash": p.params_hash,
        "risk_level": p.risk_level.value,
        "expires_at": p.expires_at,
        "source_module": p.source_module,
        "created_at": p.created_at,
        "scope": req.scope or p.action,
        "level": req.level or "read",
    }


@app.post("/permission/grant")
async def permission_grant(req: PermissionDecisionPayload, request: Request):
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    principal = get_current_principal(request)
    pending = next((p for p in perm_manager.pending_requests if p.id == req.id), None)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"Permission request '{req.id}' not found, expired, or already decided."
        )
    # Ensure caller is owner/admin or the user who requested the permission
    if not principal.is_owner() and getattr(pending, "user_id", None) != principal.user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot approve permission request belonging to another user")
    await event_bus.publish_event(
        source="api",
        event_type="permission.grant",
        payload={"id": req.id, "user_id": principal.user_id},
    )
    if pending.status != "approved":
        raise HTTPException(status_code=409, detail=f"Permission request '{req.id}' was not approved; current status is '{pending.status}'.")
    return {"id": req.id, "status": "granted"}


@app.post("/permission/deny")
async def permission_deny(req: PermissionDecisionPayload, request: Request):
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    principal = get_current_principal(request)
    pending = next((p for p in perm_manager.pending_requests if p.id == req.id), None)
    if pending is None:
        raise HTTPException(
            status_code=404,
            detail=f"Permission request '{req.id}' not found, expired, or already decided."
        )
    # Ensure caller is owner/admin or the user who requested the permission
    if not principal.is_owner() and getattr(pending, "user_id", None) != principal.user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot deny permission request belonging to another user")
    await event_bus.publish_event(
        source="api",
        event_type="permission.deny",
        payload={"id": req.id, "user_id": principal.user_id},
    )
    if pending.status != "denied":
        raise HTTPException(status_code=409, detail=f"Permission request '{req.id}' was not denied; current status is '{pending.status}'.")
    return {"id": req.id, "status": "denied"}


@app.post("/permission/auto-approve")
async def add_auto_approve_pattern(req: AutoApproveAddRequest, request: Request):
    principal = get_current_principal(request)
    if not principal.is_owner():
        raise HTTPException(status_code=403, detail="Forbidden: Admin or owner privileges required to modify auto-approve rules")
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    await event_bus.publish_event(
        source="api",
        event_type="permission.auto_approve.add",
        payload={"pattern": req.pattern},
    )
    return {"pattern": req.pattern, "status": "added"}


@app.delete("/permission/auto-approve")
async def remove_auto_approve_pattern(pattern: str, request: Request):
    principal = get_current_principal(request)
    if not principal.is_owner():
        raise HTTPException(status_code=403, detail="Forbidden: Admin or owner privileges required to modify auto-approve rules")
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    await event_bus.publish_event(
        source="api",
        event_type="permission.auto_approve.remove",
        payload={"pattern": pattern},
    )
    return {"pattern": pattern, "status": "removed"}


@app.get("/permission/history")
async def permission_history():
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    return {
        "history": [
            {
                "id": r.id,
                "action": r.action,
                "reasoning": r.reasoning,
                "benefits": r.benefits,
                "risks": r.risks,
                "alternatives": r.alternatives,
                "source_module": r.source_module,
                "resources": r.resources,
                "params": r.params,
                "target": r.target,
                "params_hash": r.params_hash,
                "risk_level": r.risk_level.value,
                "scope": r.scope,
                "expires_at": r.expires_at,
                "status": r.status,
                "created_at": r.created_at,
                "decided_at": r.decided_at,
                "decision_source": r.decision_source,
            }
            for r in perm_manager.history
        ]
    }


@app.get("/permission/patterns")
async def permission_patterns():
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    return {"patterns": perm_manager.auto_approve_patterns}


@app.get("/permission/pending")
async def permission_pending():
    if perm_manager is None:
        raise HTTPException(status_code=503, detail="Permission manager not initialized")
    requests = perm_manager.pending_requests
    return {
        "pending": [
            {
                "id": r.id,
                "action": r.action,
                "reasoning": r.reasoning,
                "benefits": r.benefits,
                "risks": r.risks,
                "alternatives": r.alternatives,
                "source_module": r.source_module,
                "resources": r.resources,
                "params": r.params,
                "target": r.target,
                "params_hash": r.params_hash,
                "risk_level": r.risk_level.value,
                "scope": r.scope,
                "expires_at": r.expires_at,
                "created_at": r.created_at,
            }
            for r in requests
        ]
    }


# ── State ──


@app.get("/state")
async def get_state():
    current = orch.current_state if orch else "unknown"
    return {"state": current}


# ── General Settings REST API ──

class SettingsUpdateRequest(BaseModel):
    autoStart: bool | None = None
    rememberHistory: bool | None = None
    animations: bool | None = None
    compactMode: bool | None = None
    soundAlerts: bool | None = None
    privateMode: bool | None = None
    shortcutHints: bool | None = None
    apiKeySaved: bool | None = None
    browser_history_enabled: bool | None = None
    browserHistoryEnabled: bool | None = None


@app.get("/settings")
async def get_settings():
    from foundation.paths import get_config_dir
    config_path = get_config_dir() / "config.json"
    settings = {
        "autoStart": True,
        "rememberHistory": True,
        "animations": True,
        "compactMode": False,
        "soundAlerts": True,
        "privateMode": False,
        "shortcutHints": True,
        "apiKeySaved": True,
        "browser_history_enabled": False,
    }
    if config_path.is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "ui_settings" in data:
                    settings.update(data["ui_settings"])
                if "browser_history_enabled" in data:
                    settings["browser_history_enabled"] = data["browser_history_enabled"]
        except Exception as e:
            logger.warning(f"Failed to read settings from {config_path}: {e}")
    # Enforce safe boolean default for browser_history_enabled
    raw_hist = settings.get("browser_history_enabled", False)
    if isinstance(raw_hist, str):
        settings["browser_history_enabled"] = raw_hist.strip().lower() in ("true", "1")
    elif not isinstance(raw_hist, bool):
        settings["browser_history_enabled"] = False
    return {"settings": settings}


@app.post("/settings")
async def update_settings(req: SettingsUpdateRequest, request: Request):
    principal = get_current_principal(request)
    if not principal.is_owner():
        raise HTTPException(status_code=403, detail="Forbidden: Admin or owner privileges required to modify settings")
    from foundation.paths import get_config_dir
    config_path = get_config_dir() / "config.json"
    config_data = {}
    if config_path.is_file():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse existing config {config_path}: {e}")

    ui_settings = config_data.get("ui_settings", {
        "autoStart": True,
        "rememberHistory": True,
        "animations": True,
        "compactMode": False,
        "soundAlerts": True,
        "privateMode": False,
        "shortcutHints": True,
        "apiKeySaved": True,
        "browser_history_enabled": False,
    })

    if hasattr(req, "model_dump"):
        updates = req.model_dump(exclude_unset=True)
    else:
        updates = req.dict(exclude_unset=True)

    if "browserHistoryEnabled" in updates and "browser_history_enabled" not in updates:
        updates["browser_history_enabled"] = updates["browserHistoryEnabled"]
    if "browser_history_enabled" in updates and "browserHistoryEnabled" not in updates:
        updates["browserHistoryEnabled"] = updates["browser_history_enabled"]

    ui_settings.update(updates)
    config_data["ui_settings"] = ui_settings
    if "browser_history_enabled" in updates:
        config_data["browser_history_enabled"] = updates["browser_history_enabled"]

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {e}")

    return {"status": "updated", "settings": ui_settings}


# ── Files Upload & Binary Storage REST API ──

_BLOCKED_UPLOAD_EXTENSIONS = {".exe", ".bat", ".cmd", ".vbs", ".ps1", ".dll", ".so", ".dylib", ".sys", ".scr"}

@app.get("/files")
async def get_uploaded_files(request: Request):
    principal = get_current_principal(request)
    uploads_dir = get_tenant_storage_manager().get_user_uploads_dir(principal.user_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for f in uploads_dir.iterdir():
        if f.is_file() and not f.is_symlink():
            try:
                stat = f.stat()
                files.append({
                    "name": f.name,
                    "filename": f.name,
                    "path": f"Uploads/{f.name}",
                    "size_bytes": stat.st_size,
                    "size": f"{round(stat.st_size / 1024 / 1024, 2)} MB",
                    "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
                    "extension": f.suffix.lower(),
                })
            except Exception as e:
                logger.warning(f"Failed to inspect uploaded file {f}: {e}")
    return {"files": files}


@app.post("/files/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    principal = get_current_principal(request)
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename cannot be empty")

    raw_clean = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', Path(file.filename).name).strip("._ ")
    if not raw_clean or raw_clean in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid or unsafe filename")

    clean_filename = raw_clean
    ext = Path(clean_filename).suffix.lower()

    if ext in _BLOCKED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"Access denied: cannot upload restricted file type '{ext}'")

    uploads_dir = get_tenant_storage_manager().get_user_uploads_dir(principal.user_id)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    dest_path = (uploads_dir / clean_filename).resolve()
    try:
        dest_path.relative_to(uploads_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal attempt rejected")

    if dest_path.is_symlink():
        raise HTTPException(status_code=400, detail="Uploading to symlink targets is prohibited")

    content = await file.read()

    with open(dest_path, "wb") as f:
        f.write(content)

    return {
        "status": "uploaded",
        "name": clean_filename,
        "path": str(dest_path),
        "size_bytes": len(content),
        "size": f"{round(len(content) / 1024 / 1024, 2)} MB",
    }


def _safe_user_filename(filename: str) -> str:
    if (
        not filename
        or filename in {".", ".."}
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise HTTPException(status_code=404, detail="File not found")
    return filename


@app.get("/files/{filename}/download")
async def download_uploaded_file(filename: str, request: Request):
    principal = get_current_principal(request)
    safe_name = _safe_user_filename(filename)
    uploads_dir = get_tenant_storage_manager().get_user_uploads_dir(principal.user_id).resolve()
    file_path = uploads_dir / safe_name
    if file_path.is_symlink() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        file_path.resolve().relative_to(uploads_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=safe_name)


@app.delete("/files/{filename}")
async def delete_uploaded_file(filename: str, request: Request):
    principal = get_current_principal(request)
    safe_name = _safe_user_filename(filename)
    uploads_dir = get_tenant_storage_manager().get_user_uploads_dir(principal.user_id).resolve()
    file_path = uploads_dir / safe_name
    if file_path.is_symlink() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        file_path.resolve().relative_to(uploads_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="File not found")
    file_path.unlink()
    return {"status": "deleted", "name": safe_name}


def _get_pipelines_file() -> Path:
    base_data = os.getenv("HELIX_DATA_DIR")
    if base_data:
        return (Path(base_data) / "pipelines" / "pipelines.json").resolve()
    return Path("./data/pipelines/pipelines.json").resolve()


def _get_agents_file() -> Path:
    base_data = os.getenv("HELIX_DATA_DIR")
    if base_data:
        return (Path(base_data) / "agents" / "agents.json").resolve()
    return Path("./data/agents/agents.json").resolve()


# ── Automation Flow Pipelines REST API ──

class PipelineSaveRequest(BaseModel):
    id: str
    name: str
    description: str | None = ""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


@app.get("/automation/pipelines")
async def get_pipelines():
    pipelines_file = _get_pipelines_file()
    if pipelines_file.is_file():
        try:
            with open(pipelines_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {"pipelines": data}
        except Exception as e:
            logger.warning(f"Failed to read pipelines from {pipelines_file}: {e}")
    default_pipelines = [
        {"id": "daily_digest", "name": "Daily Digest", "status": "idle", "lastRun": "Not yet run", "runs": 0, "nodes": [], "edges": []},
        {"id": "memory_indexer", "name": "Memory Indexer", "status": "idle", "lastRun": "Not yet run", "runs": 0, "nodes": [], "edges": []},
        {"id": "agent_scheduler", "name": "Agent Scheduler", "status": "idle", "lastRun": "Not yet run", "runs": 0, "nodes": [], "edges": []},
    ]
    return {"pipelines": default_pipelines}


@app.post("/automation/pipelines")
async def save_pipeline(req: PipelineSaveRequest):
    pipelines_file = _get_pipelines_file()
    pipelines_file.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if pipelines_file.is_file():
        try:
            with open(pipelines_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse pipelines file {pipelines_file}: {e}")

    updated = False
    for p in existing:
        if p.get("id") == req.id:
            p["name"] = req.name
            p["description"] = req.description
            p["nodes"] = req.nodes
            p["edges"] = req.edges
            updated = True
            break

    if not updated:
        existing.append({
            "id": req.id,
            "name": req.name,
            "description": req.description,
            "status": "idle",
            "lastRun": "Just created",
            "runs": 0,
            "nodes": req.nodes,
            "edges": req.edges,
        })

    with open(pipelines_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    return {"status": "saved", "pipeline_id": req.id}


@app.post("/automation/pipelines/{pipeline_id}/run")
async def run_pipeline(pipeline_id: str):
    pipelines_res = await get_pipelines()
    pipelines = pipelines_res.get("pipelines", [])
    target_pipeline = next((p for p in pipelines if p.get("id") == pipeline_id), None)

    if not target_pipeline:
        target_pipeline = {"id": pipeline_id, "name": pipeline_id, "runs": 0}
        pipelines.append(target_pipeline)

    results = []
    nodes = target_pipeline.get("nodes", [])

    for node in nodes:
        node_label = node.get("data", {}).get("label") or node.get("id", "step")
        results.append({
            "node_id": node.get("id"),
            "label": node_label,
            "status": "completed",
            "timestamp": time.time(),
        })

    target_pipeline["runs"] = target_pipeline.get("runs", 0) + 1
    target_pipeline["lastRun"] = time.strftime("%Y-%m-%d %H:%M:%S")

    pipelines_file = _get_pipelines_file()
    try:
        pipelines_file.parent.mkdir(parents=True, exist_ok=True)
        with open(pipelines_file, "w", encoding="utf-8") as f:
            json.dump(pipelines, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to update pipeline run status: {e}")

    return {
        "status": "completed",
        "pipeline_id": pipeline_id,
        "runs": target_pipeline["runs"],
        "executed_steps": len(results),
        "step_results": results,
    }


# ── Agents Deployment & Orchestrator REST API ──

class AgentDeployRequest(BaseModel):
    name: str
    role: str
    capabilities: list[str] = []
    max_tokens: int = 4096


@app.get("/agents")
async def get_agents():
    agents_file = _get_agents_file()
    if agents_file.is_file():
        try:
            with open(agents_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {"agents": data}
        except Exception as e:
            logger.warning(f"Failed to read agents from {agents_file}: {e}")

    default_agents = [
        {
            "id": "research_synthesizer",
            "name": "Research Synthesizer",
            "role": "Deep analysis & paper summarization",
            "status": "idle",
            "capabilities": ["Web search", "PDF extraction", "Summarization"],
            "tasks_completed": 42,
            "created_at": time.time() - 86400 * 5,
        },
        {
            "id": "code_reviewer",
            "name": "Code Reviewer",
            "role": "Static analysis & vulnerability detection",
            "status": "online",
            "capabilities": ["Python AST", "Git diffs", "Security linting"],
            "tasks_completed": 128,
            "created_at": time.time() - 86400 * 12,
        },
        {
            "id": "memory_indexer",
            "name": "Memory Indexer",
            "role": "Graph embedding & vector index update",
            "status": "idle",
            "capabilities": ["Vector search", "DPAPI keying", "Entity extraction"],
            "tasks_completed": 310,
            "created_at": time.time() - 86400 * 30,
        },
    ]
    return {"agents": default_agents}


@app.post("/agents/deploy")
async def deploy_agent(req: AgentDeployRequest):
    if not req.name.strip():
        raise HTTPException(status_code=400, detail="Agent name cannot be empty")

    agent_id = re.sub(r'[^a-zA-Z0-9_]', '_', req.name.lower().strip())
    new_agent = {
        "id": agent_id,
        "name": req.name,
        "role": req.role or "Custom AI Subagent",
        "status": "online",
        "capabilities": req.capabilities or ["Autonomous reasoning"],
        "max_tokens": req.max_tokens,
        "tasks_completed": 0,
        "created_at": time.time(),
    }

    agents_file = _get_agents_file()
    agents_file.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if agents_file.is_file():
        try:
            with open(agents_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to parse agents file {agents_file}: {e}")

    existing.append(new_agent)

    with open(agents_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    return {"status": "deployed", "agent": new_agent}


# ── Reminders REST API ──

class ReminderCreateRequest(BaseModel):
    text: str
    delay_seconds: float | None = None
    time: str | None = None
    trigger_at: str | None = None
    recurring: bool = False


@app.get("/reminders")
async def get_reminders():
    if timetable_scheduler is None:
        return {"reminders": []}
    reminders = timetable_scheduler.load_timetable()
    return {"reminders": reminders}


@app.post("/reminders/add")
async def add_reminder(req: ReminderCreateRequest):
    if timetable_scheduler is None:
        raise HTTPException(status_code=503, detail="TimetableScheduler is not initialized")
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    entry = timetable_scheduler.add_reminder(
        text=req.text,
        delay_seconds=req.delay_seconds,
        time_str=req.time,
        trigger_at=req.trigger_at,
        recurring=req.recurring,
    )
    return {"status": "created", "reminder": entry}


@app.delete("/reminders/{reminder_id}")
async def delete_reminder(reminder_id: str):
    if timetable_scheduler is None:
        raise HTTPException(status_code=503, detail="TimetableScheduler is not initialized")
    timetable_scheduler._reminders = [r for r in timetable_scheduler._reminders if r.get("id") != reminder_id]
    timetable_scheduler.save_timetable()
    return {"status": "deleted", "reminder_id": reminder_id}


# ── App Aliases REST API ──

class AliasRegisterRequest(BaseModel):
    alias: str
    executable: str


@app.get("/aliases")
async def get_aliases():
    if action_executor is None:
        return {"aliases": {}}
    return {"aliases": action_executor._app_aliases}


@app.post("/aliases/register")
async def register_alias(req: AliasRegisterRequest):
    if action_executor is None:
        raise HTTPException(status_code=503, detail="ActionExecutor is not initialized")
    if not req.alias.strip() or not req.executable.strip():
        raise HTTPException(status_code=400, detail="Alias and executable cannot be empty")
    action_executor.register_alias(req.alias, req.executable)
    aliases_path = Path(__file__).resolve().parents[1] / "config" / "app_aliases.json"
    try:
        aliases_path.parent.mkdir(parents=True, exist_ok=True)
        with open(aliases_path, "w", encoding="utf-8") as f:
            json.dump(action_executor._app_aliases, f, indent=2)
    except Exception as e:
        logger.warning("Failed to save updated aliases to %s: %s", aliases_path, e)
    return {"status": "registered", "alias": req.alias, "executable": req.executable}


@app.delete("/aliases/{alias}")
async def delete_alias(alias: str):
    if action_executor is None:
        raise HTTPException(status_code=503, detail="ActionExecutor is not initialized")
    alias_lower = alias.lower().strip()
    if alias_lower in action_executor._app_aliases:
        del action_executor._app_aliases[alias_lower]
        aliases_path = Path(__file__).resolve().parents[1] / "config" / "app_aliases.json"
        try:
            with open(aliases_path, "w", encoding="utf-8") as f:
                json.dump(action_executor._app_aliases, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save updated aliases to %s: %s", aliases_path, e)
    return {"status": "deleted", "alias": alias}


# ── Notes REST API ──

class NoteCreateRequest(BaseModel):
    title: str
    content: str


@app.get("/notes")
async def get_notes(request: Request):
    principal = get_current_principal(request)
    notes_dir = get_tenant_storage_manager().get_user_notes_dir(principal.user_id)
    notes_dir.mkdir(parents=True, exist_ok=True)
    notes = []
    for f in notes_dir.glob("*.txt"):
        if f.is_symlink() or not f.is_file():
            continue
        try:
            stat = f.stat()
            content = f.read_text(encoding="utf-8", errors="ignore")
            notes.append({
                "title": f.stem,
                "filename": f.name,
                "content": content,
                "created_at": stat.st_ctime,
                "updated_at": stat.st_mtime,
                "size_bytes": stat.st_size,
                "path": f"Notes/{f.name}",
            })
        except Exception as e:
            logger.warning("Failed to read note file %s: %s", f, e)
    return {"notes": notes}


@app.post("/notes/create")
async def create_note(req: NoteCreateRequest, request: Request):
    principal = get_current_principal(request)
    if not req.title.strip():
        raise HTTPException(status_code=400, detail="Note title cannot be empty")
    if len(req.content.encode("utf-8")) > 1_048_576:
        raise HTTPException(status_code=413, detail="Note content is limited to 1 MB")

    safe_title = re.sub(r"[^a-zA-Z0-9_\- ]", "_", req.title).strip(" _.")
    if not safe_title:
        raise HTTPException(status_code=400, detail="Note title does not contain a valid filename")

    notes_dir = get_tenant_storage_manager().get_user_notes_dir(principal.user_id).resolve()
    filename = f"{safe_title}.txt"
    note_path = notes_dir / filename
    if note_path.is_symlink():
        raise HTTPException(status_code=400, detail="Writing to symlink targets is prohibited")
    if note_path.exists():
        raise HTTPException(status_code=409, detail="A note with this title already exists")
    try:
        note_path.resolve().relative_to(notes_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid note path")
    try:
        with note_path.open("x", encoding="utf-8") as note_file:
            note_file.write(req.content)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="A note with this title already exists")
    stat = note_path.stat()
    return {
        "status": "created",
        "title": safe_title,
        "filename": filename,
        "size_bytes": stat.st_size,
        "path": f"Notes/{filename}",
    }


@app.delete("/notes/{title}")
async def delete_note(title: str, request: Request):
    principal = get_current_principal(request)
    if not title or Path(title).name != title or "/" in title or "\\" in title:
        raise HTTPException(status_code=404, detail="Note not found")
    raw_title = title[:-4] if title.lower().endswith(".txt") else title
    safe_title = re.sub(r"[^a-zA-Z0-9_\- ]", "_", raw_title).strip(" _.")
    if not safe_title:
        raise HTTPException(status_code=404, detail="Note not found")
    notes_dir = get_tenant_storage_manager().get_user_notes_dir(principal.user_id).resolve()
    note_file = notes_dir / f"{safe_title}.txt"
    if note_file.is_symlink() or not note_file.is_file():
        raise HTTPException(status_code=404, detail="Note not found")
    try:
        note_file.resolve().relative_to(notes_dir)
    except ValueError:
        raise HTTPException(status_code=404, detail="Note not found")
    note_file.unlink()
    return {"status": "deleted", "title": safe_title, "filename": note_file.name}


class GoalCreateRequest(BaseModel):
    title: str
    description: str
    priority: str = "medium"
    tasks: list[dict] | None = None


@app.get("/system/self_state")
async def get_self_state():
    if self_model_engine is None:
        raise HTTPException(status_code=503, detail="SelfModelEngine not initialized")
    return self_model_engine.get_state().to_dict()


@app.get("/system/world_state")
async def get_world_state():
    if world_model_engine is None:
        raise HTTPException(status_code=503, detail="WorldModelEngine not initialized")
    return world_model_engine.get_environment_state().to_dict()


@app.get("/system/capabilities")
async def get_capabilities():
    if capability_registry is None:
        raise HTTPException(status_code=503, detail="CapabilityRegistry not initialized")
    return {"capabilities": capability_registry.list_all()}


@app.get("/system/discovered_apps")
async def get_discovered_apps():
    if app_discovery_engine is None:
        raise HTTPException(status_code=503, detail="ApplicationDiscoveryEngine not initialized")
    return {"apps": app_discovery_engine.list_apps()}


@app.get("/system/projects")
async def get_discovered_projects():
    if project_registry is None:
        raise HTTPException(status_code=503, detail="ProjectRegistry not initialized")
    return {"projects": project_registry.list_projects()}


@app.get("/system/traces")
async def get_telemetry_traces(limit: int = 50):
    if observability_engine is None:
        raise HTTPException(status_code=503, detail="ObservabilityEngine not initialized")
    return {"traces": observability_engine.get_recent_traces(limit=limit)}


@app.get("/goals")
@app.get("/api/goals")
async def get_goals(
    request: Request,
    include_archived: bool = False,
    archived_only: bool = False,
):
    principal = get_current_principal(request)
    if goal_manager is None:
        return {"goals": []}
    return {"goals": goal_manager.list_goals(
        user_id=principal.user_id,
        include_archived=include_archived,
        archived_only=archived_only,
    )}


@app.post("/goals")
@app.post("/api/goals")
async def create_goal(req: GoalCreateRequest, request: Request):
    if goal_manager is None:
        raise HTTPException(status_code=503, detail="GoalManager not initialized")
    principal = get_current_principal(request)
    goal = goal_manager.create_goal(
        title=req.title,
        description=req.description,
        user_id=principal.user_id,
        priority=req.priority,
        tasks=req.tasks,
    )
    return goal.to_dict()


@app.post("/goals/{goal_id}/archive")
async def archive_goal_endpoint(goal_id: str, request: Request):
    if goal_manager is None:
        raise HTTPException(status_code=503, detail="GoalManager not initialized")
    principal = get_current_principal(request)
    success, msg = goal_manager.archive_goal(goal_id, user_id=principal.user_id)
    if not success:
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "unauthorized" in msg.lower():
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    goal = goal_manager.get_goal(goal_id, user_id=principal.user_id)
    return {"status": "archived", "goal": goal.to_dict() if goal else {}}


@app.post("/goals/{goal_id}/restore")
async def restore_goal_endpoint(goal_id: str, request: Request):
    if goal_manager is None:
        raise HTTPException(status_code=503, detail="GoalManager not initialized")
    principal = get_current_principal(request)
    success, msg = goal_manager.restore_goal(goal_id, user_id=principal.user_id)
    if not success:
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "unauthorized" in msg.lower():
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    goal = goal_manager.get_goal(goal_id, user_id=principal.user_id)
    return {"status": "restored", "goal": goal.to_dict() if goal else {}}


@app.delete("/goals/{goal_id}")
async def delete_goal_endpoint(goal_id: str, request: Request):
    if goal_manager is None:
        raise HTTPException(status_code=503, detail="GoalManager not initialized")
    principal = get_current_principal(request)
    success, msg = goal_manager.delete_goal(goal_id, user_id=principal.user_id)
    if not success:
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        if "unauthorized" in msg.lower():
            raise HTTPException(status_code=403, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "deleted", "goal_id": goal_id}


class ECSTransitionRequest(BaseModel):
    target_state: str
    reason: str = ""


class ECSInterruptRequest(BaseModel):
    priority: int = 1
    source: str = "user_api"
    reason: str
    action_required: str = "listen"


@app.get("/ecs/state")
async def get_ecs_state():
    if ecs_engine is None:
        raise HTTPException(status_code=503, detail="ExecutiveControlSystem not initialized")
    return ecs_engine.get_state().to_dict()


@app.get("/ecs/health")
async def get_ecs_health():
    if ecs_engine is None:
        raise HTTPException(status_code=503, detail="ExecutiveControlSystem not initialized")
    return ecs_engine.get_health().__dict__


@app.post("/ecs/transition")
async def transition_ecs_state(req: ECSTransitionRequest):
    if ecs_engine is None:
        raise HTTPException(status_code=503, detail="ExecutiveControlSystem not initialized")
    try:
        target = ExecutiveState(req.target_state)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid target state: {req.target_state}")
    success = await ecs_engine.transition_to(target, reason=req.reason)
    return {"success": success, "current_state": ecs_engine.get_state().current_state.value}


@app.post("/ecs/interrupt")
async def inject_ecs_interrupt(req: ECSInterruptRequest):
    if ecs_engine is None:
        raise HTTPException(status_code=503, detail="ExecutiveControlSystem not initialized")
    await ecs_engine.inject_interrupt(req.priority, req.source, req.reason, req.action_required)
    return {"status": "injected", "current_state": ecs_engine.get_state().current_state.value}


# ── Wake Word & Ambient Audio ──

class WakeSimulateRequest(BaseModel):
    phrase: str | None = None
    confidence: float = 0.95


@app.get("/voice/wake-status")
async def get_wake_status():
    if wake_word_engine is None:
        raise HTTPException(status_code=503, detail="WakeWordEngine not initialized")
    return {
        "is_listening": wake_word_engine.is_listening,
        "backend": wake_word_engine.backend,
        "wake_phrase": wake_word_engine.wake_phrase,
        "has_ring_buffer": wake_word_engine.has_ring_buffer,
        "current_marker": wake_word_engine.current_marker,
        "ecs_state": ecs_engine.get_state().current_state.value if ecs_engine else "unknown",
    }


@app.post("/voice/wake/simulate")
async def simulate_wake_trigger(req: WakeSimulateRequest | None = None):
    if wake_word_engine is None:
        raise HTTPException(status_code=503, detail="WakeWordEngine not initialized")
    phrase = req.phrase if req else None
    confidence = req.confidence if req else 0.95
    await wake_word_engine.simulate_wake(phrase=phrase, confidence=confidence)
    return {
        "status": "triggered",
        "phrase": phrase or wake_word_engine.wake_phrase,
        "confidence": confidence,
    }


# ── Portable Backup and Restore Endpoints (P0-2) ──


class BackupCreatePayload(BaseModel):
    user_id: str | None = None
    destination_path: str | None = None
    categories: list[str] | None = None
    comment: str | None = None


class BackupInspectPayload(BaseModel):
    archive_path: str


class BackupValidatePayload(BaseModel):
    archive_path: str
    target_user_id: str | None = None


class BackupRestorePayload(BaseModel):
    archive_path: str
    target_user_id: str | None = None
    confirmation: bool = False
    permission_id: str | None = None


@app.post("/backup/create")
async def create_backup_endpoint(req: BackupCreatePayload, request: Request):
    principal = get_current_principal(request)
    target_uid = req.user_id or principal.user_id
    if not principal.is_owner() and target_uid != principal.user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot create backup for another user")

    mgr = get_backup_restore_manager()
    res = mgr.create_backup(
        user_id=target_uid,
        destination_path=req.destination_path,
        include_categories=req.categories,
        comment=req.comment or "",
    )
    if not res.success:
        raise HTTPException(status_code=500, detail=f"Backup creation failed: {res.error}")

    return res.to_dict()


@app.post("/backup/inspect")
async def inspect_backup_endpoint(req: BackupInspectPayload, request: Request):
    get_current_principal(request)
    mgr = get_backup_restore_manager()
    try:
        manifest = mgr.inspect_backup(req.archive_path)
        return manifest.to_dict()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to inspect backup: {e}")


@app.post("/backup/validate")
async def validate_backup_endpoint(req: BackupValidatePayload, request: Request):
    principal = get_current_principal(request)
    target_uid = req.target_user_id or principal.user_id
    if not principal.is_owner() and target_uid != principal.user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot validate restore for another user")

    mgr = get_backup_restore_manager()
    report = mgr.validate_backup(req.archive_path, target_user_id=target_uid)
    return report.to_dict()


@app.post("/backup/restore")
async def restore_backup_endpoint(req: BackupRestorePayload, request: Request):
    principal = get_current_principal(request)
    target_uid = req.target_user_id or principal.user_id
    if not principal.is_owner() and target_uid != principal.user_id:
        raise HTTPException(status_code=403, detail="Forbidden: Cannot restore backup to another user")

    permission_grant = None
    if req.permission_id:
        if perm_manager is None:
            raise HTTPException(status_code=503, detail="Permission manager not initialized")
        # Check permission grant from perm_manager
        approved_req = next(
            (p for p in perm_manager.history if p.id == req.permission_id and p.status == "approved"),
            None
        )
        if not approved_req:
            raise HTTPException(
                status_code=403,
                detail=f"Permission request '{req.permission_id}' not found or not approved."
            )
        permission_grant = {
            "action": approved_req.action,
            "target": approved_req.target,
            "status": approved_req.status,
            "user_id": approved_req.user_id,
        }

    mgr = get_backup_restore_manager()
    try:
        res = mgr.restore_backup(
            archive_path=req.archive_path,
            target_user_id=target_uid,
            confirmation=req.confirmation,
            permission_grant=permission_grant,
        )
        if not res.success:
            raise HTTPException(status_code=500, detail=f"Restore failed: {res.error}")
        return res.to_dict()
    except BackupAuthorizationError as auth_err:
        raise HTTPException(status_code=403, detail=str(auth_err))
    except (BackupIntegrityError, BackupError) as bkp_err:
        raise HTTPException(status_code=400, detail=str(bkp_err))


# ── Direct invocation ──


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("HELIX_API_PORT", "8000"))
    host = os.getenv("HELIX_API_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "0"):
        host = "127.0.0.1"
    uvicorn.run("api.main:app", host=host, port=port, reload=True)
