import pytest

from lighthouse.models import TargetKind
from lighthouse.targets import validate_target_config


def test_local_system_defaults_and_roots():
    config = validate_target_config(
        TargetKind.SYSTEM,
        {"transport": "local", "default_cwd": "/opt/project"},
    )
    assert config["shell"] == "/bin/bash"
    assert config["allowed_roots"] == ["/opt/project"]
    assert config["strict_host_key"] if "strict_host_key" in config else True


def test_ssh_secret_references_are_environment_names():
    config = validate_target_config(
        TargetKind.SYSTEM,
        {
            "transport": "ssh",
            "host": "server.example.com",
            "user": "warehouse",
            "identity_file_env": "WAREHOUSE_SSH_KEY",
            "known_hosts_env": "WAREHOUSE_KNOWN_HOSTS",
            "default_cwd": "/opt/warehouse",
        },
    )
    assert config["strict_host_key"] is True
    assert config["identity_file_env"] == "WAREHOUSE_SSH_KEY"


def test_path_escape_is_rejected():
    with pytest.raises(ValueError):
        validate_target_config(
            TargetKind.SYSTEM,
            {
                "transport": "local",
                "default_cwd": "/opt/project",
                "project_instruction_files": ["../secret"],
            },
        )
