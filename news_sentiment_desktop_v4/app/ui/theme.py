"""桌面版全域主題（V4.5.0）— 淡色專業風

設計原則：
    - 白底＋深藍主色（公務環境投影、列印截圖對比清晰）
    - 純 QSS ＋ Fusion style，不引入第三方主題套件（少一個依賴、客製自由）
    - 頁面程式碼只做「語意標記」（objectName / property），顏色字級全部集中
      在這裡，之後調整風格只改這一個檔案

頁面端的標記慣例：
    - 頁面大標題：      label.setObjectName("pageTitle")
    - 側欄：            nav_list.setObjectName("navList")；分組列 data 帶 NAV_HEADER
    - 警示文字：        label.setObjectName("alertLabel")
    - 主要動作按鈕：    mark_primary(btn)   → 主色實心
    - 危險動作按鈕：    mark_danger(btn)    → 紅字紅框
"""
from __future__ import annotations

# ---- 色票 ----
PRIMARY = "#1B4F8A"          # 主色：深藍
PRIMARY_HOVER = "#26619F"
PRIMARY_PRESSED = "#143C69"
PRIMARY_TINT = "#E8F0FA"     # 主色淡底（選取列、tab 底）
SIDEBAR_BG = "#16324F"       # 側欄深藍底
SIDEBAR_ITEM_HOVER = "#1F4468"
SIDEBAR_TEXT = "#D7E3F0"
WINDOW_BG = "#F4F6F9"        # 視窗底：淡藍灰
SURFACE = "#FFFFFF"          # 卡片／輸入框底
BORDER = "#D8DEE6"
TEXT = "#1F2933"
TEXT_MUTED = "#6B7280"
SUCCESS = "#2E7D32"
DANGER = "#B71C1C"
DANGER_BG = "#FDECEA"
STRIPE = "#F7F9FC"           # 表格斑馬紋


# 下拉框／數字欄位的三角形箭頭圖示：QSS 蓋掉原生樣式後必須自己提供箭頭圖，
# 純 QSS 的 border 三角形技巧在 Qt 上會畫成色塊（實測），所以改在套用主題時
# 用 QPainter 產生小 PNG 存到資料目錄，QSS 以 url() 引用。
_ICON_PATHS: dict = {}


def _ensure_arrow_icons() -> None:
    """產生上下三角形圖示（需要 QApplication 已建立；失敗時安靜略過，
    只是箭頭不顯示，展開按鈕本身仍在）"""
    if _ICON_PATHS:
        return
    try:
        from PySide6.QtGui import QPixmap, QPainter, QColor, QPolygon
        from PySide6.QtCore import QPoint, Qt as _Qt
        from app.utils.paths import get_app_data_dir
        icon_dir = get_app_data_dir() / "theme"
        icon_dir.mkdir(parents=True, exist_ok=True)
        shapes = {
            "arrow_down": [(1, 3), (9, 3), (5, 8)],
            "arrow_up": [(1, 7), (9, 7), (5, 2)],
        }
        for name, pts in shapes.items():
            path = icon_dir / f"{name}.png"
            pm = QPixmap(10, 10)
            pm.fill(QColor(0, 0, 0, 0))
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(QColor(PRIMARY))
            painter.setPen(_Qt.NoPen)
            painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in pts]))
            painter.end()
            if pm.save(str(path)):
                _ICON_PATHS[name] = path.as_posix()   # QSS url() 用正斜線（含 Windows）
    except Exception:
        pass


def _arrow_rule(selector: str, icon_name: str, size: int) -> str:
    path = _ICON_PATHS.get(icon_name)
    if path:
        return (f'{selector} {{ image: url("{path}"); '
                f'width: {size}px; height: {size}px; }}')
    return f"{selector} {{ width: {size}px; height: {size}px; }}"


