"""
Reliable Conversation Handler for HELIX Practice Partner (Tasks 1-8).
Coordinates:
- Ambiguity Gate (Task 7)
- Mode State Machine (Task 3)
- Prompt Builder (Task 4)
- Schema Enforcement & Validation (Task 5)
- Planner Quarantine & Praise Filter (Task 6, 8)
- Multi-Attempt Retry Recovery (up to 3 attempts)
- Pinned Context & Observability (Task 1, 2)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from core_ai.conversation_engine.ambiguity_gate import AmbiguityGate
from core_ai.conversation_engine.mode_state_machine import ConversationMode, ModeStateMachine
from core_ai.conversation_engine.prompt_builder import PromptBuilder
from core_ai.conversation_engine.reliability_pipeline import (
    MODE_SCHEMAS,
    format_model_to_display,
    get_deterministic_fallback,
    parse_and_validate_mode_output,
)
from core_ai.llm_engine.llm_engine import LLMEngine

logger = logging.getLogger("helix.reliable_conversation_handler")


import os
import re
import json
from pathlib import Path

def get_configured_user_name() -> str:
    env_name = os.getenv("HELIX_USER_NAME")
    if env_name:
        return env_name.strip()
    try:
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "config.json"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                name = data.get("user_profile", {}).get("name")
                if name:
                    return str(name).strip()
    except Exception:
        pass
    return "Vaibhav"


class ReliableConversationHandler:
    def __init__(self, llm_engine: LLMEngine):
        self.llm_engine = llm_engine
        self.state_machine = ModeStateMachine()
        self.ambiguity_gate = AmbiguityGate()
        self.prompt_builder = PromptBuilder()

    async def handle_user_message(
        self,
        user_text: str,
        correlation_id: Optional[str] = None,
        session_id: str = "default",
    ) -> dict[str, Any]:
        """
        Process a user turn through the complete reliability pipeline.
        Returns dict containing:
        - response: formatted final text to show to user
        - mode: active ConversationMode
        - retries: number of retries executed
        - intercepted_by_ambiguity_gate: bool
        - metrics: token & finish reason observability
        """
        # 1. Ambiguity Gate (Task 7): intercept misheard or corrupted technical terms
        ambiguity_question = self.ambiguity_gate.check_ambiguity(user_text)
        if ambiguity_question:
            logger.info("Ambiguity gate intercepted user text '%s' -> '%s'", user_text, ambiguity_question)
            self.state_machine.record_turn("user", user_text)
            self.state_machine.record_turn("assistant", ambiguity_question)
            return {
                "response": ambiguity_question,
                "mode": self.state_machine.active_mode,
                "retries": 0,
                "intercepted_by_ambiguity_gate": True,
                "metrics": {"finish_reason": "ambiguity_gate"},
            }

        # 2. Greeting handling per Task 8 / T8: user's name must come from config, spelled in full
        greeting_patterns = [
            r"^(?:hi|hello|hey|greetings|good\s+(?:morning|afternoon|evening))\b",
        ]
        if any(re.search(p, user_text.lower().strip()) for p in greeting_patterns):
            user_name = get_configured_user_name()
            greeting_reply = f"Hello {user_name}! Ready for your Java OOP practice session."
            self.state_machine.record_turn("user", user_text)
            self.state_machine.record_turn("assistant", greeting_reply)
            return {
                "response": greeting_reply,
                "mode": ConversationMode.NORMAL,
                "retries": 0,
                "intercepted_by_ambiguity_gate": False,
                "metrics": {"finish_reason": "greeting_config_name"},
            }

        # 2b. Premise Verification Gate (Layer 2): correct contradicted false premises before generation
        premise_check = self.ambiguity_gate.check_premise(user_text)
        if premise_check and premise_check.get("status") == "contradicted":
            correction_reply = premise_check["correction"]
            logger.info("Premise gate intercepted contradicted false premise '%s' -> '%s'", user_text, correction_reply)
            self.state_machine.record_turn("user", user_text)
            self.state_machine.record_turn("assistant", correction_reply)
            return {
                "response": correction_reply,
                "mode": self.state_machine.active_mode,
                "retries": 0,
                "intercepted_by_ambiguity_gate": True,
                "metrics": {"finish_reason": "premise_verification_correction"},
            }

        # 3. Mode State Machine (Task 3): automatic intent classification & state transition
        mode = self.state_machine.classify_and_transition(user_text)

        # In LISTEN mode, reply is fixed text from code, never model output (Task 3 / Section 4)
        fixed_reply = self.state_machine.get_fixed_reply()
        if fixed_reply:
            self.state_machine.record_turn("user", user_text)
            self.state_machine.record_turn("assistant", fixed_reply)
            return {
                "response": fixed_reply,
                "mode": mode,
                "retries": 0,
                "intercepted_by_ambiguity_gate": False,
                "metrics": {"finish_reason": "fixed_listen_contract"},
            }

        # 3. Prompt Builder (Task 4): Per-turn prompt rebuild with rules, reference points, and last 4 turns
        recent_turns = self.state_machine.get_recent_turns(limit=4)
        messages = self.prompt_builder.build_prompt_messages(
            mode=mode,
            user_text=user_text,
            recent_turns=recent_turns,
            active_topic=self.state_machine.current_topic,
        )

        # 4. Multi-Attempt Retry Loop (Task 5, 6: up to 3 attempts with temperature 0.0-0.2 and schema)
        schema_cls = MODE_SCHEMAS.get(mode)
        schema_json = schema_cls.model_json_schema() if schema_cls else None
        per_mode_caps = {
            ConversationMode.RATE: 150,
            ConversationMode.QUIZ: 100,
            ConversationMode.GRADE: 200,
            ConversationMode.NORMAL: 400,
        }
        num_predict = per_mode_caps.get(mode, 250)

        attempt = 0
        max_retries = 3
        current_messages = list(messages)
        last_metrics: dict[str, Any] = {}

        while attempt < max_retries:
            attempt += 1
            # Adjust temperature per attempt: start deterministic at 0.0
            temp = 0.0 if attempt == 1 else 0.1 * (attempt - 1)
            options = {
                "temperature": temp,
                "num_predict": num_predict,
                "num_ctx": self.llm_engine._context_length,
            }

            chat_res = await self.llm_engine.chat_with_tools(
                messages=current_messages,
                tools=None,
                correlation_id=correlation_id,
                session_id=session_id,
                response_format=schema_json,
                options=options,
            )

            raw_content = chat_res.get("content", "")
            finish_reason = chat_res.get("finish_reason")
            last_metrics = {
                "prompt_tokens": chat_res.get("prompt_tokens"),
                "output_tokens": chat_res.get("output_tokens"),
                "num_ctx": chat_res.get("num_ctx"),
                "finish_reason": finish_reason,
            }

            # Check if truncated by length
            if finish_reason == "length":
                logger.warning("Generation truncated (finish_reason=length) on attempt %d; retrying...", attempt)
                current_messages.append({"role": "user", "content": "Keep your reply much shorter. Follow the mode format exactly."})
                continue

            # Validate output against schema, planner quarantine, and praise ban list
            is_valid, error_reason, model = parse_and_validate_mode_output(mode, raw_content)
            if is_valid:
                formatted_response = format_model_to_display(mode, model, raw_content)
                self.state_machine.record_turn("user", user_text)
                self.state_machine.record_turn("assistant", formatted_response)
                if mode == ConversationMode.QUIZ:
                    self.state_machine.last_quiz_question = formatted_response
                return {
                    "response": formatted_response,
                    "mode": mode,
                    "retries": attempt - 1,
                    "intercepted_by_ambiguity_gate": False,
                    "metrics": last_metrics,
                }

            logger.warning("Validation failed on attempt %d: %s; retrying with corrective prompt...", attempt, error_reason)
            current_messages.append({
                "role": "user",
                "content": f"ERROR: Output constraint violated: {error_reason}. Follow mode rules strictly. No praise words, no planner leakage.",
            })

        # 5. Deterministic clean fallback after all retries exhausted
        fallback = get_deterministic_fallback(mode, self.state_machine.current_topic)
        logger.error("All %d retries exhausted for mode %s; using deterministic fallback: %s", max_retries, mode.value, fallback)
        self.state_machine.record_turn("user", user_text)
        self.state_machine.record_turn("assistant", fallback)
        return {
            "response": fallback,
            "mode": mode,
            "retries": max_retries,
            "intercepted_by_ambiguity_gate": False,
            "metrics": last_metrics,
        }
