@echo off
:: stop_servers.bat
:: Kills all four MCP server processes by window title.

for %%N in (transcribe ass_bilingual ass_translate ass_burnin) do (
    taskkill /FI "WINDOWTITLE eq %%N*" /F >nul 2>&1
    echo [stop] %%N
)
echo Done.
