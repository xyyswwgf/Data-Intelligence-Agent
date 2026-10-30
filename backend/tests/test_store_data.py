"""门店数据：各种「Excel」格式的读取、源文件识别与解析、导入与知识库生成（数据均为虚构）。"""

import datetime as dt
import io
import re
import zipfile

from openpyxl import Workbook

from backend.excel_importer import read_workbook
from backend.tests.conftest import requires_db

LEDGER_HEADER = ["日期", "时间", "姓名", "产品状态", "产品型号、配置、颜色", "串码（手机：86码，其他：SN）", "收款方式",
                 "提货价格（回收给顾客的价格）", "销售价格（回收平台给公司的价格）", "实际到账金额", "国补应返 5210", "销售激励",
                 "毛利", "备注", "进店价", "毛利", "单台提成", "红包", "类型", "周数", "系列"]


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _ledger(month: int = 9, remark: str | None = None) -> bytes:
    rows = [LEDGER_HEADER,
            [dt.datetime(2026, month, 1), dt.time(10, 30), "测试员", "系统已做销售", "荣耀X80 Pro Max-8+256G全网通5G元气橙",
             "860000000000001", "银联国补pos", None, 2099, 1784.15, 314.85, None, None, remark, 1935, 164, 20, None, "X系列", 1,
             "X80PM"],
            # 赠品行：日期、姓名为空（合并单元格），沿用上一行
            [None, None, None, "系统已做销售", "钢化膜一片装", None, None, None, 0, 0, None, None, None, "同上单赠送",
             28, -28, None, None, None, 1, None],
            # 调回总库：不是门店零售，不导入
            [dt.datetime(2026, month, 2), None, "测试员", "系统已调总库", "荣耀X80-12+256G全网通5G幻夜黑", "860000000000002",
             None, None, 1999, None, None, None, None, None, 1999, 0, None, None, "X系列", 1, "X80"],
            [None] * 5]
    target = [[f"测试店荣耀 {month}月 门店达成一览表"],
              [dt.datetime(2026, month, 28), "任 务", "总 毛 利", "X系列", "台数"],
              [30, "占比", 1, 0.5],
              ["城东万达", "月度目标", 1000, 500],
              [None, "截至到目前完成", 136, 164, 1],
              [None, "达成率", 0.136, 0.328],
              ["测试员", "月度目标", 500, 250, "台数"],
              [None, "截至到目前完成", 136, 164, 1]]
    return _xlsx({"城东销售报表": rows, f"{month}月任务": target,
                  "进店价格": [["产品分类", None, "零售价", "进店价", "毛利"], ["手机", "荣耀X80 Pro Max 8+256", 2099, 1935, 164]]})


def _broken_styles(content: bytes) -> bytes:
    """模拟部分系统导出的 xlsx：样式表中的 fill 不合规范，openpyxl 无法打开。"""
    src = zipfile.ZipFile(io.BytesIO(content))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "xl/styles.xml":
                data = re.sub(rb"<fills[^>]*>.*?</fills>", b'<fills count="1"><fill><bogus/></fill></fills>', data, flags=re.S)
            dst.writestr(item, data)
    return out.getvalue()


SPREADSHEET_ML = """<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Worksheet ss:Name="账务组合查询1"><Table>
<Row><Cell><Data ss:Type="String">#账号：test</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">序号</Data></Cell><Cell><Data ss:Type="String">入账时间</Data></Cell>
<Cell><Data ss:Type="String">支付宝交易号</Data></Cell><Cell><Data ss:Type="String">账务类型</Data></Cell>
<Cell><Data ss:Type="String">收入（+元）</Data></Cell><Cell><Data ss:Type="String">对方名称</Data></Cell>
<Cell><Data ss:Type="String">商品名称</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">1</Data></Cell><Cell><Data ss:Type="String">2026-09-02 12:00:00</Data></Cell>
<Cell><Data ss:Type="String">2026090200001</Data></Cell><Cell><Data ss:Type="String">在线支付</Data></Cell>
<Cell><Data ss:Type="Number">88.5</Data></Cell><Cell><Data ss:Type="String">*三</Data></Cell>
<Cell ss:Index="7"><Data ss:Type="String">连锁门店(老城步行街店)</Data></Cell></Row>
<Row><Cell><Data ss:Type="String">#导出时间：2026-09-03</Data></Cell></Row>
</Table></Worksheet></Workbook>""".encode()

STOCK_AGE_TSV = ("行号\t仓库\t商品分类\t商品名称\t串号\t库存量\t品牌\t供应商\t成本单价\t入库单价\t入库日期\t库龄(天)\t型号\t\r\n"
                 "1\t滨河仓\t手机.荣耀\t荣耀Power2-12+512G\tSN001\t1\t荣耀\t样例\t2640.0000 \t2640.0000 \t"
                 "2026/1/15 12:01:31\t200\tPower2\t\r\n"
                 "\t\t\t\t\t1 \t\t\t2,640.0000 \t2,640.0000 \t\r\n").encode("gb18030")


