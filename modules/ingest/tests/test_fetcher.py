import unittest
from unittest.mock import patch, MagicMock
import httpx
import asyncio
from modules.ingest.src.fetcher import fetch_feed, FetchResult

class TestHTTPFetcher(unittest.IsolatedAsyncioTestCase):
    @patch("httpx.AsyncClient.get")
    async def test_fetch_success_200(self, mock_get) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<xml>rss</xml>"
        mock_response.headers = {"etag": "w/\"etag-value\"", "last-modified": "Wed, 21 Oct 2015 07:28:00 GMT"}
        mock_get.return_value = mock_response

        result = await fetch_feed("https://example.com/feed.xml", etag="old-etag")
        
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.content, b"<xml>rss</xml>")
        self.assertEqual(result.etag, "w/\"etag-value\"")
        self.assertEqual(result.last_modified, "Wed, 21 Oct 2015 07:28:00 GMT")
        self.assertIsNone(result.error_class)
        self.assertEqual(result.retry_count, 0)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_cache_304(self, mock_get) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 304
        mock_get.return_value = mock_response

        result = await fetch_feed("https://example.com/feed.xml", etag="old-etag", last_modified="old-lm")
        
        self.assertEqual(result.status_code, 304)
        self.assertIsNone(result.content)
        self.assertEqual(result.etag, "old-etag")
        self.assertEqual(result.last_modified, "old-lm")
        self.assertIsNone(result.error_class)
        self.assertEqual(result.retry_count, 0)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_non_transient_404_fails_fast(self, mock_get) -> None:
        # Mock raising HTTPStatusError for 404
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        
        # httpx raises HTTPStatusError when raise_for_status() is called
        mock_get.side_effect = httpx.HTTPStatusError(
            message="404 Not Found",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml")
        
        self.assertEqual(result.status_code, 404)
        self.assertIsNone(result.content)
        self.assertEqual(result.error_class, "http_error_4xx")
        self.assertIn("HTTP 404", result.error_detail)
        self.assertEqual(result.retry_count, 0)  # Fails fast, 0 retries

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_transient_500_retries_and_fails(self, mock_get, mock_sleep) -> None:
        # Mock raising HTTPStatusError for 500
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        
        mock_get.side_effect = httpx.HTTPStatusError(
            message="500 Internal Server Error",
            request=MagicMock(),
            response=mock_response
        )

        result = await fetch_feed("https://example.com/feed.xml", max_retries=2, backoff_factor=0.01)
        
        self.assertEqual(result.status_code, 500)
        self.assertEqual(result.error_class, "http_error_5xx")
        self.assertEqual(result.retry_count, 2)  # Should retry twice (3 total attempts)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("asyncio.sleep", return_value=None)
    @patch("httpx.AsyncClient.get")
    async def test_fetch_timeout_retries_and_fails(self, mock_get, mock_sleep) -> None:
        mock_get.side_effect = httpx.TimeoutException("Connection timed out", request=MagicMock())

        result = await fetch_feed("https://example.com/feed.xml", max_retries=2)
        
        self.assertIsNone(result.status_code)
        self.assertEqual(result.error_class, "timeout_error")
        self.assertEqual(result.retry_count, 2)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("httpx.AsyncClient.get")
    async def test_fetch_unexpected_error_fails_immediately(self, mock_get) -> None:
        mock_get.side_effect = RuntimeError("Something completely unexpected happened")

        result = await fetch_feed("https://example.com/feed.xml", max_retries=2)
        
        self.assertIsNone(result.status_code)
        self.assertEqual(result.error_class, "unexpected_error")
        self.assertEqual(result.retry_count, 0)  # Must fail immediately, no retries!

if __name__ == "__main__":
    unittest.main()
