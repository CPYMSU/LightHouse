from pathlib import Path


def test_macos_installer_allocates_instead_of_rejecting_port_conflicts():
    script = Path("install-macos.sh").read_text(encoding="utf-8")
    assert "PORT_START=8787" in script
    assert "no free local port is available" in script
    assert "assigning the default LightHouse instance" in script
    assert "cannot start LightHouse while another process owns" not in script
    assert "'instance_id': 'default'" in script
    assert "from lighthouse.instances import ensure_default_instance" in script


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


def test_console_entrypoint_wraps_the_auto_mode_terminal_entry():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    instance_entry = Path("src/lighthouse/instance_entry.py").read_text(encoding="utf-8")
    terminal_entry = Path("src/lighthouse/terminal_entry.py").read_text(encoding="utf-8")
    terminal_v3 = Path("src/lighthouse/terminal_v3.py").read_text(encoding="utf-8")
    assert 'lh = "lighthouse.instance_entry:main"' in project
    assert "from . import terminal_entry" in instance_entry
    assert "return terminal_entry.main(argv)" in instance_entry
    assert "from . import terminal_v3" in terminal_entry
    assert "return terminal_v3.main(argv)" in terminal_entry
    assert 'AUTO_MODE_KEY = "auto_mode"' in terminal_v3
