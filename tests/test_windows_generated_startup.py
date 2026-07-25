from pathlib import Path


def test_generated_startup_wrapper_is_executed_in_ci():
    service = Path("install-windows-service.ps1").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "ValidateGenerated" in service
    assert "New-ServerScriptContent" in service
    assert "LightHouse generated startup script OK" in service
    assert "-Stage ValidateGenerated" in workflow


def test_generated_start_process_has_no_here_string_line_continuation():
    service = Path("install-windows-service.ps1").read_text(encoding="utf-8")
    line = next(
        item
        for item in service.splitlines()
        if "`$apiProcess = Start-Process" in item
    )

    assert "-ArgumentList" in line
    assert "-WorkingDirectory" in line
    assert "-RedirectStandardOutput" in line
    assert "-RedirectStandardError" in line
    assert not line.rstrip().endswith("`")
