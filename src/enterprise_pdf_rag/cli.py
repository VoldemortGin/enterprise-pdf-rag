"""Executable evidence slice and conservative PDF source diagnostics."""

import argparse
import sys
from pathlib import Path

import uvicorn
from pydantic import BaseModel, ConfigDict, Field

from enterprise_pdf_rag.adapters.aia_ingestion import AIA_OUTPUT, ingest_aia
from enterprise_pdf_rag.adapters.http.app import create_configured_app
from enterprise_pdf_rag.adapters.http.document_schemas import DocumentSnapshotResponse
from enterprise_pdf_rag.adapters.http.schemas import (
    DemoResponse,
    ExtractionResponse,
    HitSchema,
)
from enterprise_pdf_rag.adapters.pdfspine_document import PdfspineDocumentAdapter
from enterprise_pdf_rag.adapters.pdfspine_figure import PdfspineFigureParser
from enterprise_pdf_rag.adapters.processing_runtime import (
    index_aia_processing,
    process_aia_layout,
    process_aia_semantics,
)
from enterprise_pdf_rag.adapters.providers import OpenAICompatibleSmoke, load_llm_config
from enterprise_pdf_rag.adapters.review import write_review
from enterprise_pdf_rag.adapters.runtime import create_runtime
from enterprise_pdf_rag.figures.models import ExecutionMode


