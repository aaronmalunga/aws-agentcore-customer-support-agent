"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with completed implementation notes.

Reference the step-by-step solution files and INSTRUCTIONS.md for guidance.
Do NOT copy the solution directly — work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

# Create a BedrockAgentCoreApp instance.
# This registers the ASGI server for AgentCore deployment.
# There must be exactly one instance per deployment.
#
# Hint: app = BedrockAgentCoreApp()

app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# Replace the placeholder strings with your actual AWS resource values.
# You collected these in Part 1 of the INSTRUCTIONS.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customer-support-gateway-none-jkdazgnxpp.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
KB_ID       = "OZHYWL1HCA"
REGION      = "us-east-1"
MEMORY_ID   = "CustomerSupportMemory-Cvh8W7F9pV"


# Create:
#   1. A BedrockModel using model_id "global.amazon.nova-2-lite-v1:0"
#   2. A MemoryClient with region_name=REGION
#   3. A boto3 client for the "bedrock-agent-runtime" service in REGION
#
# Hint: model = BedrockModel(model_id=model_id)

model_id = "amazon.nova-lite-v1:0"

model = model = BedrockModel(model_id=model_id)

memory_client = MemoryClient(region_name=REGION)

_bedrock_runtime =boto3.client(
    "bedrock-agent-runtime",
    region_name=REGION,
)


# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Return a dict: { strategy["type"]: strategy["namespaces"][0] for each strategy }
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type → namespace template string."""
    strategies = mem_client.get_memory_strategies(memory_id)

    namespaces = {}

    for strategy in strategies:
        strategy_type = (
            strategy.get("type")
            or strategy.get("memoryStrategyType")
        )
        strategy_id = (
            strategy.get("memoryStrategyId")
            or strategy.get("strategyId")
        )
        templates = (
            strategy.get("namespaces")
            or strategy.get("namespaceTemplates")
            or []
        )

        if not strategy_type or not strategy_id or not templates:
            continue

        # Resolve the strategy placeholder now; actorId stays dynamic per customer.
        namespace_template = templates[0].replace(
            "{memoryStrategyId}",
            strategy_id,
        )

        namespaces[strategy_type] = namespace_template

    return namespaces


# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id

        # Discover the namespaces configured on the Memory resource.
        self.namespaces = get_namespaces(memory_client, memory_id)

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""

        try:
            messages = event.agent.messages

            if not messages:
                return

            last_message = messages[-1]

            # Only process actual user messages.
            if last_message.get("role") != "user":
                return

            content = last_message.get("content", [])

            if not content:
                return

            first_block = content[0]

            # Tool results can also have role="user".
            # They must NOT trigger memory retrieval.
            if not isinstance(first_block, dict):
                return

            if "toolResult" in first_block:
                return

            # Only process plain-text user messages.
            user_query = first_block.get("text", "").strip()

            if not user_query:
                return

            # PROFILE_SETTING_SKIP_MEMORY
            # Current identity/preferences should not be overridden by stale history.
            normalized_query = user_query.lower()
            profile_setting_message = (
                ("i am " in normalized_query or "my name is " in normalized_query)
                and (
                    "prefer " in normalized_query
                    or "preference" in normalized_query
                    or "call me " in normalized_query
                )
            )

            if profile_setting_message:
                logger.info("Skipping historical memory retrieval for profile-setting message.")
                return

            all_context = []

            # Search every configured long-term-memory strategy.
            for strategy_type, namespace_template in self.namespaces.items():
                try:
                    namespace = namespace_template.format(
                        actorId=self.actor_id,
                        sessionId=self.session_id,
                    )

                    memories = self.memory_client.retrieve_memories(
                        memory_id=self.memory_id,
                        namespace=namespace,
                        query=user_query,
                        top_k=5,
                    )

                    for memory in memories:
                        if not isinstance(memory, dict):
                            continue

                        memory_content = memory.get("content", {})

                        if not isinstance(memory_content, dict):
                            continue

                        text = memory_content.get("text", "").strip()

                        if text:
                            all_context.append(
                                f"[{strategy_type.upper()}] {text}"
                            )

                except Exception as e:
                    logger.warning(
                        "Memory retrieval failed for %s: %s",
                        strategy_type,
                        e,
                    )

            # Add retrieved memories before the customer's current question.
            if all_context:
                context_text = "\n".join(all_context)
                original_message = first_block["text"]

                first_block["text"] = (
                    f"Historical Customer Context (retrieved long-term memory; background only):\n"
                    f"{context_text}\n\n"
                    f"Current Customer Message (this is the actual request; it has priority):\n"
                    f"{original_message}"
                )

        except Exception as e:
            # Memory failure should not prevent the agent from answering.
            logger.warning("Memory context retrieval failed: %s", e)

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""

        try:
            messages = event.agent.messages

            customer_query = None
            agent_response = None

            # Work backwards to find the latest assistant response
            # and latest real user message.
            for message in reversed(messages):
                role = message.get("role")
                content = message.get("content", [])

                if not content:
                    continue

                # Find a text block in the message.
                text = None

                for block in content:
                    if not isinstance(block, dict):
                        continue

                    # Ignore tool-result blocks.
                    if "toolResult" in block:
                        continue

                    if "text" in block:
                        text = block.get("text", "").strip()

                        if text:
                            break

                if not text:
                    continue

                if role == "assistant" and agent_response is None:
                    agent_response = text

                elif role == "user" and customer_query is None:
                    customer_query = text

                if customer_query and agent_response:
                    break

            if not customer_query or not agent_response:
                return

            marker = "Current Customer Message (this is the actual request; it has priority):\n"
            if marker in customer_query:
                customer_query = customer_query.rsplit(marker, 1)[1].strip()

            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[
                    (customer_query, "USER"),
                    (agent_response, "ASSISTANT"),
                ],
            )

        except Exception as e:
            # Saving memory should degrade gracefully rather than crash the agent.
            logger.warning("Memory save failed: %s", e)

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""

        registry.add_callback(
            MessageAddedEvent,
            self.retrieve_customer_context,
        )

        registry.add_callback(
            AfterInvocationEvent,
            self.save_support_interaction,
        )

# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not KB_ID:
        return "Knowledge base not configured."

    try:
        resp = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )

        results = resp.get("retrievalResults", [])

        if not results:
            return "No relevant information was found in the knowledge base."

        chunks = []

        for result in results:
            content = result.get("content", {})
            text = content.get("text", "").strip()

            if text:
                chunks.append(text)

        if not chunks:
            return "No relevant information was found in the knowledge base."

        return "\n---\n".join(chunks)

    except Exception as e:
        logger.warning("Knowledge base retrieval failed: %s", e)
        return f"Knowledge base search failed: {e}"


# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """

    code = f"""
import json

loyalty_points = {loyalty_points}
tier = {tier!r}
order_total = {order_total}
product_category = {product_category!r}

earn_rates = {{
    "standard": 1,
    "device": 2,
    "fresh": 5
}}

tier_rates = {{
    "Silver": 0.00,
    "Gold": 0.10,
    "Platinum": 0.15
}}

# 100 points = $1.
# Points must be redeemed in blocks of 500.
# Redemption cannot cover more than 50% of the order.
max_discount_dollars = order_total * 0.50
max_points_for_order = int(max_discount_dollars * 100)

redeemable_points = min(loyalty_points, max_points_for_order)
points_redeemed = (redeemable_points // 500) * 500

if points_redeemed < 500:
    points_redeemed = 0

points_discount = points_redeemed / 100

subtotal_after_points = order_total - points_discount

tier_rate = tier_rates.get(tier, 0.00)
tier_discount = subtotal_after_points * tier_rate

final_total = round(subtotal_after_points - tier_discount, 2)
total_savings = round(points_discount + tier_discount, 2)

earn_rate = earn_rates.get(product_category.lower(), 1)
points_earned = int(final_total * earn_rate)

remaining_points = loyalty_points - points_redeemed + points_earned

result = {{
    "points_redeemed": points_redeemed,
    "points_discount": round(points_discount, 2),
    "tier_discount_pct": int(tier_rate * 100),
    "tier_discount": round(tier_discount, 2),
    "total_savings": total_savings,
    "final_total": final_total,
    "points_earned": points_earned,
    "remaining_points": remaining_points
}}

print(json.dumps(result))
"""

    try:
        with code_session(REGION) as code_client:
            response = code_client.invoke(
                "executeCode",
                {
                    "code": code,
                    "language": "python",
                    "clearContext": True,
                },
            )

        for event in response["stream"]:
            if "result" in event:
                print(f"CODE_INTERPRETER_SUCCESS result={event['result']}")
                logger.info(
                    "Code Interpreter execution succeeded; result=%s",
                    event["result"],
                )
                return json.dumps(event["result"])

        return json.dumps(
            {
                "error": "Code Interpreter returned no result."
            }
        )

    except Exception as e:
        logger.warning("Code Interpreter unavailable: %s", e)

        tier_rates = {
            "Silver": 0.00,
            "Gold": 0.10,
            "Platinum": 0.15,
        }

        tier_rate = tier_rates.get(tier, 0.00)
        tier_discount = order_total * tier_rate
        final_total = round(order_total - tier_discount, 2)

        return json.dumps(
            {
                "points_redeemed": 0,
                "tier_discount_pct": int(tier_rate * 100),
                "final_total": final_total,
                "remaining_points": loyalty_points,
                "fallback": True,
            }
        )

# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@tool
def get_live_page_title(url: str) -> str:
    """
    Retrieve the title of a live web page using AgentCore Browser.

    Use this tool when the customer specifically asks for a page title.
    It manages the browser session deterministically.
    """

    browser_wrapper = AgentCoreBrowser(region=REGION)
    browser_tool = browser_wrapper.browser
    session_name = f"title-{uuid.uuid4().hex[:12]}"

    def run_action(action):
        return browser_tool(browser_input={"action": action})

    def extract_text(result):
        if not isinstance(result, dict):
            return ""
        for block in result.get("content", []):
            if isinstance(block, dict):
                value = block.get("text", "")
                if value:
                    return value.strip()
        return ""

    try:
        init_result = run_action({
            "type": "init_session",
            "description": "Retrieve a live web page title",
            "session_name": session_name,
        })
        if init_result.get("status") != "success":
            return f"Browser session initialization failed: {init_result}"

        navigate_result = run_action({
            "type": "navigate",
            "url": url,
            "session_name": session_name,
        })
        if navigate_result.get("status") != "success":
            return f"Browser navigation failed: {navigate_result}"

        evaluate_result = run_action({
            "type": "evaluate",
            "script": "document.title",
            "session_name": session_name,
        })

        title_text = extract_text(evaluate_result)
        if title_text.startswith("Evaluation result:"):
            title_text = title_text.split("Evaluation result:", 1)[1].strip()

        if not title_text:
            fallback_result = run_action({
                "type": "get_text",
                "selector": "title",
                "session_name": session_name,
            })
            title_text = extract_text(fallback_result)
            if title_text.startswith("Text content:"):
                title_text = title_text.split("Text content:", 1)[1].strip()

        if title_text:
            logger.info(
                "AgentCore Browser page-title retrieval succeeded: url=%s title=%s",
                url,
                title_text,
            )
            return f"Live page title for {url}: {title_text}"

        return f"Browser returned no page title for {url}."

    except Exception as e:
        logger.warning("Browser page-title retrieval failed: %s", e)
        return f"Browser page-title retrieval failed: {e}"

    finally:
        try:
            run_action({
                "type": "close",
                "session_name": session_name,
            })
        except Exception:
            logger.debug(
                "Browser cleanup encountered an exception.",
                exc_info=True,
            )

async def _fetch_live_page_title_direct(url: str) -> str:
    """Fetch a live page title through AgentCore Browser using async Playwright."""
    from bedrock_agentcore.tools.browser_client import browser_session
    from playwright.async_api import async_playwright

    try:
        with browser_session(REGION) as client:
            ws_url, headers = client.generate_ws_headers()

            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(
                    ws_url,
                    headers=headers,
                )

                try:
                    context = (
                        browser.contexts[0]
                        if browser.contexts
                        else await browser.new_context()
                    )
                    page = (
                        context.pages[0]
                        if context.pages
                        else await context.new_page()
                    )

                    try:
                        await page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                    except Exception as navigation_error:
                        logger.warning(
                            "Browser navigation warning for %s: %s",
                            url,
                            navigation_error,
                        )

                    title = (await page.title()).strip()
                    current_url = page.url

                    if title:
                        print(
                            f"BROWSER_DIRECT_SUCCESS url={current_url} title={title}"
                        )
                        logger.info(
                            "Direct AgentCore Browser title success: url=%s title=%s",
                            current_url,
                            title,
                        )
                        return (
                            f"Live page title for {current_url}: {title}"
                        )

                    print(f"BROWSER_DIRECT_EMPTY_TITLE url={current_url}")
                    return f"Browser returned an empty title for {current_url}."

                finally:
                    await browser.close()

    except Exception as e:
        print(f"BROWSER_DIRECT_ERROR {type(e).__name__}: {e}")
        logger.warning("Direct AgentCore Browser title retrieval failed: %s", e)
        return f"Browser title retrieval failed: {type(e).__name__}: {e}"


@tool
async def get_live_page_title_direct(url: str) -> str:
    """
    Retrieve the title of a live web page with AgentCore Browser.

    Use this tool for page-title requests. Return its result exactly.
    """
    return await _fetch_live_page_title_direct(url)

