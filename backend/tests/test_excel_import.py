"""Excel（.xlsx）导入：多工作表、标题行与合计行检测、日期与整数还原、按工作表命名。"""

import datetime as dt
import io
import uuid

from openpyxl import Workbook

from backend.excel_importer import read_excel
from backend.tests.conftest import requires_db

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "说明"
    ws.append(["这是说明页"])
    data = wb.create_sheet("销售明细")
    data.append(["2026 年上半年销售明细"])  # 标题行
    data.append(["订单编号", "日期", "地区", "数量", "金额"])
    for i in range(1, 7):
        data.append([f"00{i}", dt.datetime(2026, i, 15), "华北" if i % 2 else "华东", i * 3, 100.5 * i])
    data.append(["合计", None, None, 63, 2110.5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_read_excel_converts_cells():
    rows, sheets, selected = read_excel(_workbook(), "销售明细")
    assert sheets == ["说明", "销售明细"] and selected == "销售明细"
    assert rows[2] == ["001", "2026-01-15", "华北", "3", "100.5"]  # 日期转 ISO、整数不带 .0、前导零保留
    _, _, default = read_excel(_workbook())
    assert default == "说明"


def test_read_excel_rejects_non_xlsx():
    import pytest

    with pytest.raises(ValueError, match="xlsx"):
        read_excel(b"not a zip file")


@requires_db
def test_excel_preview_and_import(client, admin_headers):
    content = _workbook()
    files = {"file": ("销售.xlsx", io.BytesIO(content), XLSX)}
    preview = client.post("/api/import/csv/preview", headers=admin_headers, files=files, data={"sheet_name": "销售明细"}).json()
    assert preview["sheets"] == ["说明", "销售明细"] and preview["selected_sheet"] == "销售明细"
    assert preview["header_row"] == 2 and preview["auto_skip_trailing"] == 1 and preview["total_rows"] == 6
    types = {c["name"]: c["detected_type"] for c in preview["columns"]}
    assert types["日期"] == "DATE" and types["数量"] == "INT" and types["订单编号"].startswith("VARCHAR")
    assert preview["file_profile"]["suggested_table_name"] == "csv_销售_销售明细"

    table = f"csv_xlsx_{uuid.uuid4().hex[:6]}"
    resp = client.post("/api/import/csv", headers=admin_headers, files={"file": ("销售.xlsx", io.BytesIO(content), XLSX)},
                       data={"sheet_name": "销售明细", "table_name": table, "header_row": 2, "skip_trailing": 1})
    assert resp.status_code == 201, resp.text
    assert resp.json()["row_count"] == 6
    try:
        result = client.post("/api/analyze/table", headers=admin_headers, json={"table_name": table, "question": "哪个地区数量最多"}).json()
        assert result["status"] == "completed"
    finally:
        client.delete(f"/api/tables/{table}", headers=admin_headers)

    bad = client.post("/api/import/csv/preview", headers=admin_headers,
                      files={"file": ("a.xls", io.BytesIO(b"x"), "application/vnd.ms-excel")})
    assert bad.status_code == 400
