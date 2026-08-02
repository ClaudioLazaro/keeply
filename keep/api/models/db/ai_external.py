import json
import os
from uuid import uuid4

from pydantic import BaseModel, Json
from sqlalchemy import JSON, Column, ForeignKey, Text
from sqlmodel import Field, SQLModel


class ExternalAI(BaseModel):
    """
    Base model for external algorithms.
    """

    name: str = None
    description: str = None
    version: int = None
    api_url: str = None
    api_key: str = None
    config_default: Json = None

    @property
    def unique_id(self):
        return self.name + "_" + str(self.version)


# Not sure if we'll need to move algorithm objects to the DB,
# for now, it's ok to keep them as code.
external_ai_transformers = ExternalAI(
    name="Transformers Correlation",
    description="""A transformer-based alert-to-incident correlation algorithm,
tailored for each tenant by training on their specific alert and incident data.
The system will automatically associate new alerts with existing incidents if they are
sufficiently similar; otherwise, it will create new incidents. In essence, it behaves like a human,
analyzing the alert feed and making decisions for each incoming alert.""",
    version=1,
    api_url=os.environ.get("KEEP_EXTERNAL_AI_TRANSFORMERS_URL", None),
    api_key=os.environ.get("KEEP_EXTERNAL_AI_TRANSFORMERS_API_KEY", None),
    config_default=json.dumps(
        [
            {
                "min": 0.3,
                "max": 0.99,
                "value": 0.9,
                "type": "float",
                "name": "Model Accuracy Threshold",
                "description": "The trained model accuracy will be evaluated using 30 percent of alerts-to-incident correlations as a validation dataset. If the accuracy is below this threshold, the correlation won't be launched.",
            },
            {
                "min": 0.3,
                "max": 0.99,
                "value": 0.9,
                "type": "float",
                "name": "Correlation Threshold",
                "description": "The minimum correlation value to consider two alerts belonging to an incident.",
            },
            {
                "min": 1,
                "max": 20,
                "value": 1,
                "type": "int",
                "name": "Train Epochs",
                "description": "The amount of epochs to train the model for. The less the better to avoid over-fitting.",
            },
            {
                "value": True,
                "type": "bool",
                "name": "Create New Incidents",
                "description": "Do you want AI to issue new incident if correlation is detected and the incnident alerts are related to is resolved?",
            },
            {
                "value": True,
                "type": "bool",
                "name": "Enabled",
                "description": "Enable or disable the algorithm.",
            },
        ]
    ),
)

# Keeply's own correlation algorithm, served by the AIOps control plane
# (keep-aiops) rather than a hosted third party. Same contract as any
# external AI: Keep reminds it about the tenant, it pulls alerts with the
# issued back-API key and writes correlations back.
external_ai_keeply_correlation = ExternalAI(
    name="Keeply Alert Correlation",
    description="""Groups related alerts into a single incident instead of leaving one
incident per alert. Correlates on shared service, arrival window and fingerprint
similarity, so a service degrading in three ways lands as one incident an operator
can act on. Runs inside your cluster — alerts never leave it. Every decision is
recorded with the reason that produced it, so a wrong grouping can be traced and
undone.""",
    version=1,
    # Served by the AIOps control plane shipped alongside Keep, so there is
    # no URL for an operator to discover — only whether it should run, which
    # is the "Enabled" setting below. The override exists for deployments
    # that host the control plane elsewhere.
    api_url=os.environ.get("KEEP_AIOPS_CORRELATION_URL", "http://aiops-api:8080"),
    api_key=os.environ.get("KEEP_AIOPS_CORRELATION_API_KEY", "keeply-internal"),
    config_default=json.dumps(
        [
            {
                "value": False,
                "type": "bool",
                "name": "Enabled",
                "description": "Turn alert correlation on. Off by default: correlation joins alerts into shared incidents automatically, and that should be a deliberate choice rather than something that starts happening after an upgrade.",
            },
            {
                "min": 1,
                "max": 120,
                "value": 10,
                "type": "float",
                "name": "Correlation Window (minutes)",
                "description": "Alerts arriving within this window of each other are candidates for the same incident. Too wide and unrelated failures merge; too narrow and a slow cascade splits.",
            },
            {
                "min": 0.1,
                "max": 1.0,
                "value": 0.6,
                "type": "float",
                "name": "Similarity Threshold",
                "description": "Minimum similarity between two alerts before they are considered the same underlying problem. Below this they stay separate.",
            },
            {
                "min": 0.5,
                "max": 1.0,
                "value": 0.8,
                "type": "float",
                "name": "Auto-merge Confidence",
                "description": "Correlations at or above this confidence are applied automatically. Below it the grouping is recorded as a suggestion on the incident instead of being executed.",
            },
            {
                "min": 1,
                "max": 50,
                "value": 20,
                "type": "int",
                "name": "Max Alerts Per Incident",
                "description": "Safety cap. A correlation that would exceed this is left alone — a group that large usually means the rules are too loose, not that one incident has 50 symptoms.",
            },
        ]
    ),
)

EXTERNAL_AIS = [external_ai_transformers, external_ai_keeply_correlation]


class ExternalAIConfigAndMetadata(SQLModel, table=True):
    """
    Dynamic per-tenant algo settings and metadata
    """

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    algorithm_id: str = Field(nullable=False)
    tenant_id: str = Field(ForeignKey("tenant.id"), nullable=False)
    settings: str = Field(
        sa_column=Column(JSON),
    )
    settings_proposed_by_algorithm: str = Field(
        sa_column=Column(JSON),
    )
    feedback_logs: str = Field(sa_column=Column(Text))

    @property
    def algorithm(self) -> ExternalAI:
        matching_algos = [
            algo for algo in EXTERNAL_AIS if algo.unique_id == self.algorithm_id
        ]
        return matching_algos[0] if len(matching_algos) > 0 else None

    def from_external_ai(tenant_id: str, algorithm: ExternalAI):
        external_ai = ExternalAIConfigAndMetadata(
            algorithm_id=algorithm.unique_id,
            tenant_id=tenant_id,
            settings=json.dumps(algorithm.config_default),
        )
        return external_ai
