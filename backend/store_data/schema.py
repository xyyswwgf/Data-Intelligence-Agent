"""
门店经营数据的表结构。列名用英文，含义和口径写在 COMMENT 里 —— 智能助手生成 SQL 时读取的就是这些注释。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection

# 门店维度：code、全称、简称、别名（源文件里出现过的各种叫法，用于识别门店）
STORES = [
    ("wangjing", "望京万象汇店", "望京", "望京万象汇", ["望京", "万象汇", "望京万象汇库"]),
    ("nanluo", "南锣鼓巷店", "南锣", "南锣鼓巷", ["南锣", "南锣鼓巷店仓"]),
    ("letigang", "通州乐堤港店", "乐堤港", "远洋乐堤港", ["乐堤港", "乐提港", "通州乐堤港库"]),
    ("yangguang", "阳光新生活广场店", "阳光", "阳光新生活广场", ["阳光", "新生活", "通州新生活库"]),
]

# 业务表（重置数据时按此顺序清空）
BUSINESS_TABLES = [
    "fact_store_sales", "fact_payments", "fact_purchase_in", "fact_inventory", "fact_stock_age",
    "fact_store_targets", "dim_product_policy", "dim_product_price",
]
# 旧版电商演示数据的表，迁移时删除
LEGACY_DEMO_TABLES = [
    "fact_refunds", "fact_marketing_spend", "fact_daily_traffic", "fact_orders",
    "dim_customer", "dim_channel", "dim_product", "dim_region", "dim_date", "knowledge_chunks",
]

DDL = """
CREATE TABLE IF NOT EXISTS dim_store (
    store_id SERIAL PRIMARY KEY,
    store_code VARCHAR(30) NOT NULL UNIQUE,
    store_name VARCHAR(50) NOT NULL,
    short_name VARCHAR(20) NOT NULL,
    mall VARCHAR(50),
    aliases TEXT[] NOT NULL DEFAULT '{}'
);
COMMENT ON TABLE dim_store IS '门店维度：荣耀授权体验店。按门店统计时 JOIN dim_store 取 short_name（望京 / 南锣 / 乐堤港 / 阳光）';
COMMENT ON COLUMN dim_store.short_name IS '门店简称：望京、南锣、乐堤港、阳光';
COMMENT ON COLUMN dim_store.mall IS '所在商场';
COMMENT ON COLUMN dim_store.aliases IS '源文件中出现过的门店叫法和仓库名';

