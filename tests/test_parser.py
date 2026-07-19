from pathlib import Path

from synergy_harbor.parser import parse_synergy_jsonl

FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_step_usage() -> None:
    summary = parse_synergy_jsonl((FIXTURES / "success.jsonl").read_text())

    assert summary.input_tokens == 150
    assert summary.output_tokens == 35
    assert summary.cache_tokens == 17
    assert summary.cost_usd == 0.2
    assert summary.session_id == "ses_test"
    assert summary.step_count == 2
    assert summary.event_count == 5
    assert summary.error_count == 0


def test_empty_output_has_no_usage() -> None:
    summary = parse_synergy_jsonl("")

    assert summary.input_tokens is None
    assert summary.output_tokens is None
    assert summary.cost_usd is None
    assert summary.step_count == 0


def test_duplicate_step_update_is_not_double_counted() -> None:
    output = "\n".join(
        [
            '{"type":"step_finish","sessionID":"s","part":{"id":"x","type":"step-finish","cost":1,"tokens":{"input":2,"output":3,"reasoning":0,"cache":{"read":0,"write":0}}}}',
            '{"type":"step_finish","sessionID":"s","part":{"id":"x","type":"step-finish","cost":2,"tokens":{"input":4,"output":5,"reasoning":1,"cache":{"read":1,"write":2}}}}',
        ]
    )

    summary = parse_synergy_jsonl(output)

    assert summary.input_tokens == 4
    assert summary.output_tokens == 6
    assert summary.cache_tokens == 3
    assert summary.cost_usd == 2
    assert summary.step_count == 1


def test_malformed_lines_and_error_events_are_counted() -> None:
    output = 'not-json\n{"type":"error","sessionID":"s","error":{"name":"ApiError"}}\n'

    summary = parse_synergy_jsonl(output)

    assert summary.malformed_line_count == 1
    assert summary.error_count == 1
    assert summary.event_count == 1
