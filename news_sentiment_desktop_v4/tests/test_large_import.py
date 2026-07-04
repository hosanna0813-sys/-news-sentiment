"""測試：5,000 則匯入效能與正確性 + 重複 news_id／相同資料列處理（規格五）"""
from __future__ import annotations

import time
import openpyxl
from app.services.importer.excel_importer import import_file
from app.repositories.news_repository import NewsRepository


def _make_large_excel(path, n=5000):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["news_id", "文章標題", "摘要", "來源", "時間", "網址"])
    for i in range(n):
        # 刻意讓每 10 筆有一個重複的 news_id / 標題 / 網址，模擬真實重複資料
        dup_idx = i % 10
        ws.append([
            f"NID-{dup_idx}", f"標題-{dup_idx}", f"摘要-{i}", "測試來源",
            "2026-01-01", f"http://example.com/{dup_idx}",
        ])
    wb.save(str(path))


def test_large_import_5000_rows(tmp_path):
    excel_path = tmp_path / "large.xlsx"
    _make_large_excel(excel_path, n=5000)

    start = time.time()
    result = import_file(str(excel_path))
    elapsed = time.time() - start

    assert result.total_rows == 5000
    # row_id 即使 news_id/標題/網址重複，也必須全部唯一，不可造成回寫錯誤
    row_ids = [it.row_id for it in result.items]
    assert len(row_ids) == len(set(row_ids)) == 5000

    # 匯入應在合理時間內完成（寬鬆門檻，避免 CI 環境效能差異造成誤判）
    assert elapsed < 30, f"匯入 5000 筆耗時過久: {elapsed:.1f}s"


def test_duplicate_news_id_upsert_no_corruption(tmp_db_path, news_repo: NewsRepository):
    """即使多筆新聞的 news_id 完全相同，寫入資料庫也不可互相覆蓋或造成錯誤"""
    excel_path = tmp_db_path.parent / "dup_news_id.xlsx"
    _make_large_excel(excel_path, n=100)
    result = import_file(str(excel_path))

    news_repo.upsert_many(result.items)
    stored = news_repo.list_all()
    assert len(stored) == 100  # 全部保存，未因 news_id 重複而遺失資料

    # 重複群組偵測應正確（每 10 筆中有 10 筆同組，共 10 組）
    groups = news_repo.find_potential_duplicates()
    assert len(groups) == 10
    for members in groups.values():
        assert len(members) == 10
