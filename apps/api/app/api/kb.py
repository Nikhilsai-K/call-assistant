from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.auth import Principal, current_principal
from app.db.session import get_session
from app.models import KbDocument, KnowledgeBase

router = APIRouter(prefix="/kb", tags=["kb"])


class KbCreate(BaseModel):
    name: str
    source_type: str  # upload | url | csv
    source_config: dict = {}


class KbRead(BaseModel):
    id: UUID
    name: str
    source_type: str
    version: int

    model_config = {"from_attributes": True}


class KbDocumentCreate(BaseModel):
    title: str
    content: str
    source_url: str | None = None


@router.post("", response_model=KbRead, status_code=status.HTTP_201_CREATED)
async def create_kb(body: KbCreate, p: Principal = Depends(current_principal)) -> KbRead:
    async with get_session(p.org_id) as s:
        kb = KnowledgeBase(
            org_id=UUID(p.org_id),
            name=body.name,
            source_type=body.source_type,
            source_config=body.source_config,
        )
        s.add(kb)
        await s.flush()
        await s.refresh(kb)
        return KbRead.model_validate(kb)


@router.post("/{kb_id}/documents", status_code=status.HTTP_201_CREATED)
async def add_document(
    kb_id: UUID, body: KbDocumentCreate, p: Principal = Depends(current_principal)
) -> dict:
    async with get_session(p.org_id) as s:
        kb = await s.get(KnowledgeBase, kb_id)
        if kb is None or str(kb.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        doc = KbDocument(
            kb_id=kb_id,
            title=body.title,
            content=body.content,
            source_url=body.source_url,
        )
        s.add(doc)
        await s.flush()
        await s.refresh(doc)

        # Enqueue embedding job via Redis stream, picked up by worker.
        import redis.asyncio as aioredis

        from app.core.config import get_settings

        r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        await r.xadd(
            "vocalflow.kb.index",
            {"doc_id": str(doc.id), "kb_id": str(kb_id), "org_id": p.org_id},
        )
        await r.aclose()
        return {"id": str(doc.id), "queued": True}


@router.post("/{kb_id}/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_kb(kb_id: UUID, p: Principal = Depends(current_principal)) -> dict:
    async with get_session(p.org_id) as s:
        kb = await s.get(KnowledgeBase, kb_id)
        if kb is None or str(kb.org_id) != p.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND)

    import redis.asyncio as aioredis

    from app.core.config import get_settings

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    await r.xadd("vocalflow.kb.sync", {"kb_id": str(kb_id), "org_id": p.org_id})
    await r.aclose()
    return {"queued": True}
