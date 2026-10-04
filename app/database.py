# -*- coding: utf-8 -*-
"""SQLite 数据库层。

- keyword_data   ：原始关键词历史快照，只增不覆盖（同一自然键重复导入会被忽略）
- opportunity_data：机会计算结果（每次分析追加一条历史；--replace-run 可清理同基准旧批次）

设计要点：保留全部历史，便于后续 周→周 / 月→月 与趋势对比。
"""
from __future__ import annotations

import itertools
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Optional

from app.config import DATABASE_PATH
from app.models import (
    COLLECTION_LOG_COLUMNS,
    KEYWORD_COLUMNS,
    OPPORTUNITY_ANALYSIS_COLUMNS,
    OPPORTUNITY_CLUSTER_COLUMNS,
    OPPORTUNITY_COLUMNS,
    OPPORTUNITY_DIRECTION_COLUMNS,
    OPPORTUNITY_POOL_COLUMNS,
    OPPORTUNITY_V11_COLUMNS,
    SCREENING_COLUMNS,
    SEMANTIC_COLUMNS,
    KeywordRecord,
    OpportunityAnalysisRecord,
    OpportunityClusterRecord,
    OpportunityDirectionRecord,
    OpportunityPoolRecord,
    OpportunityRecord,
    OpportunityV11Record,
    ScreeningRecord,
    CollectionLogRecord,
    SemanticRecord,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_data (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start      TEXT    NOT NULL,
    period_end        TEXT    NOT NULL,
    period_type       TEXT    NOT NULL,              -- 'month' 月 / 'week' 周
    market            TEXT    NOT NULL,              -- 'TH' 泰国
    category          TEXT    NOT NULL,              -- 榜单类目原文（导出/页面实际）
    level_1_category  TEXT,                          -- 规范化一级类目(如 时尚配件)；旧行回填=category
    level_2_category  TEXT,                          -- 规范化二级类目(如 平价饰品)；一级数据为 NULL
    keyword_type      TEXT    NOT NULL,              -- 榜单类型：热门搜索关键词/飙升/高潜力
    keyword           TEXT    NOT NULL,              -- 关键词原文（泰文/数字原样保存）
    rank              INTEGER,
    search_volume     REAL,                          -- 导出原值，保留小数
    product_clicks    REAL,
    sku_sales_index   REAL,
    on_sale_products  INTEGER,
    avg_price         REAL,
    price_unit        TEXT,                          -- 'THB' 等；导出未标注为 NULL
    ctr_index         REAL,
    ctor_score        REAL,
    source_file       TEXT,
    file_hash         TEXT,                          -- 源文件 sha256 前16位（重复导入识别）
    imported_at       TEXT,
    -- 自然键去重：同一市场/榜单类目/榜单类型/粒度的同一关键词只保留首次导入。
    -- 周与月(period_start/end/type 不同)、一级与二级(category 不同)天然分开，历史不被覆盖。
    -- 注: 同一份文件重复导入 → 自然键完全相同 → INSERT OR IGNORE 幂等。
    UNIQUE (period_start, period_end, period_type, market, category, keyword_type, keyword)
);
CREATE INDEX IF NOT EXISTS idx_kw_snapshot
    ON keyword_data (market, category, keyword_type, period_type, period_start);
CREATE INDEX IF NOT EXISTS idx_kw_keyword ON keyword_data (keyword);

CREATE TABLE IF NOT EXISTS opportunity_data (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    computed_at        TEXT    NOT NULL,
    market             TEXT    NOT NULL,
    category           TEXT    NOT NULL,
    keyword_type       TEXT    NOT NULL,
    period_type        TEXT    NOT NULL,
    period_start       TEXT    NOT NULL,
    prev_period_start  TEXT,
    cohort_size        INTEGER NOT NULL,
    keyword            TEXT    NOT NULL,
    search_volume      REAL,
    search_volume_prev REAL,
    growth_rate        REAL,
    on_sale_products   INTEGER,          -- 源数据：在售商品数
    avg_price          REAL,             -- 源数据：平均价格(฿=THB)
    demand_score       REAL,
    growth_score       REAL,
    click_score        REAL,
    sales_score        REAL,
    competition_score  REAL,
    price_score        REAL,
    opportunity_score  REAL    NOT NULL,
    label              TEXT    NOT NULL,
    reasons            TEXT    NOT NULL               -- JSON 数组(中文解释)
);
CREATE INDEX IF NOT EXISTS idx_opp_snapshot
    ON opportunity_data (market, category, keyword_type, period_type, period_start, computed_at);

CREATE TABLE IF NOT EXISTS ai_semantic_results (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    original_keyword    TEXT    NOT NULL,               -- 原始关键词(泰文原样)
    market              TEXT    NOT NULL,
    category            TEXT,                           -- 来源上下文(溯源参考)
    keyword_type        TEXT,
    period_start        TEXT,
    language            TEXT,                           -- 默认 th
    meaning_zh          TEXT,                           -- 中文含义
    product_category    TEXT,                           -- 商品大类(中文)
    product_subcategory TEXT,                           -- 商品子类(中文)
    canonical_product_name TEXT,                        -- 标准商品机会名称(用户可理解具体商品)
    canonical_product_key TEXT,                         -- 商品机会归一化键(聚类稳定键, 如 hair_tie)
    search_terms_1688   TEXT    NOT NULL DEFAULT '[]',  -- JSON 数组
    is_product          INTEGER,                        -- 1=PRODUCT / 0=NON_PRODUCT / NULL=AMBIGUOUS
    intent_status       TEXT,                           -- PRODUCT | NON_PRODUCT | AMBIGUOUS
    confidence          REAL,                           -- AI 置信度 0~1
    model               TEXT,                           -- 使用的模型(可追溯)
    status              TEXT    NOT NULL CHECK (status IN ('success','failed')),
    attempts            INTEGER NOT NULL DEFAULT 0,     -- 已尝试次数
    error               TEXT,                           -- 失败信息留痕
    created_at          TEXT,                           -- 首次写入
    updated_at          TEXT,                           -- 最近状态变更
    UNIQUE (market, original_keyword)                   -- 同词一条语义缓存
);
CREATE INDEX IF NOT EXISTS idx_sem_status ON ai_semantic_results (status, market);
CREATE INDEX IF NOT EXISTS idx_sem_keyword ON ai_semantic_results (original_keyword);

-- V1 初期关键词筛选结果（keyword_data → 清洗 → 非商品过滤 → 通道 → 评分 → TopN）
CREATE TABLE IF NOT EXISTS screening_v1 (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            TEXT    NOT NULL,               -- 批次(computed_at)
    market            TEXT    NOT NULL,
    category          TEXT    NOT NULL,
    keyword_type      TEXT    NOT NULL,
    period_type       TEXT    NOT NULL,
    period_start      TEXT    NOT NULL,
    keyword           TEXT    NOT NULL,
    passed            INTEGER NOT NULL DEFAULT 0,     -- 1=通过 0=淘汰
    channel           TEXT,                           -- main/protection
    reject_category   TEXT,                           -- data_quality/non_product/low_demand
    reject_reason     TEXT,
    demand_pct        REAL,                           -- 搜索量百分位 0~1
    supply_pct        REAL,                           -- 在售商品百分位 0~1
    opportunity_gap   REAL,                           -- demand_pct - supply_pct
    ctor_pct          REAL,
    sku_pct           REAL,
    purchase_intent   REAL,                           -- 0.70×ctor_pct + 0.30×sku_pct
    opportunity_score REAL,                           -- 0.40×demand + 0.30×intent + 0.30×gap
    top_rank          INTEGER,                        -- 候选名次 1..N
    UNIQUE (run_id, market, keyword)
);
CREATE INDEX IF NOT EXISTS idx_scrn_run ON screening_v1 (run_id, passed);
CREATE INDEX IF NOT EXISTS idx_scrn_top ON screening_v1 (run_id, top_rank);

-- V1.1 商品机会（Python 聚类 + 评分；一 canonical_product_key 一机会）
CREATE TABLE IF NOT EXISTS product_opportunities (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 TEXT    NOT NULL,
    opportunity_id         TEXT    NOT NULL,
    market                 TEXT    NOT NULL,
    canonical_product_key  TEXT    NOT NULL,
    canonical_product_name TEXT    NOT NULL,
    keyword_count          INTEGER NOT NULL DEFAULT 0,
    source_keywords        TEXT    NOT NULL DEFAULT '[]',
    best_keyword           TEXT,
    best_keyword_score     REAL,
    best_demand_pct        REAL,
    best_purchase_intent   REAL,
    best_supply_pct        REAL,
    best_opportunity_gap   REAL,
    evidence_consistency   REAL,
    demand_component       REAL,
    intent_component       REAL,
    gap_component          REAL,
    evidence_component     REAL,
    opportunity_score      REAL    NOT NULL,
    is_final               INTEGER NOT NULL DEFAULT 0,
    final_rank             INTEGER,
    status                 TEXT    NOT NULL DEFAULT 'pool',
    created_at             TEXT,
    UNIQUE (run_id, canonical_product_key)
);
CREATE INDEX IF NOT EXISTS idx_opp11_run ON product_opportunities (run_id, is_final);

-- V2 商品机会池（跨 level/周期的后台全量机会池；正式 v1 结果不被影响）
CREATE TABLE IF NOT EXISTS product_opportunities_v2 (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 TEXT    NOT NULL,
    opportunity_id         TEXT    NOT NULL,
    market                 TEXT    NOT NULL,
    level_1_category       TEXT    NOT NULL,
    level_2_category       TEXT,
    level_3_category       TEXT,
    canonical_product_name TEXT    NOT NULL,
    canonical_product_key  TEXT    NOT NULL,
    score                  REAL    NOT NULL,
    demand_score           REAL,
    purchase_intent_score  REAL,
    supply_score           REAL,
    opportunity_gap        REAL,
    evidence_count         INTEGER NOT NULL DEFAULT 0,
    source_keywords        TEXT    NOT NULL DEFAULT '[]',
    weekly_evidence        TEXT    NOT NULL DEFAULT '[]',
    monthly_evidence       TEXT    NOT NULL DEFAULT '[]',
    first_seen             TEXT,
    last_seen              TEXT,
    category_status        TEXT    NOT NULL DEFAULT 'REVIEW',
    created_at             TEXT,
    updated_at             TEXT,
    UNIQUE (run_id, level_1_category, level_2_category, canonical_product_key)
);
CREATE INDEX IF NOT EXISTS idx_pool_run ON product_opportunities_v2 (run_id, category_status);

-- V2.1 分析层（独立于机会池；归纳≠删除/聚类≠合并；原始 pool 永不修改）
CREATE TABLE IF NOT EXISTS opportunity_directions (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 TEXT NOT NULL,
    direction_id           TEXT NOT NULL,
    market                 TEXT NOT NULL,
    direction_name         TEXT,
    description            TEXT,
    opportunity_count      INTEGER NOT NULL DEFAULT 0,
    core_count             INTEGER NOT NULL DEFAULT 0,
    adjacent_count         INTEGER NOT NULL DEFAULT 0,
    review_count           INTEGER NOT NULL DEFAULT 0,
    weekly_supported_count INTEGER NOT NULL DEFAULT 0,
    monthly_supported_count INTEGER NOT NULL DEFAULT 0,
    priority               REAL NOT NULL DEFAULT 0,
    evidence_summary       TEXT NOT NULL DEFAULT '{}',
    risk_summary           TEXT NOT NULL DEFAULT '{}',
    member_keys            TEXT NOT NULL DEFAULT '[]',
    created_at             TEXT,
    updated_at             TEXT,
    UNIQUE (run_id, direction_id)
);
CREATE TABLE IF NOT EXISTS opportunity_clusters (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 TEXT NOT NULL,
    cluster_id             TEXT NOT NULL,
    direction_id           TEXT NOT NULL,
    market                 TEXT NOT NULL,
    cluster_name           TEXT,
    cluster_type           TEXT NOT NULL DEFAULT 'PRODUCT',
    description            TEXT,
    opportunity_count      INTEGER NOT NULL DEFAULT 0,
    core_count             INTEGER NOT NULL DEFAULT 0,
    adjacent_count         INTEGER NOT NULL DEFAULT 0,
    weekly_supported_count INTEGER NOT NULL DEFAULT 0,
    monthly_supported_count INTEGER NOT NULL DEFAULT 0,
    priority               REAL NOT NULL DEFAULT 0,
    evidence_summary       TEXT NOT NULL DEFAULT '{}',
    risk_summary           TEXT NOT NULL DEFAULT '{}',
    member_keys            TEXT NOT NULL DEFAULT '[]',
    created_at             TEXT,
    updated_at             TEXT,
    UNIQUE (run_id, cluster_id)
);
CREATE INDEX IF NOT EXISTS idx_cluster_dir ON opportunity_clusters (run_id, direction_id);
CREATE TABLE IF NOT EXISTS opportunity_analysis (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 TEXT NOT NULL,
    analysis_id            TEXT NOT NULL,
    opportunity_id         TEXT NOT NULL,
    market                 TEXT NOT NULL,
    canonical_product_key  TEXT NOT NULL,
    cluster_id             TEXT,
    direction_id           TEXT,
    product_type           TEXT,
    attributes             TEXT NOT NULL DEFAULT '[]',
    style                  TEXT NOT NULL DEFAULT '[]',
    target_group           TEXT NOT NULL DEFAULT '[]',
    evidence_state         TEXT NOT NULL DEFAULT 'UNKNOWN',
    reason_tags            TEXT NOT NULL DEFAULT '[]',
    risk_tags              TEXT NOT NULL DEFAULT '[]',
    analysis_text          TEXT,
    model                  TEXT NOT NULL DEFAULT 'rule-based-v2.1',
    created_at             TEXT,
    updated_at             TEXT,
    UNIQUE (run_id, canonical_product_key)
);
CREATE INDEX IF NOT EXISTS idx_ana_run ON opportunity_analysis (run_id, direction_id, cluster_id);

-- 数据采集日志（每次采集尝试一条记录，含成功/失败/重复，独立于 keyword_data）
CREATE TABLE IF NOT EXISTS collection_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    market              TEXT    NOT NULL,
    level_1_category    TEXT    NOT NULL,
    level_2_category    TEXT,
    ranking_type        TEXT    NOT NULL,
    period_granularity  TEXT    NOT NULL,
    period_start        TEXT,
    period_end          TEXT,
    ui_period           TEXT,
    source_file         TEXT,
    file_hash           TEXT,
    file_size_bytes     INTEGER,
    n_records           INTEGER,
    inserted_rows       INTEGER,
    skipped_duplicates  INTEGER,
    status              TEXT    NOT NULL,
    error               TEXT,
    mapping_table       TEXT    NOT NULL DEFAULT '{}',
    collected_at        TEXT    NOT NULL,
    imported_at         TEXT
);
CREATE INDEX IF NOT EXISTS idx_coll_status ON collection_log (status);
CREATE INDEX IF NOT EXISTS idx_coll_key ON collection_log (market, level_1_category, level_2_category, ranking_type, period_start);

