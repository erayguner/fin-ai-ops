"""Tests for centralised logging configuration."""

import io
import logging

from core.logging_config import configure_logging


class TestConfigureLogging:
    def test_configures_root_logger_level(self):
        configure_logging(level=logging.DEBUG, stream=io.StringIO())
        assert logging.getLogger().level == logging.DEBUG

    def test_adds_stream_handler(self):
        stream = io.StringIO()
        configure_logging(stream=stream)
        root = logging.getLogger()
        assert any(
            isinstance(h, logging.StreamHandler) and h.stream is stream
            for h in root.handlers
        )

    def test_quietens_noisy_loggers(self):
        configure_logging(stream=io.StringIO())
        for name in ("urllib3", "botocore", "google.auth", "google.cloud"):
            assert logging.getLogger(name).level >= logging.WARNING

    def test_custom_format(self):
        stream = io.StringIO()
        configure_logging(fmt="%(message)s", stream=stream)
        test_logger = logging.getLogger("test_custom_fmt")
        test_logger.info("hello")
        assert "hello" in stream.getvalue()

    def test_repeated_calls_no_duplicate_handlers(self):
        stream = io.StringIO()
        configure_logging(stream=stream)
        configure_logging(stream=stream)
        root = logging.getLogger()
        stream_handlers = [h for h in root.handlers if isinstance(h, logging.StreamHandler)]
        assert len(stream_handlers) == 1
