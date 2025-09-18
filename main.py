import sys
import os
import json
import webbrowser
import requests # type: ignore
from bs4 import BeautifulSoup # type: ignore
from urllib.parse import urlparse, urljoin
import uuid
import time # 디바운싱(Debouncing)을 위해 사용
import subprocess # 데이터 폴더를 열기 위해 사용

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QVBoxLayout, QWidget,
    QPushButton, QLineEdit, QDialog, QInputDialog,
    QDialogButtonBox, QLabel, QSystemTrayIcon, QMenu, QListWidget, QListWidgetItem,
    QMessageBox, QStyle, QTabWidget, QTabBar, QComboBox, QSizePolicy, QListView,
    QHBoxLayout, QMenuBar
)
from PySide6.QtGui import (
    QIcon, QPixmap, QAction, QPainter, QDrag, QMouseEvent, QFocusEvent, QCursor, QFont, QColor,
    QKeyEvent
)
from PySide6.QtCore import Qt, QSize, QMimeData, QPoint, Signal, Slot

try:
    import keyboard # type: ignore
except ImportError:
    print("치명적 오류: 'keyboard' 라이브러리를 찾을 수 없습니다. 'pip install keyboard'를 사용하여 설치해주세요.")
    sys.exit(1)

APP_NAME = "ShortCutGroup"

# --- 수정: 데이터 경로를 루트 작업 디렉토리로 변경 ---
# 스크립트가 위치한 디렉토리 또는 실행 파일이 실행되는 디렉토리를 가져옵니다.
if getattr(sys, 'frozen', False):
    # 번들된 실행 파일(예: PyInstaller)로 실행 중인 경우
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # .py 스크립트로 실행 중인 경우
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 파일들은 기본 디렉토리에 직접 저장됩니다.
SETTINGS_FILE = os.path.join(BASE_DIR, "shortcuts.json")
FAVICON_DIR = os.path.join(BASE_DIR, "favicons")
# --- 수정 종료 ---


DEFAULT_FAVICON_FILENAME = "default_shortcut_icon.png"
HOTKEY_DEBOUNCE_TIME = 0.3 # 초 단위

def get_favicon_path(filename):
    """데이터 디렉토리에 있는 파비콘 파일의 전체 경로를 가져오는 헬퍼 함수입니다."""
    return os.path.join(FAVICON_DIR, filename)

DEFAULT_FAVICON = get_favicon_path(DEFAULT_FAVICON_FILENAME)

ADD_ITEM_IDENTIFIER = "___ADD_NEW_SHORTCUT_ITEM___"
ALL_CATEGORY_NAME = "전체"
ADD_CATEGORY_TAB_TEXT = " + "
MIME_TYPE_SHORTCUT_ID = "application/x-shortcut-id"

def fetch_favicon(url):
    """
    주어진 URL의 파비콘을 가져옵니다.
    먼저 구글의 S2 서비스를 시도하고, 실패 시 HTML을 파싱하는 방식으로 대체합니다.
    아이콘을 FAVICON_DIR에 저장합니다.
    저장된 아이콘의 경로를 반환하며, 가져오기 실패 시 DEFAULT_FAVICON을 반환합니다.
    """
    if not os.path.exists(FAVICON_DIR):
        try:
            os.makedirs(FAVICON_DIR)
        except OSError as e:
            print(f"경고 (fetch_favicon): 파비콘 디렉토리 {FAVICON_DIR} 생성 실패: {e}")
            return None # 디렉토리 생성 실패 시 저장 불가

    parsed_url = urlparse(url)
    current_effective_domain = parsed_url.netloc

    if not current_effective_domain:
        if parsed_url.scheme == 'file': # 로컬 파일은 웹 파비콘이 없습니다.
            return None
        print(f"경고 (fetch_favicon): URL에서 도메인을 파싱할 수 없음: {url}")
        return DEFAULT_FAVICON if os.path.exists(DEFAULT_FAVICON) else None

    # 도메인으로부터 안전한 파일명 기반을 생성합니다.
    current_safe_filename_base = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in current_effective_domain)
    original_domain_for_s2 = current_effective_domain # S2 서비스를 위해 원본 도메인 유지

    # 흔한 확장자로 아이콘이 이미 존재하는지 확인합니다.
    for ext in ['.png', '.ico', '.jpg', '.jpeg', '.gif', '.svg']:
        potential_path = get_favicon_path(f"{current_safe_filename_base}{ext}")
        if os.path.exists(potential_path):
            return potential_path

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    # 1. 구글 S2 파비콘 서비스 시도
    if original_domain_for_s2: # 도메인이 있을 경우에만 시도
        try:
            google_s2_url = f"https://www.google.com/s2/favicons?sz=64&domain_url={original_domain_for_s2}"
            s2_response = requests.get(google_s2_url, headers=headers, timeout=5, stream=True)
            if s2_response.status_code == 200 and 'image' in s2_response.headers.get('content-type', '').lower():
                s2_favicon_path = get_favicon_path(f"{current_safe_filename_base}.png") # S2는 png로 가정
                with open(s2_favicon_path, 'wb') as f:
                    for chunk in s2_response.iter_content(8192):
                        f.write(chunk)
                if os.path.getsize(s2_favicon_path) > 100: # 비어있거나 오류 이미지가 아닌지 기본 검사
                    return s2_favicon_path
                else: # S2가 매우 작은 (오류일 가능성이 높은) 이미지를 반환했으므로 삭제
                    try: os.remove(s2_favicon_path)
                    except OSError: pass # 삭제 실패 시 무시
        except Exception as e:
            print(f"경고 (fetch_favicon): {original_domain_for_s2}에 대한 구글 S2 오류: {e}")

    # 2. 웹사이트 자체에서 직접 가져오기로 대체
    try:
        temp_url = url
        # 웹 URL에 스킴(http/https)이 없으면 추가
        if not parsed_url.scheme and not url.lower().startswith("file:"):
            temp_url = "http://" + url # http 먼저 시도

        if urlparse(temp_url).scheme == 'file': # 로컬 파일은 웹 파비콘 없음
            return DEFAULT_FAVICON if os.path.exists(DEFAULT_FAVICON) else None

        response = requests.get(temp_url, headers=headers, timeout=7, allow_redirects=True)
        response.raise_for_status() # 잘못된 응답(4xx 또는 5xx)에 대해 HTTPError 발생

        # 다른 도메인으로 리디렉션된 경우 도메인 업데이트
        final_url_details = urlparse(response.url)
        if final_url_details.netloc and final_url_details.netloc != current_effective_domain:
            current_effective_domain = final_url_details.netloc
            current_safe_filename_base = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in current_effective_domain)
            # 새 도메인에 대해 아이콘이 이미 존재하는지 다시 확인
            for ext in ['.png', '.ico', '.jpg', '.jpeg', '.gif', '.svg']:
                potential_path = get_favicon_path(f"{current_safe_filename_base}{ext}")
                if os.path.exists(potential_path):
                    return potential_path

        soup = BeautifulSoup(response.content, 'html.parser')
        icon_url_from_html = None

        # <link rel="icon" ...> 태그 검색
        for rel_value in ['icon', 'shortcut icon', 'apple-touch-icon', 'apple-touch-icon-precomposed']:
            for tag in soup.find_all('link', rel=rel_value, href=True):
                href = tag.get('href')
                if href and not href.startswith('data:'): # 데이터 URI는 무시
                    icon_url_from_html = urljoin(response.url, href)
                    break
            if icon_url_from_html:
                break

        final_icon_url_to_fetch = icon_url_from_html

        # <link> 태그를 찾지 못했다면 /favicon.ico 시도
        if not final_icon_url_to_fetch:
            fallback_ico_url = urljoin(response.url, '/favicon.ico')
            try: # 다운로드 전에 /favicon.ico가 존재하는지 확인
                if requests.head(fallback_ico_url, headers=headers, timeout=2, allow_redirects=True).status_code == 200:
                    final_icon_url_to_fetch = fallback_ico_url
            except requests.RequestException:
                pass # /favicon.ico가 존재하지 않거나 확인 중 오류 발생

        if final_icon_url_to_fetch:
            icon_response = requests.get(final_icon_url_to_fetch, headers=headers, timeout=5, stream=True)
            icon_response.raise_for_status()

            content_type = icon_response.headers.get('content-type', '').lower()
            file_ext = '.ico' # 기본 확장자
            if 'png' in content_type: file_ext = '.png'
            elif 'jpeg' in content_type or 'jpg' in content_type: file_ext = '.jpg'
            elif 'gif' in content_type: file_ext = '.gif'
            elif 'svg' in content_type: file_ext = '.svg'
            # 필요 시 다른 타입 추가

            favicon_path = get_favicon_path(f"{current_safe_filename_base}{file_ext}")
            with open(favicon_path, 'wb') as f:
                for chunk in icon_response.iter_content(8192): # 스트림 다운로드
                    f.write(chunk)
            return favicon_path

    except requests.exceptions.SSLError: # http 대체 실행을 위해 SSL 오류를 특정하여 처리
        if url.startswith("https://"): # 원본이 https였다면 http로 시도
            print(f"경고 (fetch_favicon): {url}에서 SSL 오류 발생, http로 재시도합니다.")
            return fetch_favicon(url.replace("https://", "http://", 1))
    except Exception as e:
        print(f"경고 (fetch_favicon): {url}에 대한 메인/아이콘 요청 실패: {e}")

    return DEFAULT_FAVICON if os.path.exists(DEFAULT_FAVICON) else None


