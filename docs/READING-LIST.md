# Reading list

Links checked on 2026-11-12. The NVIDIA stack changes every month: check the version of each page before you write code against it. "Take from it" says what we need.

## Agent and sandbox

| Document | Take from it |
|---|---|
| [NemoClaw overview](https://docs.nvidia.com/nemoclaw/latest/about/overview) | What NemoClaw adds on top of OpenShell: onboarding, blueprint, routed inference, egress policy. |
| [NemoClaw with Hermes](https://docs.nvidia.com/nemoclaw/latest/user-guide/hermes/about/overview) | How to run the Hermes agent in a sandbox. Not used: decision D36 replaced Hermes with our own LangGraph agent. Kept for reference. |
| [OpenShell: sandbox policies](https://docs.nvidia.com/openshell/latest/sandboxes/policies) | The policy YAML: static parts (filesystem, process) and the network part that can change on a running sandbox. |
| [OpenShell: security best practices](https://docs.nvidia.com/openshell/security/best-practices) | Every control, its default, and the risk if we relax it. |
| [OpenShell repository](https://github.com/NVIDIA/OpenShell) | CLI commands, examples, custom containers, the agent skills in `.agents/skills/`. |
| `../nemoforge-plateforme/docs/DECISIONS.md` and `LESSONS.md` | What already works for us: OpenShell 0.1.5, Hermes, provider profiles, Guardrails proxy, Colima on the Mac. Read these before the NVIDIA pages. |

## Document extraction (NeMo Retriever)

| Document | Take from it |
|---|---|
| [Multimodal extraction](https://docs.nvidia.com/nemo/retriever/26.8.1/extraction/multimodal-extraction/) | Extraction methods for PDFs and scans, table output, OCR options, image size limits of the hosted endpoints, what drives the cost per page. |
| [Nemotron Parse NIM overview](https://docs.nvidia.com/nim/ingestion/parse/latest/overview.html) | Parse is a stand-alone NIM with a document API since version 2.4. It no longer uses the chat-completions API. |
| [Model catalog, NeMo label](https://build.nvidia.com/models?label=NeMo) | Current names and versions: `nemotron-ocr-v2`, page elements, `nemotron-table-structure-v1`, Parse. |

Point to check: the extraction docs say the Helm deployment serves Nemotron OCR v1 and that OCR v2 is the default for local Hugging Face inference. Confirm how we get OCR v2 as a service.

## Vision-language models

| Document | Take from it |
|---|---|
| [Nemotron Nano 12B v2 VL: query the API](https://docs.nvidia.com/nim/vision-language-models/1.6.0/examples/nemotron-nano-12b-v2-vl/api.html) | How to start the NIM and send a page image through the OpenAI-compatible chat API. |
| [VLM NIM documentation](https://docs.nvidia.com/nim/vision-language-models/1.7.0) | Sections "Structured Generation" (JSON that follows our schema), "Call Functions (Tools)", "Fine-Tune a Model", "Benchmarking". |
| [Nemotron 3 Nano Omni: announcement](https://huggingface.co/blog/nvidia/nemotron-3-nano-omni-multimodal-intelligence) and [report](https://arxiv.org/html/2604.24954) | Released April 2026. Higher document scores than Nano V2 VL in NVIDIA's own tables. Input for decision O4. |
| [Nemotron 3 Nano Omni on build.nvidia.com](https://build.nvidia.com/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning) | Hosted endpoint for development with mock data. |
| [Nemotron Omni on vLLM](https://vllm.ai/blog/nemotron-omni) | How to serve the model ourselves with vLLM. |
| [Nemotron 3.5 Super VL: blog](https://developer.nvidia.com/blog/nvidia-nemotron-3-5-super-vl-powers-specialized-agents-with-multimodal-reasoning) | The large model of the benchmark: 120B parameters, 12B active per token. |

## Fine-tuning

| Document | Take from it |
|---|---|
| [NeMo AutoModel: Nemotron 3.5 Super VL](https://docs.nvidia.com/nemo/automodel/model-coverage/omni/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16) | LoRA recipe on CORD-v2 (receipt field extraction), on 8 GPUs. The page links the same recipe for Nemotron Omni: this is the closest example to our LoRA on claim documents. |

## Still to find

- Brev: current documentation for instances and launchables. No link checked yet.
- NeMo Guardrails: reuse the NemoForge proxy setup (`../nemoforge-plateforme/infra/guardrails`).
- A public data set of receipts or invoices to complete the mock set (CORD-v2 is one).

## Domain reading for the intern

- How a claim is handled, from first notice to payment, and the words used (policy, coverage, limit, deductible, first notice of loss, straight-through processing).
- Common patterns of claims fraud: early claims, inflated invoices, edited documents, staged damage.
- Data protection for health data in insurance (Swiss FADP, GDPR): why the data must stay in the company.
