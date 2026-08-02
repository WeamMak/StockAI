# StockAI Project Instructions

## Project purpose

This repository contains a final course project: an AI agent that solves
a clearly defined real-world business problem.

The complete system must include:

- an intelligent AI agent
- an LLM framework such as LangGraph or LangChain
- MCP tool integration
- an HTTP API
- AWS infrastructure provisioned with Terraform
- a self-managed Kubernetes cluster on EC2
- CI/CD
- automated testing
- metrics, logs, alerts, and dashboards

## Source of truth

Before proposing, planning, or implementing anything, read:

- `docs/requirements/`
- `docs/tutorials/`
- the project assignment file
- any existing files in `docs/`

Course requirements override personal preferences and generic best practices.

Do not invent requirements.

When the course requirements are ambiguous, record the ambiguity and ask
the user before making an irreversible decision.

## Mandatory planning gate

Do not write application code, infrastructure code, Kubernetes manifests,
tests, Dockerfiles, or CI/CD workflows until both of these files exist:

- `docs/spec.md`
- `docs/plan.md`

The planning process must use the Superpowers brainstorming skill or a
good equivalent.

The specification must be produced through an interactive brainstorming
process.

The implementation plan must be produced using a writing-plans workflow
or a suitable equivalent.

Both documents must be reviewed and approved before implementation begins.

Until the user explicitly confirms approval, remain in planning mode.

## Mandatory project workflow

Follow this workflow in order:

1. Read the assignment and all relevant tutorials.
2. Use the brainstorming skill to clarify the idea and compare alternatives.
3. Use grill-with-docs to challenge the proposed design against the source documents.
4. Create `docs/spec.md`.
5. Stop and request user review.
6. Revise `docs/spec.md` until the user approves it.
7. Wait for course-staff approval through a pull request.
8. Use the writing-plans skill or an equivalent planning workflow.
9. Create `docs/plan.md`.
10. Stop and request user review.
11. Revise `docs/plan.md` until the user approves it.
12. Wait for course-staff approval through a pull request.
13. Only then begin implementation.
14. Implement one approved plan task at a time.
15. Add testing, deployment, CI/CD, and observability as required by the approved plan.

Do not skip stages.

Do not interpret approval of one stage as approval of later stages.

Do not begin implementation merely because `docs/spec.md` and
`docs/plan.md` exist. Implementation requires explicit user approval
after both documents have received the required course-staff approval.

## Requirements analysis

Before finalizing the idea, identify:

- target customer
- specific business problem
- measurable business value
- current manual workflow or alternative
- why an AI agent is appropriate
- why ordinary deterministic software is not enough
- expected user interaction
- actions the agent may perform
- actions requiring human approval
- required MCP tools
- data sources
- privacy and security risks
- MVP boundaries
- success metrics

Clearly label every statement as one of:

- explicit course requirement
- tutorial-supported approach
- project decision
- assumption
- open question

## `docs/spec.md` requirements

The design specification must include at least:

1. Problem statement
2. Target users
3. Business value
4. Measurable success criteria
5. User workflows
6. Agent persona
7. Agent capabilities
8. Agent boundaries
9. System prompt design
10. Architecture
11. Components
12. Data flow
13. MCP integrations
14. Custom MCP server design, when applicable
15. HTTP API design
16. Web UI design, when included
17. Data storage
18. AWS services
19. Kubernetes deployment
20. Error handling
21. Retries and timeouts
22. Graceful termination
23. Fallback behavior
24. Security and secrets management
25. Observability
26. Testing strategy
27. Risks and limitations
28. MVP scope
29. Explicitly excluded features
30. Requirements traceability matrix

Every important architecture decision must explain:

- why it is needed
- which requirement it satisfies
- alternatives considered
- why the selected option was chosen

## `docs/plan.md` requirements

The implementation plan must:

- be step-by-step
- use small, independently reviewable tasks
- reference concrete files and directories
- include tests for every important behavior
- keep the system runnable after each major phase
- map tasks to assignment requirements
- define completion criteria
- identify dependencies and risks

Recommended implementation strategy:

