"""Single authorized entry point for web and Telegram questions."""

import logging
import re
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields
from app.models.request import InvoiceRequest
from app.permissions import permissions
from app.services import answer_generator, embedding_service, vector_store
from app.services.chat_context import request_context, serialize

logger = logging.getLogger(__name__)
DENIED = "You are not authorized to view this client information, or the record could not be found."


class RagAnswer(str):
    """String-compatible answer shared with Telegram, with optional web evidence."""

    def __new__(cls, answer, chunks_used=0, *, sources=None, coverage=None, has_more_items=False):
        obj = super().__new__(cls, answer)
        obj.chunks_used = chunks_used
        obj.sources = sources or []
        obj.coverage = coverage
        obj.has_more_items = has_more_items
        return obj


def authorized_requests(user):
    stmt = select(InvoiceRequest)
    if user.role.value != "SUPER_ADMIN":
        stmt = stmt.where(
            InvoiceRequest.client_id.in_(
                select(ClientAssignment.client_id).where(
                    ClientAssignment.user_id == user.user_id, ClientAssignment.is_active.is_(True)
                )
            )
        )
    return stmt


def authorized_clients(user):
    stmt = select(Client)
    if user.role.value != "SUPER_ADMIN":
        stmt = stmt.where(
            Client.client_id.in_(
                select(ClientAssignment.client_id).where(
                    ClientAssignment.user_id == user.user_id, ClientAssignment.is_active.is_(True)
                )
            )
        )
    return stmt


def mentioned(question, value):
    return bool(value and re.search(r"(?<!\w)" + re.escape(str(value)) + r"(?!\w)", question, re.I))


def explicit_targets(question):
    """Recognize explicit selectors without ever querying an inaccessible identifier."""
    targets = re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", question, re.I)
    targets += re.findall(r"\b[A-Za-z]{2,}[-/]\d[\w/-]*", question)
    targets += re.findall(r'["]([^"\n]+)["]', question)
    # Labeled identifiers, including client/company names, stop at question qualifiers.
    pattern = (
        r"\b(?:client|company|invoice|request)\s+(?:named\s+|number\s+|id\s+|#\s*)?"
        r"([\w][\w .&/-]*?)(?=\s+(?:and|or|with|including|which|what|that|is|has|status|details|"
        r"information|invoices|active|inactive)\b|[?;,\n]|$)"
    )
    generic = {
        "this",
        "that",
        "these",
        "those",
        "selected",
        "current",
        "name",
        "code",
        "profile",
        "chat",
        "pic",
        "information",
        "details",
        "status",
        "statuses",
        "numbers",
        "number",
        "items",
        "records",
        "amounts",
        "content",
        "contents",
        "history",
        "progress",
        "totals",
        "taxes",
        "is",
        "are",
        "has",
        "and",
    }
    for match in re.finditer(pattern, question, re.I):
        value = re.split(
            r"\s+(?:and|or|with|including|which|what|that|is|has|status|details|information|invoices)\b",
            match.group(1),
            maxsplit=1,
            flags=re.I,
        )[0].strip(" .?/")
        if value and value.lower().split()[0] not in generic:
            targets.append(value)
    for match in re.finditer(
        r"\b(?:status of|details (?:of|for)|information (?:about|on)|invoices? for|about)\s+([^?;:\n]+)", question, re.I
    ):
        value = re.split(r"\s+(?:with|including|which|what|that|is|has)\b", match.group(1), maxsplit=1, flags=re.I)[
            0
        ].strip(" .")
        if value and value.lower().split()[0] not in generic | {
            "my",
            "our",
            "the",
            "your",
            "all",
            "an",
            "a",
            "me",
            "each",
            "every",
        }:
            targets.append(value)
    return [re.sub(r"^(?:company|client|invoice|request)\s+", "", target, flags=re.I).strip() for target in targets]


