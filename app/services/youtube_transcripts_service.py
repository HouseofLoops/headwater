"""
YouTube Transcripts Service.

This module handles all business logic for fetching and processing
YouTube video transcripts, keeping the API router thin.

Features:
- Proxy rotation with automatic retry on IP blocks
- Async-first design using asyncio.to_thread()
- Centralized exception handling
- Updated for youtube-transcript-api v1.x API compatibility
"""
import logging
import asyncio
import csv
import io
from typing import List, Optional, Dict, Any, Callable, TypeVar
from functools import wraps

from youtube_transcript_api import (
    YouTubeTranscriptApi,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    IpBlocked,
    RequestBlocked,
)
from youtube_transcript_api.proxies import GenericProxyConfig
from youtube_transcript_api.formatters import WebVTTFormatter, SRTFormatter
from requests.exceptions import ProxyError, ConnectionError as RequestsConnectionError
from fastapi import HTTPException

from app.core.proxy import get_proxy_sync, proxy_for, is_host_excluded, rotate_proxy, ENABLE_PROXY, mask_proxy

# Everything this service fetches lives on youtube.com, so proxy decisions
# are made against this one host.
YOUTUBE_HOST = "https://www.youtube.com"

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Maximum retry attempts when IP is blocked
MAX_RETRY_ATTEMPTS = 3


