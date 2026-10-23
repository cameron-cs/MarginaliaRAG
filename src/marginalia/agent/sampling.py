"""How the agent's model samples, adjustable per question.

A reader may set any of these when asking; what they leave out comes from the server's settings
(CP_AGENT_TEMPERATURE, CP_AGENT_TOP_P, CP_AGENT_TOP_K, CP_AGENT_MAX_TOOL_CALLS). Only the agent's
own model uses them: the grader that checks answers keeps the server's settings, so a playful
temperature cannot loosen the check.

The limits keep the agent working. qwen3 is a thinking model, and Qwen advises against greedy
decoding for it: very low temperatures make its reasoning repeat itself, hence the floor of 0.1.
"""

from pydantic import BaseModel, ConfigDict, Field


class Sampling(BaseModel):
    model_config = ConfigDict(extra='forbid')

    temperature: float | None = Field(None, ge=0.1, le=1.5,
                                      description='Randomness of the wording; lower is more focused.')
    top_p: float | None = Field(None, ge=0.05, le=1.0,
                                description='Sample only from the most likely words that add up to this share.')
    top_k: int | None = Field(None, ge=1, le=200, description='Sample only from this many most likely words.')
    max_searches: int | None = Field(None, ge=1, le=10, description='Most tool calls the agent may make.')
    seed: int | None = Field(None, ge=0, le=2**31 - 1, description='Fixed seed, for repeatable answers.')

    def custom(self):
        """The values the reader set, for storing with the question and keying the cache."""
        return self.model_dump(exclude_none=True)

    def resolve(self, settings):
        """Every value, the reader's or the server's."""
        return Resolved(
            temperature=self.temperature if self.temperature is not None else settings.agent_temperature,
            top_p=self.top_p if self.top_p is not None else settings.agent_top_p,
            top_k=self.top_k if self.top_k is not None else settings.agent_top_k,
            max_searches=self.max_searches if self.max_searches is not None else settings.agent_max_tool_calls,
            seed=self.seed)


class Resolved(BaseModel):
    temperature: float
    top_p: float
    top_k: int
    max_searches: int
    seed: int | None = None


def limits():
    """{field: {min, max, description}} for the UI's controls."""
    out = {}
    for name, info in Sampling.model_fields.items():
        bounds = {type(m).__name__: m for m in info.metadata}
        out[name] = {'min': getattr(bounds.get('Ge'), 'ge', None), 'max': getattr(bounds.get('Le'), 'le', None),
                     'description': info.description}
    return out
