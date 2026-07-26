from pathlib import Path


def test_macos_installer_allocates_instead_of_rejecting_port_conflicts():
    script = Path("install-macos.sh").read_text(encoding="utf-8")
    assert "PORT_START=8787" in script
    assert "no free local port is available" in script
    assert "assigning the default LightHouse instance" in script
    assert "cannot start LightHouse while another process owns" not in script
    assert "'instance_id': 'default'" in script
    assert "from lighthouse.instances import ensure_default_instance" in script


def test_macos_bootstrap_uses_two_github_hosts_and_validates_downloads():
    script = Path("install-macos.sh").read_text(encoding="utf-8")
    assert "RAW_INSTALL_URL=" in script
    assert "API_INSTALL_URL=" in script
    assert "fetch_script()" in script
    assert "application/vnd.github.raw+json" in script
    assert "--http1.1" in script
    assert "--connect-timeout 20" in script
    assert "/bin/bash -n" in script
    assert "for source in raw api" in script
    assert "for attempt in 1 2 3" in script
    assert "api.github.com/repos/Homebrew/install/contents/install.sh" in script


def test_macos_installer_recovers_postgres_without_touching_data():
    script = Path("install-macos.sh").read_text(encoding="utf-8")
    assert "start_postgres_with_recovery" in script
    assert "postgres_ready" in script
    assert "wait_for_postgres" in script
    assert "brew services cleanup" in script
    assert 'launchctl bootout "gui/$(id -u)/homebrew.mxcl.postgresql@16"' in script
    assert '"$PG_BIN/pg_ctl" -D "$PG_DATA" -l "$PG_LOG" start' in script
    assert "Existing database files were not modified" in script
    assert 'rm -rf "$PG_DATA"' not in script
    assert "initdb" not in script


def test_macos_installer_repairs_lighthouse_launchd_registration():
    script = Path("install-macos.sh").read_text(encoding="utf-8")
    assert "start_lighthouse_service_with_recovery" in script
    assert "wait_for_lighthouse" in script
    assert 'launchctl bootout "$target"' in script
    assert 'launchctl bootout "$domain" "$PLIST"' in script
    assert 'launchctl bootstrap "$domain" "$PLIST"' in script
    assert 'launchctl kickstart -k "$target"' in script
    assert "LightHouse service startup issue detected; repairing automatically" in script


def test_local_cli_recovery_is_platform_scoped_and_health_checked():
    source = Path("src/lighthouse/local_service.py").read_text(encoding="utf-8")
    cli = Path("src/lighthouse/cli.py").read_text(encoding="utf-8")
    assert "recover_local_service" in source
    assert 'Path.home() / "Library" / "LaunchAgents"' in source
    assert '"launchctl", "kickstart", "-k"' in source
    assert '"launchctl", "bootstrap"' in source
    assert '"schtasks.exe", "/Run", "/TN", "LightHouse"' in source
    assert 'base_url.rstrip("/") + "/healthz"' in source
    assert "except (httpx.ConnectError, httpx.ConnectTimeout)" in cli
    assert "except httpx.RequestError" in cli


def test_windows_bootstrap_stops_the_installed_runtime_before_upgrade():
    script = Path("install-windows.ps1").read_text(encoding="utf-8")
    assert "function Stop-LightHouseRuntime" in script
    assert "Stop-ScheduledTask -TaskName 'LightHouse'" in script
    assert "Get-CimInstance Win32_Process" in script
    assert "StartsWith($venvRoot" in script
    assert "Stop-LightHouseRuntime" in script.split("-Stage Install")[0]
    assert "v=1.6.0" in script


def test_windows_core_allocates_and_registers_the_default_instance():
    script = Path("install-windows-core.ps1").read_text(encoding="utf-8")
    assert "function Find-FreeApiPort" in script
    assert "$ApiPort = Find-FreeApiPort $PreferredApiPort" in script
    assert "port $ApiPort is already in use" not in script
    assert "$Config['instance_id'] = 'default'" in script
    assert "from lighthouse.instances import ensure_default_instance" in script


def test_windows_service_uses_the_configured_port_everywhere():
    script = Path("install-windows-service.ps1").read_text(encoding="utf-8")
    assert "$ApiPort = [int]$Config['port']" in script
    assert 'http://127.0.0.1:$ApiPort/healthz' in script
    assert "did not become healthy on port $ApiPort" in script
    assert "$env:LIGHTHOUSE_INSTANCE_ID = 'default'" in script


def test_console_entrypoint_wraps_the_lazy_auto_observatory_terminal():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    instance_entry = Path("src/lighthouse/instance_entry.py").read_text(encoding="utf-8")
    terminal_entry = Path("src/lighthouse/terminal_entry.py").read_text(encoding="utf-8")
    terminal_v4 = Path("src/lighthouse/terminal_v4.py").read_text(encoding="utf-8")
    assert 'lh = "lighthouse.instance_entry:main"' in project
    assert "from . import terminal_entry" in instance_entry
    assert "return terminal_entry.main(argv)" in instance_entry
    assert "from . import terminal_v3" in terminal_entry  # 1.0 compatibility marker
    assert "from . import terminal_v4" in terminal_entry
    assert "return terminal_v4.main(argv)" in terminal_entry
    assert 'AUTO_MODE_KEY = "auto_mode"' in terminal_v4
    assert "ASK ON ACTION" in terminal_entry
    assert "_ask_auto_mode" not in terminal_v4
