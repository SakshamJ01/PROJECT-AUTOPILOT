"""WORKFLOW STATE MACHINE — Phase 0.
States and transitions from AGENTS.md / IMPLEMENTATION_PLAN.md.
Illegal transitions are blocked explicitly.
"""
from __future__ import annotations
from enum import Enum, auto
from pydantic import BaseModel, Field
from typing import Optional


class WorkflowState(str, Enum):
    IDEA = "IDEA"
    RESEARCHING = "RESEARCHING"
    RESEARCHED = "RESEARCHED"
    SCRIPTING = "SCRIPTING"
    SCRIPTED = "SCRIPTED"
    ASSET_PREPARING = "ASSET_PREPARING"
    ASSETS_READY = "ASSETS_READY"
    VOICING = "VOICING"
    VOICE_READY = "VOICE_READY"
    EDITING = "EDITING"
    RENDERING = "RENDERING"
    RENDERED = "RENDERED"
    QA = "QA"
    APPROVED = "APPROVED"
    READY = "READY"
    PUBLISHING = "PUBLISHING"
    PUBLISHED = "PUBLISHED"
    LEARNED = "LEARNED"
    REGENERATING = "REGENERATING"
    NEEDS_REVIEW = "NEEDS_REVIEW"

    # Failure states
    FAILED_RESEARCH = "FAILED_RESEARCH"
    FAILED_SCRIPT = "FAILED_SCRIPT"
    FAILED_ASSETS = "FAILED_ASSETS"
    FAILED_TTS = "FAILED_TTS"
    FAILED_RENDER = "FAILED_RENDER"
    FAILED_QA = "FAILED_QA"
    FAILED_PUBLISH = "FAILED_PUBLISH"


class TransitionRecord(BaseModel):
    from_state: WorkflowState
    to_state: WorkflowState
    reason: Optional[str] = None
    idempotency_key: Optional[str] = None


# Legal transition map: from_state -> set of allowed to_states
_TRANSITIONS: dict[WorkflowState, set[WorkflowState]] = {
    WorkflowState.IDEA: {
        WorkflowState.RESEARCHING,
        WorkflowState.FAILED_RESEARCH,
    },
    WorkflowState.RESEARCHING: {
        WorkflowState.RESEARCHED,
        WorkflowState.FAILED_RESEARCH,
    },
    WorkflowState.RESEARCHED: {
        WorkflowState.SCRIPTING,
        WorkflowState.FAILED_SCRIPT,
    },
    WorkflowState.SCRIPTING: {
        WorkflowState.SCRIPTED,
        WorkflowState.FAILED_SCRIPT,
    },
    WorkflowState.SCRIPTED: {
        WorkflowState.ASSET_PREPARING,
        WorkflowState.FAILED_ASSETS,
    },
    WorkflowState.ASSET_PREPARING: {
        WorkflowState.ASSETS_READY,
        WorkflowState.FAILED_ASSETS,
    },
    WorkflowState.ASSETS_READY: {
        WorkflowState.VOICING,
        WorkflowState.FAILED_TTS,
    },
    WorkflowState.VOICING: {
        WorkflowState.VOICE_READY,
        WorkflowState.FAILED_TTS,
    },
    WorkflowState.VOICE_READY: {
        WorkflowState.EDITING,
        WorkflowState.FAILED_RENDER,
    },
    WorkflowState.EDITING: {
        WorkflowState.RENDERING,
        WorkflowState.FAILED_RENDER,
    },
    WorkflowState.RENDERING: {
        WorkflowState.RENDERED,
        WorkflowState.FAILED_RENDER,
    },
    WorkflowState.RENDERED: {
        WorkflowState.QA,
        WorkflowState.FAILED_QA,
    },
    WorkflowState.QA: {
        WorkflowState.APPROVED,
        WorkflowState.FAILED_QA,
        WorkflowState.REGENERATING,
        WorkflowState.NEEDS_REVIEW,
    },
    WorkflowState.APPROVED: {
        WorkflowState.PUBLISHING,
        WorkflowState.FAILED_PUBLISH,
        WorkflowState.READY,
    },
    WorkflowState.READY: {
        WorkflowState.PUBLISHING,
        WorkflowState.PUBLISHED,
        WorkflowState.FAILED_PUBLISH,
        WorkflowState.IDEA,
    },
    WorkflowState.REGENERATING: {
        WorkflowState.SCRIPTING,
        WorkflowState.VOICING,
        WorkflowState.RENDERING,
        WorkflowState.QA,
        WorkflowState.NEEDS_REVIEW,
        WorkflowState.IDEA,
    },
    WorkflowState.NEEDS_REVIEW: {
        WorkflowState.IDEA,
        WorkflowState.REGENERATING,
        WorkflowState.APPROVED,
    },
    WorkflowState.PUBLISHING: {
        WorkflowState.PUBLISHED,
        WorkflowState.FAILED_PUBLISH,
    },
    WorkflowState.PUBLISHED: {
        WorkflowState.LEARNED,
    },
    WorkflowState.LEARNED: {
        WorkflowState.IDEA,  # loop / next batch
    },
    # Failure states
    WorkflowState.FAILED_RESEARCH: {
        WorkflowState.IDEA,
    },
    WorkflowState.FAILED_SCRIPT: {
        WorkflowState.RESEARCHED,
        WorkflowState.IDEA,
        WorkflowState.REGENERATING,
        WorkflowState.NEEDS_REVIEW,
    },
    WorkflowState.FAILED_ASSETS: {
        WorkflowState.SCRIPTED,
        WorkflowState.IDEA,
    },
    WorkflowState.FAILED_TTS: {
        WorkflowState.ASSETS_READY,
        WorkflowState.IDEA,
    },
    WorkflowState.FAILED_RENDER: {
        WorkflowState.VOICE_READY,
        WorkflowState.IDEA,
    },
    WorkflowState.FAILED_QA: {
        WorkflowState.RENDERED,
        WorkflowState.IDEA,
        WorkflowState.REGENERATING,
        WorkflowState.NEEDS_REVIEW,
    },
    WorkflowState.FAILED_PUBLISH: {
        WorkflowState.APPROVED,
        WorkflowState.READY,
        WorkflowState.IDEA,
    },
}


def is_transition_legal(from_state: WorkflowState, to_state: WorkflowState) -> bool:
    allowed = _TRANSITIONS.get(from_state, set())
    return to_state in allowed


def validate_transition(from_state: WorkflowState, to_state: WorkflowState) -> TransitionRecord:
    if from_state == to_state:
        # Idempotent stay is allowed but recorded
        return TransitionRecord(from_state=from_state, to_state=to_state, reason="idempotent_stay")
    if not is_transition_legal(from_state, to_state):
        raise ValueError(
            f"Illegal transition from {from_state.value} to {to_state.value}"
        )
    return TransitionRecord(from_state=from_state, to_state=to_state, reason="valid")
