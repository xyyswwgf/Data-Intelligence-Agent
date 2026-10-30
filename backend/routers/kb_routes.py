"""知识库 / Wiki / RAG 问答 API。"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import auth
from backend.auth import CurrentUser, get_current_user, require_role
from backend.config import KB_MAX_FILE_MB, RAG_TOP_K
from backend.kb import documents, embeddings, indexer, rag, retrieval, spaces, wiki
from backend import llm_gateway

router = APIRouter(prefix="/api", tags=["knowledge"])

_MEDIA_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class PageCreate(BaseModel):
    space_id: int | None = Field(None, description="放入哪个知识空间，留空为企业空间")
    title: str = Field(..., min_length=1, max_length=300)
    content_md: str = Field("", max_length=500_000)
    parent_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    slug: str | None = Field(None, max_length=180)


class PageUpdate(BaseModel):
    base_version: int = Field(..., ge=1, description="编辑时的版本号（乐观锁）")
    title: str | None = Field(None, min_length=1, max_length=300)
    content_md: str | None = Field(None, max_length=500_000)
    parent_id: int | None = None
    move: bool = Field(False, description="为 true 时才修改 parent_id（允许移动到根目录）")
    tags: list[str] | None = None
    change_note: str | None = Field(None, max_length=300)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(RAG_TOP_K, ge=1, le=12)


# ---------------------------------------------------------------------------
# Wiki
# ---------------------------------------------------------------------------


def _scope(user: CurrentUser, space_id: int | None = None) -> list[int]:
    """本次请求的读取范围：用户可读的全部空间，或其中指定的一个。"""
    readable = spaces.readable_spaces(user.id)
    if space_id is None:
        return readable
    if space_id not in readable:
        raise HTTPException(status_code=404, detail="知识空间不存在")
    return [space_id]


def _require_page(user: CurrentUser, page_id: int, action: str) -> int:
    """返回页面所在空间；不可读时 404，没有写 / 删权限时 403。"""
    space_id = spaces.page_space(page_id)
    if space_id is None or not spaces.can_read(user.id, space_id):
        raise HTTPException(status_code=404, detail="页面不存在")
    allowed = spaces.can_delete if action == "delete" else spaces.can_write
    if action != "read" and not allowed(user.id, user.role, space_id):
        raise HTTPException(status_code=403, detail="没有权限修改这个知识空间的内容")
    return space_id


@router.get("/kb/spaces")
def kb_spaces(user: CurrentUser = Depends(get_current_user)):
    return {"items": spaces.list_spaces(user.id, user.role)}


@router.get("/wiki/tree")
def wiki_tree(space_id: int | None = None, user: CurrentUser = Depends(get_current_user)):
    return {"items": wiki.list_tree(_scope(user, space_id))}


@router.get("/wiki/index")
def wiki_index(space_id: int | None = None, user: CurrentUser = Depends(get_current_user)):
    return wiki.knowledge_index(_scope(user, space_id))


@router.get("/wiki/tags/{tag}")
def wiki_tag(tag: str, user: CurrentUser = Depends(get_current_user)):
    return {"tag": tag, "items": wiki.pages_by_tag(tag, _scope(user))}


@router.get("/wiki/search")
def wiki_search(q: str = Query(..., min_length=1, max_length=200), user: CurrentUser = Depends(get_current_user)):
    return retrieval.search_pages(q, spaces=_scope(user))


@router.get("/wiki/pages/{slug}")
def wiki_page(slug: str, user: CurrentUser = Depends(get_current_user)):
    page = wiki.get_page(slug, _scope(user))
    if not page:
        raise HTTPException(status_code=404, detail=f"页面 {slug} 不存在")
    page["can_write"] = spaces.can_write(user.id, user.role, page["space_id"])
    page["can_delete"] = spaces.can_delete(user.id, user.role, page["space_id"])
    return page


@router.post("/wiki/pages", status_code=201)
def wiki_create(req: PageCreate, user: CurrentUser = Depends(get_current_user)):
    space_id = req.space_id or _scope(user)[0]  # 默认企业空间
    if not spaces.can_write(user.id, user.role, space_id):
        raise HTTPException(status_code=403, detail="没有权限在这个知识空间新建页面")
    try:
        page = wiki.create_page(req.title, req.content_md, user.id, req.parent_id, req.tags, req.slug, space_id=space_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auth.audit("wiki_create", user, "wiki_page", page["id"], {"title": page["title"], "slug": page["slug"], "space_id": space_id})
    return page


@router.put("/wiki/pages/{page_id}")
def wiki_update(page_id: int, req: PageUpdate, user: CurrentUser = Depends(get_current_user)):
    _require_page(user, page_id, "write")
    try:
        page = wiki.update_page(
            page_id, user.id, req.base_version, title=req.title, content_md=req.content_md,
            parent_id=req.parent_id if req.move else ..., tags=req.tags, change_note=req.change_note,
        )
    except wiki.ConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    auth.audit("wiki_update", user, "wiki_page", page_id, {"version": page["version"], "note": req.change_note})
    return page


@router.delete("/wiki/pages/{page_id}")
def wiki_delete(page_id: int, user: CurrentUser = Depends(get_current_user)):
    _require_page(user, page_id, "delete")
    try:
        page = wiki.delete_page(page_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    auth.audit("wiki_delete", user, "wiki_page", page_id, {"title": page["title"], "slug": page["slug"]})
    return {"deleted": page["slug"]}


@router.get("/wiki/pages/{page_id}/versions")
def wiki_versions(page_id: int, user: CurrentUser = Depends(get_current_user)):
    _require_page(user, page_id, "read")
    return {"items": wiki.list_versions(page_id)}


@router.get("/wiki/pages/{page_id}/versions/{version}")
def wiki_version(page_id: int, version: int, user: CurrentUser = Depends(get_current_user)):
    _require_page(user, page_id, "read")
    snapshot = wiki.get_version(page_id, version)
    if not snapshot:
        raise HTTPException(status_code=404, detail="版本不存在")
    return snapshot


@router.post("/wiki/pages/{page_id}/versions/{version}/restore")
def wiki_restore(page_id: int, version: int, user: CurrentUser = Depends(get_current_user)):
    _require_page(user, page_id, "write")
    try:
        page = wiki.restore_version(page_id, version, user.id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    auth.audit("wiki_restore", user, "wiki_page", page_id, {"restored_version": version})
    return page


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------


@router.post("/kb/documents", status_code=201)
def upload_document(
    file: UploadFile = File(...),
    parent_id: int | None = Form(None),
    tags: str | None = Form(None, description="逗号分隔的标签"),
    space_id: int | None = Form(None, description="放入哪个知识空间，留空为企业空间"),
    user: CurrentUser = Depends(get_current_user),
):
    space_id = space_id or _scope(user)[0]
    if not spaces.can_write(user.id, user.role, space_id):
        raise HTTPException(status_code=403, detail="没有权限向这个知识空间上传文档")
    data = file.file.read(KB_MAX_FILE_MB * 1024 * 1024 + 1)
    tag_list = [t.strip() for t in (tags or "").replace("，", ",").split(",") if t.strip()]
    try:
        result = documents.ingest(file.filename or "", data, user.id, parent_id, tag_list, space_id=space_id)
    except documents.DuplicateDocument as exc:
        raise HTTPException(status_code=409, detail={
            "error": "duplicate", "message": f"该文件已导入过（{exc.document['filename']}）",
            "page_slug": exc.document["page_slug"],
        })
    except ValueError as exc:
        auth.audit("document_upload_failed", user, "document", None, {"filename": file.filename, "error": str(exc)})
        raise HTTPException(status_code=422, detail=str(exc))
    auth.audit("document_upload", user, "document", result["document_id"],
               {"filename": result["filename"], "page": result["page"]["slug"]})
    return result


@router.get("/kb/documents/{document_id}/download")
def download_document(document_id: int, user: CurrentUser = Depends(get_current_user)):
    found = documents.get_document_file(document_id, _scope(user))
    if not found:
        raise HTTPException(status_code=404, detail="原始文件不存在")
    filename, file_type, path = found
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(file_type, "application/octet-stream"),
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


# ---------------------------------------------------------------------------
# 问答 / 运维
# ---------------------------------------------------------------------------


@router.post("/kb/ask")
def ask(req: AskRequest, user: CurrentUser = Depends(get_current_user)):
    result = rag.ask(req.question, req.top_k, spaces=_scope(user))
    auth.audit("kb_ask", user, "question", None, {"question": req.question[:200], "hits": len(result["citations"])})
    return result


@router.get("/admin/system")
def system_status(user: CurrentUser = Depends(require_role("admin"))):
    return {
        "embedding": embeddings.health(),
        "llm_configured": llm_gateway.llm_configured(),
        "llm": llm_gateway.describe(),
        "pending_index": len(indexer.pending_page_ids()),
    }


@router.post("/admin/kb/reindex")
def reindex(all_pages: bool = Query(False), user: CurrentUser = Depends(require_role("admin"))):
    count = indexer.reindex(all_pages)
    auth.audit("kb_reindex", user, "knowledge_base", None, {"pages": count, "all": all_pages})
    return {"queued": count}
