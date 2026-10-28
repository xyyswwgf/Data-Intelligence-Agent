"""门店数据导入 API：网页上传销售台账、收款明细、ERP、库龄表、门店指导书，写入门店业务表并更新知识库。"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from backend import auth
from backend.auth import CurrentUser, require_role
from backend.config import KB_MAX_FILE_MB
from backend.store_data.loader import SUPPORTED_SUFFIXES, load

router = APIRouter(prefix="/api/store-data", tags=["store-data"])

MAX_FILES = 30


def _safe_name(filename: str | None) -> str:
    # 门店、月份从文件名识别，保留原文件名，只去掉路径部分
    name = Path((filename or "").replace("\\", "/")).name.strip()
    if not name or name.startswith(("~$", ".")) or Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
        raise HTTPException(status_code=400, detail=f"不支持的文件：{filename or '未命名'}，请上传 .xlsx / .xls 文件")
    return name


@router.post("/import")
def import_store_data(
    files: list[UploadFile] = File(..., description="门店数据文件，可多选"),
    dry_run: bool = Form(False, description="只识别、不写入"),
    user: CurrentUser = Depends(require_role("editor")),
):
    """
    识别并导入门店数据。只替换上传文件覆盖到的门店-月份 / 单据，其他月份保留；
    指导书、月度任务生成知识库页面，索引在后台进行。先用 dry_run=true 预览识别结果。
    """
    if not files:
        raise HTTPException(status_code=400, detail="请选择文件")
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_FILES} 个文件")
    max_bytes = KB_MAX_FILE_MB * 1024 * 1024
    with tempfile.TemporaryDirectory(prefix="store-import-") as tmp:
        names: list[str] = []
        for upload in files:
            name = _safe_name(upload.filename)
            if name in names:
                raise HTTPException(status_code=400, detail=f"文件重名：{name}")
            data = upload.file.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise HTTPException(status_code=413, detail=f"{name} 超过 {KB_MAX_FILE_MB}MB")
            (Path(tmp) / name).write_bytes(data)
            names.append(name)
        try:
            report = load([tmp], dry_run=dry_run, wait_index=False)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    if not dry_run:
        auth.audit("store_data_import", user, "store_data", None,
                   {"files": names, "inserted": report.get("inserted")})
    return {"dry_run": dry_run, **report}
