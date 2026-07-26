from lighthouse.code_foundry import CodeActionKind, CodeToolContext, CodeToolSpec


def spec(
    kind: CodeActionKind,
    description: str,
    *,
    capability: str | None = "system.example.v1",
    parallel: bool = True,
    mutates: bool = False,
) -> CodeToolSpec:
    return CodeToolSpec(
        kind=kind,
        description=description,
        capability=capability,
        supports_parallel=parallel,
        mutates_workspace=mutates,
    )


def test_tool_context_renders_first_line_stably_and_escapes_xml_text():
    context = CodeToolContext(
        (
            spec(CodeActionKind.SEARCH, "  Find <symbols> & text.\nIgnore this line."),
            spec(CodeActionKind.READ, "Read one file."),
        )
    )

    assert context.render_diff(None) == (
        "<code_tools>\nAvailable CodeFoundry tools:\n"
        "- read: Read one file. [system.example.v1; parallel; read-only]\n"
        "- search: Find &lt;symbols&gt; &amp; text. [system.example.v1; parallel; read-only]\n"
        "</code_tools>"
    )


def test_tool_context_reports_updated_and_removed_tools_from_a_snapshot():
    first = CodeToolContext((spec(CodeActionKind.SEARCH, "Find symbols."), spec(CodeActionKind.READ, "Read files.")))
    second = CodeToolContext(
        (
            spec(CodeActionKind.SEARCH, "Find symbols.", parallel=False),
            spec(CodeActionKind.PATCH, "Apply a patch.", parallel=False, mutates=True),
        )
    )

    assert second.render_diff(first.snapshot()) == (
        "<code_tools>\nAdded or updated CodeFoundry tools:\n"
        "- patch: Apply a patch. [system.example.v1; serial; mutates workspace]\n"
        "- search: Find symbols. [system.example.v1; serial; read-only]\n"
        "Removed CodeFoundry tools:\n"
        "- read: Read files. [system.example.v1; parallel; read-only]\n"
        "</code_tools>"
    )


def test_tool_context_omits_unchanged_snapshot_and_bounds_large_rendering():
    context = CodeToolContext((spec(CodeActionKind.SEARCH, "Find symbols."),))
    assert context.render_diff(context.snapshot()) is None

    large = CodeToolContext(
        tuple(
            spec(
                kind,
                "&" * 250,
                capability=f"system.example.{index}.v1",
            )
            for index, kind in enumerate(CodeActionKind)
        )
    )
    rendered = large.render_diff(None)

    assert rendered is not None
    assert len(rendered.encode("utf-8")) <= 4 * 1024
    assert "additional tools omitted" in rendered