def build_stylesheet() -> str:
    return f"""
/* ---- 基底 ---- */
QMainWindow, QDialog {{ background: {WINDOW_BG}; }}
QWidget {{ color: {TEXT}; font-size: 13px; }}
QLabel {{ background: transparent; }}
QToolTip {{
    background: {TEXT}; color: white; border: none; padding: 5px 8px; font-size: 12px;
}}

/* ---- 工具列／狀態列 ---- */
QToolBar {{
    background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
    padding: 4px 8px; spacing: 8px;
}}
QToolBar QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 6px;
    padding: 5px 12px; color: {PRIMARY}; font-weight: bold;
}}
QToolBar QToolButton:hover {{ background: {PRIMARY_TINT}; border-color: {BORDER}; }}
QToolBar QLabel {{ color: {TEXT_MUTED}; padding: 0 6px; }}
QStatusBar {{
    background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_MUTED};
}}

/* ---- 側欄導覽 ---- */
#sidebar {{ background: {SIDEBAR_BG}; }}
#navList {{
    background: {SIDEBAR_BG}; border: none; outline: none; padding: 6px 0;
}}
#navList::item {{
    color: {SIDEBAR_TEXT}; padding: 9px 14px; border: none;
    border-left: 3px solid transparent;
}}
#navList::item:hover {{ background: {SIDEBAR_ITEM_HOVER}; }}
#navList::item:selected {{
    background: {SIDEBAR_ITEM_HOVER}; color: white; font-weight: bold;
    border-left: 3px solid #6EB2FF;
}}
#navList::item:disabled {{
    color: #7C93AB; font-size: 11px; font-weight: bold;
    padding: 12px 12px 4px 12px; background: {SIDEBAR_BG};
}}
#appTitle {{
    background: {SIDEBAR_BG}; color: white; font-size: 15px; font-weight: bold;
    padding: 14px 14px 2px 14px;
}}
#appSubtitle {{
    background: {SIDEBAR_BG}; color: #7C93AB; font-size: 11px; padding: 0 14px 8px 14px;
}}

/* ---- 頁面標題／警示／操作提示 ---- */
#pageTitle {{ font-size: 18px; font-weight: bold; color: {PRIMARY}; padding: 2px 0 6px 0; }}
#alertLabel {{ color: {DANGER}; font-weight: bold; }}
#hintLabel {{ color: {TEXT_MUTED}; font-size: 12px; }}

/* ---- 卡片（QGroupBox） ---- */
QGroupBox {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    margin-top: 10px; padding: 10px 8px 8px 8px; font-weight: bold;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {PRIMARY};
}}

/* ---- 按鈕 ---- */
QPushButton {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 6px 14px; min-height: 16px;
}}
QPushButton:hover {{ border-color: {PRIMARY}; color: {PRIMARY}; }}
QPushButton:pressed {{ background: {PRIMARY_TINT}; }}
QPushButton:disabled {{ color: #9AA5B1; background: #EEF1F5; border-color: {BORDER}; }}
QPushButton[primary="true"] {{
    background: {PRIMARY}; color: white; border: 1px solid {PRIMARY}; font-weight: bold;
}}
QPushButton[primary="true"]:hover {{ background: {PRIMARY_HOVER}; color: white; }}
QPushButton[primary="true"]:pressed {{ background: {PRIMARY_PRESSED}; }}
QPushButton[primary="true"]:disabled {{ background: #A9BFD6; border-color: #A9BFD6; color: white; }}
QPushButton[danger="true"] {{ color: {DANGER}; border-color: #E4B6B2; }}
QPushButton[danger="true"]:hover {{ background: {DANGER_BG}; border-color: {DANGER}; }}

/* ---- 輸入元件 ---- */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox,
QDateTimeEdit, QDateEdit, QTimeEdit {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 4px 8px; selection-background-color: {PRIMARY}; selection-color: white;
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QDateTimeEdit:focus {{
    border: 1px solid {PRIMARY};
}}
QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background: #EEF1F5; color: {TEXT_MUTED};
}}
/* 下拉框右側要有明顯的展開按鈕＋箭頭（QSS 蓋掉原生樣式後必須自己畫，
   否則下拉選單看起來跟文字框一模一樣，使用者不知道可以展開） */
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 26px; border-left: 1px solid {BORDER};
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
    background: #EDF2F8;
}}
QComboBox::drop-down:hover {{ background: {PRIMARY_TINT}; }}
{_arrow_rule("QComboBox::down-arrow", "arrow_down", 10)}
QComboBox QAbstractItemView {{
    background: {SURFACE}; border: 1px solid {BORDER};
    selection-background-color: {PRIMARY_TINT}; selection-color: {TEXT};
}}
/* 數字/日期欄位的上下調整鈕同理，自己畫出按鈕與箭頭 */
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateTimeEdit::up-button,
QDateEdit::up-button, QTimeEdit::up-button {{
    subcontrol-origin: border; subcontrol-position: top right; width: 22px;
    border-left: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    border-top-right-radius: 6px; background: #EDF2F8;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateTimeEdit::down-button,
QDateEdit::down-button, QTimeEdit::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right; width: 22px;
    border-left: 1px solid {BORDER};
    border-bottom-right-radius: 6px; background: #EDF2F8;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QDateTimeEdit::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover, QDateTimeEdit::down-button:hover {{
    background: {PRIMARY_TINT};
}}
{_arrow_rule("QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateTimeEdit::up-arrow, "
              "QDateEdit::up-arrow, QTimeEdit::up-arrow", "arrow_up", 8)}
{_arrow_rule("QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateTimeEdit::down-arrow, "
              "QDateEdit::down-arrow, QTimeEdit::down-arrow", "arrow_down", 8)}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border: 1px solid {BORDER}; border-radius: 4px;
    background: {SURFACE};
}}
QCheckBox::indicator:hover {{ border-color: {PRIMARY}; }}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}

/* ---- 分頁（QTabWidget） ---- */
QTabWidget::pane {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; top: -1px;
}}
QTabBar::tab {{
    background: transparent; border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    padding: 7px 16px; color: {TEXT_MUTED}; margin-right: 2px;
}}
QTabBar::tab:hover {{ color: {PRIMARY}; }}
QTabBar::tab:selected {{
    color: {PRIMARY}; font-weight: bold; border-bottom: 2px solid {PRIMARY};
}}

/* ---- 表格 ---- */
QTableView, QTableWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
    gridline-color: #EDF0F4; alternate-background-color: {STRIPE};
    selection-background-color: {PRIMARY_TINT}; selection-color: {TEXT};
}}
QHeaderView::section {{
    background: #EDF2F8; color: {PRIMARY}; font-weight: bold;
    border: none; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
}}
QTableCornerButton::section {{ background: #EDF2F8; border: none; }}
/* 表格內勾選框加大（留用欄），比 Qt 預設的小方塊好點擊、好辨識 */
QTableView::indicator {{
    width: 18px; height: 18px; border: 1px solid {BORDER}; border-radius: 4px;
    background: {SURFACE};
}}
QTableView::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}

/* ---- 清單 ---- */
QListWidget {{
    background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; outline: none;
}}
QListWidget::item {{ padding: 6px 8px; }}
QListWidget::item:hover {{ background: {STRIPE}; }}
QListWidget::item:selected {{ background: {PRIMARY_TINT}; color: {TEXT}; }}

/* ---- 進度條 ---- */
QProgressBar {{
    background: #E6EAF0; border: none; border-radius: 7px;
    height: 14px; text-align: center; color: {TEXT}; font-size: 11px;
}}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 7px; }}

/* ---- 分割器／卷軸 ---- */
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 2px; }}
QSplitter::handle:vertical {{ height: 2px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C3CBD6; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #9AA8BA; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C3CBD6; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #9AA8BA; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ border: none; background: transparent; }}
/* 捲動區的內容容器維持透明，否則會在白色卡片內露出一塊視窗灰底 */
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def apply_theme(app) -> None:
    """掛上全域主題：Fusion style（跨平台渲染一致、QSS 相容性最好）＋
    微軟正黑體（非 Windows 環境由 Qt 自動 fallback 系統字型）＋ 全域 QSS"""
    from PySide6.QtGui import QFont
    app.setStyle("Fusion")
    font = QFont("Microsoft JhengHei UI", 10)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
    _ensure_arrow_icons()   # 需在 QApplication 建立後、組 QSS 前產生
    app.setStyleSheet(build_stylesheet())


def _repolish(widget) -> None:
    """property 變更後要重新 polish 才會套用對應的 QSS 規則"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def mark_primary(btn) -> None:
    """把按鈕標成「主要動作」（主色實心）——每頁最醒目的那一顆"""
    btn.setProperty("primary", "true")
    _repolish(btn)


def mark_danger(btn) -> None:
    """把按鈕標成「危險動作」（紅字紅框）——取消／清除類"""
    btn.setProperty("danger", "true")
    _repolish(btn)
