from pathlib import Path

from lighthouse import __version__


def test_release_version_is_consistent():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    package = Path("src/lighthouse/__init__.py").read_text(encoding="utf-8")
    windows = Path("install-windows.ps1").read_text(encoding="utf-8")
    readme = Path("README.md").read_text(encoding="utf-8")
    release_notes = Path("RELEASE_NOTES_1.4.0.md").read_text(encoding="utf-8")

    assert __version__ == "1.4.0"
    assert 'version = "1.4.0"' in project
    assert '__version__ = "1.4.0"' in package
    assert windows.count("v=1.4.0") == 3
    assert "# LightHouse OS 1.4.0" in release_notes
    assert "Cognitive Continuity" in release_notes
    assert "Observable Engineering Progress" in release_notes
    assert "api.github.com/repos/CPYMSU/LightHouse/contents/install-macos.sh" in readme
    assert "Memory Fabric 0.7" not in readme
