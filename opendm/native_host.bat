@echo off
rem OpenDM Native Messaging Host launcher.
rem All paths are derived from this batch file's own directory (%~dp0), so
rem OpenDM can be installed anywhere - there is no hard-coded install path.
setlocal
set "HOSTDIR=%~dp0"
rem Set the working directory to the host's directory for good measure.
rem Token bootstrap itself does not depend on CWD: native_host.py resolves its
rem config via config_dir() (per-user %APPDATA%\OpenDM on Windows) and derives
rem PROJECT_ROOT from its own __file__ location.
cd /d "%HOSTDIR%"
"%HOSTDIR%..\..\python\python.exe" -u "%HOSTDIR%native_host.py" %*
