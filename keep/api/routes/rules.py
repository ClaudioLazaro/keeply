import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from keep.api.core.cel_to_sql.cel_ast_converter import CelToAstConverter
from keep.api.core.db import create_rule as create_rule_db
from keep.api.core.db import delete_rule as delete_rule_db
from keep.api.core.db import get_rule_distribution as get_rule_distribution_db
from keep.api.core.db import get_rule_incidents_count_db
from keep.api.core.db import get_rules as get_rules_db
from keep.api.core.db import update_rule as update_rule_db
from keep.api.models.db.rule import CreateIncidentOn, ResolveOn
from keep.identitymanager.authenticatedentity import AuthenticatedEntity
from keep.identitymanager.identitymanagerfactory import IdentityManagerFactory

router = APIRouter()

logger = logging.getLogger(__name__)


class RuleCreateDto(BaseModel):
    ruleName: str
    sqlQuery: dict
    celQuery: str
    timeframeInSeconds: int
    timeUnit: str
    groupingCriteria: list = []
    groupDescription: str = None
    requireApprove: bool = False
    resolveOn: str = ResolveOn.NEVER.value
    createOn: str = CreateIncidentOn.ANY.value
    incidentNameTemplate: str = None
    incidentPrefix: str = None
    multiLevel: bool = False
    multiLevelPropertyName: str = None
    threshold: int = 1
    assignee: str = None


