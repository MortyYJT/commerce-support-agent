from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from importlib.resources import as_file, files
from pathlib import Path

from pydantic import ValidationError

from commerce_support.schemas import AfterSales

_RAG_DISPOSITIONS = {
    "positive",
    "partial",
    "clarify",
    "no_evidence",
    "explicit_negative",
}
_UNKNOWN_BASES = {
    "explicit_source",
    "missing_input",
    "not_defined_in_canonical_sources",
}


def _mapping(value: object) -> Mapping[str, object] | None:
    return value if isinstance(value, Mapping) else None


def _list(value: object) -> list[object] | None:
    return value if isinstance(value, list) else None


def _evidence_index(
    record: Mapping[str, object],
    section_bodies: Mapping[str, str],
    errors: list[str],
    sample_id: str,
) -> dict[str, str]:
    indexed: dict[str, str] = {}
    sources = _list(record.get("sources"))
    if sources is None:
        errors.append(f"{sample_id}: sources must be a list")
        return indexed

    for source_index, raw_source in enumerate(sources):
        source = _mapping(raw_source)
        if source is None:
            errors.append(f"{sample_id}: source {source_index} must be an object")
            continue
        section_id = source.get("section_id")
        if not isinstance(section_id, str) or section_id not in section_bodies:
            errors.append(f"{sample_id}: unknown source section_id {section_id!r}")
            continue
        evidence_rows = _list(source.get("evidence"))
        if evidence_rows is None:
            errors.append(f"{sample_id}: evidence for {section_id} must be a list")
            continue
        for evidence_index, raw_evidence in enumerate(evidence_rows):
            evidence = _mapping(raw_evidence)
            if evidence is None:
                errors.append(
                    f"{sample_id}: evidence {evidence_index} in {section_id} must be an object"
                )
                continue
            evidence_id = evidence.get("id")
            text = evidence.get("text")
            if not isinstance(evidence_id, str) or not evidence_id:
                errors.append(f"{sample_id}: evidence id in {section_id} must be non-empty")
                continue
            if evidence_id in indexed:
                errors.append(f"{sample_id}: duplicate evidence id {evidence_id}")
                continue
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{sample_id}: evidence {evidence_id} has empty text")
                continue
            if text not in section_bodies[section_id]:
                errors.append(
                    f"{sample_id}: evidence is not present in cited section {section_id}: "
                    f"{evidence_id}"
                )
                continue
            indexed[evidence_id] = section_id
    return indexed


def _validate_refs(
    refs_value: object,
    evidence_index: Mapping[str, str],
    errors: list[str],
    location: str,
    *,
    required: bool,
) -> list[str]:
    refs = _list(refs_value)
    if refs is None:
        errors.append(f"{location}: evidence_refs must be a list")
        return []
    if required and not refs:
        errors.append(f"{location}: answer point has no evidence reference")
    valid_refs: list[str] = []
    for ref in refs:
        if not isinstance(ref, str) or ref not in evidence_index:
            errors.append(f"{location}: unknown evidence reference {ref!r}")
        else:
            valid_refs.append(ref)
    return valid_refs


def validate_rag_records(
    records: Sequence[Mapping[str, object]], section_bodies: Mapping[str, str]
) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, record in enumerate(records):
        sample_id_value = record.get("sample_id")
        sample_id = sample_id_value if isinstance(sample_id_value, str) else f"row-{index + 1}"
        if not isinstance(sample_id_value, str) or not sample_id_value.strip():
            errors.append(f"{sample_id}: sample_id must be non-empty")
        elif sample_id in seen_ids:
            errors.append(f"{sample_id}: duplicate sample_id")
        else:
            seen_ids.add(sample_id)

        review = _mapping(record.get("review"))
        if review is None:
            errors.append(f"{sample_id}: review must be an object")
            continue
        if review.get("status") != "reviewed":
            errors.append(f"{sample_id}: review status must be reviewed")
        reason = review.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{sample_id}: review reason must be non-empty")
        disposition = review.get("disposition")
        if disposition not in _RAG_DISPOSITIONS:
            errors.append(f"{sample_id}: unsupported disposition {disposition!r}")
        should_refuse = review.get("should_refuse")
        if not isinstance(should_refuse, bool):
            errors.append(f"{sample_id}: should_refuse must be boolean")

        evidence_index = _evidence_index(record, section_bodies, errors, sample_id)
        points = _list(review.get("expected_answer_points"))
        if points is None:
            errors.append(f"{sample_id}: expected_answer_points must be a list")
            points = []
        for point_index, raw_point in enumerate(points):
            point = _mapping(raw_point)
            location = f"{sample_id}: answer point {point_index + 1}"
            if point is None:
                errors.append(f"{location} must be an object")
                continue
            text = point.get("text")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{location} text must be non-empty")
            _validate_refs(
                point.get("evidence_refs"), evidence_index, errors, location, required=True
            )

        unknown_parts = _list(review.get("unknown_parts"))
        if unknown_parts is None:
            errors.append(f"{sample_id}: unknown_parts must be a list")
            unknown_parts = []
        for part_index, raw_part in enumerate(unknown_parts):
            location = f"{sample_id}: unknown part {part_index + 1}"
            part = _mapping(raw_part)
            if part is None:
                errors.append(f"{location} must be an object")
                continue
            text = part.get("text")
            basis = part.get("basis")
            if not isinstance(text, str) or not text.strip():
                errors.append(f"{location} text must be non-empty")
            if basis not in _UNKNOWN_BASES:
                errors.append(f"{location} has unsupported basis {basis!r}")
            refs = _validate_refs(
                part.get("evidence_refs"),
                evidence_index,
                errors,
                location,
                required=basis == "explicit_source",
            )
            if basis == "not_defined_in_canonical_sources" and refs:
                errors.append(f"{location}: undefined rules must not invent evidence references")

        if disposition == "no_evidence":
            if should_refuse is not True or points or record.get("sources"):
                errors.append(
                    f"{sample_id}: no_evidence must refuse and have no answer points or sources"
                )
            if not unknown_parts:
                errors.append(f"{sample_id}: no_evidence requires an unknown part")
        elif disposition in {"positive", "partial", "explicit_negative"}:
            if should_refuse is not False:
                errors.append(f"{sample_id}: {disposition} must not refuse")
            if not points:
                errors.append(f"{sample_id}: {disposition} requires an answer point")
        elif disposition == "clarify" and should_refuse is not False:
            errors.append(f"{sample_id}: clarify must not be labeled as a refusal")
    return errors


