import sys
import os
import json
import webbrowser
import requests # type: ignore
from bs4 import BeautifulSoup # type: ignore
from urllib.parse import urlparse, urljoin
import uuid
import time # For debouncing

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
    print("CRITICAL ERROR: 'keyboard' library not found. Please install it using 'pip install keyboard'")
    sys.exit(1)

APP_NAME = "ShortCutGroup"

_appdata_dir = os.getenv('APPDATA') or os.path.expanduser("~")
APP_DATA_BASE_DIR = os.path.join(_appdata_dir, APP_NAME)
SETTINGS_FILE = os.path.join(APP_DATA_BASE_DIR, "shortcuts.json")
FAVICON_DIR = os.path.join(APP_DATA_BASE_DIR, "favicons")

DEFAULT_FAVICON_FILENAME = "default_shortcut_icon.png"
HOTKEY_DEBOUNCE_TIME = 0.3 # Seconds

def get_appdata_favicon_path(filename):
    """Helper function to get the full path to a favicon file in the app data directory."""
    return os.path.join(FAVICON_DIR, filename)

DEFAULT_FAVICON = get_appdata_favicon_path(DEFAULT_FAVICON_FILENAME)

ADD_ITEM_IDENTIFIER = "___ADD_NEW_SHORTCUT_ITEM___"
ALL_CATEGORY_NAME = "All" # 한국어: "전체"
ADD_CATEGORY_TAB_TEXT = " + "
MIME_TYPE_SHORTCUT_ID = "application/x-shortcut-id"

def fetch_favicon(url):
    """
    Fetches a favicon for the given URL.
    Tries Google's S2 service first, then falls back to parsing HTML.
    Saves the icon to the FAVICON_DIR.
    Returns the path to the saved icon or DEFAULT_FAVICON if fetching fails.
    """
    if not os.path.exists(FAVICON_DIR):
        try:
            os.makedirs(FAVICON_DIR)
        except OSError as e:
            print(f"Warning (fetch_favicon): Could not create favicons directory {FAVICON_DIR}: {e}")
            return None # Cannot save if directory creation fails

    parsed_url = urlparse(url)
    current_effective_domain = parsed_url.netloc

    if not current_effective_domain:
        if parsed_url.scheme == 'file': # Local files don't have web favicons
            return None
        print(f"Warning (fetch_favicon): Could not parse domain from URL: {url}")
        return DEFAULT_FAVICON if os.path.exists(DEFAULT_FAVICON) else None

    # Create a safe filename base from the domain
    current_safe_filename_base = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in current_effective_domain)
    original_domain_for_s2 = current_effective_domain # Keep original domain for S2

    # Check if icon already exists with common extensions
    for ext in ['.png', '.ico', '.jpg', '.jpeg', '.gif', '.svg']:
        potential_path = get_appdata_favicon_path(f"{current_safe_filename_base}{ext}")
        if os.path.exists(potential_path):
            return potential_path

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    # 1. Try Google S2 Favicon service
    if original_domain_for_s2: # Only try if we have a domain
        try:
            google_s2_url = f"https://www.google.com/s2/favicons?sz=64&domain_url={original_domain_for_s2}"
            s2_response = requests.get(google_s2_url, headers=headers, timeout=5, stream=True)
            if s2_response.status_code == 200 and 'image' in s2_response.headers.get('content-type', '').lower():
                s2_favicon_path = get_appdata_favicon_path(f"{current_safe_filename_base}.png") # Assume png from S2
                with open(s2_favicon_path, 'wb') as f:
                    for chunk in s2_response.iter_content(8192):
                        f.write(chunk)
                if os.path.getsize(s2_favicon_path) > 100: # Basic check for empty/error image
                    return s2_favicon_path
                else: # S2 returned a tiny (likely error) image, delete it
                    try: os.remove(s2_favicon_path)
                    except OSError: pass # Ignore if deletion fails
        except Exception as e:
            print(f"Warning (fetch_favicon): Google S2 error for {original_domain_for_s2}: {e}")

    # 2. Fallback to fetching from the website itself
    try:
        temp_url = url
        # Add scheme if missing for web URLs
        if not parsed_url.scheme and not url.lower().startswith("file:"):
            temp_url = "http://" + url # Try http first

        if urlparse(temp_url).scheme == 'file': # Local file, no web favicon
            return DEFAULT_FAVICON if os.path.exists(DEFAULT_FAVICON) else None

        response = requests.get(temp_url, headers=headers, timeout=7, allow_redirects=True)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        # Update domain if redirected to a different one
        final_url_details = urlparse(response.url)
        if final_url_details.netloc and final_url_details.netloc != current_effective_domain:
            current_effective_domain = final_url_details.netloc
            current_safe_filename_base = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in current_effective_domain)
            # Check again if icon exists for the new domain
            for ext in ['.png', '.ico', '.jpg', '.jpeg', '.gif', '.svg']:
                potential_path = get_appdata_favicon_path(f"{current_safe_filename_base}{ext}")
                if os.path.exists(potential_path):
                    return potential_path

        soup = BeautifulSoup(response.content, 'html.parser')
        icon_url_from_html = None

        # Look for <link rel="icon" ...>
        for rel_value in ['icon', 'shortcut icon', 'apple-touch-icon', 'apple-touch-icon-precomposed']:
            for tag in soup.find_all('link', rel=rel_value, href=True):
                href = tag.get('href')
                if href and not href.startswith('data:'): # Ignore data URIs
                    icon_url_from_html = urljoin(response.url, href)
                    break
            if icon_url_from_html:
                break

        final_icon_url_to_fetch = icon_url_from_html

        # If no <link> tag found, try /favicon.ico
        if not final_icon_url_to_fetch:
            fallback_ico_url = urljoin(response.url, '/favicon.ico')
            try: # Check if /favicon.ico exists before trying to download
                if requests.head(fallback_ico_url, headers=headers, timeout=2, allow_redirects=True).status_code == 200:
                    final_icon_url_to_fetch = fallback_ico_url
            except requests.RequestException:
                pass # /favicon.ico doesn't exist or error checking

        if final_icon_url_to_fetch:
            icon_response = requests.get(final_icon_url_to_fetch, headers=headers, timeout=5, stream=True)
            icon_response.raise_for_status()

            content_type = icon_response.headers.get('content-type', '').lower()
            file_ext = '.ico' # Default extension
            if 'png' in content_type: file_ext = '.png'
            elif 'jpeg' in content_type or 'jpg' in content_type: file_ext = '.jpg'
            elif 'gif' in content_type: file_ext = '.gif'
            elif 'svg' in content_type: file_ext = '.svg'
            # Add more types if needed

            favicon_path = get_appdata_favicon_path(f"{current_safe_filename_base}{file_ext}")
            with open(favicon_path, 'wb') as f:
                for chunk in icon_response.iter_content(8192): # Stream download
                    f.write(chunk)
            return favicon_path

    except requests.exceptions.SSLError: # Handle SSL errors specifically for http fallback
        if url.startswith("https://"): # If original was https, try http
            print(f"Warning (fetch_favicon): SSL error for {url}, trying http.")
            return fetch_favicon(url.replace("https://", "http://", 1))
    except Exception as e:
        print(f"Warning (fetch_favicon): Main/icon request failed for {url}: {e}")

    return DEFAULT_FAVICON if os.path.exists(DEFAULT_FAVICON) else None


def load_icon_pixmap(icon_path, icon_size: QSize):
    """Loads an icon from a path and returns a QIcon scaled to icon_size."""
    if not icon_path : return QIcon() # Return empty QIcon if no path

    # Determine if path is absolute or relative to FAVICON_DIR
    final_path = icon_path
    if not os.path.isabs(icon_path):
        final_path = get_appdata_favicon_path(os.path.basename(icon_path))

    if os.path.exists(final_path):
        px = QPixmap(final_path)
        if not px.isNull():
            return QIcon(px.scaled(icon_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    return QIcon() # Return empty QIcon if loading fails or path doesn't exist

class ShortcutDialog(QDialog):
    """Dialog for adding or editing a shortcut."""
    def __init__(self, parent=None, shortcut_data=None, categories=None):
        super().__init__(parent)
        self.setWindowTitle("바로가기 추가" if not shortcut_data else "바로가기 편집") # 한국어
        self.setMinimumWidth(400)
        self.existing_categories = categories if categories else []

        self.layout = QVBoxLayout(self)

        # Name input
        self.name_label = QLabel("이름 (선택 사항):") # 한국어
        self.name_input = QLineEdit()
        self.layout.addWidget(self.name_label)
        self.layout.addWidget(self.name_input)

        # URL input
        self.url_label = QLabel("웹사이트 주소 (URL, 필수):") # 한국어
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com 또는 file:///C:/path/to/file.txt") # 한국어
        self.layout.addWidget(self.url_label)
        self.layout.addWidget(self.url_input)

        # Hotkey input
        self.hotkey_label = QLabel("단축키 (선택 사항):") # 한국어
        self.hotkey_input = HotkeyInputLineEdit(self) # Use the custom HotkeyInputLineEdit
        self.hotkey_input.setPlaceholderText("예: ctrl+shift+1 (영문 기준)") # 한국어
        self.layout.addWidget(self.hotkey_label)
        self.layout.addWidget(self.hotkey_input)

        # Category selection
        self.category_label = QLabel("카테고리:") # 한국어
        self.category_combo = QComboBox()
        if not self.existing_categories:
            self.category_combo.addItem("일반") # 한국어: "일반"
        else:
            self.category_combo.addItems(self.existing_categories)
        self.layout.addWidget(self.category_label)
        self.layout.addWidget(self.category_combo)


        # Dialog buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.try_accept) # Connect to custom validation
        self.button_box.rejected.connect(self.reject)
        self.layout.addWidget(self.button_box)

        # Populate fields if editing
        if shortcut_data:
            self.name_input.setText(shortcut_data.get("name", ""))
            self.url_input.setText(shortcut_data.get("url", ""))
            self.hotkey_input.set_hotkey_string(shortcut_data.get("hotkey", "")) # Use specific setter
            current_category = shortcut_data.get("category", "일반") # 한국어: "일반"
            if self.category_combo.findText(current_category) != -1:
                self.category_combo.setCurrentText(current_category)
            elif self.existing_categories : # If current_category not found, select first available
                self.category_combo.setCurrentIndex(0)


    def try_accept(self):
        """Validates the URL before accepting the dialog."""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "입력 오류", "웹사이트 주소(URL) 또는 파일 경로를 입력해주세요.") # 한국어
            self.url_input.setFocus()
            return

        parsed_url = urlparse(url)
        is_valid_scheme = parsed_url.scheme in ["http", "https", "file"]
        has_netloc_for_web = bool(parsed_url.netloc) and parsed_url.scheme in ["http", "https"]
        has_path_for_file = bool(parsed_url.path) and parsed_url.scheme == "file"
        # Allow schemeless if it looks like a domain (e.g., "example.com")
        is_schemeless_domain_like = not parsed_url.scheme and "." in url and not "/" in url.split("?")[0].split("#")[0]


        if not (is_valid_scheme and (has_netloc_for_web or has_path_for_file)) and not is_schemeless_domain_like:
            # More lenient check for things like "localhost:8000" or "domain.com/path" without scheme
            if not parsed_url.scheme and ('.' in url and ('/' in url or '.' in parsed_url.path) or "localhost" in url.lower() or (url.count(':') == 1 and url.split(':')[1].isdigit() and not parsed_url.scheme)):
                pass # Likely a valid local or schemeless URL
            else:
                QMessageBox.warning(self, "입력 오류", "유효한 웹 주소 또는 파일 경로 형식이 아닙니다.") # 한국어
                self.url_input.setFocus()
                return
        self.accept()


    def get_data(self):
        """Returns the shortcut data from the dialog inputs."""
        name = self.name_input.text().strip()
        url = self.url_input.text().strip()
        hotkey = self.hotkey_input.get_hotkey_string() # Use specific getter
        category = self.category_combo.currentText()

        # Auto-prefix URL with http:// if no scheme is present and it's not a file path
        parsed_url_check = urlparse(url)
        if not parsed_url_check.scheme and not url.lower().startswith("file:"):
            # Avoid double http:// if user types //domain.com
            if not (url.startswith("//") or url.startswith("http://") or url.startswith("https://")):
                url = "http://" + url
        elif url.lower().startswith("file:"): # Normalize file paths
            if url.lower().startswith("file://") and not url.lower().startswith("file:///"):
                url = "file:///" + url[len("file://"):] # Add the third slash for local files if only two
            else: # Handles "file:path"
                url = "file:///" + url[len("file:"):]
            url = url.replace("\\", "/") # Use forward slashes

        parsed_url = urlparse(url) # Re-parse after potential modification

        # Auto-generate name if not provided
        if not name:
            if parsed_url.scheme == "file":
                name = os.path.basename(parsed_url.path) or "파일 바로가기" # 한국어
            else: # For http/https
                name = parsed_url.netloc or os.path.basename(parsed_url.path) or "이름 없는 바로가기" # 한국어
            if not name or name.lower() in ["http:", "https:", "file:"]: # Further fallback if name is just scheme
                name = os.path.basename(parsed_url.path) if parsed_url.path else "이름 없는 바로가기" # 한국어

        return {"name": name, "url": url, "hotkey": hotkey, "category": category}

class DraggableListWidget(QListWidget):
    """A QListWidget that supports drag and drop of items for reordering."""
    item_dropped_signal = Signal(str, int, object) # item_id, new_row, list_widget_instance

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove) # Allow internal reordering

    def startDrag(self, supportedActions: Qt.DropAction):
        """Initiates a drag operation for the selected item."""
        selected_items = self.selectedItems()
        if not selected_items:
            return

        item_data = selected_items[0].data(Qt.ItemDataRole.UserRole)
        # Prevent dragging the "Add New Shortcut" item
        if isinstance(item_data, dict) and item_data.get("type") == ADD_ITEM_IDENTIFIER:
            return

        drag = QDrag(self)
        mime_data = QMimeData()
        item_id = item_data.get("id") # Assuming item_data is a dict with an 'id'

        if item_id:
            mime_data.setData(MIME_TYPE_SHORTCUT_ID, item_id.encode()) # Store item ID
            drag.setMimeData(mime_data)

            # Set a pixmap for the drag object (e.g., the item's icon)
            pixmap = selected_items[0].icon().pixmap(self.iconSize())
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))

            drag.exec(supportedActions, Qt.DropAction.MoveAction)

    def dropEvent(self, event: QMouseEvent):
        """Handles a drop event to reorder items."""
        if not event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
            event.ignore()
            return

        source_item_id = event.mimeData().data(MIME_TYPE_SHORTCUT_ID).data().decode()

        if event.source() == self: # Internal move
            super().dropEvent(event) # Let QListWidget handle the move
            if event.isAccepted() and event.dropAction() == Qt.DropAction.MoveAction :
                # Find the new row of the dropped item
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
            event.ignore() # Not an internal move


