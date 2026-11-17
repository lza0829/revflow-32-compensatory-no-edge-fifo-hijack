from fastapi import APIRouter

from app.services import settle as svc

router = APIRouter(tags=["账户"])


@router.get("/accounts")
def get_accounts():
    return svc.list_accounts()