def validate_extract_examples(examples: Sequence[Mapping[str, object]]) -> list[str]:
    errors: list[str] = []
    seen_ids: set[str] = set()
    for index, example in enumerate(examples):
        sample_id_value = example.get("sample_id")
        sample_id = sample_id_value if isinstance(sample_id_value, str) else f"row-{index + 1}"
        if not isinstance(sample_id_value, str) or not sample_id_value.strip():
            errors.append(f"{sample_id}: sample_id must be non-empty")
        elif sample_id in seen_ids:
            errors.append(f"{sample_id}: duplicate sample_id")
        else:
            seen_ids.add(sample_id)
        expected = _mapping(example.get("current_schema_expected"))
        if expected is None:
            errors.append(f"{sample_id}: current_schema_expected must be an object")
            continue
        try:
            AfterSales.model_validate(dict(expected))
        except ValidationError as exc:
            errors.append(f"{sample_id}: current AfterSales schema validation failed: {exc}")
    return errors


def validate_manifest_hashes(package_root: Path, manifest: Mapping[str, object]) -> list[str]:
    errors: list[str] = []
    resources = _mapping(manifest.get("resource_files"))
    if resources is None:
        return ["manifest: resource_files must be an object"]
    for relative_path, expected_hash in resources.items():
        if not isinstance(relative_path, str) or not isinstance(expected_hash, str):
            errors.append("manifest: resource file paths and hashes must be strings")
            continue
        target = package_root / relative_path
        if not target.is_file():
            errors.append(f"manifest: missing resource file {relative_path}")
            continue
        actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            errors.append(f"manifest: SHA-256 mismatch for {relative_path}")

    packaged_files = {
        path.relative_to(package_root).as_posix()
        for path in package_root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    for relative_path in sorted(packaged_files - set(resources)):
        errors.append(f"manifest: missing resource hash for {relative_path}")

    source_files = _list(manifest.get("source_files"))
    if source_files is None:
        errors.append("manifest: source_files must be a list")
        return errors
    source_revision = _mapping(manifest.get("source_snapshot"))
    expected_revision = source_revision.get("revision") if source_revision else None
    for index, raw_source in enumerate(source_files):
        source = _mapping(raw_source)
        if source is None:
            errors.append(f"manifest: source_files[{index}] must be an object")
            continue
        archived_path = source.get("archived_path")
        expected_hash = source.get("source_sha256")
        revision = source.get("source_revision", expected_revision)
        if revision != expected_revision:
            errors.append(f"manifest: source revision mismatch for {archived_path!r}")
        if not isinstance(archived_path, str) or not isinstance(expected_hash, str):
            errors.append(f"manifest: source_files[{index}] has invalid archive metadata")
            continue
        target = package_root / archived_path
        if not target.is_file():
            errors.append(f"manifest: missing archived source {archived_path}")
            continue
        actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            errors.append(f"manifest: source SHA-256 mismatch for {archived_path}")
    return errors


def _section_bodies(package_root: Path, catalog: Mapping[str, object]) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    rows = _list(catalog.get("heading_inventory"))
    if rows is None:
        return {}, ["sections: heading_inventory must be a list"]
    bodies: dict[str, str] = {}
    seen_ids: set[str] = set()
    grouped: dict[str, list[tuple[int, int, str, list[str]]]] = {}
    for index, raw_row in enumerate(rows):
        row = _mapping(raw_row)
        if row is None:
            errors.append(f"sections: heading_inventory[{index}] must be an object")
            continue
        section_id = row.get("id")
        document_id = row.get("document_id")
        level = row.get("heading_level")
        title = row.get("title")
        heading_path = row.get("heading_path")
        if not all(isinstance(item, str) and item for item in (section_id, document_id, title)):
            errors.append(f"sections: heading_inventory[{index}] has invalid identifiers")
            continue
        if (
            not isinstance(level, int)
            or isinstance(level, bool)
            or not isinstance(heading_path, list)
            or not all(isinstance(part, str) and part for part in heading_path)
        ):
            errors.append(f"sections: heading_inventory[{index}] has invalid heading metadata")
            continue
        if section_id in seen_ids:
            errors.append(f"sections: duplicate section id {section_id}")
            continue
        seen_ids.add(section_id)
        doc_path = package_root / "knowledge" / f"{document_id}.md"
        if not doc_path.is_file():
            errors.append(f"sections: missing knowledge document {doc_path.name}")
            continue
        grouped.setdefault(document_id, []).append((index, level, title, heading_path))

    for document_id, rows_for_doc in grouped.items():
        lines = (package_root / "knowledge" / f"{document_id}.md").read_text(
            encoding="utf-8"
        ).splitlines()
        headings: list[tuple[int, int, str, list[str]]] = []
        stack: list[tuple[int, str]] = []
        for line_number, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if not match:
                continue
            level, title = len(match.group(1)), match.group(2)
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            headings.append((line_number, level, title, [item[1] for item in stack]))
        catalog_heading_keys = Counter(
            (level, title, tuple(heading_path))
            for _, level, title, heading_path in rows_for_doc
        )
        markdown_heading_keys = Counter(
            (level, title, tuple(heading_path))
            for _, level, title, heading_path in headings
        )
        if catalog_heading_keys != markdown_heading_keys:
            errors.append(
                f"sections: heading inventory does not match all Markdown headings in {document_id}"
            )
        for catalog_index, level, title, heading_path in rows_for_doc:
            row = _mapping(rows[catalog_index])
            assert row is not None
            section_id = row["id"]
            matches = [
                index
                for index, (_, candidate_level, candidate_title, candidate_path) in enumerate(
                    headings
                )
                if candidate_level == level
                and candidate_title == title
                and candidate_path == heading_path
            ]
            if len(matches) != 1:
                errors.append(
                    f"sections: {section_id} maps to {len(matches)} Markdown headings"
                )
                continue
            heading_index = matches[0]
            start = headings[heading_index][0]
            end = headings[heading_index + 1][0] if heading_index + 1 < len(headings) else len(lines)
            bodies[section_id] = "\n".join(lines[start + 1 : end]).strip()
    return bodies, errors


def _validate_section_summaries(
    catalog: Mapping[str, object], section_bodies: Mapping[str, str]
) -> tuple[dict[str, int], list[str]]:
    errors: list[str] = []
    inventory = _list(catalog.get("heading_inventory")) or []
    inventory_by_id = {
        row.get("id"): row
        for raw in inventory
        if (row := _mapping(raw)) is not None and isinstance(row.get("id"), str)
    }
    knowledge_rows = _list(catalog.get("knowledge_sections"))
    faq_rows = _list(catalog.get("faq_sections"))
    if knowledge_rows is None:
        errors.append("sections: knowledge_sections must be a list")
        knowledge_rows = []
    if faq_rows is None:
        errors.append("sections: faq_sections must be a list")
        faq_rows = []

    knowledge_by_id: dict[str, Mapping[str, object]] = {}
    for index, raw in enumerate(knowledge_rows):
        row = _mapping(raw)
        if row is None:
            errors.append(f"sections: knowledge_sections[{index}] must be an object")
            continue
        section_id = row.get("id")
        if not isinstance(section_id, str) or not section_id:
            errors.append(f"sections: knowledge_sections[{index}] has no section ID")
            continue
        if section_id in knowledge_by_id:
            errors.append(f"sections: duplicate knowledge section {section_id}")
            continue
        knowledge_by_id[section_id] = row
        body = section_bodies.get(section_id)
        heading = inventory_by_id.get(section_id)
        if body is None or not body.strip():
            errors.append(f"sections: knowledge section {section_id} has no parsed body")
            continue
        if heading is None:
            errors.append(f"sections: knowledge section {section_id} is absent from heading inventory")
        elif any(row.get(key) != heading.get(key) for key in ("document_id", "title", "heading_level", "heading_path")):
            errors.append(f"sections: knowledge metadata differs from heading inventory for {section_id}")
        expected_path = f"knowledge/{row.get('document_id')}.md"
        if row.get("document_path") != expected_path:
            errors.append(f"sections: invalid document_path for {section_id}")
        content_chars = row.get("content_chars")
        if not isinstance(content_chars, int) or isinstance(content_chars, bool) or content_chars != len(body):
            errors.append(f"sections: content_chars mismatch for {section_id}")
        digest = row.get("content_sha256")
        expected_digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if digest != expected_digest:
            errors.append(f"sections: content_sha256 mismatch for {section_id}")
        if not isinstance(row.get("faq_candidate"), bool):
            errors.append(f"sections: faq_candidate must be boolean for {section_id}")

    nonempty_heading_ids = {
        section_id for section_id, body in section_bodies.items() if body.strip()
    }
    for section_id, heading in inventory_by_id.items():
        if not isinstance(heading, Mapping):
            continue
        body_exists = bool(section_bodies.get(section_id, "").strip())
        if heading.get("has_body") is not body_exists:
            errors.append(f"sections: has_body flag differs from parsed Markdown for {section_id}")
    if set(knowledge_by_id) != nonempty_heading_ids:
        errors.append("sections: knowledge_sections do not cover exactly the non-empty headings")

    faq_ids: list[str] = []
    for index, raw in enumerate(faq_rows):
        row = _mapping(raw)
        if row is None:
            errors.append(f"sections: faq_sections[{index}] must be an object")
            continue
        section_id = row.get("id")
        if not isinstance(section_id, str) or not section_id:
            errors.append(f"sections: faq_sections[{index}] has no section ID")
            continue
        faq_ids.append(section_id)
        source = knowledge_by_id.get(section_id)
        if source is None:
            errors.append(f"sections: FAQ section {section_id} is absent from knowledge_sections")
        elif row != source:
            errors.append(f"sections: FAQ section metadata differs for {section_id}")
    if len(faq_ids) != len(set(faq_ids)):
        errors.append("sections: faq_sections contains duplicate IDs")
    expected_faq_ids = {
        section_id
        for section_id, row in knowledge_by_id.items()
        if row.get("faq_candidate") is True
    }
    if set(faq_ids) != expected_faq_ids:
        errors.append("sections: faq_sections do not match faq_candidate sections")

    return (
        {
            "knowledge_sections": len(knowledge_rows),
            "faq_sections": len(faq_rows),
            "heading_nodes_h2_h3": sum(
                isinstance((row := _mapping(raw)), Mapping)
                and row.get("heading_level") in (2, 3)
                for raw in inventory
            ),
        },
        errors,
    )


def _read_json(path: Path, errors: list[str]) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"cannot read {path.name}: {exc}")
        return None


