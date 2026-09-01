"""Read-only Validator Definitions endpoint — one row per workflow stage's
independent validator agent (see app/models/validator.py and
app/services/validator_agent.py, called automatically by the Loop Engine's
VALIDATE step). Not user-editable yet; seeded by app/db/seed.py.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import ValidatorDefinition
from app.schemas.validator import ValidatorDefinitionRead

router = APIRouter(prefix="/validators", tags=["validators"])


@router.get("", response_model=list[ValidatorDefinitionRead])
def list_validator_definitions(db: Session = Depends(get_db)) -> list[ValidatorDefinition]:
    return db.query(ValidatorDefinition).order_by(ValidatorDefinition.stage).all()


@router.get("/{stage}", response_model=ValidatorDefinitionRead)
def get_validator_definition(stage: str, db: Session = Depends(get_db)) -> ValidatorDefinition:
    validator = db.query(ValidatorDefinition).filter(ValidatorDefinition.stage == stage).first()
    if validator is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No validator is configured for stage '{stage}'")
    return validator
