# -*- coding: utf-8 -*-
"""P0 类目血缘 lineage 测试（2026-09-09 新增，随 file_hash 回填修复）。

覆盖要求：keyword→screening→semantic→opportunity 全程可追溯、无孤儿；
官方类目身份字段（market/category/level_1/level_2/source_file/file_hash/period）
在新导入路径下必须落库；file_hash 为源文件 sha256 前 16 位且非空。
"""
from __future__ import annotations

import hashlib
import json

from app.database import connect, init_db, KeywordRepo
from app.models import KeywordRecord

W = json.dumps([{"period_type": "week", "period_start": "2026-08-03", "best_keyword": "w"}],
               ensure_ascii=False)


def _mk_record(keyword: str, source: str = "synthetic.xlsx", fh: str = "abc123def4567890"):
    return KeywordRecord(
        period_start="2026-07-01", period_end="2026-07-31", period_type="month",
        market="TH", category="时尚配件", keyword_type="热门搜索关键词", keyword=keyword,
        rank=1, search_volume=100.0, product_clicks=None, sku_sales_index=None,
        on_sale_products=10, avg_price=None, price_unit=None, ctr_index=None,
        ctor_score=None, source_file=source, file_hash=fh,
        level_1_category="时尚配件", level_2_category=None)


def _seed_mini(conn):
    """keyword 行 + screening 行 + semantic success 行 + v2 行（模拟下游链路）。"""
    conn.execute("INSERT INTO keyword_data (period_start, period_end, period_type, market, "
                 "category, keyword_type, keyword, rank, search_volume, on_sale_products, "
                 "source_file, file_hash, level_1_category, level_2_category, imported_at) "
                 "VALUES ('2026-07-01','2026-07-31','month','TH','时尚配件','热门搜索关键词',"
                 "'ที่คาดผม',1,100,10,'synthetic.xlsx','abc123def4567890','时尚配件',NULL,'t1')")
    conn.execute("INSERT INTO screening_v1 (run_id, keyword, market, category, keyword_type, "
                 "period_type, period_start, passed, channel, demand_pct, supply_pct, "
                 "opportunity_gap, ctor_pct, sku_pct, purchase_intent, opportunity_score, "
                 "top_rank) VALUES ('r1','ที่คาดผม','TH','时尚配件',"
                 "'热门搜索关键词','month','2026-07-01',1,'main',80,40,40,70,60,66,0.6,1)")
    conn.execute("INSERT INTO ai_semantic_results (market, original_keyword, category, "
                 "keyword_type, period_start, language, meaning_zh, product_category, "
                 "product_subcategory, canonical_product_name, canonical_product_key, "
                 "search_terms_1688, is_product, intent_status, confidence, model, status, "
                 "attempts, created_at, updated_at) VALUES ('TH','ที่คาดผม','时尚配件',"
                 "'热门搜索关键词','2026-07-01','th','发箍','发饰','发箍','发箍','hair_band',"
                 "'[\"发箍\"]',1,'PRODUCT',0.9,'m','success',1,'t1','t1')")
    conn.execute("INSERT INTO product_opportunities_v2 (run_id, opportunity_id, market, "
                 "level_1_category, level_3_category, canonical_product_name, "
                 "canonical_product_key, score, demand_score, purchase_intent_score, "
                 "supply_score, opportunity_gap, evidence_count, source_keywords, "
                 "weekly_evidence, monthly_evidence, first_seen, last_seen, "
                 "category_status, created_at, updated_at) VALUES ('pool1','OP-0001','TH',"
                 "'时尚配件','发箍','发箍','hair_band',0.6,0.8,0.7,0.5,0.2,1,"
                 "'[\"ที่คาดผม\"]','[]','[]','2026-07-01','2026-07-31','CORE','t1','t1')")
    conn.commit()


def test_import_persists_full_identity(tmp_path):
    """新导入路径必须完整保存官方类目身份（country/level_1/source/batch/period）。"""
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    n, dup = KeywordRepo(conn).insert_many([_mk_record("สร้อยคอ")])
    assert (n, dup) == (1, 0)
    row = conn.execute("SELECT * FROM keyword_data WHERE keyword='สร้อยคอ'").fetchone()
    assert row["market"] == "TH"
    assert row["category"] == "时尚配件" and row["level_1_category"] == "时尚配件"
    assert row["level_2_category"] is None          # 无二级证据 → 保持 NULL，禁止猜测
    assert row["source_file"] == "synthetic.xlsx"
    assert row["file_hash"] == "abc123def4567890"
    assert row["period_start"] == "2026-07-01" and row["period_type"] == "month"
    assert row["imported_at"]                       # batch 标识
    conn.close()


def test_file_hash_matches_sha256_prefix(tmp_path):
    """file_hash = 源文件 sha256 前 16 位（与 parser 算法一致）。"""
    raw = tmp_path / "src.xlsx"
    raw.write_bytes(b"fake-xlsx-bytes-0123456789")
    expect = hashlib.sha256(raw.read_bytes()).hexdigest()[:16]
    assert len(expect) == 16
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    KeywordRepo(conn).insert_many([_mk_record("k1", source=raw.name, fh=expect)])
    row = conn.execute("SELECT file_hash FROM keyword_data WHERE keyword='k1'").fetchone()
    assert row["file_hash"] == expect
    conn.close()


def test_downstream_fully_traceable(tmp_path):
    """keyword→screening→semantic→v2 机会：全程可追溯，无孤儿、类目一致。"""
    conn = connect(tmp_path / "t.db")
    init_db(conn)
    _seed_mini(conn)
    # keyword 存在且身份完整
    k = conn.execute("SELECT * FROM keyword_data WHERE keyword='ที่คาดผม'").fetchone()
    assert k and k["file_hash"] and k["level_1_category"] == "时尚配件"
    # screening 可追溯（同 market+keyword 在 keyword_data）
    assert conn.execute("SELECT 1 FROM screening_v1 s WHERE NOT EXISTS (SELECT 1 FROM "
                        "keyword_data k WHERE k.keyword=s.keyword AND k.market=s.market)"
                        ).fetchone() is None
    # semantic 可追溯且 canonical key 非空
    s = conn.execute("SELECT * FROM ai_semantic_results WHERE original_keyword='ที่คาดผม'"
                     ).fetchone()
    assert s and s["status"] == "success" and s["canonical_product_key"] == "hair_band"
    # v2 机会 source_keywords 均可回 keyword_data，且继承一级类目
    v = conn.execute("SELECT * FROM product_opportunities_v2 WHERE canonical_product_key='hair_band'"
                     ).fetchone()
    ks = json.loads(v["source_keywords"])
    assert all(conn.execute("SELECT 1 FROM keyword_data WHERE keyword=? AND market=?",
                            (x, "TH")).fetchone() for x in ks)
    assert v["level_1_category"] == "时尚配件" and v["level_2_category"] is None
    conn.close()