-- Page TOP20 页面原生排序采集数据（独立表，不混入 keyword_data）
-- 每行 = 某周期某榜单某排序指标下的一个 TOP20 关键词
CREATE TABLE IF NOT EXISTS page_top20_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market TEXT NOT NULL,
    level_1_category TEXT NOT NULL,
    level_2_category TEXT,
    ranking_type TEXT NOT NULL,
    period_granularity TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    ranking_field TEXT NOT NULL,
    rank INTEGER NOT NULL,
    keyword TEXT NOT NULL,
    display_value TEXT NOT NULL,
    numeric_value REAL,
    source_file TEXT,
    batch_id TEXT,
    collected_at TEXT NOT NULL,
    UNIQUE(period_start, period_end, period_granularity, market, level_1_category, level_2_category, ranking_type, ranking_field, keyword)
);
CREATE INDEX IF NOT EXISTS idx_top20_key ON page_top20_data (market, level_1_category, level_2_category, ranking_type, period_start);
CREATE INDEX IF NOT EXISTS idx_top20_batch ON page_top20_data (batch_id);
"""


def now_str() -> str:
    """时间戳 + 进程内单调序号：同一批入库/同一次分析共用一个值。

    注意：Windows 上 time.time()/time_ns() 存在毫秒级量化，连续调用可能返回相同值，
    不能只靠时间区分批次；故叠加进程内递增序号（每次调用必然不同）。
    """
    return time.strftime("%Y-%m-%d %H:%M:%S") + f".{next(_SEQ):06d}"


# 进程内单调批次序号（now_str 每次调用必然不同，见 now_str 注释）
_SEQ = itertools.count(1)


def connect(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    """打开连接（默认 database/opportunity.db），并启用行工厂。"""
    path = Path(db_path) if db_path else DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表（幂等），并对既有库做轻量列迁移。"""
    conn.executescript(_SCHEMA)
    _ensure_legacy_columns(conn)
    conn.commit()


