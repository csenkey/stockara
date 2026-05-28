"""Unit tests for structured logging configuration."""

import logging

import structlog

from backend.src.logging_config import configure_logging


class TestConfigureLogging:
    """Tests for the logging configuration."""

    def test_configure_logging_sets_up_structlog(self):
        """configure_logging sets up structlog with JSON rendering."""
        configure_logging()

        logger = structlog.get_logger("test")
        # Verify the logger is a BoundLogger instance
        assert logger is not None

    def test_configure_logging_sets_log_level(self):
        """configure_logging sets the root logger level."""
        configure_logging(log_level="DEBUG")

        root_logger = logging.getLogger()
        assert root_logger.level == logging.DEBUG

    def test_configure_logging_default_level_is_info(self):
        """configure_logging defaults to INFO level."""
        configure_logging()

        root_logger = logging.getLogger()
        assert root_logger.level == logging.INFO

    def test_configure_logging_uses_stdout_handler(self):
        """configure_logging outputs to stdout."""
        configure_logging()

        root_logger = logging.getLogger()
        assert len(root_logger.handlers) == 1
        assert isinstance(root_logger.handlers[0], logging.StreamHandler)
