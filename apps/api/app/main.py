from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.api.routes import (
    agent_definitions,
    agent_runs,
    artifacts,
    code_runs,
    confluence_integration,
    github_integration,
    health,
    implementation_runs,
    integrations,
    jira_integration,
    knowledge,
    maintenance_runs,
    ops,
    pr_review_runs,
    projects,
    prompts,
    releases,
    reviews,
    sprints,
    stories,
    test_runs,
    users,
    validators,
)
from app.core.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """Turn a raw DB constraint violation into a clean 409 instead of a 500."""
    return JSONResponse(
        status_code=409,
        content={"detail": "The request conflicts with an existing record or constraint."},
    )


app.include_router(health.router, tags=["health"])
app.include_router(projects.router)
app.include_router(artifacts.router)
app.include_router(reviews.router)
app.include_router(prompts.router)
app.include_router(agent_runs.router)
app.include_router(agent_definitions.router)
app.include_router(knowledge.router)
app.include_router(integrations.router)
app.include_router(github_integration.router)
app.include_router(jira_integration.router)
app.include_router(confluence_integration.router)
app.include_router(implementation_runs.router)
app.include_router(code_runs.router)
app.include_router(test_runs.router)
app.include_router(pr_review_runs.router)
app.include_router(maintenance_runs.router)
app.include_router(stories.router)
app.include_router(sprints.router)
app.include_router(releases.router)
app.include_router(ops.router)
app.include_router(users.router)
app.include_router(validators.router)