def _read_jsonl(path: Path, errors: list[str]) -> list[Mapping[str, object]]:
    records: list[Mapping[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        errors.append(f"cannot read {path.name}: {exc}")
        return records
    for line_number, line in enumerate(lines, 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}:{line_number}: invalid JSON: {exc}")
            continue
        if not isinstance(record, Mapping):
            errors.append(f"{path.name}:{line_number}: record must be an object")
            continue
        records.append(record)
    return records


def _validate_rag_source_preservation(
    package_root: Path,
    source_index: Sequence[Mapping[str, object]],
    reviewed_records: Sequence[Mapping[str, object]],
) -> list[str]:
    errors: list[str] = []
    source_row = next(
        (
            row
            for row in source_index
            if row.get("reviewed_path") == "samples/rag_reviewed.jsonl"
            and row.get("source_path") == "tests/data/eval_ch04.jsonl"
        ),
        None,
    )
    if source_row is None or not isinstance(source_row.get("archived_path"), str):
        return ["rag provenance: source_index has no archived eval_ch04 source"]
    original = _read_jsonl(package_root / source_row["archived_path"], errors)
    if len(original) != 300 or len(reviewed_records) != 300:
        errors.append(
            f"rag provenance: expected 300 originals and reviewed rows, found "
            f"{len(original)} and {len(reviewed_records)}"
        )
    original_ids: set[str] = set()
    reviewed_ids: set[str] = set()
    annotation_fields = {
        "expect_section": [],
        "expect_sections_all": [],
        "expect_points": [],
        "should_refuse": False,
    }
    for index, (source, reviewed) in enumerate(zip(original, reviewed_records, strict=False), 1):
        source_id = source.get("id")
        sample_id = reviewed.get("sample_id")
        if not isinstance(source_id, str) or not source_id:
            errors.append(f"rag provenance: original row {index} has no ID")
            continue
        if source_id in original_ids:
            errors.append(f"rag provenance: duplicate original ID {source_id}")
        original_ids.add(source_id)
        if not isinstance(sample_id, str) or not sample_id:
            errors.append(f"rag provenance: reviewed row {index} has no sample_id")
            continue
        if sample_id in reviewed_ids:
            errors.append(f"rag provenance: duplicate reviewed ID {sample_id}")
        reviewed_ids.add(sample_id)
        if sample_id != source_id:
            errors.append(
                f"rag provenance: original row {index} ID {source_id} changed to {sample_id}"
            )
        for field_name in ("query", "bucket"):
            if reviewed.get(field_name) != source.get(field_name):
                errors.append(f"rag provenance:{source_id}: original {field_name} changed")
        expected_annotation = {
            key: source.get(key, default) for key, default in annotation_fields.items()
        }
        if reviewed.get("source_annotation") != expected_annotation:
            errors.append(f"rag provenance:{source_id}: source_annotation differs from original")
    if original_ids != reviewed_ids:
        errors.append("rag provenance: reviewed IDs do not equal original IDs")
    return errors


def _validate_classification_source_preservation(
    package_root: Path,
    source_index: Sequence[Mapping[str, object]],
    reviewed_records: Sequence[Mapping[str, object]],
) -> list[str]:
    errors: list[str] = []
    for source_row in source_index:
        source_path = source_row.get("source_path")
        archived_path = source_row.get("archived_path")
        if not isinstance(source_path, str) or not isinstance(archived_path, str):
            continue
        if source_path.endswith("golden_samples.jsonl"):
            sample_set = "golden"
        elif source_path.endswith("supplement_sizefit.jsonl"):
            sample_set = "supplement"
        else:
            continue
        original = _read_jsonl(package_root / archived_path, errors)
        reviewed = [row for row in reviewed_records if row.get("sample_set") == sample_set]
        if len(original) != len(reviewed):
            errors.append(
                f"classification provenance:{sample_set}: expected {len(original)} reviewed rows, "
                f"found {len(reviewed)}"
            )
        for index, (source, current) in enumerate(zip(original, reviewed, strict=False), 1):
            expected_annotation = {key: source.get(key) for key in ("text", "labels")}
            if current.get("source_annotation") != expected_annotation:
                errors.append(
                    f"classification provenance:{sample_set}:{index}: source annotation changed"
                )
    return errors


def _validate_source_index(
    package_root: Path, manifest: Mapping[str, object]
) -> list[str]:
    errors: list[str] = []
    index_path = package_root / "samples" / "source_index.json"
    data = _read_json(index_path, errors)
    rows = _list(data)
    if rows is None:
        errors.append("source_index: expected a list")
        return errors
    seen_sources: set[str] = set()
    source_snapshot = _mapping(manifest.get("source_snapshot"))
    expected_revision = source_snapshot.get("revision") if source_snapshot else None
    manifest_sources = _list(manifest.get("source_files")) or []
    manifest_by_source = {
        row.get("source_path"): row
        for raw in manifest_sources
        if (row := _mapping(raw)) is not None and isinstance(row.get("source_path"), str)
    }
    for position, raw_row in enumerate(rows):
        row = _mapping(raw_row)
        if row is None:
            errors.append(f"source_index[{position}]: expected an object")
            continue
        archived_path = row.get("archived_path")
        source_path = row.get("source_path")
        revision = row.get("source_revision")
        digest = row.get("source_sha256")
        size = row.get("source_bytes")
        reviewed_path = row.get("reviewed_path")
        if not all(isinstance(value, str) and value for value in (archived_path, source_path, revision, digest, reviewed_path)):
            errors.append(f"source_index[{position}]: missing provenance fields")
            continue
        if source_path in seen_sources:
            errors.append(f"source_index: duplicate source path {source_path}")
        seen_sources.add(source_path)
        if revision != expected_revision:
            errors.append(f"source_index: source revision mismatch for {source_path}")
        manifest_source = manifest_by_source.get(source_path)
        if manifest_source is not None and any(
            row.get(source_key) != manifest_source.get(manifest_key)
            for source_key, manifest_key in (
                ("archived_path", "archived_path"),
                ("source_sha256", "source_sha256"),
            )
        ):
            errors.append(f"source_index: provenance differs from manifest for {source_path}")
        archived = package_root / archived_path
        reviewed = package_root / reviewed_path
        if not archived.is_file():
            errors.append(f"source_index: missing archive {archived_path}")
            continue
        raw = archived.read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            errors.append(f"source_index: source SHA-256 mismatch for {archived_path}")
        if not isinstance(size, int) or isinstance(size, bool):
            errors.append(f"source_index: source byte count is invalid for {archived_path}")
        elif len(raw) != size:
            errors.append(f"source_index: source byte count mismatch for {archived_path}")
        if not reviewed.is_file():
            errors.append(f"source_index: missing reviewed file {reviewed_path}")
    return errors


def _validate_sample_review(records: Sequence[Mapping[str, object]], file_name: str) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    for index, record in enumerate(records):
        sample_id = record.get("sample_id", record.get("case_id"))
        if not isinstance(sample_id, str) or not sample_id.strip():
            errors.append(f"{file_name}:{index + 1}: sample id must be non-empty")
        elif sample_id in ids:
            errors.append(f"{file_name}: duplicate sample id {sample_id}")
        else:
            ids.add(sample_id)
        review = _mapping(record.get("review"))
        if review is None:
            errors.append(f"{file_name}:{sample_id}: review must be an object")
            continue
        if review.get("status") != "reviewed":
            errors.append(f"{file_name}:{sample_id}: status must be reviewed")
        if not isinstance(review.get("reason"), str) or not str(review.get("reason")).strip():
            errors.append(f"{file_name}:{sample_id}: reason must be non-empty")
    return errors


def validate_taxonomy_records(
    taxonomy: Mapping[str, object], samples: Sequence[Mapping[str, object]]
) -> list[str]:
    errors: list[str] = []
    classes = _list(taxonomy.get("classes"))
    declared_count = taxonomy.get("class_count")
    if classes is None:
        return ["taxonomy: classes must be a list"]
    if not isinstance(declared_count, int) or declared_count != len(classes):
        errors.append("taxonomy: class_count must equal the number of classes")

    label_ids: list[int] = []
    names: set[str] = set()
    for index, raw_class in enumerate(classes):
        item = _mapping(raw_class)
        if item is None:
            errors.append(f"taxonomy: class {index + 1} must be an object")
            continue
        label_id = item.get("label_id")
        name = item.get("name")
        if not isinstance(label_id, int) or isinstance(label_id, bool):
            errors.append(f"taxonomy: class {index + 1} label_id must be an integer")
        else:
            label_ids.append(label_id)
        if not isinstance(name, str) or not name.strip():
            errors.append(f"taxonomy: class {index + 1} name must be non-empty")
        elif name in names:
            errors.append(f"taxonomy: duplicate class name {name}")
        else:
            names.add(name)
    if sorted(label_ids) != list(range(len(classes))) or len(set(label_ids)) != len(label_ids):
        errors.append("taxonomy: label_id values must be unique and contiguous from zero")

    seen_ids: set[str] = set()
    for index, sample in enumerate(samples):
        sample_id_value = sample.get("sample_id")
        sample_id = sample_id_value if isinstance(sample_id_value, str) else f"row-{index + 1}"
        if sample_id in seen_ids:
            errors.append(f"classification: duplicate sample_id {sample_id}")
        seen_ids.add(sample_id)
        review = _mapping(sample.get("review"))
        if review is None:
            errors.append(f"classification:{sample_id}: review must be an object")
            continue
        disposition = review.get("disposition")
        eligible = review.get("training_eligible")
        if not isinstance(eligible, bool):
            errors.append(f"classification:{sample_id}: training_eligible must be boolean")
        if disposition == "quarantine" and eligible is not False:
            errors.append(f"classification:{sample_id}: quarantined sample must not be training eligible")
        reviewed_labels = _list(review.get("reviewed_labels"))
        if reviewed_labels is None:
            errors.append(f"classification:{sample_id}: reviewed_labels must be a list")
            continue
        for label in reviewed_labels:
            if not isinstance(label, str) or label not in names:
                errors.append(f"classification:{sample_id}: unknown taxonomy label {label!r}")
        annotation = _mapping(sample.get("source_annotation"))
        if annotation is not None:
            source_labels = _list(annotation.get("labels"))
            if source_labels is not None:
                for label in source_labels:
                    if not isinstance(label, str) or label not in names:
                        errors.append(
                            f"classification:{sample_id}: source has unknown taxonomy label {label!r}"
                        )
    return errors


def _validate_auxiliary_sources(
    records: Sequence[Mapping[str, object]], section_bodies: Mapping[str, str], file_name: str
) -> list[str]:
    errors: list[str] = []
    for index, record in enumerate(records):
        sample_id = str(record.get("sample_id", record.get("case_id", f"row-{index + 1}")))
        evidence_index = (
            _evidence_index(record, section_bodies, errors, sample_id)
            if "sources" in record
            else {}
        )
        review = _mapping(record.get("review"))
        if review is None:
            continue
        point_fields = ("expected_answer_points", "known_points")
        for field_name in point_fields:
            points = _list(review.get(field_name))
            if points is None:
                continue
            for point_index, raw_point in enumerate(points):
                point = _mapping(raw_point)
                if point is None:
                    errors.append(f"{file_name}:{sample_id}: {field_name}[{point_index}] must be an object")
                    continue
                location = f"{file_name}:{sample_id}:{field_name}[{point_index}]"
                if not isinstance(point.get("text"), str) or not str(point.get("text")).strip():
                    errors.append(f"{location}: text must be non-empty")
                _validate_refs(point.get("evidence_refs"), evidence_index, errors, location, required=True)
    return errors


def _validate_unknown_parts(
    records: Sequence[Mapping[str, object]],
    section_bodies: Mapping[str, str],
    file_name: str,
) -> list[str]:
    errors: list[str] = []
    for index, record in enumerate(records):
        sample_id = str(record.get("sample_id", record.get("case_id", f"row-{index + 1}")))
        evidence_index = _evidence_index(record, section_bodies, errors, sample_id)
        review = _mapping(record.get("review"))
        if review is None:
            continue
        unknown_parts = _list(review.get("unknown_parts"))
        if unknown_parts is None:
            errors.append(f"{file_name}:{sample_id}: unknown_parts must be a list")
            continue
        for part_index, raw_part in enumerate(unknown_parts):
            location = f"{file_name}:{sample_id}:unknown_parts[{part_index}]"
            part = _mapping(raw_part)
            if part is None:
                errors.append(f"{location}: must be an object")
                continue
            if not isinstance(part.get("text"), str) or not str(part.get("text")).strip():
                errors.append(f"{location}: text must be non-empty")
            basis = part.get("basis")
            if basis not in _UNKNOWN_BASES:
                errors.append(f"{location}: unsupported basis {basis!r}")
            refs = _validate_refs(
                part.get("evidence_refs"),
                evidence_index,
                errors,
                location,
                required=basis == "explicit_source",
            )
            if basis == "not_defined_in_canonical_sources" and refs:
                errors.append(f"{location}: undefined rules must not invent evidence references")
    return errors


def _validate_mining_enrichment(
    records: Sequence[Mapping[str, object]], section_bodies: Mapping[str, str]
) -> list[str]:
    errors: list[str] = []
    for index, record in enumerate(records):
        sample_id = str(record.get("sample_id", f"row-{index + 1}"))
        review = _mapping(record.get("review"))
        if review is None:
            continue
        evidence_index = (
            _evidence_index(record, section_bodies, errors, sample_id)
            if "sources" in record
            else {}
        )
        enrichment = _list(review.get("canonical_enrichment"))
        refs_value = review.get("canonical_enrichment_evidence_refs", [])
        refs = _list(refs_value)
        if refs is None:
            errors.append(f"mining_reviewed:{sample_id}: enrichment evidence refs must be a list")
            continue
        if enrichment is not None and enrichment and not refs:
            errors.append(f"mining_reviewed:{sample_id}: canonical enrichment has no evidence refs")
        if (enrichment is None or not enrichment) and refs:
            errors.append(f"mining_reviewed:{sample_id}: evidence refs exist without canonical enrichment")
        for ref in refs:
            if not isinstance(ref, str) or ref not in evidence_index:
                errors.append(f"mining_reviewed:{sample_id}: unknown enrichment evidence ref {ref!r}")
    return errors


def _validate_manifest_counts(
    manifest: Mapping[str, object], observed_counts: Mapping[str, int]
) -> list[str]:
    counts = _mapping(manifest.get("counts"))
    if counts is None:
        return ["manifest: counts must be an object"]
    errors: list[str] = []
    for name, observed in observed_counts.items():
        declared = counts.get(name)
        if not isinstance(declared, int) or isinstance(declared, bool) or declared != observed:
            errors.append(
                f"manifest: counts.{name} is {declared!r}, expected {observed} from resources"
            )
    return errors


def _validate_sample_review_summary(
    summary: Mapping[str, object],
    rag_records: Sequence[Mapping[str, object]],
    extract_records: Sequence[Mapping[str, object]],
    rewrite_records: Sequence[Mapping[str, object]],
    retrieval_records: Sequence[Mapping[str, object]],
    flywheel_records: Sequence[Mapping[str, object]],
    mining_records: Sequence[Mapping[str, object]],
    classification_records: Sequence[Mapping[str, object]],
    boundary_records: Sequence[Mapping[str, object]],
) -> list[str]:
    errors: list[str] = []
    if summary.get("source_rag_count") != len(rag_records):
        errors.append("sample_review_summary: source_rag_count differs from reviewed RAG rows")

    actual_buckets = Counter(
        str(record.get("bucket")) for record in rag_records
    )
    actual_dispositions = Counter(
        str(review.get("disposition"))
        for record in rag_records
        if (review := _mapping(record.get("review"))) is not None
    )
    for field_name, actual in (
        ("rag_buckets", actual_buckets),
        ("rag_dispositions", actual_dispositions),
    ):
        declared = _mapping(summary.get(field_name))
        if declared is None or dict(declared) != dict(actual):
            errors.append(f"sample_review_summary: {field_name} differs from reviewed rows")

    auxiliary = _mapping(summary.get("auxiliary_counts"))
    if auxiliary is None:
        return errors + ["sample_review_summary: auxiliary_counts must be an object"]
    actual_counts = {
        "extract": len(extract_records),
        "rewrite": len(rewrite_records),
        "retrieval": len(retrieval_records),
        "flywheel": len(flywheel_records),
        "mining": len(mining_records),
        "classification": len(classification_records),
        "boundary_cases": len(boundary_records),
        "golden": sum(row.get("sample_set") == "golden" for row in classification_records),
        "supplement": sum(
            row.get("sample_set") == "supplement" for row in classification_records
        ),
    }
    for field_name, actual in actual_counts.items():
        declared = auxiliary.get(field_name)
        if not isinstance(declared, int) or isinstance(declared, bool) or declared != actual:
            errors.append(f"sample_review_summary: auxiliary_counts.{field_name} differs from rows")

    actual_classification_dispositions = Counter(
        str(review.get("disposition"))
        for record in classification_records
        if (review := _mapping(record.get("review"))) is not None
    )
    declared_classification_dispositions = _mapping(
        auxiliary.get("classification_dispositions")
    )
    if (
        declared_classification_dispositions is None
        or dict(declared_classification_dispositions) != dict(actual_classification_dispositions)
    ):
        errors.append(
            "sample_review_summary: classification_dispositions differs from reviewed rows"
        )
    return errors


def validate_support_resource_package(package_root: Path | None = None) -> list[str]:
    if package_root is None:
        package = files("commerce_support.resources.customer_support").joinpath("v1")
        with as_file(package) as resource_path:
            return validate_support_resource_package(Path(resource_path))

    root = Path(package_root)
    errors: list[str] = []
    manifest_value = _read_json(root / "manifest.json", errors)
    manifest = _mapping(manifest_value)
    if manifest is None:
        errors.append("manifest: expected an object")
        return errors
    errors.extend(validate_manifest_hashes(root, manifest))

    catalog_value = _read_json(root / "sections.json", errors)
    catalog = _mapping(catalog_value)
    if catalog is None:
        errors.append("sections: expected an object")
        return errors
    section_bodies, section_errors = _section_bodies(root, catalog)
    errors.extend(section_errors)
    section_counts, summary_errors = _validate_section_summaries(catalog, section_bodies)
    errors.extend(summary_errors)

    rag_records = _read_jsonl(root / "samples" / "rag_reviewed.jsonl", errors)
    errors.extend(validate_rag_records(rag_records, section_bodies))

    source_index_value = _read_json(root / "samples" / "source_index.json", errors)
    source_index_rows = _list(source_index_value) or []
    typed_source_rows = [row for row in source_index_rows if isinstance(row, Mapping)]
    if len(typed_source_rows) != len(source_index_rows):
        errors.append("source_index: every record must be an object")
    errors.extend(_validate_rag_source_preservation(root, typed_source_rows, rag_records))

    extract_value = _read_json(root / "samples" / "extract_reviewed.json", errors)
    extract_examples = _list(extract_value)
    if extract_examples is None:
        errors.append("extract_reviewed: expected a list")
        typed_extract_examples: list[Mapping[str, object]] = []
    else:
        typed_examples = [item for item in extract_examples if isinstance(item, Mapping)]
        if len(typed_examples) != len(extract_examples):
            errors.append("extract_reviewed: every example must be an object")
        errors.extend(validate_extract_examples(typed_examples))
        typed_extract_examples = typed_examples

    json_records: dict[str, list[Mapping[str, object]]] = {}
    for file_name in ("retrieval_reviewed.json", "flywheel_reviewed.json", "boundary_cases.json"):
        data = _read_json(root / "samples" / file_name, errors)
        records = _list(data)
        if records is None:
            errors.append(f"{file_name}: expected a list")
            json_records[file_name] = []
            continue
        typed_records = [item for item in records if isinstance(item, Mapping)]
        if len(typed_records) != len(records):
            errors.append(f"{file_name}: every record must be an object")
        errors.extend(_validate_sample_review(typed_records, file_name))
        errors.extend(_validate_auxiliary_sources(typed_records, section_bodies, file_name))
        if file_name in {"flywheel_reviewed.json", "boundary_cases.json"}:
            errors.extend(_validate_unknown_parts(typed_records, section_bodies, file_name))
        json_records[file_name] = typed_records

    jsonl_records: dict[str, list[Mapping[str, object]]] = {}
    for file_name in ("rewrite_reviewed.jsonl", "classification_reviewed.jsonl"):
        records = _read_jsonl(root / "samples" / file_name, errors)
        errors.extend(_validate_sample_review(records, file_name))
        jsonl_records[file_name] = records

    taxonomy_value = _read_json(root / "taxonomy.json", errors)
    taxonomy = _mapping(taxonomy_value)
    classification_records = jsonl_records["classification_reviewed.jsonl"]
    if taxonomy is None:
        errors.append("taxonomy: expected an object")
    else:
        errors.extend(validate_taxonomy_records(taxonomy, classification_records))
    errors.extend(
        _validate_classification_source_preservation(
            root, typed_source_rows, classification_records
        )
    )

    mining_value = _read_json(root / "samples" / "mining_reviewed.json", errors)
    mining_records = _list(mining_value)
    if mining_records is None:
        errors.append("mining_reviewed: expected a list")
        typed_mining_records: list[Mapping[str, object]] = []
    else:
        typed_records = [item for item in mining_records if isinstance(item, Mapping)]
        if len(typed_records) != len(mining_records):
            errors.append("mining_reviewed: every record must be an object")
        errors.extend(_validate_sample_review(typed_records, "mining_reviewed.json"))
        errors.extend(_validate_auxiliary_sources(typed_records, section_bodies, "mining_reviewed.json"))
        errors.extend(_validate_mining_enrichment(typed_records, section_bodies))
        typed_mining_records = typed_records

    review_summary_value = _read_json(root / "sample_review_summary.json", errors)
    review_summary = _mapping(review_summary_value)
    if review_summary is None:
        errors.append("sample_review_summary: expected an object")
    else:
        errors.extend(
            _validate_sample_review_summary(
                review_summary,
                rag_records,
                typed_extract_examples,
                jsonl_records["rewrite_reviewed.jsonl"],
                json_records["retrieval_reviewed.json"],
                json_records["flywheel_reviewed.json"],
                typed_mining_records,
                classification_records,
                json_records["boundary_cases.json"],
            )
        )

    errors.extend(_validate_source_index(root, manifest))
    expected_counts = {
        "samples/rag_reviewed.jsonl": (rag_records, 300),
        "samples/extract_reviewed.json": (typed_extract_examples, 5),
        "samples/rewrite_reviewed.jsonl": (
            jsonl_records["rewrite_reviewed.jsonl"],
            5,
        ),
        "samples/retrieval_reviewed.json": (
            json_records["retrieval_reviewed.json"],
            5,
        ),
        "samples/flywheel_reviewed.json": (
            json_records["flywheel_reviewed.json"],
            12,
        ),
        "samples/mining_reviewed.json": (typed_mining_records, 2),
        "samples/classification_reviewed.jsonl": (classification_records, 64),
        "samples/boundary_cases.json": (
            json_records["boundary_cases.json"],
            17,
        ),
    }
    for file_name, (records, expected_count) in expected_counts.items():
        if len(records) != expected_count:
            errors.append(
                f"{file_name}: expected {expected_count} reviewed records, found {len(records)}"
            )
    if len(classification_records) == 64:
        golden_count = sum(row.get("sample_set") == "golden" for row in classification_records)
        supplement_count = sum(
            row.get("sample_set") == "supplement" for row in classification_records
        )
        if (golden_count, supplement_count) != (30, 34):
            errors.append(
                "classification_reviewed.jsonl: expected 30 golden and 34 supplement records"
            )

    faq_sections = _list(catalog.get("faq_sections")) or []
    aliases_data = _read_json(root / "faq_aliases.json", errors)
    aliases_object = _mapping(aliases_data)
    aliases = _list(aliases_object.get("aliases")) if aliases_object else None
    taxonomy_classes = _list(taxonomy.get("classes")) if taxonomy else None
    auxiliary_source_groups = {
        row.get("source_path")
        for row in typed_source_rows
        if row.get("reviewed_path") != "taxonomy.json"
        and isinstance(row.get("source_path"), str)
    }
    from commerce_support.resources.customer_support.loader import _load_catalog

    faq_row_count = 0
    try:
        faq_row_count = len(_load_catalog(root).rows)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"FAQ resource catalog is invalid: {exc}")
    observed_counts = {
        "knowledge_documents": len(list((root / "knowledge").glob("*.md"))),
        **section_counts,
        "rag_samples": len(rag_records),
        "classification_samples": len(classification_records),
        "taxonomy_classes": len(taxonomy_classes or []),
        "auxiliary_source_groups": len(auxiliary_source_groups),
        "sample_and_taxonomy_sources": len(typed_source_rows),
        "boundary_cases": len(json_records["boundary_cases.json"]),
        "managed_faq_rows": faq_row_count,
    }
    if faq_row_count != len(faq_sections) + len(aliases or []):
        errors.append("FAQ resource catalog row count differs from faq_sections plus aliases")
    errors.extend(_validate_manifest_counts(manifest, observed_counts))
    return errors
