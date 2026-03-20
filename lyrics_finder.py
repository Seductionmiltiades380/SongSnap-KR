import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import ctypes
import io
import re
import threading
import tkinter as tk
from tkinter import ttk

import easyocr
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageGrab, ImageTk

# DPI awareness for sharp screenshots on Windows
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# ── EasyOCR 리더 (lazy init) ────────────────────────────────
_reader = None
_reader_lock = threading.Lock()


def get_reader():
    global _reader
    if _reader is None:
        with _reader_lock:
            if _reader is None:
                print("  OCR 모델 로딩 중... (최초 1회)")
                _reader = easyocr.Reader(["ko", "en"], gpu=False)
                print("  OCR 모델 로딩 완료!")
    return _reader


# ── 가사로 노래 검색 ────────────────────────────────────────
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def search_song_by_lyrics(lyrics_text: str) -> list[dict]:
    """Search for songs matching the given lyrics text."""
    results = []

    # Clean up the lyrics text
    lyrics_text = lyrics_text.strip()
    if not lyrics_text or len(lyrics_text) < 3:
        return results

    # Try multiple search approaches
    results = _search_google(lyrics_text)

    if len(results) < 3:
        results.extend(_search_google_alternate(lyrics_text))

    # Deduplicate
    seen = set()
    unique = []
    for r in results:
        key = (r["title"].lower(), r["artist"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(r)

    return unique[:3]


def _search_google(lyrics: str) -> list[dict]:
    """Search Google for lyrics."""
    results = []
    query = f'{lyrics} 가사 노래 제목'
    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": query},
            headers=HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract from search results
        for g in soup.select("div.g, div[data-sokoban-container]"):
            title_el = g.select_one("h3")
            snippet_el = g.select_one("div[data-sncf], span.st, div.VwiC3b")
            if title_el:
                title_text = title_el.get_text()
                snippet_text = snippet_el.get_text() if snippet_el else ""
                parsed = _parse_song_from_result(title_text, snippet_text)
                if parsed:
                    results.append(parsed)

        # Also try knowledge panel
        for kp in soup.select("div[data-attrid*='title'], div.kno-ecr-pt"):
            text = kp.get_text()
            if text:
                results.append({
                    "title": text.strip(),
                    "artist": "",
                    "confidence": 70,
                    "source": "Google Knowledge Panel",
                })

    except Exception as e:
        print(f"  Google 검색 오류: {e}")

    return results


def _search_google_alternate(lyrics: str) -> list[dict]:
    """Alternate Google search with English query."""
    results = []
    query = f'"{lyrics}" lyrics song'
    try:
        resp = requests.get(
            "https://www.google.com/search",
            params={"q": query},
            headers=HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        for g in soup.select("div.g"):
            title_el = g.select_one("h3")
            snippet_el = g.select_one("div[data-sncf], span.st, div.VwiC3b")
            if title_el:
                title_text = title_el.get_text()
                snippet_text = snippet_el.get_text() if snippet_el else ""
                parsed = _parse_song_from_result(title_text, snippet_text)
                if parsed:
                    results.append(parsed)

    except Exception as e:
        print(f"  보조 검색 오류: {e}")

    return results


def _parse_song_from_result(title: str, snippet: str) -> dict | None:
    """Try to extract song title and artist from a search result."""
    # Common patterns in lyrics search results
    # "Song Title - Artist 가사"
    # "Artist - Song Title Lyrics"
    # "Song Title 가사 - Artist"

    # Remove common suffixes
    clean = title
    for suffix in ["가사", "lyrics", "Lyrics", "LYRICS", "- 벅스", "- Melon",
                    "- 지니", "| Lyrics", "- FLO", "노래 가사", "song lyrics"]:
        clean = clean.replace(suffix, "")
    clean = clean.strip(" -|·")

    if not clean:
        return None

    # Try "Artist - Title" or "Title - Artist" pattern
    separators = [" - ", " – ", " — ", " | ", " · "]
    for sep in separators:
        if sep in clean:
            parts = clean.split(sep, 1)
            if len(parts) == 2 and len(parts[0].strip()) > 0 and len(parts[1].strip()) > 0:
                return {
                    "title": parts[1].strip(),
                    "artist": parts[0].strip(),
                    "confidence": 85,
                    "source": "Google Search",
                }

    # If no separator, use the whole thing as title
    if len(clean) > 1:
        return {
            "title": clean.strip(),
            "artist": _extract_artist_from_snippet(snippet),
            "confidence": 60,
            "source": "Google Search",
        }

    return None


def _extract_artist_from_snippet(snippet: str) -> str:
    """Try to extract artist name from snippet text."""
    # Look for patterns like "가수: XXX" or "아티스트: XXX"
    for pattern in [r"(?:가수|아티스트|artist|singer)\s*[:\-]\s*(.+?)(?:\s*[,.\n]|$)",
                    r"by\s+(.+?)(?:\s*[,.\n]|$)"]:
        m = re.search(pattern, snippet, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return "알 수 없음"


# ── GUI 앱 ──────────────────────────────────────────────────
class LyricsFinderApp:
    def __init__(self):
        # ── 캡처 영역 창 (투명 오버레이) ──
        self.overlay = tk.Tk()
        self.overlay.title("캡처 영역")
        self.overlay.geometry("500x200+100+100")
        self.overlay.attributes("-alpha", 0.3)
        self.overlay.attributes("-topmost", True)
        self.overlay.configure(bg="#ff0000")
        self.overlay.minsize(200, 80)

        # 캡처 영역 안내 레이블
        self.overlay_label = tk.Label(
            self.overlay,
            text="이 창을 가사 위에 놓고\n크기를 조절하세요",
            font=("맑은 고딕", 14, "bold"),
            bg="#ff0000",
            fg="#ffffff",
        )
        self.overlay_label.place(relx=0.5, rely=0.5, anchor="center")

        # ── 결과 창 ──
        self.result_win = tk.Toplevel(self.overlay)
        self.result_win.title("가사로 노래 찾기")
        self.result_win.geometry("520x650+650+100")
        self.result_win.attributes("-topmost", True)
        self.result_win.configure(bg="#1a1a2e")

        self._build_result_ui()

        # 닫기 처리
        self.overlay.protocol("WM_DELETE_WINDOW", self._on_close)
        self.result_win.protocol("WM_DELETE_WINDOW", self._on_close)

        # OCR 리더 백그라운드 로딩
        threading.Thread(target=get_reader, daemon=True).start()

    def _build_result_ui(self):
        win = self.result_win

        # 상단 프레임
        top_frame = tk.Frame(win, bg="#1a1a2e", pady=10, padx=15)
        top_frame.pack(fill="x")

        title_label = tk.Label(
            top_frame,
            text="🎵 가사로 노래 찾기",
            font=("맑은 고딕", 16, "bold"),
            bg="#1a1a2e",
            fg="#e0e0ff",
        )
        title_label.pack(anchor="w")

        desc_label = tk.Label(
            top_frame,
            text="빨간 창을 가사 위에 놓고 [캡처 & 검색] 버튼을 누르세요",
            font=("맑은 고딕", 9),
            bg="#1a1a2e",
            fg="#888899",
        )
        desc_label.pack(anchor="w", pady=(2, 0))

        # 버튼 프레임
        btn_frame = tk.Frame(win, bg="#1a1a2e", padx=15)
        btn_frame.pack(fill="x", pady=(0, 10))

        self.capture_btn = tk.Button(
            btn_frame,
            text="📷 캡처 & 검색",
            font=("맑은 고딕", 12, "bold"),
            bg="#4a3aff",
            fg="white",
            activebackground="#6a5aff",
            activeforeground="white",
            relief="flat",
            padx=20,
            pady=8,
            cursor="hand2",
            command=self._on_capture,
        )
        self.capture_btn.pack(side="left")

        self.opacity_label = tk.Label(
            btn_frame,
            text="투명도:",
            font=("맑은 고딕", 9),
            bg="#1a1a2e",
            fg="#888899",
        )
        self.opacity_label.pack(side="left", padx=(20, 5))

        self.opacity_var = tk.IntVar(value=30)
        self.opacity_scale = tk.Scale(
            btn_frame,
            from_=10,
            to=80,
            orient="horizontal",
            variable=self.opacity_var,
            bg="#1a1a2e",
            fg="#aaaacc",
            highlightthickness=0,
            troughcolor="#333355",
            length=120,
            command=self._on_opacity_change,
        )
        self.opacity_scale.pack(side="left")

        # 상태 표시
        self.status_var = tk.StringVar(value="준비됨")
        status_label = tk.Label(
            win,
            textvariable=self.status_var,
            font=("맑은 고딕", 9),
            bg="#1a1a2e",
            fg="#666688",
            padx=15,
        )
        status_label.pack(anchor="w")

        # 인식된 텍스트
        text_frame = tk.LabelFrame(
            win,
            text=" 인식된 가사 ",
            font=("맑은 고딕", 10),
            bg="#1a1a2e",
            fg="#9999bb",
            padx=10,
            pady=8,
        )
        text_frame.pack(fill="x", padx=15, pady=(5, 10))

        self.ocr_text = tk.Text(
            text_frame,
            height=4,
            font=("맑은 고딕", 10),
            bg="#0d0d1a",
            fg="#ccccee",
            insertbackground="#ccccee",
            relief="flat",
            wrap="word",
            padx=8,
            pady=5,
        )
        self.ocr_text.pack(fill="x")

        # 검색 결과
        result_frame = tk.LabelFrame(
            win,
            text=" 검색 결과 ",
            font=("맑은 고딕", 10),
            bg="#1a1a2e",
            fg="#9999bb",
            padx=10,
            pady=8,
        )
        result_frame.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        self.results_container = tk.Frame(result_frame, bg="#1a1a2e")
        self.results_container.pack(fill="both", expand=True)

        # 초기 안내
        self.placeholder = tk.Label(
            self.results_container,
            text="캡처 버튼을 눌러 시작하세요",
            font=("맑은 고딕", 11),
            bg="#1a1a2e",
            fg="#444466",
        )
        self.placeholder.pack(pady=40)

        # 하단: 직접 입력 영역
        manual_frame = tk.Frame(win, bg="#252540", padx=15, pady=10)
        manual_frame.pack(fill="x", side="bottom")

        tk.Label(
            manual_frame,
            text="직접 입력:",
            font=("맑은 고딕", 9),
            bg="#252540",
            fg="#888899",
        ).pack(side="left")

        self.manual_entry = tk.Entry(
            manual_frame,
            font=("맑은 고딕", 10),
            bg="#0d0d1a",
            fg="#ccccee",
            insertbackground="#ccccee",
            relief="flat",
        )
        self.manual_entry.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.manual_entry.bind("<Return>", lambda e: self._on_manual_search())

        self.manual_btn = tk.Button(
            manual_frame,
            text="검색",
            font=("맑은 고딕", 9, "bold"),
            bg="#4a3aff",
            fg="white",
            relief="flat",
            padx=12,
            cursor="hand2",
            command=self._on_manual_search,
        )
        self.manual_btn.pack(side="left")

    def _on_opacity_change(self, val):
        self.overlay.attributes("-alpha", int(val) / 100)

    def _on_capture(self):
        self.capture_btn.config(state="disabled", text="처리 중...")
        self.status_var.set("화면 캡처 중...")
        self.overlay.update()

        # 잠시 오버레이 숨기고 캡처
        self.overlay.withdraw()
        self.overlay.update_idletasks()
        import time
        time.sleep(0.3)

        x = self.overlay.winfo_x()
        y = self.overlay.winfo_y()
        w = self.overlay.winfo_width()
        h = self.overlay.winfo_height()

        try:
            screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        except Exception as e:
            self.status_var.set(f"캡처 오류: {e}")
            self.overlay.deiconify()
            self.capture_btn.config(state="normal", text="📷 캡처 & 검색")
            return

        self.overlay.deiconify()

        # OCR + 검색을 백그라운드에서 실행
        threading.Thread(
            target=self._process_image, args=(screenshot,), daemon=True
        ).start()

    def _process_image(self, image: Image.Image):
        self._update_status("OCR 처리 중...")

        try:
            reader = get_reader()
            # Convert to numpy array for easyocr
            import numpy as np
            img_array = np.array(image)
            results = reader.readtext(img_array, detail=0, paragraph=True)
            text = "\n".join(results).strip()
        except Exception as e:
            self._update_status(f"OCR 오류: {e}")
            self._enable_button()
            return

        if not text:
            self._update_status("텍스트를 인식하지 못했습니다")
            self._enable_button()
            return

        self._set_ocr_text(text)
        self._update_status("노래 검색 중...")

        try:
            songs = search_song_by_lyrics(text)
        except Exception as e:
            self._update_status(f"검색 오류: {e}")
            self._enable_button()
            return

        self._show_results(songs, text)
        self._enable_button()

    def _on_manual_search(self):
        text = self.manual_entry.get().strip()
        if not text:
            return

        self.manual_btn.config(state="disabled")
        self.capture_btn.config(state="disabled")
        self._set_ocr_text(text)
        self._update_status("노래 검색 중...")

        def do_search():
            try:
                songs = search_song_by_lyrics(text)
                self._show_results(songs, text)
            except Exception as e:
                self._update_status(f"검색 오류: {e}")
            self._enable_button()

        threading.Thread(target=do_search, daemon=True).start()

    def _show_results(self, songs: list[dict], query: str):
        def update():
            # Clear previous results
            for w in self.results_container.winfo_children():
                w.destroy()

            if not songs:
                no_result = tk.Label(
                    self.results_container,
                    text="일치하는 노래를 찾지 못했습니다.\n다른 가사 부분을 캡처해 보세요.",
                    font=("맑은 고딕", 11),
                    bg="#1a1a2e",
                    fg="#888899",
                    justify="center",
                )
                no_result.pack(pady=30)
                self.status_var.set("결과 없음")
                return

            # Assign confidence based on ranking
            confidences = [90, 70, 50]

            for i, song in enumerate(songs[:3]):
                conf = song.get("confidence", confidences[i] if i < 3 else 40)
                # Adjust confidence by position
                if i == 0:
                    conf = min(conf + 5, 95)
                elif i == 1:
                    conf = max(conf - 10, 40)
                elif i == 2:
                    conf = max(conf - 20, 30)

                self._create_result_card(i + 1, song, conf)

            self.status_var.set(f"{len(songs[:3])}개 결과 찾음")

        self.result_win.after(0, update)

    def _create_result_card(self, rank: int, song: dict, confidence: int):
        # Card frame
        card = tk.Frame(
            self.results_container,
            bg="#252540",
            padx=15,
            pady=12,
            highlightbackground="#333355",
            highlightthickness=1,
        )
        card.pack(fill="x", pady=(0, 8))

        # Rank + Confidence row
        top_row = tk.Frame(card, bg="#252540")
        top_row.pack(fill="x")

        rank_colors = {1: "#ffd700", 2: "#c0c0c0", 3: "#cd7f32"}
        rank_label = tk.Label(
            top_row,
            text=f"#{rank}",
            font=("맑은 고딕", 14, "bold"),
            bg="#252540",
            fg=rank_colors.get(rank, "#888899"),
        )
        rank_label.pack(side="left")

        # Confidence bar
        conf_frame = tk.Frame(top_row, bg="#252540")
        conf_frame.pack(side="right")

        conf_color = "#4ade80" if confidence >= 70 else "#f59e0b" if confidence >= 50 else "#888899"
        conf_label = tk.Label(
            conf_frame,
            text=f"{confidence}%",
            font=("맑은 고딕", 11, "bold"),
            bg="#252540",
            fg=conf_color,
        )
        conf_label.pack(side="right")

        tk.Label(
            conf_frame,
            text="가능성 ",
            font=("맑은 고딕", 9),
            bg="#252540",
            fg="#666688",
        ).pack(side="right")

        # Song info
        title_label = tk.Label(
            card,
            text=song["title"],
            font=("맑은 고딕", 13, "bold"),
            bg="#252540",
            fg="#e0e0ff",
            anchor="w",
            wraplength=440,
            justify="left",
        )
        title_label.pack(fill="x", pady=(6, 0))

        artist = song.get("artist", "알 수 없음")
        if artist:
            artist_label = tk.Label(
                card,
                text=artist,
                font=("맑은 고딕", 10),
                bg="#252540",
                fg="#8888aa",
                anchor="w",
            )
            artist_label.pack(fill="x", pady=(2, 0))

    def _update_status(self, text: str):
        self.result_win.after(0, lambda: self.status_var.set(text))

    def _set_ocr_text(self, text: str):
        def update():
            self.ocr_text.delete("1.0", "end")
            self.ocr_text.insert("1.0", text)
        self.result_win.after(0, update)

    def _enable_button(self):
        def update():
            self.capture_btn.config(state="normal", text="📷 캡처 & 검색")
            self.manual_btn.config(state="normal")
        self.result_win.after(0, update)

    def _on_close(self):
        self.overlay.destroy()

    def run(self):
        print("\n  === 가사로 노래 찾기 ===")
        print("  1. 빨간 창을 가사가 보이는 곳으로 이동하세요")
        print("  2. 창 크기를 가사에 맞게 조절하세요")
        print("  3. [캡처 & 검색] 버튼을 누르세요\n")
        self.overlay.mainloop()


if __name__ == "__main__":
    app = LyricsFinderApp()
    app.run()
