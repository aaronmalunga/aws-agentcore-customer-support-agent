AWS AgentCore Customer Support Agent

A production-oriented customer support AI agent built with Amazon Bedrock AgentCore, Strands Agents, Amazon Bedrock Knowledge Bases, AgentCore Memory, AgentCore Code Interpreter, AgentCore Browser, Lambda, and MCP Gateway.

This project was completed as part of the Udacity AWS AI Engineering Nanodegree and extends the starter implementation into an end-to-end agent capable of retrieval, tool use, persistent memory, deterministic calculations, live browsing, and cloud deployment.

What the Agent Can Do

Track customer orders through Gateway-exposed backend tools

Initiate refunds through AWS Lambda

Answer product and loyalty questions with RAG

Remember customer facts and preferences across sessions

Calculate loyalty discounts with deterministic Python execution

Retrieve live webpage information through AgentCore Browser

Run as a deployed Bedrock AgentCore Runtime

Produce CloudWatch runtime evidence for tool execution and failures

Architecture

flowchart TD
    U[Customer] --> R[Bedrock AgentCore Runtime]
    R --> A[Strands Agent]

    A --> G[AgentCore Gateway / MCP]
    G --> O[Order Tracking Tool]
    G --> F[Refund Lambda Tool]

    A --> KB[Bedrock Knowledge Base]
    KB --> S3[S3 Product Catalog]
    KB --> OS[OpenSearch Serverless]

    A --> M[AgentCore Memory]
    M --> MF[Customer Facts]
    M --> MP[Customer Preferences]

    A --> CI[AgentCore Code Interpreter]
    A --> B[AgentCore Browser]

    R --> CW[CloudWatch Logs / Observability]

Core AWS Services

Service

Purpose

Amazon Bedrock AgentCore Runtime

Hosts and runs the deployed support agent

AgentCore Gateway

Exposes backend tools over MCP

AWS Lambda

Implements refund and support backend operations

Amazon Bedrock Knowledge Bases

Provides RAG over the product catalog

Amazon OpenSearch Serverless

Vector store for the Knowledge Base

Amazon S3

Stores product catalog source data

AgentCore Memory

Persists facts and customer preferences across sessions

AgentCore Code Interpreter

Executes deterministic loyalty calculations

AgentCore Browser

Retrieves live webpage information

Amazon CloudWatch

Captures runtime logs and operational evidence

Implementation Highlights

Gateway Tool Integration

The agent loads backend tools through an MCPClient connected to Amazon Bedrock AgentCore Gateway. The deployed support workflow includes separate tools for:

order lookup and tracking

refund initiation

This keeps operational actions outside the language model and allows backend capabilities to be governed independently.

Retrieval-Augmented Generation

search_knowledge_base() uses the Bedrock Knowledge Base Retrieve API to answer product, returns, and loyalty questions from a managed product catalog instead of relying on model memory.

Cross-Session Customer Memory

A custom MemoryHook retrieves long-term customer context before an agent turn and persists the completed interaction afterward.

The implementation distinguishes historical customer context from the current user request so previously stored memories do not override the active query.

Deterministic Loyalty Calculations

Loyalty calculations are executed with AgentCore Code Interpreter rather than delegated to model arithmetic.

The calculation returns structured values including:

points redeemed

points discount

loyalty tier discount

total savings

final order total

points earned

remaining points

A fallback path is included for environments where Code Interpreter is unavailable.

Live Browser Retrieval

The agent integrates AgentCore Browser for live web access. For the page-title scenario, the runtime performs a live browser session and returns the retrieved document title rather than inferring it from model knowledge.

Functional Test Coverage

The final implementation was validated against the six required end-to-end scenarios.

Test

Capability

Result

1

Order tracking

Pass

2

Refund processing

Pass

3

Knowledge Base / RAG

Pass

4

Cross-session memory

Pass

5

Loyalty calculation / Code Interpreter

