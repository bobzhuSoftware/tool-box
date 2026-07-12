"""Authentication: password hashing, JWT, current-user dependencies, auth routes."""
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import ACCESS_TOKEN_EXPIRE_MINUTES, ALGORITHM, SECRET_KEY
from app.core.db import User, engine

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


def _hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()


def _verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode(), hashed.encode())


def _create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str | None = Depends(oauth2_scheme)) -> User | None:
    """Return the authenticated User or None (for optional auth)."""
    if token is None:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            return None
    except JWTError:
        return None
    with Session(engine) as session:
        return session.get(User, user_id)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    """Raise 401 if no valid user."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def user_from_token_or_header(
    token: str | None = None,
    user: User | None = Depends(get_current_user),
) -> User:
    """Resolve the current user from the OAuth2 ``Authorization`` header, falling
    back to a ``?token=`` query parameter (used by ``window.open`` downloads that
    cannot set headers). Raises 401 if neither yields a valid user.

    Use this as the auth dependency for file-download endpoints.
    """
    if user is None and token:
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            uid = payload.get("sub")
            if uid:
                with Session(engine) as session:
                    user = session.get(User, uid)
        except JWTError:
            pass
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user



# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    username: str
    password: str


router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/register")
def register(req: RegisterRequest):
    username = req.username.strip()
    if not username or len(username) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    with Session(engine) as session:
        existing = session.query(User).filter(User.username == username).first()
        if existing:
            raise HTTPException(status_code=409, detail="Username already exists")
        user = User(
            id=uuid.uuid4().hex,
            username=username,
            password_hash=_hash_password(req.password),
            created_at=datetime.now(timezone.utc),
        )
        session.add(user)
        session.commit()
        token = _create_access_token({"sub": user.id})
    return {"access_token": token, "token_type": "bearer", "username": username}


@router.post("/login")
def login(req: RegisterRequest):
    with Session(engine) as session:
        user = session.query(User).filter(User.username == req.username.strip()).first()
    if not user or not _verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = _create_access_token({"sub": user.id})
    return {"access_token": token, "token_type": "bearer", "username": user.username}


@router.get("/health")
def health_check():
    return {"status": "ok"}


@router.get("/me")
def get_me(user: User = Depends(require_user)):
    return {"username": user.username}
