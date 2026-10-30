"""
源文件解析：按工作表的表头内容识别文件类型（不依赖文件名），输出与业务表列名一致的记录。

门店归属在 loader 中统一解析：记录里的 store_label 是可用于识别门店的文字
（文件名、工作表名、收款门店名、仓库名）。

不导入与经营分析无关的个人信息：微信付款用户标识、支付宝对方账户 / 姓名、开票单位税号等；
备注里手写的顾客手机号、身份证号（以及紧挨着手机号的姓名）导入前打码，见 mask_personal。
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.excel_importer import read_workbook
from backend.store_data.schema import store_names

# 台账「类型」中属于手机的产品线（样例数据和产品线归类用；是否手机以商品名称为准，见 is_phone）
PHONE_TYPES = {"FD", "数字", "X系列", "畅玩系列", "畅玩", "Power", "ND"}
# 不算门店零售的系统状态：调回总库、批发。台账中这些行不导入
NON_RETAIL_STATUS = {"系统已调总库", "系统已做批发"}


def is_phone(product_name: str | None, product_type: str | None, status: str | None, price: float | None) -> bool:
    """
    是否计入「手机台数」：按商品名称判断，不依赖手工填写的「类型」列。

    台账里的「类型」是按产品线手填的（平板也可能填成 X系列，线上订单的手机填成「线上订单」），
    手机的商品名称则固定带「全网通」（如 荣耀X80 Pro Max-8+256G全网通5G元气橙）。
    另外排除：平板、回收业务、调回总库 / 批发（不是门店零售）、售价为 0 的赠品。
    """
    name = product_name or ""
    return ("全网通" in name and "平板" not in name and "二手" not in name and product_type != "回收"
            and (status or "") not in NON_RETAIL_STATUS and (price or 0) > 0)


@dataclass
class ParsedFile:
    path: str
    kind: str
    records: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add(self, table: str, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.records.setdefault(table, []).extend(rows)


# ---------------------------------------------------------------------------
# 值清洗
# ---------------------------------------------------------------------------

_ERROR_VALUES = {"#REF!", "#VALUE!", "#N/A", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!", "-", "—", ""}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)) if value is not None else ""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    s = str(value).strip().lstrip("`").replace("\t", "").strip()
    return None if s in _ERROR_VALUES else s


_PHONE = re.compile(r"(?P<name>[\u4e00-\u9fa5]{2,3})?(?<!\d)(?P<phone>1[3-9]\d{9})(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![\dXx])")


def mask_personal(value: str | None) -> str | None:
    """备注中的手机号保留前 3 后 4 位，紧挨着的姓名只留姓；身份证号整体打码。"""
    if not value:
        return value

    def phone(m: re.Match) -> str:
        name = m["name"][0] + "*" * (len(m["name"]) - 1) if m["name"] else ""
        return f"{name}{m['phone'][:3]}****{m['phone'][-4:]}"

    return _ID_CARD.sub("******************", _PHONE.sub(phone, value))


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lstrip("`").replace(",", "").replace("¥", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    n = _number(value)
    return int(round(n)) if n is not None else None


def _datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time())
    if isinstance(value, (int, float)) and 20000 < value < 80000:  # 未带日期格式的 Excel 日期序列号
        return dt.datetime(1899, 12, 30) + dt.timedelta(days=float(value))
    s = _text(value)
    if not s:
        return None
    s = s.replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None


def _time(value: Any) -> dt.time | None:
    if isinstance(value, dt.time):
        return value
    if isinstance(value, dt.datetime):
        return value.time()
    n = _number(value)
    if n is not None and 0 <= n < 1:
        seconds = round(n * 86400)
        return dt.time(seconds // 3600 % 24, seconds // 60 % 60, seconds % 60)
    s = _text(value)
    if s and re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", s):
        parts = [int(p) for p in s.split(":")]
        return dt.time(*parts)
    return None


def _month_from(text: str) -> str | None:
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}"
    return None


class _Header:
    """按表头名（去空白后的前缀）定位列；重复列名可指定取第几个。"""

    def __init__(self, row: list[Any]):
        self.names = [_norm(v) for v in row]

    def find(self, *prefixes: str, last: bool = False) -> int | None:
        hits = [i for i, name in enumerate(self.names) if name and any(name.startswith(p) for p in prefixes)]
        if not hits:
            return None
        return hits[-1] if last else hits[0]


def _get(row: list[Any], index: int | None) -> Any:
    return row[index] if index is not None and index < len(row) else None


def _header_row(rows: list[list[Any]], required: tuple[str, ...], scan: int = 6) -> int | None:
    for i, row in enumerate(rows[:scan]):
        names = {_norm(v) for v in row}
        if all(any(n.startswith(r) for n in names) for r in required):
            return i
    return None


# ---------------------------------------------------------------------------
# 门店销售台账
# ---------------------------------------------------------------------------


def parse_sales_ledger(rows: list[list[Any]], label: str, source: str,
                       notes: list[str] | None = None) -> list[dict[str, Any]]:
    """调回总库、批发的行不是门店零售，不导入（不计入销售额、毛利和台数），跳过的数量写入 notes。"""
    head_at = _header_row(rows, ("产品型号", "收款方式"))
    h = _Header(rows[head_at])
    col = {
        "date": h.find("日期", "销售日期"),
        "time": h.find("时间"),
        "salesperson": h.find("姓名", "销售人员"),
        "status": h.find("产品状态"),
        "product": h.find("产品型号"),
        "serial": h.find("串码"),
        "payment": h.find("收款方式"),
        "recycle": h.find("提货价格"),
        "price": h.find("销售价格"),
        "received": h.find("实际到账"),
        "subsidy": h.find("国补应返"),
        "incentive": h.find("销售激励"),
        "remark": h.find("备注"),
        "invoice_flag": h.find("是否开票"),
        "invoice_amount": h.find("开票金额"),
        "cost": h.find("进店价"),
        "profit": h.find("毛利", last=True),  # 台账里第一列「毛利」多为空，进店价后面那列才是计算结果
        "commission": h.find("单台提成"),
        "red_packet": h.find("红包"),
        "type": h.find("类型", "类别"),
        "week": h.find("周数"),
        "series": h.find("系列"),
        "order": h.find("系统订金单号", "系统定单号", "系统订单号"),
    }
    if col["type"] is None and col["week"]:
        col["type"] = col["week"] - 1  # 个别台账「类型」列没有表头，固定在「周数」左侧

    records = []
    skipped: dict[str, list[float]] = {}
    last_date: dt.datetime | None = None
    last_person = None
    for row in rows[head_at + 1:]:
        product = _text(_get(row, col["product"]))
        if not product:
            continue
        sale_date = _datetime(_get(row, col["date"])) or last_date
        if sale_date is None:
            continue
        last_date = sale_date
        person = _text(_get(row, col["salesperson"])) or last_person
        last_person = person
        status = _text(_get(row, col["status"]))
        if status in NON_RETAIL_STATUS:
            skipped.setdefault(status, []).append(_number(_get(row, col["price"])) or 0)
            continue
        product_type = _text(_get(row, col["type"]))
        records.append({
            "store_label": label,
            "sale_date": sale_date.date(),
            "sale_time": _time(_get(row, col["time"])),
            "month_label": sale_date.strftime("%Y-%m"),
            "week_no": _int(_get(row, col["week"])),
            "salesperson": person,
            "product_status": _text(_get(row, col["status"])) or ("-" if _get(row, col["status"]) == "-" else None),
            "product_name": product,
            "serial_no": _text(_get(row, col["serial"])),
            "payment_method": _text(_get(row, col["payment"])),
            "recycle_price": _number(_get(row, col["recycle"])),
            "sale_price": _number(_get(row, col["price"])),
            "received_amount": _number(_get(row, col["received"])),
            "subsidy_receivable": _number(_get(row, col["subsidy"])),
            "sales_incentive": _number(_get(row, col["incentive"])),
            "cost_price": _number(_get(row, col["cost"])),
            "gross_profit": _number(_get(row, col["profit"])),
            "commission": _number(_get(row, col["commission"])),
            "red_packet": _number(_get(row, col["red_packet"])),
            "product_type": product_type,
            "series": _text(_get(row, col["series"])),
            "is_phone": is_phone(product, product_type, status, _number(_get(row, col["price"]))),
            "remark": mask_personal(_text(_get(row, col["remark"]))),
            "invoice_flag": _text(_get(row, col["invoice_flag"])),
            "invoice_amount": _number(_get(row, col["invoice_amount"])),
            "order_no": _text(_get(row, col["order"])),
            "source_file": source,
        })
    if notes is not None:
        for status, prices in skipped.items():
            notes.append(f"{label}：{len(prices)} 行「{status}」不是门店零售，未导入（销售价格合计 {sum(prices):,.2f} 元）")
    return records


# ---------------------------------------------------------------------------
# 收款明细
# ---------------------------------------------------------------------------


def parse_wechat(rows: list[list[Any]], source: str) -> list[dict[str, Any]]:
    head_at = _header_row(rows, ("交易时间", "微信订单号"))
    h = _Header(rows[head_at])
    col = {name: h.find(name) for name in (
        "交易时间", "微信订单号", "商户订单号", "交易类型", "交易状态", "订单金额", "应结订单金额",
        "退款金额", "商品名称", "手续费", "费率")}
    records = []
    for row in rows[head_at + 1:]:
        paid_at = _datetime(_get(row, col["交易时间"]))
        if paid_at is None:
            continue  # 汇总行
        merchant = _text(_get(row, col["商品名称"]))
        records.append({
            "channel": "微信",
            "paid_at": paid_at,
            "store_label": merchant,
            "merchant_label": merchant,
            "trade_no": _text(_get(row, col["微信订单号"])),
            "merchant_order_no": _text(_get(row, col["商户订单号"])),
            "trade_type": _text(_get(row, col["交易类型"])),
            "status": _text(_get(row, col["交易状态"])),
            "amount": _number(_get(row, col["订单金额"])) or _number(_get(row, col["应结订单金额"])),
            "expense": None,
            "refund_amount": _number(_get(row, col["退款金额"])),
            "fee": _number(_get(row, col["手续费"])),
            "fee_rate": _text(_get(row, col["费率"])),
            "balance": None,
            "remark": None,
            "source_file": source,
        })
    return records


def parse_alipay(rows: list[list[Any]], source: str) -> list[dict[str, Any]]:
    head_at = _header_row(rows, ("入账时间", "支付宝交易号"))
    h = _Header(rows[head_at])
    col = {name: h.find(name) for name in (
        "入账时间", "支付宝交易号", "商户订单号", "账务类型", "收入", "支出", "账户余额", "商品名称", "备注",
        "业务描述")}
    records = []
    for row in rows[head_at + 1:]:
        paid_at = _datetime(_get(row, col["入账时间"]))
        if paid_at is None:
            continue
        merchant = _text(_get(row, col["商品名称"]))
        remark = mask_personal(" ".join(filter(None, [_text(_get(row, col["备注"])), _text(_get(row, col["业务描述"]))])) or None)
        records.append({
            "channel": "支付宝",
            "paid_at": paid_at,
            "store_label": " ".join(filter(None, [merchant, remark])),
            "merchant_label": merchant,
            "trade_no": _text(_get(row, col["支付宝交易号"])),
            "merchant_order_no": _text(_get(row, col["商户订单号"])),
            "trade_type": _text(_get(row, col["账务类型"])),
            "status": None,
            "amount": _number(_get(row, col["收入"])),
            "expense": _number(_get(row, col["支出"])),
            "refund_amount": None,
            "fee": None,
            "fee_rate": None,
            "balance": _number(_get(row, col["账户余额"])),
            "remark": remark,
            "source_file": source,
        })
    return records


# ---------------------------------------------------------------------------
# ERP 进存 / 库龄
# ---------------------------------------------------------------------------


def parse_purchase_in(rows: list[list[Any]]) -> list[dict[str, Any]]:
    head_at = _header_row(rows, ("单号", "商品编码", "供应商"))
    h = _Header(rows[head_at])
    col = {name: h.find(name) for name in (
        "公司/门店", "单据类型", "入库方式", "单号", "日期", "业务时间", "供应商", "审核", "仓库", "商品编码",
        "数量", "品牌", "单价", "金额", "成本金额", "备注", "商品名称", "分类")}
    records = []
    for row in rows[head_at + 1:]:
        doc_no = _text(_get(row, col["单号"]))
        doc_date = _datetime(_get(row, col["日期"]))
        if not doc_no or doc_date is None:
            continue
        records.append({
            "doc_no": doc_no,
            "doc_date": doc_date.date(),
            "biz_time": _datetime(_get(row, col["业务时间"])),
            "company": _text(_get(row, col["公司/门店"])),
            "doc_type": _text(_get(row, col["单据类型"])),
            "in_method": _text(_get(row, col["入库方式"])),
            "supplier": _text(_get(row, col["供应商"])),
            "audit_status": _text(_get(row, col["审核"])),
            "warehouse": _text(_get(row, col["仓库"])),
            "item_code": _text(_get(row, col["商品编码"])),
            "product_name": _text(_get(row, col["商品名称"])),
            "category": _text(_get(row, col["分类"])),
            "brand": _text(_get(row, col["品牌"])),
            "quantity": _int(_get(row, col["数量"])),
            "unit_price": _number(_get(row, col["单价"])),
            "amount": _number(_get(row, col["金额"])),
            "cost_amount": _number(_get(row, col["成本金额"])),
            "remark": mask_personal(_text(_get(row, col["备注"]))),
        })
    return records


def parse_inventory(rows: list[list[Any]], snapshot: dt.date) -> list[dict[str, Any]]:
    head_at = _header_row(rows, ("商品编码", "库存"))
    h = _Header(rows[head_at])
    col = {name: h.find(name) for name in ("品牌", "商品分类", "商品编码", "商品名称", "库存", "调出在途", "调入在途", "条形码")}
    records = []
    for row in rows[head_at + 1:]:
        code = _text(_get(row, col["商品编码"]))
        if not code:
            continue
        records.append({
            "snapshot_date": snapshot,
            "item_code": code,
            "product_name": _text(_get(row, col["商品名称"])),
            "category": _text(_get(row, col["商品分类"])),
            "brand": _text(_get(row, col["品牌"])),
            "stock_qty": _int(_get(row, col["库存"])),
            "transfer_out_qty": _int(_get(row, col["调出在途"])),
            "transfer_in_qty": _int(_get(row, col["调入在途"])),
            "barcode": _text(_get(row, col["条形码"])),
        })
    return records


def parse_stock_age(rows: list[list[Any]], snapshot: dt.date) -> list[dict[str, Any]]:
    head_at = _header_row(rows, ("仓库", "库龄"))
    h = _Header(rows[head_at])
    col = {name: h.find(name) for name in (
        "行号", "仓库", "商品分类", "商品名称", "串号", "库存量", "品牌", "供应商", "成本单价", "入库单价", "入库日期",
        "库龄", "型号")}
    records = []
    for row in rows[head_at + 1:]:
        warehouse = _text(_get(row, col["仓库"]))
        if not warehouse or not _text(_get(row, col["商品名称"])):
            continue  # 合计行
        records.append({
            "snapshot_date": snapshot,
            "store_label": warehouse,
            "warehouse": warehouse,
            "category": _text(_get(row, col["商品分类"])),
            "product_name": _text(_get(row, col["商品名称"])),
            "serial_no": _text(_get(row, col["串号"])),
            "quantity": _int(_get(row, col["库存量"])),
            "brand": _text(_get(row, col["品牌"])),
            "supplier": _text(_get(row, col["供应商"])),
            "cost_price": _number(_get(row, col["成本单价"])),
            "in_price": _number(_get(row, col["入库单价"])),
            "in_time": _datetime(_get(row, col["入库日期"])),
            "age_days": _int(_get(row, col["库龄"])),
            "model": _text(_get(row, col["型号"])),
        })
    return records


# ---------------------------------------------------------------------------
# 月度任务（门店达成一览表）
# ---------------------------------------------------------------------------

_COUNT_UNITS = {"台数", "笔数", "数量"}


def parse_targets(rows: list[list[Any]], label: str) -> list[dict[str, Any]]:
    title = " ".join(_text(v) or "" for v in rows[0]) if rows else ""
    month = _month_from(title.replace(" ", "")) or None
    header_at = next((i for i, r in enumerate(rows[:5]) if any(_norm(v) == "总毛利" for v in r)), None)
    if header_at is None:
        return []
    header = rows[header_at]
    as_of = _datetime(header[0]) if header else None
    if month is None:
        match = re.search(r"(\d{1,2})\s*月", title)
        if match and as_of:
            month = f"{as_of.year}-{int(match.group(1)):02d}"
    if month is None and as_of:
        month = as_of.strftime("%Y-%m")
    if month is None:
        return []

    # 列 → (考核项, 单位)：「台数 / 笔数 / 数量」列属于左边最近的考核项
    items: dict[int, tuple[str, str]] = {}
    current = None
    for i, value in enumerate(header):
        name = _norm(value)
        if i < 2 or not name or name in ("数据截至", "本月天数", "剩余天数"):
            continue
        if name in _COUNT_UNITS and current:
            items[i] = (current, name)
        else:
            current = name
            items[i] = ("总毛利" if name == "总毛利" else name, "毛利")

    records = []
    owner = None
    target_row = None
    for row in rows[header_at + 1:]:
        kind = _norm(_get(row, 1))
        if kind == "月度目标":
            owner = _text(_get(row, 0))
            target_row = row
        elif kind == "截至到目前完成" and target_row is not None and owner:
            owner_type = "store" if any(s in owner for s in store_names()) else "staff"
            for i, (item, unit) in items.items():
                target = _number(_get(target_row, i))
                achieved = _number(_get(row, i))
                if target is None and achieved is None:
                    continue
                records.append({
                    "store_label": label,
                    "month_label": month,
                    "owner": owner,
                    "owner_type": owner_type,
                    "item": item,
                    "unit": unit,
                    "target_value": target,
                    "achieved_value": achieved,
                    "as_of": as_of.date() if as_of else None,
                })
            target_row = None
        elif _norm(_get(row, 2)) == "销售额":
            break  # 下方是销售额汇总区，与任务无关
    return records


# ---------------------------------------------------------------------------
# 门店指导书 · 产品政策 / 进店价格表
# ---------------------------------------------------------------------------


def parse_product_policy(rows: list[list[Any]], month: str) -> list[dict[str, Any]]:
    head_at = _header_row(rows, ("产品名称", "进店价", "常规指导价"))
    h = _Header(rows[head_at])
    col = {
        "name": h.find("产品名称"), "config": h.find("配置"), "margin": h.find("进店毛利"),
        "cost": h.find("进店价"), "guide": h.find("常规指导价"), "guide_commission": h.find("指导价提成"),
        "so": h.find("SO激励"), "promo": h.find("活动促销价"), "promo_commission": h.find("促销价提成"),
        "remark": h.find("备注"),
    }
    records = []
    name = None
    for row in rows[head_at + 1:]:
        name = _text(_get(row, col["name"])) or name
        cost = _number(_get(row, col["cost"]))
        guide = _number(_get(row, col["guide"]))
        if not name or (cost is None and guide is None):
            continue
        extra = [_text(v) for v in row[(col["remark"] or len(row)):]]
        records.append({
            "month_label": month,
            "product_name": name,
            "config": _text(_get(row, col["config"])),
            "store_margin": _number(_get(row, col["margin"])),
            "cost_price": cost,
            "guide_price": guide,
            "guide_commission": _number(_get(row, col["guide_commission"])),
            "so_incentive": _number(_get(row, col["so"])),
            "promo_price": _number(_get(row, col["promo"])),
            "promo_commission": _number(_get(row, col["promo_commission"])),
            "remark": "；".join(v for v in extra if v) or None,
        })
    return records


def parse_price_list(rows: list[list[Any]]) -> list[dict[str, Any]]:
    head_at = _header_row(rows, ("零售价", "进店价"))
    h = _Header(rows[head_at])
    cat, retail, cost, margin = h.find("产品分类"), h.find("零售价"), h.find("进店价"), h.find("毛利")
    name_col = (cat + 1) if cat is not None else 1
    records = []
    for row in rows[head_at + 1:]:
        name = _text(_get(row, name_col))
        if not name or _number(_get(row, retail)) is None:
            continue
        records.append({
            "category": _text(_get(row, cat)),
            "product_name": name,
            "retail_price": _number(_get(row, retail)),
            "cost_price": _number(_get(row, cost)),
            "gross_margin": _number(_get(row, margin)),
        })
    return records


# ---------------------------------------------------------------------------
# 文件识别
# ---------------------------------------------------------------------------


def _sheet_has(rows: list[list[Any]], *names: str) -> bool:
    return _header_row(rows, names) is not None


def parse_file(path: str | Path) -> ParsedFile:
    path = Path(path)
    sheets = read_workbook(path.read_bytes())
    snapshot = dt.date.fromtimestamp(path.stat().st_mtime)
    parsed = ParsedFile(str(path), "unknown")
    month = _month_from(path.stem)
    kinds = []
    for name, rows in sheets.items():
        if not rows:
            continue
        label = f"{path.stem} {name}"
        if _sheet_has(rows, "产品型号", "收款方式"):
            parsed.add("fact_store_sales", parse_sales_ledger(rows, label, path.name, parsed.notes))
            kinds.append("门店销售台账")
        elif _sheet_has(rows, "交易时间", "微信订单号"):
            parsed.add("fact_payments", parse_wechat(rows, path.name))
            kinds.append("微信收款明细")
        elif _sheet_has(rows, "入账时间", "支付宝交易号"):
            parsed.add("fact_payments", parse_alipay(rows, path.name))
            kinds.append("支付宝收款明细")
        elif _sheet_has(rows, "单号", "商品编码", "供应商"):
            parsed.add("fact_purchase_in", parse_purchase_in(rows))
            kinds.append("ERP 入库明细")
        elif _sheet_has(rows, "商品编码", "库存", "调出在途"):
            parsed.add("fact_inventory", parse_inventory(rows, snapshot))
            kinds.append("ERP 库存明细")
        elif _sheet_has(rows, "仓库", "库龄"):
            parsed.add("fact_stock_age", parse_stock_age(rows, snapshot))
            kinds.append("库龄表")
        elif _sheet_has(rows, "产品名称", "进店价", "常规指导价"):
            if month is None:
                parsed.notes.append(f"{path.name}：文件名中没有「YYYY年M月」，产品政策未导入")
                continue
            parsed.add("dim_product_policy", parse_product_policy(rows, month))
            kinds.append("门店指导书")
        elif _sheet_has(rows, "零售价", "进店价"):
            parsed.add("dim_product_price", parse_price_list(rows))
        elif "任务" in name:
            parsed.add("fact_store_targets", parse_targets(rows, label))
    parsed.kind = "、".join(dict.fromkeys(kinds)) or ("进店价格 / 月度任务" if parsed.records else "unknown")
    return parsed
