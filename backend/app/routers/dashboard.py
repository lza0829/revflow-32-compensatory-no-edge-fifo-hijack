from fastapi import APIRouter

from app.services import settle as svc

router = APIRouter(tags=["总览"])


@router.get("/dashboard")
def get_dashboard():
    return svc.dashboard()
