from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services import compensatory as svc

router = APIRouter(tags=["补正向"])


class CompensatoryIn(BaseModel):
    reverse_entry_key: str = Field(..., description="被引用的已归并冲正流水键")
    amount_cents: int = Field(..., gt=0, description="补正向金额（分），必须 > 0")
    offset_cents: int | None = Field(
        None, gt=0, description="对消分；缺省等于补正向金额，不得大于补额"
    )
    entry_key: str | None = Field(None, description="流水键；缺省自动生成 CMP-YYYYMMDD-NNN")
    biz_ref: str = ""
    booked_at: str | None = None
    note: str = ""


@router.post("/compensatory-forwards")
def post_compensatory(payload: CompensatoryIn):
    return svc.register(**payload.model_dump())


@router.get("/compensatory-forwards")
def get_compensatory():
    return svc.list_compensatory_entries()


@router.get("/offset-edges")
def get_offset_edges():
    return svc.list_offset_edges()


@router.get("/compensatory-report")
def get_report():
    return svc.report()