1. Build a minimal local end-to-end walking skeleton containing:
   - a basic HTTP API
   - a minimal agent workflow using the selected coded LLM framework
   - at least one real MCP tool call over the real MCP transport
   - a simple user-facing result
   - basic structured logs and meaningful request, LLM, and MCP metrics
   - automated unit and integration tests for the happy path and one
     representative failure path
2. Containerize the working walking skeleton.
3. Provision the minimum required AWS infrastructure and separate `dev` and
   `prod` configuration with Terraform.
4. Establish the CI/CD and GitOps promotion path described below.
5. Deploy the walking skeleton to the `dev` Kubernetes namespace and validate
   its end-to-end user interaction in dev.
6. Promote the same tested walking-skeleton artifact to the `prod` Kubernetes
   namespace through an explicit approval gate and verify it with smoke checks.
7. Add the remaining capabilities as small end-to-end vertical slices.
8. For every vertical slice, update together:
   - application and agent code
   - unit and integration tests
   - documentation and requirements traceability
   - metrics, logs, alerts, and dashboard panels when relevant
   - configuration, Terraform, containers, and Kubernetes manifests when
     relevant
9. Finish with a requirements-hardening pass covering security, retries,
   timeouts, fallbacks, graceful termination, probes, resource requests and
   limits, HPA, secrets, actionable alerts, dashboards, documentation, and
   presentation/demo preparation.

Use this branch and promotion workflow:

- `main` is the protected production branch and maps to the `prod`
  environment.
- `dev` is an unprotected integration branch and maps to the `dev`
  environment. Pull requests into `dev` are not required.
- Create each feature branch from the latest `main`.
- Implement and commit the feature on its feature branch.
- Merge the feature branch locally into `dev`, resolve any conflicts, and push
  `dev` directly.
- A push to `dev` triggers only the relevant dev GitHub Actions flows, such as
  building and publishing the container image and updating the desired image
  tag or other deployment configuration under the dev manifest path.
- Docker Scout checks run on merge and push to `dev`.
- Tests checks run on pull requests targeting `main`, not on
  pushes to `dev`.
- GitHub Actions must not deploy workloads directly with `kubectl`. Configure
  the dev Argo CD application to track the `dev` revision and dev manifest path
  and reconcile the `dev` namespace.
- After the change is validated in dev, open a pull request from the new feature branch to
  `main`.
- Run the complete automated test suite and Docker Scout checks on that pull
  request. Do not merge it until all required checks pass.
- Merging the pull request to `main` is the explicit production promotion
  decision. The main GitHub Actions flow promotes the same immutable artifact
  validated in dev and updates the desired production deployment
  configuration.
- GitHub Actions must not deploy directly to production. Configure the prod
  Argo CD application to track the `main` revision and prod manifest path and
  reconcile the `prod` namespace.
- Do not push feature work directly to `main`.
- Keep `dev` releasable. Do not merge unrelated features into it when they
  should not be promoted together in the next new feature branch to `main` pull request.
- Apply an urgent production hotfix through a branch created from `main`, then
  reconcile the accepted change back into `dev`.

Do not create one large task called "implement the project."

## Agent requirements

The agent must:

- solve a clearly defined business problem
- provide measurable value
- use a coded LLM framework such as LangGraph or LangChain
- not use no-code agent platforms
- expose an HTTP API
- call MCP tools during a real interaction
- have a system prompt defining persona, capabilities, and boundaries
- return clear errors
- support retries and timeouts
- terminate gracefully
- include fallback behavior

Do not claim that a capability is agentic unless it requires reasoning,
tool selection, or multi-step decision-making.

## MCP requirements

The system must connect to at least one public or self-hosted MCP server.

A custom domain-specific MCP server is strongly preferred.

For every MCP server, document:

- purpose
- tools
- tool inputs
- tool outputs
- authentication
- permissions
- external systems accessed
- failure behavior
- timeout behavior
- retry policy
- whether it is built or reused
- why MCP is appropriate

The agent must call MCP tools in an actual end-to-end interaction.

## Infrastructure requirements

The Kubernetes cluster must be self-managed on AWS EC2.

Do not use EKS.

