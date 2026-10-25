import logging
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger("helix.capability_registry")


@dataclass
class CapabilityDescriptor:
    id: str
    name: str
    description: str
    supported_actions: list[str]
    risk_level: str = "low"  # low | medium | high | critical
    requirements: list[str] = field(default_factory=list)
    health: str = "available"  # available | degraded | unavailable
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CapabilityRegistry:
    def __init__(self):
        self._capabilities: dict[str, CapabilityDescriptor] = {}
        self._register_default_capabilities()

    def _register_default_capabilities(self) -> None:
        defaults = [
            CapabilityDescriptor(
                id="voice_interaction",
                name="Voice Engine & Speech Streaming",
                description="Real-time Faster-Whisper STT and Piper ONNX TTS speech synthesis.",
                supported_actions=["voice.listen", "voice.speak", "voice.tts"],
                risk_level="low",
                requirements=["microphone", "speakers", "cuda"],
            ),
            CapabilityDescriptor(
                id="app_launcher",
                name="Native Application Execution",
                description="Launches discovered Windows desktop software and shell executables.",
                supported_actions=["launch_app", "open_url", "start_process"],
                risk_level="medium",
                requirements=["windows_os"],
            ),
            CapabilityDescriptor(
                id="file_automation",
                name="Filesystem & Workspace Management",
                description="Creates, reads, uploads, and sandboxes local notes and workspace files.",
                supported_actions=["create_note", "upload_file", "list_files", "delete_file"],
                risk_level="medium",
                requirements=["storage_access"],
            ),
            CapabilityDescriptor(
                id="automation_dag",
                name="Automation Flow DAG Engine",
                description="Persists and executes multi-node automated pipeline DAG workflows.",
                supported_actions=["run_pipeline", "schedule_task", "manage_agent"],
                risk_level="high",
                requirements=["event_bus"],
            ),
            CapabilityDescriptor(
                id="local_memory",
                name="DPAPI Encrypted Cognitive Memory",
                description="Stores encrypted conversation history, preferences, and operational context.",
                supported_actions=["store_memory", "retrieve_memory", "archive_memory"],
                risk_level="low",
                requirements=["dpapi_crypto"],
            ),
        ]
        for cap in defaults:
            self._capabilities[cap.id] = cap

    def register(self, capability: CapabilityDescriptor) -> None:
        self._capabilities[capability.id] = capability
        logger.info("Registered capability: %s (%s)", capability.name, capability.id)

    def get(self, capability_id: str) -> CapabilityDescriptor | None:
        return self._capabilities.get(capability_id)

    def list_all(self) -> list[dict[str, Any]]:
        return [cap.to_dict() for cap in self._capabilities.values()]

    def is_action_supported(self, action: str) -> bool:
        for cap in self._capabilities.values():
            if action in cap.supported_actions and cap.health == "available":
                return True
        return False
