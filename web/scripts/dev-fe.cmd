@echo off
rem 前端 dev server 启动器：先修 TMP（本机默认 TEMP 无写权限会导致 esbuild 服务崩溃：
rem "[plugin:vite:esbuild] The service is no longer running"），再拉起 vite。
rem 用法: scripts\dev-fe.cmd [端口]   （默认 9501）
setlocal
set "TMP=C:\datas\AItosql\.tmp"
set "TEMP=C:\datas\AItosql\.tmp"
if not exist "%TMP%" mkdir "%TMP%"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=9501"
set "NODE=C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
"%NODE%" "%~dp0..\node_modules\vite\bin\vite.js" --host 127.0.0.1 --port %PORT% --strictPort