The complete stack must be deployed to:

- `dev` namespace
- `prod` namespace

Each environment must have separate configuration.

Kubernetes workloads must include, where relevant:

- liveness probes
- readiness probes
- resource requests
- resource limits
- Horizontal Pod Autoscaler
- ConfigMaps
- secrets management
- graceful shutdown behavior

All AWS resources must be provisioned with Terraform.

Do not instruct the user to create production resources manually through
the AWS Console.

Manual console inspection is acceptable, but infrastructure creation and
configuration must remain reproducible through Terraform.

## CI/CD requirements

The pipeline must:

- run tests on every pull request
- report test results clearly
- deploy to dev
- deploy to prod
- keep environment configuration separate
- fail clearly when validation or deployment fails

Production deployment should use an explicit approval or protected
environment unless the project requirements state otherwise.

## Testing requirements

Create a clear test plan.

Required automated tests include:

### Unit tests

Test:

- agent logic
- routing and decision logic
- prompt-independent business rules
- MCP tools in isolation
- error handling
- retries
- fallbacks

Mock:

- LLM calls
- external APIs
- cloud services

### Integration tests

Test:

- the agent and local MCP server together
- the real MCP transport
- representative tool calls
- tool failures
- timeouts
- malformed tool responses

Do not claim tests passed unless they were actually run successfully.

## Observability requirements

Collect metrics and logs from all services.

Define what healthy means for:

- the agent API
- LLM calls
- MCP calls
- Kubernetes workloads
- external dependencies

Track meaningful signals such as:

- request volume
- error rate
- latency
- LLM failures
- MCP tool failures
- MCP tool timeouts
- retry count
- token usage
- pod restarts
- resource usage

Provide:

- dashboards
- alerts
- structured logs
- useful health endpoints

Alerts must represent actionable conditions, not arbitrary thresholds.

## AWS service selection

### Required platform infrastructure

- EC2 for the self-managed Kubernetes control plane and worker nodes
- VPC, subnets, route tables, gateways, and security groups
- IAM roles and policies
- Terraform state infrastructure when required

### Optional application and operational services

Use these only when justified by the domain:

- S3
- DynamoDB
- SQS
- SNS
- CloudWatch
- Systems Manager Parameter Store
- Secrets Manager

For every AWS service, explain:

- why it is required
- what data it stores or processes
- how the application accesses it
- how permissions are restricted
- how it is provisioned with Terraform

Avoid adding cloud services only to make the architecture look complex.

## Security rules

- Never commit secrets.
- Never place API keys directly in source files.
- Use environment variables or a proper secret store.
- Use least-privilege IAM policies.
- Validate all external inputs.
- Treat MCP tool outputs as untrusted.
- Require human approval for destructive or high-impact actions.
- Avoid logging secrets or sensitive content.
- Document trust boundaries.

## Implementation rules

After planning approval:

- implement one task from `docs/plan.md` at a time
- explain the task before changing files
- identify relevant requirements
- keep changes small
- run relevant tests
- report what changed
- report what remains incomplete
- update documentation when design decisions change

Do not silently deviate from `docs/spec.md` or `docs/plan.md`.

When a change is necessary:

1. explain the reason
2. update the relevant document
3. obtain approval when the change is significant
4. then implement it

## Simplicity rule

Prefer the smallest architecture that fully satisfies the assignment.

Do not add:

- unnecessary microservices
- unnecessary agents
- unnecessary databases
- unnecessary queues
- unnecessary AWS services
- unnecessary abstractions

Complexity must be justified by a real requirement.

## Presentation requirements

The completed project must support a 15-minute presentation and live demo.

The final preparation must include:

- introduction
- problem and business value
- architecture
- agent design
- MCP servers
- AWS infrastructure
- observability
- testing strategy
- AI coding-agent reflection
- skills used
- live end-to-end request
- live observability dashboard
- GitHub Actions pipeline

The user must be able to explain every major decision.

## Current status

The project is currently in planning mode.

Do not begin implementation until:

- `docs/spec.md` exists
- `docs/plan.md` exists
- both documents have been reviewed
- the user explicitly approves implementation
