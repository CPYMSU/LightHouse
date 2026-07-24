from lighthouse import terminal


def test_help_describes_integrated_terminal(capsys):
    assert terminal.main(["help"]) == 0
    output = capsys.readouterr().out
    assert "integrated LightHouse terminal" in output
    assert "lh agent" in output
