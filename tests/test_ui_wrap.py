from __future__ import annotations

from io import StringIO

from rich.console import Console

from lighthouse.ui import SwissTerminal


def test_long_single_line_final_message_is_not_truncated():
    stream = StringIO()
    console = Console(
        file=stream,
        width=52,
        color_system=None,
        force_terminal=False,
        highlight=False,
    )
    ui = SwissTerminal(console)
    message = "已完成網頁升級，" + "加入更多內容與交互效果，" * 12 + "完整結尾標記"
    ui.final({"run": {"status": "succeeded", "final_message": message}})
    output = stream.getvalue()
    assert "已完成網頁升級" in output
    assert "完整結尾標記" in output


def test_long_single_line_input_card_is_not_truncated():
    stream = StringIO()
    console = Console(
        file=stream,
        width=48,
        color_system=None,
        force_terminal=False,
        highlight=False,
    )
    ui = SwissTerminal(console)
    message = "請確認這個很長的問題：" + "需要完整顯示全部文字，" * 10 + "問題結尾"
    ui.notice("INPUT REQUIRED", message, tone="amber")
    output = stream.getvalue()
    assert "請確認這個很長的問題" in output
    assert "問題結尾" in output