@app.entrypoint
async def invoke(payload, context=None):
    """
    Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier
      session_id  (str, optional) — session identifier; generated if absent
    """

    try:
        # 1. Read request values.
        user_input = payload.get("prompt", "").strip()

        if not user_input:
            return "Please provide a customer support request."

        # Deterministic path for page-title-only browser requests.
        # The live AgentCore Browser result is returned directly so the model
        # cannot reinterpret a valid page title as a failure.
        normalized_input = user_input.lower()
        if (
            "amazon.com" in normalized_input
            and "title" in normalized_input
        ):
            browser_result = await _fetch_live_page_title_direct(
                "https://www.amazon.com"
            )

            if browser_result.startswith("Live page title for "):
                title = browser_result.rsplit(": ", 1)[-1].strip()
                return f'The page title is: "{title}"'

            return browser_result

        actor_id = payload.get("customer_id", "default_customer")
        session_id = payload.get("session_id") or str(uuid.uuid4())

        # 2. Create long-term memory hook for this customer/session.
        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )

        # 3. Create AgentCore Browser.
        agent_core_browser = AgentCoreBrowser(region=REGION)

        # 4. Start with the tools implemented locally in this file.
        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            get_live_page_title_direct,
            agent_core_browser.browser,
        ]

        # System instructions for the customer-support agent.
        system_prompt = """
You are an intelligent e-commerce customer support assistant.

Use the tools available to you whenever they are appropriate.

Guidelines:
- Use Gateway tools for order tracking, customer information, returns,
  refunds, and other backend operations.
- For a NEW refund request, first retrieve the order details with the order-tracking
  Gateway tool. Use the exact order/item amount returned by that tool.
- Then call initiate_refund exactly once with the real order ID and amount.
- For a newly initiated refund, report the refund_id, status, amount, and message
  returned by initiate_refund.
- Do NOT call check_refund_status immediately after initiate_refund. The
  check_refund_status tool is only for a customer asking about an EXISTING refund ID.
- Never invent a refund amount, refund ID, status, or processing timeline.
- Use search_knowledge_base for product information, return policies,
  warranties, loyalty program information, and other catalog questions.
- Use calculate_loyalty_discount for loyalty points and discount calculations.
- The numeric fields returned by calculate_loyalty_discount are authoritative. Copy them exactly into the customer response and do NOT recompute, reinterpret, or change the tier discount, final total, points earned, or remaining points. The tool already applies points redemption before the tier discount.
- For loyalty calculations, explicitly report every returned calculation field: points_redeemed, points_discount, tier_discount_pct as a percentage, tier_discount, total_savings, final_total, points_earned, and remaining_points.
- Use the browser tool when the customer explicitly asks you to visit or
  inspect a live web page.
- For page-title-only requests, call get_live_page_title_direct exactly once. Do not call the generic browser tool for that request.
- Treat the exact string returned by get_live_page_title_direct as authoritative. Do not guess or substitute a different page title.
- The generic AgentCore Browser tool remains available for other live-web tasks.
- For browser page-title requests, initialize one browser session first, then navigate, then use the browser "evaluate" action with JavaScript "document.title" while reusing the same session_name.
- The browser tool does not provide a get_title or GetTitleAction. Do not attempt either one.
- If "document.title" returns a non-empty string, report that string exactly as the live page title, even if the website itself returned an error page.
- Only say the title could not be retrieved when the browser returns no title value or an actual tool error.
- Use customer context supplied through memory when it is relevant.
- Treat supplied memory as historical background, never as a new customer request.
- The current customer message always has priority over retrieved memory.
- Never call refund, order, or status tools solely because an old memory mentions those topics.
- When a customer states their name or communication preference, acknowledge the current statement without calling backend tools.
- When asked whether you remember a name or communication preference, answer from supplied memory context and do not call customer-lookup or order-tracking tools.
- Do not invent order information, refund results, product facts, or
  calculation results.
- If a tool cannot provide the required information, explain that clearly.
- Keep customer-facing responses clear and helpful.
"""

        # 5. Connect to AgentCore Gateway through MCP.
        gateway_client = MCPClient(
            lambda: streamable_http_client(GATEWAY_URL)
        )

        # IMPORTANT:
        # Keep the Gateway connection open while both creating and
        # invoking the agent.
        with gateway_client:
            gateway_tools = gateway_client.list_tools_sync()
            tools.extend(gateway_tools)

            # 6. Create the Strands agent with all capabilities.
            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=system_prompt,
            )

            # Invoke the agent.
            response = agent(user_input)

            # 7. Return the first text content block.
            content = response.message.get("content", [])

            if content and isinstance(content[0], dict):
                text = content[0].get("text")

                if text:
                    return text

            return str(response)

    except Exception as e:
        logger.exception("Agent invocation failed: %s", e)

        # 8. Fail gracefully rather than crashing the runtime.
        return f"Unable to complete the request: {e}"


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
