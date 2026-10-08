from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

RESOURCE_PACKAGE = "commerce_support.resources.customer_support"


@dataclass(frozen=True)
class FAQSeedCatalog:
    rows: tuple[dict[str, object], ...]
    known_prior_rows: Mapping[int, tuple[dict[str, object], ...]]
    resource_id_start: int


def _resource_root() -> Any:
    return files(RESOURCE_PACKAGE).joinpath("v1")


def _section_bodies(root: Any, catalog: dict[str, object]) -> dict[str, str]:
    rows = catalog.get("heading_inventory")
    if not isinstance(rows, list):
        raise TypeError("sections.json heading_inventory must be a list")

    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("sections.json heading rows must be objects")
        document_id = row.get("document_id")
        if isinstance(document_id, str):
            grouped.setdefault(document_id, []).append(row)

    bodies: dict[str, str] = {}
    for document_id, document_rows in grouped.items():
        lines = root.joinpath("knowledge", f"{document_id}.md").read_text(
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

        for row in document_rows:
            level = row.get("heading_level")
            title = row.get("title")
            path = row.get("heading_path")
            section_id = row.get("id")
            if not isinstance(level, int) or not isinstance(title, str) or not isinstance(path, list):
                raise TypeError(f"invalid heading metadata for section {section_id!r}")
            if not isinstance(section_id, str):
                raise TypeError("section id must be a string")
            matches = [
                index
                for index, (_, found_level, found_title, found_path) in enumerate(headings)
                if found_level == level and found_title == title and found_path == path
            ]
            if len(matches) != 1:
                raise ValueError(f"section {section_id} maps to {len(matches)} Markdown headings")
            heading_index = matches[0]
            start = headings[heading_index][0]
            end = headings[heading_index + 1][0] if heading_index + 1 < len(headings) else len(lines)
            bodies[section_id] = "\n".join(lines[start + 1 : end]).strip()
    return bodies


def _category(document_id: str, title: str, *, legacy_id: int | None = None) -> str:
    if legacy_id == 1 or document_id == "returns-policy":
        return "returns"
    if document_id == "billing-shipping":
        return "shipping" if "运费" in title or "邮费" in title else "billing"
    if document_id == "after-sales-manual":
        return "after-sales"
    if document_id == "member-benefits":
        return "membership"
    return "product"


def _load_catalog(root: Any | None = None) -> FAQSeedCatalog:
    resource_root = root or _resource_root()
    catalog = json.loads(resource_root.joinpath("sections.json").read_text(encoding="utf-8"))
    layout = json.loads(
        resource_root.joinpath("faq_seed_layout.json").read_text(encoding="utf-8")
    )
    aliases_data = json.loads(
        resource_root.joinpath("faq_aliases.json").read_text(encoding="utf-8")
    )
    if not isinstance(catalog, dict) or not isinstance(layout, dict):
        raise TypeError("resource catalog and FAQ layout must be JSON objects")
    bodies = _section_bodies(resource_root, catalog)
    heading_rows = catalog.get("heading_inventory")
    if not isinstance(heading_rows, list):
        raise TypeError("sections.json heading_inventory must be a list")

    managed_legacy_raw = layout.get("managed_legacy_ids", {})
    if not isinstance(managed_legacy_raw, dict):
        raise TypeError("faq_seed_layout.managed_legacy_ids must be an object")
    legacy_ids: dict[str, int] = {}
    for raw_id, target in managed_legacy_raw.items():
        if not str(raw_id).isdigit() or not isinstance(target, str):
            raise TypeError("managed legacy FAQ IDs must map integer strings to resource keys")
        if not target.startswith("faq_aliases.json:"):
            legacy_ids[target] = int(raw_id)

    alias_rows = aliases_data.get("aliases") if isinstance(aliases_data, dict) else None
    if not isinstance(alias_rows, list):
        raise TypeError("faq_aliases.json aliases must be a list")
    aliases_by_id: dict[int, dict[str, object]] = {}
    for alias in alias_rows:
        if not isinstance(alias, dict):
            raise TypeError("FAQ alias rows must be objects")
        seed_id = alias.get("seed_id")
        if not isinstance(seed_id, int) or not isinstance(alias.get("question"), str):
            raise TypeError("FAQ alias needs an integer seed_id and question")
        aliases_by_id[seed_id] = alias

    resource_id_start = layout.get("resource_id_start")
    if not isinstance(resource_id_start, int) or resource_id_start < 100_000:
        raise ValueError("resource_id_start must reserve a high deterministic ID range")

    rows: list[dict[str, object]] = []
    section_ids_seen: set[str] = set()
    next_resource_id = resource_id_start
    for row in heading_rows:
        if not isinstance(row, dict):
            continue
        if row.get("heading_level") not in (2, 3) or not row.get("has_body"):
            continue
        section_id = row.get("id")
        document_id = row.get("document_id")
        title = row.get("title")
        if not isinstance(section_id, str) or not isinstance(document_id, str) or not isinstance(title, str):
            raise TypeError("FAQ section catalog has invalid identifiers")
        answer = bodies.get(section_id, "")
        if not answer:
            raise ValueError(f"FAQ section {section_id} is marked non-empty but has no body")
        if section_id in section_ids_seen:
            raise ValueError(f"duplicate FAQ section id {section_id}")
        section_ids_seen.add(section_id)
        legacy_id = legacy_ids.get(section_id)
        question = title if title.endswith(("？", "?")) else f"{title}？"
        row_id = legacy_id if legacy_id is not None else next_resource_id
        if legacy_id is None:
            next_resource_id += 1
        category = _category(document_id, title, legacy_id=legacy_id)
        rows.append({"id": row_id, "question": question, "answer": answer, "category": category})

    for seed_id, alias in aliases_by_id.items():
        section_id = alias.get("section_id")
        if not isinstance(section_id, str) or section_id not in bodies:
            raise ValueError(f"FAQ alias points to unknown section {section_id!r}")
        section = next(
            (row for row in heading_rows if isinstance(row, dict) and row.get("id") == section_id),
            None,
        )
        if section is None:
            raise ValueError(f"FAQ alias section {section_id} is absent from catalog")
        document_id = section.get("document_id")
        title = section.get("title")
        if not isinstance(document_id, str) or not isinstance(title, str):
            raise TypeError(f"FAQ alias section {section_id} has invalid metadata")
        rows.append(
            {
                "id": seed_id,
                "question": alias["question"],
                "answer": bodies[section_id],
                "category": _category(document_id, title),
            }
        )

    ids = [row["id"] for row in rows]
    questions = [row["question"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("FAQ resource contains duplicate IDs")
    if len(questions) != len(set(questions)):
        raise ValueError("FAQ resource contains duplicate questions")
    for row in rows:
        question = row["question"]
        answer = row["answer"]
        category = row["category"]
        if not isinstance(question, str) or not 1 <= len(question) <= 256:
            raise ValueError(f"FAQ question length outside 1..256: {question!r}")
        if not isinstance(answer, str) or not 1 <= len(answer) <= 1000:
            raise ValueError(f"FAQ answer length outside 1..1000: {question!r}")
        if not isinstance(category, str) or not 1 <= len(category) <= 64:
            raise ValueError(f"FAQ category length outside 1..64: {question!r}")

    prior: dict[int, list[dict[str, object]]] = {}
    for field_name in ("known_legacy_rows", "known_resource_rows"):
        values = layout.get(field_name, [])
        if not isinstance(values, list):
            raise TypeError(f"faq_seed_layout.{field_name} must be a list")
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                raise TypeError(f"faq_seed_layout.{field_name} entries must include integer IDs")
            prior.setdefault(item["id"], []).append(dict(item))
    return FAQSeedCatalog(
        rows=tuple(rows),
        known_prior_rows={key: tuple(value) for key, value in prior.items()},
        resource_id_start=resource_id_start,
    )


def load_faq_seed_rows() -> list[dict[str, object]]:
    return [dict(row) for row in _load_catalog().rows]


def load_faq_seed_catalog() -> FAQSeedCatalog:
    return _load_catalog()
