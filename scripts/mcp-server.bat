@echo off
REM DepthFusion MCP server launcher for Windows Claude Desktop
REM Referenced from %APPDATA%\Claude\claude_desktop_config.json
REM Do not run directly — launched automatically by Claude Desktop.

if not defined DEPTHFUSION_ENV_FILE (
    set "DEPTHFUSION_ENV_FILE=%APPDATA%\Claude\depthfusion.env"
)
"%~dp0..\.depthfusion-venv\Scripts\python.exe" -m depthfusion.mcp %*
