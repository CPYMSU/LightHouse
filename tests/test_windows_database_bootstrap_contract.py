from __future__ import annotations

from pathlib import Path
import re


def _database_installer() -> str:
    return Path("install-windows-database.ps1").read_text(encoding="utf-8")


def test_windows_database_installer_honors_external_database_environment():
    script = _database_installer()

    assert "LIGHTHOUSE_DATABASE_URL" in script
    assert "Get-ExternalDatabaseUrl" in script
    assert "$config['database_url'] = $externalDatabaseUrl" in script
    assert "$config['database_managed'] = $false" in script
    assert "Using LIGHTHOUSE_DATABASE_URL" in script


def test_windows_database_installer_validates_complete_postgres_runtime():
    script = _database_installer()

    for required in (
        "postgres.exe",
        "pg_config.exe",
        "dict_snowball.dll",
        "plpgsql.dll",
        "postgres.bki",
        "postgresql.conf.sample",
    ):
        assert required in script

    assert "--pkglibdir" in script
    assert "--sharedir" in script
    assert "Get-PostgresRuntimeReport" in script
    assert "PostgreSQL 16 is installed but incomplete" in script


def test_windows_database_installer_repairs_incomplete_winget_install():
    script = _database_installer()

    assert "winget.exe repair" in script
    assert "--source', 'winget'" in script
    assert "--force" in script
    assert "Wait-Postgres16 300" in script
    assert "winget.exe uninstall" not in script
    assert "Remove-Item \"C:\\Program Files\\PostgreSQL" not in script


def test_windows_private_cluster_uses_dedicated_port_and_neutral_locale():
    script = _database_installer()

    assert "$DefaultPrivatePort = 55432" in script
    assert "function Find-PrivatePort" in script
    assert "if (-not (Test-TcpListener 5432)) { return 5432 }" not in script
    assert "--locale=C" in script
    assert "--encoding=UTF8" in script


def test_windows_public_bootstrap_refreshes_staged_helpers_without_version_drift():
    script = Path("install-windows.ps1").read_text(encoding="utf-8")

    helper_urls = re.findall(
        r"https://raw\.githubusercontent\.com/CPYMSU/LightHouse/main/"
        r"install-windows-(?:database|core|service)\.ps1\?v=([^&']+)&rev=([^']+)",
        script,
    )
    assert len(helper_urls) == 3
    assert {version for version, _revision in helper_urls} == {"1.8.0"}
    revisions = {revision for _version, revision in helper_urls}
    assert len(revisions) == 1
    assert next(iter(revisions)).strip()
