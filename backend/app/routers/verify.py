from fastapi import APIRouter, Query

from app.services import settle as svc

router = APIRouter(tags=["净额互证"])


@router.get("/verify-reports")
def get_reports():
    return svc.list_verify_reports()


@router.post("/verify-reports")
def post_report(settle_run_id: int | None = Query(None)):
    return svc.run_verify(settle_run_id)
