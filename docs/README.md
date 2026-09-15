# Documentation Index

The root README is the public landing page. This directory contains the longer
references that keep the README short.

## Public User Docs

- [Scientific workflow](./workflow.md): normal coding-agent and skills workflow,
  project outputs, interaction pattern, and current boundaries.
- [Data locations](./data_locations.md): where local datasets live and how
  agents/scripts resolve them.
- [BWM overview](./bwm/README.md): current derived BWM dataset contents, sizes,
  and links to detailed schemas.
- [BWM ephys dataset](./bwm/ephys.md)
- [BWM behavior dataset](./bwm/behavior.md)

## Skill And Agent Docs

- [Skill layer](./skills.md): overview of the repository-local agent
  instructions.
- [Architecture](./ARCHITECTURE.md): experimental runtime architecture and boundaries.
- [Experimental ask/runtime notes](./ask/ASK_EXPERIMENTAL.md)
- [Ask runtime](./ask/ASK_RUNTIME.md)
- [Ask reliability hardening plan](./ask/ASK_RELIABILITY_HARDENING_PLAN.md)

The normal public workflow uses a coding agent with repository skills. The `ibl-ai-agent ask`
runtime remains experimental unless a task explicitly targets that path.

## Developer Docs

- [Contributing](./CONTRIBUTING.md)
- [Decisions](./decisions/)

## Historical And Internal Notes

These documents are useful for provenance, maintenance, or historical context,
but they are not current user-facing guidance.
