from fastapi import APIRouter, Query

from app.services import settle as svc

router = APIRouter(tags=["流水"])


@router.get("/entries")
def get_entries(kind: str | None = Query(None, description="正向|冲正")):
    return svc.list_entries(kind)
