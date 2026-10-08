from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from commerce_support.resources.validation import (
    _validate_mining_enrichment,
    _validate_rag_source_preservation,
    _validate_section_summaries,
    _validate_unknown_parts,
    validate_extract_examples,
    validate_manifest_hashes,
    validate_rag_records,
    validate_support_resource_package,
    validate_taxonomy_records,
)

RESOURCE_ROOT = (
    Path(__file__).resolve().parents[1]
    / "src/commerce_support/resources/customer_support/v1"
)


def _record(
    *,
    disposition: str = "positive",
    point_refs: list[str] | None = None,
    evidence_text: str = "满99元包邮。",
    section_id: str = "billing-shipping::运费与包邮",
    unknown_parts: list[dict[str, object]] | None = None,
    should_refuse: bool = False,
) -> dict[str, object]:
    evidence_id = f"{section_id}#e1"
    return {
        "schema_version": 1,
        "sample_id": "A01",
        "bucket": "A_policy",
        "query": "多少元包邮",
        "source_annotation": {"expect_points": ["包邮"]},
        "review": {
            "status": "reviewed",
            "disposition": disposition,
            "reason": "核对当前运费规则。",
            "expected_answer_points": [
                {"text": "满99元包邮。", "evidence_refs": point_refs or [evidence_id]}
            ],
            "unknown_parts": unknown_parts or [],
            "should_refuse": should_refuse,
        },
        "sources": [
            {
                "section_id": section_id,
                "evidence": [{"id": evidence_id, "text": evidence_text}],
            }
        ],
    }


def test_rag_validator_accepts_literal_evidence_in_the_cited_section() -> None:
    record = _record()

    errors = validate_rag_records(
        [record],
        {"billing-shipping::运费与包邮": "单笔实付满99元包邮。"},
    )

    assert errors == []


def test_rag_validator_rejects_evidence_from_an_unrelated_section() -> None:
    record = _record(
        section_id="returns-policy::无理由退货",
        evidence_text="会员银卡享九折。",
    )

    errors = validate_rag_records(
        [record],
        {"returns-policy::无理由退货": "签收后7天内可以申请退货。"},
    )

    assert any("evidence is not present in cited section" in error for error in errors)


def test_rag_validator_rejects_answer_points_without_source_references() -> None:
    record = _record(point_refs=[])
    record["review"]["expected_answer_points"][0]["evidence_refs"] = []

    errors = validate_rag_records(
        [record],
        {"billing-shipping::运费与包邮": "单笔实付满99元包邮。"},
    )

    assert any("answer point has no evidence reference" in error for error in errors)


def test_rag_validator_requires_unknown_parts_for_a_partial_answer() -> None:
    record = _record(disposition="partial")
    record["review"]["unknown_parts"] = []

    errors = validate_rag_records(
        [record],
        {"billing-shipping::运费与包邮": "单笔实付满99元包邮。"},
    )

    assert any("partial requires at least one unknown part" in error for error in errors)


def test_rag_validator_distinguishes_missing_input_from_unsupported_policy() -> None:
    record = _record(
        unknown_parts=[
            {
                "text": "未提供商品型号。",
                "evidence_refs": [],
                "basis": "missing_input",
            }
        ]
    )

    errors = validate_rag_records(
        [record],
        {"billing-shipping::运费与包邮": "单笔实付满99元包邮。"},
    )

    assert errors == []


def test_auxiliary_validator_rejects_unknown_basis_and_unbound_explicit_fact() -> None:
    record = _record()
    record["review"]["unknown_parts"] = [
        {"text": "未知规则。", "basis": "guessed", "evidence_refs": []},
        {"text": "原文明确写明。", "basis": "explicit_source", "evidence_refs": ["missing#e1"]},
    ]

    errors = _validate_unknown_parts(
        [record],
        {"billing-shipping::运费与包邮": "单笔实付满99元包邮。"},
        "boundary_cases.json",
    )

    assert any("unsupported basis 'guessed'" in error for error in errors)
    assert any("unknown evidence reference 'missing#e1'" in error for error in errors)