async def ask(question, user, db, request_id=None, *, item_page=0) -> str:
    question = question.strip()
    if not question or len(question) > 2000:
        raise HTTPException(422, "Enter a question between 1 and 2000 characters.")
    if getattr(user, "must_change_password", False):
        raise HTTPException(403, "Change your temporary password in the web app before continuing.")
    capabilities = permissions(user)
    if "chat" not in capabilities:
        raise HTTPException(403, "Amiga AI is unavailable for this role.")
    if not user.is_active:
        raise HTTPException(403, "This account is inactive.")
    if not isinstance(item_page, int) or not 0 <= item_page <= 10000:
        raise HTTPException(422, "Invalid line-item page.")
    authorized = authorized_requests(user)
    if request_id is not None:
        request_id = uuid.UUID(str(request_id))
        if not await db.scalar(authorized.where(InvoiceRequest.request_id == request_id)):
            raise HTTPException(404, DENIED)
    # Only identifiers from the authorized relational scope participate in resolution.
    clients = list((await db.scalars(authorized_clients(user))).all())
    rows = (
        await db.execute(
            authorized.add_columns(HeaderFields.invoice_number)
            .outerjoin(Draft, Draft.request_id == InvoiceRequest.request_id)
            .outerjoin(HeaderFields, HeaderFields.draft_id == Draft.draft_id)
        )
    ).all()
    known = [str(r.request_id) for r, _ in rows] + [n for _, n in rows if n]
    known += [v for c in clients for v in (c.client_name, c.client_code) if v]
    targets = explicit_targets(question)
    # Split comparisons only when the full selector is not itself a known company name.
    targets = [
        part.strip()
        for target in targets
        for part in (
            [target]
            if target.casefold() in {str(v).casefold() for v in known}
            else re.split(r"\s+(?:and|or|versus|vs)\s+", target, flags=re.I)
        )
    ]
    if any(target.casefold() not in {str(value).casefold() for value in known} for target in targets):
        return RagAnswer(DENIED)
    matched_clients = [c for c in clients if mentioned(question, c.client_name) or mentioned(question, c.client_code)]
    matched_rows = [(r, n) for r, n in rows if mentioned(question, r.request_id) or mentioned(question, n)]
    if matched_clients and matched_rows:
        matching_company_ids = {c.client_id for c in matched_clients}
        matched_rows = [(r, n) for r, n in matched_rows if r.client_id in matching_company_ids]
        if not matched_rows:
            return RagAnswer("The invoice and company selectors do not match. Please clarify the company or invoice.")
    names = [c.client_name.casefold() for c in matched_clients]
    if len(names) != len(set(names)) and request_id is None and len(matched_rows) != 1:
        return RagAnswer("Several accessible companies match that name. Include the company code or choose a request.")
    if len({r.request_id for r, _ in matched_rows}) > 1:
        # Shared invoice numbers need disambiguation; explicitly requested distinct numbers may be compared.
        numbers = [n.casefold() for _, n in matched_rows if n and mentioned(question, n)]
        if len(numbers) != len(set(numbers)) and request_id is None:
            return RagAnswer("Several accessible requests match that invoice number. Choose a request in Search scope.")
    ids = {r.request_id for r, _ in matched_rows}
    if matched_clients:
        client_request_ids = {r.request_id for r, _ in rows if r.client_id in {c.client_id for c in matched_clients}}
        if ids and not ids <= client_request_ids:
            return RagAnswer("The invoice and company selectors do not match. Please clarify the company or invoice.")
        if not ids:
            ids = client_request_ids
    if request_id is not None:
        if ids and request_id not in ids:
            return RagAnswer(
                "That question refers to a different scope. Choose the matching request or all authorized records."
            )
        ids = {request_id}
    semantic_ids = []
    if not ids and not matched_clients:
        try:
            vector = await embedding_service.embed(question)
            retrieved = await vector_store.similarity_search(
                vector,
                8,
                db,
                filter_request_id=request_id,
                user_id=None if user.role.value == "SUPER_ADMIN" else user.user_id,
            )
            semantic_ids = [c.request_id for c in retrieved]
        except Exception as exc:
            # Live records remain usable during embedding outages. Never log SDK error text.
            logger.warning("Semantic retrieval unavailable (%s)", type(exc).__name__)
        ids = set(semantic_ids)
        # Include unindexed requests, and use live data exclusively for all selected requests.
        ids.update(r.request_id for r, _ in sorted(rows, key=lambda row: str(row[0].updated_at), reverse=True)[:5])
    live_order = [
        r.request_id
        for r, _ in sorted(rows, key=lambda row: (str(row[0].updated_at), str(row[0].request_id)), reverse=True)
    ]
    known_ids = set(live_order)
    # Preserve similarity rank, then fill with recent live requests. Unknown vector IDs
    # cannot consume the result budget or cause an inaccessible content lookup.
    selected_ids = list(dict.fromkeys(rid for rid in semantic_ids + live_order if rid in ids & known_ids))[:5]
    # Recheck assignments immediately before loading any content; do not trust vector metadata.
    selected = (
        list(
            (
                await db.scalars(
                    authorized.where(InvoiceRequest.request_id.in_(selected_ids)).execution_options(
                        populate_existing=True
                    )
                )
            ).all()
        )
        if selected_ids
        else []
    )
    texts, sources, coverage = [], [], []
    request_texts, company_texts = {}, {}
    has_more = False
    for req in selected:
        text, source, description, more = await request_context(
            req, db, include_history="history" in capabilities, item_page=item_page
        )
        request_texts[req.request_id] = (text, source, description)
        has_more |= more
    # Company-only questions must work even when a company has no requests.
    company_ids = {c.client_id for c in matched_clients}
    if not selected and not targets and not company_ids:
        company_ids = {c.client_id for c in clients[:5]}
    if company_ids:
        fresh_clients = list(
            (
                await db.scalars(
                    authorized_clients(user)
                    .where(Client.client_id.in_(company_ids))
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        for company in fresh_clients:
            company_texts[company.client_id] = (
                serialize(
                    {
                        "source": f"company:{company.client_id}",
                        "client_name": company.client_name,
                        "client_code": company.client_code,
                        "is_active": company.is_active,
                        "note": "No additional company profile fields are recorded.",
                    }
                ),
                {"client_id": str(company.client_id), "label": company.client_name},
            )
    # A second live scope check after context loading also protects against revocation
    # during database reads. No cached scope or embedding metadata is authoritative.
    final_ids = (
        set(
            (
                await db.scalars(
                    authorized.with_only_columns(InvoiceRequest.request_id).where(
                        InvoiceRequest.request_id.in_(list(request_texts))
                    )
                )
            ).all()
        )
        if request_texts
        else set()
    )
    final_clients = (
        set(
            (
                await db.scalars(
                    authorized_clients(user)
                    .with_only_columns(Client.client_id)
                    .where(Client.client_id.in_(list(company_texts)))
                )
            ).all()
        )
        if company_texts
        else set()
    )
    for rid, (text, source, description) in request_texts.items():
        if rid in final_ids:
            texts.append(text)
            sources.append(source)
            coverage.append(description)
    for cid, (text, source) in company_texts.items():
        if cid in final_clients:
            texts.append(text)
            sources.append(source)
    if not texts:
        return RagAnswer(
            DENIED if targets or request_id else "I could not find accessible information to answer that question."
        )
    summary = f"Based on {len(final_ids)} accessible requests; " "request results may be partial. " + " ".join(coverage)
    if has_more:
        summary += " More line items are available on the next page."
    if any("audit_timeline" in t for t in texts):
        summary += " Audit history reflects available recorded milestones, not guaranteed complete history."
    texts.append(serialize({"coverage": summary}))
    answer = await answer_generator.generate(question, texts)
    return RagAnswer(answer, len(texts) - 1, sources=sources, coverage=summary, has_more_items=has_more)
