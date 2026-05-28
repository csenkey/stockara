"""Tests for the encryption service."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from backend.src.services.encryption_service import (
    DecryptionError,
    EncryptionError,
    EncryptionService,
)


@pytest.fixture
def mock_kms_client():
    """Create a mock KMS client that simulates real key generation/decryption."""
    client = MagicMock()
    # Use a fixed 32-byte key for testing
    test_key = os.urandom(32)
    encrypted_key_blob = b"encrypted-key-blob-for-testing-purposes"

    client.generate_data_key.return_value = {
        "Plaintext": test_key,
        "CiphertextBlob": encrypted_key_blob,
    }
    client.decrypt.return_value = {
        "Plaintext": test_key,
    }
    return client


@pytest.fixture
def encryption_service(mock_kms_client):
    """Create an EncryptionService with mock KMS."""
    return EncryptionService(
        kms_key_id="arn:aws:kms:us-east-1:123456789:key/test-key",
        kms_client=mock_kms_client,
    )


@pytest.fixture
def sample_portfolio():
    return {
        "holdings": [
            {"ticker": "AAPL", "quantity": 50, "buying_price": 175.20, "added_date": "2025-03-15"},
            {"ticker": "MSFT", "quantity": 30, "buying_price": 420.00, "added_date": "2025-04-01"},
        ]
    }


class TestEncryptionServiceInit:
    def test_init_with_explicit_key(self, mock_kms_client):
        svc = EncryptionService(kms_key_id="arn:aws:kms:us-east-1:123:key/k", kms_client=mock_kms_client)
        assert svc.kms_key_id == "arn:aws:kms:us-east-1:123:key/k"

    def test_init_from_env_var(self, mock_kms_client):
        with patch.dict(os.environ, {"KMS_KEY_ID": "arn:aws:kms:us-east-1:123:key/env-key"}):
            svc = EncryptionService(kms_client=mock_kms_client)
            assert svc.kms_key_id == "arn:aws:kms:us-east-1:123:key/env-key"

    def test_init_no_key_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            # Remove KMS_KEY_ID if set
            os.environ.pop("KMS_KEY_ID", None)
            with pytest.raises(EncryptionError, match="KMS_KEY_ID is not configured"):
                EncryptionService(kms_client=MagicMock())


class TestEncryptPortfolio:
    def test_encrypt_returns_base64_string(self, encryption_service, sample_portfolio):
        result = encryption_service.encrypt_portfolio(sample_portfolio)
        assert isinstance(result, str)
        # Should be valid base64
        import base64
        decoded = base64.b64decode(result)
        assert len(decoded) > 0

    def test_encrypt_different_each_time(self, encryption_service, sample_portfolio):
        """Each encryption should produce different output due to random nonce."""
        r1 = encryption_service.encrypt_portfolio(sample_portfolio)
        r2 = encryption_service.encrypt_portfolio(sample_portfolio)
        assert r1 != r2

    def test_encrypt_calls_kms_generate_data_key(self, encryption_service, mock_kms_client, sample_portfolio):
        encryption_service.encrypt_portfolio(sample_portfolio)
        mock_kms_client.generate_data_key.assert_called_once_with(
            KeyId="arn:aws:kms:us-east-1:123456789:key/test-key",
            KeySpec="AES_256",
        )

    def test_encrypt_kms_failure_raises_encryption_error(self, encryption_service, mock_kms_client, sample_portfolio):
        mock_kms_client.generate_data_key.side_effect = Exception("KMS unavailable")
        with pytest.raises(EncryptionError, match="Failed to encrypt portfolio data"):
            encryption_service.encrypt_portfolio(sample_portfolio)


class TestDecryptPortfolio:
    def test_roundtrip_encrypt_decrypt(self, encryption_service, sample_portfolio):
        encrypted = encryption_service.encrypt_portfolio(sample_portfolio)
        decrypted = encryption_service.decrypt_portfolio(encrypted)
        assert decrypted == sample_portfolio

    def test_decrypt_invalid_base64_raises(self, encryption_service):
        with pytest.raises(DecryptionError, match="Failed to decrypt portfolio data"):
            encryption_service.decrypt_portfolio("not-valid-base64!!!")

    def test_decrypt_truncated_data_raises(self, encryption_service):
        import base64
        # Too short to contain anything meaningful
        short_data = base64.b64encode(b"\x00\x00").decode()
        with pytest.raises(DecryptionError, match="Failed to decrypt portfolio data"):
            encryption_service.decrypt_portfolio(short_data)

    def test_decrypt_tampered_ciphertext_raises(self, encryption_service, sample_portfolio):
        import base64
        encrypted = encryption_service.encrypt_portfolio(sample_portfolio)
        # Tamper with the last byte of ciphertext
        raw = bytearray(base64.b64decode(encrypted))
        raw[-1] ^= 0xFF
        tampered = base64.b64encode(bytes(raw)).decode()
        with pytest.raises(DecryptionError, match="Failed to decrypt portfolio data"):
            encryption_service.decrypt_portfolio(tampered)

    def test_decrypt_kms_failure_raises_decryption_error(self, encryption_service, mock_kms_client, sample_portfolio):
        encrypted = encryption_service.encrypt_portfolio(sample_portfolio)
        mock_kms_client.decrypt.side_effect = Exception("KMS unavailable")
        with pytest.raises(DecryptionError, match="Failed to decrypt portfolio data"):
            encryption_service.decrypt_portfolio(encrypted)

    def test_decrypt_never_exposes_partial_data(self, encryption_service, mock_kms_client, sample_portfolio):
        """On failure, the error message must not contain any portfolio data."""
        encrypted = encryption_service.encrypt_portfolio(sample_portfolio)
        mock_kms_client.decrypt.side_effect = Exception("KMS failure")
        with pytest.raises(DecryptionError) as exc_info:
            encryption_service.decrypt_portfolio(encrypted)
        error_msg = str(exc_info.value)
        assert "AAPL" not in error_msg
        assert "MSFT" not in error_msg
        assert "175.20" not in error_msg

    def test_roundtrip_empty_holdings(self, encryption_service):
        portfolio = {"holdings": []}
        encrypted = encryption_service.encrypt_portfolio(portfolio)
        decrypted = encryption_service.decrypt_portfolio(encrypted)
        assert decrypted == portfolio

    def test_roundtrip_large_portfolio(self, encryption_service):
        """Round-trip with a larger portfolio (20 holdings)."""
        portfolio = {
            "holdings": [
                {
                    "ticker": f"TK{i:02d}",
                    "quantity": i * 10,
                    "buying_price": 100.0 + i * 5.5,
                    "added_date": f"2025-01-{i + 1:02d}",
                }
                for i in range(20)
            ]
        }
        encrypted = encryption_service.encrypt_portfolio(portfolio)
        decrypted = encryption_service.decrypt_portfolio(encrypted)
        assert decrypted == portfolio
        assert len(decrypted["holdings"]) == 20