def test_rag_provenance_validator_detects_changed_original_query(tmp_path) -> None:
    archived_path = tmp_path / "legacy/original/samples/eval_ch04.jsonl"
    archived_path.parent.mkdir(parents=True)
    original_rows = [
        {
            "id": f"A{index:03d}",
            "bucket": "A_policy",
            "query": f"query {index}",
            "expect_section": ["Returns"],
            "expect_points": ["7 days"],
            "should_refuse": False,
        }
        for index in range(300)
    ]
    archived_path.write_text(
        "\n".join(json.dumps(row) for row in original_rows), encoding="utf-8"
    )
    reviewed_rows = [
        {
            "sample_id": row["id"],
            "bucket": row["bucket"],
            "query": row["query"],
            "source_annotation": {
                "expect_section": row["expect_section"],
                "expect_sections_all": [],
                "expect_points": row["expect_points"],
                "should_refuse": row["should_refuse"],
            },
        }
        for row in original_rows
    ]
    reviewed_rows[42]["query"] = "silently changed query"
    source_index = [
        {
            "source_path": "tests/data/eval_ch04.jsonl",
            "reviewed_path": "samples/rag_reviewed.jsonl",
            "archived_path": "legacy/original/samples/eval_ch04.jsonl",
        }
    ]

    errors = _validate_rag_source_preservation(tmp_path, source_index, reviewed_rows)

    assert any("original query changed" in error for error in errors)


def test_section_summary_validator_checks_computed_character_count_and_hash() -> None:
    section = {
        "id": "returns::policy",
        "document_id": "returns",
        "document_path": "knowledge/returns.md",
        "title": "Policy",
        "heading_path": ["Returns", "Policy"],
        "heading_level": 2,
        "content_chars": 9,
        "content_sha256": "0" * 64,
        "faq_candidate": True,
    }
    catalog = {
        "heading_inventory": [
            {
                "id": "returns::policy",
                "document_id": "returns",
                "title": "Policy",
                "heading_path": ["Returns", "Policy"],
                "heading_level": 2,
            }
        ],
        "knowledge_sections": [section],
        "faq_sections": [section],
    }

    _counts, errors = _validate_section_summaries(catalog, {"returns::policy": "seven days"})

    assert any("content_chars mismatch" in error for error in errors)
    assert any("content_sha256 mismatch" in error for error in errors)


def test_mining_validator_rejects_enrichment_reference_outside_source_evidence() -> None:
    record = {
        "sample_id": "M01",
        "sources": [
            {
                "section_id": "billing-shipping::运费与包邮",
                "evidence": [
                    {
                        "id": "billing-shipping::运费与包邮#e1",
                        "text": "偏远地区加收12元附加运费。",
                    }
                ],
            }
        ],
        "review": {
            "canonical_enrichment": ["偏远附加费为12元。"],
            "canonical_enrichment_evidence_refs": ["other-section#e1"],
        },
    }

    errors = _validate_mining_enrichment(
        [record],
        {"billing-shipping::运费与包邮": "偏远地区加收12元附加运费。"},
    )

    assert any("unknown enrichment evidence ref 'other-section#e1'" in error for error in errors)


def test_rag_validator_rejects_duplicate_ids_and_false_refusal_labels() -> None:
    first = _record(disposition="no_evidence", should_refuse=False)
    second = _record()

    errors = validate_rag_records(
        [first, second],
        {"billing-shipping::运费与包邮": "单笔实付满99元包邮。"},
    )

    assert any("duplicate sample_id" in error for error in errors)
    assert any("no_evidence must refuse and have no answer points" in error for error in errors)


def test_extract_validator_checks_the_live_after_sales_schema() -> None:
    examples = [
        {
            "sample_id": "X04",
            "current_schema_expected": {
                "order_id": None,
                "request_type": "其他",
                "expected_solution": "用户投诉快递员态度差。",
            },
        }
    ]

    assert validate_extract_examples(examples) == []


