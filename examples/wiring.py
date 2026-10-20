"""Use in the application's lifespan. This does not define public API routes."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from beauty_ai.image_gateway import HTTPImageGateway
from beauty_ai.review_service import ReviewService
from beauty_ai.text_gateway import OpenAITextGateway, make_openai_client


@dataclass
class Gateways:
    text: OpenAITextGateway
    image: HTTPImageGateway
    review: ReviewService


@asynccontextmanager
async def create_gateways():
    required = ["OPENAI_API_KEY", "OPENAI_TEXT_MODEL", "OPENAI_VISION_MODEL", "OPENAI_REVIEW_MODEL",
                "INFERENCE_BASE_URL", "INFERENCE_INTERNAL_TOKEN"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Missing configuration: " + ", ".join(missing))
    sdk = make_openai_client(os.environ["OPENAI_API_KEY"])
    async with sdk, httpx.AsyncClient(
        base_url=os.environ["INFERENCE_BASE_URL"],
        headers={"Authorization": "Bearer " + os.environ["INFERENCE_INTERNAL_TOKEN"]},
        timeout=httpx.Timeout(15.0, connect=3.0),
        follow_redirects=False,
    ) as http:
        text = OpenAITextGateway(
            sdk, text_model=os.environ["OPENAI_TEXT_MODEL"],
            vision_model=os.environ["OPENAI_VISION_MODEL"],
            review_model=os.environ["OPENAI_REVIEW_MODEL"],
            concurrency=int(os.getenv("TEXT_CONCURRENCY", "2")),
            total_timeout=float(os.getenv("TEXT_TOTAL_TIMEOUT_SECONDS", "90")),
        )
        yield Gateways(text=text, image=HTTPImageGateway(http), review=ReviewService(text))
