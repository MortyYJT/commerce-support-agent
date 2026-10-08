# Customer support resources v1

This package is the versioned source for customer-support knowledge and resource-level evaluation. Its six normalized knowledge documents preserve the original Chinese business material while resolving the approved shipping and return policy and recording each revision in `manifest.json`.

The source snapshot is `50f20d148589c9fc0659c1f7d2bb12054fee6fa1`. Original source files are archived byte-for-byte under `legacy/original/`; their paths and SHA-256 hashes are in the manifest. This archive is for source auditing only and is excluded from current retrieval, FAQ generation, and training or evaluation inputs. The old classification tooling contained source headers crediting the public account `小林coding`; that attribution is retained here. The migrated examples are not represented as original authored work.

The Markdown contains 52 H2/H3 headings, including two empty container headings in the returns document. The catalog has 50 nonempty business sections for FAQ generation and preserves the product-specs H1 introduction as the 51st nonempty knowledge section; that introduction is not emitted as a customer-facing FAQ. FAQ answers are derived from the canonical Markdown sections, and `faq_aliases.json` contains only question wording mapped to a source section. Seeded questions and answers must remain within 256 and 1000 characters, and serialized public tool results remain subject to the existing 4096-byte contract.

The approved policy is: seven-day eligible no-reason returns from receipt, CNY 99 free-shipping threshold, CNY 10 standard base shipping below the threshold, and a separate CNY 12 remote-area surcharge that is not waived by free shipping. The source's silver-card 9折 and gold-card 95折 rules remain as written. A 10 CNY shipping redemption uses 1000 points. Unknown rules remain unknown.

The classifier examples are resource-level samples only. This package does not include a complete training/validation/test corpus or trained model, and no classifier quality result is claimed. The historical evaluation reports are not results from this migration.

## Resource layout and evaluation boundaries

- `knowledge/` contains the six canonical documents; `sections.json` maps each nonempty section to its content hash. `faq_seed_layout.json` reserves managed IDs and recognized prior row states.
- `samples/rag_reviewed.jsonl` preserves all 300 original question IDs and questions. Each answer point references literal evidence within a canonical section. `unknown_parts` distinguishes missing user inputs, undefined policies, and explicit source limitations. Partial cases require both a supported answer and acknowledgment of the remaining unknowns. The dispositions in `sample_review_summary.json` are annotation counts, not model scores.
- `samples/boundary_cases.json` adds 17 policy checks. The five extraction examples map to the current three-field `AfterSales` schema; legacy complaint intent maps to `其他` while the requested solution retains the complaint. Schema validation does not test LLM extraction.
- `samples/flywheel_reviewed.json` separates semantic candidate matching from whether the knowledge can answer the question. `samples/mining_reviewed.json` separates facts extracted from a conversation from canonical enrichment. Rewrite and retrieval samples describe future resource-level evaluation, not implemented synonym expansion or RAG behavior.
- `taxonomy.json` preserves 17 historical topic definitions. Of the 64 classification examples, G20, G22, and S34 are quarantined pending topic-boundary decisions; 61 are eligible annotation examples. A topic label does not establish fault, eligibility, or a service outcome.
- `samples/source_index.json` records nine source artifacts, including the taxonomy. `legacy/original/` preserves source bytes and original labels exclusively for provenance and comparison. Legacy examples, source headers, and policy conflicts must not be selected as current runtime inputs.

Run `python scripts/validate_customer_support_resources.py` from the repository, or call `commerce_support.resources.validation.validate_support_resource_package()` against the installed wheel. Structural validation verifies hashes, source references and schema invariants; semantic label review and live-model evaluations remain separate checks.
