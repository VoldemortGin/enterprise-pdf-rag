"""Rebuild chart qualification from pinned source assets before publication/use."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter

from enterprise_pdf_rag.adapters.aia_ingestion import read_text_sidecar
from enterprise_pdf_rag.adapters.document_store import LocalDocumentStore
from enterprise_pdf_rag.adapters.donut_qualification import DonutQualification
from enterprise_pdf_rag.adapters.figure_label_qualification import (
    FIGURE_LABEL_SCOPE,
    qualify_source_labels,
)
from enterprise_pdf_rag.adapters.figure_reasoning import (
    ContextualFigureModelView,
    FigureModelView,
    prepare_figure,
)
from enterprise_pdf_rag.documents.models import AssetRef
from enterprise_pdf_rag.figures.models import (
    ChartIR,
    FigureQualification,
    TextDescription,
    Verification,
)
from enterprise_pdf_rag.figures.validation import validate_pair
from enterprise_pdf_rag.processing.models import ObjectKind, PageInput, ProcessingScope
from enterprise_pdf_rag.processing.retrieval import RetrievalMember


class ChartPublicationReceipt(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    object_id: str
    source_manifest_id: str
    region_id: str
    ir: AssetRef
    description: AssetRef
    source_svg: AssetRef
    raw_chart: AssetRef
    raw_description: AssetRef
    view: AssetRef
    qualification: FigureQualification
    schema_version: Literal["source-chart-qualification-v1"] = (
        "source-chart-qualification-v1"
    )


def validate_chart_member(
    sources: LocalDocumentStore,
    assets: LocalDocumentStore,
    scope: ProcessingScope,
    member: RetrievalMember,
) -> tuple[ChartIR, TextDescription, FigureQualification]:
    """No model/network calls: reconstruct source, then repeat the scoped proof."""
    receipt = ChartPublicationReceipt.model_validate_json(
        assets.get(member.qualification)
    )
    semantic_scope = receipt.qualification.semantic_scope
    if semantic_scope not in {"explicit-distribution-shares", FIGURE_LABEL_SCOPE}:
        raise ValueError("Unsupported chart qualification scope")
    if (
        member.kind is not ObjectKind.CHART
        or member.page_index not in scope.selected_page_indices
        or (
            receipt.object_id,
            receipt.source_manifest_id,
            receipt.ir,
            receipt.description,
            receipt.source_svg,
        )
        != (
            member.object_id,
            scope.source_manifest_id,
            member.ir,
            member.description,
            member.source_svg,
        )
        or not receipt.region_id
    ):
        raise ValueError(
            "Chart qualification differs from the pinned object dependencies"
        )
    expected_lineage = {receipt.raw_chart, receipt.raw_description, receipt.view}
    if (
        len(member.lineage_refs) != 3
        or set(member.lineage_refs) != expected_lineage
        or len(expected_lineage) != 3
    ):
        raise ValueError(
            "Chart publication requires the complete raw branch and model-view closure"
        )
    source = sources.load(scope.source_manifest_id)
    anchor = receipt.qualification.source
    if (
        source.manifest.source.sha256 != scope.source_sha256
        or len(source.manifest.pages) != scope.source_page_count
        or (anchor.document_sha256, anchor.source_revision, anchor.page_index)
        != (scope.source_sha256, scope.source_sha256, member.page_index)
    ):
        raise ValueError("Chart qualification is outside the pinned source scope")
    page_record = source.manifest.pages[member.page_index]
    page = PageInput(
        scope.source_manifest_id,
        scope.source_sha256,
        member.page_index,
        page_record.width,
        page_record.height,
        page_record.svg,
        read_text_sidecar(sources, source, member.page_index),
    )
    view: FigureModelView | ContextualFigureModelView = TypeAdapter(
        FigureModelView | ContextualFigureModelView
    ).validate_json(assets.get(receipt.view), strict=True, extra="forbid")
    context_ids = (
        tuple(observation.source_span_id for observation in view.page_context)
        if isinstance(view, ContextualFigureModelView)
        else ()
    )
    prepared = prepare_figure(
        page=page,
        native_svg=sources.get(page_record.svg),
        bbox=anchor.bbox,
        region_id=receipt.region_id,
        context_span_ids=context_ids,
    )
    if (
        view != prepared.model_view
        or assets.get(member.source_svg) != prepared.svg.svg.encode()
    ):
        raise ValueError(
            "Chart SVG or model view differs from the pinned source derivation"
        )
    raw_chart = TypeAdapter(ChartIR).validate_json(
        assets.get(receipt.raw_chart), strict=True
    )
    raw_description = TypeAdapter(TextDescription).validate_json(
        assets.get(receipt.raw_description), strict=True
    )
    if (
        raw_chart.verification is Verification.REJECTED
        or raw_description.verification is Verification.REJECTED
    ):
        raise ValueError("Rejected raw chart branches cannot be published")
    if semantic_scope == FIGURE_LABEL_SCOPE:
        labels = qualify_source_labels(prepared.svg, raw_chart, raw_description)
        expected = (labels.chart, labels.description, labels.receipt)
    else:
        numeric = DonutQualification(prepared).qualify_pair(
            prepared.svg, raw_chart, raw_description
        )
        expected = (numeric.chart, numeric.description, numeric.receipt)
    chart = TypeAdapter(ChartIR).validate_json(assets.get(member.ir), strict=True)
    description = TypeAdapter(TextDescription).validate_json(
        assets.get(member.description), strict=True
    )
    if (chart, description, receipt.qualification) != expected:
        raise ValueError(
            "Chart projection or receipt differs from independent source qualification"
        )
    if semantic_scope == "explicit-distribution-shares":
        validate_pair(prepared.svg, chart, description)
    return chart, description, expected[2]
