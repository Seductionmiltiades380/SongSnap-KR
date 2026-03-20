import sys

# Windows 콘솔 UTF-8 설정
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import asyncio
import io
import json
import os
import signal
import threading
import webbrowser
import time
import wave
from contextlib import asynccontextmanager
from datetime import datetime

import numpy as np
import pyaudiowpatch as pyaudio
import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from ShazamAPI import Shazam
from deep_translator import GoogleTranslator

translator = GoogleTranslator(source="auto", target="ko")


def translate_to_korean(text: str) -> str:
    """한국어가 아닌 텍스트를 한국어로 번역"""
    try:
        # 이미 한국어면 그대로
        if all("\uac00" <= c <= "\ud7a3" or not c.isalpha() for c in text):
            return ""
        result = translator.translate(text)
        if result and result != text:
            return result
    except Exception:
        pass
    return ""

# ── 상태 관리 ────────────────────────────────────────────────
current_song = {"title": "", "artist": "", "cover": "", "time": ""}
song_history = []
clients: list[WebSocket] = []
running = True
_loop = None


# ── Shazam 인식 ──────────────────────────────────────────────
def recognize_from_wav_bytes(wav_bytes: bytes) -> dict | None:
    try:
        shazam = Shazam(wav_bytes)
        for offset, result in shazam.recognizeSong():
            if result.get("matches"):
                track = result["track"]
                title = track.get("title", "Unknown")
                artist = track.get("subtitle", "Unknown")
                title_ko = translate_to_korean(title)
                artist_ko = translate_to_korean(artist)
                return {
                    "title": title,
                    "title_ko": title_ko,
                    "artist": artist,
                    "artist_ko": artist_ko,
                    "cover": track.get("images", {}).get("coverarthq", ""),
                }
            break
    except Exception as e:
        print(f"  인식 오류: {e}")
    return None


# ── 루프백 장치 찾기 ─────────────────────────────────────────
def get_loopback_device(p: pyaudio.PyAudio):
    try:
        wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    except OSError:
        print("  WASAPI를 찾을 수 없습니다.")
        return None

    default_output = p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
    print(f"  기본 출력 장치: {default_output['name']}")

    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if dev.get("isLoopbackDevice") and dev["name"].startswith(
            default_output["name"]
        ):
            print(f"  루프백 장치: {dev['name']}")
            return dev

    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        if dev.get("isLoopbackDevice"):
            print(f"  루프백 장치 (대체): {dev['name']}")
            return dev
    return None


# ── 오디오 캡처 + 인식 루프 ──────────────────────────────────
def audio_capture_loop():
    global current_song, song_history, running

    p = pyaudio.PyAudio()
    loopback = get_loopback_device(p)
    if not loopback:
        print("  루프백 장치를 찾을 수 없습니다!")
        p.terminate()
        return

    channels = int(loopback["maxInputChannels"])
    rate = int(loopback["defaultSampleRate"])
    chunk = 1024

    print(f"  설정: {channels}ch, {rate}Hz")
    print(f"  음악을 재생해보세요!\n")

    stream = p.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        input=True,
        input_device_index=loopback["index"],
        frames_per_buffer=chunk,
    )

    RECORD_SECONDS = 5
    SILENCE_THRESHOLD = 500

    while running:
        try:
            frames = []
            for _ in range(int(rate / chunk * RECORD_SECONDS)):
                if not running:
                    break
                data = stream.read(chunk, exception_on_overflow=False)
                frames.append(data)

            if not running:
                break

            audio_data = b"".join(frames)
            samples = np.frombuffer(audio_data, dtype=np.int16)
            rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))

            if rms < SILENCE_THRESHOLD:
                continue

            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(channels)
                wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
                wf.setframerate(rate)
                wf.writeframes(audio_data)

            result = recognize_from_wav_bytes(wav_buffer.getvalue())

            if result and (
                result["title"] != current_song.get("title")
                or result["artist"] != current_song.get("artist")
            ):
                now = datetime.now().strftime("%H:%M:%S")
                current_song = {**result, "time": now}
                song_history.insert(0, current_song.copy())
                song_history[:] = song_history[:50]

                print(f"  >> [{now}] {result['artist']} - {result['title']}")
                broadcast_song()

        except Exception as e:
            print(f"  캡처 오류: {e}")
            time.sleep(1)

    stream.stop_stream()
    stream.close()
    p.terminate()


# ── WebSocket 브로드캐스트 ───────────────────────────────────
def broadcast_song():
    msg = json.dumps(
        {"type": "song", "current": current_song, "history": song_history[:20]},
        ensure_ascii=False,
    )
    if _loop:
        asyncio.run_coroutine_threadsafe(_send_all(msg), _loop)