class HotkeyInputLineEdit(QLineEdit):
    """A QLineEdit specialized for capturing and displaying keyboard hotkeys."""
    # Map Qt.Key enum values to their string representations for 'keyboard' library
    QT_KEY_TO_STR_MAP = {
        Qt.Key.Key_Control: 'ctrl', Qt.Key.Key_Shift: 'shift', Qt.Key.Key_Alt: 'alt', Qt.Key.Key_Meta: 'win', # 'win' for Windows/Super key
        Qt.Key.Key_Return: 'enter', Qt.Key.Key_Enter: 'enter', Qt.Key.Key_Escape: 'esc', Qt.Key.Key_Space: 'space',
        Qt.Key.Key_Tab: 'tab', Qt.Key.Key_Backspace: 'backspace', Qt.Key.Key_Delete: 'delete',
        Qt.Key.Key_Up: 'up', Qt.Key.Key_Down: 'down', Qt.Key.Key_Left: 'left', Qt.Key.Key_Right: 'right',
        Qt.Key.Key_Home: 'home', Qt.Key.Key_End: 'end', Qt.Key.Key_PageUp: 'pageup', Qt.Key.Key_PageDown: 'pagedown',
        Qt.Key.Key_Insert: 'insert', Qt.Key.Key_CapsLock: 'caps lock', Qt.Key.Key_ScrollLock: 'scroll lock',
        Qt.Key.Key_NumLock: 'num lock', Qt.Key.Key_Print: 'print screen', Qt.Key.Key_Pause: 'pause',
        # Punctuation and symbols - these might vary with 'keyboard' library expectations
        Qt.Key.Key_Plus: '+', Qt.Key.Key_Minus: '-', Qt.Key.Key_Equal: '=',
        Qt.Key.Key_BracketLeft: '[', Qt.Key.Key_BracketRight: ']', Qt.Key.Key_Backslash: '\\',
        Qt.Key.Key_Semicolon: ';', Qt.Key.Key_Apostrophe: '\'', Qt.Key.Key_Comma: ',',
        Qt.Key.Key_Period: '.', Qt.Key.Key_Slash: '/', Qt.Key.Key_QuoteLeft: '`', # Grave accent
    }
    # Add F1-F24 keys
    for i in range(1, 25):
        QT_KEY_TO_STR_MAP[getattr(Qt.Key, f'Key_F{i}')] = f'f{i}'


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True) # Prevent manual text editing
        self.setPlaceholderText("여기를 클릭하고 키를 누르세요 (예: Ctrl+Shift+X)") # 한국어
        self._current_modifier_keys = set() # Stores Qt.Key values for active modifiers
        self._current_non_modifier_key = None # Stores Qt.Key value for the main key
        self._current_non_modifier_key_str = None # Stores string representation of the main key

    def keyPressEvent(self, event: QKeyEvent):
        """Handles key press events to capture the hotkey combination."""
        key = event.key()
        text = event.text() # For character keys

        if key == Qt.Key.Key_unknown:
            event.ignore()
            return

        # Modifier keys
        if key in [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta]:
            self._current_modifier_keys.add(key)
        # Clear keys
        elif key == Qt.Key.Key_Backspace or key == Qt.Key.Key_Delete:
            self.clear_hotkey()
            event.accept()
            return
        # Non-modifier keys
        else:
            self._current_non_modifier_key = key
            self._current_non_modifier_key_str = self._qt_key_to_display_string(key, text)

        self._update_display_text()
        event.accept()

    def keyReleaseEvent(self, event: QKeyEvent):
        """Handles key release events, primarily for modifiers."""
        key = event.key()

        if event.isAutoRepeat(): # Ignore auto-repeated release events
            event.accept()
            return

        if key in [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta]:
            if key in self._current_modifier_keys:
                self._current_modifier_keys.remove(key)
        elif key == self._current_non_modifier_key:
            # The main key is considered "released" for the purpose of capturing a new one
            # if no modifiers are held. If modifiers are still held, we keep the main key.
            pass # Don't clear _current_non_modifier_key yet, wait for focus out or explicit clear

        # If all keys are released (no modifiers and no main key string), clear the display
        if not self._current_modifier_keys and not self._current_non_modifier_key_str:
            self.clear_hotkey() # Or if explicitly cleared by backspace/delete

        # self._update_display_text() # No need to update display on release unless clearing
        event.accept()

    def _qt_key_to_display_string(self, qt_key_code, text_from_event):
        """Converts a Qt.Key code to a string suitable for display and the 'keyboard' library."""
        # Check our explicit map first
        if qt_key_code in self.QT_KEY_TO_STR_MAP:
            return self.QT_KEY_TO_STR_MAP[qt_key_code]

        # Alphabetic keys (A-Z)
        if Qt.Key.Key_A <= qt_key_code <= Qt.Key.Key_Z:
            return chr(qt_key_code).lower() # Use lowercase 'a'-'z'

        # Numeric keys (0-9 from top row)
        if Qt.Key.Key_0 <= qt_key_code <= Qt.Key.Key_9:
            return chr(qt_key_code)

        # For other keys, if text() gives a single non-space char, use it.
        # This helps with international keyboards or symbols not in QT_KEY_TO_STR_MAP.
        if text_from_event and len(text_from_event) == 1 and not text_from_event.isspace():
            # Specific handling for numpad keys if text() is ambiguous
            if qt_key_code == Qt.Key.Key_Asterisk: return "*" # Numpad multiply
            if qt_key_code == Qt.Key.Key_Plus and text_from_event == "+": return "+" # Numpad plus
            if qt_key_code == Qt.Key.Key_Minus and text_from_event == "-": return "-" # Numpad minus
            if qt_key_code == Qt.Key.Key_Period and text_from_event == ".": return "." # Numpad decimal
            if qt_key_code == Qt.Key.Key_Slash and text_from_event == "/": return "/" # Numpad divide
            return text_from_event.lower() # Fallback to text if available

        return None # Could not determine string for this key

    def _update_display_text(self):
        """Updates the QLineEdit text to show the current hotkey combination."""
        parts = []
        # Add modifiers in a consistent order (Ctrl, Alt, Shift, Meta/Win)
        if Qt.Key.Key_Control in self._current_modifier_keys:
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Control])
        if Qt.Key.Key_Alt in self._current_modifier_keys:
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Alt])
        if Qt.Key.Key_Shift in self._current_modifier_keys:
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Shift])
        if Qt.Key.Key_Meta in self._current_modifier_keys: # Windows/Super key
            parts.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Meta])

        if self._current_non_modifier_key_str:
            parts.append(self._current_non_modifier_key_str)

        self.setText(" + ".join(parts) if parts else "")

    def get_hotkey_string(self):
        """Returns the captured hotkey as a string (e.g., "ctrl+shift+a")."""
        return self.text().replace(" ", "") # Remove spaces for 'keyboard' library format

    def set_hotkey_string(self, hotkey_str):
        """Sets the hotkey from a string (e.g., "ctrl+shift+a")."""
        self.clear_hotkey() # Clear any current internal state
        if not hotkey_str:
            self.setText("")
            return

        parts = hotkey_str.lower().split('+')
        processed_parts_for_display = []

        for part_raw in parts:
            part = part_raw.strip()
            found_modifier = False
            # Check if part is a known modifier string
            for qt_key, str_val in self.QT_KEY_TO_STR_MAP.items():
                if str_val == part and qt_key in [Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta]:
                    self._current_modifier_keys.add(qt_key)
                    processed_parts_for_display.append(str_val) # Add to display list
                    found_modifier = True
                    break
            if not found_modifier:
                self._current_non_modifier_key_str = part # Assume it's the main key
                # Try to find a Qt.Key for it (optional, mainly for internal consistency if needed later)
                for qt_key, str_val in self.QT_KEY_TO_STR_MAP.items():
                    if str_val == part: self._current_non_modifier_key = qt_key; break
                if not self._current_non_modifier_key: # Try letters/numbers
                    if len(part) == 1:
                        if 'a' <= part <= 'z': self._current_non_modifier_key = Qt.Key(ord(part.upper()))
                        elif '0' <= part <= '9': self._current_non_modifier_key = Qt.Key(ord(part))
                processed_parts_for_display.append(part)

        # Ensure consistent order for display (Ctrl, Alt, Shift, Meta, Key)
        display_order = []
        temp_modifiers = set(self._current_modifier_keys) # operate on a copy

        if Qt.Key.Key_Control in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Control]); temp_modifiers.remove(Qt.Key.Key_Control)
        if Qt.Key.Key_Alt in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Alt]); temp_modifiers.remove(Qt.Key.Key_Alt)
        if Qt.Key.Key_Shift in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Shift]); temp_modifiers.remove(Qt.Key.Key_Shift)
        if Qt.Key.Key_Meta in temp_modifiers: display_order.append(self.QT_KEY_TO_STR_MAP[Qt.Key.Key_Meta]); temp_modifiers.remove(Qt.Key.Key_Meta)
        if self._current_non_modifier_key_str: display_order.append(self._current_non_modifier_key_str)

        self.setText(" + ".join(display_order))


    def clear_hotkey(self):
        """Clears the current hotkey."""
        self._current_modifier_keys.clear()
        self._current_non_modifier_key = None
        self._current_non_modifier_key_str = None
        self.setText("")

    def focusOutEvent(self, event: QFocusEvent):
        """
        When focus is lost, if no main key was pressed with modifiers,
        clear the modifiers. This prevents "Ctrl+" from sticking if
        only Ctrl was pressed and then focus was lost.
        """
        # if not self._current_non_modifier_key_str and self._current_modifier_keys:
        #     self.clear_hotkey() # Clear if only modifiers were pressed and focus is lost
        super().focusOutEvent(event) # Call base class method

