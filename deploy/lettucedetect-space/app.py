"""LettuceDetect answer check behind one HTTP route — the RAG_AUDIT_BACKEND=lettuce endpoint.

Runs as a PRIVATE Hugging Face Docker Space (free CPU tier: 2 vCPU, 16GB RAM),
because the app's own Render box (512MB) cannot hold the model and the laptop
should not have to. Stateless: it never logs or stores what it is sent, which
matters because every request carries a tenant's retrieved chunks.

POST /check {"context": [str], "question": str, "answer": str}
  -> {"model": str, "spans": [{"text": str, "confidence": float}]}
An empty ``spans`` list means nothing in the answer was flagged.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from lettucedetect.models.inference import HallucinationDetector
from pydantic import BaseModel

MODEL = os.getenv("LETTUCE_MODEL", "KRLabsOrg/lettucedect-large-modernbert-en-v1")
detector = HallucinationDetector(method="transformer", model_path=MODEL)
app = FastAPI()


class CheckRequest(BaseModel):
    context: list[str]
    question: str
    answer: str


@app.post("/check")
def check(req: CheckRequest) -> dict:
    spans = detector.predict(
        context=req.context, question=req.question, answer=req.answer, output_format="spans"
    )
    return {
        "model": MODEL,
        "spans": [{"text": s["text"], "confidence": float(s["confidence"])} for s in spans],
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": MODEL}