def _ensure_legacy_columns(conn: sqlite3.Connection) -> None:
    """对早期已建的表补齐新增列（幂等），并回填规范化类目层级。"""
    kd_cols = {r[1] for r in conn.execute("PRAGMA table_info(keyword_data)").fetchall()}
    for col in ("level_1_category", "level_2_category", "file_hash"):
        if col not in kd_cols:
            conn.execute(f"ALTER TABLE keyword_data ADD COLUMN {col} TEXT")
    # 回填：历史行（只有 category）→ level_1=category, level_2=NULL（一级数据）
    if "level_1_category" in kd_cols or True:
        conn.execute(
            "UPDATE keyword_data SET level_1_category = category "
            "WHERE level_1_category IS NULL OR level_1_category=''")
    # collection_log 新增列（excel_export / page_top20 区分 + batch_id）
    cl_cols = {r[1] for r in conn.execute("PRAGMA table_info(collection_log)").fetchall()}
    for col in ("source", "batch_id"):
        if col not in cl_cols:
            conn.execute(f"ALTER TABLE collection_log ADD COLUMN {col} TEXT")
    sem_cols = {r[1] for r in conn.execute("PRAGMA table_info(ai_semantic_results)").fetchall()}
    if sem_cols:
        for col in ("intent_status", "canonical_product_name", "canonical_product_key"):
            if col not in sem_cols:
                conn.execute(f"ALTER TABLE ai_semantic_results ADD COLUMN {col} TEXT")
    conn.commit()


