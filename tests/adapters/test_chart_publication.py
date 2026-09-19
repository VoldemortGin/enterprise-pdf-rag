"""Publication admits a chart only from its pinned source and raw branch closure."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from tests.adapters.test_donut_qualification import sample

from enterprise_pdf_rag.adapters.chart_publication import (
    ChartPublicationReceipt,
    validate_chart_member,
)
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
from enterprise_pdf_rag.adapters.processing_retrieval import ProcessingRetrieval
from enterprise_pdf_rag.adapters.processing_store import ProcessingStore
from enterprise_pdf_rag.documents.models import (
    DocumentManifest,
    PageRecord,
    RegionRecord,
    TextSidecar,
    TextSpan,
)
from enterprise_pdf_rag.figures.models import (
    ChartIR,
    DescriptionClaim,
    QualifiedFigurePair,
    SvgElement,
    TextDescription,
    Verification,
)
from enterprise_pdf_rag.processing.models import (
    ObjectKind,
    ObjectProcessingRecord,
    PageInput,
    ProcessingScope,
    StageOutcome,
    StageState,
)
from enterprise_pdf_rag.processing.retrieval import (
    RetrievalMember,
    require_financial_qualification,
)


def member_fixture(
    tmp_path: Path,
    *,
    label_scope: bool = False,
    context: bool = False,
) -> tuple[
    LocalDocumentStore,
    LocalDocumentStore,
    ProcessingScope,
    RetrievalMember,
    QualifiedFigurePair,
]:
    initial, raw_chart, raw_description = sample()
    sources, assets = (
        LocalDocumentStore(tmp_path / "source"),
        LocalDocumentStore(tmp_path / "semantic"),
    )
    pdf = sources.put(b"independently-authored-source", media_type="application/pdf")
    native = initial.crop_svg.split(">", 1)[1][:-6].encode()
    native_ref = sources.put(native, media_type="image/svg+xml")
    longest: dict[str, SvgElement] = {}
    for element in initial.svg.elements:
        assert element.source_span_id is not None
        old = longest.get(element.source_span_id)
        if old is None or len(old.text) < len(element.text):
            longest[element.source_span_id] = element
    sidecar = TextSidecar(
        "source-text-v1",
        pdf.sha256,
        0,
        tuple(
            TextSpan(key, element.text, element.anchor.bbox)
            for key, element in longest.items()
        ),
    )
    if context:
        sidecar = replace(
            sidecar,
            spans=(
                *sidecar.spans,
                TextSpan(
                    "footer", "Comparisons use constant FX.", (5.0, 152.0, 230.0, 158.0)
                ),
            ),
        )
    text_ref = sources.put(
        TypeAdapter(TextSidecar).dump_json(sidecar), media_type="application/json"
    )
    manifest_id = sources.publish(
        DocumentManifest(
            "source-ingestion-v1",
            "authored.pdf",
            pdf,
            "authored-fixture",
            (
                PageRecord(
                    0, 240.0, 160.0, 0, native_ref, text_ref, len(sidecar.spans), ()
                ),
            ),
            RegionRecord(
                0, initial.svg.source.bbox, native_ref, native_ref, text_ref, ()
            ),
        )
    )
    prepared = prepare_figure(
        page=PageInput(manifest_id, pdf.sha256, 0, 240.0, 160.0, native_ref, sidecar),
        native_svg=native,
        bbox=initial.svg.source.bbox,
        region_id="authored-distribution",
        context_span_ids=("footer",) if context else (),
    )
    raw_chart = replace(raw_chart, binding=prepared.svg.binding)
    raw_description = replace(raw_description, binding=prepared.svg.binding)
    if label_scope:
        assert raw_chart.title is not None
        raw_description = replace(
            raw_description,
            claims=(
                *raw_description.claims,
                DescriptionClaim(raw_chart.title.text, raw_chart.title.evidence),
            ),
        )
        labels = qualify_source_labels(prepared.svg, raw_chart, raw_description)
        pair = QualifiedFigurePair(
            labels.chart,
            labels.description,
            labels.receipt,
            labels.raw_chart_id,
            labels.raw_description_id,
            labels.excluded_claim_paths,
        )
    else:
        pair = DonutQualification(prepared).qualify_pair(
            prepared.svg, raw_chart, raw_description
        )
    raw_chart_ref = assets.put(
        TypeAdapter(ChartIR).dump_json(raw_chart), media_type="application/json"
    )
    raw_description_ref = assets.put(
        TypeAdapter(TextDescription).dump_json(raw_description),
        media_type="application/json",
    )
    view_ref = assets.put(
        TypeAdapter[object](type(prepared.model_view)).dump_json(prepared.model_view),
        media_type="application/json",
    )
    chart_ref = assets.put(
        TypeAdapter(ChartIR).dump_json(pair.chart), media_type="application/json"
    )
    description_ref = assets.put(
        TypeAdapter(TextDescription).dump_json(pair.description),
        media_type="application/json",
    )
    svg_ref = assets.put(prepared.svg.svg.encode(), media_type="image/svg+xml")
    receipt = ChartPublicationReceipt(
        object_id="chart",
        source_manifest_id=manifest_id,
        region_id="authored-distribution",
        ir=chart_ref,
        description=description_ref,
        source_svg=svg_ref,
        raw_chart=raw_chart_ref,
        raw_description=raw_description_ref,
        view=view_ref,
        qualification=pair.receipt,
    )
    receipt_ref = assets.put(
        receipt.model_dump_json().encode(), media_type="application/json"
    )
    member = RetrievalMember(
        "chart",
        ObjectKind.CHART,
        0,
        chart_ref,
        description_ref,
        receipt_ref,
        description_ref,
        svg_ref,
        "test-embedding-not-called",
        1,
        lineage_refs=(raw_chart_ref, raw_description_ref, view_ref),
    )
    return (
        sources,
        assets,
        ProcessingScope(manifest_id, pdf.sha256, 1, (0,)),
        member,
        pair,
    )


def test_source_aware_chart_preflight_rebuilds_the_actual_view_and_projection(
    tmp_path: Path,
) -> None:
    sources, assets, scope, member, pair = member_fixture(tmp_path)
    chart, description, receipt = validate_chart_member(sources, assets, scope, member)
    assert (chart, description, receipt) == (pair.chart, pair.description, pair.receipt)


@pytest.mark.parametrize(
    "change", ["raw-branch", "view", "missing-raw", "qualified-value", "receipt"]
)
def test_plausible_immutable_references_cannot_hide_tampered_source_lineage(
    tmp_path: Path, change: str
) -> None:
    sources, assets, scope, member, _ = member_fixture(tmp_path)
    receipt = ChartPublicationReceipt.model_validate_json(
        assets.get(member.qualification)
    )
    lineage = member.lineage_refs
    if change == "raw-branch":
        raw = TypeAdapter(ChartIR).validate_json(
            assets.get(receipt.raw_chart), strict=True
        )
        first, second = raw.points
        raw = replace(raw, points=(replace(first, category=second.category), second))
        new_ref = assets.put(
            TypeAdapter(ChartIR).dump_json(raw), media_type="application/json"
        )
        lineage = tuple(new_ref if ref == receipt.raw_chart else ref for ref in lineage)
        receipt = receipt.model_copy(update={"raw_chart": new_ref})
    elif change == "view":
        view = TypeAdapter(FigureModelView).validate_json(
            assets.get(receipt.view), strict=True
        )
        new_ref = assets.put(
            TypeAdapter(FigureModelView).dump_json(
                replace(view, source_manifest_id="f" * 64)
            ),
            media_type="application/json",
        )
        lineage = tuple(new_ref if ref == receipt.view else ref for ref in lineage)
        receipt = receipt.model_copy(update={"view": new_ref})
    elif change == "missing-raw":
        assets.asset_path(receipt.raw_chart).unlink()
    elif change == "qualified-value":
        chart = TypeAdapter(ChartIR).validate_json(assets.get(member.ir), strict=True)
        first, second = chart.points
        assert first.value.value is not None
        chart = replace(
            chart,
            points=(
                replace(first, value=replace(first.value, value=first.value.value + 1)),
                second,
            ),
        )
        new_ref = assets.put(
            TypeAdapter(ChartIR).dump_json(chart), media_type="application/json"
        )
        member = replace(member, ir=new_ref)
        receipt = receipt.model_copy(update={"ir": new_ref})
    else:
        receipt = receipt.model_copy(
            update={
                "qualification": replace(
                    receipt.qualification, method="self-reported model verified"
                )
            }
        )
    ref = assets.put(receipt.model_dump_json().encode(), media_type="application/json")
    member = replace(member, qualification=ref, lineage_refs=lineage)
    with pytest.raises((ValueError, FileNotFoundError)):
        validate_chart_member(sources, assets, scope, member)


def test_omitting_raw_inputs_from_manifest_closure_fails_before_admission(
    tmp_path: Path,
) -> None:
    sources, assets, scope, member, _ = member_fixture(tmp_path)
    with pytest.raises(ValueError, match="complete raw branch"):
        validate_chart_member(sources, assets, scope, replace(member, lineage_refs=()))


@pytest.mark.parametrize(
    "label_scope,with_context", [(False, False), (True, False), (True, True)]
)
def test_durable_chart_search_embeds_only_qualified_description_and_hydrates_its_snapshot(
    tmp_path: Path,
    label_scope: bool,
    with_context: bool,
) -> None:
    sources, assets, scope, member, pair = member_fixture(
        tmp_path, label_scope=label_scope, context=with_context
    )
    receipt = ChartPublicationReceipt.model_validate_json(
        assets.get(member.qualification)
    )
    stages = tuple(
        StageOutcome(
            name, str(index) * 64, StageState.SUCCEEDED, "test-source-bound", ref
        )
        for index, (name, ref) in enumerate(
            (
                ("qualified_ir", member.ir),
                ("qualified_description", member.description),
                ("qualification", member.qualification),
                ("svg", member.source_svg),
                ("ir", receipt.raw_chart),
                ("description", receipt.raw_description),
                ("model_view", receipt.view),
            ),
            start=1,
        )
    )
    record = ObjectProcessingRecord(member.object_id, ObjectKind.CHART, stages)

    class EmbeddingSpy:
        fingerprint = "test-qualified-description-only"
        texts: list[str]

        def __init__(self) -> None:
            self.texts = []

        def embed_description(self, text: str) -> tuple[float, ...]:
            self.texts.append(text)
            return (1.0, 0.0)

        def embed_query(self, text: str) -> tuple[float, ...]:
            return (1.0, 0.0)

    embedding = EmbeddingSpy()
    retrieval = ProcessingRetrieval(sources, ProcessingStore(assets.root), embedding)
    publication = retrieval.build(scope, ((0, record),))
    assert embedding.texts == [pair.description.text]
    reopened = ProcessingRetrieval(sources, ProcessingStore(assets.root), embedding)
    (hit,) = reopened.search(publication, "Agency share", limit=1)
    context = reopened.resolve(publication, hit)
    assert context.ir == pair.chart and context.description == pair.description
    assert context.snapshot_id == publication.snapshot_id
    if label_scope:
        assert context.scope == FIGURE_LABEL_SCOPE
        assert isinstance(context.ir, ChartIR)
        assert context.ir.verification is Verification.PENDING
        assert context.ir.axes == ()
        assert context.ir.points == ()
        assert context.ir.marks == ()
        assert context.ir.title is context.ir.period is None
        with pytest.raises(ValueError, match="financial"):
            require_financial_qualification(context)
    else:
        assert context.scope == "explicit-distribution-shares"
        require_financial_qualification(context)
    assert set(member.lineage_refs).issubset(set(publication.dependencies))
    with pytest.raises(ValueError, match="snapshot"):
        reopened.resolve(publication, replace(hit, snapshot_id="f" * 64))


@pytest.mark.parametrize(
    "change",
    [
        "numeric-projection",
        "unknown-scope",
        "context-text",
        "context-id",
        "context-page",
    ],
)
def test_label_scope_does_not_admit_forged_projection_or_context(
    tmp_path: Path, change: str
) -> None:
    sources, assets, scope, member, _ = member_fixture(
        tmp_path, label_scope=True, context=True
    )
    receipt = ChartPublicationReceipt.model_validate_json(
        assets.get(member.qualification)
    )
    if change == "numeric-projection":
        raw_chart = TypeAdapter(ChartIR).validate_json(
            assets.get(receipt.raw_chart), strict=True
        )
        new_ir = assets.put(
            TypeAdapter(ChartIR).dump_json(raw_chart), media_type="application/json"
        )
        receipt = receipt.model_copy(update={"ir": new_ir})
        member = replace(member, ir=new_ir)
    elif change == "unknown-scope":
        receipt = receipt.model_copy(
            update={
                "qualification": replace(
                    receipt.qualification, semantic_scope="self-promoted-financial-v1"
                )
            }
        )
    else:
        view = TypeAdapter(ContextualFigureModelView).validate_json(
            assets.get(receipt.view), strict=True
        )
        (old,) = view.page_context
        context = (
            replace(old, text="Comparisons use actual FX.")
            if change == "context-text"
            else replace(old, source_span_id="missing")
            if change == "context-id"
            else replace(old, source=replace(old.source, page_index=1))
        )
        changed = replace(view, page_context=(context,))
        view_ref = assets.put(
            TypeAdapter(ContextualFigureModelView).dump_json(changed),
            media_type="application/json",
        )
        member = replace(
            member,
            lineage_refs=tuple(
                view_ref if ref == receipt.view else ref for ref in member.lineage_refs
            ),
        )
        receipt = receipt.model_copy(update={"view": view_ref})
    receipt_ref = assets.put(
        receipt.model_dump_json().encode(), media_type="application/json"
    )
    member = replace(member, qualification=receipt_ref)
    with pytest.raises(ValueError):
        validate_chart_member(sources, assets, scope, member)
