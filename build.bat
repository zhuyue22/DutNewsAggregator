@echo off
REM 大工资讯聚合 · 桌面软件打包脚本（PyInstaller onedir）
REM 用法： 双击运行，或 build.bat
REM 产物： dist/DutNewsAggregator/DutNewsAggregator.exe

echo === 大工资讯聚合 · 打包 ===

set VENV=C:\Users\赵朔\.workbuddy\binaries\python\envs\dut\Scripts\python.exe
set APPDIR=%CD%

"%VENV%" -m PyInstaller ^
  --noconfirm --clean ^
  --name DutNewsAggregator ^
  --onefile ^
  --windowed ^
  --icon "%APPDIR%\assets\icon.ico" ^
  --add-data "index.html;." ^
  --add-data "data;data" ^
  --add-data "sources.json;." ^
  --add-data "assets;assets" ^
  --hidden-import collector ^
  --hidden-import collector_eng ^
  --hidden-import eng_lib ^
  --hidden-import sectionize ^
  --hidden-import serve ^
  "%APPDIR%\app.py"

if errorlevel 1 (
   echo 打包失败！
   pause
   exit /b 1
)

echo.
echo === 完成！产物在 dist/DutNewsAggregator/DutNewsAggregator.exe ===
echo 注意：onefile 模式会把静态资源打包进 exe；首次运行需解压，稍慢属正常。
pause