CREATE TABLE IF NOT EXISTS fact_store_sales (
    sale_id BIGSERIAL PRIMARY KEY,
    store_id INT REFERENCES dim_store(store_id),
    sale_date DATE NOT NULL,
    sale_time TIME,
    month_label VARCHAR(7) NOT NULL,
    week_no INT,
    salesperson VARCHAR(30),
    product_status VARCHAR(30),
    product_name TEXT NOT NULL,
    serial_no VARCHAR(64),
    payment_method VARCHAR(50),
    recycle_price NUMERIC(12,2),
    sale_price NUMERIC(12,2),
    received_amount NUMERIC(12,2),
    subsidy_receivable NUMERIC(12,2),
    sales_incentive NUMERIC(12,2),
    cost_price NUMERIC(12,2),
    gross_profit NUMERIC(12,2),
    commission NUMERIC(12,2),
    red_packet NUMERIC(12,2),
    product_type VARCHAR(30),
    series VARCHAR(30),
    is_phone BOOLEAN NOT NULL DEFAULT FALSE,
    remark TEXT,
    invoice_flag VARCHAR(20),
    invoice_amount NUMERIC(12,2),
    order_no VARCHAR(64),
    source_file VARCHAR(200)
);
COMMENT ON TABLE fact_store_sales IS '门店销售台账（每行一个商品，赠品单独一行、售价为 0）。销售额 = SUM(sale_price)，毛利 = SUM(gross_profit)，毛利率 = 毛利 / 销售额，手机台数 = COUNT(*) FILTER (WHERE is_phone)，手机销售额 = SUM(sale_price) FILTER (WHERE is_phone)。调回总库、批发的行不在本表中。product_type=回收 是旧机回收业务，不计入销售额时用 product_type IS DISTINCT FROM ''回收''';
COMMENT ON COLUMN fact_store_sales.sale_date IS '销售日期';
COMMENT ON COLUMN fact_store_sales.month_label IS '月份 YYYY-MM';
COMMENT ON COLUMN fact_store_sales.week_no IS '当月第几周（台账中的周数）';
COMMENT ON COLUMN fact_store_sales.salesperson IS '销售人员姓名';
COMMENT ON COLUMN fact_store_sales.product_status IS '系统状态：系统已做销售 / -（文创等未入系统）。调回总库、批发不是门店零售，不导入本表';
COMMENT ON COLUMN fact_store_sales.product_name IS '产品型号、配置、颜色';
COMMENT ON COLUMN fact_store_sales.serial_no IS '串码：手机为 86 开头的 IMEI，其他为 SN';
COMMENT ON COLUMN fact_store_sales.payment_method IS '收款方式：银联国补pos、线下微信、线下支付宝、荣耀+微信、京东收银、闪回收平台、现金等';
COMMENT ON COLUMN fact_store_sales.recycle_price IS '回收业务：付给顾客的提货价格';
COMMENT ON COLUMN fact_store_sales.sale_price IS '销售价格（回收业务为回收平台给公司的价格）';
COMMENT ON COLUMN fact_store_sales.received_amount IS '实际到账金额（国补订单为扣除国补后的到账）';
COMMENT ON COLUMN fact_store_sales.subsidy_receivable IS '国家补贴应返金额（国补应返）';
COMMENT ON COLUMN fact_store_sales.sales_incentive IS '销售激励';
COMMENT ON COLUMN fact_store_sales.cost_price IS '进店价（门店成本）';
COMMENT ON COLUMN fact_store_sales.gross_profit IS '单笔毛利，门店毛利直接 SUM';
COMMENT ON COLUMN fact_store_sales.commission IS '单台提成（店员）';
COMMENT ON COLUMN fact_store_sales.red_packet IS '红包（店员激励）';
COMMENT ON COLUMN fact_store_sales.product_type IS '类型：FD（折叠屏）、数字（数字系列）、X系列、畅玩系列、Power、全场景（PC/平板/穿戴/音频/配件）、文创、贴膜、保险、回收、号卡、线上订单';
COMMENT ON COLUMN fact_store_sales.series IS '系列：V6、M8、M8Pro、X80PM、600Pro、Power2、PC、平板、穿戴、音频、配件、充电宝等';
COMMENT ON COLUMN fact_store_sales.is_phone IS '是否计入手机台数：商品名称含「全网通」的手机，不含平板、回收和售价为 0 的赠品；线上订单的手机也计入';
COMMENT ON COLUMN fact_store_sales.order_no IS '系统订单号 / 订金单号';

CREATE TABLE IF NOT EXISTS fact_payments (
    payment_id BIGSERIAL PRIMARY KEY,
    channel VARCHAR(10) NOT NULL,
    paid_at TIMESTAMP NOT NULL,
    store_id INT REFERENCES dim_store(store_id),
    merchant_label VARCHAR(100),
    trade_no VARCHAR(64),
    merchant_order_no VARCHAR(64),
    trade_type VARCHAR(30),
    status VARCHAR(20),
    amount NUMERIC(12,2),
    expense NUMERIC(12,2),
    refund_amount NUMERIC(12,2),
    fee NUMERIC(12,4),
    fee_rate VARCHAR(10),
    balance NUMERIC(14,2),
    remark TEXT,
    source_file VARCHAR(200)
);
COMMENT ON TABLE fact_payments IS '微信 / 支付宝收款明细，用于对账。收款额 = SUM(amount)，手续费 = SUM(fee)。store_id 为空表示收款码（如 SCN 开头的终端号）未对应到门店';
COMMENT ON COLUMN fact_payments.channel IS '微信 或 支付宝';
COMMENT ON COLUMN fact_payments.merchant_label IS '收款门店 / 终端名称（原始商品名称字段）';
COMMENT ON COLUMN fact_payments.trade_type IS '微信：JSAPI（扫码）/ MICROPAY（付款码）；支付宝：账务类型（在线支付、分账、提现、收费等）';
COMMENT ON COLUMN fact_payments.status IS '微信交易状态：SUCCESS / REFUND';
COMMENT ON COLUMN fact_payments.amount IS '收入金额：微信为订单金额，支付宝为收入';
COMMENT ON COLUMN fact_payments.expense IS '支付宝支出金额（提现、退款、服务费）';
COMMENT ON COLUMN fact_payments.fee IS '手续费';

