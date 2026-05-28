"""
Encryption service for portfolio data using AES-256-GCM with AWS KMS data keys.

Flow:
- Encrypt: Generate data key from KMS → encrypt JSON with plaintext key → 
  store encrypted data key + nonce + tag + ciphertext as base64
- Decrypt: Use KMS to decrypt the data key → decrypt ciphertext with plaintext key
- On failure: raise error without exposing partial data
"""

import base64
import json
import os
import struct

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionError(Exception):
    """Raised when encryption fails."""
    pass


class DecryptionError(Exception):
    """Raised when decryption fails. Never exposes partial data."""
    pass


class EncryptionService:
    """Encrypts and decrypts portfolio data using AES-256-GCM with AWS KMS data keys."""

    def __init__(self, kms_key_id: str | None = None, kms_client=None):
        """
        Initialize the encryption service.

        Args:
            kms_key_id: The ARN or alias of the KMS key. Defaults to KMS_KEY_ID env var.
            kms_client: Optional boto3 KMS client (for testing/injection).
        """
        self.kms_key_id = kms_key_id or os.environ.get("KMS_KEY_ID")
        if not self.kms_key_id:
            raise EncryptionError("KMS_KEY_ID is not configured")
        self.kms_client = kms_client or boto3.client("kms")

    def encrypt_portfolio(self, portfolio_data: dict) -> str:
        """
        Encrypt portfolio data to a base64-encoded string for storage.

        Args:
            portfolio_data: The portfolio dict to encrypt (e.g. {"holdings": [...]}).

        Returns:
            A base64-encoded string containing the encrypted data key, nonce, tag, and ciphertext.

        Raises:
            EncryptionError: If encryption fails for any reason.
        """
        try:
            # Serialize portfolio to JSON bytes
            plaintext = json.dumps(portfolio_data, separators=(",", ":")).encode("utf-8")

            # Generate a data key from KMS
            response = self.kms_client.generate_data_key(
                KeyId=self.kms_key_id,
                KeySpec="AES_256",
            )
            plaintext_key = response["Plaintext"]  # 32 bytes for AES-256
            encrypted_key = response["CiphertextBlob"]  # Encrypted version of the key

            # Encrypt using AES-256-GCM
            aesgcm = AESGCM(plaintext_key)
            nonce = os.urandom(12)  # 96-bit nonce for GCM
            ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, None)

            # Pack: [encrypted_key_len (4 bytes)][encrypted_key][nonce (12 bytes)][ciphertext+tag]
            packed = (
                struct.pack(">I", len(encrypted_key))
                + encrypted_key
                + nonce
                + ciphertext_with_tag
            )

            return base64.b64encode(packed).decode("utf-8")

        except Exception as e:
            raise EncryptionError(f"Failed to encrypt portfolio data: {e}") from e

    def decrypt_portfolio(self, encrypted_data: str) -> dict:
        """
        Decrypt a base64-encoded encrypted portfolio string back to a dict.

        Args:
            encrypted_data: The base64 string produced by encrypt_portfolio.

        Returns:
            The decrypted portfolio dict.

        Raises:
            DecryptionError: If decryption fails. Never exposes partial data.
        """
        plaintext = None
        try:
            # Decode base64
            packed = base64.b64decode(encrypted_data)

            # Unpack: [encrypted_key_len (4 bytes)][encrypted_key][nonce (12 bytes)][ciphertext+tag]
            if len(packed) < 4:
                raise ValueError("Invalid encrypted data: too short")

            key_len = struct.unpack(">I", packed[:4])[0]
            offset = 4

            if len(packed) < offset + key_len + 12:
                raise ValueError("Invalid encrypted data: incomplete")

            encrypted_key = packed[offset : offset + key_len]
            offset += key_len

            nonce = packed[offset : offset + 12]
            offset += 12

            ciphertext_with_tag = packed[offset:]

            # Decrypt the data key using KMS
            response = self.kms_client.decrypt(CiphertextBlob=encrypted_key)
            plaintext_key = response["Plaintext"]

            # Decrypt the ciphertext using AES-256-GCM
            aesgcm = AESGCM(plaintext_key)
            plaintext = aesgcm.decrypt(nonce, ciphertext_with_tag, None)

            # Parse JSON
            portfolio = json.loads(plaintext.decode("utf-8"))
            return portfolio

        except Exception:
            # Clear any sensitive data from memory
            plaintext = None
            raise DecryptionError(
                "Failed to decrypt portfolio data"
            )