# ---------------------------------------------------------------------------
# keyword_data 仓储（原始数据，只增不改）
# ---------------------------------------------------------------------------
class KeywordRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert_many(self, records: Iterable[KeywordRecord]) -> tuple[int, int]:
        """批量插入。返回 (成功插入数, 因自然键重复被忽略数)。历史不覆盖。"""
        recs = list(records)
        if not recs:
            return 0, 0
        ts = now_str()
        placeholders = ",".join("?" * len(KEYWORD_COLUMNS))
        sql = f"INSERT OR IGNORE INTO keyword_data ({','.join(KEYWORD_COLUMNS)}) VALUES ({placeholders})"
        inserted = 0
        with self.conn:  # 事务
            for r in recs:
                r.imported_at = ts  # 同一批同时间戳
                cur = self.conn.execute(sql, r.as_tuple())
                inserted += cur.rowcount  # 被 IGNORE 的行 rowcount=0
        return inserted, len(recs) - inserted

    def snapshot_periods(
        self, market: str, category: str, keyword_type: str, period_type: str
    ) -> list[str]:
        """该口径下已有的周期（升序，YYYY-MM-DD 字符串可排序）。"""
        rows = self.conn.execute(
            "SELECT DISTINCT period_start FROM keyword_data "
            "WHERE market=? AND category=? AND keyword_type=? AND period_type=? "
            "ORDER BY period_start ASC",
            (market, category, keyword_type, period_type),
        ).fetchall()
        return [r["period_start"] for r in rows]

    # ---- 按规范化类目层级(level_1/level_2)读取（标准化市场数据层入口）----
    def snapshot_periods_by_level(
        self, market: str, level_1: str, level_2: Optional[str],
        keyword_type: str, period_type: str,
    ) -> list[str]:
        """按 level 层级取已有周期。level_2=None 表示只取一级数据(level_2 IS NULL)。"""
        if level_2:
            sql = ("SELECT DISTINCT period_start FROM keyword_data "
                   "WHERE market=? AND level_1_category=? AND level_2_category=? "
                   "AND keyword_type=? AND period_type=? ORDER BY period_start ASC")
            args = (market, level_1, level_2, keyword_type, period_type)
        else:
            sql = ("SELECT DISTINCT period_start FROM keyword_data "
                   "WHERE market=? AND level_1_category=? AND level_2_category IS NULL "
                   "AND keyword_type=? AND period_type=? ORDER BY period_start ASC")
            args = (market, level_1, keyword_type, period_type)
        return [r["period_start"] for r in self.conn.execute(sql, args).fetchall()]

    def snapshot_rows_by_level(
        self, market: str, level_1: str, level_2: Optional[str],
        keyword_type: str, period_type: str, period_start: str,
    ) -> list[sqlite3.Row]:
        """按 level 层级取某一周期快照全部行（一级=level_2 IS NULL；二级=精确匹配）。"""
        if level_2:
            sql = ("SELECT * FROM keyword_data "
                   "WHERE market=? AND level_1_category=? AND level_2_category=? "
                   "AND keyword_type=? AND period_type=? AND period_start=? "
                   "ORDER BY (rank IS NULL), rank ASC, id ASC")
            args = (market, level_1, level_2, keyword_type, period_type, period_start)
        else:
            sql = ("SELECT * FROM keyword_data "
                   "WHERE market=? AND level_1_category=? AND level_2_category IS NULL "
                   "AND keyword_type=? AND period_type=? AND period_start=? "
                   "ORDER BY (rank IS NULL), rank ASC, id ASC")
            args = (market, level_1, keyword_type, period_type, period_start)
        return self.conn.execute(sql, args).fetchall()

    def snapshot_rows(
        self, market: str, category: str, keyword_type: str, period_type: str, period_start: str
    ) -> list[sqlite3.Row]:
        """取某一周期快照全部行（按榜单名次排序）。"""
        return self.conn.execute(
            "SELECT * FROM keyword_data "
            "WHERE market=? AND category=? AND keyword_type=? AND period_type=? AND period_start=? "
            "ORDER BY (rank IS NULL), rank ASC, id ASC",
            (market, category, keyword_type, period_type, period_start),
        ).fetchall()

    def previous_period(
        self, market: str, category: str, keyword_type: str, period_type: str, period_start: str
    ) -> Optional[str]:
        """严格早于基准周期的最近一个周期（用于环比）。"""
        row = self.conn.execute(
            "SELECT MAX(period_start) AS p FROM keyword_data "
            "WHERE market=? AND category=? AND keyword_type=? AND period_type=? AND period_start < ?",
            (market, category, keyword_type, period_type, period_start),
        ).fetchone()
        return row["p"] if row and row["p"] else None


