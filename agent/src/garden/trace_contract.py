"""Shared OpenTelemetry GenAI and Garden trace attribute names.

The gateway, API instrumentation, and eval runner use these names so their spans
can be queried together without depending on producer-specific fields.
"""

GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_CUSTOM_PROVIDER = "custom"
GEN_AI_CONVERSATION_ID = "gen_ai.conversation.id"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_STREAM = "gen_ai.request.stream"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_STATUS = "gen_ai.response.status"
GEN_AI_RESPONSE_ID = "gen_ai.response.id"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"
GEN_AI_INFERENCE_EVENT = "gen_ai.client.inference.operation.details"
GEN_AI_CAPTURE_CONTENT_ENV = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT"
GEN_AI_CAPTURE_CONTENT_MODES = frozenset(
    {"NO_CONTENT", "SPAN_ONLY", "EVENT_ONLY", "SPAN_AND_EVENT"}
)
GEN_AI_CAPTURE_SPAN_MODES = frozenset({"SPAN_ONLY", "SPAN_AND_EVENT"})
GEN_AI_CAPTURE_EVENT_MODES = frozenset({"EVENT_ONLY", "SPAN_AND_EVENT"})

GARDEN_PERSON_ID = "garden.person.id"
GARDEN_AGENT_LOGIN_ID = "garden.agent.login.id"
GARDEN_AGENT_FLAG = "garden.agent.flag"
GARDEN_AGENT_CONVERSATION_ID = "garden.agent.conversation.id"
GARDEN_EVAL_RUN_ID = "garden.eval.run.id"
GARDEN_EVAL_TRIAL_ID = "garden.eval.trial.id"
GARDEN_CONTENT_CAPTURED = "garden.content.captured"
GARDEN_MODEL_TOTAL_TOKENS = "garden.model.usage.total_tokens"
GARDEN_MODEL_TOOL_NAMES = "garden.model.tool_names"
GARDEN_MODEL_SIDE_CALL_TYPE = "garden.model.side_call.type"
GARDEN_CONVERSATION_TITLE_SIDE_CALL = "conversation_title"
GARDEN_EVAL_RUN_ID_ENV = "GARDEN_EVAL_RUN_ID"
GARDEN_EVAL_TRIAL_ID_ENV = "GARDEN_EVAL_TRIAL_ID"

AGENT_CONVERSATION_HEADER = "X-Garden-Agent-Conversation-ID"
MODEL_SIDE_CALL_HEADER = "X-Garden-Model-Side-Call"
GARDEN_AGENT_LOGIN_ID_HEADER = "x-garden-agent-login-id"
GARDEN_PERSON_ID_HEADER = "x-garden-person-id"
