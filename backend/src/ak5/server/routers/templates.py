"""Board template registry API (issue #33 review follow-up)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from ak5.contracts.errors import HTTP_STATUS, ErrorCode, error_body
from ak5.server.models.actor import Actor
from ak5.server.routers.auth import get_current_actor
from ak5.server.schemas.template import TemplateColumnOut, TemplateOut
from ak5.server.services.templates import (
    BUILTIN_TEMPLATES,
    BoardTemplate,
    get_template,
    list_templates,
)

router = APIRouter(prefix="/templates", tags=["templates"])


def _template_out(tmpl: BoardTemplate) -> TemplateOut:
    builtin = BUILTIN_TEMPLATES.get(tmpl.name)
    source = "builtin" if builtin is not None and builtin is tmpl else "workspace"
    return TemplateOut(
        name=tmpl.name,
        source=source,
        max_attempts=tmpl.max_attempts,
        worker_capabilities=list(tmpl.worker_capabilities),
        acceptance_criteria_defaults=list(tmpl.acceptance_criteria_defaults),
        review_checklist=list(tmpl.review_checklist),
        columns=[
            TemplateColumnOut(
                name=c.name,
                stage=c.stage,
                position=c.position,
                wip_limit=c.wip_limit,
            )
            for c in tmpl.columns
        ],
    )


@router.get("", response_model=list[TemplateOut])
async def list_board_templates(
    current_actor: Annotated[Actor, Depends(get_current_actor)],
) -> list[TemplateOut]:
    """List built-in + server-side workspace templates."""
    _ = current_actor
    return [_template_out(t) for t in list_templates().values()]


@router.get("/{name}", response_model=None)
async def get_board_template(
    name: str,
    current_actor: Annotated[Actor, Depends(get_current_actor)],
) -> TemplateOut | JSONResponse:
    """Return one template by name, or ``404 NOT_FOUND``."""
    _ = current_actor
    try:
        tmpl = get_template(name)
    except KeyError:
        return JSONResponse(
            status_code=HTTP_STATUS[ErrorCode.NOT_FOUND],
            content=error_body(
                ErrorCode.NOT_FOUND,
                f"Unknown board template '{name}'",
                details={"template": name},
            ),
        )
    return _template_out(tmpl)
