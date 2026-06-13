"""FastAPI router for Cognito authentication integration."""

import os
import re
import time
from typing import Optional

import boto3
import requests
from botocore.exceptions import ClientError
from fastapi import APIRouter, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, EmailStr
from jose import jwt, JWTError

import structlog

from backend.src.db.connection import store

logger = structlog.get_logger(__name__)

# Configuration from environment variables
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Cognito password policy constants
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128

# Session timeout (30 minutes in seconds)
SESSION_TIMEOUT_SECONDS = 30 * 60

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer()

# Cache for JWKS keys
_jwks_cache: Optional[dict] = None
_jwks_cache_time: float = 0
JWKS_CACHE_TTL = 3600  # Cache JWKS for 1 hour


# --- Request/Response Models ---


class RegisterRequest(BaseModel):
    """Request body for user registration."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class LoginRequest(BaseModel):
    """Request body for user login."""

    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., description="User password")


class AuthResponse(BaseModel):
    """Response body for successful authentication."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class RegisterResponse(BaseModel):
    """Response body for successful registration."""

    message: str
    user_id: str


# --- Password Validation ---


def validate_password_policy(password: str) -> list[str]:
    """Validate password against Cognito password policy.

    Policy: 8-128 chars, at least one uppercase, one lowercase, one digit.

    Returns a list of error messages. Empty list means valid.
    """
    errors = []

    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(f"Password must be at most {PASSWORD_MAX_LENGTH} characters")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        errors.append("Password must contain at least one digit")

    return errors


# --- Cognito Client ---


def get_cognito_client():
    """Get a boto3 Cognito Identity Provider client."""
    return boto3.client("cognito-idp", region_name=AWS_REGION)


# --- JWKS Handling ---


def get_jwks_url() -> str:
    """Get the JWKS URL for the configured Cognito User Pool."""
    return f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"


def get_jwks() -> dict:
    """Fetch and cache JWKS keys from Cognito."""
    global _jwks_cache, _jwks_cache_time

    current_time = time.time()
    if _jwks_cache is not None and (current_time - _jwks_cache_time) < JWKS_CACHE_TTL:
        return _jwks_cache

    jwks_url = get_jwks_url()
    response = requests.get(jwks_url, timeout=10)
    response.raise_for_status()
    _jwks_cache = response.json()
    _jwks_cache_time = current_time
    return _jwks_cache


def verify_jwt_token(token: str) -> dict:
    """Verify a JWT token using Cognito JWKS.

    Returns the decoded token claims if valid.
    Raises HTTPException with 401 if invalid.
    """
    try:
        # Decode header without verification to get kid
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        if not kid:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Get the matching key from JWKS
        jwks = get_jwks()
        key = None
        for k in jwks.get("keys", []):
            if k["kid"] == kid:
                key = k
                break

        if key is None:
            raise HTTPException(status_code=401, detail="Invalid token")

        # Verify and decode the token
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=COGNITO_CLIENT_ID,
            issuer=f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{COGNITO_USER_POOL_ID}",
        )

        # Check token expiration
        if claims.get("exp", 0) < time.time():
            raise HTTPException(status_code=401, detail="Token expired")

        return claims

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except requests.RequestException:
        raise HTTPException(status_code=401, detail="Unable to verify token")


# --- FastAPI Dependencies ---


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> str:
    """FastAPI dependency that validates JWT and returns the user_id (sub claim).

    Usage:
        @router.get("/protected")
        async def protected_route(user_id: str = Depends(get_current_user)):
            ...
    """
    token = credentials.credentials
    claims = verify_jwt_token(token)
    user_id = claims.get("sub")

    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")

    return user_id


# --- Endpoints ---


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(request: RegisterRequest):
    """Register a new user via Cognito.

    Validates password policy locally before calling Cognito.
    Creates user record in local users table on success.
    """
    # Validate password policy locally
    password_errors = validate_password_policy(request.password)
    if password_errors:
        raise HTTPException(status_code=400, detail={"errors": password_errors})

    cognito_client = get_cognito_client()

    try:
        # Register user with Cognito
        response = cognito_client.sign_up(
            ClientId=COGNITO_CLIENT_ID,
            Username=request.email,
            Password=request.password,
            UserAttributes=[
                {"Name": "email", "Value": request.email},
            ],
        )

        user_sub = response["UserSub"]

        # Auto-confirm the user for simplicity (in production, use email verification)
        try:
            cognito_client.admin_confirm_sign_up(
                UserPoolId=COGNITO_USER_POOL_ID,
                Username=request.email,
            )
        except ClientError as e:
            logger.warning(
                "Failed to auto-confirm user",
                email=request.email,
                error=str(e),
            )

        # Mirror the Cognito user locally for app-owned data references.
        store.put_user(user_sub, request.email)

        logger.info("User registered successfully", user_id=user_sub, email=request.email)

        return RegisterResponse(
            message="Registration successful",
            user_id=user_sub,
        )

    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "UsernameExistsException":
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists",
            )
        elif error_code == "InvalidPasswordException":
            raise HTTPException(
                status_code=400,
                detail={"errors": ["Password does not meet requirements"]},
            )
        else:
            logger.error("Cognito registration error", error_code=error_code, error=str(e))
            raise HTTPException(
                status_code=500,
                detail="Registration failed. Please try again later.",
            )


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """Authenticate a user via Cognito and return JWT tokens.

    Returns generic error messages on failure (doesn't reveal if email or password was wrong).
    Surfaces account lockout message if applicable.
    """
    cognito_client = get_cognito_client()

    try:
        response = cognito_client.initiate_auth(
            ClientId=COGNITO_CLIENT_ID,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": request.email,
                "PASSWORD": request.password,
            },
        )

        auth_result = response["AuthenticationResult"]

        logger.info("User logged in successfully", email=request.email)

        return AuthResponse(
            access_token=auth_result["AccessToken"],
            expires_in=auth_result["ExpiresIn"],
        )

    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "NotAuthorizedException":
            # Generic message — don't reveal whether email or password was wrong
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
            )
        elif error_code == "UserNotFoundException":
            # Same generic message for security
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
            )
        elif error_code == "UserNotConfirmedException":
            raise HTTPException(
                status_code=403,
                detail="Account not confirmed. Please check your email.",
            )
        elif error_code == "PasswordResetRequiredException":
            raise HTTPException(
                status_code=403,
                detail="Password reset required",
            )
        elif error_code == "TooManyRequestsException":
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please try again later.",
            )
        elif error_code in ("LimitExceededException", "TooManyFailedAttemptsException"):
            # Account locked after 5 failed attempts
            raise HTTPException(
                status_code=429,
                detail="Account temporarily locked due to too many failed attempts. Please try again later.",
            )
        else:
            logger.error("Cognito login error", error_code=error_code, error=str(e))
            raise HTTPException(
                status_code=401,
                detail="Invalid credentials",
            )