async def _send_all(msg):
    dead = []
    for ws in clients:
        try:
            await ws.send_text(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        if ws in clients:
            clients.remove(ws)


# ── FastAPI 앱 ───────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    global _loop, running
    _loop = asyncio.get_event_loop()
    thread = threading.Thread(target=audio_capture_loop, daemon=True)
    thread.start()
    # 서버 준비 후 브라우저 열기
    threading.Thread(target=lambda: (time.sleep(1), webbrowser.open("http://localhost:8000")), daemon=True).start()
    yield
    running = False


app = FastAPI(lifespan=lifespan)

HTML_PAGE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>실시간 노래 인식기</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'Segoe UI', -apple-system, sans-serif;
    background: #0a0a0f;
    color: #e0e0e0;
    min-height: 100vh;
    overflow-x: hidden;
  }
  .bg-gradient {
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background:
      radial-gradient(ellipse at 20% 50%, rgba(120,50,255,0.15) 0%, transparent 50%),
      radial-gradient(ellipse at 80% 20%, rgba(255,50,120,0.1) 0%, transparent 50%),
      radial-gradient(ellipse at 50% 80%, rgba(50,120,255,0.1) 0%, transparent 50%);
    z-index: 0;
  }
  .container {
    position: relative; z-index: 1;
    max-width: 600px; margin: 0 auto; padding: 40px 20px;
  }
  header { text-align: center; margin-bottom: 40px; }
  header h1 { font-size: 1.5rem; font-weight: 600; color: #fff; letter-spacing: -0.02em; }
  .status {
    display: inline-flex; align-items: center; gap: 8px; margin-top: 12px;
    padding: 6px 16px; background: rgba(255,255,255,0.06);
    border-radius: 20px; font-size: 0.85rem; color: #aaa;
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%; background: #4ade80;
    animation: pulse 2s ease-in-out infinite;
  }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  .now-playing {
    background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.08);
    border-radius: 20px; padding: 32px; text-align: center;
    margin-bottom: 32px; backdrop-filter: blur(20px); transition: all 0.5s ease;
  }
  .now-playing.has-song { border-color: rgba(120,50,255,0.3); background: rgba(120,50,255,0.08); }
  .now-label { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.1em; color: #888; margin-bottom: 20px; }
  .cover-art {
    width: 160px; height: 160px; border-radius: 16px; margin: 0 auto 24px;
    background: rgba(255,255,255,0.05); overflow: hidden; display: none;
  }
  .cover-art img { width: 100%; height: 100%; object-fit: cover; }
  .cover-art.visible { display: block; }
  .song-title { font-size: 1.6rem; font-weight: 700; color: #fff; margin-bottom: 8px; line-height: 1.3; }
  .song-artist { font-size: 1.1rem; color: #aaa; }
  .song-title-ko { font-size: 1rem; color: #9b7aff; margin-bottom: 4px; font-weight: 500; }
  .song-artist-ko { font-size: 0.9rem; color: #888; }
  .waiting-text { color: #555; font-size: 1.1rem; }

  .eq-bars { display: inline-flex; gap: 3px; align-items: flex-end; height: 20px; margin-bottom: 16px; }
  .eq-bar {
    width: 4px; background: linear-gradient(to top, #7832ff, #ff3278);
    border-radius: 2px; animation: eq 0.8s ease-in-out infinite alternate;
  }
  .eq-bar:nth-child(1){height:8px;animation-delay:0s}
  .eq-bar:nth-child(2){height:16px;animation-delay:.2s}
  .eq-bar:nth-child(3){height:10px;animation-delay:.4s}
  .eq-bar:nth-child(4){height:18px;animation-delay:.1s}
  .eq-bar:nth-child(5){height:6px;animation-delay:.3s}
  @keyframes eq { 0%{transform:scaleY(.3)} 100%{transform:scaleY(1)} }
  .eq-bars.paused .eq-bar { animation-play-state: paused; }

  .history-section h2 {
    font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.08em;
    color: #666; margin-bottom: 16px; padding-left: 4px;
  }
  .history-list { display: flex; flex-direction: column; gap: 8px; }
  .history-item {
    display: flex; align-items: center; gap: 16px; padding: 14px 18px;
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.05);
    border-radius: 14px; transition: all 0.3s ease; animation: slideIn 0.4s ease;
  }
  @keyframes slideIn { from{opacity:0;transform:translateY(-10px)} to{opacity:1;transform:translateY(0)} }
  .history-item:hover { background: rgba(255,255,255,0.06); }
  .history-cover { width:44px; height:44px; border-radius:8px; background:rgba(255,255,255,0.08); flex-shrink:0; overflow:hidden; }
  .history-cover img { width:100%; height:100%; object-fit:cover; }
  .history-info { flex:1; min-width:0; }
  .history-title { font-size:0.95rem; font-weight:600; color:#ddd; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .history-artist { font-size:0.8rem; color:#777; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
  .history-time { font-size:0.75rem; color:#555; flex-shrink:0; font-variant-numeric:tabular-nums; }
  .empty-history { text-align:center; color:#444; padding:40px; font-size:0.9rem; }

  .shutdown-btn {
    display: block; margin: 40px auto 0; padding: 12px 32px;
    background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3);
    border-radius: 12px; color: #ef4444; font-size: 0.9rem;
    cursor: pointer; transition: all 0.3s ease;
  }
  .shutdown-btn:hover { background: rgba(239,68,68,0.2); border-color: rgba(239,68,68,0.5); }
</style>
</head>
<body>
<div class="bg-gradient"></div>
<div class="container">
  <header>
    <h1>실시간 노래 인식기</h1>
    <div class="status">
      <div class="status-dot"></div>
      <span id="statusText">듣는 중...</span>
    </div>
  </header>
  <div class="now-playing" id="nowPlaying">
    <div class="now-label">NOW PLAYING</div>
    <div class="eq-bars paused" id="eqBars">
      <div class="eq-bar"></div><div class="eq-bar"></div><div class="eq-bar"></div>
      <div class="eq-bar"></div><div class="eq-bar"></div>
    </div>
    <div class="cover-art" id="coverArt"><img id="coverImg" src="" alt=""></div>
    <div class="song-title" id="songTitle"><span class="waiting-text">음악을 재생해보세요</span></div>
    <div class="song-title-ko" id="songTitleKo"></div>
    <div class="song-artist" id="songArtist"></div>
    <div class="song-artist-ko" id="songArtistKo"></div>
  </div>
  <div class="history-section">
    <h2>인식 기록</h2>
    <div class="history-list" id="historyList">
      <div class="empty-history">아직 인식된 곡이 없습니다</div>
    </div>
  </div>
  <button class="shutdown-btn" onclick="shutdownServer()">서버 종료</button>
</div>
<script>
  let ws;
  function connect() {
    ws = new WebSocket(`ws://${location.host}/ws`);
    ws.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.type === 'song') { updateNowPlaying(data.current); updateHistory(data.history); }
    };
    ws.onclose = () => {
      document.getElementById('statusText').textContent = '재연결 중...';
      document.querySelector('.status-dot').style.background = '#f59e0b';
      setTimeout(connect, 2000);
    };
    ws.onopen = () => {
      document.getElementById('statusText').textContent = '듣는 중...';
      document.querySelector('.status-dot').style.background = '#4ade80';
    };
  }
  connect();

  function updateNowPlaying(song) {
    if (!song || !song.title) return;
    document.getElementById('nowPlaying').classList.add('has-song');
    document.getElementById('eqBars').classList.remove('paused');
    document.getElementById('songTitle').textContent = song.title;
    document.getElementById('songTitleKo').textContent = song.title_ko || '';
    document.getElementById('songArtist').textContent = song.artist;
    document.getElementById('songArtistKo').textContent = song.artist_ko || '';
    const coverArt = document.getElementById('coverArt');
    if (song.cover) {
      document.getElementById('coverImg').src = song.cover;
      coverArt.classList.add('visible');
    } else { coverArt.classList.remove('visible'); }
  }

  function updateHistory(history) {
    const list = document.getElementById('historyList');
    if (!history || !history.length) {
      list.innerHTML = '<div class="empty-history">아직 인식된 곡이 없습니다</div>';
      return;
    }
    list.innerHTML = history.map(s => `
      <div class="history-item">
        <div class="history-cover">${s.cover ? `<img src="${esc(s.cover)}" alt="">` : ''}</div>
        <div class="history-info">
          <div class="history-title">${esc(s.title)}${s.title_ko ? ` <span style="color:#9b7aff;font-weight:400;font-size:0.85rem">(${esc(s.title_ko)})</span>` : ''}</div>
          <div class="history-artist">${esc(s.artist)}${s.artist_ko ? ` (${esc(s.artist_ko)})` : ''}</div>
        </div>
        <div class="history-time">${s.time}</div>
      </div>`).join('');
  }

  function esc(t) { const d = document.createElement('div'); d.textContent = t; return d.innerHTML; }

  function shutdownServer() {
    if (!confirm('서버를 종료하시겠습니까?')) return;
    fetch('/shutdown', { method: 'POST' }).then(() => {
      document.body.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100vh;color:#666;font-family:sans-serif;background:#0a0a0f"><p>서버가 종료되었습니다. 이 탭을 닫아주세요.</p></div>';
    });
  }
</script>
</body>
</html>"""


@app.get("/")
async def get_page():
    return HTMLResponse(HTML_PAGE)


@app.post("/shutdown")
async def shutdown():
    global running
    running = False
    # 약간의 딜레이 후 프로세스 종료
    threading.Thread(target=lambda: (time.sleep(0.5), os.kill(os.getpid(), signal.SIGTERM)), daemon=True).start()
    return {"message": "서버를 종료합니다"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    clients.append(websocket)
    if current_song.get("title"):
        await websocket.send_text(
            json.dumps(
                {"type": "song", "current": current_song, "history": song_history[:20]},
                ensure_ascii=False,
            )
        )
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in clients:
            clients.remove(websocket)


if __name__ == "__main__":
    print(flush=True)
    print("  === 실시간 노래 인식기 ===", flush=True)
    print("  http://localhost:8000", flush=True)
    print(flush=True)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