# ---------------------------------------------------------------------------
# opportunity_data 仓储（计算结果）
# ---------------------------------------------------------------------------
class OpportunityRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def delete_snapshot_batch(
        self, market: str, category: str, keyword_type: str, period_type: str, period_start: str
    ) -> int:
        """删除同一基准周期的历史机会批次（--replace-run 用，避免重复分析堆积）。"""
        cur = self.conn.execute(
            "DELETE FROM opportunity_data WHERE market=? AND category=? AND keyword_type=? "
            "AND period_type=? AND period_start=?",
            (market, category, keyword_type, period_type, period_start),
        )
        self.conn.commit()
        return cur.rowcount

    def insert_many(self, records: Iterable[OpportunityRecord]) -> int:
        recs = list(records)
        if not recs:
            return 0
        placeholders = ",".join("?" * len(OPPORTUNITY_COLUMNS))
        sql = f"INSERT INTO opportunity_data ({','.join(OPPORTUNITY_COLUMNS)}) VALUES ({placeholders})"
        with self.conn:
            for r in recs:
                self.conn.execute(sql, r.as_tuple())
        return len(recs)

    def latest(
        self, market: str, category: str, keyword_type: str, period_type: str,
        limit: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        """最近一次分析结果（机会分从高到低）。"""
        sub = (
            "SELECT MAX(computed_at) AS c FROM opportunity_data "
            "WHERE market=? AND category=? AND keyword_type=? AND period_type=?"
        )
        sql = (
            "SELECT * FROM opportunity_data WHERE market=? AND category=? AND keyword_type=? "
            "AND period_type=? AND computed_at = (" + sub + ") "
            "ORDER BY opportunity_score DESC, search_volume DESC"
        )
        # 外层 4 个 + 子查询 4 个 = 8 个占位符
        args = (market, category, keyword_type, period_type) * 2
        if limit:
            sql += " LIMIT ?"
            args = args + (limit,)
        return self.conn.execute(sql, args).fetchall()


# ---------------------------------------------------------------------------
# ai_semantic_results 仓储（AI 语义结果：成功缓存 + 失败可重试）
# ---------------------------------------------------------------------------
class SemanticRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _now() -> str:
        return now_str()

    def upsert_success(self, rec: SemanticRecord) -> None:
        """写入/覆盖为 success。已存在的 created_at 保留（首见时间），仅刷新结果与时间。

        注意：DO UPDATE 必须覆盖全部语义列（含 intent_status/canonical_*），
        否则旧 failed 行升级为 success 时会残留 NULL（真实调用踩过的坑，有回归测试）。
        """
        cols = SEMANTIC_COLUMNS
        placeholders = ",".join("?" * len(cols))
        sql = (
            f"INSERT INTO ai_semantic_results ({','.join(cols)}) VALUES ({placeholders}) "
            "ON CONFLICT (market, original_keyword) DO UPDATE SET "
            "category=excluded.category, keyword_type=excluded.keyword_type, "
            "period_start=excluded.period_start, language=excluded.language, "
            "meaning_zh=excluded.meaning_zh, product_category=excluded.product_category, "
            "product_subcategory=excluded.product_subcategory, "
            "canonical_product_name=excluded.canonical_product_name, "
            "canonical_product_key=excluded.canonical_product_key, "
            "search_terms_1688=excluded.search_terms_1688, is_product=excluded.is_product, "
            "intent_status=excluded.intent_status, confidence=excluded.confidence, "
            "model=excluded.model, status='success', "
            "attempts=excluded.attempts, error=NULL, updated_at=excluded.updated_at"
        )
        with self.conn:
            self.conn.execute(sql, rec.as_tuple())

    def upsert_failed(self, rec: SemanticRecord) -> None:
        """写入/覆盖为 failed（仅当旧行不是 success——成功缓存不允许被失败覆盖）。"""
        cols = SEMANTIC_COLUMNS
        placeholders = ",".join("?" * len(cols))
        sql = (
            f"INSERT INTO ai_semantic_results ({','.join(cols)}) VALUES ({placeholders}) "
            "ON CONFLICT (market, original_keyword) DO UPDATE SET "
            "category=excluded.category, keyword_type=excluded.keyword_type, "
            "period_start=excluded.period_start, language=excluded.language, "
            "status='failed', attempts=excluded.attempts, error=excluded.error, "
            "updated_at=excluded.updated_at "
            "WHERE ai_semantic_results.status != 'success'"
        )
        with self.conn:
            self.conn.execute(sql, rec.as_tuple())

    def get(self, market: str, keyword: str) -> Optional[sqlite3.Row]:
        row = self.conn.execute(
            "SELECT * FROM ai_semantic_results WHERE market=? AND original_keyword=?",
            (market, keyword),
        ).fetchone()
        return row

    def pending_keywords(
        self,
        market: str,
        *,
        category: Optional[str] = None,
        keyword_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        """keyword_data 中尚无 success 语义缓存的词（含曾失败的词，用于断点重试）。

        同词跨周期只取一行做溯源参考；排序：该词历史最大搜索量降序（需求大的先处理）。
        """
        sql = (
            "SELECT k.keyword, k.category, k.keyword_type, k.period_start, "
            "       MAX(k.search_volume) AS max_search_volume "
            "FROM keyword_data k "
            "WHERE k.market = ? "
            "  AND NOT EXISTS (SELECT 1 FROM ai_semantic_results s "
            "                  WHERE s.market = k.market "
            "                    AND s.original_keyword = k.keyword "
            "                    AND s.status = 'success') "
        )
        args: list = [market]
        if category:
            sql += " AND k.category = ?"
            args.append(category)
        if keyword_type:
            sql += " AND k.keyword_type = ?"
            args.append(keyword_type)
        sql += " GROUP BY k.keyword ORDER BY max_search_volume DESC"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return self.conn.execute(sql, args).fetchall()

    def failed_attempts(self, market: str) -> dict[str, int]:
        """返回 {关键词: 已失败尝试次数}（用于 attempts 上限判断）。"""
        rows = self.conn.execute(
            "SELECT original_keyword, attempts FROM ai_semantic_results "
            "WHERE market=? AND status='failed'",
            (market,),
        ).fetchall()
        return {r["original_keyword"]: r["attempts"] for r in rows}

    def count_by_status(self, market: str) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM ai_semantic_results WHERE market=? GROUP BY status",
            (market,),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# screening_v1 仓储（V1 筛选结果，历史批次追加）
# ---------------------------------------------------------------------------
class ScreeningRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert_many(self, records: Iterable[ScreeningRecord]) -> int:
        recs = list(records)
        if not recs:
            return 0
        placeholders = ",".join("?" * len(SCREENING_COLUMNS))
        sql = f"INSERT INTO screening_v1 ({','.join(SCREENING_COLUMNS)}) VALUES ({placeholders})"
        with self.conn:
            for r in recs:
                self.conn.execute(sql, r.as_tuple())
        return len(recs)

    def delete_run(self, market: str, category: str, keyword_type: str,
                   period_type: str, period_start: str) -> int:
        """清理同基准(口径+周期)的历史筛选批次（--replace-run 用）。"""
        cur = self.conn.execute(
            "DELETE FROM screening_v1 WHERE market=? AND category=? AND keyword_type=? "
            "AND period_type=? AND period_start=?",
            (market, category, keyword_type, period_type, period_start),
        )
        self.conn.commit()
        return cur.rowcount

    def latest_run_records(
        self, market: str, category: str, keyword_type: str, period_type: str,
        passed_only: bool = True, limit: Optional[int] = None,
    ) -> list[sqlite3.Row]:
        """最近一次筛选批次记录（默认只看通过词，按 top_rank 升序）。"""
        sub = (
            "SELECT MAX(run_id) AS r FROM screening_v1 "
            "WHERE market=? AND category=? AND keyword_type=? AND period_type=?"
        )
        sql = (
            "SELECT * FROM screening_v1 WHERE market=? AND category=? AND keyword_type=? "
            "AND period_type=? AND run_id = (" + sub + ")"
        )
        args = (market, category, keyword_type, period_type) * 2
        if passed_only:
            sql += " AND passed=1 AND top_rank IS NOT NULL ORDER BY top_rank ASC"
        if limit:
            sql += " LIMIT ?"
            args = args + (limit,)
        return self.conn.execute(sql, args).fetchall()


# ---------------------------------------------------------------------------
# product_opportunities 仓储（V1.1 商品机会）
# ---------------------------------------------------------------------------
class OpportunityV11Repo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert_many(self, records: Iterable[OpportunityV11Record]) -> int:
        recs = list(records)
        if not recs:
            return 0
        placeholders = ",".join("?" * len(OPPORTUNITY_V11_COLUMNS))
        sql = f"INSERT INTO product_opportunities ({','.join(OPPORTUNITY_V11_COLUMNS)}) VALUES ({placeholders})"
        with self.conn:
            for r in recs:
                self.conn.execute(sql, r.as_tuple())
        return len(recs)

    def replace_market(self, market: str) -> int:
        """幂等：删除该 market 的全部旧批次，使表只保留最新一次完整快照。

        product_opportunities 仅由 build_opportunities_v11 写入（无其它来源），
        每次运行 = 基于当前 ai_semantic_results 的全量重算 → 重建即幂等，
        重复运行不会产生重复机会行。
        """
        cur = self.conn.execute("DELETE FROM product_opportunities WHERE market=?", (market,))
        self.conn.commit()
        return cur.rowcount

    def delete_run(self, run_id: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM product_opportunities WHERE run_id=?", (run_id,))
        self.conn.commit()
        return cur.rowcount

    def latest(self, market: str = "TH", final_only: bool = True) -> list[sqlite3.Row]:
        """最近一次机会生成批次（final_only=True 只取最终 ≤9，按 final_rank）。"""
        sub = "SELECT MAX(run_id) AS r FROM product_opportunities WHERE market=?"
        sql = ("SELECT * FROM product_opportunities WHERE market=? AND run_id=(" + sub + ")")
        args = (market, market)
        if final_only:
            sql += " AND is_final=1 ORDER BY final_rank ASC"
        else:
            sql += " ORDER BY opportunity_score DESC"
        return self.conn.execute(sql, args).fetchall()


# ---------------------------------------------------------------------------
# product_opportunities_v2 仓储（V2 商品机会池）
# ---------------------------------------------------------------------------
class OpportunityPoolRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert_many(self, records: Iterable[OpportunityPoolRecord]) -> int:
        recs = list(records)
        if not recs:
            return 0
        ph = ",".join("?" * len(OPPORTUNITY_POOL_COLUMNS))
        sql = f"INSERT INTO product_opportunities_v2 ({','.join(OPPORTUNITY_POOL_COLUMNS)}) VALUES ({ph})"
        with self.conn:
            for r in recs:
                self.conn.execute(sql, r.as_tuple())
        return len(recs)

    def replace_all(self) -> int:
        """幂等：清空整表再写当前池（v1 正式 47 在 product_opportunities 不受影响）。"""
        cur = self.conn.execute("DELETE FROM product_opportunities_v2")
        self.conn.commit()
        return cur.rowcount

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM product_opportunities_v2").fetchone()[0]


# ---------------------------------------------------------------------------
# V2.1 分析层仓储（opportunity_directions / opportunity_clusters / opportunity_analysis）
# ---------------------------------------------------------------------------
class AnalysisRepo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def replace_run(self, run_id: str) -> int:
        """幂等：删除同 run 的旧分析结果（同一池批次重复分析不翻倍）。"""
        n = 0
        with self.conn:
            for t in ("opportunity_directions", "opportunity_clusters", "opportunity_analysis"):
                n += self.conn.execute(f"DELETE FROM {t} WHERE run_id=?", (run_id,)).rowcount
        return n

    def insert_directions(self, recs) -> int:
        return self._ins("opportunity_directions", OPPORTUNITY_DIRECTION_COLUMNS, recs)

    def insert_clusters(self, recs) -> int:
        return self._ins("opportunity_clusters", OPPORTUNITY_CLUSTER_COLUMNS, recs)

    def insert_analysis(self, recs) -> int:
        return self._ins("opportunity_analysis", OPPORTUNITY_ANALYSIS_COLUMNS, recs)

    def _ins(self, table: str, cols: list[str], recs) -> int:
        recs = list(recs)
        if not recs:
            return 0
        ph = ",".join("?" * len(cols))
        sql = f"INSERT INTO {table} ({','.join(cols)}) VALUES ({ph})"
        with self.conn:
            for r in recs:
                self.conn.execute(sql, r.as_tuple())
        return len(recs)

    def latest_run(self) -> Optional[str]:
        row = self.conn.execute(
            "SELECT MAX(run_id) AS r FROM opportunity_directions").fetchone()
        return row["r"] if row and row["r"] else None


class CollectionLogRepo:
    """collection_log 仓储：每次采集尝试一条记录。"""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert(self, rec: CollectionLogRecord) -> int:
        ph = ",".join("?" * len(COLLECTION_LOG_COLUMNS))
        sql = f"INSERT INTO collection_log ({','.join(COLLECTION_LOG_COLUMNS)}) VALUES ({ph})"
        with self.conn:
            cur = self.conn.execute(sql, rec.as_tuple())
        self.conn.commit()
        return cur.lastrowid

    def exists_period(
        self, market: str, level_1: str, level_2: Optional[str],
        ranking_type: str, period_start: str, period_end: str,
    ) -> bool:
        if level_2:
            sql = (
                "SELECT 1 FROM keyword_data "
                "WHERE market=? AND level_1_category=? AND level_2_category=? "
                "AND keyword_type=? AND period_start=? AND period_end=? LIMIT 1"
            )
            args = (market, level_1, level_2, ranking_type, period_start, period_end)
        else:
            sql = (
                "SELECT 1 FROM keyword_data "
                "WHERE market=? AND level_1_category=? AND level_2_category IS NULL "
                "AND keyword_type=? AND period_start=? AND period_end=? LIMIT 1"
            )
            args = (market, level_1, ranking_type, period_start, period_end)
        return self.conn.execute(sql, args).fetchone() is not None

    def latest_success(
        self, market: str, level_1: str, level_2: Optional[str], ranking_type: str,
    ) -> Optional[sqlite3.Row]:
        if level_2:
            sql = (
                "SELECT * FROM collection_log "
                "WHERE market=? AND level_1_category=? AND level_2_category=? "
                "AND ranking_type=? AND status='SUCCESS' "
                "ORDER BY collected_at DESC LIMIT 1"
            )
            args = (market, level_1, level_2, ranking_type)
        else:
            sql = (
                "SELECT * FROM collection_log "
                "WHERE market=? AND level_1_category=? AND level_2_category IS NULL "
                "AND ranking_type=? AND status='SUCCESS' "
                "ORDER BY collected_at DESC LIMIT 1"
            )
            args = (market, level_1, ranking_type)
        return self.conn.execute(sql, args).fetchone()

    def all_logs(
        self, market: str, level_1: str, level_2: Optional[str], ranking_type: str,
    ) -> list[sqlite3.Row]:
        if level_2:
            sql = (
                "SELECT * FROM collection_log "
                "WHERE market=? AND level_1_category=? AND level_2_category=? "
                "AND ranking_type=? ORDER BY collected_at DESC"
            )
            args = (market, level_1, level_2, ranking_type)
        else:
            sql = (
                "SELECT * FROM collection_log "
                "WHERE market=? AND level_1_category=? AND level_2_category IS NULL "
                "AND ranking_type=? ORDER BY collected_at DESC"
            )
            args = (market, level_1, ranking_type)
        return self.conn.execute(sql, args).fetchall()


# ---------------------------------------------------------------------------
# page_top20_data 仓储（页面原生排序 TOP20 数据，独立于 keyword_data）
# ---------------------------------------------------------------------------
class PageTop20Repo:
    """Page TOP20 页面采集数据仓储。

    语义约束（由业务层保证，数据库层做最小约束）：
    - 每行 = 某指标某排名的一个关键词
    - 同一关键词在不同 ranking_field 下各存一行（不合并、不去重）
    - display_value 保存页面原始显示值，numeric_value 仅用于校验
    - 历史只增不覆盖
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def insert_many(self, records: Iterable[PageTop20Record]) -> tuple[int, int]:
        """批量插入。返回 (成功插入数, 因自然键重复被忽略数)。历史不覆盖。"""
        from app.models import PAGE_TOP20_COLUMNS  # 延迟导入避免循环
        recs = list(records)
        if not recs:
            return 0, 0
        ts = now_str()
        placeholders = ",".join("?" * len(PAGE_TOP20_COLUMNS))
        sql = f"INSERT OR IGNORE INTO page_top20_data ({','.join(PAGE_TOP20_COLUMNS)}) VALUES ({placeholders})"
        inserted = 0
        with self.conn:
            for r in recs:
                r.collected_at = ts  # 同一批同时间戳
                cur = self.conn.execute(sql, r.as_tuple())
                inserted += cur.rowcount
        return inserted, len(recs) - inserted

    def count_by_batch(self, batch_id: str) -> int:
        """某批次总记录数。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM page_top20_data WHERE batch_id=?",
            (batch_id,),
        ).fetchone()
        return row["n"] if row else 0

    def count_by_ranking_field(self, batch_id: str, ranking_field: str) -> int:
        """某批次某指标的记录数。"""
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM page_top20_data WHERE batch_id=? AND ranking_field=?",
            (batch_id, ranking_field),
        ).fetchone()
        return row["n"] if row else 0

    def get_batch_rows(self, batch_id: str) -> list[sqlite3.Row]:
        """获取某批次全部行。"""
        return self.conn.execute(
            "SELECT * FROM page_top20_data WHERE batch_id=? "
            "ORDER BY ranking_field ASC, rank ASC",
            (batch_id,),
        ).fetchall()

    def latest_valid_batch(
        self, market: str, level_1: str, level_2: Optional[str],
        ranking_type: str, period_granularity: str,
    ) -> Optional[sqlite3.Row]:
        """获取该口径下最新一个完整 SUCCESS 批次（从 collection_log 反查）。

        latest_valid 原则：只有完整 batch 标记为 SUCCESS 的才能被视为有效。
        """
        if level_2:
            sql = (
                "SELECT * FROM collection_log "
                "WHERE market=? AND level_1_category=? AND level_2_category=? "
                "AND ranking_type=? AND source='page_top20' AND status='SUCCESS' "
                "ORDER BY collected_at DESC LIMIT 1"
            )
            args = (market, level_1, level_2, ranking_type)
        else:
            sql = (
                "SELECT * FROM collection_log "
                "WHERE market=? AND level_1_category=? AND level_2_category IS NULL "
                "AND ranking_type=? AND source='page_top20' AND status='SUCCESS' "
                "ORDER BY collected_at DESC LIMIT 1"
            )
            args = (market, level_1, ranking_type)
        return self.conn.execute(sql, args).fetchone()

    def latest_valid_rows(
        self, market: str, level_1: str, level_2: Optional[str],
        ranking_type: str, period_granularity: str,
    ) -> list[sqlite3.Row]:
        """获取该口径下 latest_valid 批次的全部数据行。"""
        log_row = self.latest_valid_batch(market, level_1, level_2, ranking_type, period_granularity)
        if not log_row or not log_row["batch_id"]:
            return []
        return self.get_batch_rows(log_row["batch_id"])

    def delete_batch(self, batch_id: str) -> int:
        """删除整个批次的数据（用于失败回滚 / 清理）。"""
        cur = self.conn.execute("DELETE FROM page_top20_data WHERE batch_id=?", (batch_id,))
        self.conn.commit()
        return cur.rowcount


# 便捷函数：一条连接 + 初始化（脚本入口用）
def open_db(db_path: Optional[str | Path] = None) -> tuple[sqlite3.Connection, KeywordRepo, OpportunityRepo]:
    conn = connect(db_path)
    init_db(conn)
    return conn, KeywordRepo(conn), OpportunityRepo(conn)
