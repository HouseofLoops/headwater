"""
YouTube Transcripts API Router.

Thin API layer that delegates to the YouTubeTranscriptsService.
"""

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.auth import get_api_key
from app.core.cache_manager import generate_cache_key, get_cached_or_fetch
from app.core.rate_limiter import rate_limit
from app.services.youtube_transcripts_service import youtube_transcripts_service

logger = logging.getLogger(__name__)
youtube_transcripts_router = APIRouter()


# Pydantic models
class TranslationLanguage(BaseModel):
    language: str
    language_code: str


class TranscriptItem(BaseModel):
    text: str
    start: float
    duration: float


class TranscriptResponse(BaseModel):
    video_id: str
    language: str
    language_code: str
    is_generated: bool
    is_translatable: bool
    translation_languages: list[TranslationLanguage]
    transcript: list[TranscriptItem]


class TranscriptListResponse(BaseModel):
    transcripts: list[dict[str, Any]]


# One batch fans out to one upstream fetch per video id, so an unbounded list
# just times out the request instead of returning anything useful.
MAX_BATCH_VIDEO_IDS = 50


class BatchTranscriptRequest(BaseModel):
    video_ids: list[str] = Field(..., description="List of YouTube video IDs")
    languages: list[str] | None = Field(None, description="Language codes by priority")
    preserve_formatting: bool | None = Field(None, description="Preserve HTML formatting")


@youtube_transcripts_router.get("/get-transcript", response_model=TranscriptResponse, summary="Get Transcript")
async def get_transcript(
    video_id: str = Query(..., description="YouTube video ID", examples=["dQw4w9WgXcQ"]),
    languages: list[str] | None = Query(["en"], description="Language codes by priority", examples=[["en", "es"]]),
    preserve_formatting: bool = Query(False, description="Preserve HTML formatting"),
    api_key: str = Depends(get_api_key),
    _rate_limit: None = Depends(rate_limit),
):
    """Get transcript for a YouTube video with automatic proxy rotation."""
    cache_key = generate_cache_key(
        "youtube_transcript", video_id=video_id, languages=languages or ["en"], preserve_formatting=preserve_formatting
    )

    async def fetch_data():
        lang_list = languages or ["en"]

        # Use service with retry and proxy rotation
        transcript_data = await youtube_transcripts_service.fetch_transcript_async(video_id, lang_list)
        transcript_items = [TranscriptItem(**item) for item in transcript_data]

        # Get metadata via service
        transcript_meta = await asyncio.to_thread(
            youtube_transcripts_service.get_transcript_metadata, video_id, lang_list
        )

        return TranscriptResponse(
            video_id=transcript_meta.video_id,
            language=transcript_meta.language,
            language_code=transcript_meta.language_code,
            is_generated=transcript_meta.is_generated,
            is_translatable=transcript_meta.is_translatable,
            translation_languages=[
                TranslationLanguage(language=lang.language, language_code=lang.language_code)
                for lang in transcript_meta.translation_languages
            ],
            transcript=transcript_items,
        )

    return await get_cached_or_fetch(cache_key, fetch_data)


@youtube_transcripts_router.get("/list-transcripts", response_model=TranscriptListResponse, summary="List Transcripts")
async def list_transcripts(
    video_id: str = Query(..., description="YouTube video ID", examples=["dQw4w9WgXcQ"]),
    api_key: str = Depends(get_api_key),
    _rate_limit: None = Depends(rate_limit),
):
    """List all available transcripts for a video with automatic proxy rotation."""
    cache_key = generate_cache_key("youtube_transcript_list", video_id=video_id)

    async def fetch_data():
        # Service handles retry and proxy rotation
        transcripts_info = await youtube_transcripts_service.list_available_transcripts_async(video_id)
        return TranscriptListResponse(transcripts=transcripts_info)

    return await get_cached_or_fetch(cache_key, fetch_data)


@youtube_transcripts_router.get(
    "/translate-transcript", response_model=TranscriptResponse, summary="Translate Transcript"
)
async def translate_transcript(
    video_id: str = Query(..., description="YouTube video ID", examples=["dQw4w9WgXcQ"]),
    target_language: str = Query(..., description="Target language code", examples=["es"]),
    source_languages: list[str] | None = Query(["en"], description="Source language codes", examples=[["en"]]),
    api_key: str = Depends(get_api_key),
    _rate_limit: None = Depends(rate_limit),
):
    """Translate transcript to target language with automatic proxy rotation."""
    cache_key = generate_cache_key(
        "youtube_transcript_translate",
        video_id=video_id,
        target_language=target_language,
        source_languages=source_languages or ["en"],
    )

    async def fetch_data():
        # Service handles retry and proxy rotation
        result = await youtube_transcripts_service.translate_transcript_async(
            video_id, target_language, source_languages
        )

        transcript_items = [TranscriptItem(**item) for item in result["transcript_data"]]

        return TranscriptResponse(
            video_id=result["video_id"],
            language=result["language"],
            language_code=result["language_code"],
            is_generated=result["is_generated"],
            is_translatable=result["is_translatable"],
            translation_languages=[TranslationLanguage(**lang) for lang in result["translation_languages"]],
            transcript=transcript_items,
        )

    return await get_cached_or_fetch(cache_key, fetch_data)


