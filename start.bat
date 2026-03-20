@echo off
chcp 65001 >nul
echo.
echo   === 실시간 노래 인식기 ===
echo   서버 시작 중... 브라우저가 자동으로 열립니다.
echo   종료하려면 사이트의 [서버 종료] 버튼을 누르세요.
echo.
python "%~dp0app.py"