class YouTubeTranscriptsService:
    """Service class for YouTube transcript operations with proxy support."""

    def __init__(self):
        """Initialize the service."""
        self._api_cache: Dict[str, YouTubeTranscriptApi] = {}

    def _get_youtube_api(self, proxy_url: Optional[str] = None) -> YouTubeTranscriptApi:
        """
        Get or create a YouTubeTranscriptApi instance with the given proxy.

        Args:
            proxy_url: Optional proxy URL to use

        Returns:
            YouTubeTranscriptApi instance
        """
        cache_key = proxy_url or "no_proxy"

        if cache_key not in self._api_cache:
            if proxy_url:
                # This used to log ``proxy_url[:50]``. Truncation is not redaction:
                # it hides the password only while the username runs past the cut.
                # This deployment's username is 52 characters, so the secret stayed
                # off the line by luck; a shorter one printed it, which is how a
                # credential turned up in real log output. mask_proxy replaces the
                # password regardless of length.
                logger.debug("Creating YouTube API with proxy: %s", mask_proxy(proxy_url))
                proxy_config = GenericProxyConfig(
                    http_url=proxy_url,
                    https_url=proxy_url
                )
                self._api_cache[cache_key] = YouTubeTranscriptApi(proxy_config=proxy_config)
            else:
                logger.debug("Creating YouTube API without proxy")
                self._api_cache[cache_key] = YouTubeTranscriptApi()

        return self._api_cache[cache_key]

    def _get_current_api(self) -> YouTubeTranscriptApi:
        """
        Get YouTubeTranscriptApi with current proxy configuration.

        Returns:
            YouTubeTranscriptApi instance with current proxy
        """
        # Ask for the proxy by host rather than globally. Bright Data refuses
        # CONNECT to youtube.com with policy_20050 (a compliance permission, not
        # a zone setting), so listing it in NO_PROXY_HOSTS lets Reddit and news
        # keep their proxy while transcripts go direct, which is where they work.
        proxy_url = proxy_for(YOUTUBE_HOST) if ENABLE_PROXY else None
        return self._get_youtube_api(proxy_url)

    def _handle_youtube_exception(self, e: Exception, video_id: str, operation: str) -> None:
        """
        Handle YouTube API exceptions consistently.

        Args:
            e: The exception to handle
            video_id: The video ID being processed
            operation: Description of the operation that failed

        Raises:
            HTTPException: Always raises an appropriate HTTP exception
        """
        if isinstance(e, NoTranscriptFound):
            raise HTTPException(
                status_code=404,
                detail="No transcript found for the given video ID."
            )
        elif isinstance(e, TranscriptsDisabled):
            raise HTTPException(
                status_code=403,
                detail="Transcripts are disabled for this video."
            )
        elif isinstance(e, VideoUnavailable):
            raise HTTPException(
                status_code=404,
                detail="The specified video is unavailable."
            )
        elif isinstance(e, (IpBlocked, RequestBlocked)):
            logger.error(
                "IP blocked while %s for video_id %s: %s",
                operation, video_id, mask_proxy(str(e)),
            )
            raise HTTPException(
                status_code=503,
                detail="YouTube is temporarily blocking requests. Please try again later."
            )
        elif isinstance(e, (ProxyError, RequestsConnectionError)):
            # requests embeds the full proxy URL, credentials and all, in the text
            # of ProxyError and ConnectionError ("Cannot connect to proxy ...").
            # Printing str(e) raw is the same leak as the truncated debug line was.
            logger.error(
                "Proxy error while %s for video_id %s: %s",
                operation, video_id, mask_proxy(str(e)),
            )
            raise HTTPException(
                status_code=502,
                detail="Proxy connection failed. Please check proxy configuration."
            )
        elif isinstance(e, HTTPException):
            raise e
        else:
            # Catch-all branch: whatever requests raised underneath may carry the
            # proxy URL in its message, so mask before logging.
            logger.error(
                "Error %s for video_id %s: %s",
                operation, video_id, mask_proxy(str(e)),
            )
            raise HTTPException(
                status_code=500,
                detail=f"Internal Server Error while {operation}."
            )

    def _execute_with_retry(
        self,
        func: Callable[..., T],
        video_id: str,
        operation: str,
        *args,
        **kwargs
    ) -> T:
        """
        Execute a function with retry logic and proxy rotation.

        Args:
            func: Function to execute (receives api as first arg)
            video_id: Video ID for logging
            operation: Operation description for logging
            *args: Additional args for func
            **kwargs: Additional kwargs for func

        Returns:
            Result from the function

        Raises:
            HTTPException: If all retries fail
        """
        last_exception = None

        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                api = self._get_current_api()
                return func(api, *args, **kwargs)
            except (IpBlocked, RequestBlocked, ProxyError, RequestsConnectionError) as e:
                last_exception = e
                logger.warning(
                    f"Attempt {attempt + 1}/{MAX_RETRY_ATTEMPTS} failed for {operation} "
                    f"(video_id={video_id}): {type(e).__name__}"
                )

                # No point rotating when this host bypasses the proxy: every
                # candidate would be skipped and we would just retry direct.
                if (
                    ENABLE_PROXY
                    and not is_host_excluded(YOUTUBE_HOST)
                    and attempt < MAX_RETRY_ATTEMPTS - 1
                ):
                    # Rotate to a new proxy
                    new_proxy = rotate_proxy()
                    if new_proxy:
                        logger.info("Rotating to new proxy: %s", mask_proxy(new_proxy))
                        # Clear cache to force new API instance
                        self._api_cache.clear()
                    else:
                        logger.warning("No alternative proxy available")
            except Exception as e:
                self._handle_youtube_exception(e, video_id, operation)

        # All retries failed
        self._handle_youtube_exception(last_exception, video_id, operation)

    def fetch_transcript(
        self,
        video_id: str,
        languages: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch transcript for a YouTube video with retry support.

        Args:
            video_id: YouTube video ID
            languages: List of language codes in descending priority

        Returns:
            List of transcript items with text, start, and duration

        Raises:
            HTTPException: If transcript cannot be fetched
        """
        if languages is None:
            languages = ["en"]

        def _fetch(api: YouTubeTranscriptApi) -> List[Dict[str, Any]]:
            fetched = api.fetch(video_id, languages=tuple(languages))
            return fetched.to_raw_data()

        return self._execute_with_retry(_fetch, video_id, "fetching transcript")

    async def fetch_transcript_async(
        self,
        video_id: str,
        languages: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Asynchronously fetch transcript for a YouTube video.

        Args:
            video_id: YouTube video ID
            languages: List of language codes in descending priority

        Returns:
            List of transcript items
        """
        return await asyncio.to_thread(
            self.fetch_transcript,
            video_id,
            languages
        )

    def list_available_transcripts(self, video_id: str) -> List[Dict[str, Any]]:
        """
        List all available transcripts for a video with retry support.

        Args:
            video_id: YouTube video ID

        Returns:
            List of transcript metadata dictionaries

        Raises:
            HTTPException: If transcripts cannot be listed
        """
        def _list(api: YouTubeTranscriptApi) -> List[Dict[str, Any]]:
            transcript_list = api.list(video_id)
            transcripts_info = []
            for transcript in transcript_list:
                translation_langs = [
                    {"language": lang.language, "language_code": lang.language_code}
                    for lang in transcript.translation_languages
                ]
                transcripts_info.append({
                    "video_id": transcript.video_id,
                    "language": transcript.language,
                    "language_code": transcript.language_code,
                    "is_generated": transcript.is_generated,
                    "is_translatable": transcript.is_translatable,
                    "translation_languages": translation_langs
                })
            return transcripts_info

        return self._execute_with_retry(_list, video_id, "listing transcripts")

    async def list_available_transcripts_async(
        self,
        video_id: str
    ) -> List[Dict[str, Any]]:
        """
        Asynchronously list all available transcripts.

        Args:
            video_id: YouTube video ID

        Returns:
            List of transcript metadata dictionaries
        """
        return await asyncio.to_thread(
            self.list_available_transcripts,
            video_id
        )

    def get_transcript_metadata(
        self,
        video_id: str,
        languages: List[str] = None
    ):
        """
        Get transcript metadata for a video.

        Args:
            video_id: YouTube video ID
            languages: List of language codes to find

        Returns:
            Transcript object with metadata

        Raises:
            HTTPException: If metadata cannot be retrieved
        """
        if languages is None:
            languages = ["en"]

        def _get_metadata(api: YouTubeTranscriptApi):
            transcript_list = api.list(video_id)
            return transcript_list.find_transcript(languages)

        return self._execute_with_retry(_get_metadata, video_id, "getting metadata")

    def translate_transcript(
        self,
        video_id: str,
        target_language: str,
        source_languages: List[str] = None
    ) -> Dict[str, Any]:
        """
        Translate a transcript to the target language with retry support.

        Args:
            video_id: YouTube video ID
            target_language: Target language code
            source_languages: List of source language codes

        Returns:
            Dictionary with translated transcript data and metadata

        Raises:
            HTTPException: If translation fails
        """
        if source_languages is None:
            source_languages = ["en"]

        def _translate(api: YouTubeTranscriptApi) -> Dict[str, Any]:
            transcript_list = api.list(video_id)
            transcript_obj = transcript_list.find_transcript(source_languages)
            translated_transcript = transcript_obj.translate(target_language)
            fetched = translated_transcript.fetch()
            transcript_data = fetched.to_raw_data()

            translation_langs = [
                {"language": lang.language, "language_code": lang.language_code}
                for lang in translated_transcript.translation_languages
            ]

            return {
                "video_id": fetched.video_id,
                "language": fetched.language,
                "language_code": fetched.language_code,
                "is_generated": fetched.is_generated,
                "is_translatable": translated_transcript.is_translatable,
                "translation_languages": translation_langs,
                "transcript_data": transcript_data
            }

        return self._execute_with_retry(_translate, video_id, "translating transcript")

    async def translate_transcript_async(
        self,
        video_id: str,
        target_language: str,
        source_languages: List[str] = None
    ) -> Dict[str, Any]:
        """
        Asynchronously translate a transcript.

        Args:
            video_id: YouTube video ID
            target_language: Target language code
            source_languages: List of source language codes

        Returns:
            Dictionary with translated transcript data and metadata
        """
        return await asyncio.to_thread(
            self.translate_transcript,
            video_id,
            target_language,
            source_languages
        )

    def format_transcript(
        self,
        video_id: str,
        format_type: str,
        languages: List[str] = None
    ) -> str:
        """
        Fetch and format transcript directly from a video with retry support.

        Args:
            video_id: YouTube video ID
            format_type: Desired format (txt, vtt, srt, csv)
            languages: List of language codes in descending priority

        Returns:
            Formatted transcript string

        Raises:
            HTTPException: If format type is invalid or transcript cannot be fetched
        """
        if languages is None:
            languages = ["en"]

        def _format(api: YouTubeTranscriptApi) -> str:
            fetched = api.fetch(video_id, languages=tuple(languages))

            if format_type == "txt":
                return "\n".join([snippet.text for snippet in fetched.snippets])

            elif format_type == "vtt":
                formatter = WebVTTFormatter()
                return formatter.format_transcript(fetched)

            elif format_type == "srt":
                formatter = SRTFormatter()
                return formatter.format_transcript(fetched)

            elif format_type == "csv":
                output = io.StringIO()
                writer = csv.writer(output)
                writer.writerow(['Start', 'Duration', 'Text'])
                for snippet in fetched.snippets:
                    writer.writerow([snippet.start, snippet.duration, snippet.text])
                return output.getvalue()

            else:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid format type specified. Use: txt, vtt, srt, or csv"
                )

        return self._execute_with_retry(_format, video_id, "formatting transcript")

    async def format_transcript_async(
        self,
        video_id: str,
        format_type: str,
        languages: List[str] = None
    ) -> str:
        """
        Asynchronously format a transcript.

        Args:
            video_id: YouTube video ID
            format_type: Desired format (txt, vtt, srt, csv)
            languages: List of language codes in descending priority

        Returns:
            Formatted transcript string
        """
        return await asyncio.to_thread(
            self.format_transcript,
            video_id,
            format_type,
            languages
        )


# Singleton instance for convenience
youtube_transcripts_service = YouTubeTranscriptsService()
