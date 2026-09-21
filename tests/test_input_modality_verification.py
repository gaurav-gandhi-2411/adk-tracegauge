"""The claim "image and video input are priced at the text rate" rests on recorded evidence, not on a
past read: ``input_modality_verification`` in the price table names, per model, the raw vendor cell or
doc it was read from and when. These tests keep that record complete and internally consistent; the
weekly vendor check (scripts/check_price_table_vs_vendor.py) is what re-reads the pages."""

from __future__ import annotations

from datetime import date

from scripts.check_price_table_vs_vendor import _image_video_split

from adk_tracegauge._pricing import load_gemini_prices

_BASES = (
    "explicit: ",
    "single_input_price_listed: ",
    "images_billed_as_input_tokens: ",
    "not_applicable: ",
)


def test_every_table_entry_has_a_recorded_modality_basis_and_source():
    table = load_gemini_prices()
    record = table["input_modality_verification"]
    date.fromisoformat(record["checked_on"])
    assert set(record["by_model"]) == set(table["models"])
    for key, item in record["by_model"].items():
        assert item["basis"].startswith(_BASES), key
        assert item["source"], key


def test_a_recorded_gemini_cell_never_prices_image_or_video_apart_from_text():
    by_model = load_gemini_prices()["input_modality_verification"]["by_model"]
    cells = {k: v["cell"] for k, v in by_model.items() if "cell" in v}
    assert cells  # the Gemini entries carry the raw cell text they were read from
    for key, cell in cells.items():
        assert not _image_video_split(cell), key
    for key, item in by_model.items():
        if item["basis"].startswith("explicit: "):
            assert "text / image / video" in item["cell"], key
        if item["basis"].startswith("single_input_price_listed: "):
            assert "(" not in item["cell"].replace("(the > 200k", ""), key


def test_raw_page_hashes_are_recorded_for_every_source_named():
    record = load_gemini_prices()["input_modality_verification"]
    assert record["raw_page_sha256_prefix"]
    assert all(len(h) == 16 for h in record["raw_page_sha256_prefix"].values())
    assert record["fetched_at_utc"].startswith(record["checked_on"])
