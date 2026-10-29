"""Data the Analyse agents exchange. Research and strategy outputs are schema-constrained, not parsed from prose."""

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

Action = Literal["hold", "add", "trim", "exit"]
Confidence = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Subject:
    """The holding being analysed."""

    tradingsymbol: str
    exchange: str
    kind: Literal["equity", "mf"]
    name: str | None = None

    @property
    def label(self) -> str:
        if self.kind == "mf":
            return f"the mutual fund {self.name or self.tradingsymbol} (ISIN {self.tradingsymbol})"
        return f"{self.tradingsymbol} ({self.exchange})"


# --- research ------------------------------------------------------------------------------------


class Fact(BaseModel):
    claim: str = Field(description="One specific fact with its figures, e.g. 'Q2 FY27 revenue ₹11,905 cr, +24% YoY'.")
    as_of: str = Field(
        description="The date the fact refers to or was published: YYYY-MM-DD, or YYYY-MM if that is all."
    )
    source: str = Field(
        description="Where it came from: the exact URL of the search result or fetched page, copied verbatim, "
        "or 'tool:<tool name>' for data returned by one of your tools (e.g. 'tool:get_technicals')."
    )


class Topic(BaseModel):
    heading: str = Field(description="A short heading, e.g. 'Quarterly results', 'Broker ratings'.")
    facts: list[Fact]


class ResearchFindings(BaseModel):
    topics: list[Topic]
    gaps: list[str] = Field(description="Things you looked for but could not find or confirm.")


# --- strategy ------------------------------------------------------------------------------------


class Verdict(BaseModel):
    action: Action
    confidence: Confidence
    target_weight_pct: float | None = Field(
        description="If adding or trimming: the portfolio weight (%) to add up to or trim down to. Otherwise null."
    )
    summary: str = Field(description="The decision in one sentence, specific to this position.")
    rationale: list[str] = Field(description="3-6 bullets: the reasons, each grounded in the research or the position.")
    what_would_change: list[str] = Field(description="2-4 concrete triggers that would change the decision.")


class StrategyOutput(BaseModel):
    report: str = Field(
        description="The full report in Markdown with every requested section. No verdict section: the verdict "
        "goes in the verdict field and is rendered after the risk check."
    )
    verdict: Verdict


# --- risk ----------------------------------------------------------------------------------------


class UnsupportedClaim(BaseModel):
    claim: str = Field(description="The claim as written in the report.")
    problem: str = Field(description="Why it is not supported: missing from the dossier, or contradicted by it.")


class Review(BaseModel):
    grounded: bool = Field(description="False if any material claim is unsupported by or contradicts the dossier.")
    unsupported_claims: list[UnsupportedClaim]
    summary: str = Field(description="One or two sentences on how well the report is grounded.")


class RuleResult(BaseModel):
    rule: str
    status: Literal["pass", "warn", "block"]
    detail: str


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The model's JSON schema with `additionalProperties: false` on every object, as strict tools require."""
    schema = model.model_json_schema()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                node["additionalProperties"] = False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return schema