CREATE TABLE IF NOT EXISTS fact_purchase_in (
    in_id BIGSERIAL PRIMARY KEY,
    doc_no VARCHAR(40) NOT NULL,
    doc_date DATE NOT NULL,
    biz_time TIMESTAMP,
    company VARCHAR(50),
    doc_type VARCHAR(30),
    in_method VARCHAR(30),
    supplier VARCHAR(100),
    audit_status VARCHAR(20),
    warehouse VARCHAR(50),
    item_code VARCHAR(30),
    product_name TEXT,
    category VARCHAR(50),
    brand VARCHAR(30),
    quantity INT,
    unit_price NUMERIC(12,2),
    amount NUMERIC(14,2),
    cost_amount NUMERIC(14,2),
    remark TEXT
);
COMMENT ON TABLE fact_purchase_in IS 'ERP 入库明细（采购入库）。入库金额 = SUM(amount)，入库数量 = SUM(quantity)';
COMMENT ON COLUMN fact_purchase_in.category IS 'ERP 商品分类，如 手机.荣耀、穿戴、平板、融合';

CREATE TABLE IF NOT EXISTS fact_inventory (
    inventory_id BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    item_code VARCHAR(30),
    product_name TEXT,
    category VARCHAR(50),
    brand VARCHAR(30),
    stock_qty INT,
    transfer_out_qty INT,
    transfer_in_qty INT,
    barcode VARCHAR(40)
);
COMMENT ON TABLE fact_inventory IS 'ERP 库存明细快照（全公司合计，不分门店）。stock_qty 为当前库存，transfer_*_qty 为调拨在途';

CREATE TABLE IF NOT EXISTS fact_stock_age (
    stock_id BIGSERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    store_id INT REFERENCES dim_store(store_id),
    warehouse VARCHAR(50),
    category VARCHAR(50),
    product_name TEXT,
    serial_no VARCHAR(64),
    quantity INT,
    brand VARCHAR(30),
    supplier VARCHAR(100),
    cost_price NUMERIC(12,2),
    in_price NUMERIC(12,2),
    in_time TIMESTAMP,
    age_days INT,
    model VARCHAR(100)
);
COMMENT ON TABLE fact_stock_age IS '库龄表：各门店仓库在库商品（按串号），age_days 为库龄天数。库存金额 = SUM(cost_price * quantity)；超过 90 天视为长库龄';
COMMENT ON COLUMN fact_stock_age.category IS '商品分类：手机.荣耀、演示机、融合、音影、穿戴、平板、配件 等';
COMMENT ON COLUMN fact_stock_age.brand IS '品牌：荣耀、荣耀亲选、极选、二手机';

CREATE TABLE IF NOT EXISTS fact_store_targets (
    target_id BIGSERIAL PRIMARY KEY,
    store_id INT REFERENCES dim_store(store_id),
    month_label VARCHAR(7) NOT NULL,
    owner VARCHAR(30) NOT NULL,
    owner_type VARCHAR(10) NOT NULL,
    item VARCHAR(30) NOT NULL,
    unit VARCHAR(10) NOT NULL,
    target_value NUMERIC(14,2),
    achieved_value NUMERIC(14,2),
    as_of DATE
);
COMMENT ON TABLE fact_store_targets IS '月度任务（门店达成一览表）：门店和店员按产品线的毛利 / 台数目标及截至 as_of 的完成值。达成率 = achieved_value / target_value';
COMMENT ON COLUMN fact_store_targets.owner IS '门店简称或店员姓名';
COMMENT ON COLUMN fact_store_targets.owner_type IS 'store（门店）或 staff（店员）';
COMMENT ON COLUMN fact_store_targets.item IS '考核项：总毛利、FD、V6、X系列、数字、全场景、PC、平板、回收、贴膜、号卡、保险 等';
COMMENT ON COLUMN fact_store_targets.unit IS '毛利（元）或 台数 / 笔数 / 数量';