def test_extract_validator_rejects_fields_outside_the_live_schema() -> None:
    examples = [
        {
            "sample_id": "X04",
            "current_schema_expected": {
                "order_id": None,
                "request_type": "投诉",
                "expected_solution": "用户投诉快递员态度差。",
            },
        }
    ]

    errors = validate_extract_examples(examples)

    assert any("current AfterSales schema validation failed" in error for error in errors)


def test_manifest_validator_requires_every_packaged_file_to_be_hashed(tmp_path) -> None:
    tracked = tmp_path / "knowledge.md"
    tracked.write_text("canonical text", encoding="utf-8")
    untracked = tmp_path / "samples.jsonl"
    untracked.write_text("reviewed examples", encoding="utf-8")
    manifest = {
        "resource_files": {
            "knowledge.md": hashlib.sha256(tracked.read_bytes()).hexdigest()
        },
        "source_files": [],
    }

    errors = validate_manifest_hashes(tmp_path, manifest)

    assert any("missing resource hash for samples.jsonl" in error for error in errors)


def test_manifest_validator_detects_a_tampered_packaged_file(tmp_path) -> None:
    resource = tmp_path / "knowledge.md"
    resource.write_text("changed canonical text", encoding="utf-8")
    manifest = {
        "resource_files": {"knowledge.md": "0" * 64},
        "source_files": [],
    }

    errors = validate_manifest_hashes(tmp_path, manifest)

    assert any("SHA-256 mismatch for knowledge.md" in error for error in errors)


def test_manifest_count_validator_rejects_stale_summary_counts() -> None:
    from commerce_support.resources.validation import _validate_manifest_counts

    errors = _validate_manifest_counts(
        {"counts": {"rag_samples": 299}},
        {"rag_samples": 300},
    )

    assert errors == ["manifest: counts.rag_samples is 299, expected 300 from resources"]


def test_taxonomy_validator_uses_catalog_ids_and_rejects_unknown_labels() -> None:
    taxonomy = {
        "class_count": 1,
        "classes": [{"label_id": 0, "name": "退换货"}],
    }
    samples = [
        {
            "sample_id": "G01",
            "review": {
                "status": "reviewed",
                "disposition": "positive",
                "reason": "标签描述原句表达的主题。",
                "reviewed_labels": ["未知标签"],
                "training_eligible": True,
            },
        }
    ]

    errors = validate_taxonomy_records(taxonomy, samples)

    assert any("unknown taxonomy label" in error for error in errors)


def test_taxonomy_validator_rejects_noncontiguous_ids_and_trainable_quarantine() -> None:
    taxonomy = {
        "class_count": 2,
        "classes": [
            {"label_id": 0, "name": "退换货"},
            {"label_id": 2, "name": "质量问题"},
        ],
    }
    samples = [
        {
            "sample_id": "G20",
            "review": {
                "status": "reviewed",
                "disposition": "quarantine",
                "reason": "标签边界仍有歧义。",
                "reviewed_labels": ["退换货"],
                "training_eligible": True,
            },
        }
    ]

    errors = validate_taxonomy_records(taxonomy, samples)

    assert any("label_id values must be unique and contiguous" in error for error in errors)
    assert any("quarantined sample must not be training eligible" in error for error in errors)


def test_packaged_customer_support_resources_pass_all_cross_file_checks() -> None:
    assert validate_support_resource_package() == []


def test_package_validator_rejects_review_summary_that_disagrees_with_samples(tmp_path) -> None:
    package = tmp_path / "v1"
    shutil.copytree(RESOURCE_ROOT, package)

    summary_path = package / "sample_review_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["rag_dispositions"]["positive"] += 1
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["resource_files"]["sample_review_summary.json"] = hashlib.sha256(
        summary_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    errors = validate_support_resource_package(package)

    assert any(
        "sample_review_summary: rag_dispositions differs from reviewed rows" in error
        for error in errors
    )
