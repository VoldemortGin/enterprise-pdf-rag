"""A typed query endpoint; no model calls, free-text parsing or summary fallback."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from enterprise_pdf_rag.adapters.http.chart_qa_schemas import (
    ChartQueryErrorDetail,
    ChartQueryErrorResponse,
    ChartQueryRequest,
    ChartQueryResponse,
)
from enterprise_pdf_rag.figures.chart_qa.models import ChartQueryError, QueryFailure
from enterprise_pdf_rag.figures.chart_qa.service import ChartQAService


def create_chart_qa_router(service: ChartQAService) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/v1/queries",
        response_model=ChartQueryResponse,
        responses={
            409: {"model": ChartQueryErrorResponse},
            503: {"model": ChartQueryErrorResponse},
        },
    )
    def query(body: ChartQueryRequest) -> ChartQueryResponse | JSONResponse:
        try:
            return ChartQueryResponse.from_domain(service.answer(body.to_domain()))
        except ChartQueryError as error:
            status = 503 if error.code is QueryFailure.UNAVAILABLE_EVIDENCE else 409
            result = ChartQueryErrorResponse(
                error=ChartQueryErrorDetail(code=error.code.value, message=str(error))
            )
            return JSONResponse(
                status_code=status, content=result.model_dump(mode="json")
            )

    return router