def test_read_workbook_formats():
    assert list(read_workbook(_broken_styles(_ledger()))) == ["城东销售报表", "9月任务", "进店价格"]
    broken = read_workbook(_broken_styles(_ledger()))["城东销售报表"]
    assert broken[1][0] == dt.datetime(2026, 9, 1) and broken[1][8] == 2099  # 日期格式从样式表中识别

    sheet = read_workbook(SPREADSHEET_ML)["账务组合查询1"]
    assert sheet[2][4] == 88.5 and sheet[2][6] == "连锁门店(老城步行街店)"  # ss:Index 跳过的列补空

    rows = read_workbook(STOCK_AGE_TSV)["Sheet1"]
    assert rows[0][1] == "仓库" and rows[1][1] == "滨河仓"


def test_parsers_recognise_files(tmp_path):
    from backend.store_data.parsers import parse_file

    ledger = tmp_path / "城东销量报表.xlsx"
    ledger.write_bytes(_broken_styles(_ledger()))
    parsed = parse_file(ledger)
    sales = parsed.records["fact_store_sales"]
    assert parsed.kind == "门店销售台账" and len(sales) == 2
    assert any("1 行「系统已调总库」" in n and "1,999.00" in n for n in parsed.notes)
    assert sales[0]["gross_profit"] == 164 and sales[0]["is_phone"] and sales[0]["month_label"] == "2026-09"
    assert sales[1]["salesperson"] == "测试员" and sales[1]["sale_date"] == dt.date(2026, 9, 1)  # 沿用上一行
    targets = {(r["owner"], r["item"], r["unit"]): r for r in parsed.records["fact_store_targets"]}
    assert targets[("城东万达", "总毛利", "毛利")]["target_value"] == 1000
    assert targets[("测试员", "X系列", "台数")]["achieved_value"] == 1
    assert targets[("城东万达", "总毛利", "毛利")]["month_label"] == "2026-09"

    alipay = tmp_path / "支付宝收款明细.xls"
    alipay.write_bytes(SPREADSHEET_ML)
    payments = parse_file(alipay).records["fact_payments"]
    assert len(payments) == 1 and payments[0]["amount"] == 88.5
    assert "对方名称" not in str(payments[0]) and "*三" not in payments[0].values()  # 不导入对方个人信息

    stock = tmp_path / "库龄表.XLS"
    stock.write_bytes(STOCK_AGE_TSV)
    rows = parse_file(stock).records["fact_stock_age"]
    assert len(rows) == 1 and rows[0]["age_days"] == 200 and rows[0]["cost_price"] == 2640


@requires_db
def test_loader_and_knowledge(tmp_path, client):
    from sqlalchemy import text

    from backend.database import get_connection
    from backend.store_data.loader import load
    from backend.store_data.sample import load_sample

    (tmp_path / "城东销量报表.xlsx").write_bytes(_ledger())
    (tmp_path / "支付宝收款明细.xls").write_bytes(SPREADSHEET_ML)
    (tmp_path / "特玩2026年9月门店指导书.xlsx").write_bytes(_xlsx({
        "产品政策": [[None, "产品名称", "配置", "进店毛利", "进店价", "常规指导价", "指导价提成", "SO激励", "活动促销价", "促销价提成", "备注"],
                 [None, "Magic V6", "12+512", None, 9200, 9999, 80, None, None, None, None],
                 [None, None, "16+512", None, 10120, 10999, 80, None, None, None, None]],
        "店长店员考核": [["店长考核：底薪+店提+红包"], ["店提点位", 0.06, 0.08, 0.09],
                   ["老城", "毛利≤60000元", "60000元≤毛利<80000元", "80000元≤毛利"],
                   ["城东", "毛利≤40000元", "40000元≤毛利<60000元", "60000元≤毛利"]],
    }))
    try:
        report = load([str(tmp_path)], reset=True)
        assert report["inserted"]["fact_store_sales"] == 2 and report["inserted"]["dim_product_policy"] == 2
        with get_connection() as conn:
            store = conn.execute(text(
                "SELECT d.short_name FROM fact_store_sales s JOIN dim_store d USING (store_id) LIMIT 1")).scalar()
            paid_store = conn.execute(text(
                "SELECT d.short_name FROM fact_payments p JOIN dim_store d USING (store_id)")).scalar()
            pages = dict(conn.execute(text("SELECT slug, content_md FROM wiki_pages")).all())
        assert store == "城东" and paid_store == "老城"
        assert {"home", "store-data-guide", "store-targets-2026-09", "store-guide-2026-09"} <= set(pages)
        policy = next(v for k, v in pages.items() if k.startswith("store-guide-2026-09-") and "Magic V6" in v)
        assert "### Magic V6" in policy and "| 16+512 | 10,120 |" in policy
        rules = next(v for k, v in pages.items() if k.startswith("store-guide-2026-09-") and "店提点位" in v)
        assert "| 店提点位 | 6% | 8% | 9% |" in rules  # 点位按百分比展示
        assert "| 城东 | 毛利≤40000元 |" in rules  # 不规整的工作表逐行保留，不能把内容行当表头吞掉
        assert "测试员" in pages["store-data-guide"]

        again = load([str(tmp_path)])  # 重复导入不产生重复数据和页面
        assert again["inserted"]["fact_store_sales"] == 2 and again["knowledge"]["pages"] == report["knowledge"]["pages"]
        with get_connection() as conn:
            assert conn.execute(text("SELECT COUNT(*) FROM fact_store_sales")).scalar() == 2
    finally:
        with get_connection() as conn:
            load_sample(conn)