class GlobalHotkeySettingsDialog(QDialog):
    """Dialog for setting the global 'show/hide window' hotkey."""
    def __init__(self, parent, current_hotkey):
        super().__init__(parent)
        self.main_window = parent # Store reference to main window to check its shortcuts
        self.setWindowTitle("전역 단축키 설정") # 한국어
        self.setMinimumWidth(400)

        layout = QVBoxLayout(self)

        self.info_label = QLabel(f"현재 '창 보이기/숨기기' 단축키: <b>{current_hotkey or '설정 안됨'}</b>") # 한국어
        layout.addWidget(self.info_label)

        input_label = QLabel("새 단축키 (아래 칸에 키를 직접 누르세요):") # 한국어
        layout.addWidget(input_label)
        self.hotkey_input_widget = HotkeyInputLineEdit(self) # Use the specialized widget
        self.hotkey_input_widget.set_hotkey_string(current_hotkey) # Pre-fill with current
        layout.addWidget(self.hotkey_input_widget)

        # Buttons layout
        button_layout = QHBoxLayout()
        self.default_button = QPushButton("기본값으로") # 한국어
        self.default_button.clicked.connect(self.set_to_default)
        button_layout.addWidget(self.default_button)
        button_layout.addStretch()

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.try_save)
        self.button_box.rejected.connect(self.reject)
        button_layout.addWidget(self.button_box)

        layout.addLayout(button_layout)

    def set_to_default(self):
        """Sets the hotkey input to the default value."""
        self.hotkey_input_widget.set_hotkey_string("ctrl+shift+x") # Default hotkey

    def try_save(self):
        """Validates and saves the new global hotkey."""
        new_hotkey_str_raw = self.hotkey_input_widget.get_hotkey_string() # Get from specialized widget

        if not new_hotkey_str_raw: # If cleared (empty string)
            reply = QMessageBox.question(self, "단축키 없음",
                                         "단축키를 비우시겠습니까? (창 보이기/숨기기 기능을 사용하지 않음)", # 한국어
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.new_hotkey = "" # Set to empty string to signify no hotkey
                self.accept()
            else:
                return # User cancelled clearing
        else:
            self.new_hotkey = new_hotkey_str_raw
            # Check for conflicts with existing item shortcuts in the main window
            for sc_data in self.main_window.shortcuts: # Access main_window's shortcuts
                if sc_data.get("hotkey") == self.new_hotkey:
                    QMessageBox.warning(self, "단축키 충돌", # 한국어
                                        f"단축키 '{self.new_hotkey}'은(는) '{sc_data.get('name')}' 바로가기에서 이미 사용 중입니다.\n다른 단축키를 지정해주세요.")
                    return
            self.accept() # No conflict, or user confirmed clearing

    def get_new_hotkey(self):
        """Returns the new hotkey string, or None if dialog was cancelled before save."""
        return getattr(self, 'new_hotkey', None)


class ShortcutManagerWindow(QMainWindow):
    """Main application window for managing shortcuts."""
    # Signals for thread-safe GUI updates from the 'keyboard' library thread
    request_toggle_window_visibility_signal = Signal()
    request_always_show_window_signal = Signal()


    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setGeometry(200, 200, 800, 600) # Default size and position

        self.shortcuts: list[dict] = [] # List of shortcut data dictionaries
        self.categories_order: list[str] = [] # Order of categories for tabs
        self.hotkey_actions: dict = {} # Stores registered hotkeys for items {hotkey_str: callback}

        self._highlighted_tab_index = -1 # For drag-over tab highlighting
        self._default_tab_stylesheet = "" # Store default stylesheet
        self.last_selected_valid_category_index = 0 # Tracks last user-selected category tab
        self._category_to_select_after_update = None # Temp storage for category to select after UI update

        self.global_show_window_hotkey_str = "ctrl+shift+x" # Default global hotkey
        self.is_global_show_hotkey_registered = False
        self._last_global_hotkey_time = 0 # Timestamp for debouncing global hotkey

        self._init_default_icon()
        self.init_ui_layout()
        self.create_menus()
        self.load_data_and_register_hotkeys() # This will also register the global hotkey
        self.init_tray_icon()
        self.setWindowIcon(self.create_app_icon())
        self.setAcceptDrops(True) # For dragging shortcuts between categories

        # Connect signals for cross-thread communication
        self.request_toggle_window_visibility_signal.connect(self._execute_toggle_window_visibility_gui_thread)
        self.request_always_show_window_signal.connect(self._execute_always_show_window_gui_thread)


    def _init_default_icon(self):
        """
        Initializes or creates a default application/shortcut icon if one doesn't exist.
        Uses Pillow (PIL) to create a simple icon.
        """
        self.default_icon_available = os.path.exists(DEFAULT_FAVICON)
        if not self.default_icon_available:
            if not os.path.exists(FAVICON_DIR): # Ensure favicons dir exists before trying to save
                # This should have been created at startup, but double-check
                try: os.makedirs(FAVICON_DIR, exist_ok=True)
                except OSError: return # Cannot proceed if dir creation fails

            try:
                from PIL import Image, ImageDraw, ImageFont # type: ignore
                s=48;img=Image.new('RGBA',(s,s),(0,0,0,0));d=ImageDraw.Draw(img)
                d.ellipse((2,2,s-3,s-3),fill='dodgerblue',outline='white',width=1)
                try:font=ImageFont.truetype("arial.ttf",int(s*0.5))
                except IOError:font=ImageFont.load_default() # Fallback font
                txt="S";
                # PIL textbbox for better centering
                if hasattr(d,'textbbox'):
                    bb=d.textbbox((0,0),txt,font=font); w,h=bb[2]-bb[0],bb[3]-bb[1]
                    x=(s-w)/2-bb[0]; y=(s-h)/2-bb[1]
                else: # Fallback for older Pillow versions
                    text_size_result=d.textsize(txt,font=font) if hasattr(d,'textsize') else (font.getsize(txt) if hasattr(font,'getsize') else (0,0))
                    w,h=text_size_result[0],text_size_result[1]
                    x,y=(s-w)/2,(s-h)/2
                d.text((x,y),txt,fill="white",font=font)
                img.save(DEFAULT_FAVICON)
                self.default_icon_available=True
            except ImportError:
                print("INFO: Pillow library not found. Cannot create default icon. Please install with 'pip install Pillow'.")
            except Exception as e:
                print(f"WARNING: Error creating default icon: {e}")

    def create_app_icon(self):
        """Creates the main application icon, using default if available."""
        if self.default_icon_available and os.path.exists(DEFAULT_FAVICON):
            ico = QIcon(DEFAULT_FAVICON)
            if not ico.isNull(): return ico
        # Fallback to a standard system icon or a generic pixmap
        std_ico = self.style().standardIcon(QStyle.StandardPixmap.SP_ApplicationIcon) # Or SP_ComputerIcon, SP_DriveHDIcon
        if not std_ico.isNull(): return std_ico
        # Absolute fallback: a simple colored pixmap
        px = QPixmap(32,32); px.fill(Qt.GlobalColor.cyan); return QIcon(px)

    def get_fallback_qicon(self, target_size: QSize) -> QIcon:
        """
        Returns a fallback QIcon (default app icon or a placeholder)
        scaled to the target_size.
        """
        if self.default_icon_available and os.path.exists(DEFAULT_FAVICON):
            px_def = QPixmap(DEFAULT_FAVICON)
            if not px_def.isNull():
                return QIcon(px_def.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        # Try a standard file icon from style
        px_std = self.style().standardPixmap(QStyle.StandardPixmap.SP_FileIcon, None, self) # Or SP_DesktopIcon
        if not px_std.isNull():
            return QIcon(px_std.scaled(target_size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

        # Absolute fallback: draw a simple placeholder (e.g., a cross)
        px = QPixmap(target_size)
        px.fill(Qt.GlobalColor.lightGray)
        p = QPainter(px)
        pen = p.pen()
        pen.setColor(QColor(Qt.GlobalColor.darkGray))
        pen.setWidth(2)
        p.setPen(pen)
        # Draw a simple 'X' or similar placeholder
        p.drawLine(int(px.width()*0.2), int(px.height()*0.2), int(px.width()*0.8), int(px.height()*0.8))
        p.drawLine(int(px.width()*0.8), int(px.height()*0.2), int(px.width()*0.2), int(px.height()*0.8))
        p.end()
        return QIcon(px)


    def init_ui_layout(self):
        """Initializes the main UI layout with category tabs."""
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(5, 5, 5, 5) # Small margins

        self.category_tabs = QTabWidget()
        tab_bar_font = QFont()
        tab_bar_font.setPointSize(10) # Adjust tab font size if needed
        self.category_tabs.setFont(tab_bar_font)
        self.category_tabs.tabBar().setMinimumHeight(30) # Ensure tab bar is tall enough

        # Store default stylesheet for easy reset after highlighting
        self._default_tab_stylesheet = """
            QTabBar::tab {
                min-width: 80px; /* Minimum width for each tab */
                padding: 5px;
                margin-right: 1px; /* Small gap between tabs */
                border: 1px solid #C4C4C3; /* Light grey border */
                border-bottom: none; /* No bottom border for inactive tabs */
                background-color: #F0F0F0; /* Light grey background */
            }
            QTabBar::tab:hover {
                background-color: #E0E0E0; /* Slightly darker on hover */
            }
            QTabBar::tab:selected {
                background-color: white; /* White background for selected tab */
                border-color: #9B9B9B; /* Darker border for selected */
                border-bottom: 2px solid white; /* Overlap pane border */
            }
            QTabBar::tab:last { /* Special style for the '+' (add category) tab */
                background-color: #D8D8D8;
                font-weight: bold;
                color: #333;
                border-left: 1px solid #C4C4C3; /* Separator line */
            }
            QTabBar::tab:last:hover {
                background-color: #cceeff; /* Light blue hover for '+' */
            }
            QTabWidget::pane { /* The content area of the tab widget */
                border: 1px solid #C4C4C3;
                top: -1px; /* Overlap with tab bar bottom border */
                background: white;
            }
        """
        self.category_tabs.setStyleSheet(self._default_tab_stylesheet)

        self.category_tabs.setMovable(True) # Allow reordering tabs
        self.category_tabs.tabBar().tabMoved.connect(self.on_tab_moved)
        self.category_tabs.setTabsClosable(False) # Categories deleted via context menu
        self.category_tabs.currentChanged.connect(self.on_category_changed)

        # Context menu for category tabs (rename, delete)
        self.category_tabs.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.category_tabs.tabBar().customContextMenuRequested.connect(self.show_category_context_menu)

        self.category_tabs.setAcceptDrops(True) # Allow dropping shortcuts onto tabs
        main_layout.addWidget(self.category_tabs)

    def create_menus(self):
        """Creates the main menu bar."""
        menu_bar = self.menuBar()
        if not menu_bar: # Ensure menubar exists, especially on some platforms/styles
            menu_bar = QMenuBar(self)
            self.setMenuBar(menu_bar)

        # File Menu
        file_menu = menu_bar.addMenu("파일(&F)") # 한국어
        exit_action = QAction("종료(&X)", self) # 한국어
        exit_action.triggered.connect(self.quit_application)
        file_menu.addAction(exit_action)

        # Settings Menu
        settings_menu = menu_bar.addMenu("설정(&S)") # 한국어
        refresh_icons_action = QAction("아이콘 전체 새로고침(&R)", self) # 한국어
        refresh_icons_action.triggered.connect(self.refresh_all_icons_action)
        settings_menu.addAction(refresh_icons_action)

        global_hotkey_action = QAction("전역 단축키 설정(&G)...", self) # 한국어
        global_hotkey_action.triggered.connect(self.open_global_hotkey_settings_dialog)
        settings_menu.addAction(global_hotkey_action)


    def open_global_hotkey_settings_dialog(self):
        """Opens the dialog to configure the global show/hide window hotkey."""
        dialog = GlobalHotkeySettingsDialog(self, self.global_show_window_hotkey_str)
        if dialog.exec():
            new_hotkey = dialog.get_new_hotkey() # This can be "" if user chose to clear
            if new_hotkey is not None: # Proceed only if dialog wasn't cancelled (Save was clicked)
                if new_hotkey != self.global_show_window_hotkey_str:
                    self.unregister_current_global_show_window_hotkey()
                    self.global_show_window_hotkey_str = new_hotkey
                    if self.register_new_global_show_window_hotkey(): # Attempt to register the new one
                        self.save_data() # Save only if successfully registered
                        QMessageBox.information(self, "단축키 변경 완료", # 한국어
                                                f"창 보이기/숨기기 단축키가 '{new_hotkey}'(으)로 설정되었습니다." if new_hotkey else "창 보이기/숨기기 단축키가 해제되었습니다.")
                    else: # Registration failed (e.g., invalid hotkey string for 'keyboard' lib)
                        QMessageBox.critical(self, "단축키 등록 실패", # 한국어
                                             f"단축키 '{new_hotkey}' 등록에 실패했습니다. 이전 설정을 유지합니다.")
                        # Attempt to revert to the previously saved hotkey from settings file
                        previous_valid_hotkey = self.load_specific_setting("global_show_window_hotkey", "ctrl+shift+x") # Load from file or default
                        self.global_show_window_hotkey_str = previous_valid_hotkey
                        self.register_new_global_show_window_hotkey() # Re-register the (hopefully) valid previous one

    def _on_global_show_hotkey_triggered(self):
        """
        Callback for the global show/hide window hotkey.
        This method is called by the 'keyboard' library thread.
        Uses a signal to delegate GUI updates to the main GUI thread.
        Implements debouncing to prevent rapid firing.
        """
        current_time = time.time()
        if (current_time - self._last_global_hotkey_time) > HOTKEY_DEBOUNCE_TIME:
            self._last_global_hotkey_time = current_time
            # Emit a signal to execute the GUI update in the main GUI thread.
            # print(f"Debug: Global hotkey triggered, emitting signal at {current_time}") # Debug print
            self.request_toggle_window_visibility_signal.emit()
        # else:
            # print(f"Debug: Global hotkey debounced at {current_time}") # Debug print


    @Slot()
    def _execute_always_show_window_gui_thread(self):
        """
        Ensures the window is shown, focused, and brought to front.
        This slot is executed in the main GUI thread.
        """
        # print("Debug: _execute_always_show_window_gui_thread called") # Debug print
        # Temporarily set StayOnTopHint to ensure it comes to front
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.showNormal() # Ensure it's not minimized
        self.raise_()     # Bring to top of window stack
        self.activateWindow() # Request focus
        QApplication.processEvents() # Process events to help focus change take effect
        # Remove StayOnTopHint after showing so it behaves normally afterwards
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
        self.show() # Ensure visibility after flags are reset
        QApplication.processEvents() # Another processEvents for good measure


    @Slot()
    def _execute_toggle_window_visibility_gui_thread(self):
        """
        Toggles window visibility based on its current state and focus.
        If hidden or not focused, it shows, focuses, and brings to front.
        If visible and focused, it hides.
        This slot is executed in the main GUI thread.
        """
        # print("Debug: _execute_toggle_window_visibility_gui_thread called") # Debug print
        is_currently_active = self.isActiveWindow()
        is_visible_and_not_minimized = self.isVisible() and not self.isMinimized()

        # print(f"Debug: is_currently_active: {is_currently_active}, is_visible_and_not_minimized: {is_visible_and_not_minimized}") # Debug print

        if is_visible_and_not_minimized:
            if is_currently_active:
                # Window is visible, not minimized, AND focused: Hide it
                # print("Debug: Hiding window") # Debug print
                self.hide()
            else:
                # Window is visible, not minimized, BUT NOT focused: Bring to front and focus
                # print("Debug: Showing, raising, activating (was visible but not active)") # Debug print
                self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
                self.showNormal() # Ensure it's not minimized
                self.raise_()
                self.activateWindow() # Request to activate
                QApplication.processEvents() # Process events to help focus change take effect
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
                self.show() # Ensure visibility after flags are reset and focus attempt
                QApplication.processEvents()
        else:
            # Window is hidden or minimized: Show it, bring to front, and focus
            # print("Debug: Showing, raising, activating (was hidden or minimized)") # Debug print
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            self.showNormal()
            self.raise_()
            self.activateWindow() # Request to activate
            QApplication.processEvents() # Process events
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowStaysOnTopHint)
            self.show() # Ensure visibility
            QApplication.processEvents()


    def register_new_global_show_window_hotkey(self):
        """Registers the global show/hide window hotkey using the 'keyboard' library."""
        if self.global_show_window_hotkey_str: # Only if a hotkey string is set
            try:
                # suppress=False allows the key event to pass through to other applications if needed,
                # though for a global show/hide, suppression is often desired.
                # trigger_on_release=False (default) means it triggers on press.
                keyboard.add_hotkey(self.global_show_window_hotkey_str,
                                    self._on_global_show_hotkey_triggered,
                                    suppress=False) # Set suppress=True if you want to block the hotkey from other apps
                self.is_global_show_hotkey_registered = True
                print(f"INFO: Global toggle window hotkey '{self.global_show_window_hotkey_str}' registered.")
                return True
            except Exception as e: # Broad exception for 'keyboard' library issues
                print(f"ERROR: Failed to register global toggle window hotkey '{self.global_show_window_hotkey_str}': {e}")
                self.is_global_show_hotkey_registered = False
                return False
        else: # No hotkey string set
            self.is_global_show_hotkey_registered = False
            print("INFO: Global toggle window hotkey is empty. Not registering.")
            return True # Considered "successful" as there's nothing to register

    def unregister_current_global_show_window_hotkey(self):
        """Unregisters the current global show/hide window hotkey."""
        if self.is_global_show_hotkey_registered and self.global_show_window_hotkey_str:
            try:
                keyboard.remove_hotkey(self.global_show_window_hotkey_str)
                print(f"INFO: Global toggle window hotkey '{self.global_show_window_hotkey_str}' unregistered.")
            except Exception as e: # Catch errors if hotkey wasn't actually registered by 'keyboard'
                print(f"WARNING: Could not unregister global toggle window hotkey '{self.global_show_window_hotkey_str}': {e}")
            finally:
                self.is_global_show_hotkey_registered = False
        elif not self.global_show_window_hotkey_str: # If hotkey string was empty
            self.is_global_show_hotkey_registered = False


    def refresh_all_icons_action(self):
        """Action to re-fetch all favicons for all shortcuts."""
        reply = QMessageBox.question(self, "아이콘 새로고침 확인", # 한국어
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

                # Attempt to remove old icon file before fetching new one
                parsed_url = urlparse(url)
                domain = parsed_url.netloc
                if domain: # Try to remove old icon if domain exists
                    safe_domain_name = "".join(c if c.isalnum() or c in ['.', '-'] else '_' for c in domain)
                    for ext in ['.png', '.ico', '.jpg', '.jpeg', '.gif', '.svg']: # Check all possible extensions
                        cached_path = get_appdata_favicon_path(f"{safe_domain_name}{ext}")
                        if os.path.exists(cached_path) and os.path.basename(cached_path) != DEFAULT_FAVICON_FILENAME: # Don't delete the default icon
                            try:
                                os.remove(cached_path)
                            except OSError as e:
                                failed_to_delete_count +=1
                                print(f"Warning (Refresh): Failed to remove {cached_path}: {e}")

                # Fetch new icon
                new_icon_path = fetch_favicon(url) # This will try to get a new one
                if sc_data.get("icon_path") != new_icon_path: # Update if different (or if old was None)
                    sc_data["icon_path"] = new_icon_path
                    updated_count += 1
                QApplication.processEvents() # Keep UI responsive during long operation

            self.save_data() # Save changes to icon paths
            self.populate_list_for_current_tab() # Refresh the view
            QApplication.restoreOverrideCursor()
            msg = f"{len(self.shortcuts)}개 바로 가기 중 {updated_count}개의 아이콘 정보가 업데이트되었습니다." # 한국어
            if failed_to_delete_count > 0:
                msg += f"\n{failed_to_delete_count}개의 기존 아이콘 파일 삭제에 실패했습니다." # 한국어
            QMessageBox.information(self, "새로고침 완료", msg) # 한국어

    def _clear_tab_highlight(self):
        """Resets tab stylesheet to default, clearing any drag-over highlight."""
        if self._highlighted_tab_index != -1:
            self.category_tabs.setStyleSheet(self._default_tab_stylesheet)
            self._highlighted_tab_index = -1

    def dragEnterEvent(self, event: QMouseEvent):
        """Handles drag enter events for dropping shortcuts onto category tabs."""
        if event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QMouseEvent):
        """Highlights a category tab when a shortcut is dragged over it."""
        self._clear_tab_highlight() # Clear previous highlight
        if event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
            tab_bar = self.category_tabs.tabBar()
            pos_in_tab_bar = tab_bar.mapFromGlobal(QCursor.pos()) # Get cursor pos relative to tab bar
            tab_idx = tab_bar.tabAt(pos_in_tab_bar)

            if tab_idx != -1 and tab_bar.rect().contains(pos_in_tab_bar): # Check if cursor is over a valid tab
                cat_name = self.category_tabs.tabText(tab_idx)
                if cat_name != ALL_CATEGORY_NAME and cat_name != ADD_CATEGORY_TAB_TEXT: # Don't highlight "All" or "+"
                    # Highlight the tab under the cursor
                    # Note: nth-child is 1-based in CSS
                    highlight_style = f"QTabBar::tab:nth-child({tab_idx + 1}) {{ background-color: lightblue; border: 1px solid blue; }}"
                    self.category_tabs.setStyleSheet(self._default_tab_stylesheet + highlight_style)
                    self._highlighted_tab_index = tab_idx
                    event.acceptProposedAction()
                    return
            event.acceptProposedAction() # Accept even if not over a droppable tab, dropEvent will handle final check
        else:
            event.ignore()

    def dropEvent(self, event: QMouseEvent):
        """Handles dropping a shortcut onto a category tab to change its category."""
        self._clear_tab_highlight() # Always clear highlight on drop

        tab_bar = self.category_tabs.tabBar()
        pos_in_tab_bar = tab_bar.mapFromGlobal(QCursor.pos())

        if tab_bar.rect().contains(pos_in_tab_bar): # Check if drop occurred within the tab bar area
            tab_idx = tab_bar.tabAt(pos_in_tab_bar)
            if tab_idx != -1:
                cat_name = self.category_tabs.tabText(tab_idx)
                # Ensure it's a user category (not "All" or "+")
                if cat_name != ALL_CATEGORY_NAME and cat_name != ADD_CATEGORY_TAB_TEXT:
                    if event.mimeData().hasFormat(MIME_TYPE_SHORTCUT_ID):
                        sc_id = event.mimeData().data(MIME_TYPE_SHORTCUT_ID).data().decode()
                        self.move_shortcut_to_category(sc_id, cat_name)
                        event.acceptProposedAction()
                        return
        event.ignore() # Ignore if not a valid drop target

    def dragLeaveEvent(self, event: QMouseEvent):
        """Clears tab highlight when a drag operation leaves the widget area."""
        self._clear_tab_highlight()
        event.accept()


    def on_tab_moved(self, from_index: int, to_index: int):
        """Handles reordering of category tabs by the user."""
        tab_bar = self.category_tabs.tabBar()
        num_tabs = tab_bar.count()

        # Find current positions of "All" and "+" tabs
        add_tab_text_current_idx = -1
        all_tab_current_idx = -1
        for i in range(num_tabs):
            if tab_bar.tabText(i) == ADD_CATEGORY_TAB_TEXT:
                add_tab_text_current_idx = i
            if tab_bar.tabText(i) == ALL_CATEGORY_NAME:
                all_tab_current_idx = i

        # Ensure "All" tab is always first, if it exists
        if all_tab_current_idx != -1 and all_tab_current_idx != 0:
            tab_bar.blockSignals(True) # Prevent recursive calls or unwanted signal emissions
            tab_bar.moveTab(all_tab_current_idx, 0)
            tab_bar.blockSignals(False)

        # Re-evaluate num_tabs and "+" tab index as "All" might have moved
        num_tabs = tab_bar.count()
        add_tab_text_current_idx = -1 # Reset and find again
        for i in range(num_tabs): # Find "+" tab again
            if tab_bar.tabText(i) == ADD_CATEGORY_TAB_TEXT:
                add_tab_text_current_idx = i
                break

        # Ensure "+" tab is always last, if it exists
        if add_tab_text_current_idx != -1 and add_tab_text_current_idx != num_tabs - 1:
            tab_bar.blockSignals(True)
            tab_bar.moveTab(add_tab_text_current_idx, num_tabs - 1)
            tab_bar.blockSignals(False)

        # Update categories_order based on new tab positions (excluding "All" and "+")
        # Assumes "All" is at index 0 and "+" is at the last index
        new_order = [self.category_tabs.tabText(i) for i in range(1, self.category_tabs.count() - 1)]
        if self.categories_order != new_order:
            self.categories_order = new_order
            self.save_data()


    def init_tray_icon(self):
        """Initializes the system tray icon and its context menu."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.create_app_icon()) # Use the same app icon

        menu = QMenu(self)
        show_act = QAction("창 열기", self); show_act.triggered.connect(self._execute_always_show_window_gui_thread); menu.addAction(show_act) # 한국어
        add_act = QAction("바로가기 추가...", self); add_act.triggered.connect(self.add_shortcut_from_tray); menu.addAction(add_act) # 한국어
        menu.addSeparator()
        quit_act = QAction("종료", self); quit_act.triggered.connect(self.quit_application); menu.addAction(quit_act) # 한국어

        self.tray_icon.setContextMenu(menu)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()

        # Connect activation signal (click/double-click on tray icon)
        self.tray_icon.activated.connect(self.on_tray_icon_activated)

    def on_tray_icon_activated(self, reason):
        """Handles activation of the tray icon (e.g., click)."""
        # Show window on left-click (Trigger) or double-click
        if reason in [QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick]:
            self._execute_always_show_window_gui_thread() # Always show, don't toggle from tray click

    def add_shortcut_from_tray(self):
        """Shows the window and then opens the add shortcut dialog."""
        self._execute_always_show_window_gui_thread() # Ensure window is visible
        self.add_shortcut() # Call the main add_shortcut method

    def closeEvent(self, event):
        """Overrides the window close event to hide to tray instead of quitting."""
        event.ignore()  # Prevent the window from actually closing
        self.hide()     # Hide the window

        # Show a message if the tray icon is available and visible
        if hasattr(self, 'tray_icon') and self.tray_icon and self.tray_icon.isVisible() and QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.showMessage(
                APP_NAME,
                "앱이 트레이에서 실행 중입니다.", # 한국어: "App is running in tray."
                self.create_app_icon(), # Use app icon for message
                2000 # milliseconds
            )
        # Note: self.quit_application() is NOT called here, so the app continues running.

    def quit_application(self):
        """Properly quits the application, unregistering hotkeys and hiding tray icon."""
        self.unregister_current_global_show_window_hotkey() # Unregister global hotkey
        for key in list(self.hotkey_actions.keys()): # Unregister all item hotkeys
            self.unregister_hotkey(key)

        if hasattr(self, 'tray_icon') and self.tray_icon:
            self.tray_icon.hide() # Hide tray icon before quitting

        QApplication.instance().quit()

    def load_specific_setting(self, key_name, default_value):
        """Loads a specific key from the settings JSON file."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return data.get(key_name, default_value)
            except json.JSONDecodeError: # Handle corrupted JSON
                return default_value
        return default_value # File not found

    def load_data_and_register_hotkeys(self):
        """Loads shortcuts and settings from JSON, then registers hotkeys."""
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                self.categories_order = data.get("categories_order", [])
                self.shortcuts = data.get("shortcuts", [])
                self.global_show_window_hotkey_str = data.get("global_show_window_hotkey", "ctrl+shift+x") # Load global hotkey

                # Data integrity checks and migration for older versions
                needs_save = False
                max_prio_val = 0.0 # To help assign new priorities if needed
                for idx, sc in enumerate(self.shortcuts):
                    if "id" not in sc or not sc["id"]: # Ensure unique ID
                        sc["id"] = str(uuid.uuid4())
                        needs_save = True
                    if "category" not in sc: # Ensure category
                        sc["category"] = self.categories_order[0] if self.categories_order else "일반" # 한국어: "일반"
                        needs_save = True
                    if "priority" not in sc: # Ensure priority for ordering
                        sc["priority"] = float(idx + 1.0) # Assign simple incremental priority
                        needs_save = True
                    current_prio = sc.get("priority", 0.0)
                    if not isinstance(current_prio, float): # Ensure priority is float
                        try: sc['priority'] = float(current_prio); needs_save = True
                        except ValueError: sc['priority'] = float(idx + 1.0); needs_save = True

                    max_prio_val = max(max_prio_val, sc.get("priority", 0.0))


                if needs_save:
                    self.save_data() # Save if any corrections were made

            except Exception as e: # Broad exception for file read/JSON parse errors
                QMessageBox.warning(self, "데이터 로드 오류", f"{SETTINGS_FILE} 로드 오류: {e}.\n기본 설정으로 시작합니다.") # 한국어
                self.shortcuts, self.categories_order = [], []
                self.global_show_window_hotkey_str = "ctrl+shift+x" # Reset to default on error
        else: # No settings file exists, use defaults
             self.global_show_window_hotkey_str = "ctrl+shift+x"


        # Ensure there's at least one category if all else fails (e.g., "기본")
        if not self.categories_order and not self.shortcuts: # Only if both are empty
            self.categories_order = ["기본"] # 한국어: "기본" (Default)

        # Clean up reserved names from categories_order just in case
        self.categories_order = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]

        self.update_category_tabs() # Create/update tabs based on loaded/default data
        self.register_all_item_hotkeys() # Register hotkeys for loaded items
        self.register_new_global_show_window_hotkey() # Register the global show/hide hotkey

        # Select a valid tab after loading
        current_idx = self.category_tabs.currentIndex()
        if self.category_tabs.count() > 0: # If there are any tabs
            first_valid_tab_index = 0 # Usually "All"
            # Handle edge case where "All" might not be first (shouldn't happen with tab move logic)
            if self.category_tabs.tabText(first_valid_tab_index) == ADD_CATEGORY_TAB_TEXT and self.category_tabs.count() > 1:
                first_valid_tab_index = 1

            if current_idx == -1 or self.category_tabs.tabText(current_idx) == ADD_CATEGORY_TAB_TEXT:
                self.category_tabs.setCurrentIndex(first_valid_tab_index)
            else: # If a valid tab is already current, ensure its content is populated
                self.on_category_changed(current_idx) # Trigger populate for the current valid tab
        elif not self.categories_order: # No categories, but "All" and "+" exist
             self.populate_list_for_current_tab() # Populate "All" tab (will show "Add New")


    def save_data(self):
        """Saves shortcuts and settings to a JSON file."""
        if not os.path.exists(APP_DATA_BASE_DIR):
            try:
                os.makedirs(APP_DATA_BASE_DIR)
            except OSError as e:
                QMessageBox.critical(self, "데이터 저장 오류", f"데이터 폴더 '{APP_DATA_BASE_DIR}' 생성 실패: {e}") # 한국어
                return

        # Save only user-defined categories in categories_order
        user_cats = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]

        # Sort shortcuts by priority before saving to maintain order on next load (if list widget sort isn't perfect)
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
            QMessageBox.critical(self, "데이터 저장 오류", f"{SETTINGS_FILE} 파일 저장 실패: {e}") # 한국어

    def update_category_tabs(self):
        """Updates the category tabs based on self.categories_order."""
        # Disconnect signal to prevent issues during clear/repopulation
        try:
            self.category_tabs.currentChanged.disconnect(self.on_category_changed)
        except RuntimeError: # If not connected, it's fine
            pass

        # Try to preserve selection
        intended_selection_text = None
        if hasattr(self, '_category_to_select_after_update') and self._category_to_select_after_update:
            intended_selection_text = self._category_to_select_after_update
        else: # If not explicitly set, try to keep current or last valid
            current_idx_before_clear = self.category_tabs.currentIndex()
            if current_idx_before_clear != -1:
                temp_text = self.category_tabs.tabText(current_idx_before_clear)
                if temp_text not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]:
                    intended_selection_text = temp_text
                # Fallback to last known valid selected category if current is "All" or "+"
                elif 0 <= self.last_selected_valid_category_index < self.category_tabs.count() and \
                     self.category_tabs.tabText(self.last_selected_valid_category_index) not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]:
                     intended_selection_text = self.category_tabs.tabText(self.last_selected_valid_category_index)


        self.category_tabs.clear() # Remove all existing tabs

        # Add "All" tab first
        all_list_widget = self._create_new_list_widget()
        self.category_tabs.addTab(all_list_widget, ALL_CATEGORY_NAME)

        # Add user-defined category tabs
        for cat_name in self.categories_order:
            cat_list_widget = self._create_new_list_widget()
            self.category_tabs.addTab(cat_list_widget, cat_name)

        # Add "+" tab (for adding new categories) at the end
        self.category_tabs.addTab(QWidget(), ADD_CATEGORY_TAB_TEXT) # Placeholder QWidget for "+"

        # Reconnect signal
        self.category_tabs.currentChanged.connect(self.on_category_changed)

        # Restore selection
        idx_to_select = 0 # Default to "All" (index 0)
        if intended_selection_text:
            for i in range(self.category_tabs.count() -1): # -1 to exclude "+" tab from selection restoration logic
                if self.category_tabs.tabText(i) == intended_selection_text:
                    idx_to_select = i
                    break

        # If current index is already the one we want to select, and it's not "+", manually trigger populate
        if self.category_tabs.currentIndex() == idx_to_select and self.category_tabs.tabText(idx_to_select) != ADD_CATEGORY_TAB_TEXT :
            self.populate_list_for_current_tab()
        self.category_tabs.setCurrentIndex(idx_to_select) # This will trigger on_category_changed if index actually changes


    def _create_new_list_widget(self) -> DraggableListWidget:
        """Helper to create and configure a new DraggableListWidget for a tab."""
        list_widget = DraggableListWidget()
        list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        list_widget.setViewMode(QListView.ViewMode.IconMode)
        list_widget.setIconSize(QSize(48, 48)) # Default icon size for shortcuts
        list_widget.setFlow(QListView.Flow.LeftToRight) # Arrange items left-to-right
        list_widget.setWrapping(True) # Wrap items to next line if space runs out
        list_widget.setResizeMode(QListView.ResizeMode.Adjust) # Adjust layout on resize
        list_widget.setUniformItemSizes(False) # Allow items to have different widths based on text
        list_widget.setGridSize(QSize(100, 80)) # Approximate item cell size (width, height)
        list_widget.setSpacing(10) # Spacing between items
        list_widget.setWordWrap(True) # Wrap text within item labels

        list_widget.itemActivated.connect(self.on_item_activated) # Double click / Enter
        list_widget.item_dropped_signal.connect(self.on_shortcut_item_reordered)

        # Context menu for shortcut items (edit, delete)
        list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        list_widget.customContextMenuRequested.connect(self.show_shortcut_context_menu)
        return list_widget

    def on_category_changed(self, index):
        """Handles changing of the selected category tab."""
        if index == -1: return # Should not happen if tabs exist

        current_tab_text = self.category_tabs.tabText(index)

        if current_tab_text == ADD_CATEGORY_TAB_TEXT:
            self.add_category() # This will update tabs and potentially change index
            # If after adding, the current tab is still "+", revert to last valid or "All"
            if self.category_tabs.tabText(self.category_tabs.currentIndex()) == ADD_CATEGORY_TAB_TEXT:
                valid_fallback_idx = 0 # Default to "All"
                if 0 <= self.last_selected_valid_category_index < (self.category_tabs.count() -1) : # -1 to exclude new "+"
                    valid_fallback_idx = self.last_selected_valid_category_index
                self.category_tabs.setCurrentIndex(valid_fallback_idx)
        else:
            self.last_selected_valid_category_index = index # Update last valid selected tab
            self.populate_list_for_current_tab()

    def get_current_category_name(self):
        """Returns the name of the currently selected category tab."""
        idx = self.category_tabs.currentIndex()
        if idx != -1:
            current_text = self.category_tabs.tabText(idx)
            # If "+" tab is somehow selected for data purposes, use last valid actual category
            if current_text == ADD_CATEGORY_TAB_TEXT:
                if 0 <= self.last_selected_valid_category_index < self.category_tabs.count() -1: # -1 to exclude "+"
                    return self.category_tabs.tabText(self.last_selected_valid_category_index)
                return ALL_CATEGORY_NAME # Fallback if no other valid tab was selected
            return current_text
        return ALL_CATEGORY_NAME # Default if no tab selected (shouldn't happen)

    def populate_list_for_current_tab(self):
        """Populates the list widget of the current tab with shortcuts."""
        current_tab_index = self.category_tabs.currentIndex()
        if current_tab_index == -1: return

        current_tab_category_name = self.category_tabs.tabText(current_tab_index)
        current_list_widget = self.category_tabs.widget(current_tab_index)

        # Ensure it's a DraggableListWidget (not the QWidget for "+")
        if not isinstance(current_list_widget, DraggableListWidget):
            if current_list_widget is not None and hasattr(current_list_widget, 'clear'):
                current_list_widget.clear() # Clear if it's the "+" tab's QWidget (empty anyway)
            return

        current_list_widget.clear()
        icon_size = current_list_widget.iconSize() # Get configured icon size
        fallback_qicon = self.get_fallback_qicon(icon_size) # Get a pre-scaled fallback

        # Filter shortcuts for the current category
        items_to_display = []
        if current_tab_category_name == ALL_CATEGORY_NAME:
            items_to_display = list(self.shortcuts) # Show all
        else:
            items_to_display = [sc for sc in self.shortcuts if sc.get("category") == current_tab_category_name]

        # Sort items by priority for consistent display
        items_to_display.sort(key=lambda x: x.get('priority', float('inf')))

        for sc_data in items_to_display:
            name = sc_data.get("name", "N/A")
            item = QListWidgetItem(name)
            current_icon = fallback_qicon # Default to fallback

            icon_path_from_data = sc_data.get("icon_path")
            if icon_path_from_data:
                loaded_user_icon = load_icon_pixmap(icon_path_from_data, icon_size) # Load and scale
                if not loaded_user_icon.isNull():
                    current_icon = loaded_user_icon

            item.setIcon(current_icon)
            item.setData(Qt.ItemDataRole.UserRole, sc_data) # Store full data dict
            item.setToolTip(f"{name}\nURL: {sc_data.get('url')}\n단축키: {sc_data.get('hotkey') or '없음'}") # 한국어: "없음"
            current_list_widget.addItem(item)

        # Add the "Add New Shortcut" item at the end of each list
        add_shortcut_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder) # Using a folder icon for "add"
        # Or: QStyle.StandardPixmap.SP_FileIcon, SP_ToolBarHorizontalExtensionButton
        add_item = QListWidgetItem(add_shortcut_icon, "새 바로가기") # 한국어
        add_item.setData(Qt.ItemDataRole.UserRole, {"type": ADD_ITEM_IDENTIFIER, "id": ADD_ITEM_IDENTIFIER}) # Special type
        add_item.setToolTip("새로운 바로가기를 추가합니다.") # 한국어
        add_item.setFlags(add_item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled) # Not draggable
        current_list_widget.addItem(add_item)


    def register_all_item_hotkeys(self):
        """Unregisters all existing item hotkeys and re-registers from self.shortcuts."""
        for key in list(self.hotkey_actions.keys()): # Iterate over a copy of keys
            self.unregister_hotkey(key)
        self.hotkey_actions.clear() # Clear the tracking dictionary

        for sc_data in self.shortcuts:
            self.register_item_hotkey(sc_data)

    def on_item_activated(self, item: QListWidgetItem):
        """Handles activation (double-click/Enter) of a list item."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, dict):
            if data.get("type") == ADD_ITEM_IDENTIFIER:
                self.add_shortcut() # Call the add shortcut dialog
            elif "url" in data:
                self.open_url(data["url"])

    def on_shortcut_item_reordered(self, dropped_item_id: str, new_row_in_listwidget: int, source_list_widget: DraggableListWidget):
        """
        Handles reordering of shortcuts within a list via drag and drop.
        Recalculates priority based on visual order in the current list view.
        """
        # Get data of all items currently in the list (excluding "Add New")
        current_view_items_data = []
        for i in range(source_list_widget.count()):
            item = source_list_widget.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(item_data, dict) and item_data.get("type") != ADD_ITEM_IDENTIFIER:
                current_view_items_data.append(item_data)

        if not current_view_items_data: return # Should not happen if items exist

        # Find the actual new row of the dropped item within this filtered list
        # (The new_row_in_listwidget from signal might include "Add New" if it was visible)
        actual_new_row_in_filtered_list = -1
        moved_item_actual_data = None
        for idx, data_dict in enumerate(current_view_items_data):
            if data_dict.get("id") == dropped_item_id:
                actual_new_row_in_filtered_list = idx
                moved_item_actual_data = data_dict
                break

        if actual_new_row_in_filtered_list == -1 or not moved_item_actual_data:
            print(f"Warning (Reorder): Dropped item ID {dropped_item_id} not found in current view.")
            return

        new_priority = 0.0
        num_items_in_view = len(current_view_items_data)

        if num_items_in_view == 1: # Only one item
            new_priority = 1.0
        elif actual_new_row_in_filtered_list == 0: # Moved to the beginning
            priority_after = current_view_items_data[1].get('priority', 2.0) # Priority of the item now at index 1
            new_priority = priority_after / 2.0
            if new_priority <= 0: new_priority = priority_after - 0.5 # Avoid zero/negative if P_after is small
            if new_priority <=0: new_priority = 0.001 # Absolute minimum positive
        elif actual_new_row_in_filtered_list == num_items_in_view - 1: # Moved to the end
            priority_before = current_view_items_data[num_items_in_view - 2].get('priority', 0.0) # Priority of item now before it
            new_priority = priority_before + 1.0
        else: # Moved in between two items
            priority_before = current_view_items_data[actual_new_row_in_filtered_list - 1].get('priority', 0.0)
            priority_after = current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority', 0.0)
            new_priority = (priority_before + priority_after) / 2.0

        # Check for priority conflicts (e.g., if division results in an existing priority or too close)
        epsilon = 0.00001 # Small tolerance for float comparison
        conflict = False
        # Check against new neighbors
        if actual_new_row_in_filtered_list > 0 and abs(new_priority - current_view_items_data[actual_new_row_in_filtered_list - 1].get('priority',0.0)) < epsilon:
            conflict = True
        if actual_new_row_in_filtered_list < num_items_in_view - 1 and abs(new_priority - current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority',0.0)) < epsilon:
            conflict = True

        if conflict: # If new priority is too close to an adjacent one, adjust slightly or re-prioritize
            # This is a simple conflict resolution; a more robust one might re-number all priorities
            # For now, try to nudge it.
            if actual_new_row_in_filtered_list > 0: # If there's an item before
                new_priority = current_view_items_data[actual_new_row_in_filtered_list - 1].get('priority',0.0) + 0.5
            elif num_items_in_view > 1 : # If it's first and there's an item after
                 new_priority = current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority',0.0) - 0.5
                 if new_priority <=0 : new_priority = current_view_items_data[actual_new_row_in_filtered_list + 1].get('priority',0.0) / 2.0 # Ensure positive
            else: # Single item, or still problematic
                new_priority = 1.0 # Fallback

        # Update the priority in the main self.shortcuts list
        for sc_dict_global in self.shortcuts:
            if sc_dict_global.get("id") == dropped_item_id:
                sc_dict_global['priority'] = new_priority
                break

        self.save_data()
        self.populate_list_for_current_tab() # Re-sort based on new priorities and re-populate


    def add_shortcut(self):
        """Handles adding a new shortcut via dialog."""
        user_selectable_cats = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]
        dlg_cats = user_selectable_cats if user_selectable_cats else ["일반"] # 한국어: "일반"

        # Determine a sensible default category for the dialog
        initial_category_for_dialog = "일반" # 한국어: "일반"
        current_tab_name = self.get_current_category_name() # Get name of current tab (could be "All")
        if current_tab_name != ALL_CATEGORY_NAME and current_tab_name != ADD_CATEGORY_TAB_TEXT:
            initial_category_for_dialog = current_tab_name # Pre-select current category
        elif dlg_cats: # If current is "All", pick the first user category if available
            initial_category_for_dialog = dlg_cats[0]
        # If dlg_cats is empty and current is "All", it will default to "일반" in ShortcutDialog

        # Create a temporary shortcut_data with the pre-selected category for the dialog
        temp_shortcut_data = {"category": initial_category_for_dialog}

        dlg = ShortcutDialog(self, shortcut_data=temp_shortcut_data, categories=dlg_cats) # Pass None for shortcut_data to indicate new
        if dlg.exec():
            new_data = dlg.get_data()
            new_data["id"] = str(uuid.uuid4()) # Generate new unique ID

            # Assign priority (e.g., max existing + 1, or 1.0 if first item)
            max_priority = 0.0
            if self.shortcuts: # If there are existing shortcuts
                max_priority = max(sc.get("priority", 0.0) for sc in self.shortcuts)
            new_data["priority"] = max_priority + 1.0

            # Hotkey conflict check
            if new_data["hotkey"]: # Only check if a hotkey is actually entered
                # Check against other item hotkeys
                if any(s.get("hotkey") == new_data["hotkey"] for s in self.shortcuts):
                    QMessageBox.warning(self, "단축키 중복", f"단축키 '{new_data['hotkey']}'은(는) 이미 다른 바로가기에서 사용 중입니다.") # 한국어
                    return # Abort adding
                # Check against global show/hide hotkey
                if new_data["hotkey"] == self.global_show_window_hotkey_str and self.global_show_window_hotkey_str:
                    QMessageBox.warning(self, "단축키 충돌", f"단축키 '{new_data['hotkey']}'은(는) 창 보이기 전역 단축키로 사용 중입니다.") # 한국어
                    return # Abort adding

            # Fetch Icon
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            new_data["icon_path"] = fetch_favicon(new_data["url"])
            QApplication.restoreOverrideCursor()

            # Add to shortcuts list
            self.shortcuts.append(new_data)

            # If the chosen category was "일반" and it's not in categories_order, add it.
            chosen_cat = new_data["category"]
            if chosen_cat == "일반" and "일반" not in self.categories_order: # 한국어: "일반"
                self.categories_order.append("일반") # 한국어: "일반"
                # No need to immediately update_category_tabs here, will be done after save

            self.save_data()
            self.register_all_item_hotkeys() # Re-register all hotkeys as a new one is added

            # Update UI: select the category tab of the newly added item
            self._category_to_select_after_update = chosen_cat
            self.update_category_tabs() # This will repopulate tabs and select the correct one
            self._category_to_select_after_update = None # Clear the marker

    def add_category(self):
        """Handles adding a new category via input dialog."""
        name, ok = QInputDialog.getText(self, "새 카테고리", "카테고리 이름을 입력하세요:") # 한국어
        if ok and name:
            name = name.strip() # Remove leading/trailing whitespace
            if not name:
                QMessageBox.warning(self, "입력 오류", "카테고리 이름은 비워둘 수 없습니다.") # 한국어
                return
            if name in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT] or name in self.categories_order:
                QMessageBox.warning(self, "입력 오류", f"'{name}'은(는) 사용 중이거나 사용할 수 없는 카테고리 이름입니다.") # 한국어
                return

            self.categories_order.append(name)
            self.save_data()
            self._category_to_select_after_update = name # Mark for selection after tabs update
            self.update_category_tabs() # This will create the new tab and select it
            self._category_to_select_after_update = None # Clear marker
        elif self.category_tabs.tabText(self.category_tabs.currentIndex()) == ADD_CATEGORY_TAB_TEXT:
            # User cancelled the dialog when "+" tab was active, revert to last valid or "All"
            valid_fallback_idx = 0 # Default to "All"
            if 0 <= self.last_selected_valid_category_index < (self.category_tabs.count() -1) : # -1 because "+" is still there
                valid_fallback_idx = self.last_selected_valid_category_index
            self.category_tabs.setCurrentIndex(valid_fallback_idx)


    def delete_category_action(self, category_name_to_delete):
        """Action to delete a category and move its shortcuts."""
        if category_name_to_delete == ALL_CATEGORY_NAME or category_name_to_delete == ADD_CATEGORY_TAB_TEXT:
            QMessageBox.warning(self, "삭제 불가", f"'{category_name_to_delete}' 카테고리는 삭제할 수 없습니다.") # 한국어
            return

        reply = QMessageBox.question(self, "카테고리 삭제 확인", # 한국어
                                     f"'{category_name_to_delete}' 카테고리를 삭제하시겠습니까?\n\n이 카테고리의 모든 바로가기는 첫 번째 사용 가능한 카테고리 또는 '일반' 카테고리로 이동됩니다.",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            # Determine fallback category
            target_fallback_category = "일반" # 한국어: "일반" Default fallback
            if self.categories_order: # If other user categories exist
                remaining_categories = [cat for cat in self.categories_order if cat != category_name_to_delete]
                if remaining_categories:
                    target_fallback_category = remaining_categories[0] # Use the first remaining one
                # If no other categories remain, items will go to "일반"
                # and "일반" will be added to categories_order if not present.

            # Move items from deleted category to fallback
            for sc in self.shortcuts:
                if sc.get("category") == category_name_to_delete:
                    sc["category"] = target_fallback_category

            # Remove category from order
            if category_name_to_delete in self.categories_order:
                self.categories_order.remove(category_name_to_delete)

            # If fallback was "일반" and it's not in categories_order but items now use it, add it.
            if target_fallback_category == "일반" and \
               "일반" not in self.categories_order and \
               any(sc.get("category") == "일반" for sc in self.shortcuts): # 한국어
                self.categories_order.append("일반") # 한국어

            self.save_data()
            self.update_category_tabs() # Refresh UI, will select a valid tab

            # Ensure a valid tab is selected after deletion and its content populated
            if self.category_tabs.count() > 0 :
                 if self.category_tabs.tabText(self.category_tabs.currentIndex()) == ADD_CATEGORY_TAB_TEXT:
                     self.category_tabs.setCurrentIndex(0) # Select "All" if "+" was somehow selected
                 else: # This will trigger populate_list_for_current_tab via on_category_changed
                    self.on_category_changed(self.category_tabs.currentIndex())
            else: # Should not happen, "All" and "+" always exist conceptually
                 self.populate_list_for_current_tab()


    def show_category_context_menu(self, position: QPoint):
        """Shows context menu for category tabs (e.g., delete category)."""
        tab_bar = self.category_tabs.tabBar()
        tab_index = tab_bar.tabAt(position)
        if tab_index != -1:
            category_name = self.category_tabs.tabText(tab_index)
            # No context menu for "All" or "+" tabs
            if category_name == ALL_CATEGORY_NAME or category_name == ADD_CATEGORY_TAB_TEXT:
                return

            menu = QMenu(self)
            delete_action = QAction(f"'{category_name}' 카테고리 삭제", self) # 한국어
            # Use lambda to pass the category_name to the action
            delete_action.triggered.connect(lambda checked=False, name=category_name: self.delete_category_action(name))
            menu.addAction(delete_action)
            menu.exec(tab_bar.mapToGlobal(position)) # Show menu at global cursor position

    def show_shortcut_context_menu(self, position: QPoint):
        """Shows context menu for shortcut items (edit, delete)."""
        current_tab_idx = self.category_tabs.currentIndex()
        if current_tab_idx == -1: return

        list_widget = self.category_tabs.widget(current_tab_idx)
        if not isinstance(list_widget, DraggableListWidget): return # Not a list widget (e.g. "+" tab)

        item = list_widget.itemAt(position) # Get item at cursor position
        if item:
            item_data = item.data(Qt.ItemDataRole.UserRole)
            # Ensure it's a real shortcut item, not the "Add New" item
            if isinstance(item_data, dict) and item_data.get("type") != ADD_ITEM_IDENTIFIER and "id" in item_data:
                menu = QMenu(self)
                edit_action = QAction("편집", self) # 한국어
                # Pass the specific item (it) to edit_shortcut_context
                edit_action.triggered.connect(lambda checked=False, it=item: self.edit_shortcut_context(it))
                menu.addAction(edit_action)

                delete_action = QAction("삭제", self) # 한국어
                # Pass the specific item (it) to delete_shortcut_context
                delete_action.triggered.connect(lambda checked=False, it=item: self.delete_shortcut_context(it))
                menu.addAction(delete_action)

                menu.exec(list_widget.mapToGlobal(position))

    def edit_shortcut_context(self, item: QListWidgetItem):
        """Called from context menu to edit a shortcut. `item` is the QListWidgetItem."""
        if not item: return
        # Optional: for visual feedback, make the item current if not already
        # item.listWidget().setCurrentItem(item)
        self.edit_shortcut(item) # Pass the specific item to the main edit_shortcut method

    def delete_shortcut_context(self, item: QListWidgetItem):
        """Called from context menu to delete a shortcut. `item` is the QListWidgetItem."""
        if not item: return
        # Optional: for visual feedback
        # item.listWidget().setCurrentItem(item)
        self.delete_shortcut(item) # Pass the specific item to the main delete_shortcut method


    def edit_shortcut(self, item_to_edit: QListWidgetItem): # item_to_edit is the QListWidgetItem
        """Handles editing an existing shortcut."""
        if not item_to_edit:
            QMessageBox.information(self, "알림", "편집할 항목이 유효하지 않습니다.") # 한국어
            return

        data_item = item_to_edit.data(Qt.ItemDataRole.UserRole) # Get data from the passed item
        if not isinstance(data_item, dict) or "id" not in data_item or data_item.get("type") == ADD_ITEM_IDENTIFIER:
            # This implies item_to_edit was not a valid shortcut item
            return

        shortcut_id_to_edit = data_item.get("id")
        # Find the original full shortcut data from self.shortcuts using the ID
        original_shortcut_data = next((s for s in self.shortcuts if s.get("id") == shortcut_id_to_edit), None)

        if not original_shortcut_data:
            QMessageBox.critical(self, "편집 오류", "편집할 바로가기 원본 데이터를 찾을 수 없습니다.") # 한국어
            return

        user_selectable_cats = [c for c in self.categories_order if c not in [ALL_CATEGORY_NAME, ADD_CATEGORY_TAB_TEXT]]
        dlg_cats = user_selectable_cats if user_selectable_cats else ["일반"] # 한국어: "일반"

        dlg = ShortcutDialog(self, shortcut_data=original_shortcut_data, categories=dlg_cats)
        if dlg.exec():
            new_data = dlg.get_data()
            new_data["id"] = shortcut_id_to_edit # Preserve original ID
            new_data["priority"] = original_shortcut_data.get("priority") # Preserve original priority

            # Hotkey conflict check (only if hotkey changed)
            if new_data["hotkey"] and new_data["hotkey"] != original_shortcut_data.get("hotkey"):
                # Check against other items
                if any(s.get("hotkey") == new_data["hotkey"] and s.get("id") != shortcut_id_to_edit for s in self.shortcuts):
                    QMessageBox.warning(self, "단축키 중복", f"단축키 '{new_data['hotkey']}'은(는) 이미 다른 바로가기에서 사용 중입니다.") # 한국어
                    return
                # Check against global hotkey
                if new_data["hotkey"] == self.global_show_window_hotkey_str and self.global_show_window_hotkey_str:
                    QMessageBox.warning(self, "단축키 충돌", f"단축키 '{new_data['hotkey']}'은(는) 창 보이기 전역 단축키로 사용 중입니다.") # 한국어
                    return

            chosen_cat = new_data["category"]
            # If "일반" was chosen and it's not in categories_order, add it.
            if chosen_cat == "일반" and "일반" not in self.categories_order: # 한국어: "일반"
                self.categories_order.append("일반") # 한국어: "일반"

            # Icon update only if URL changed
            if new_data["url"] != original_shortcut_data.get("url"):
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                # Try to remove old icon file if it exists and is not the default
                old_icon_path = original_shortcut_data.get("icon_path")
                if old_icon_path and os.path.exists(old_icon_path) and os.path.basename(old_icon_path) != DEFAULT_FAVICON_FILENAME :
                    try: os.remove(old_icon_path)
                    except OSError as e: print(f"Warning (Edit): Failed to remove old icon {old_icon_path}: {e}")
                new_data["icon_path"] = fetch_favicon(new_data["url"])
                QApplication.restoreOverrideCursor()
            else: # URL didn't change, keep the old icon path
                new_data["icon_path"] = original_shortcut_data.get("icon_path")

            # Update the shortcut in the main list
            for i, s_loop_var in enumerate(self.shortcuts):
                if s_loop_var.get("id") == shortcut_id_to_edit:
                    self.shortcuts[i] = new_data
                    break

            self.save_data()
            self.register_all_item_hotkeys() # Re-register all hotkeys as one might have changed

            # Update UI and select the (potentially new) category tab
            self._category_to_select_after_update = chosen_cat
            self.update_category_tabs() # This will refresh tabs and select the correct one
            self._category_to_select_after_update = None # Clear marker

    def delete_shortcut(self, item_to_delete: QListWidgetItem): # item_to_delete is the QListWidgetItem
        """Handles deleting a shortcut."""
        if not item_to_delete:
            QMessageBox.information(self, "알림", "삭제할 항목이 유효하지 않습니다.") # 한국어
            return

        data = item_to_delete.data(Qt.ItemDataRole.UserRole) # Get data from the passed item
        if not isinstance(data, dict) or "id" not in data or data.get("type") == ADD_ITEM_IDENTIFIER:
            return # Not a deletable shortcut item

        shortcut_id_to_delete = data.get("id")
        shortcut_name = data.get("name", "이 바로가기") # 한국어: "이 바로가기" (This shortcut)
        reply = QMessageBox.question(self, "삭제 확인", f"'{shortcut_name}' 바로가기를 삭제하시겠습니까?", # 한국어
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            # Try to delete associated icon file (if not the default)
            icon_to_delete = data.get("icon_path")
            if icon_to_delete and os.path.exists(icon_to_delete) and os.path.basename(icon_to_delete) != DEFAULT_FAVICON_FILENAME :
                try:
                    os.remove(icon_to_delete)
                except OSError as e:
                    print(f"Warning (Delete): Could not remove icon file {icon_to_delete}: {e}")

            # Remove from the main list of shortcuts
            self.shortcuts = [s for s in self.shortcuts if s.get("id") != shortcut_id_to_delete]
            self.save_data()
            self.register_all_item_hotkeys() # Update registered hotkeys
            self.populate_list_for_current_tab() # Refresh the UI

    def open_url(self, url):
        """Opens a URL in the default web browser."""
        try:
            webbrowser.open(url)
        except Exception as e:
            QMessageBox.warning(self, "URL 열기 오류", f"URL '{url}'을(를) 여는 데 실패했습니다: {e}") # 한국어

    def register_item_hotkey(self, sc_data: dict):
        """Registers a hotkey for a single shortcut item."""
        hotkey_str = sc_data.get("hotkey")
        url_to_open = sc_data.get("url")

        if not hotkey_str or not url_to_open: # No hotkey or URL defined
            return

        # Prevent re-registering the same hotkey string if already active (e.g. by another item)
        if hotkey_str in self.hotkey_actions:
            print(f"Warning: Hotkey '{hotkey_str}' for item '{sc_data.get('name')}' already registered (possibly by another item). Skipping.")
            return

        # Prevent item hotkey from conflicting with global show/hide hotkey
        if hotkey_str == self.global_show_window_hotkey_str and self.global_show_window_hotkey_str :
            print(f"Warning: Item hotkey '{hotkey_str}' conflicts with global show window hotkey. Item hotkey for '{sc_data.get('name')}' will not be registered.")
            return

        try:
            # Create a lambda that captures the URL for this specific shortcut
            callback_func = lambda u=url_to_open: webbrowser.open(u)
            # suppress=False: allows the key combination to be processed by other applications too.
            # Set to True if you want this application to exclusively handle it.
            keyboard.add_hotkey(hotkey_str, callback_func, suppress=False)
            self.hotkey_actions[hotkey_str] = callback_func # Store for later unregistration
            print(f"INFO: Item hotkey '{hotkey_str}' for '{sc_data.get('name')}' registered.")
        except Exception as e: # Catch errors from 'keyboard' library (e.g., invalid hotkey format)
            print(f"Error registering item hotkey '{hotkey_str}' for '{sc_data.get('name')}': {e}")

    def unregister_hotkey(self, hotkey_str: str):
        """Unregisters a specific item hotkey."""
        if not hotkey_str or hotkey_str not in self.hotkey_actions:
            return
        try:
            keyboard.remove_hotkey(hotkey_str)
            # print(f"INFO: Item hotkey '{hotkey_str}' unregistered.") # Can be noisy
        except Exception as e: # Catch errors if 'keyboard' lib didn't have it registered
            print(f"Error unregistering item hotkey '{hotkey_str}': {e}")
        finally:
            if hotkey_str in self.hotkey_actions:
                del self.hotkey_actions[hotkey_str] # Remove from our tracking

    def move_shortcut_to_category(self, shortcut_id: str, new_category_name: str):
        """Moves a shortcut to a new category after being dragged onto a tab."""
        found = False
        for sc_data in self.shortcuts:
            if sc_data.get("id") == shortcut_id:
                sc_data["category"] = new_category_name
                found = True
                break
        if found:
            self.save_data()
            # Refresh the currently displayed list. If it was "All", it will update.
            # If it was the source or target category, it will also update.
            self.populate_list_for_current_tab()
            # If the target tab is not the current one, the user will see the change
            # when they switch to that tab.
        else:
            print(f"Warning (move_shortcut_to_category): Shortcut ID {shortcut_id} not found.")


if __name__ == '__main__':
    # Check for root privileges on Linux if using 'keyboard' for global hotkeys
    # This is a common requirement for low-level keyboard hooks.
    if sys.platform.startswith('linux') and os.geteuid() != 0:
        print("INFO: On Linux, the 'keyboard' library might need root privileges for global hotkeys.")
        print("INFO: If hotkeys do not work, try running with 'sudo python main.py'")

    app = QApplication(sys.argv)
    # Prevent app from closing when the last window is closed (for tray icon behavior)
    app.setQuitOnLastWindowClosed(False)

    # Create application data directories if they don't exist
    # 한국어 주석: 애플리케이션 데이터 및 파비콘 저장 폴더 생성
    for path_to_check, path_desc in [(APP_DATA_BASE_DIR, "애플리케이션 데이터"), (FAVICON_DIR, "파비콘 저장")]:
        if not os.path.exists(path_to_check):
            try:
                os.makedirs(path_to_check, exist_ok=True)
            except OSError as e:
                # Critical error, cannot proceed without data directories
                error_msg_box = QMessageBox()
                error_msg_box.setIcon(QMessageBox.Icon.Critical)
                error_msg_box.setWindowTitle("치명적 오류") # 한국어
                error_msg_box.setText(f"{path_desc} 폴더 '{path_to_check}' 생성 실패.\n{e}\n애플리케이션을 종료합니다.") # 한국어
                error_msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                error_msg_box.exec()
                sys.exit(1) # Exit if directory creation fails

    window = ShortcutManagerWindow()

    # Initial window visibility logic
    initial_show_behavior = True # Start visible by default, can be changed to False to start minimized to tray
    if QSystemTrayIcon.isSystemTrayAvailable():
        if initial_show_behavior:
            window._execute_always_show_window_gui_thread() # Show and activate
        # else: window will start hidden, accessible via tray or global hotkey
    else: # No system tray, always show the window
        print("INFO: System tray not available. Application window will be shown.")
        window._execute_always_show_window_gui_thread()


    sys.exit(app.exec())
