"""Table tests for the spawn-death classifier."""

from sssf.postmortem import classify_failure


def test_missing_entry_file_hints_layout():
    tail = (
        "python: can't open file '/work/adws/modules/adw_simple_sdlc.py':"
        " [Errno 2] No such file or directory"
    )
    hint = classify_failure(tail, "2")
    assert "not in the worktree" in hint
    assert "git add -A && git commit" in hint
    assert "/work/adws/modules/adw_simple_sdlc.py" in hint


def test_no_such_file_naming_adws_hints_layout():
    tail = "python: can't open file '/work/adws/config/sssf.config.yaml': No such file or directory"
    assert "not in the worktree" in classify_failure(tail, "2")


def test_import_error_hints_stale_image():
    tail = (
        "ImportError: cannot import name 'paths' from 'sssf.adw_modules'"
        " (/usr/local/lib/python3.11/site-packages/sssf/adw_modules/__init__.py)"
    )
    assert "sssf sandbox build" in classify_failure(tail, "1")


def test_import_error_is_case_insensitive():
    tail = "modulenotfounderror: no module named 'sssf.adw_modules.paths'"
    assert "sssf sandbox build" in classify_failure(tail, "1")


def test_exit_127_hints_missing_binary():
    tail = "bun: command not found"
    hint = classify_failure(tail, "127")
    assert "missing from the runner image" in hint


def test_executable_not_found_hints_missing_binary():
    tail = 'exec: "snyk": executable file not found in $PATH'
    hint = classify_failure(tail, "1")
    assert "missing from the runner image" in hint
    assert "snyk" in hint  # the hint must name the missing binary (spec table)


def test_executable_not_found_without_quoted_binary_is_generic():
    # No `exec: "..."` name to extract — the hint still lands, just unnamed.
    tail = "executable file not found in $PATH"
    hint = classify_failure(tail, "1")
    assert "missing from the runner image" in hint
    assert "(" not in hint


def test_127_with_specific_signature_prefers_signature():
    # The entry-file error exits 2, but if a tail BOTH names adws/ and
    # carries a 127, the layout signature wins — specific before generic.
    tail = "can't open file '/work/adws/modules/x.py': No such file or directory"
    assert "not in the worktree" in classify_failure(tail, "127")


def test_layout_hint_is_length_capped():
    # Pathological long path must not blow the ≤ 300-char hint bound.
    tail = "can't open file '/work/adws/" + "a" * 500 + ".py': No such file or directory"
    hint = classify_failure(tail, "2")
    assert "not in the worktree" in hint
    assert len(hint) <= 300


def test_unknown_tail_passes_through():
    tail = "some mysterious failure line"
    assert classify_failure(tail, "1") == tail


def test_unknown_tail_is_trimmed_to_300():
    tail = "x" * 500
    assert len(classify_failure(tail, "1")) <= 300


def test_no_evidence_returns_none():
    assert classify_failure("", "") is None
    assert classify_failure("   ", "   ") is None


def test_empty_tail_with_exit_code():
    hint = classify_failure("", "137")
    assert "137" in hint
    assert "no output" in hint
