import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from investlab.contracts import Action
from investlab.journal import (
    DecisionFacts,
    EmptyReasoningError,
    EntryKind,
    Journal,
    export_evidence_packet,
    facts_from_order,
)

D = Decimal
AT = datetime(2026, 9, 8, 20, 5, tzinfo=UTC)


def facts(symbol="AAA"):
    return DecisionFacts(
        session=date(2026, 9, 8),
        symbol=symbol,
        action=Action.BUY,
        quantity=200,
        price=D("50.00"),
        indicators={"rsi_14": D("58.2"), "atr_14": D("1.85")},
        binding_constraints=("position_ceiling",),
        data_source="yfinance 1.7.0 cache 2026-09-08",
    )


def test_reasoning_is_stored_verbatim(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    text = "  i want exposure to semis before the sept guide.\nStop under the 20d low.  "
    entry = j.record(facts(), text, recorded_at=AT)
    assert entry.reasoning == text
    assert j.entries()[0].reasoning == text


def test_empty_reasoning_is_refused_and_nothing_is_written(tmp_path):
    path = tmp_path / "journal.jsonl"
    j = Journal(path)
    with pytest.raises(EmptyReasoningError):
        j.record(facts(), "   \n  ", recorded_at=AT)
    assert not path.exists() or path.read_text() == ""


def test_the_journal_never_generates_reasoning():
    """Guardrail: no code path in this module may author student text."""
    import inspect

    from investlab import journal

    source = inspect.getsource(journal)
    for banned in (
        "def generate_",
        "def suggest_",
        "def draft_",
        "def autocomplete",
        "def template_",
    ):
        assert banned not in source
    sig = inspect.signature(journal.Journal.record)
    assert sig.parameters["reasoning"].default is inspect.Parameter.empty


def test_entries_are_append_only_and_a_correction_references_the_original(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    first = j.record(facts(), "Original reasoning.", recorded_at=AT)
    corrected = j.amend(first.entry_id, "I mis-stated the stop; it was 46.00.", recorded_at=AT)
    entries = j.entries()
    assert len(entries) == 2
    assert entries[0].reasoning == "Original reasoning."  # history untouched
    assert corrected.kind is EntryKind.CORRECTION
    assert corrected.corrects == first.entry_id


def test_amending_an_unknown_entry_is_refused(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    with pytest.raises(KeyError):
        j.amend("JE-999999", "text", recorded_at=AT)


def test_storage_is_one_json_object_per_line(tmp_path):
    path = tmp_path / "journal.jsonl"
    j = Journal(path)
    j.record(facts("AAA"), "a", recorded_at=AT)
    j.record(facts("BBB"), "b", recorded_at=AT)
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["facts"]["symbol"] == "AAA"
    assert json.loads(lines[0])["facts"]["price"] == "50.00"  # Decimal as string


def test_hash_chain_detects_edited_history(tmp_path):
    path = tmp_path / "journal.jsonl"
    j = Journal(path)
    j.record(facts(), "Original reasoning.", recorded_at=AT)
    j.record(facts("BBB"), "Second entry.", recorded_at=AT)
    assert j.verify_chain()

    lines = path.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["reasoning"] = "Something I never wrote."
    lines[0] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n")
    assert not Journal(path).verify_chain()


def test_facts_from_order_carries_the_binding_constraint(tmp_path):
    from investlab.contracts import SizingConstraints
    from investlab.portfolio.sizing import size_order

    con = SizingConstraints(
        equity=D("100000.00"),
        spendable_cash=D("100000.00"),
        risk_fraction=D("0.01"),
        position_ceiling_fraction=D("0.10"),
        min_shares=1,
        min_price=D("3.00"),
        commission_per_trade=D("0"),
    )
    order = size_order("AAA", D("50.00"), D("46.00"), con)
    f = facts_from_order(order, date(2026, 9, 8), {"rsi_14": D("58.2")}, "yfinance cache")
    assert f.symbol == "AAA"
    assert f.quantity == 200
    assert "position_ceiling" in f.binding_constraints


def test_evidence_packet_contains_student_text_facts_and_a_provenance_footer(tmp_path):
    j = Journal(tmp_path / "journal.jsonl")
    j.record(facts(), "My own reasoning, in my own words.", recorded_at=AT)
    packet = export_evidence_packet(j.entries(), data_sources=("yfinance 1.7.0",), generated_at=AT)
    assert "My own reasoning, in my own words." in packet
    assert "AAA" in packet and "200" in packet and "50.00" in packet
    assert "rsi_14" in packet
    assert "investlab" in packet
    assert "yfinance 1.7.0" in packet
    assert "student-authored" in packet.lower()