CREATE TABLE IF NOT EXISTS dim_product_policy (
    policy_id BIGSERIAL PRIMARY KEY,
    month_label VARCHAR(7) NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    config VARCHAR(50),
    store_margin NUMERIC(12,2),
    cost_price NUMERIC(12,2),
    guide_price NUMERIC(12,2),
    guide_commission NUMERIC(12,2),
    so_incentive NUMERIC(12,2),
    promo_price NUMERIC(12,2),
    promo_commission NUMERIC(12,2),
    remark TEXT
);
COMMENT ON TABLE dim_product_policy IS '门店指导书 · 产品政策（按月）：进店价、常规指导价、提成、SO 激励、活动促销价';
COMMENT ON COLUMN dim_product_policy.store_margin IS '进店毛利';
COMMENT ON COLUMN dim_product_policy.guide_price IS '常规指导价（零售价）';
COMMENT ON COLUMN dim_product_policy.guide_commission IS '按指导价销售的单台提成';
COMMENT ON COLUMN dim_product_policy.so_incentive IS 'SO 激励：进店价 - SO 激励 = 本月实际进店价';
COMMENT ON COLUMN dim_product_policy.promo_price IS '活动促销价';
COMMENT ON COLUMN dim_product_policy.promo_commission IS '按促销价销售的单台提成';

CREATE TABLE IF NOT EXISTS dim_product_price (
    price_id BIGSERIAL PRIMARY KEY,
    category VARCHAR(30),
    product_name TEXT NOT NULL UNIQUE,
    retail_price NUMERIC(12,2),
    cost_price NUMERIC(12,2),
    gross_margin NUMERIC(12,2)
);
COMMENT ON TABLE dim_product_price IS '进店价格表（SKU 级）：零售价、进店价、单台毛利';

CREATE INDEX IF NOT EXISTS idx_store_sales_store_month ON fact_store_sales(store_id, month_label);
CREATE INDEX IF NOT EXISTS idx_store_sales_date ON fact_store_sales(sale_date);
CREATE INDEX IF NOT EXISTS idx_payments_paid_at ON fact_payments(paid_at);
CREATE INDEX IF NOT EXISTS idx_stock_age_store ON fact_stock_age(store_id);
"""


def create_schema(conn: Connection) -> None:
    for table in LEGACY_DEMO_TABLES:
        conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    conn.execute(text(DDL))
    for code, name, short, mall, aliases in STORES:
        conn.execute(
            text("""
                INSERT INTO dim_store (store_code, store_name, short_name, mall, aliases)
                VALUES (:code, :name, :short, :mall, :aliases)
                ON CONFLICT (store_code) DO UPDATE
                SET store_name = EXCLUDED.store_name, short_name = EXCLUDED.short_name,
                    mall = EXCLUDED.mall, aliases = EXCLUDED.aliases
            """),
            {"code": code, "name": name, "short": short, "mall": mall, "aliases": aliases},
        )


def store_lookup(conn: Connection) -> dict[str, int]:
    """门店名 / 简称 / 别名 → store_id。"""
    lookup: dict[str, int] = {}
    for row in conn.execute(text("SELECT store_id, store_name, short_name, mall, aliases FROM dim_store")).mappings():
        for name in [row["store_name"], row["short_name"], row["mall"], *row["aliases"]]:
            if name:
                lookup[name] = row["store_id"]
    return lookup


def match_store(label: str | None, lookup: dict[str, int]) -> int | None:
    """在一段文字（文件名、收款门店名、仓库名）中找门店，优先匹配更长的叫法。"""
    if not label:
        return None
    for name in sorted(lookup, key=len, reverse=True):
        if name in label:
            return lookup[name]
    return None
