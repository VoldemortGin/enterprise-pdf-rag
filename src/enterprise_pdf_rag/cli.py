"""Executable evidence slice and conservative PDF source diagnostics."""

import argparse
import sys
from pathlib import Path

from enterprise_pdf_rag.adapters.aia_ingestion import AIA_OUTPUT, ingest_aia
from enterprise_pdf_rag.adapters.http.document_schemas import DocumentSnapshotResponse
from enterprise_pdf_rag.adapters.http.schemas import (
    DemoResponse,
    ExtractionResponse,
    HitSchema,
)
from enterprise_pdf_rag.adapters.pdfspine_document import PdfspineDocumentAdapter
from enterprise_pdf_rag.adapters.pdfspine_figure import PdfspineFigureParser
from enterprise_pdf_rag.adapters.providers import OpenAICompatibleSmoke, load_llm_config
from enterprise_pdf_rag.adapters.review import write_review
from enterprise_pdf_rag.adapters.runtime import create_runtime
from enterprise_pdf_rag.figures.models import ExecutionMode


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="enterprise-pdf-rag")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "ingest-aia", help="Persist and review only the selected AIA PDF; no models"
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
