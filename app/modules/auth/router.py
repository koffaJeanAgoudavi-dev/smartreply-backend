"""
SmartReply Agent — MODULE AUTH : inscription + connexion (Étape 2)
==================================================================
Première brique de la couche multi-utilisateur : comptes email + mot de passe
dans la base Neon (table users), tokens JWT (HS256, validité 24 h).

Endpoints :
  POST /api/v1/auth/register  — crée un compte, renvoie l'utilisateur + un JWT
  POST /api/v1/auth/login     — vérifie les identifiants, renvoie un JWT

Tests :
  curl -X POST http://localhost:8000/api/v1/auth/register \
    -H "Content-Type: application/json" \
    -d '{"email": "alice@example.com", "password": "motdepasse123"}'

  curl -X POST http://localhost:8000/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email": "alice@example.com", "password": "motdepasse123"}'
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.modules.auth.schemas import RegisterResponse, Token, UserCreate, UserLogin, UserOut
from app.modules.auth.security import create_access_token
from app.modules.auth.service import authenticate_user, create_user

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Inscription : email + mot de passe (haché en Argon2), renvoie un JWT."""
    try:
        user = create_user(db, payload.email, payload.password)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un compte existe déjà pour cet email",
        )
    return RegisterResponse(
        user=UserOut.model_validate(user),
        access_token=create_access_token(user.id, user.email),
    )


@router.post("/login", response_model=Token)
def login(payload: UserLogin, db: Session = Depends(get_db)):
    """Connexion : identifiants valides → JWT (HS256, validité 24 h)."""
    user = authenticate_user(db, payload.email, payload.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
        )
    return Token(access_token=create_access_token(user.id, user.email))