def load_icon_pixmap(icon_path, icon_size: QSize):
    """경로에서 아이콘을 로드하여 icon_size로 스케일링된 QIcon을 반환합니다."""
    if not icon_path : return QIcon() # 경로가 없으면 빈 QIcon 반환

    # 경로가 절대 경로인지 FAVICON_DIR에 대한 상대 경로인지 결정
    final_path = icon_path
    if not os.path.isabs(icon_path):
        final_path = get_favicon_path(os.path.basename(icon_path))

    if os.path.exists(final_path):
        px = QPixmap(final_path)
        if not px.isNull():
            return QIcon(px.scaled(icon_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    return QIcon() # 로딩 실패 또는 경로가 존재하지 않으면 빈 QIcon 반환

class ShortcutDialog(QDialog):
    """바로가기 추가 또는 편집을 위한 대화상자입니다."""
    def __init__(self, parent=None, shortcut_data=None, categories=None):
        super().__init__(parent)
        self.setWindowTitle("바로가기 추가" if not shortcut_data else "바로가기 편집")
        self.setMinimumWidth(400)
        self.existing_categories = categories if categories else []

        self.layout = QVBoxLayout(self)

        # 이름 입력
        self.name_label = QLabel("이름 (선택 사항):")
        self.name_input = QLineEdit()
        self.layout.addWidget(self.name_label)
        self.layout.addWidget(self.name_input)

        # URL 입력
        self.url_label = QLabel("웹사이트 주소 (URL, 필수):")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com 또는 file:///C:/path/to/file.txt")
        self.layout.addWidget(self.url_label)
        self.layout.addWidget(self.url_input)

        # 단축키 입력
        self.hotkey_label = QLabel("단축키 (선택 사항):")
        self.hotkey_input = HotkeyInputLineEdit(self) # 커스텀 HotkeyInputLineEdit 사용
        self.hotkey_input.setPlaceholderText("예: ctrl+shift+1 (영문 기준)")
        self.layout.addWidget(self.hotkey_label)
        self.layout.addWidget(self.hotkey_input)

        # 카테고리 선택
        self.category_label = QLabel("카테고리:")
        self.category_combo = QComboBox()
        if not self.existing_categories:
            self.category_combo.addItem("일반")
        else:
            self.category_combo.addItems(self.existing_categories)
        self.layout.addWidget(self.category_label)
        self.layout.addWidget(self.category_combo)


        # 대화상자 버튼
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.try_accept) # 커스텀 유효성 검사에 연결
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

        # 편집 시 필드 채우기
        if shortcut_data:
            self.name_input.setText(shortcut_data.get("name", ""))
            self.url_input.setText(shortcut_data.get("url", ""))
            self.hotkey_input.set_hotkey_string(shortcut_data.get("hotkey", "")) # 전용 setter 사용
            current_category = shortcut_data.get("category", "일반")
            if self.category_combo.findText(current_category) != -1:
                self.category_combo.setCurrentText(current_category)
            elif self.existing_categories : # 현재 카테고리를 찾을 수 없으면 첫 번째 사용 가능한 카테고리 선택
                self.category_combo.setCurrentIndex(0)


    def try_accept(self):
        """대화상자를 수락하기 전에 URL의 유효성을 검사합니다."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "입력 오류", "웹사이트 주소(URL) 또는 파일 경로를 입력해주세요.")
            self.url_input.setFocus()
            return

        parsed_url = urlparse(url)
        is_valid_scheme = parsed_url.scheme in ["http", "https", "file"]
        has_netloc_for_web = bool(parsed_url.netloc) and parsed_url.scheme in ["http", "https"]
        has_path_for_file = bool(parsed_url.path) and parsed_url.scheme == "file"
        # "example.com"과 같이 스킴이 없는 도메인 형태 허용
        is_schemeless_domain_like = not parsed_url.scheme and "." in url and not "/" in url.split("?")[0].split("#")[0]


        if not (is_valid_scheme and (has_netloc_for_web or has_path_for_file)) and not is_schemeless_domain_like:
            # 스킴 없는 "localhost:8000" 또는 "domain.com/path"와 같은 경우에 대한 더 관대한 검사
            if not parsed_url.scheme and ('.' in url and ('/' in url or '.' in parsed_url.path) or "localhost" in url.lower() or (url.count(':') == 1 and url.split(':')[1].isdigit() and not parsed_url.scheme)):
                pass # 유효한 로컬 또는 스킴 없는 URL일 가능성이 높음
            else:
                QMessageBox.warning(self, "입력 오류", "유효한 웹 주소 또는 파일 경로 형식이 아닙니다.")
                self.url_input.setFocus()
                return
        self.accept()


    def get_data(self):
        """대화상자 입력으로부터 바로가기 데이터를 반환합니다."""
        name = self.name_input.text().strip()
        url = self.url_input.text().strip()
        hotkey = self.hotkey_input.get_hotkey_string() # 전용 getter 사용
        category = self.category_combo.currentText()

        # 스킴이 없고 파일 경로가 아닌 경우 URL에 http:// 자동 접두사 추가
        parsed_url_check = urlparse(url)
        if not parsed_url_check.scheme and not url.lower().startswith("file:"):
            # 사용자가 //domain.com을 입력한 경우 이중 http:// 방지
            if not (url.startswith("//") or url.startswith("http://") or url.startswith("https://")):
                url = "http://" + url
        elif url.lower().startswith("file:"): # 파일 경로 정규화
            if url.lower().startswith("file://") and not url.lower().startswith("file:///"):
                url = "file:///" + url[len("file://"):] # 로컬 파일에 슬래시가 두 개만 있으면 세 번째 슬래시 추가
            else: # "file:path" 처리
                url = "file:///" + url[len("file:"):]
            url = url.replace("\\", "/") # 순방향 슬래시 사용

        parsed_url = urlparse(url) # 잠재적 수정 후 다시 파싱

        # 이름이 제공되지 않은 경우 자동 생성
        if not name:
            if parsed_url.scheme == "file":
                name = os.path.basename(parsed_url.path) or "파일 바로가기"
            else: # http/https의 경우
                name = parsed_url.netloc or os.path.basename(parsed_url.path) or "이름 없는 바로가기"
            if not name or name.lower() in ["http:", "https:", "file:"]: # 이름이 스킴만 있는 경우 추가 대체
                name = os.path.basename(parsed_url.path) if parsed_url.path else "이름 없는 바로가기"

        return {"name": name, "url": url, "hotkey": hotkey, "category": category}

class DraggableListWidget(QListWidget):
    """항목의 순서 변경을 위해 드래그 앤 드롭을 지원하는 QListWidget입니다."""
    item_dropped_signal = Signal(str, int, object) # item_id, new_row, list_widget_instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove) # 내부 순서 변경 허용

    def startDrag(self, supportedActions: Qt.DropAction):
        """선택된 항목에 대한 드래그 작업을 시작합니다."""
        selected_items = self.selectedItems()
        if not selected_items:
            return

        item_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
        # "새 바로가기 추가" 항목 드래그 방지
        if isinstance(item_data, dict) and item_data.get("type") == ADD_ITEM_IDENTIFIER:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        item_id = item_data.get("id") # item_data가 'id'를 가진 dict라고 가정

        if item_id:
            mime_data.setData(MIME_TYPE_SHORTCUT_ID, item_id.encode()) # 항목 ID 저장
            drag.setMimeData(mime_data)

            # 드래그 객체에 대한 픽스맵 설정 (예: 항목의 아이콘)
            pixmap = selected_items[0].icon().pixmap(self.iconSize())
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

            drag.exec(supportedActions, Qt.DropAction.MoveAction)

    def dropEvent(self, event: QMouseEvent):
        """항목 순서를 변경하기 위해 드롭 이벤트를 처리합니다."""
        if not event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
            event.ignore()
            return

        source_item_id = event.mimeData().data(MIME_TYPE_SHORTCUT_ID).data().decode()

        if event.source() == self: # 내부 이동
            super().dropEvent(event) # QListWidget이 이동을 처리하도록 함
            if event.isAccepted() and event.dropAction() == Qt.DropAction.MoveAction :
                # 드롭된 항목의 새 행 찾기
                new_row = -1
                for i in range(self.count()):
                    item = self.item(i)
                    item_data = item.data(Qt.ItemDataRole.UserRole)
                    if isinstance(item_data, dict) and item_data.get("id") == source_item_id:
                        new_row = i
                        break
                if new_row != -1:
                    self.item_dropped_signal.emit(source_item_id, new_row, self)
            else:
                event.ignore()
        else:
            event.ignore() # 내부 이동이 아님


class HotkeyInputLineEdit(QLineEdit):
    """키보드 단축키를 캡처하고 표시하는 데 특화된 QLineEdit입니다."""
    # Qt.Key 열거형 값을 'keyboard' 라이브러리의 문자열 표현으로 매핑
    QT_KEY_TO_STR_MAP = {
        Qt.Key.Key_Control: 'ctrl', Qt.Key.Key_Shift: 'shift', Qt.Key.Key_Alt: 'alt', Qt.Key.Key_Meta: 'win', # Windows/Super 키
        Qt.Key.Key_Return: 'enter', Qt.Key.Key_Enter: 'enter', Qt.Key.Key_Escape: 'esc', Qt.Key.Key_Space: 'space',
        Qt.Key.Key_Tab: 'tab', Qt.Key.Key_Backspace: 'backspace', Qt.Key.Key_Delete: 'delete',
        Qt.Key.Key_Up: 'up', Qt.Key.Key_Down: 'down', Qt.Key.Key_Left: 'left', Qt.Key.Key_Right: 'right',
        Qt.Key.Key_Home: 'home', Qt.Key.Key_End: 'end', Qt.Key.Key_PageUp: 'pageup', Qt.Key.Key_PageDown: 'pagedown',
        Qt.Key.Key_Insert: 'insert', Qt.Key.Key_CapsLock: 'caps lock', Qt.Key.Key_ScrollLock: 'scroll lock',
        Qt.Key.Key_NumLock: 'num lock', Qt.Key.Key_Print: 'print screen', Qt.Key.Key_Pause: 'pause',
        # 구두점 및 기호 - 'keyboard' 라이브러리 기대치와 다를 수 있음
        Qt.Key.Key_Plus: '+', Qt.Key.Key_Minus: '-', Qt.Key.Key_Equal: '=',
        Qt.Key.Key_BracketLeft: '[', Qt.Key.Key_BracketRight: ']', Qt.Key.Key_Backslash: '\\',
        Qt.Key.Key_Semicolon: ';', Qt.Key.Key_Apostrophe: '\'', Qt.Key.Key_Comma: ',',
        Qt.Key.Key_Period: '.', Qt.Key.Key_Slash: '/', Qt.Key.Key_QuoteLeft: '`', # Grave accent
    }
    # F1-F24 키 추가
    for i in range(1, 25):
        QT_KEY_TO_STR_MAP[getattr(Qt.Key, f'Key_F{i}')] = f'f{i}'


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True) # 수동 텍스트 편집 방지
        self.setPlaceholderText("여기를 클릭하고 키를 누르세요 (예: Ctrl+Shift+X)")
        self._current_modifier_keys = set() # 활성 조합 키의 Qt.Key 값을 저장
        self._current_non_modifier_key = None # 주 키의 Qt.Key 값을 저장
        self._current_non_modifier_key_str = None # 주 키의 문자열 표현을 저장

    def keyPressEvent(self, event: QKeyEvent):
        """단축키 조합을 캡처하기 위해 키 누름 이벤트를 처리합니다."""
        key = event.key()
        text = event.text() # 문자 키의 경우

        if key == Qt.Key.Key_unknown:
            event.ignore()
            return

        # 조합 키
        if key in [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta]:
            self._current_modifier_keys.add(key)
        # 지우기 키
        elif key == Qt.Key.Key_Backspace or key == Qt.Key.Key_Delete:
            self.clear_hotkey()
            event.accept()
            return
        # 조합 키가 아닌 키
        else:
            self._current_non_modifier_key = key
            self._current_non_modifier_key_str = self._qt_key_to_display_string(key, text)

        self._update_display_text()
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        """주로 조합 키에 대한 키 떼기 이벤트를 처리합니다."""
        key = event.key()

        if event.isAutoRepeat(): # 자동 반복 떼기 이벤트는 무시
            event.accept()
            return

        if key in [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta]:
            if key in self._current_modifier_keys:
                self._current_modifier_keys.remove(key)
        elif key == self._current_non_modifier_key:
            # 조합 키가 눌려있지 않으면, 주 키는 새 키를 캡처할 목적으로 "떼어진" 것으로 간주됩니다.
            # 조합 키가 여전히 눌려 있다면, 주 키를 유지합니다.
            pass # 아직 _current_non_modifier_key를 지우지 않고, 포커스 아웃 또는 명시적 지우기를 기다립니다.

        # 모든 키가 떼어진 경우 (조합 키 없고 주 키 문자열도 없음), 디스플레이를 지웁니다.
        if not self._current_modifier_keys and not self._current_non_modifier_key_str:
            self.clear_hotkey() # 또는 백스페이스/딜리트로 명시적으로 지운 경우

        # self._update_display_text() # 지우는 경우 외에는 떼기 시 디스플레이 업데이트 필요 없음
        event.accept()

    def _qt_key_to_display_string(self, qt_key_code, text_from_event):
        """Qt.Key 코드를 디스플레이 및 'keyboard' 라이브러리에 적합한 문자열로 변환합니다."""
        # 먼저 명시적 맵 확인
        if qt_key_code in self.QT_KEY_TO_STR_MAP:
            return self.QT_KEY_TO_STR_MAP[qt_key_code]

        # 알파벳 키 (A-Z)
        if Qt.Key.Key_A <= qt_key_code <= Qt.Key.Key_Z:
            return chr(qt_key_code).lower() # 소문자 'a'-'z' 사용

        # 숫자 키 (상단 행 0-9)
        if Qt.Key.Key_0 <= qt_key_code <= Qt.Key.Key_9:
            return chr(qt_key_code)

        # 다른 키의 경우, text()가 공백이 아닌 단일 문자를 반환하면 그것을 사용합니다.
        # 이는 국제 키보드나 QT_KEY_TO_STR_MAP에 없는 기호에 도움이 됩니다.
        if text_from_event and len(text_from_event) == 1 and not text_from_event.isspace():
            # text()가 모호할 수 있는 숫자패드 키에 대한 특정 처리
            if qt_key_code == Qt.Key.Key_Asterisk: return "*" # 숫자패드 곱하기
            if qt_key_code == Qt.Key.Key_Plus and text_from_event == "+": return "+" # 숫자패드 더하기
            if qt_key_code == Qt.Key.Key_Minus and text_from_event == "-": return "-" # 숫자패드 빼기
            if qt_key_code == Qt.Key.Key_Period and text_from_event == ".": return "." # 숫자패드 소수점
            if qt_key_code == Qt.Key.Key_Slash and text_from_event == "/": return "/" # 숫자패드 나누기
            return text_from_event.lower() # 가능한 경우 text()로 대체

        return None # 이 키에 대한 문자열을 결정할 수 없음

    def _update_display_text(self):
        """QLineEdit 텍스트를 업데이트하여 현재 단축키 조합을 표시합니다."""
        parts = []
        # 일관된 순서(Ctrl, Alt, Shift, Meta/Win)로 조합 키 추가
        if Qt.Key.Key_Control in self._current_modifier_keys:
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Control])
        if Qt.Key.Key_Alt in self._current_modifier_keys:
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Alt])
        if Qt.Key.Key_Shift in self._current_modifier_keys:
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Shift])
        if Qt.Key.Key_Meta in self._current_modifier_keys: # Windows/Super 키
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Meta])

        if self._current_non_modifier_key_str:
            parts.append(self._current_non_modifier_key_str)

        self.setText(" + ".join(parts) if parts else "")

    def get_hotkey_string(self):
        """캡처된 단축키를 문자열로 반환합니다 (예: "ctrl+shift+a")."""
        return self.text().replace(" ", "") # 'keyboard' 라이브러리 형식을 위해 공백 제거

    def set_hotkey_string(self, hotkey_str):
        """문자열로부터 단축키를 설정합니다 (예: "ctrl+shift+a")."""
        self.clear_hotkey() # 현재 내부 상태 초기화
        if not hotkey_str:
            self.setText("")
            return

        parts = hotkey_str.lower().split('+')
        processed_parts_for_display = []

        for part_raw in parts:
            part = part_raw.strip()
            found_modifier = False
            # 부분이 알려진 조합 키 문자열인지 확인
            for qt_key, str_val in self.QT_KEY_TO_STR_MAP.items():
                if str_val == part and qt_key in [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta]:
                    self._current_modifier_keys.add(qt_key)
                    processed_parts_for_display.append(str_val) # 표시 목록에 추가
                    found_modifier = True
                    break
            if not found_modifier:
                self._current_non_modifier_key_str = part # 주 키라고 가정
                # Qt.Key를 찾아보려고 시도 (선택 사항, 나중에 필요할 경우 내부 일관성을 위해)
                for qt_key, str_val in self.QT_KEY_TO_STR_MAP.items():
                    if str_val == part: self._current_non_modifier_key = qt_key; break
                if not self._current_non_modifier_key: # 글자/숫자 시도
                    if len(part) == 1:
                        if 'a' <= part <= 'z': self._current_non_modifier_key = Qt.Key(ord(part.upper()))
                        elif '0' <= part <= '9': self._current_non_modifier_key = Qt.Key(ord(part))
                processed_parts_for_display.append(part)

        # 표시를 위해 일관된 순서 보장 (Ctrl, Alt, Shift, Meta, Key)
        display_order = []
        temp_modifiers = set(self._current_modifier_keys) # 복사본으로 작업

        if Qt.Key.Key_Control in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Control]); temp_modifiers.remove(Qt.Key.Key_Control)
        if Qt.Key.Key_Alt in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Alt]); temp_modifiers.remove(Qt.Key.Key_Alt)
        if Qt.Key.Key_Shift in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Shift]); temp_modifiers.remove(Qt.Key.Key_Shift)
        if Qt.Key.Key_Meta in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Meta]); temp_modifiers.remove(Qt.Key.Key_Meta)
        if self._current_non_modifier_key_str: display_order.append(self._current_non_modifier_key_str)

        self.setText(" + ".join(display_order))


    def clear_hotkey(self):
        """현재 단축키를 지웁니다."""
        self._current_modifier_keys.clear()
        self._current_non_modifier_key = None
        self._current_non_modifier_key_str = None
        self.setText("")

    def focusOutEvent(self, event: QFocusEvent):
        """
        포커스를 잃었을 때, 조합 키와 함께 주 키가 눌리지 않았다면
        조합 키를 지웁니다. 이는 Ctrl만 누르고 포커스를 잃었을 때 "Ctrl+"가
        남는 것을 방지합니다.
        """
        # if not self._current_non_modifier_key_str and self._current_modifier_keys:
        #     self.clear_hotkey() # 조합 키만 누르고 포커스를 잃으면 지움
        super().focusOutEvent(event) # 기본 클래스 메서드 호출

class GlobalHotkeySettingsDialog(QDialog):
    """전역 '창 보이기/숨기기' 단축키 설정을 위한 대화상자입니다."""
    def __init__(self, parent, current_hotkey):
        super().__init__(parent)
        self.main_window = parent # 메인 윈도우의 바로가기 확인을 위해 참조 저장
        self.setWindowTitle("전역 단축키 설정")
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        self.info_label = QLabel(f"현재 '창 보이기/숨기기' 단축키: <b>{current_hotkey or '설정 안됨'}</b>")
        layout.addWidget(self.info_label)

        input_label = QLabel("새 단축키 (아래 칸에 키를 직접 누르세요):")
        layout.addWidget(input_label)
        self.hotkey_input_widget = HotkeyInputLineEdit(self) # 특화된 위젯 사용
        self.hotkey_input_widget.set_hotkey_string(current_hotkey) # 현재 값으로 미리 채움
        layout.addWidget(self.hotkey_input_widget)

        # 버튼 레이아웃
        button_layout = QHBoxLayout()
        self.default_button = QPushButton("기본값으로")
        self.default_button.clicked.connect(self.set_to_default)
        button_layout.addWidget(self.default_button)
        button_layout.addStretch()

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.try_save)
        self.button_box.rejected.connect(self.reject)
        button_layout.addWidget(self.button_box)

        layout.addLayout(button_layout)

    def set_to_default(self):
        """단축키 입력을 기본값으로 설정합니다."""
        self.hotkey_input_widget.set_hotkey_string("ctrl+shift+x") # 기본 단축키

    def try_save(self):
        """새 전역 단축키의 유효성을 검사하고 저장합니다."""
        new_hotkey_str_raw = self.hotkey_input_widget.get_hotkey_string() # 특화된 위젯에서 가져오기

        if not new_hotkey_str_raw: # 지워진 경우 (빈 문자열)
            reply = QMessageBox.question(self, "단축키 없음",
                                         "단축키를 비우시겠습니까? (창 보이기/숨기기 기능을 사용하지 않음)",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.new_hotkey = "" # 단축키 없음을 나타내기 위해 빈 문자열로 설정
                self.accept()
            else:
                return # 사용자가 지우기를 취소함
        else:
            self.new_hotkey = new_hotkey_str_raw
            # 메인 윈도우의 기존 항목 바로가기와 충돌 확인
            for sc_data in self.main_window.shortcuts: # main_window의 바로가기 접근
                if sc_data.get("hotkey") == self.new_hotkey:
                    QMessageBox.warning(self, "단축키 충돌",
                                        f"단축키 '{self.new_hotkey}'은(는) '{sc_data.get('name')}' 바로가기에서 이미 사용 중입니다.\n다른 단축키를 지정해주세요.")
                    return
            self.accept() # 충돌 없거나 사용자가 지우기 확인

    def get_new_hotkey(self):
        """새 단축키 문자열을 반환하며, 저장 전 대화상자가 취소되면 None을 반환합니다."""
        return getattr(self, 'new_hotkey', None)


class ShortcutManagerWindow(QMainWindow):
    """바로가기 관리를 위한 메인 애플리케이션 창입니다."""
    # 'keyboard' 라이브러리 스레드로부터 스레드 안전한 GUI 업데이트를 위한 시그널
    request_toggle_window_visibility_signal = Signal()
    request_always_show_window_signal = Signal()


    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setGeometry(200, 200, 800, 600) # 기본 크기 및 위치

        self.shortcuts: list[dict] = [] # 바로가기 데이터 딕셔너리 목록
        self.categories_order: list[str] = [] # 탭을 위한 카테고리 순서
        self.hotkey_actions: dict = {} # 등록된 항목 단축키 저장 {hotkey_str: callback}

        self._highlighted_tab_index = -1 # 드래그 오버 탭 하이라이트를 위함
        self._default_tab_stylesheet = "" # 기본 스타일시트 저장
        self.last_selected_valid_category_index = 0 # 마지막으로 사용자가 선택한 카테고리 탭 추적
        self._category_to_select_after_update = None # UI 업데이트 후 선택할 카테고리 임시 저장

        self.global_show_window_hotkey_str = "ctrl+shift+x" # 기본 전역 단축키
        self.is_global_show_hotkey_registered = False
        self._last_global_hotkey_time = 0 # 전역 단축키 디바운싱을 위한 타임스탬프

        self._init_default_icon()
        self.init_ui_layout()
        self.create_menus()
        self.load_data_and_register_hotkeys() # 이 과정에서 전역 단축키도 등록됩니다.
        self.init_tray_icon()
        self.setWindowIcon(self.create_app_icon())
        self.setAcceptDrops(True) # 카테고리 간 바로가기 드래그를 위함

        # 스레드 간 통신을 위한 시그널 연결
        self.request_toggle_window_visibility_signal.connect(self._execute_toggle_window_visibility_gui_thread)
        self.request_always_show_window_signal.connect(self._execute_always_show_window_gui_thread)


    def _init_default_icon(self):
        """
        기본 애플리케이션/바로가기 아이콘이 없는 경우 초기화하거나 생성합니다.
        Pillow (PIL)을 사용하여 간단한 아이콘을 만듭니다.
        """
        self.default_icon_available = os.path.exists(DEFAULT_FAVICON)
        if not self.default_icon_available:
            if not os.path.exists(FAVICON_DIR): # 저장 시도 전에 favicons 디렉토리 존재 확인
                # 시작 시 생성되었어야 하지만, 재확인
                try: os.makedirs(FAVICON_DIR, exist_ok=True)
                except OSError: return # 디렉토리 생성 실패 시 진행 불가

            try:
                from PIL import Image, ImageDraw, ImageFont # type: ignore
                s=48;img=Image.new('RGBA',(s,s),(0,0,0,0));d=ImageDraw.Draw(img)
                d.ellipse((2,2,s-3,s-3),fill='dodgerblue',outline='white',width=1)
                try:font=ImageFont.truetype("arial.ttf",int(s*0.5))
                except IOError:font=ImageFont.load_default() # 대체 폰트
                txt="S";
                # 더 나은 중앙 정렬을 위한 PIL textbbox
                if hasattr(d,'textbbox'):
                    bb=d.textbbox((0,0),txt,font=font); w,h=bb[2]-bb[0],bb[3]-bb[1]
                    x=(s-w)/2-bb[0]; y=(s-h)/2-bb[1]
                else: # 구버전 Pillow를 위한 대체
                    text_size_result=d.textsize(txt,font=font) if hasattr(d,'textsize') else (font.getsize(txt) if hasattr(font,'getsize') else (0,0))
                    w,h=text_size_result[0],text_size_result[1]
                    x,y=(s-w)/2,(s-h)/2
                d.text((x,y),txt,fill="white",font=font)
                img.save(DEFAULT_FAVICON)
                self.default_icon_available=True
            except ImportError:
                print("정보: Pillow 라이브러리를 찾을 수 없습니다. 기본 아이콘을 생성할 수 없습니다. 'pip install Pillow'로 설치해주세요.")
            except Exception as e:
                print(f"경고: 기본 아이콘 생성 중 오류 발생: {e}")

    def create_app_icon(self):
        """메인 애플리케이션 아이콘을 생성하며, 가능한 경우 기본 아이콘을 사용합니다."""
        if self.default_icon_available and os.path.exists(DEFAULT_FAVICON):
            ico = QIcon(DEFAULT_FAVICON)
            if not ico.isNull(): return ico
        # 표준 시스템 아이콘 또는 일반 픽스맵으로 대체
        std_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_ApplicationIcon) # 또는 SP_ComputerIcon, SP_DriveHDIcon
        if not std_ico.isNull(): return std_ico
        # 최후의 수단: 간단한 색상의 픽스맵
        px = QPixmap(32,32); px.fill(Qt.GlobalColor.cyan); return QIcon(px)

    def get_fallback_qicon(self, target_size: QSize) -> QIcon:
        """
        target_size로 스케일링된 대체 QIcon(기본 앱 아이콘 또는 플레이스홀더)을 반환합니다.
        """
        if self.default_icon_available and os.path.exists(DEFAULT_FAVICON):
            px_def = QPixmap(DEFAULT_FAVICON)
            if not px_def.isNull():
                return QIcon(px_def.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        # 스타일에서 표준 파일 아이콘 시도
        px_std = self.style().standardPixmap(QStyle.StandardPixmap.SP_FileIcon, None, self) # 또는 SP_DesktopIcon
        if not px_std.isNull():
            return QIcon(px_std.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        # 최후의 수단: 간단한 플레이스홀더 그리기 (예: X자)
        px = QPixmap(target_size)
        px.fill(Qt.GlobalColor.lightGray)
        p = QPainter(px)
        pen = p.pen()
        pen.setColor(QColor(Qt.GlobalColor.darkGray))
        pen.setWidth(2)
        p.setPen(pen)
        # 간단한 'X' 또는 유사한 플레이스홀더 그리기
        p.drawLine(int(px.width()*0.2), int(px.height()*0.2), int(px.width()*0.8), int(px.height()*0.8))
        p.drawLine(int(px.width()*0.8), int(px.height()*0.2), int(px.width()*0.2), int(px.height()*0.8))
        p.end()
        return QIcon(px)


    def init_ui_layout(self):
        """카테고리 탭이 있는 메인 UI 레이아웃을 초기화합니다."""
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(5, 5, 5, 5) # 작은 여백

        self.category_tabs = QTabWidget()
        tab_bar_font = QFont()
        tab_bar_font.setPointSize(10) # 필요 시 탭 폰트 크기 조절
        self.category_tabs.setFont(tab_bar_font)
        self.category_tabs.tabBar().setMinimumHeight(30) # 탭 바가 충분히 높도록 보장

        # 하이라이트 후 쉬운 리셋을 위해 기본 스타일시트 저장
        self._default_tab_stylesheet = """
            QTabBar::tab {
                min-width: 80px; /* 각 탭의 최소 너비 */
                padding: 5px;
                margin-right: 1px; /* 탭 사이의 작은 간격 */
                border: 1px solid #C4C4C3; /* 밝은 회색 테두리 */
                border-bottom: none; /* 비활성 탭의 하단 테두리 없음 */
                background-color: #F0F0F0; /* 밝은 회색 배경 */
            }
            QTabBar::tab:hover {
                background-color: #E0E0E0; /* 마우스 올리면 약간 어둡게 */
            }
            QTabBar::tab:selected {
                background-color: white; /* 선택된 탭은 흰색 배경 */
                border-color: #9B9B9B; /* 선택된 탭은 더 어두운 테두리 */
                border-bottom: 2px solid white; /* 창 테두리와 겹치게 */
            }
            QTabBar::tab:last { /* '+' (카테고리 추가) 탭을 위한 특별 스타일 */
                background-color: #D8D8D8;
                font-weight: bold;
                color: #333;
                border-left: 1px solid #C4C4C3; /* 구분선 */
            }
            QTabBar::tab:last:hover {
                background-color: #cceeff; /* '+'에 마우스 올리면 밝은 파란색 */
            }
            QTabWidget::pane { /* 탭 위젯의 내용 영역 */
                border: 1px solid #C4C4C3;
                top: -1px; /* 탭 바 하단 테두리와 겹치게 */
                background: white;
            }
        """
        self.category_tabs.setStyleSheet(self._default_tab_stylesheet)

        self.category_tabs.setMovable(True) # 탭 순서 변경 허용
        self.category_tabs.tabBar().tabMoved.connect(self.on_tab_moved)
        self.category_tabs.setTabsClosable(False) # 카테고리는 컨텍스트 메뉴를 통해 삭제
        self.category_tabs.currentChanged.connect(self.on_category_changed)

        # 카테고리 탭 컨텍스트 메뉴 (이름 바꾸기, 삭제)
        self.category_tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.category_tabs.tabBar().customContextMenuRequested.connect(self.show_category_context_menu)

        self.category_tabs.setAcceptDrops(True) # 바로가기를 탭 위로 드롭하는 것 허용
        main_layout.addWidget(self.category_tabs)

    def create_menus(self):
        """메인 메뉴 바를 생성합니다."""
        menu_bar = self.menuBar()
        if not menu_bar: # 일부 플랫폼/스타일에서 메뉴바 존재 보장
            menu_bar = QMenuBar(self)
            self.setMenuBar(menu_bar)

        # 파일 메뉴
        file_menu = menu_bar.addMenu("파일(&F)")

        # --- 수정: 명확성을 위해 메뉴 항목 이름 변경 ---
        open_folder_action = QAction("작업 폴더 열기(&O)", self)
        open_folder_action.triggered.connect(self.open_data_folder)
        file_menu.addAction(open_folder_action)
        file_menu.addSeparator()
        # --- 수정 종료 ---

        exit_action = QAction("종료(&X)", self)
        exit_action.triggered.connect(self.quit_application)
        file_menu.addAction(exit_action)

        # 설정 메뉴
        settings_menu = menu_bar.addMenu("설정(&S)")
        refresh_icons_action = QAction("아이콘 전체 새로고침(&R)", self)
        refresh_icons_action.triggered.connect(self.refresh_all_icons_action)
        settings_menu.addAction(refresh_icons_action)

        global_hotkey_action = QAction("전역 단축키 설정(&G)...", self)
        global_hotkey_action.triggered.connect(self.open_global_hotkey_settings_dialog)
        settings_menu.addAction(global_hotkey_action)

    def open_data_folder(self):
        """애플리케이션의 데이터 디렉토리를 기본 파일 탐색기에서 엽니다."""
        # --- 수정: 경로는 이제 루트 디렉토리를 가리킴 ---
        path = os.path.realpath(BASE_DIR)
        # --- 수정 종료 ---
        if not os.path.exists(path):
            # 이 경우는 BASE_DIR이 항상 존재해야 하므로 발생할 가능성이 낮음
            QMessageBox.warning(self, "폴더 열기 오류", f"작업 폴더를 찾을 수 없습니다:\n{path}")
            return

        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin": # macOS
                subprocess.run(["open", path])
            else: # Linux 및 기타 Unix 계열 시스템
                subprocess.run(["xdg-open", path])
        except Exception as e:
            QMessageBox.critical(self, "폴더 열기 오류", f"파일 탐색기를 열 수 없습니다:\n{e}")

    def open_global_hotkey_settings_dialog(self):
        """전역 창 보이기/숨기기 단축키 구성 대화상자를 엽니다."""
        dialog = GlobalHotkeySettingsDialog(self, self.global_show_window_hotkey_str)
        if dialog.exec():
            new_hotkey = dialog.get_new_hotkey() # 사용자가 지우기를 선택하면 ""가 될 수 있음
            if new_hotkey is not None: # 대화상자가 취소되지 않은 경우에만 진행 (저장 클릭)
                if new_hotkey != self.global_show_window_hotkey_str:
                    self.unregister_current_global_show_window_hotkey()
                    self.global_show_window_hotkey_str = new_hotkey
                    if self.register_new_global_show_window_hotkey(): # 새 단축키 등록 시도
                        self.save_data() # 성공적으로 등록된 경우에만 저장
                        QMessageBox.information(self, "단축키 변경 완료",
                                                f"창 보이기/숨기기 단축키가 '{new_hotkey}'(으)로 설정되었습니다." if new_hotkey else "창 보이기/숨기기 단축키가 해제되었습니다.")
                    else: # 등록 실패 (예: 'keyboard' 라이브러리에 대한 잘못된 단축키 문자열)
                        QMessageBox.critical(self, "단축키 등록 실패",
                                             f"단축키 '{new_hotkey}' 등록에 실패했습니다. 이전 설정을 유지합니다.")
                        # 설정 파일에서 이전에 저장된 단축키로 되돌리기 시도
                        previous_valid_hotkey = self.load_specific_setting("global_show_window_hotkey", "ctrl+shift+x") # 파일 또는 기본값에서 로드
                        self.global_show_window_hotkey_str = previous_valid_hotkey
                        self.register_new_global_show_window_hotkey() # (바라건대) 유효한 이전 단축키 재등록

    def _on_global_show_hotkey_triggered(self):
        """
        전역 창 보이기/숨기기 단축키의 콜백입니다.
        이 메서드는 'keyboard' 라이브러리 스레드에 의해 호출됩니다.
        시그널을 사용하여 GUI 업데이트를 메인 GUI 스레드에 위임합니다.
        빠른 연타를 방지하기 위해 디바운싱을 구현합니다.
        """
        current_time = time.time()
        if (current_time - self._last_global_hotkey_time) > HOTKEY_DEBOUNCE_TIME:
            self._last_global_hotkey_time = current_time
            # 메인 GUI 스레드에서 GUI 업데이트를 실행하도록 시그널을 보냅니다.
            # print(f"디버그: 전역 단축키 트리거됨, 시그널 발생 시간 {current_time}") # 디버그 출력
            self.request_toggle_window_visibility_signal.emit()
        # else:
            # print(f"디버그: 전역 단축키 디바운스됨 시간 {current_time}") # 디버그 출력


    @Slot()
    def _execute_always_show_window_gui_thread(self):
        """
        창이 표시되고, 포커스를 받고, 맨 앞으로 오도록 보장합니다.
        이 슬롯은 메인 GUI 스레드에서 실행됩니다.
        """
        # print("디버그: _execute_always_show_window_gui_thread 호출됨") # 디버그 출력
        # 맨 앞으로 오도록 임시로 StayOnTopHint 설정
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.showNormal() # 최소화되지 않았는지 확인
        self.raise_()     # 창 스택의 맨 위로 가져오기
        self.activateWindow() # 포커스 요청
        QApplication.processEvents() # 포커스 변경이 적용되도록 이벤트 처리
        # 표시 후 정상적으로 동작하도록 StayOnTopHint 제거
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show() # 플래그 재설정 후 가시성 보장
        QApplication.processEvents() # 만약을 위해 한 번 더 이벤트 처리


    @Slot()
    def _execute_toggle_window_visibility_gui_thread(self):
        """
        현재 상태와 포커스에 따라 창의 가시성을 토글합니다.
        숨겨져 있거나 포커스가 없으면, 표시하고, 포커스를 주고, 맨 앞으로 가져옵니다.
        보이고 포커스가 있으면, 숨깁니다.
        이 슬롯은 메인 GUI 스레드에서 실행됩니다.
        """
        # print("디버그: _execute_toggle_window_visibility_gui_thread 호출됨") # 디버그 출력
        is_currently_active = self.isActiveWindow()
        is_visible_and_not_minimized = self.isVisible() and not self.isMinimized()

        # print(f"디버그: is_currently_active: {is_currently_active}, is_visible_and_not_minimized: {is_visible_and_not_minimized}") # 디버그 출력

        if is_visible_and_not_minimized:
            if is_currently_active:
                # 창이 보이고, 최소화되지 않았으며, 포커스가 있음: 숨기기
                # print("디버그: 창 숨기기") # 디버그 출력
                self.hide()
            else:
                # 창이 보이고, 최소화되지 않았지만, 포커스가 없음: 맨 앞으로 가져오고 포커스 주기
                # print("디버그: 보이기, 올리기, 활성화 (보이지만 활성화되지 않았음)") # 디버그 출력
                self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
                self.showNormal() # 최소화되지 않았는지 확인
                self.raise_()
                self.activateWindow() # 활성화 요청
                QApplication.processEvents() # 포커스 변경이 적용되도록 이벤트 처리
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
                self.show() # 플래그 재설정 및 포커스 시도 후 가시성 보장
                QApplication.processEvents()
        else:
            # 창이 숨겨져 있거나 최소화됨: 표시하고, 맨 앞으로 가져오고, 포커스 주기
            # print("디버그: 보이기, 올리기, 활성화 (숨겨졌거나 최소화되었음)") # 디버그 출력
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            self.showNormal()
            self.raise_()
            self.activateWindow() # 활성화 요청
            QApplication.processEvents() # 이벤트 처리
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
            self.show() # 가시성 보장
            QApplication.processEvents()


    def register_new_global_show_window_hotkey(self):
        """'keyboard' 라이브러리를 사용하여 전역 창 보이기/숨기기 단축키를 등록합니다."""
        if self.global_show_window_hotkey_str: # 단축키 문자열이 설정된 경우에만
            try:
                # suppress=False는 필요 시 다른 애플리케이션으로 키 이벤트가 전달되도록 허용하지만,
                # 전역 보이기/숨기기에는 종종 억제(suppress)가 바람직합니다.
                # trigger_on_release=False(기본값)는 누를 때 트리거됨을 의미합니다.
                keyboard.add_hotkey(self.global_show_window_hotkey_str,
                                    self._on_global_show_hotkey_triggered,
                                    suppress=False) # 다른 앱에서 단축키를 차단하려면 suppress=True로 설정
                self.is_global_show_hotkey_registered = True
                print(f"정보: 전역 창 토글 단축키 '{self.global_show_window_hotkey_str}' 등록됨.")
                return True
            except Exception as e: # 'keyboard' 라이브러리 문제에 대한 광범위한 예외 처리
                print(f"오류: 전역 창 토글 단축키 '{self.global_show_window_hotkey_str}' 등록 실패: {e}")
                self.is_global_show_hotkey_registered = False
                return False
        else: # 단축키 문자열이 설정되지 않음
            self.is_global_show_hotkey_registered = False
            print("정보: 전역 창 토글 단축키가 비어있습니다. 등록하지 않습니다.")
            return True # 등록할 것이 없으므로 "성공"으로 간주

    def unregister_current_global_show_window_hotkey(self):
        """현재 전역 창 보이기/숨기기 단축키를 등록 해제합니다."""
        if self.is_global_show_hotkey_registered and self.global_show_window_hotkey_str:
            try:
                keyboard.remove_hotkey(self.global_show_window_hotkey_str)
                print(f"정보: 전역 창 토글 단축키 '{self.global_show_window_hotkey_str}' 등록 해제됨.")
            except Exception as e: # 'keyboard'에 의해 실제로 등록되지 않은 단축키일 경우 오류 포착
                print(f"경고: 전역 창 토글 단축키 '{self.global_show_window_hotkey_str}' 등록 해제 실패: {e}")
            finally:
                self.is_global_show_hotkey_registered = False
        elif not self.global_show_window_hotkey_str: # 단축키 문자열이 비어있는 경우
            self.is_global_show_hotkey_registered = False


    def refresh_all_icons_action(self):
        """모든 바로가기의 모든 파비콘을 다시 가져오는 액션입니다."""
        reply = QMessageBox.question(self, "아이콘 새로고침 확인",
                                     "모든 바로 가기의 아이콘을 새로고침 하시겠습니까?\n이 작업은 시간이 다소 걸릴 수 있으며, 기존 아이콘 파일이 삭제되고 다시 다운로드됩니다.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            updated_count = 0
            failed_to_delete_count = 0
            for sc_data in self.shortcuts:
                url = sc_data.get("url")
                if not url: continue

                # 새 아이콘을 가져오기 전에 이전 아이콘 파일 삭제 시도
                parsed_url = urlparse(url)
                domain = parsed_url.netloc
                if domain: # 도메인이 있는 경우 이전 아이콘 삭제 시도
                    safe_domain_name = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in domain)
                    for ext in ['.png', '.ico', '.jpg', '.jpeg', '.gif', '.svg']: # 모든 가능한 확장자 확인
                        cached_path = get_favicon_path(f"{safe_domain_name}{ext}")
                        if os.path.exists(cached_path) and os.path.basename(cached_path) != DEFAULT_FAVICON_FILENAME: # 기본 아이콘은 삭제하지 않음
                            try:
                                os.remove(cached_path)
                            except OSError as e:
                                failed_to_delete_count +=1
                                print(f"경고 (새로고침): {cached_path} 제거 실패: {e}")

                # 새 아이콘 가져오기
                new_icon_path = fetch_favicon(url) # 새 아이콘을 가져오려고 시도
                if sc_data.get("icon_path") != new_icon_path: # 다른 경우 (또는 이전이 None인 경우) 업데이트
                    sc_data["icon_path"] = new_icon_path
                    updated_count += 1
                QApplication.processEvents() # 긴 작업 동안 UI 반응 유지

            self.save_data() # 아이콘 경로 변경 사항 저장
            self.populate_list_for_current_tab() # 뷰 새로고침
            QApplication.restoreOverrideCursor()
            msg = f"{len(self.shortcuts)}개 바로 가기 중 {updated_count}개의 아이콘 정보가 업데이트되었습니다."
            if failed_to_delete_count > 0:
                msg += f"\n{failed_to_delete_count}개의 기존 아이콘 파일 삭제에 실패했습니다."
            QMessageBox.information(self, "새로고침 완료", msg)

    def _clear_tab_highlight(self):
        """탭 스타일시트를 기본값으로 재설정하여 드래그-오버 하이라이트를 지웁니다."""
        if self._highlighted_tab_index != -1:
            self.category_tabs.setStyleSheet(self._default_tab_stylesheet)
            self._highlighted_tab_index = -1

    def dragEnterEvent(self, event: QMouseEvent):
        """바로가기를 카테고리 탭으로 드롭하기 위한 드래그 진입 이벤트를 처리합니다."""
        if event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QMouseEvent):
        """바로가기가 위로 드래그될 때 카테고리 탭을 하이라이트합니다."""
        self._clear_tab_highlight() # 이전 하이라이트 지우기
        if event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
            tab_bar = self.category_tabs.tabBar()
            pos_in_tab_bar = tab_bar.mapFromGlobal(QCursor.pos()) # 탭 바에 대한 상대적 커서 위치 가져오기
            tab_idx = tab_bar.tabAt(pos_in_tab_bar)

            if tab_idx != -1 and tab_bar.rect().contains(pos_in_tab_bar): # 커서가 유효한 탭 위에 있는지 확인
                cat_name = self.category_tabs.tabText(tab_idx)
                if cat_name != ALL_CATEGORY_NAME and cat_name != ADD_CATEGORY_TAB_TEXT: # "전체" 또는 "+"는 하이라이트하지 않음
                    # 커서 아래의 탭 하이라이트
                    # 참고: nth-child는 CSS에서 1-기반
                    highlight_style = f"QTabBar::tab:nth-child({tab_idx + 1}) {{ background-color: lightblue; border: 1px solid blue; }}"
                    self.category_tabs.setStyleSheet(self._default_tab_stylesheet + highlight_style)
                    self._highlighted_tab_index = tab_idx
                    event.acceptProposedAction()
                    return
            event.acceptProposedAction() # 드롭 가능한 탭 위가 아니더라도 수락, dropEvent가 최종 확인 처리
        else:
            event.ignore()

    def dropEvent(self, event: QMouseEvent):
        """바로가기를 카테고리 탭에 드롭하여 카테고리를 변경하는 것을 처리합니다."""
        self._clear_tab_highlight() # 드롭 시 항상 하이라이트 지우기

        tab_bar = self.category_tabs.tabBar()
        pos_in_tab_bar = tab_bar.mapFromGlobal(QCursor.pos())

        if tab_bar.rect().contains(pos_in_tab_bar): # 드롭이 탭 바 영역 내에서 발생했는지 확인
            tab_idx = tab_bar.tabAt(pos_in_tab_bar)
            if tab_idx != -1:
                cat_name = self.category_tabs.tabText(tab_idx)
                # 사용자 카테고리인지 확인 ("전체" 또는 "+"가 아님)
                if cat_name != ALL_CATEGORY_NAME and cat_name != ADD_CATEGORY_TAB_TEXT:
                    if event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
                        sc_id = event.mimeData().data(MIME_TYPE_SHORTCUT_ID).data().decode()
                        self.move_shortcut_to_category(sc_id, cat_name)
                        event.acceptProposedAction()
                        return
        event.ignore() # 유효한 드롭 대상이 아니면 무시

    def dragLeaveEvent(self, event: QMouseEvent):
        """드래그 작업이 위젯 영역을 떠날 때 탭 하이라이트를 지웁니다."""
        self._clear_tab_highlight()
        event.accept()


    def on_tab_moved(self, from_index: int, to_index: int):
        """사용자에 의한 카테고리 탭 순서 변경을 처리합니다."""
        tab_bar = self.category_tabs.tabBar()
        num_tabs = tab_bar.count()

        # "전체"와 "+" 탭의 현재 위치 찾기
        add_tab_text_current_idx = -1
        all_tab_current_idx = -1
        for i in range(num_tabs):
            if tab_bar.tabText(i) == ADD_CATEGORY_TAB_TEXT:
                add_tab_text_current_idx = i
            if tab_bar.tabText(i) == ALL_CATEGORY_NAME:
                all_tab_current_idx = i

        # "전체" 탭이 항상 첫 번째에 있도록 보장
        if all_tab_current_idx != -1 and all_tab_current_idx != 0:
            tab_bar.blockSignals(True) # 재귀 호출 또는 원치 않는 시그널 발생 방지
            tab_bar.moveTab(all_tab_current_idx, 0)
            tab_bar.blockSignals(False)

        # "전체"가 이동했을 수 있으므로 num_tabs와 "+" 탭 인덱스 재평가
        num_tabs = tab_bar.count()
        add_tab_text_current_idx = -1 # 리셋하고 다시 찾기
        for i in range(num_tabs): # "+" 탭 다시 찾기
            if tab_bar.tabText(i) == ADD_CATEGORY_TAB_TEXT:
                add_tab_text_current_idx = i
                break

        # "+" 탭이 항상 마지막에 있도록 보장
        if add_tab_text_current_idx != -1 and add_tab_text_current_idx != num_tabs - 1:
            tab_bar.blockSignals(True)
            tab_bar.moveTab(add_tab_text_current_idx, num_tabs - 1)
            tab_bar.blockSignals(False)

        # 새 탭 위치에 따라 categories_order 업데이트 ("전체"와 "+" 제외)
        # "전체"가 인덱스 0에 있고 "+"가 마지막 인덱스에 있다고 가정
        new_order = [self.category_tabs.tabText(i) for i in range(1, self.category_tabs.count() - 1)]
        if self.categories_order != new_order:
            self.categories_order = new_order
            self.save_data()


    def init_tray_icon(self):
        """시스템 트레이 아이콘과 그 컨텍스트 메뉴를 초기화합니다."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.create_app_icon()) # 동일한 앱 아이콘 사용

        menu = QMenu(self)
        show_act = QAction("창 열기", self); show_act.triggered.connect(self._execute_always_show_window_gui_thread); menu.addAction(show_act)
        add_act = QAction("바로가기 추가...", self); add_act.triggered.connect(self.add_shortcut_from_tray); menu.addAction(add_act)
        menu.addSeparator()
        quit_act = QAction("종료", self); quit_act.triggered.connect(self.quit_application); menu.addAction(quit_act)

        self.tray_icon.setContextMenu(menu)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()

        # 활성화 시그널 연결 (트레이 아이콘 클릭/더블클릭)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

    def on_tray_icon_activated(self, reason):
        """트레이 아이콘 활성화(예: 클릭)를 처리합니다."""
        # 왼쪽 클릭(Trigger) 또는 더블클릭 시 창 표시
        if reason in [QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick]:
            self._execute_always_show_window_gui_thread() # 트레이 클릭에서는 토글하지 않고 항상 표시

    def add_shortcut_from_tray(self):
        """창을 표시한 다음 바로가기 추가 대화상자를 엽니다."""
        self._execute_always_show_window_gui_thread() # 창이 보이도록 보장
        self.add_shortcut() # 메인 add_shortcut 메서드 호출

    def closeEvent(self, event):
        """창 닫기 이벤트를 오버라이드하여 종료 대신 트레이로 숨깁니다."""
        event.ignore()  # 창이 실제로 닫히는 것을 방지
        self.hide()     # 창 숨기기

        # 트레이 아이콘이 사용 가능하고 보이는 경우 메시지 표시
        if hasattr(self, 'tray_icon') and self.tray_icon and self.tray_icon.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.showMessage(
                APP_NAME,
                "앱이 트레이에서 실행 중입니다.",
                self.create_app_icon(), # 메시지에 앱 아이콘 사용
                2000 # 밀리초
            )
        # 참고: 여기서는 self.quit_application()을 호출하지 않으므로 앱은 계속 실행됩니다.

    def quit_application(self):
        """단축키 등록 해제 및 트레이 아이콘 숨기기를 통해 애플리케이션을 적절히 종료합니다."""
        self.unregister_current_global_show_window_hotkey() # 전역 단축키 등록 해제
        for key in list(self.hotkey_actions.keys()): # 모든 항목 단축키 등록 해제
            self.unregister_hotkey(key)

        if hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.hide() # 종료 전에 트레이 아이콘 숨기기

        QApplication.instance().quit()

    def load_specific_setting(self, key_name, default_value):
        """설정 JSON 파일에서 특정 키를 로드합니다."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data.get(key_name, default_value)
            except json.JSONDecodeError: # 손상된 JSON 처리
                return default_value
        return default_value # 파일을 찾을 수 없음

    def load_data_and_register_hotkeys(self):
        """JSON에서 바로가기와 설정을 로드한 다음 단축키를 등록합니다."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.categories_order = data.get("categories_order", [])
                self.shortcuts = data.get("shortcuts", [])
                self.global_show_window_hotkey_str = data.get("global_show_window_hotkey", "ctrl+shift+x") # 전역 단축키 로드

                # 이전 버전에 대한 데이터 무결성 검사 및 마이그레이션
                needs_save = False
                max_prio_val = 0.0 # 필요 시 새 우선순위 할당에 도움
                for idx, sc in enumerate(self.shortcuts):
                    if "id" not in sc or not sc["id"]: # 고유 ID 보장
                        sc["id"] = str(uuid.uuid4())
                        needs_save = True
                    if "category" not in sc: # 카테고리 보장
                        sc["category"] = self.categories_order[0] if self.categories_order else "일반"
                        needs_save = True
                    if "priority" not in sc: # 순서 지정을 위한 우선순위 보장
                        sc["priority"] = float(idx + 1.0) # 간단한 증분 우선순위 할당
                        needs_save = True
                    current_prio = sc.get("priority", 0.0)
                    if not isinstance(current_prio, float): # 우선순위가 float인지 보장
                        try: sc['priority'] = float(current_prio); needs_save = True
                        except ValueError: sc['priority'] = float(idx + 1.0); needs_save = True

                    max_prio_val = max(max_prio_val, sc.get("priority", 0.0))


                if needs_save:
                    self.save_data() # 수정 사항이 있으면 저장

            except Exception as e: # 파일 읽기/JSON 파싱 오류에 대한 광범위한 예외 처리
                QMessageBox.warning(self, "데이터 로드 오류", f"{SETTINGS_FILE} 로드 오류: {e}.\n기본 설정으로 시작합니다.")
                self.shortcuts, self.categories_order = [], []
                self.global_show_window_hotkey_str = "ctrl+shift+x" # 오류 시 기본값으로 리셋
        else: # 설정 파일이 없으면 기본값 사용
             self.global_show_window_hotkey_str = "ctrl+shift+x"


        # 모든 것이 실패할 경우 최소한 하나의 카테고리가 있도록 보장 (예: "기본")
        if not self.categories_order and not self.shortcuts: # 둘 다 비어있는 경우에만
            self.categories_order = ["기본"]

        # 만일을 대비해 categories_order에서 예약된 이름 정리
        self.categories_order = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]

        self.update_category_tabs() # 로드/기본 데이터 기반으로 탭 생성/업데이트
        self.register_all_item_hotkeys() # 로드된 항목에 대한 단축키 등록
        self.register_new_global_show_window_hotkey() # 전역 보이기/숨기기 단축키 등록

        # 로드 후 유효한 탭 선택
        current_idx = self.category_tabs.currentIndex()
        if self.category_tabs.count() > 0: # 탭이 있는 경우
            first_valid_tab_index = 0 # 보통 "전체"
            # "전체"가 첫 번째가 아닐 수 있는 엣지 케이스 처리 (탭 이동 로직으로는 발생하지 않아야 함)
            if self.category_tabs.tabText(first_valid_tab_index) == ADD_CATEGORY_TAB_TEXT and self.category_tabs.count() > 1:
                first_valid_tab_index = 1

            if current_idx == -1 or self.category_tabs.tabText(current_idx) == ADD_CATEGORY_TAB_TEXT:
                self.category_tabs.setCurrentIndex(first_valid_tab_index)
            else: # 유효한 탭이 이미 현재 탭이면 내용이 채워지도록 보장
                self.on_category_changed(current_idx) # 현재 유효한 탭에 대해 populate 트리거
        elif not self.categories_order: # 카테고리는 없지만 "전체"와 "+"는 존재
             self.populate_list_for_current_tab() # "전체" 탭 채우기 ("새로 추가"가 표시될 것임)


    def save_data(self):
        """바로가기와 설정을 JSON 파일에 저장합니다."""
        # --- 수정: 중복되는 디렉토리 생성 확인 제거 ---
        # 시작 로직이 이제 파비콘 디렉토리 생성을 처리합니다.

        # categories_order에는 사용자 정의 카테고리만 저장
        user_cats = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]

        # 다음 로드 시 순서 유지를 위해 저장 전 우선순위로 바로가기 정렬
        self.shortcuts.sort(key=lambda x: x.get('priority', float('inf')))

        data_to_save = {
            "categories_order": user_cats,
            "shortcuts": self.shortcuts,
            "global_show_window_hotkey": self.global_show_window_hotkey_str
        }
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=4)
        except Exception as e:
            QMessageBox.critical(self, "데이터 저장 오류", f"{SETTINGS_FILE} 파일 저장 실패: {e}")

    def update_category_tabs(self):
        """self.categories_order에 기반하여 카테고리 탭을 업데이트합니다."""
        # 지우기/재채우기 중 문제 방지를 위해 시그널 연결 해제
        try:
            self.category_tabs.currentChanged.disconnect(self.on_category_changed)
        except RuntimeError: # 연결되지 않았으면 괜찮음
            pass

        # 선택 상태 보존 시도
        intended_selection_text = None
        if hasattr(self, '_category_to_select_after_update') and self._category_to_select_after_update:
            intended_selection_text = self._category_to_select_after_update
        else: # 명시적으로 설정되지 않았으면 현재 또는 마지막 유효한 것 유지 시도
            current_idx_before_clear = self.category_tabs.currentIndex()
            if current_idx_before_clear != -1:
                temp_text = self.category_tabs.tabText(current_idx_before_clear)
                if temp_text not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]:
                    intended_selection_text = temp_text
                # 현재가 "전체" 또는 "+"인 경우 마지막으로 알려진 유효 선택 카테고리로 대체
                elif 0 <= self.last_selected_valid_category_index < self.category_tabs.count() and \
                     self.category_tabs.tabText(self.last_selected_valid_category_index) not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]:
                     intended_selection_text = self.category_tabs.tabText(self.last_selected_valid_category_index)


        self.category_tabs.clear() # 기존 모든 탭 제거

        # "전체" 탭 먼저 추가
        all_list_widget = self._create_new_list_widget()
        self.category_tabs.addTab(all_list_widget, ALL_CATEGORY_NAME)

        # 사용자 정의 카테고리 탭 추가
        for cat_name in self.categories_order:
            cat_list_widget = self._create_new_list_widget()
            self.category_tabs.addTab(cat_list_widget, cat_name)

        # 마지막에 "+" 탭(새 카테고리 추가용) 추가
        self.category_tabs.addTab(QWidget(), ADD_CATEGORY_TAB_TEXT) # "+"를 위한 플레이스홀더 QWidget

        # 시그널 재연결
        self.category_tabs.currentChanged.connect(self.on_category_changed)

        # 선택 복원
        idx_to_select = 0 # "전체"(인덱스 0)로 기본 설정
        if intended_selection_text:
            for i in range(self.category_tabs.count() -1): # 선택 복원 로직에서 "+" 탭 제외
                if self.category_tabs.tabText(i) == intended_selection_text:
                    idx_to_select = i
                    break

        # 현재 인덱스가 이미 선택하려는 것이고 "+"가 아니면 수동으로 populate 트리거
        if self.category_tabs.currentIndex() == idx_to_select and self.category_tabs.tabText(idx_to_select) != ADD_CATEGORY_TAB_TEXT :
            self.populate_list_for_current_tab()
        self.category_tabs.setCurrentIndex(idx_to_select) # 인덱스가 실제로 변경되면 on_category_changed 트리거


    def _create_new_list_widget(self) -> DraggableListWidget:
        """탭을 위한 새 DraggableListWidget을 생성하고 구성하는 헬퍼 함수입니다."""
        list_widget = DraggableListWidget()
        list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        list_widget.setViewMode(QListView.ViewMode.IconMode)
        list_widget.setIconSize(QSize(48, 48)) # 바로가기 기본 아이콘 크기
        list_widget.setFlow(QListView.Flow.LeftToRight) # 항목을 왼쪽에서 오른쪽으로 배열
        list_widget.setWrapping(True) # 공간이 부족하면 다음 줄로 항목 줄 바꿈
        list_widget.setResizeMode(QListView.ResizeMode.Adjust) # 리사이즈 시 레이아웃 조정
        list_widget.setUniformItemSizes(False) # 텍스트에 따라 항목이 다른 너비를 가질 수 있도록 허용
        list_widget.setGridSize(QSize(100, 80)) # 대략적인 항목 셀 크기 (너비, 높이)
        list_widget.setSpacing(10) # 항목 사이 간격
        list_widget.setWordWrap(True) # 항목 레이블 내 텍스트 줄 바꿈

        list_widget.itemActivated.connect(self.on_item_activated) # 더블 클릭 / 엔터
        list_widget.item_dropped_signal.connect(self.on_shortcut_item_reordered)

        # 바로가기 항목 컨텍스트 메뉴 (편집, 삭제)
        list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        list_widget.customContextMenuRequested.connect(self.show_shortcut_context_menu)
        return list_widget

    def on_category_changed(self, index):
        """선택된 카테고리 탭 변경을 처리합니다."""
        if index == -1: return # 탭이 존재하면 발생하지 않아야 함

        current_tab_text = self.category_tabs.tabText(index)

        if current_tab_text == ADD_CATEGORY_TAB_TEXT:
            self.add_category() # 이는 탭을 업데이트하고 인덱스를 변경할 수 있음
            # 추가 후 현재 탭이 여전히 "+"이면 마지막 유효 또는 "전체"로 되돌림
            if self.category_tabs.tabText(self.category_tabs.currentIndex()) == ADD_CATEGORY_TAB_TEXT:
                valid_fallback_idx = 0 # "전체"로 기본 설정
                if 0 <= self.last_selected_valid_category_index < (self.category_tabs.count() -1) : # 새 "+" 제외
                    valid_fallback_idx = self.last_selected_valid_category_index
                self.category_tabs.setCurrentIndex(valid_fallback_idx)
        else:
            self.last_selected_valid_category_index = index # 마지막 유효 선택 탭 업데이트
            self.populate_list_for_current_tab()

    def get_current_category_name(self):
        """현재 선택된 카테고리 탭의 이름을 반환합니다."""
        idx = self.category_tabs.currentIndex()
        if idx != -1:
            current_text = self.category_tabs.tabText(idx)
            # 데이터 목적으로 "+" 탭이 어떻게든 선택되면 마지막 유효 실제 카테고리 사용
            if current_text == ADD_CATEGORY_TAB_TEXT:
                if 0 <= self.last_selected_valid_category_index < self.category_tabs.count() -1: # "+" 제외
                    return self.category_tabs.tabText(self.last_selected_valid_category_index)
                return ALL_CATEGORY_NAME # 다른 유효한 탭이 선택되지 않은 경우 대체
            return current_text
        return ALL_CATEGORY_NAME # 선택된 탭이 없는 경우 기본값 (발생하지 않아야 함)

    def populate_list_for_current_tab(self):
        """현재 탭의 리스트 위젯을 바로가기로 채웁니다."""
        current_tab_index = self.category_tabs.currentIndex()
        if current_tab_index == -1: return

        current_tab_category_name = self.category_tabs.tabText(current_tab_index)
        current_list_widget = self.category_tabs.widget(current_tab_index)

        # DraggableListWidget인지 확인 ("+"의 QWidget이 아님)
        if not isinstance(current_list_widget, DraggableListWidget):
            if current_list_widget is not None and hasattr(current_list_widget, 'clear'):
                current_list_widget.clear() # "+" 탭의 QWidget이면 지움 (어차피 비어있음)
            return

        current_list_widget.clear()
        icon_size = current_list_widget.iconSize() # 구성된 아이콘 크기 가져오기
        fallback_qicon = self.get_fallback_qicon(icon_size) # 미리 스케일링된 대체 아이콘 가져오기

        # 현재 카테고리에 대한 바로가기 필터링
        items_to_display = []
        if current_tab_category_name == ALL_CATEGORY_NAME:
            items_to_display = list(self.shortcuts) # 모두 표시
        else:
            items_to_display = [sc for sc in self.shortcuts if sc.get("category") == current_tab_category_name]

        # 일관된 표시를 위해 우선순위로 항목 정렬
        items_to_display.sort(key=lambda x: x.get('priority', float('inf')))

        for sc_data in items_to_display:
            name = sc_data.get("name", "N/A")
            item = QListWidgetItem(name)
            current_icon = fallback_qicon # 기본적으로 대체 아이콘 사용

            icon_path_from_data = sc_data.get("icon_path")
            if icon_path_from_data:
                loaded_user_icon = load_icon_pixmap(icon_path_from_data, icon_size) # 로드 및 스케일링
                if not loaded_user_icon.isNull():
                    current_icon = loaded_user_icon

            item.setIcon(current_icon)
            item.setData(Qt.ItemDataRole.UserRole, sc_data) # 전체 데이터 dict 저장
            item.setToolTip(f"{name}\nURL: {sc_data.get('url')}\n단축키: {sc_data.get('hotkey') or '없음'}")
            current_list_widget.addItem(item)

        # 각 리스트의 끝에 "새 바로가기 추가" 항목 추가
        add_shortcut_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder) # "추가"에 폴더 아이콘 사용
        # 또는: QStyle.StandardPixmap.SP_FileIcon, SP_ToolBarHorizontalExtensionButton
        add_item = QListWidgetItem(add_shortcut_icon, "새 바로가기")
        add_item.setData(Qt.ItemDataRole.UserRole, {"type": ADD_ITEM_IDENTIFIER, "id": ADD_ITEM_IDENTIFIER}) # 특수 타입
        add_item.setToolTip("새로운 바로가기를 추가합니다.")
        add_item.setFlags(add_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled) # 드래그 불가
        current_list_widget.addItem(add_item)


    def register_all_item_hotkeys(self):
        """기존 모든 항목 단축키를 등록 해제하고 self.shortcuts에서 다시 등록합니다."""
        for key in list(self.hotkey_actions.keys()): # 키 복사본으로 반복
            self.unregister_hotkey(key)
        self.hotkey_actions.clear() # 추적 딕셔너리 지우기

        for sc_data in self.shortcuts:
            self.register_item_hotkey(sc_data)

    def on_item_activated(self, item: QListWidgetItem):
        """리스트 항목의 활성화(더블클릭/엔터)를 처리합니다."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            if data.get("type") == ADD_ITEM_IDENTIFIER:
                self.add_shortcut() # 바로가기 추가 대화상자 호출
            elif "url" in data:
                self.open_url(data["url"])

    def on_shortcut_item_reordered(self, dropped_item_id: str, new_row_in_listwidget: int, source_list_widget: DraggableListWidget):
        """
        드래그 앤 드롭을 통해 리스트 내 바로가기 순서 변경을 처리합니다.
        현재 리스트 뷰의 시각적 순서에 따라 우선순위를 재계산합니다.
        """
        # 현재 리스트에 있는 모든 항목의 데이터 가져오기 ("새로 추가" 제외)
        current_view_items_data = []
        for i in range(source_list_widget.count()):
            item = source_list_widget.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and item_data.get("type") != ADD_ITEM_IDENTIFIER:
                current_view_items_data.append(item_data)

        if not current_view_items_data: return # 항목이 존재하면 발생하지 않아야 함

        # 이 필터링된 리스트 내에서 드롭된 항목의 실제 새 행 찾기
        # (시그널의 new_row_in_listwidget는 "새로 추가"가 보였다면 포함할 수 있음)
        actual_new_row_in_filtered_list = -1
        moved_item_actual_data = None
        for idx, data_dict in enumerate(current_view_items_data):
            if data_dict.get("id") == dropped_item_id:
                actual_new_row_in_filtered_list = idx
                moved_item_actual_data = data_dict
                break

        if actual_new_row_in_filtered_list == -1 or not moved_item_actual_data:
            print(f"경고 (순서 변경): 드롭된 항목 ID {dropped_item_id}를 현재 뷰에서 찾을 수 없습니다.")
            return

        new_priority = 0.0
        num_items_in_view = len(current_view_items_data)

        if num_items_in_view == 1: # 항목이 하나뿐
            new_priority = 1.0
        elif actual_new_row_in_filtered_list == 0: # 맨 앞으로 이동
            priority_after = current_view_items_data[1].get('priority', 2.0) # 현재 인덱스 1에 있는 항목의 우선순위
            new_priority = priority_after / 2.0
            if new_priority <= 0: new_priority = priority_after - 0.5 # P_after가 작은 경우 0/음수 방지
            if new_priority <=0: new_priority = 0.001 # 절대 최소 양수
        elif actual_new_row_in_filtered_list == num_items_in_view - 1: # 맨 뒤로 이동
            priority_before = current_view_items_data[num_items_in_view - 2].get('priority', 0.0) # 현재 그 앞에 있는 항목의 우선순위
            new_priority = priority_before + 1.0
        else: # 두 항목 사이로 이동
            priority_before = current_view_items_data[actual_new_row_in_filtered_list - 1].get('priority', 0.0)
            priority_after = current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority', 0.0)
            new_priority = (priority_before + priority_after) / 2.0

        # 우선순위 충돌 확인 (예: 나누기 결과가 기존 우선순위이거나 너무 가까운 경우)
        epsilon = 0.00001 # float 비교를 위한 작은 허용 오차
        conflict = False
        # 새 이웃과 비교
        if actual_new_row_in_filtered_list > 0 and abs(new_priority - current_view_items_data[actual_new_row_in_filtered_list - 1].get('priority',0.0)) < epsilon:
            conflict = True
        if actual_new_row_in_filtered_list < num_items_in_view - 1 and abs(new_priority - current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority',0.0)) < epsilon:
            conflict = True

        if conflict: # 새 우선순위가 인접한 것과 너무 가까우면 약간 조정하거나 우선순위를 다시 매김
            # 이것은 간단한 충돌 해결 방법이며, 더 견고한 방법은 모든 우선순위를 다시 번호 매길 수 있음
            # 지금은 살짝 밀어보기.
            if actual_new_row_in_filtered_list > 0: # 앞에 항목이 있는 경우
                new_priority = current_view_items_data[actual_new_row_in_filtered_list - 1].get('priority',0.0) + 0.5
            elif num_items_in_view > 1 : # 첫 번째이고 뒤에 항목이 있는 경우
                 new_priority = current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority',0.0) - 0.5
                 if new_priority <=0 : new_priority = current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority',0.0) / 2.0 # 양수 보장
            else: # 단일 항목이거나 여전히 문제
                new_priority = 1.0 # 대체

        # 메인 self.shortcuts 리스트에서 우선순위 업데이트
        for sc_dict_global in self.shortcuts:
            if sc_dict_global.get("id") == dropped_item_id:
                sc_dict_global['priority'] = new_priority
                break

        self.save_data()
        self.populate_list_for_current_tab() # 새 우선순위에 따라 다시 정렬하고 재채우기


    def add_shortcut(self):
        """대화상자를 통해 새 바로가기 추가를 처리합니다."""
        user_selectable_cats = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]
        dlg_cats = user_selectable_cats if user_selectable_cats else ["일반"]

        # 대화상자를 위한 합리적인 기본 카테고리 결정
        initial_category_for_dialog = "일반"
        current_tab_name = self.get_current_category_name() # 현재 탭 이름 가져오기 ("전체"일 수 있음)
        if current_tab_name != ALL_CATEGORY_NAME and current_tab_name != ADD_CATEGORY_TAB_TEXT:
            initial_category_for_dialog = current_tab_name # 현재 카테고리 미리 선택
        elif dlg_cats: # 현재가 "전체"인 경우, 사용 가능한 첫 번째 사용자 카테고리 선택
            initial_category_for_dialog = dlg_cats[0]
        # dlg_cats가 비어있고 현재가 "전체"인 경우, ShortcutDialog에서 "일반"으로 기본 설정됨

        # 대화상자를 위해 미리 선택된 카테고리로 임시 shortcut_data 생성
        temp_shortcut_data = {"category": initial_category_for_dialog}

        dlg = ShortcutDialog(self, shortcut_data=temp_shortcut_data, categories=dlg_cats) # 새 항목임을 나타내기 위해 shortcut_data에 None 전달
        if dlg.exec():
            new_data = dlg.get_data()
            new_data["id"] = str(uuid.uuid4()) # 새 고유 ID 생성

            # 우선순위 할당 (예: 기존 최대값 + 1, 또는 첫 항목이면 1.0)
            max_priority = 0.0
            if self.shortcuts: # 기존 바로가기가 있는 경우
                max_priority = max(sc.get("priority", 0.0) for sc in self.shortcuts)
            new_data["priority"] = max_priority + 1.0

            # 단축키 충돌 확인
            if new_data["hotkey"]: # 단축키가 실제로 입력된 경우에만 확인
                # 다른 항목 단축키와 비교
                if any(s.get("hotkey") == new_data["hotkey"] for s in self.shortcuts):
                    QMessageBox.warning(self, "단축키 중복", f"단축키 '{new_data['hotkey']}'은(는) 이미 다른 바로가기에서 사용 중입니다.")
                    return # 추가 중단
                # 전역 보이기/숨기기 단축키와 비교
                if new_data["hotkey"] == self.global_show_window_hotkey_str and self.global_show_window_hotkey_str:
                    QMessageBox.warning(self, "단축키 충돌", f"단축키 '{new_data['hotkey']}'은(는) 창 보이기 전역 단축키로 사용 중입니다.")
                    return # 추가 중단

            # 아이콘 가져오기
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            new_data["icon_path"] = fetch_favicon(new_data["url"])
            QApplication.restoreOverrideCursor()

            # 바로가기 리스트에 추가
            self.shortcuts.append(new_data)

            # 선택된 카테고리가 "일반"이고 categories_order에 없으면 추가
            chosen_cat = new_data["category"]
            if chosen_cat == "일반" and "일반" not in self.categories_order:
                self.categories_order.append("일반")
                # 여기서 즉시 update_category_tabs를 호출할 필요 없음, 저장 후 처리됨

            self.save_data()
            self.register_all_item_hotkeys() # 새 단축키가 추가되었으므로 모든 단축키 재등록

            # UI 업데이트: 새로 추가된 항목의 카테고리 탭 선택
            self._category_to_select_after_update = chosen_cat
            self.update_category_tabs() # 탭을 다시 채우고 올바른 탭을 선택
            self._category_to_select_after_update = None # 마커 지우기

    def add_category(self):
        """입력 대화상자를 통해 새 카테고리 추가를 처리합니다."""
        name, ok = QInputDialog.getText(self, "새 카테고리", "카테고리 이름을 입력하세요:")
        if ok and name:
            name = name.strip() # 앞뒤 공백 제거
            if not name:
                QMessageBox.warning(self, "입력 오류", "카테고리 이름은 비워둘 수 없습니다.")
                return
            if name in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT] or name in self.categories_order:
                QMessageBox.warning(self, "입력 오류", f"'{name}'은(는) 사용 중이거나 사용할 수 없는 카테고리 이름입니다.")
                return

            self.categories_order.append(name)
            self.save_data()
            self._category_to_select_after_update = name # 탭 업데이트 후 선택을 위해 표시
            self.update_category_tabs() # 새 탭을 생성하고 선택
            self._category_to_select_after_update = None # 마커 지우기
        elif self.category_tabs.tabText(self.category_tabs.currentIndex()) == ADD_CATEGORY_TAB_TEXT:
            # "+" 탭이 활성 상태일 때 사용자가 대화상자를 취소, 마지막 유효 또는 "전체"로 되돌림
            valid_fallback_idx = 0 # "전체"로 기본 설정
            if 0 <= self.last_selected_valid_category_index < (self.category_tabs.count() -1) : # "+"는 아직 있으므로 -1
                valid_fallback_idx = self.last_selected_valid_category_index
            self.category_tabs.setCurrentIndex(valid_fallback_idx)


    def delete_category_action(self, category_name_to_delete):
        """카테고리를 삭제하고 그 안의 바로가기를 이동하는 액션입니다."""
        if category_name_to_delete == ALL_CATEGORY_NAME or category_name_to_delete == ADD_CATEGORY_TAB_TEXT:
            QMessageBox.warning(self, "삭제 불가", f"'{category_name_to_delete}' 카테고리는 삭제할 수 없습니다.")
            return

        reply = QMessageBox.question(self, "카테고리 삭제 확인",
                                     f"'{category_name_to_delete}' 카테고리를 삭제하시겠습니까?\n\n이 카테고리의 모든 바로가기는 첫 번째 사용 가능한 카테고리 또는 '일반' 카테고리로 이동됩니다.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            # 대체 카테고리 결정
            target_fallback_category = "일반" # 기본 대체
            if self.categories_order: # 다른 사용자 카테고리가 존재하면
                remaining_categories = [cat for cat in self.categories_order if cat != category_name_to_delete]
                if remaining_categories:
                    target_fallback_category = remaining_categories[0] # 첫 번째 남은 것 사용
                # 다른 카테고리가 남지 않으면, 항목들은 "일반"으로 이동하고
                # "일반"이 없으면 categories_order에 추가될 것임.

            # 삭제된 카테고리의 항목들을 대체 카테고리로 이동
            for sc in self.shortcuts:
                if sc.get("category") == category_name_to_delete:
                    sc["category"] = target_fallback_category

            # 순서에서 카테고리 제거
            if category_name_to_delete in self.categories_order:
                self.categories_order.remove(category_name_to_delete)

            # 대체 카테고리가 "일반"이고 categories_order에 없지만 항목들이 이제 그것을 사용하면, 추가.
            if target_fallback_category == "일반" and \
               "일반" not in self.categories_order and \
               any(sc.get("category") == "일반" for sc in self.shortcuts):
                self.categories_order.append("일반")

            self.save_data()
            self.update_category_tabs() # UI 새로고침, 유효한 탭 선택

            # 삭제 후 유효한 탭이 선택되고 내용이 채워지도록 보장
            if self.category_tabs.count() > 0 :
                 if self.category_tabs.tabText(self.category_tabs.currentIndex()) == ADD_CATEGORY_TAB_TEXT:
                     self.category_tabs.setCurrentIndex(0) # "+"가 어떻게든 선택되면 "전체" 선택
                 else: # on_category_changed를 통해 populate_list_for_current_tab 트리거
                    self.on_category_changed(self.category_tabs.currentIndex())
            else: # 발생하지 않아야 함, "전체"와 "+"는 개념적으로 항상 존재
                 self.populate_list_for_current_tab()


    def show_category_context_menu(self, position: QPoint):
        """카테고리 탭에 대한 컨텍스트 메뉴를 표시합니다 (예: 카테고리 삭제)."""
        tab_bar = self.category_tabs.tabBar()
        tab_index = tab_bar.tabAt(position)
        if tab_index != -1:
            category_name = self.category_tabs.tabText(tab_index)
            # "전체" 또는 "+" 탭에는 컨텍스트 메뉴 없음
            if category_name == ALL_CATEGORY_NAME or category_name == ADD_CATEGORY_TAB_TEXT:
                return

            menu = QMenu(self)
            delete_action = QAction(f"'{category_name}' 카테고리 삭제", self)
            # 람다를 사용하여 category_name을 액션에 전달
            delete_action.triggered.connect(lambda checked=False, name=category_name: self.delete_category_action(name))
            menu.addAction(delete_action)
            menu.exec(tab_bar.mapToGlobal(position)) # 전역 커서 위치에 메뉴 표시

    def show_shortcut_context_menu(self, position: QPoint):
        """바로가기 항목에 대한 컨텍스트 메뉴를 표시합니다 (편집, 삭제)."""
        current_tab_idx = self.category_tabs.currentIndex()
        if current_tab_idx == -1: return

        list_widget = self.category_tabs.widget(current_tab_idx)
        if not isinstance(list_widget, DraggableListWidget): return # 리스트 위젯이 아님 (예: "+" 탭)

        item = list_widget.itemAt(position) # 커서 위치의 항목 가져오기
        if item:
            item_data = item.data(Qt.ItemDataRole.UserRole)
            # 실제 바로가기 항목인지 확인, "새로 추가" 항목이 아님
            if isinstance(item_data, dict) and item_data.get("type") != ADD_ITEM_IDENTIFIER and "id" in item_data:
                menu = QMenu(self)
                edit_action = QAction("편집", self)
                # 특정 항목(it)을 edit_shortcut_context에 전달
                edit_action.triggered.connect(lambda checked=False, it=item: self.edit_shortcut_context(it))
                menu.addAction(edit_action)

                delete_action = QAction("삭제", self)
                # 특정 항목(it)을 delete_shortcut_context에 전달
                delete_action.triggered.connect(lambda checked=False, it=item: self.delete_shortcut_context(it))
                menu.addAction(delete_action)

                menu.exec(list_widget.mapToGlobal(position))

    def edit_shortcut_context(self, item: QListWidgetItem):
        """바로가기 편집을 위해 컨텍스트 메뉴에서 호출됩니다. `item`은 QListWidgetItem입니다."""
        if not item: return
        # 선택 사항: 시각적 피드백을 위해, 아직 현재 항목이 아니면 현재 항목으로 만들기
        # item.listWidget().setCurrentItem(item)
        self.edit_shortcut(item) # 특정 항목을 메인 edit_shortcut 메서드에 전달

    def delete_shortcut_context(self, item: QListWidgetItem):
        """바로가기 삭제를 위해 컨텍스트 메뉴에서 호출됩니다. `item`은 QListWidgetItem입니다."""
        if not item: return
        # 선택 사항: 시각적 피드백
        # item.listWidget().setCurrentItem(item)
        self.delete_shortcut(item) # 특정 항목을 메인 delete_shortcut 메서드에 전달


    def edit_shortcut(self, item_to_edit: QListWidgetItem): # item_to_edit는 QListWidgetItem
        """기존 바로가기 편집을 처리합니다."""
        if not item_to_edit:
            QMessageBox.information(self, "알림", "편집할 항목이 유효하지 않습니다.")
            return

        data_item = item_to_edit.data(Qt.ItemDataRole.UserRole) # 전달된 항목에서 데이터 가져오기
        if not isinstance(data_item, dict) or "id" not in data_item or data_item.get("type") == ADD_ITEM_IDENTIFIER:
            # item_to_edit가 유효한 바로가기 항목이 아니었음을 의미
            return

        shortcut_id_to_edit = data_item.get("id")
        # ID를 사용하여 self.shortcuts에서 원본 전체 바로가기 데이터 찾기
        original_shortcut_data = next((s for s in self.shortcuts if s.get("id") == shortcut_id_to_edit), None)

        if not original_shortcut_data:
            QMessageBox.critical(self, "편집 오류", "편집할 바로가기 원본 데이터를 찾을 수 없습니다.")
            return

        user_selectable_cats = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]
        dlg_cats = user_selectable_cats if user_selectable_cats else ["일반"]

        dlg = ShortcutDialog(self, shortcut_data=original_shortcut_data, categories=dlg_cats)
        if dlg.exec():
            new_data = dlg.get_data()
            new_data["id"] = shortcut_id_to_edit # 원본 ID 보존
            new_data["priority"] = original_shortcut_data.get("priority") # 원본 우선순위 보존

            # 단축키 충돌 확인 (단축키가 변경된 경우에만)
            if new_data["hotkey"] and new_data["hotkey"] != original_shortcut_data.get("hotkey"):
                # 다른 항목과 비교
                if any(s.get("hotkey") == new_data["hotkey"] and s.get("id") != shortcut_id_to_edit for s in self.shortcuts):
                    QMessageBox.warning(self, "단축키 중복", f"단축키 '{new_data['hotkey']}'은(는) 이미 다른 바로가기에서 사용 중입니다.")
                    return
                # 전역 단축키와 비교
                if new_data["hotkey"] == self.global_show_window_hotkey_str and self.global_show_window_hotkey_str:
                    QMessageBox.warning(self, "단축키 충돌", f"단축키 '{new_data['hotkey']}'은(는) 창 보이기 전역 단축키로 사용 중입니다.")
                    return

            chosen_cat = new_data["category"]
            # "일반"이 선택되었고 categories_order에 없으면 추가.
            if chosen_cat == "일반" and "일반" not in self.categories_order:
                self.categories_order.append("일반")

            # URL이 변경된 경우에만 아이콘 업데이트
            if new_data["url"] != original_shortcut_data.get("url"):
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                # 이전 아이콘 파일이 존재하고 기본 아이콘이 아니면 삭제 시도
                old_icon_path = original_shortcut_data.get("icon_path")
                if old_icon_path and os.path.exists(old_icon_path) and os.path.basename(old_icon_path) != DEFAULT_FAVICON_FILENAME :
                    try: os.remove(old_icon_path)
                    except OSError as e: print(f"경고 (편집): 이전 아이콘 {old_icon_path} 제거 실패: {e}")
                new_data["icon_path"] = fetch_favicon(new_data["url"])
                QApplication.restoreOverrideCursor()
            else: # URL이 변경되지 않았으면 이전 아이콘 경로 유지
                new_data["icon_path"] = original_shortcut_data.get("icon_path")

            # 메인 리스트의 바로가기 업데이트
            for i, s_loop_var in enumerate(self.shortcuts):
                if s_loop_var.get("id") == shortcut_id_to_edit:
                    self.shortcuts[i] = new_data
                    break

            self.save_data()
            self.register_all_item_hotkeys() # 하나가 변경되었을 수 있으므로 모든 단축키 재등록

            # UI 업데이트하고 (잠재적으로 새로운) 카테고리 탭 선택
            self._category_to_select_after_update = chosen_cat
            self.update_category_tabs() # 탭을 새로고침하고 올바른 탭 선택
            self._category_to_select_after_update = None # 마커 지우기

    def delete_shortcut(self, item_to_delete: QListWidgetItem): # item_to_delete는 QListWidgetItem
        """바로가기 삭제를 처리합니다."""
        if not item_to_delete:
            QMessageBox.information(self, "알림", "삭제할 항목이 유효하지 않습니다.")
            return

        data = item_to_delete.data(Qt.ItemDataRole.UserRole) # 전달된 항목에서 데이터 가져오기
        if not isinstance(data, dict) or "id" not in data or data.get("type") == ADD_ITEM_IDENTIFIER:
            return # 삭제 가능한 바로가기 항목이 아님

        shortcut_id_to_delete = data.get("id")
        shortcut_name = data.get("name", "이 바로가기")
        reply = QMessageBox.question(self, "삭제 확인", f"'{shortcut_name}' 바로가기를 삭제하시겠습니까?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            # 연관된 아이콘 파일 삭제 시도 (기본이 아닌 경우)
            icon_to_delete = data.get("icon_path")
            if icon_to_delete and os.path.exists(icon_to_delete) and os.path.basename(icon_to_delete) != DEFAULT_FAVICON_FILENAME :
                try:
                    os.remove(icon_to_delete)
                except OSError as e:
                    print(f"경고 (삭제): 아이콘 파일 {icon_to_delete}을(를) 제거할 수 없습니다: {e}")

            # 메인 바로가기 리스트에서 제거
            self.shortcuts = [s for s in self.shortcuts if s.get("id") != shortcut_id_to_delete]
            self.save_data()
            self.register_all_item_hotkeys() # 등록된 단축키 업데이트
            self.populate_list_for_current_tab() # UI 새로고침

    def open_url(self, url):
        """기본 웹 브라우저에서 URL을 엽니다."""
        try:
            webbrowser.open(url)
        except Exception as e:
            QMessageBox.warning(self, "URL 열기 오류", f"URL '{url}'을(를) 여는 데 실패했습니다: {e}")

    def register_item_hotkey(self, sc_data: dict):
        """단일 바로가기 항목에 대한 단축키를 등록합니다."""
        hotkey_str = sc_data.get("hotkey")
        url_to_open = sc_data.get("url")

        if not hotkey_str or not url_to_open: # 단축키나 URL이 정의되지 않음
            return

        # 이미 활성화된 동일한 단축키 문자열 재등록 방지 (예: 다른 항목에 의해)
        if hotkey_str in self.hotkey_actions:
            print(f"경고: 항목 '{sc_data.get('name')}'의 단축키 '{hotkey_str}'는 이미 등록되어 있습니다(다른 항목에 의해 가능). 건너뜁니다.")
            return

        # 항목 단축키가 전역 보이기/숨기기 단축키와 충돌하는 것 방지
        if hotkey_str == self.global_show_window_hotkey_str and self.global_show_window_hotkey_str :
            print(f"경고: 항목 단축키 '{hotkey_str}'가 전역 창 보이기 단축키와 충돌합니다. '{sc_data.get('name')}'의 항목 단축키는 등록되지 않습니다.")
            return

        try:
            # 이 특정 바로가기의 URL을 캡처하는 람다 생성
            callback_func = lambda u=url_to_open: webbrowser.open(u)
            # suppress=False: 키 조합이 다른 애플리케이션에서도 처리되도록 허용합니다.
            # 이 애플리케이션이 독점적으로 처리하게 하려면 True로 설정합니다.
            keyboard.add_hotkey(hotkey_str, callback_func, suppress=False)
            self.hotkey_actions[hotkey_str] = callback_func # 나중에 등록 해제를 위해 저장
            print(f"정보: 항목 '{sc_data.get('name')}'의 단축키 '{hotkey_str}' 등록됨.")
        except Exception as e: # 'keyboard' 라이브러리의 오류 포착 (예: 잘못된 단축키 형식)
            print(f"항목 '{sc_data.get('name')}'의 단축키 '{hotkey_str}' 등록 오류: {e}")

    def unregister_hotkey(self, hotkey_str: str):
        """특정 항목 단축키를 등록 해제합니다."""
        if not hotkey_str or hotkey_str not in self.hotkey_actions:
            return
        try:
            keyboard.remove_hotkey(hotkey_str)
            # print(f"정보: 항목 단축키 '{hotkey_str}' 등록 해제됨.") # 출력이 너무 많을 수 있음
        except Exception as e: # 'keyboard' 라이브러리에 등록되지 않은 경우 오류 포착
            print(f"항목 단축키 '{hotkey_str}' 등록 해제 오류: {e}")
        finally:
            if hotkey_str in self.hotkey_actions:
                del self.hotkey_actions[hotkey_str] # 우리 추적에서 제거

    def move_shortcut_to_category(self, shortcut_id: str, new_category_name: str):
        """탭 위로 드래그된 후 바로가기를 새 카테고리로 이동합니다."""
        found = False
        for sc_data in self.shortcuts:
            if sc_data.get("id") == shortcut_id:
                sc_data["category"] = new_category_name
                found = True
                break
        if found:
            self.save_data()
            # 현재 표시된 리스트를 새로고침합니다. "전체"였다면 업데이트됩니다.
            # 소스 또는 대상 카테고리였다면 역시 업데이트됩니다.
            self.populate_list_for_current_tab()
            # 대상 탭이 현재 탭이 아니라면, 사용자는 그 탭으로 전환할 때 변경 사항을 보게 됩니다.
        else:
            print(f"경고 (move_shortcut_to_category): 바로가기 ID {shortcut_id}를 찾을 수 없습니다.")


if __name__ == '__main__':
    # Linux에서 전역 단축키에 'keyboard'를 사용하는 경우 루트 권한 확인
    # 이는 저수준 키보드 훅에 대한 일반적인 요구 사항입니다.
    if sys.platform.startswith('linux') and os.geteuid() != 0:
        print("정보: Linux에서 'keyboard' 라이브러리는 전역 단축키를 위해 루트 권한이 필요할 수 있습니다.")
        print("정보: 단축키가 작동하지 않으면 'sudo python main.py'로 실행해보세요.")

    app = QApplication(sys.argv)
    # 마지막 창이 닫힐 때 앱이 종료되는 것을 방지 (트레이 아이콘 동작을 위해)
    app.setQuitOnLastWindowClosed(False)

    # --- 수정: 시작 시 디렉토리 생성 로직 간소화 ---
    # 파비콘 디렉토리가 없으면 생성합니다.
    # 스크립트가 실행되는 기본 디렉토리는 존재한다고 가정합니다.
    if not os.path.exists(FAVICON_DIR):
        try:
            os.makedirs(FAVICON_DIR, exist_ok=True)
        except OSError as e:
            # 데이터 디렉토리 없이는 진행할 수 없는 치명적 오류
            error_msg_box = QMessageBox()
            error_msg_box.setIcon(QMessageBox.Icon.Critical)
            error_msg_box.setWindowTitle("치명적 오류")
            error_msg_box.setText(f"파비콘 저장 폴더 '{FAVICON_DIR}' 생성 실패.\n{e}\n애플리케이션을 종료합니다.")
            error_msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
            error_msg_box.exec()
            sys.exit(1) # 디렉토리 생성 실패 시 종료
    # --- 수정 종료 ---

    window = ShortcutManagerWindow()

    # 초기 창 가시성 로직
    initial_show_behavior = True # 기본적으로 보이게 시작, 트레이로 최소화하여 시작하려면 False로 변경 가능
    if QSystemTrayIcon.isSystemTrayAvailable():
        if initial_show_behavior:
            window._execute_always_show_window_gui_thread() # 보이고 활성화
        # else: 창은 숨겨진 채 시작, 트레이나 전역 단축키로 접근 가능
    else: # 시스템 트레이 없음, 항상 창 표시
        print("정보: 시스템 트레이를 사용할 수 없습니다. 애플리케이션 창이 표시됩니다.")
        window._execute_always_show_window_gui_thread()


    sys.exit(app.exec())