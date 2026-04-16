from fastapi import APIRouter
from angel.client import angel_client

router = APIRouter()


@router.get("/ping")
def ping():
    return {
        "status": "ok",
        "angel_connected": angel_client.is_connected,
    }
