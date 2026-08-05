from pathlib import Path


def test_rust_kernel_contract_is_versioned() -> None:
    cargo = Path("rust/lighthouse-code-kernel/Cargo.toml").read_text(encoding="utf-8")
    source = Path("rust/lighthouse-code-kernel/src/main.rs").read_text(encoding="utf-8")
    assert 'name = "lighthouse-code-kernel"' in cargo
    assert '"process/spawn"' in source
    assert '"process/terminate"' in source
    assert 'workspaceWrite' in source