class _ServerOptions(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    host: str = Field(min_length=1)
    port: int = Field(ge=1, le=65535)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="enterprise-pdf-rag")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser(
        "serve", help="Serve the explicitly configured API; no ingestion or model calls"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8766)
    commands.add_parser(
        "ingest-aia", help="Persist and review only the selected AIA PDF; no models"
    )
    layout = commands.add_parser(
        "process-aia-layout",
        help="Explicitly call the configured model for selected physical pages 1-20; object semantics remain deferred",
    )
    layout.add_argument(
        "--page",
        type=int,
        action="append",
        required=True,
        help="Physical page 1-20; repeat for multiple pages",
    )
    layout.add_argument(
        "--max-live-calls",
        type=int,
        required=True,
        help="Maximum new layout calls; 0 permits cached responses only",
    )
    layout.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help="Socket I/O timeout in seconds, at most 180",
    )
    layout.add_argument(
        "--retry-failed",
        action="store_true",
        help="Explicitly allow one immutable retry record for a previously failed identical request",
    )
    semantics = commands.add_parser(
        "process-aia-semantics",
        help="Process saved first-20 layouts into actual typed IR and independent descriptions; no automatic embedding",
    )
    semantics.add_argument("--page", type=int, action="append", required=True)
    semantics.add_argument("--max-live-calls", type=int, required=True)
    semantics.add_argument("--timeout", type=float, default=180.0)
    semantics.add_argument("--retry-failed", action="store_true")
    semantics.add_argument(
        "--correct-description-request",
        action="append",
        default=[],
        help="Explicit original request fingerprint for one source-binding correction; at most two, no automatic retry",
    )
    semantics.add_argument(
        "--correct-chart-request",
        action="append",
        default=[],
        help="Explicit original chart fingerprint for one source-binding correction; no automatic retry",
    )
    semantics.add_argument(
        "--qualification-policy",
        choices=["none", "source-labels-only", "donut"],
        default="none",
        help="Explicit qualification policy; never an automatic fallback",
    )
    indexing = commands.add_parser(
        "index-aia-processing",
        help="Explicitly embed eligible descriptions on the local service, rerank and hydrate one fixed processing snapshot",
    )
    indexing.add_argument("--processing-id", required=True)
    indexing.add_argument("--query", required=True)
    indexing.add_argument("--limit", type=int, default=5)
    indexing.add_argument(
        "--rerank-configuration-id",
        default="unrecorded",
        help="Reference to the actual provider configuration evidence; not a quality approval",
    )
    commands.add_parser(
        "llm-smoke",
        help="Explicitly send one short live request using the three OPENAI environment settings",
    )
    demo = commands.add_parser(
        "demo", help="Execute the synthetic PDF-to-context slice"
    )
    demo.add_argument("--mode", choices=["offline-demo"], required=True)
    demo.add_argument("--query", default="Revenue 2025")
    demo.add_argument("--snapshot-id", default="demo-v1")
    demo.add_argument("--output", type=Path, required=True)
    extract = commands.add_parser(
        "extract", help="Export a pending SVG for source review"
    )
    extract.add_argument("--pdf", type=Path, required=True)
    extract.add_argument(
        "--page", type=int, required=True, help="Physical PDF page, 1-based"
    )
    extract.add_argument(
        "--bbox", type=float, nargs=4, required=True, metavar=("X0", "Y0", "X1", "Y1")
    )
    extract.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "serve":
            options = _ServerOptions(host=arguments.host, port=arguments.port)
            app = create_configured_app()
            uvicorn.run(app, host=options.host, port=options.port)
            return 0
        if arguments.command == "index-aia-processing":
            indexed = index_aia_processing(
                processing_id=arguments.processing_id,
                query=arguments.query,
                limit=arguments.limit,
                rerank_configuration_id=arguments.rerank_configuration_id,
            )
            sys.stdout.write(indexed.model_dump_json(indent=2) + "\n")
            return 0
        if arguments.command in ("process-aia-layout", "process-aia-semantics"):
            if arguments.command == "process-aia-layout":
                summary = process_aia_layout(
                    physical_pages=tuple(arguments.page),
                    max_live_calls=arguments.max_live_calls,
                    timeout=arguments.timeout,
                    retry_failed=arguments.retry_failed,
                )
            else:
                summary = process_aia_semantics(
                    physical_pages=tuple(arguments.page),
                    max_live_calls=arguments.max_live_calls,
                    timeout=arguments.timeout,
                    retry_failed=arguments.retry_failed,
                    qualification_policy=arguments.qualification_policy,
                    description_corrections=tuple(
                        arguments.correct_description_request
                    ),
                    chart_corrections=tuple(arguments.correct_chart_request),
                )
            sys.stdout.write(summary.model_dump_json(indent=2) + "\n")
            return 0
        if arguments.command == "ingest-aia":
            snapshot = ingest_aia(extractor=PdfspineDocumentAdapter())
            sys.stdout.write(
                DocumentSnapshotResponse(
                    manifest_id=snapshot.manifest_id, manifest=snapshot.manifest
                ).model_dump_json()
                + "\n"
            )
            sys.stdout.write(f"Review: {AIA_OUTPUT / 'review.html'}\n")
            return 0
        if arguments.command == "llm-smoke":
            smoke = OpenAICompatibleSmoke(load_llm_config()).run()
            sys.stdout.write(smoke.model_dump_json(indent=2) + "\n")
            return 0
        if arguments.command == "demo":
            runtime = create_runtime(mode=ExecutionMode(arguments.mode))
            result = runtime.run_demo(
                query=arguments.query, snapshot_id=arguments.snapshot_id
            )
            write_review(result.svg, arguments.output)
            (arguments.output / "source.pdf").write_bytes(result.pdf)
            response = DemoResponse(
                bundle=result.bundle,
                hits=tuple(HitSchema.from_domain(hit) for hit in result.hits),
                context=result.context,
            )
            serialized = response.model_dump_json(indent=2)
        else:
            x0, y0, x1, y1 = arguments.bbox
            artifact = PdfspineFigureParser().extract(
                arguments.pdf.read_bytes(),
                page_index=arguments.page - 1,
                bbox=(x0, y0, x1, y1),
            )
            write_review(artifact, arguments.output)
            serialized = ExtractionResponse(
                artifact_id=artifact.artifact_id, artifact=artifact
            ).model_dump_json(indent=2)
        (arguments.output / "result.json").write_text(
            serialized + "\n", encoding="utf-8"
        )
        sys.stdout.write(serialized + "\n")
        return 0
    except (ValueError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    raise SystemExit(main())