@router.get(
    "",
    description="Get Rules",
)
def get_rules(
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["read:rules"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    logger.info("Getting rules")
    rules = get_rules_db(tenant_id=tenant_id)
    # now add this:
    rules_dist = get_rule_distribution_db(tenant_id=tenant_id, minute=True)
    rules_incidents = get_rule_incidents_count_db(tenant_id=tenant_id)
    logger.info("Got rules")
    # return rules
    rules = [rule.model_dump() for rule in rules]
    for rule in rules:
        rule["distribution"] = rules_dist.get(rule["id"], [])
        rule["incidents"] = rules_incidents.get(rule["id"], 0)
        rule["definition_cel_ast"] = CelToAstConverter().convert_to_ast(
            rule["definition_cel"]
        )

    return rules


@router.post(
    "",
    description="Create Rule",
)
async def create_rule(
    rule_create_request: RuleCreateDto,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:rules"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    created_by = authenticated_entity.email
    logger.info("Creating rule")
    rule_name = rule_create_request.ruleName
    cel_query = rule_create_request.celQuery
    timeframe = rule_create_request.timeframeInSeconds
    timeunit = rule_create_request.timeUnit
    grouping_criteria = rule_create_request.groupingCriteria
    group_description = rule_create_request.groupDescription
    require_approve = rule_create_request.requireApprove
    resolve_on = rule_create_request.resolveOn
    create_on = rule_create_request.createOn
    sql = rule_create_request.sqlQuery.get("sql")
    params = rule_create_request.sqlQuery.get("params")
    incident_name_template = rule_create_request.incidentNameTemplate
    incident_prefix = rule_create_request.incidentPrefix
    multi_level = rule_create_request.multiLevel
    multi_level_property_name = rule_create_request.multiLevelPropertyName
    threshold = rule_create_request.threshold
    assignee = rule_create_request.assignee

    if not sql:
        raise HTTPException(status_code=400, detail="SQL is required")

    # params can be {} for example on '(( source is not null ))'
    if not params and not params == {}:
        raise HTTPException(status_code=400, detail="Params are required")

    if not cel_query:
        raise HTTPException(status_code=400, detail="CEL is required")

    if not rule_name:
        raise HTTPException(status_code=400, detail="Rule name is required")

    if not timeframe:
        raise HTTPException(status_code=400, detail="Timeframe is required")

    if not timeunit:
        raise HTTPException(status_code=400, detail="Timeunit is required")

    if not resolve_on:
        raise HTTPException(status_code=400, detail="resolveOn is required")

    if not create_on:
        raise HTTPException(status_code=400, detail="createOn is required")

    if not threshold:
        raise HTTPException(status_code=400, detail="threshold is required")

    rule = create_rule_db(
        tenant_id=tenant_id,
        name=rule_name,
        definition={
            "sql": sql,
            "params": params,
        },
        timeframe=timeframe,
        timeunit=timeunit,
        definition_cel=cel_query,
        created_by=created_by,
        grouping_criteria=grouping_criteria,
        group_description=group_description,
        require_approve=require_approve,
        resolve_on=resolve_on,
        create_on=create_on,
        incident_name_template=incident_name_template,
        incident_prefix=incident_prefix,
        multi_level=multi_level,
        multi_level_property_name=multi_level_property_name,
        threshold=threshold,
        assignee=assignee,
    )
    logger.info("Rule created")
    return rule


class RuleFromCelDto(BaseModel):
    """Create a rule from a CEL expression alone.

    The regular create endpoint expects the caller to supply the SQL form
    too, which the web UI derives from its query builder. Programmatic
    callers (the correlation suggester) only have CEL, and re-implementing
    the conversion outside Keep would mean two dialects of the same
    translation drifting apart. Keep already owns that translation, so it
    happens here.
    """

    ruleName: str
    celQuery: str
    timeframeInSeconds: int = 600
    timeUnit: str = "seconds"
    groupingCriteria: list[str] = []
    requireApprove: bool = True
    createOn: str = "any"
    resolveOn: str = "never"
    incidentNameTemplate: str | None = None
    groupDescription: str | None = None


@router.post(
    "/from-cel",
    description="Create a rule from a CEL expression, deriving the SQL form",
)
async def create_rule_from_cel(
    body: RuleFromCelDto,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["write:rules"])
    ),
):
    from keep.api.core.alerts import properties_metadata
    from keep.api.core.cel_to_sql.sql_providers.get_cel_to_sql_provider_for_dialect import (
        get_cel_to_sql_provider,
    )

    if not body.celQuery:
        raise HTTPException(status_code=400, detail="CEL is required")
    if not body.ruleName:
        raise HTTPException(status_code=400, detail="Rule name is required")

    try:
        sql = get_cel_to_sql_provider(properties_metadata).convert_to_sql_str(
            body.celQuery
        )
    except Exception as exc:
        # A CEL expression Keep cannot translate would create a rule that
        # never matches; refuse it rather than storing something inert.
        raise HTTPException(
            status_code=400, detail=f"Invalid CEL expression: {exc}"
        ) from exc

    rule = create_rule_db(
        tenant_id=authenticated_entity.tenant_id,
        name=body.ruleName,
        timeframe=body.timeframeInSeconds,
        timeunit=body.timeUnit,
        definition={"sql": sql, "params": {}},
        definition_cel=body.celQuery,
        created_by=authenticated_entity.email,
        grouping_criteria=body.groupingCriteria,
        group_description=body.groupDescription,
        require_approve=body.requireApprove,
        resolve_on=body.resolveOn,
        create_on=body.createOn,
        incident_name_template=body.incidentNameTemplate,
        incident_prefix=None,
        multi_level=False,
        multi_level_property_name=None,
        threshold=1,
        assignee=None,
    )
    logger.info("Rule created from CEL", extra={"rule_id": str(rule.id)})
    return rule


@router.delete(
    "/{rule_id}",
    description="Delete Rule",
)
async def delete_rule(
    rule_id: str,
    request: Request,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["delete:rules"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    logger.info(f"Deleting rule {rule_id}")
    if delete_rule_db(tenant_id=tenant_id, rule_id=rule_id):
        logger.info(f"Rule {rule_id} deleted")
        return {"message": "Rule deleted"}
    else:
        logger.info(f"Rule {rule_id} not found")
        raise HTTPException(status_code=404, detail="Rule not found")


@router.put(
    "/{rule_id}",
    description="Update Rule",
)
async def update_rule(
    rule_id: str,
    request: Request,
    authenticated_entity: AuthenticatedEntity = Depends(
        IdentityManagerFactory.get_auth_verifier(["update:rules"])
    ),
):
    tenant_id = authenticated_entity.tenant_id
    updated_by = authenticated_entity.email
    logger.info(f"Updating rule {rule_id}")
    try:
        body = await request.json()
        rule_name = body["ruleName"]
        sql_query = body["sqlQuery"]
        cel_query = body["celQuery"]
        timeframe = body["timeframeInSeconds"]
        timeunit = body["timeUnit"]
        resolve_on = body["resolveOn"]
        create_on = body["createOn"]
        grouping_criteria = body.get("groupingCriteria", [])
        require_approve = body.get("requireApprove", [])
        incident_template_name = body.get("incidentNameTemplate", None)
        incident_prefix = body.get("incidentPrefix", None)
        multi_level = body.get("multiLevel", False)
        multi_level_property_name = body.get("multiLevelPropertyName", None)
        threshold = body.get("threshold", 1)
        assignee = body.get("assignee", None)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid request body")

    sql = sql_query.get("sql")
    params = sql_query.get("params")

    if not sql:
        raise HTTPException(status_code=400, detail="SQL is required")

    if (
        not params and not params == {}
    ):  # params can be {} for example on '(( source is not null ))'
        raise HTTPException(status_code=400, detail="Params are required")

    if not cel_query:
        raise HTTPException(status_code=400, detail="CEL is required")

    if not rule_name:
        raise HTTPException(status_code=400, detail="Rule name is required")

    if not timeframe:
        raise HTTPException(status_code=400, detail="Timeframe is required")

    if not timeunit:
        raise HTTPException(status_code=400, detail="Timeunit is required")

    if not resolve_on:
        raise HTTPException(status_code=400, detail="resolveOn is required")

    if not create_on:
        raise HTTPException(status_code=400, detail="createOn is required")

    if not threshold:
        raise HTTPException(status_code=400, detail="threshold is required")

    rule = update_rule_db(
        tenant_id=tenant_id,
        rule_id=rule_id,
        name=rule_name,
        definition={
            "sql": sql,
            "params": params,
        },
        timeframe=timeframe,
        timeunit=timeunit,
        definition_cel=cel_query,
        updated_by=updated_by,
        grouping_criteria=grouping_criteria,
        require_approve=require_approve,
        resolve_on=resolve_on,
        create_on=create_on,
        incident_name_template=incident_template_name,
        incident_prefix=incident_prefix,
        multi_level=multi_level,
        multi_level_property_name=multi_level_property_name,
        threshold=threshold,
        assignee=assignee,
    )

    if rule:
        logger.info(f"Rule {rule_id} updated")
        return rule
    else:
        logger.info(f"Rule {rule_id} not found")
        raise HTTPException(status_code=404, detail="Rule not found")
