from pydantic import BaseModel

class SignalResponse(BaseModel):
    ticker: str
    side: str
    score: float
    reason: str
    price: float
    qty: int
    stop: float
    take: float