import pytest

from ingest import parse_args


def test_defaults_to_all_documents():
    args = parse_args([])
    assert args.docs == ["gpo", "gep", "imac"]
    assert args.reset is False


def test_doc_can_be_repeated_and_unknown_docs_are_rejected():
    assert parse_args(["--doc", "imac", "--doc", "gpo", "--reset"]).docs == ["imac", "gpo"]
    with pytest.raises(SystemExit):
        parse_args(["--doc", "nope"])