def test_phone_count_by_product_name():
    from backend.store_data.parsers import is_phone

    phone = "荣耀X80 Pro Max-8+256G全网通5G元气橙"
    assert is_phone(phone, "X系列", "系统已做销售", 2099)
    assert is_phone(phone, "线上订单", "系统已做销售", 2199)  # 线上订单的手机也算
    assert is_phone("荣耀600 超级版-12+512G全网通5G青苹果", None, None, 3106)  # 类型漏填
    assert not is_phone("荣耀平板X10 Pro Max 13.0吋-8+256GWIFI版苍山灰", "X系列", "系统已做销售", 2199)  # 平板填成 X系列
    assert not is_phone("荣耀极简背包 黑色", "X系列", "系统已做销售", 0)
    assert not is_phone("钢化膜一片装", "X系列", "系统已做销售", 69)
    assert not is_phone(phone, "回收", "系统已做销售", 1800)
    assert not is_phone(phone, "X系列", "系统已调总库", 2199)
    assert not is_phone(phone, "X系列", "系统已做批发", 2199)
    assert not is_phone(phone, "X系列", "系统已做销售", 0)  # 赠品


def test_mask_personal():
    from backend.store_data.parsers import mask_personal

    assert mask_personal("李小明13900001111") == "李**139****1111"
    assert mask_personal("身份证110101199003071234 已登记") == "身份证****************** 已登记"
    assert mask_personal("同上单销售 一笔支付699 串码860000000000001") == "同上单销售 一笔支付699 串码860000000000001"
    assert mask_personal(None) is None


@requires_db
def test_monthly_import_keeps_earlier_months(tmp_path, client):
    from sqlalchemy import text

    from backend.database import get_connection
    from backend.store_data.loader import load
    from backend.store_data.sample import load_sample

    sept, octo = tmp_path / "9", tmp_path / "10"
    sept.mkdir()
    octo.mkdir()
    # 收银系统每月导出的文件名相同
    (sept / "城东销量报表.xlsx").write_bytes(_ledger(9))
    (octo / "城东销量报表.xlsx").write_bytes(_ledger(10, remark="顾客张三丰13800138000"))
    try:
        load([str(sept)], reset=True, update_kb=False)
        load([str(octo)], update_kb=False)
        load([str(octo)], update_kb=False)  # 同一个月重复导入只替换该月
        with get_connection() as conn:
            sales = dict(conn.execute(text(
                "SELECT month_label, COUNT(*) FROM fact_store_sales GROUP BY 1")).all())
            targets = conn.execute(text("SELECT DISTINCT month_label FROM fact_store_targets ORDER BY 1")).scalars().all()
            remark = conn.execute(text(
                "SELECT remark FROM fact_store_sales WHERE month_label = '2026-10' AND remark IS NOT NULL")).scalar()
        assert sales == {"2026-09": 2, "2026-10": 2}
        assert targets == ["2026-09", "2026-10"]
        assert remark == "顾客张**138****8000"
    finally:
        with get_connection() as conn:
            load_sample(conn)


@requires_db
def test_store_data_import_api(client, admin_headers, make_user):
    from backend.store_data.sample import load_sample
    from backend.database import get_connection

    files = [("files", ("城东销量报表.xlsx", _ledger(), "application/octet-stream")),
             ("files", ("支付宝收款明细.xls", SPREADSHEET_ML, "application/octet-stream"))]
    viewer = make_user("viewer")
    assert client.post("/api/store-data/import", headers=viewer, files=files).status_code == 403
    bad = client.post("/api/store-data/import", headers=admin_headers,
                      files=[("files", ("说明.pdf", b"%PDF", "application/pdf"))])
    assert bad.status_code == 400

    try:
        preview = client.post("/api/store-data/import", headers=admin_headers, files=files, data={"dry_run": "true"})
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["dry_run"] and body["would_insert"]["fact_store_sales"] == 2 and "inserted" not in body
        assert {f["kind"] for f in body["files"]} == {"门店销售台账", "支付宝收款明细"}

        done = client.post("/api/store-data/import", headers=admin_headers, files=files)
        assert done.status_code == 200, done.text
        assert done.json()["inserted"]["fact_payments"] == 1 and done.json()["knowledge"]["pages"] >= 2
    finally:
        with get_connection() as conn:
            load_sample(conn)
