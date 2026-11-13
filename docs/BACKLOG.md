# Backlog

Ordered. Each line becomes a GitHub issue (`/prd-to-issues`). **Base** = we build it first, with agents. **Intern** = kept for the intern: clear scope, a measurable result, and room to make it better.

The boundary is the tool `extract_document` (decision D24). The base ships a fixture reader behind it. The intern builds the real reader.

## M0: Setup (base)

1. Create the repository, labels, branch protection and CI (tests, types, lint).
2. Skeleton: FastAPI backend with uv, React + Vite + TypeScript frontend, Docker Compose, `.env.example`.
3. Pydantic schemas of `docs/ARCHITECTURE.md` section 4, with the provenance rule (non-negotiable 3).

## M1: First slice — travel claim in `local` mode (base)

4. Mock policy database with the customers of `docs/MOCK-DATA.md`.
5. Mock document generator: the three travel PDFs of claim file 1, each with its `expected.json`.
6. Tool gateway with the event log (non-negotiable 5).
7. Fixture reader behind the contract of `extract_document` and `classify_document`: it returns the content of `expected.json`.
8. Travel workflow: required documents, schema, rules.
9. Scripted claim agent: policy → story → documents → result.
10. Score and routing, with the no-rejection rule (non-negotiable 1).
11. Customer panel: chat and upload. Customer view, with the same message for `handler_review` and `investigation` (non-negotiable 2, decision D18).
12. Pipeline panel: live events, extracted fields with sources, signals.
13. End-to-end test: claim file 1 gives `straight_through`.

## M2: Fraud and the two other workflows in `local` mode (base)

14. Property workflow and claim file 2 (`handler_review`).
15. Health workflow and claim file 3.
16. Tampering checks T001–T003 on real file bytes.
17. Consistency checks C001–C003.
18. Fraud report (non-negotiable 4) and its view in the pipeline panel. Claim file 3 gives `investigation`.
19. README for the intern: what is his, how to run the base, the contract of `extract_document`, his six tasks.

## M3: Agent in the sandbox, on the VM (base)

20. LLM agent with tool calling, in place of the scripted agent (`nim` mode). Spike: Omni as the agent model (decision D17).
21. Spike: our own LangGraph agent in OpenShell, one MCP tool call (decision D36, which replaces the Hermes plan of D27). Done: issue #13.
22. `sandbox` mode: the graph in OpenShell, the tool gateway as an MCP server, a policy that allows only the gateway and the model provider (D44 to D49). Done: issue #14.
23. Sandbox policy, denials and a confinement test shown in the pipeline panel, with the mode in the sidebar. Tests for non-negotiable 6 (D50 to D54). Done: issue #15.
24. Deploy on a Brev VM (no GPU needed with hosted endpoints). Write `docs/DEPLOY.md`. The intern gets access. Done: issue #16 (D58). Left: the Brev invite of the intern, by Paul.

## M4: Document reading pipeline (intern)

Each task ends with a number.

25. Pydantic schema per document type (booking confirmation, invoice, medical certificate, and the others of the kit). Result: every `expected.json` of the kit validates.
26. Omni reader: page image → JSON that follows the schema, with the source page of each field. Behind the contract of `extract_document`. Also measures the rate limit of the free key (O15).
27. Evaluation harness: accuracy per field, pass rate and time per claim, against `expected.json`. Metrics page.
28. Tuning: prompt, image resolution, one call per page or per document, reasoning on or off. Result: a table of accuracy and time per variant.
29. Document classification with Omni, in place of the fixture. Result: accuracy on the kit.
30. Hard cases: scans and phone photos. Test Nemotron Parse where Omni fails. Result: the gain per document kind. These numbers support the request for GPU credits.
30b. Rails on what the agent reads (issue #62, D60): NeMo Guardrails on the customer message, on the text of the documents and on the agent answer. A block never rejects a claim: it goes to a person. Result: an injected document is blocked, with the count of false blocks on the kit and the time added per turn.

## M5: Make it better (intern or later)

31. Mock document kit: grow from the three claim files to 20 to 30 documents, with scans, phone photos and seeded fraud cases (decision D14). Done in #23 (D57): 24 documents, 9 hard versions, `data/mock/index.json`. Left for the intern: real photographs, more scans per type, more seeded fraud cases.
32. Specialised chain: OCR v2, page elements, table structure. Measure what each one adds.
33. Damage photo description and check C004.
34. Fraud report written by an LLM from the signals only.
35. Consistency prompts: test variants, measure false alarms on the clean files.
36. Handler review: confirm or correct each field next to its source page.
37. Tampering detection, second level: fonts, pixels, edited photos, EXIF data.
38. Duplicate detection across claims (invoice numbers, photos).

## M6: GPU bursts (with Paul, needs GPU credits)

39. Self-hosted models on Brev: Omni and the chain. GPU usage per claim, and throughput on a batch.
40. Benchmark Nemotron 3.5 Super VL against Omni on the hard documents (4× B200).
41. LoRA fine-tune of the small VLM on our claim documents (8× H100). Compare with the large model.

## M7: Demo

42. Demo mode for visitors: account choice, kit of documents, "no real document" notice, reset that deletes the uploads.
43. Dry run on the demo instance. Fix list.

## Notes on the split

- The intern starts with a working flow in `local` mode. He does not start from an empty repository.
- Tasks 25, 26 and 27 come first: the schemas, one reader and the measure make the other tasks testable.
- M3 and M4 run at the same time. They touch different folders: `agent/` and `infra/` for M3, `docintel/` for M4.
