import logging
from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.learning_companion")

_DSA_KEYWORDS = {
    "binary search", "quicksort", "mergesort", "dynamic programming",
    "graph", "tree", "dijkstra", "sorting", "recursion", "linked list",
    "array", "hash map", "binary tree", "bst", "heap", "stack", "queue"
}

class LearningCompanion:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    async def start(self) -> None:
        self._event_handler_map = {
            "memory.conversation.stored": self._on_conversation_stored,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("Learning Companion started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("Learning Companion stopped")

    async def _on_conversation_stored(self, event: HelixEvent) -> None:
        role = event.payload.get("role", "")
        content = event.payload.get("content", "") or ""
        
        if role != "user":
            return
            
        content_lower = content.lower()
        matched_keywords = [kw for kw in _DSA_KEYWORDS if kw in content_lower]
        
        if matched_keywords:
            kw_str = ", ".join(matched_keywords)
            logger.info("Learning Companion detected interest in DSA topics: %s", kw_str)
            # Recommend a related topic and trigger bilingual suggestion
            await self._event_bus.publish_event(
                source="learning_companion",
                event_type="productivity.suggestion.create",
                payload={
                    "action": "dsa_review",
                    "title": f"Bilingual explanation: {matched_keywords[0]}",
                    "description": f"Learn '{matched_keywords[0]}' bilingually. Aap is topic ko Hindi/English mix (Hinglish) mein dynamic code examples ke sath samajh sakte hain.",
                    "workflow": {"action": "dsa_explain", "params": {"topic": matched_keywords[0]}},
                    "status": "detected",
                }
            )
from typing import Any