Pass

6

Live Browser retrieval

Pass

Representative validated behaviors include:

ORD-001 → shipping status, UPS carrier, tracking number, estimated delivery

ORD-002 → approved refund workflow

Platinum loyalty → same-day shipping, 15% discount, priority support

customer memory → recalls name and concise-response preference across sessions

Gold loyalty calculation → deterministic points and discount output

Amazon page title → retrieved through a live AgentCore Browser session

Project Structure

.
├── main.py
├── product_catalog.txt
├── pyproject.toml
├── uv.lock
├── lambda/
│   ├── lambda_schema
│   ├── order_tracker.py
│   └── refund_processor.py
└── README.md

Generated deployment artifacts, local virtual environments, temporary diagnostics, and sandbox-specific AgentCore metadata are intentionally excluded from version control.

Running Locally

Prerequisites

Python 3.13+

uv

AWS CLI

Amazon Bedrock / AgentCore permissions

AgentCore CLI

Access to Amazon Nova Lite in us-east-1

Install dependencies:

uv sync

Run the application locally:

uv run python main.py

Deployment

Configure AgentCore:

agentcore configure

Deploy:

agentcore deploy

Invoke the deployed runtime:

agentcore invoke '{
  "prompt": "Can you track order ORD-001?",
  "customer_id": "CUST-123",
  "session_id": "demo-1"
}'

AWS resource identifiers and sandbox-specific deployment metadata are intentionally not committed to this repository.

Example Test Scenarios

Order Tracking

agentcore invoke '{
  "prompt": "Can you track order ORD-001?",
  "customer_id": "CUST-123",
  "session_id": "t1"
}'

Knowledge Base

agentcore invoke '{
  "prompt": "What are the benefits of the Platinum loyalty tier?",
  "customer_id": "CUST-123",
  "session_id": "t3"
}'

Long-Term Memory

First session:

agentcore invoke '{
  "prompt": "Hi, I am Jane. I prefer concise responses.",
  "customer_id": "CUST-123",
  "session_id": "s-A"
}'

Later session:

agentcore invoke '{
  "prompt": "Do you remember my name and communication preference?",
  "customer_id": "CUST-123",
  "session_id": "s-B"
}'

Loyalty Calculation

agentcore invoke '{
  "prompt": "I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.",
  "customer_id": "CUST-123",
  "session_id": "t5"
}'

Production Considerations

The implementation was built in a constrained training sandbox, so several production hardening steps would be required before real deployment:

authenticated Gateway access

least-privilege IAM policies

secret rotation and centralized secret management

structured response validation

CloudWatch metrics and alarms

distributed tracing where supported

retry, timeout, and circuit-breaker policies

request and tool-level cost controls

PII handling and data-retention controls

automated integration and regression tests

Design Decisions

A few implementation choices were deliberately production-oriented:

Deterministic arithmetic through Code Interpreter instead of free-form model calculation

Tool-backed operational actions instead of embedding business actions in prompts

RAG-backed factual support answers rather than relying on foundation-model memory

Long-term memory separated from the current request to avoid stale context dominating active intent

Live Browser retrieval for current webpage data instead of inferred answers

Generated deployment metadata excluded from Git to avoid publishing environment-specific AWS details

Learning Outcomes

This project demonstrates practical experience with:

agent orchestration

MCP-based tool integration

serverless backend integration

Retrieval-Augmented Generation

vector search

persistent agent memory

deterministic tool execution

live browser automation

cloud deployment

observability

production hardening considerations

Course Context

Built for the Udacity AWS AI Engineering Nanodegree — Course 2 project on production-grade customer support agents with Amazon Bedrock AgentCore.

The repository contains my implementation and project-specific engineering decisions. The original Udacity starter instructions and grading rubric are not reproduced here.

License

This repository is intended for educational and portfolio use. Third-party services, SDKs, and course materials remain subject to their respective licenses and terms.
