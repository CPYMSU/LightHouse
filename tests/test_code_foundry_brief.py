from __future__ import annotations

from lighthouse.code_foundry import CodeBriefCompiler


def test_compiler_builds_a_bounded_code_working_set_without_global_agent_state():
    brief = CodeBriefCompiler().compile(
        task="Fix the parser null-input regression.",
        project_context={
            "cwd": "/project",
            "instructions": [
                {"path": "AGENTS.md", "content": "Run parser tests."},
                {"path": "AGENTS.md", "content": "duplicate is ignored"},
            ],
            "files": ["ignored-by-the-brief.py"],
        },
        cognitive_context={
            "active_task": {"goal": "Restore parser input validation."},
            "verified_facts": [{"fact": "Parser errors use ValueError."}],
            "uncertainties": [{"claim": "Null handling may have changed."}],
            "relevant_files": [{"path": "src/parser.py"}],
            "recent_locators": [{"relative_path": "tests/test_parser.py"}],
            "available_agents": [{"role": "unrelated"}],
            "capability_world": {"complete_map": {"tool_count": 100}},
        },
        git_status={"branch": "main", "dirty": True},
        existing_diff="diff --git a/src/parser.py",
        test_commands=["pytest -q tests/test_parser.py"],
        relevant_files=["src/parser.py", "README.md"],
    )

    assert brief.repository_root == "/project"
    assert brief.instructions[0].path == "AGENTS.md"
    assert brief.relevant_files == ("src/parser.py", "README.md", "tests/test_parser.py")
    assert brief.verified_facts == ("Parser errors use ValueError.",)
    assert brief.uncertainties == ("Null handling may have changed.",)
    assert brief.active_task == "Restore parser input validation."
    assert brief.git_status == {"branch": "main", "dirty": True}
    assert "available_agents" not in brief.public_dict()
    assert "capability_world" not in brief.public_dict()


def test_compiler_requires_a_non_empty_task():
    compiler = CodeBriefCompiler()

    try:
        compiler.compile(task="   ")
    except ValueError as exc:
        assert str(exc) == "code brief task must not be empty"
    else:
        raise AssertionError("empty task should be rejected")
