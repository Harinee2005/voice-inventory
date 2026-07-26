"""
Episodic memory compression for ARIA.

After every COMPRESSION_BATCH_SIZE turns in a session, the oldest batch
is compressed into a short summary and stored in session_summaries. On
the next turn, load_context_node loads the summary + the last
RECENT_TURNS_KEPT raw turns instead of all 30.

This keeps the context window predictable and prevents old turns from
being silently dropped — they become a compact digest instead.
"""

import json
import logging
import os
from typing import Optional

from sqlalchemy.orm import Session

from models import ConversationMessage, SessionSummary

logger = logging.getLogger(__name__)

COMPRESSION_BATCH_SIZE = int(os.getenv("COMPRESSION_BATCH_SIZE", "10"))
RECENT_TURNS_KEPT = int(os.getenv("RECENT_TURNS_KEPT", "10"))


async def maybe_compress_session(session_id: str, db: Session) -> None:
    """
    Called after execute_node. If the session has accumulated enough uncompressed
    turns, compress the oldest batch into a digest.

    A turn is "uncompressed" if its id is greater than the turn_end of the
    latest existing summary for this session.
    """
    from clients.llm_client import get_llm_client

    # Find highest already-compressed turn_end for this session
    latest_summary = (
        db.query(SessionSummary)
        .filter(SessionSummary.session_id == session_id)
        .order_by(SessionSummary.turn_end.desc())
        .first()
    )
    since_id = latest_summary.turn_end if latest_summary else 0

    # Count uncompressed turns since last summary
    uncompressed = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.session_id == session_id,
            ConversationMessage.id > since_id,
        )
        .order_by(ConversationMessage.id.asc())
        .all()
    )

    if len(uncompressed) < COMPRESSION_BATCH_SIZE:
        return  # Not enough turns yet

    # Take exactly one batch (oldest COMPRESSION_BATCH_SIZE turns)
    batch = uncompressed[:COMPRESSION_BATCH_SIZE]
    first_id = batch[0].id
    last_id = batch[-1].id

    transcript = "\n".join(
        f"{m.role.upper()}: {m.content}" for m in batch
    )

    prompt = (
        "You are summarising a short conversation between a restaurant kitchen worker "
        "and an AI inventory assistant (ARIA). Produce a concise 3-5 sentence digest "
        "that captures: what items were discussed, what actions were taken or confirmed, "
        "any corrections or clarifications made, and the worker's apparent intent. "
        "Do not include filler phrases. Output plain text only.\n\n"
        f"Conversation:\n{transcript}"
    )

    try:
        from utils.llm_retry import call_llm
        from clients.llm_client import get_llm_model
        mini_model = get_llm_model(intent=True)
        response = await call_llm(
            lambda: get_llm_client().messages.create(
                model=mini_model,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            ),
            label="intent",
            model=mini_model,
        )
        summary_text = response.content[0].text.strip() if response.content else ""
        db.add(SessionSummary(
            session_id=session_id,
            summary=summary_text,
            turn_start=first_id,
            turn_end=last_id,
        ))
        db.commit()
        logger.info(
            "EPISODIC COMPRESS  session=%s  turns=%d–%d  summary_len=%d",
            session_id, first_id, last_id, len(summary_text),
        )
    except Exception as exc:
        logger.warning("EPISODIC COMPRESS FAILED  session=%s  error=%s", session_id, exc)


def load_compressed_history(session_id: str, db: Session) -> tuple[str, list[dict]]:
    """
    Returns (digest_text, recent_raw_turns).

    digest_text : concatenated summaries of all compressed batches (or "" if none)
    recent_raw_turns : last RECENT_TURNS_KEPT raw turns not yet compressed
    """
    latest_summary = (
        db.query(SessionSummary)
        .filter(SessionSummary.session_id == session_id)
        .order_by(SessionSummary.turn_end.desc())
        .first()
    )

    since_id = latest_summary.turn_end if latest_summary else 0

    # All summaries in chronological order
    summaries = (
        db.query(SessionSummary)
        .filter(SessionSummary.session_id == session_id)
        .order_by(SessionSummary.turn_start.asc())
        .all()
    )

    digest = ""
    if summaries:
        parts = [f"[Earlier in this session]\n{s.summary}" for s in summaries]
        digest = "\n\n".join(parts)

    # Uncompressed turns since last summary (most recent RECENT_TURNS_KEPT)
    recent_msgs = (
        db.query(ConversationMessage)
        .filter(
            ConversationMessage.session_id == session_id,
            ConversationMessage.id > since_id,
        )
        .order_by(ConversationMessage.id.desc())
        .limit(RECENT_TURNS_KEPT)
        .all()
    )
    recent_msgs = list(reversed(recent_msgs))

    recent_turns = [{"role": m.role, "content": m.content} for m in recent_msgs]
    return digest, recent_turns
