from fastapi import APIRouter

from app.services import settle as svc

router = APIRouter(tags=["未平隔离"])


@router.get("/quarantine")
def get_quarantine():
    return svc.list_quarantine()
