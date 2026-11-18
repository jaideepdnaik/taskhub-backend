"""Ask TaskHub's question flow: retrieve -> generate -> cite -> save."""
from django.utils import timezone

from . import llm
from .models import Message, MessageRole
from .retrieval import retrieve

HISTORY_TURNS = 10  # earlier messages sent along for follow-up questions
TITLE_LENGTH = 80


def title_from(question):
    question = " ".join(question.split())
    if len(question) <= TITLE_LENGTH:
        return question
    return question[: TITLE_LENGTH - 1].rstrip() + "…"


def ask(conversation, membership, question):
    """Answer `question` in `conversation` and save both turns.

    Raises llm.AssistantError if no answer could be produced; nothing is saved then.
    Returns the assistant Message.
    """
    history = list(conversation.messages.order_by("-created_at", "-id")[:HISTORY_TURNS])[::-1]

    # Follow-ups ("who owns those?") are vague on their own: search with the
    # previous question too, and keep the sources the last answer cited.
    previous_question = next(
        (m.content for m in reversed(history) if m.role == MessageRole.USER), ""
    )
    previous_answer = next((m for m in reversed(history) if m.role == MessageRole.ASSISTANT), None)
    carried = [c["chunk_id"] for c in previous_answer.citations] if previous_answer else []

    sources = retrieve(
        membership,
        f"{previous_question}\n{question}".strip(),
        project=conversation.project,
        include_chunk_ids=carried,
    )

    messages = [{"role": m.role, "content": m.content} for m in history]
    messages.append(
        {"role": "user", "content": llm.build_question(question, sources, timezone.localdate())}
    )
    completion = llm.get_backend().generate(llm.SYSTEM_PROMPT, messages)
    answer, citations = llm.extract_citations(completion.text, sources)

    Message.objects.create(conversation=conversation, role=MessageRole.USER, content=question)
    reply = Message.objects.create(
        conversation=conversation,
        role=MessageRole.ASSISTANT,
        content=answer,
        citations=citations,
        model=completion.model,
        usage=completion.usage,
    )
    conversation.save(update_fields=["updated_at"])  # bump it to the top of the list
    return reply