@youtube_transcripts_router.post(
    "/batch-get-transcripts", response_model=list[TranscriptResponse], summary="Batch Get Transcripts"
)
async def batch_get_transcripts(
    payload: BatchTranscriptRequest | None = Body(None),
    # The query form predates the body and existing callers still send repeated
    # ?video_ids= parameters, so it stays supported alongside the JSON body.
    video_ids: list[str] | None = Query(None, description="List of video IDs (query form)"),
    languages: list[str] | None = Query(None, description="Language codes by priority"),
    preserve_formatting: bool | None = Query(None, description="Preserve HTML formatting"),
    api_key: str = Depends(get_api_key),
    _rate_limit: None = Depends(rate_limit),
):
    """Get transcripts for multiple videos with automatic proxy rotation.

    Accepts either form:
      - JSON body: {"video_ids": [...], "languages": [...], "preserve_formatting": false}
      - Query string: ?video_ids=ID1&video_ids=ID2&languages=en

    The body wins when both are supplied. See MAX_BATCH_VIDEO_IDS for the
    per-request cap; larger batches are rejected with HTTP 400.
    """
    resolved_video_ids = payload.video_ids if payload is not None and payload.video_ids else video_ids or []

    if not resolved_video_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                'video_ids is required: send a JSON body {"video_ids": [...]} or repeated ?video_ids= query parameters.'
            ),
        )

    if len(resolved_video_ids) > MAX_BATCH_VIDEO_IDS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many video IDs: {len(resolved_video_ids)} requested, maximum is {MAX_BATCH_VIDEO_IDS} per batch."
            ),
        )

    if payload is not None and payload.languages is not None:
        languages = payload.languages
    if payload is not None and payload.preserve_formatting is not None:
        preserve_formatting = payload.preserve_formatting
    if preserve_formatting is None:
        preserve_formatting = False

    async def get_single_transcript(video_id: str):
        cache_key = generate_cache_key(
            "youtube_transcript",
            video_id=video_id,
            languages=languages or ["en"],
            preserve_formatting=preserve_formatting,
        )

        async def fetch_data():
            lang_list = languages or ["en"]

            # Use service with retry and proxy rotation
            transcript_data = await youtube_transcripts_service.fetch_transcript_async(video_id, lang_list)
            transcript_items = [TranscriptItem(**item) for item in transcript_data]

            # Get metadata via service
            transcript_meta = await asyncio.to_thread(
                youtube_transcripts_service.get_transcript_metadata, video_id, lang_list
            )

            return TranscriptResponse(
                video_id=transcript_meta.video_id,
                language=transcript_meta.language,
                language_code=transcript_meta.language_code,
                is_generated=transcript_meta.is_generated,
                is_translatable=transcript_meta.is_translatable,
                translation_languages=[
                    TranslationLanguage(language=lang.language, language_code=lang.language_code)
                    for lang in transcript_meta.translation_languages
                ],
                transcript=transcript_items,
            )

        return await get_cached_or_fetch(cache_key, fetch_data)

    # Concurrent processing with caching
    tasks = [get_single_transcript(video_id) for video_id in resolved_video_ids]
    transcripts = await asyncio.gather(*tasks, return_exceptions=True)

    # Handle exceptions
    result = []
    for transcript in transcripts:
        if isinstance(transcript, Exception):
            result.append({"error": str(transcript)})
        else:
            result.append(transcript)

    return result


@youtube_transcripts_router.get("/format-transcript", summary="Format Transcript")
async def format_transcript(
    video_id: str = Query(..., description="YouTube video ID", examples=["dQw4w9WgXcQ"]),
    format_type: str = Query("json", description="Output format: json, txt, vtt, srt, csv"),
    languages: list[str] | None = Query(["en"], description="Language codes by priority"),
    api_key: str = Depends(get_api_key),
    _rate_limit: None = Depends(rate_limit),
):
    """Get transcript in specified format (JSON, TXT, VTT, SRT, CSV) with automatic proxy rotation."""
    cache_key = generate_cache_key(
        "youtube_transcript_format", video_id=video_id, format_type=format_type, languages=languages or ["en"]
    )

    async def fetch_data():
        lang_list = languages or ["en"]

        if format_type == "json":
            # Get transcript data and metadata via service
            transcript_data = await youtube_transcripts_service.fetch_transcript_async(video_id, lang_list)
            transcript_items = [TranscriptItem(**item) for item in transcript_data]

            transcript_meta = await asyncio.to_thread(
                youtube_transcripts_service.get_transcript_metadata, video_id, lang_list
            )

            response = TranscriptResponse(
                video_id=transcript_meta.video_id,
                language=transcript_meta.language,
                language_code=transcript_meta.language_code,
                is_generated=transcript_meta.is_generated,
                is_translatable=transcript_meta.is_translatable,
                translation_languages=[
                    TranslationLanguage(language=lang.language, language_code=lang.language_code)
                    for lang in transcript_meta.translation_languages
                ],
                transcript=transcript_items,
            )
            return TranscriptListResponse.model_validate({"transcripts": [response.model_dump()]})
        else:
            # Use service's format method for txt, vtt, srt, csv
            formatted = await youtube_transcripts_service.format_transcript_async(video_id, format_type, lang_list)
            return {"formatted_transcript": formatted}

    return await get_cached_or_fetch(cache_key, fetch_data)
